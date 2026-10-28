from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import publish_primary_three_rank as p0
from top10decision.decision.three_rank import build_promotion_context_features
from top10decision.decision.three_engine_models import RUNTIME_ALIGNED_FEATURE_COLUMNS


DATES = ["20260921", "20260922", "20260923", "20260924", "20260925", "20260928"]
CODE = "002813.SZ"


def evidence() -> dict:
    return {
        "schema_version": "dc20_confirmed_suspensions_v1",
        "suspensions": [{
            "ts_code": CODE, "start_date": DATES[1], "resume_date": DATES[-1],
            "sources": [{"url": "https://static.cninfo.com.cn/test.pdf", "published_date": "20260927", "pdf_sha256": "a" * 64}],
        }],
    }


def engine(tmp_path: Path, monkeypatch, payload: dict | None = None):
    obj = p0.PrimaryDReadOnlyEngine.__new__(p0.PrimaryDReadOnlyEngine)
    obj.config = SimpleNamespace(root=tmp_path)
    obj._primary_signal_date = DATES[-1]
    obj._primary_context_dates = tuple(DATES)
    obj._primary_verifier = None
    obj._primary_consumed = {}
    obj._confirmed_suspensions = None
    obj._confirmed_effective_dates_cache = {}
    obj._streak_count_cache = {}
    obj._path_cache = {}
    obj._promotion_context_cache = {}
    bars = {
        DATES[0]: {"ts_code": CODE, "close": 10.0, "pre_close": 9.09},
        DATES[-1]: {"ts_code": CODE, "close": 11.0, "pre_close": 10.0},
    }

    def table(day, name):
        rows = []
        if name == "daily" and day in bars:
            rows = [bars[day]]
        elif name == "stk_limit":
            rows = [{"ts_code": CODE, "up_limit": bars.get(day, {}).get("close", 11.0)}]
        return pd.DataFrame(rows, columns=["ts_code", "close", "pre_close", "up_limit"]).set_index("ts_code", drop=False)

    obj.market_table = table
    successors = dict(zip(DATES, DATES[1:]))
    monkeypatch.setattr(p0, "next_a_share_trading_day", lambda day: successors.get(day, "20260929"))
    if payload is not None:
        path = tmp_path / "data/market/confirmed_suspensions.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    return obj, bars


def test_confirmed_suspension_counts_only_real_limit_bars_and_binds_evidence(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch, evidence())
    calls = []
    obj._primary_verifier = SimpleNamespace(bind=lambda relative, **kwargs: calls.append(relative) or {"path": relative, "git_blob_sha1": "b" * 40, "sha256": "c" * 64})
    assert obj._consecutive_limit_up_count(DATES[-1], CODE) == 2
    assert obj._confirmed_trading_dates(DATES[-1], CODE) == [DATES[0], DATES[-1]]
    assert calls == ["data/market/confirmed_suspensions.json"]
    binding = obj.consumed_bindings()[0]
    assert binding["git_blob_sha1"] == "b" * 40
    assert binding["used_intervals"][0]["skipped_sessions"] == DATES[1:-1]


def test_unknown_gap_is_not_skipped(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch)
    assert obj._consecutive_limit_up_count(DATES[-1], CODE) == 1


@pytest.mark.parametrize("resume_close, expected_single, expected_bridged", [
    (109.90, 1, 2),  # Within the canonical 0.25% tolerance, beyond 0.011.
    (109.70, 0, 0),  # Outside the canonical tolerance.
])
def test_confirmed_streak_uses_original_high_price_limit_predicate(tmp_path, monkeypatch, resume_close, expected_single, expected_bridged):
    obj, bars = engine(tmp_path, monkeypatch, evidence())
    bars[DATES[0]]["close"] = 100.0
    bars[DATES[-1]].update(close=resume_close, pre_close=100.0)
    original_table = obj.market_table

    def table(day, name):
        frame = original_table(day, name)
        if day == DATES[-1] and name == "stk_limit":
            frame.loc[CODE, "up_limit"] = 110.0
        return frame

    obj.market_table = table
    assert p0.AuctionV3Engine._consecutive_limit_up_count(obj, DATES[-1], CODE, DATES) == expected_single
    obj._streak_count_cache.clear()
    assert obj._consecutive_limit_up_count(DATES[-1], CODE) == expected_bridged


