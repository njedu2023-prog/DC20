from __future__ import annotations

import json
import math
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