def test_historical_query_cannot_use_a_later_disclosure(tmp_path, monkeypatch):
    payload = evidence()
    payload["suspensions"][0]["sources"][0]["published_date"] = "20260929"
    obj, _ = engine(tmp_path, monkeypatch, payload)
    # The file is available at this P0 run, but was not available at the
    # earlier signal date whose features are being queried.
    obj._primary_signal_date = "20260929"
    with pytest.raises(p0.PrimaryDGenerationError, match="after queried signal_date"):
        obj._consecutive_limit_up_count(DATES[-1], CODE)


def test_six_sse_context_matches_frozen_ledger_with_nan(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch, evidence())
    actual = obj._promotion_source_context_features(DATES[-1], CODE)
    expected = build_promotion_context_features(2, [10, math.nan, math.nan, math.nan, math.nan, 11], [True, False, False, False, False, True])
    for key, value in expected.items():
        assert math.isnan(actual[key]) if math.isnan(value) else actual[key] == value
    assert math.isnan(actual["five_year_streak_runup"])
    assert actual["five_year_recent_limit_up_count"] == 2
    assert not any(column.startswith("path_") for column in RUNTIME_ALIGNED_FEATURE_COLUMNS)


@pytest.mark.parametrize("mutation, message", [
    ("future", "future evidence"),
    ("overlap", "overlapping"),
    ("hash", "provenance"),
    ("outside", "history closure"),
])
def test_invalid_evidence_fails_closed(tmp_path, monkeypatch, mutation, message):
    payload = evidence()
    if mutation == "future":
        payload["suspensions"][0]["sources"][0]["published_date"] = "20260929"
    elif mutation == "overlap":
        payload["suspensions"].append(dict(payload["suspensions"][0]))
    elif mutation == "hash":
        payload["suspensions"][0]["sources"][0]["pdf_sha256"] = ""
    elif mutation == "outside":
        payload["suspensions"][0]["start_date"] = "20260918"
    obj, _ = engine(tmp_path, monkeypatch, payload)
    with pytest.raises(p0.PrimaryDGenerationError, match=message):
        obj._consecutive_limit_up_count(DATES[-1], CODE)


@pytest.mark.parametrize("mutation, message", [
    ("trade_during_suspension", "conflicts with a daily bar"),
    ("missing_boundary", "lacks boundary bars"),
    ("adjustment", "price is discontinuous"),
])
def test_boundary_and_suspension_quotes_must_agree(tmp_path, monkeypatch, mutation, message):
    obj, bars = engine(tmp_path, monkeypatch, evidence())
    if mutation == "trade_during_suspension":
        bars[DATES[2]] = {"ts_code": CODE, "close": 10.0}
    elif mutation == "missing_boundary":
        del bars[DATES[0]]
    elif mutation == "adjustment":
        bars[DATES[-1]]["pre_close"] = 9.0
    with pytest.raises(p0.PrimaryDGenerationError, match=message):
        obj._consecutive_limit_up_count(DATES[-1], CODE)


def test_calendar_hole_cannot_be_treated_as_suspension(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch, evidence())
    monkeypatch.setattr(p0, "next_a_share_trading_day", lambda day: "20990101")
    with pytest.raises(p0.PrimaryDGenerationError, match="calendar gap"):
        obj._consecutive_limit_up_count(DATES[-1], CODE)


def test_calendar_hole_outside_suspension_bridge_is_rejected(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch, evidence())
    obj._primary_context_dates = ("20260918", *DATES)
    # The announcement's bridge is intact; an unrelated earlier missing SSE
    # session must not become valid merely because a suspension was confirmed.
    successors = dict(zip(DATES, DATES[1:]))
    successors["20260918"] = "20260919"
    monkeypatch.setattr(p0, "next_a_share_trading_day", lambda day: successors.get(day, "20260929"))
    with pytest.raises(p0.PrimaryDGenerationError, match="committed suspension context has a calendar gap"):
        obj._consecutive_limit_up_count(DATES[-1], CODE)


def test_path_uses_actual_bar_dates_but_context_calendar_stays_full(tmp_path, monkeypatch):
    obj, _ = engine(tmp_path, monkeypatch, evidence())
    seen = []
    monkeypatch.setattr(p0.AuctionV3Engine, "_streak_path_features", lambda self, d, c, dates=None: seen.extend(dates) or {"path_days_observed": float(len(dates))})
    result = obj._streak_path_features(DATES[-1], CODE)
    assert result["path_days_observed"] == 2
    assert seen == [DATES[0], DATES[-1]]
    assert obj.market_dates() == DATES


def committed_boundary_engine(tmp_path, monkeypatch):
    obj, bars = engine(tmp_path, monkeypatch, evidence())
    calendar = tmp_path / "data/market/trade_cal_sse.csv"
    pd.DataFrame({"exchange": "SSE", "cal_date": DATES, "is_open": 1}).to_csv(calendar, index=False)
    for day in DATES:
        path = tmp_path / f"data/market/raw/{day[:4]}/{day}/daily.csv"
        path.parent.mkdir(parents=True)
        # A nonempty full-market table is still required on suspended days.
        row = bars.get(day, {"ts_code": "600000.SH", "close": 5, "pre_close": 5})
        pd.DataFrame([{**row, "trade_date": day}]).to_csv(path, index=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit_boundary_fixture(tmp_path)
    obj._primary_verifier = p0.GitHeadInputVerifier(tmp_path)
    obj._primary_context_dates = tuple(DATES[1:])
    return obj


def commit_boundary_fixture(root):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=fixture", "-c",
        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "immutable diagnostic fixture",
    ], check=True)


@pytest.mark.parametrize("window_start", [1, 2, 3])
def test_rolling_left_boundary_uses_evidence_without_extending_features(tmp_path, monkeypatch, window_start):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    obj._primary_context_dates = tuple(DATES[window_start:])
    before = obj.market_dates()
    assert obj._confirmed_trading_dates(DATES[-1], CODE) == [DATES[-1]]
    assert obj.market_dates() == before
    assert DATES[0] not in obj._confirmed_trading_dates(DATES[-1], CODE)
    binding = obj._primary_consumed[f"suspension_boundary:{DATES[1]}:{DATES[-1]}"]
    assert binding["git_head"] == obj._primary_verifier.head
    assert binding["feature_window_extended"] is False
    assert [f["trade_date"] for f in binding["files"]] == DATES
    assert all(f["sha256"] and f["git_blob_sha1"] for f in binding["files"])


@pytest.mark.parametrize("mutation, message", [
    ("missing_file", "missing, empty"),
    ("missing_pre_bar", "lacks boundary bars"),
    ("traded", "conflicts with a daily bar"),
    ("discontinuous", "price is discontinuous"),
    ("future_row", "not exact-D"),
    ("drift", "drifted from HEAD"),
    ("uncommitted", "not committed in HEAD"),
    ("future_disclosure", "future evidence"),
    ("symlink", "must not be a symlink"),
])
def test_clipped_suspension_evidence_fails_closed(tmp_path, monkeypatch, mutation, message):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    first = tmp_path / f"data/market/raw/2026/{DATES[0]}/daily.csv"
    resume = tmp_path / f"data/market/raw/2026/{DATES[-1]}/daily.csv"
    if mutation == "missing_file":
        first.unlink()
    elif mutation in {"missing_pre_bar", "drift", "future_row"}:
        frame = pd.read_csv(first)
        if mutation == "missing_pre_bar":
            frame["ts_code"] = "600000.SH"
        elif mutation == "future_row":
            frame["trade_date"] = "20260929"
        else:
            frame["close"] = 9.0
        frame.to_csv(first, index=False)
    elif mutation == "traded":
        pd.DataFrame([{"ts_code": CODE, "trade_date": DATES[2], "close": 10}]).to_csv(
            tmp_path / f"data/market/raw/2026/{DATES[2]}/daily.csv", index=False)
    elif mutation == "discontinuous":
        frame = pd.read_csv(resume)
        frame["pre_close"] = 9.0
        frame.to_csv(resume, index=False)
    elif mutation == "uncommitted":
        subprocess.run(["git", "-C", str(tmp_path), "rm", "--cached", str(first)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "remove boundary witness"], check=True)
    elif mutation == "future_disclosure":
        payload = evidence()
        payload["suspensions"][0]["sources"][0]["published_date"] = "20260929"
        (tmp_path / "data/market/confirmed_suspensions.json").write_text(json.dumps(payload))
    elif mutation == "symlink":
        first.unlink()
        first.symlink_to(resume)
    if mutation not in {"missing_file", "drift", "uncommitted", "symlink"}:
        commit_boundary_fixture(tmp_path)
    obj._primary_verifier = p0.GitHeadInputVerifier(tmp_path)
    with pytest.raises(p0.PrimaryDGenerationError, match=message):
        obj._confirmed_trading_dates(DATES[-1], CODE)


def test_clipped_boundary_cannot_read_after_query_or_extend_unboundedly(tmp_path, monkeypatch):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    with pytest.raises(p0.PrimaryDGenerationError, match="future date"):
        obj._suspension_boundary_evidence(DATES[1], DATES[-1], DATES[-2])
    monkeypatch.setattr(p0, "D_CLOSE_MAX_HISTORY_BARS", 0)
    with pytest.raises(p0.PrimaryDGenerationError, match="bounded history closure"):
        obj._suspension_boundary_evidence(DATES[1], DATES[-1], DATES[-1])


def test_boundary_verifier_cannot_follow_a_new_head(tmp_path, monkeypatch):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    first = tmp_path / f"data/market/raw/2026/{DATES[0]}/daily.csv"
    frame = pd.read_csv(first)
    frame["close"] = 9.0
    frame.to_csv(first, index=False)
    commit_boundary_fixture(tmp_path)
    # Keep the original verifier: changing HEAD must not silently rebind evidence.
    with pytest.raises(p0.PrimaryDGenerationError, match="drifted from HEAD"):
        obj._confirmed_trading_dates(DATES[-1], CODE)


def test_boundary_calendar_requires_a_committed_preceding_session(tmp_path, monkeypatch):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    calendar = tmp_path / "data/market/trade_cal_sse.csv"
    frame = pd.read_csv(calendar)
    frame.iloc[1:].to_csv(calendar, index=False)
    commit_boundary_fixture(tmp_path)
    obj._primary_verifier = p0.GitHeadInputVerifier(tmp_path)
    with pytest.raises(p0.PrimaryDGenerationError, match="lacks preceding SSE session"):
        obj._confirmed_trading_dates(DATES[-1], CODE)


@pytest.mark.parametrize('kind', ['daily', 'calendar'])
@pytest.mark.parametrize('cached', [False, True])
def test_boundary_rejects_replacement_after_bind_even_with_cached_binding(tmp_path, monkeypatch, kind, cached):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    relative = ('data/market/trade_cal_sse.csv' if kind == 'calendar'
                else f'data/market/raw/2026/{DATES[-1]}/daily.csv')
    verifier = obj._primary_verifier
    if cached:
        verifier.bind(relative, label='fixture cache')
    original = verifier.bind
    def replace_after_binding(path, *, label):
        binding = original(path, label=label)
        if path == relative:
            target = tmp_path/path
            replacement = target.with_suffix('.replacement')
            replacement.write_bytes(target.read_bytes()+b'\n')
            replacement.replace(target)
        return binding
    monkeypatch.setattr(verifier, 'bind', replace_after_binding)
    with pytest.raises(p0.PrimaryDGenerationError, match='parsed bytes differ from fixed HEAD'):
        obj._confirmed_trading_dates(DATES[-1], CODE)


@pytest.mark.parametrize('kind', ['daily', 'calendar'])
def test_boundary_parses_verified_bytes_without_rereading_changed_worktree(tmp_path, monkeypatch, kind):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    relative = ('data/market/trade_cal_sse.csv' if kind == 'calendar'
                else f'data/market/raw/2026/{DATES[0]}/daily.csv')
    path = tmp_path/relative
    original_bytes = path.read_bytes()
    original_reader = p0.pd.read_csv
    calls = []
    def change_disk_at_parse(stream, **kwargs):
        assert isinstance(stream, p0.io.BytesIO)
        calls.append(stream.getvalue())
        path.write_bytes(b'not the committed CSV anymore\n')
        return original_reader(stream, **kwargs)
    monkeypatch.setattr(p0.pd, 'read_csv', change_disk_at_parse)
    frame, binding = obj._primary_verifier.read_bound_csv(relative, label='single-byte fixture')
    assert calls == [original_bytes]
    assert binding['sha256'] == p0.hashlib.sha256(original_bytes).hexdigest()
    assert binding['git_blob_sha1'] == p0.hashlib.sha1(b'blob '+str(len(original_bytes)).encode()+b'\0'+original_bytes).hexdigest()
    assert not frame.empty and path.read_bytes() != original_bytes
    # A subsequent cached-binding read must reject the new disk contents.
    with pytest.raises(p0.PrimaryDGenerationError, match='parsed bytes differ from fixed HEAD'):
        obj._primary_verifier.read_bound_csv(relative, label='cached fixture')
    assert len(calls) == 1


def test_discontinuous_committed_price_cannot_be_repaired_after_bind(tmp_path, monkeypatch):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    relative = f'data/market/raw/2026/{DATES[-1]}/daily.csv'
    target = tmp_path/relative
    frame = pd.read_csv(target); frame['pre_close'] = 9.0; frame.to_csv(target, index=False)
    commit_boundary_fixture(tmp_path)
    obj._primary_verifier = p0.GitHeadInputVerifier(tmp_path)
    original = obj._primary_verifier.bind
    def forged_price(path, *, label):
        binding = original(path, label=label)
        if path == relative:
            frame = pd.read_csv(target); frame['pre_close'] = 10.0; frame.to_csv(target,index=False)
        return binding
    monkeypatch.setattr(obj._primary_verifier, 'bind', forged_price)
    with pytest.raises(p0.PrimaryDGenerationError, match='parsed bytes differ from fixed HEAD'):
        obj._confirmed_trading_dates(DATES[-1], CODE)


@pytest.mark.parametrize('rewrite', ['sha256', 'git_blob_sha1'])
def test_cached_binding_requires_both_hashes_of_parsed_bytes(tmp_path, monkeypatch, rewrite):
    obj = committed_boundary_engine(tmp_path, monkeypatch)
    relative = f'data/market/raw/2026/{DATES[0]}/daily.csv'
    verifier = obj._primary_verifier
    verifier.bind(relative,label='cached witness')
    target=tmp_path/relative; raw=target.read_bytes()+b'\n';target.write_bytes(raw)
    # Model a race during the original separate hash operations: one hash may
    # describe changed bytes, but both must describe the same committed blob.
    verifier._bindings[relative][rewrite] = (p0.hashlib.sha256(raw).hexdigest() if rewrite=='sha256'
        else p0.hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest())
    with pytest.raises(p0.PrimaryDGenerationError,match='parsed bytes differ from fixed HEAD'):
        verifier.read_bound_csv(relative,label='dual digest witness')


def test_cached_boundary_binding_does_not_follow_moved_head(tmp_path, monkeypatch):
    obj=committed_boundary_engine(tmp_path,monkeypatch)
    relative=f'data/market/raw/2026/{DATES[0]}/daily.csv'
    verifier=obj._primary_verifier;verifier.bind(relative,label='old HEAD')
    initial=verifier.head
    target=tmp_path/relative;target.write_bytes(target.read_bytes()+b'\n');commit_boundary_fixture(tmp_path)
    assert p0.GitHeadInputVerifier(tmp_path).head != initial
    with pytest.raises(p0.PrimaryDGenerationError,match='parsed bytes differ from fixed HEAD'):
        verifier.read_bound_csv(relative,label='must retain old HEAD')
    assert verifier.head == initial


@pytest.mark.parametrize('alias', ['symlink', 'hardlink'])
def test_cached_boundary_read_rejects_filesystem_alias(tmp_path, monkeypatch, alias):
    obj=committed_boundary_engine(tmp_path,monkeypatch)
    relative=f'data/market/raw/2026/{DATES[0]}/daily.csv'
    verifier=obj._primary_verifier;verifier.bind(relative,label='cached regular file')
    target=tmp_path/relative;other=tmp_path/'same-bytes.csv';other.write_bytes(target.read_bytes())
    target.unlink()
    if alias=='symlink':target.symlink_to(other)
    else:p0.os.link(other,target)
    with pytest.raises(p0.PrimaryDGenerationError):verifier.read_bound_csv(relative,label='alias witness')


def test_unrelated_head_move_keeps_original_verified_bytes_and_binding(tmp_path, monkeypatch):
    obj=committed_boundary_engine(tmp_path,monkeypatch)
    verifier=obj._primary_verifier;initial=verifier.head
    relative=f'data/market/raw/2026/{DATES[0]}/daily.csv'
    before=verifier.bind(relative,label='original HEAD')
    (tmp_path/'unrelated.txt').write_text('new commit must not replace captured HEAD')
    commit_boundary_fixture(tmp_path)
    assert p0.GitHeadInputVerifier(tmp_path).head != initial
    frame,after=verifier.read_bound_csv(relative,label='retained immutable HEAD')
    assert not frame.empty and before==after and verifier.head==initial
