from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest

from top10decision.decision.shadow_exit_minute_truth import expected_bar_ends, minute_paths, source_bytes
from work.profit_1000_upgrade.labels import SCHEMA, SETTLED, build_labels


BASE = Path(__file__).resolve().parents[2]
D, T, T1, NEXT = "20260910", "20260911", "20260914", "20260915"
CODE = "600000.SH"


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def binding(root, path):
    return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def daily(root, day, *, price=10, pre=10, up=11, down=9, code=CODE, volume=24000):
    directory = root / f"data/market/raw/2026/{day}"
    write_csv(directory / "daily.csv", [{"ts_code": code, "trade_date": day,
              "open": price, "high": price, "low": price, "close": price, "pre_close": pre, "vol": volume}])
    write_csv(directory / "stk_limit.csv", [{"ts_code": code, "trade_date": day, "pre_close": pre,
              "up_limit": up, "down_limit": down}])


def minutes(root, day=T1, *, price=9.8, code=CODE):
    rows = [{"ts_code": code, "trade_time": stamp, "open": price, "close": price,
             "high": price, "low": price, "vol": 100, "amount": 1000} for stamp in expected_bar_ends(day)]
    raw, meta = source_bytes(rows, day, code, fetched_at_utc="2026-09-15T08:00:00Z")
    path, meta_path = minute_paths(root, day, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    meta_path.write_bytes(meta)
    return path, meta_path


def auction(root, *, price=10, amount=20_000_000):
    path = root / f"data/market/raw/2026/{T}/stk_auction_o.csv"
    rows = [{"ts_code": CODE, "trade_date": T, "close": price, "amount": amount}]
    write_csv(path, rows)
    normalized = path.read_text().replace("\r\n", "\n")
    meta = {"schema_version": "decision_auction_truth_v1", "source": "tushare:stk_auction_o",
            "trade_date": T, "immutable": True, "credential_persisted": False,
            "rows": 1, "fields": list(rows[0]), "sha256": hashlib.sha256(normalized.encode()).hexdigest()}
    path.with_suffix(".meta.json").write_text(json.dumps(meta))
    return path


@pytest.fixture
def case(tmp_path):
    calendar = tmp_path / "data/market/trade_cal_sse.csv"
    calendar.parent.mkdir(parents=True)
    shutil.copyfile(BASE / "data/market/trade_cal_sse.csv", calendar)
    source = tmp_path / "historical_features.json"
    source.write_text('{"contract":"audited D-only reconstructed feature source"}')
    row = {"signal_date": D, "ts_code": CODE, "stage": 2, "promotion_rank": 1,
           "feature_as_of_date": D, "feature_available_at": "2026-09-10T23:59:00+08:00",
           "features": {"promotion_probability": 0.6, "path_change": -0.1}, "shadow_max_price": 10.5}
    manifest = {"schema_version": SCHEMA, "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
                "feature_columns": ["promotion_probability", "path_change"],
                "source_bindings": [binding(tmp_path, source)], "rows": [row],
                "expected_candidate_codes": {D: [CODE]}}
    daily(tmp_path, T)
    daily(tmp_path, T1, price=9.8)
    minutes(tmp_path)
    return tmp_path, manifest


def test_negative_return_and_cost_once_and_actual_maturity(case):
    root, manifest = case
    out = build_labels(root, manifest, as_of_date=T1)
    row = out["rows"][0]
    assert row["label_status"] == SETTLED
    assert row["net_return"] == pytest.approx(-0.0245)
    assert row["slot_net_return"] == row["conditional_net_return"] == row["net_return"]
    assert row["entry_price_source"] == "DAILY_OPEN_PROXY"
    assert row["label_maturity_at"] == "2026-09-14T10:00:00+08:00"
    assert row["label_available_at"] == "2026-09-14T15:00:00+08:00"
    assert row["label_available_date"] == T1
    assert row["cohort_complete"] and out["cohorts_by_date"][D]["complete"]
    assert out["historical_counterfactual"] and not out["old_open_exit_labels_consumed"]
    assert not row["actual_execution_claimed"] and not row["capacity_proxy_verified"] and not row["actual_capacity_verified"]


def test_final_auction_preferred_and_bound_with_observed_capacity(case):
    root, manifest = case
    path = auction(root)
    out = build_labels(root, manifest, as_of_date=T1)
    row = out["rows"][0]
    assert row["entry_price_source"] == "TUSHARE_STK_AUCTION_O" and row["capacity_proxy_verified"]
    assert not row["actual_capacity_verified"]
    assert {binding(root, path)["path"], binding(root, path.with_suffix(".meta.json"))["path"]} <= {b["path"] for b in out["source_files"]}


@pytest.mark.parametrize("kind", ["orphan", "sha", "price"])
def test_present_invalid_auction_never_falls_back(case, kind):
    root, manifest = case
    path = auction(root, price=-1 if kind == "price" else 10)
    if kind == "orphan":
        path.unlink()
    if kind == "sha":
        path.write_text(path.read_text().replace("20000000", "30000000"))
    row = build_labels(root, manifest, as_of_date=T1)["rows"][0]
    assert row["label_status"] == "PENDING_INVALID_SOURCE"
    assert row["net_return"] is None and row["slot_net_return"] is None


def test_missing_minutes_not_legacy_open_exit_fallback(case):
    root, manifest = case
    for path in minute_paths(root, T1, CODE):
        path.unlink()
    legacy = root / "data/decision_executable_profit/forward/settlements/settlement_20260910.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"net_return":0.99,"exit_open_price":99}')
    out = build_labels(root, manifest, as_of_date=T1)
    row = out["rows"][0]
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert row["missing_evidence_date"] == T1 and row["missing_evidence_code"] == CODE and row["missing_evidence_kind"] == "exit_1000_1m"
    assert row["proxy_fill"] == 1 and row["net_return"] is None and row["slot_net_return"] is None
    assert not out["cohorts_by_date"][D]["complete"]
    assert all("settlements" not in b["path"] for b in out["source_files"])


def test_asof_before_t_never_reads_future_entry_or_exit(case):
    root, manifest = case
    (root / f"data/market/raw/2026/{T}/daily.csv").write_text("bad future source")
    out = build_labels(root, manifest, as_of_date=D)
    assert out["rows"][0]["label_status"] == "PENDING_T"
    assert len(out["source_files"]) == 2


def test_asof_t_never_reads_t1_minutes(case):
    root, manifest = case
    minute_paths(root, T1, CODE)[0].write_text("corrupt not yet available")
    row = build_labels(root, manifest, as_of_date=T)["rows"][0]
    assert row["label_status"] == "PENDING_T1" and row["net_return"] is None


def test_limit_held_label_matures_at_later_actual_exit(case):
    root, manifest = case
    daily(root, T1, price=11)
    minutes(root, price=11)
    held = build_labels(root, manifest, as_of_date=T1)["rows"][0]
    assert held["label_status"] == "PENDING_EXIT_LIMIT_UP_HELD" and held["slot_net_return"] is None
    daily(root, NEXT, price=11.5, pre=11, up=12.1, down=9.9)
    minutes(root, NEXT, price=11.5)
    row = build_labels(root, manifest, as_of_date=NEXT)["rows"][0]
    assert row["label_status"] == SETTLED and row["held_limit_up_sessions"] == 1
    assert row["label_available_date"] == NEXT and row["actual_exit_date"] == NEXT
    assert row["net_return"] == pytest.approx(0.1455)


@pytest.mark.parametrize("no_fill", ["cap", "opening_up", "capacity", "suspended"])
def test_known_no_fill_only_slot_zero_conditional_null(case, no_fill):
    root, manifest = case
    if no_fill == "cap":
        manifest["rows"][0]["shadow_max_price"] = 9.9
    elif no_fill == "opening_up":
        daily(root, T, price=11)
        manifest["rows"][0]["shadow_max_price"] = 11
    elif no_fill == "capacity":
        auction(root, amount=1_000_000)
    else:
        daily(root, T, volume=0)
    row = build_labels(root, manifest, as_of_date=T)["rows"][0]
    assert row["label_status"].startswith("NO_FILL_")
    assert row["proxy_fill"] == 0 and row["slot_net_return"] == 0
    assert row["net_return"] is None and row["conditional_net_return"] is None
    assert row["label_available_date"] == T and row["cohort_complete"]


@pytest.mark.parametrize("change", ["future_time", "future_day", "no_timezone", "outcome_feature", "extra_feature", "duplicate", "missing_member", "wrong_date", "bad_stage", "wrong_code", "source_sha"])
def test_candidate_contract_rejects_leakage_and_membership_drift(case, change):
    root, manifest = case
    row = manifest["rows"][0]
    if change == "future_time":
        row["feature_available_at"] = "2026-09-11T09:25:00+08:00"
    elif change == "future_day":
        row["feature_as_of_date"] = T
    elif change == "no_timezone":
        row["feature_available_at"] = "2026-09-10T23:59:00"
    elif change == "outcome_feature":
        manifest["feature_columns"].append("net_return")
        row["features"]["net_return"] = 0.1
    elif change == "extra_feature":
        row["features"]["future_price"] = 1
    elif change == "duplicate":
        manifest["rows"].append(copy.deepcopy(row))
    elif change == "missing_member":
        manifest["expected_candidate_codes"][D].append("600001.SH")
    elif change == "wrong_date":
        row["exec_date"] = T1
    elif change == "bad_stage":
        row["stage"] = 4
    elif change == "wrong_code":
        row["ts_code"] = "600000"
    else:
        manifest["source_bindings"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        build_labels(root, manifest, as_of_date=T1)


def test_one_missing_stock_blocks_complete_date_but_preserves_good_row(case):
    root, manifest = case
    other = copy.deepcopy(manifest["rows"][0])
    other.update(ts_code="600001.SH", promotion_rank=2)
    manifest["rows"].append(other)
    manifest["expected_candidate_codes"][D].append("600001.SH")
    out = build_labels(root, manifest, as_of_date=T1)
    assert len(out["rows"]) == 2 and out["rows"][0]["label_status"] == SETTLED
    assert out["rows"][1]["label_status"] == "PENDING_T_MISSING_DAILY_OR_LIMITS"
    assert not any(r["cohort_complete"] for r in out["rows"])


def test_candidate_csv_binding_and_explicit_features(case):
    root, manifest = case
    row = manifest.pop("rows")[0]
    row["features_json"] = json.dumps(row.pop("features"))
    path = root / "research/candidates.csv"
    write_csv(path, [row])
    manifest["candidate_file"] = binding(root, path)
    assert build_labels(root, manifest, as_of_date=T1)["rows"][0]["label_status"] == SETTLED
    path.write_text(path.read_text().replace("0.6", "0.9"))
    with pytest.raises(ValueError, match="SHA mismatch"):
        build_labels(root, manifest, as_of_date=T1)


def test_frozen_truth_sha_replay_rejects_later_source_changes(case):
    root, manifest = case
    out = build_labels(root, manifest, as_of_date=T1)
    manifest["truth_sources"] = [b for b in out["source_files"] if "data/market/raw" in b["path"] or "exit_1000" in b["path"]]
    assert build_labels(root, manifest, as_of_date=T1)["rows"][0]["label_status"] == SETTLED
    path = root / f"data/market/raw/2026/{T1}/daily.csv"
    path.write_text(path.read_text().replace("9.8", "9.9"))
    with pytest.raises(ValueError, match="SHA mismatch"):
        build_labels(root, manifest, as_of_date=T1)


def test_auction_daily_conflict_is_unknown_not_no_fill(case):
    root, manifest = case
    auction(root, price=10.1)
    row = build_labels(root, manifest, as_of_date=T1)["rows"][0]
    assert row["label_status"] == "PENDING_ENTRY_SOURCE_CONFLICT"
    assert row["slot_net_return"] is None and row["proxy_fill"] is None


def test_missing_d_feature_is_not_globally_imputed(case):
    root, manifest = case
    manifest["rows"][0]["features"]["path_change"] = None
    manifest["feature_columns"].append("five_year_pre_streak_1d_return")
    manifest["rows"][0]["features"]["five_year_pre_streak_1d_return"] = -0.02
    row = build_labels(root, manifest, as_of_date=T1)["rows"][0]
    assert row["features"]["path_change"] is None
    assert row["features"]["five_year_pre_streak_1d_return"] == -0.02


@pytest.mark.parametrize("kind", ["wrong_code", "wrong_day", "duplicate"])
def test_market_identity_mismatch_and_duplicates_cannot_settle(case, kind):
    root, manifest = case
    path = root / f"data/market/raw/2026/{T}/daily.csv"
    data = path.read_text()
    if kind == "wrong_code":
        data = data.replace(CODE, "600001.SH")
    elif kind == "wrong_day":
        data = data.replace(T, T1)
    else:
        data += data.splitlines()[1] + "\n"
    path.write_text(data)
    row = build_labels(root, manifest, as_of_date=T1)["rows"][0]
    assert row["label_status"].startswith("PENDING_") and row["slot_net_return"] is None


@pytest.fixture
def full_market_auction_case(case):
    root, manifest = case
    second, unrelated = "600001.SH", "600999.SH"
    candidate = copy.deepcopy(manifest["rows"][0])
    candidate.update(ts_code=second, promotion_rank=2)
    manifest["rows"].append(candidate)
    manifest["expected_candidate_codes"][D].append(second)
    for day in (T, T1):
        for kind in ("daily", "stk_limit"):
            path = root / f"data/market/raw/2026/{day}/{kind}.csv"
            with path.open(newline="") as handle:
                original = list(csv.DictReader(handle))
            write_csv(path, original + [dict(original[0], ts_code=second)])
    minutes(root, code=second)
    path = root / f"data/market/raw/2026/{T}/stk_auction_o.csv"
    rows = [{"ts_code": code, "trade_date": T, "close": 10, "amount": 20_000_000}
            for code in (CODE, second, unrelated)]
    write_csv(path, rows)
    meta = {"schema_version": "decision_auction_truth_v1", "source": "tushare:stk_auction_o",
            "trade_date": T, "immutable": True, "credential_persisted": False, "rows": len(rows),
            "fields": list(rows[0]), "sha256": hashlib.sha256(path.read_text().encode()).hexdigest()}
    path.with_suffix(".meta.json").write_text(json.dumps(meta))
    return root, manifest, path, second, unrelated


def test_auction_cache_retains_all_same_t_candidates_only_after_full_validation(full_market_auction_case, monkeypatch):
    from work.profit_1000_upgrade.labels import settlement
    root, manifest, path, second, unrelated = full_market_auction_case
    verifier, entry = settlement._verified_auction_sources_v2, settlement._entry_price_v2
    validated, consumed = [], []
    def verify(actual_root, day):
        sources = verifier(actual_root, day)
        assert set(sources[0]["rows"]) == {CODE, second, unrelated}
        validated.append(sources)
        return sources
    def price(code, daily_path, daily_open, actual_root, sources):
        assert set(sources[0]["rows"]) == {CODE, second}
        assert unrelated not in sources[0]["rows"]
        # Exact original file and metadata bindings are retained, not replaced
        # with a SHA of the reduced in-memory row set.
        assert sources[0]["file"] == validated[0][0]["file"]
        assert sources[0]["metadata"] == validated[0][0]["metadata"]
        actual = entry(code, daily_path, daily_open, actual_root, sources)
        assert actual == entry(code, daily_path, daily_open, actual_root, validated[0])
        consumed.append(code)
        return actual
    monkeypatch.setattr(settlement, "_verified_auction_sources_v2", verify)
    monkeypatch.setattr(settlement, "_entry_price_v2", price)
    result = build_labels(root, manifest, as_of_date=T1)
    assert len(validated) == 1 and consumed == [CODE, second]
    assert all(row["label_status"] == SETTLED and row["net_return"] == pytest.approx(-.0245) for row in result["rows"])
    assert binding(root, path) in result["source_files"]


@pytest.mark.parametrize("corruption", ["wrong_unrelated_date", "source_sha"])
def test_auction_cache_cannot_hide_bad_non_candidate_source_row(full_market_auction_case, corruption):
    root, manifest, path, _, unrelated = full_market_auction_case
    raw = path.read_text()
    if corruption == "wrong_unrelated_date":
        path.write_text(raw.replace(f"{unrelated},{T}", f"{unrelated},{T1}"))
        metadata = json.loads(path.with_suffix(".meta.json").read_text())
        metadata["sha256"] = hashlib.sha256(path.read_text().encode()).hexdigest()
        path.with_suffix(".meta.json").write_text(json.dumps(metadata))
    else:
        # Even a change in an uncached, non-candidate row invalidates the whole
        # source's original SHA; filtering must not make it disappear.
        path.write_text(raw.replace(f"{unrelated},{T},10,", f"{unrelated},{T},11,"))
    result = build_labels(root, manifest, as_of_date=T1)
    assert all(row["label_status"] == "PENDING_INVALID_SOURCE" for row in result["rows"])
    assert all(row["slot_net_return"] is None for row in result["rows"])


def test_auction_cache_preserves_final_whole_source_sha_recheck(full_market_auction_case, monkeypatch):
    from work.profit_1000_upgrade.labels import settlement
    root, manifest, path, _, unrelated = full_market_auction_case
    original = settlement._entry_price_v2
    calls = []
    def change_after_verified_read(*args):
        result = original(*args)
        if not calls:
            path.write_text(path.read_text().replace(f"{unrelated},{T},10,", f"{unrelated},{T},11,"))
        calls.append(args[0])
        return result
    monkeypatch.setattr(settlement, "_entry_price_v2", change_after_verified_read)
    with pytest.raises(ValueError, match="source SHA mismatch"):
        build_labels(root, manifest, as_of_date=T1)


def test_candidate_auction_daily_conflict_is_unchanged_with_reduced_cache(full_market_auction_case):
    root, manifest, path, second, _ = full_market_auction_case
    path.write_text(path.read_text().replace(f"{second},{T},10,", f"{second},{T},10.1,"))
    metadata = json.loads(path.with_suffix(".meta.json").read_text())
    metadata["sha256"] = hashlib.sha256(path.read_text().encode()).hexdigest()
    path.with_suffix(".meta.json").write_text(json.dumps(metadata))
    result = build_labels(root, manifest, as_of_date=T1)
    assert result["rows"][0]["label_status"] == SETTLED
    assert result["rows"][1]["label_status"] == "PENDING_ENTRY_SOURCE_CONFLICT"
    assert result["rows"][1]["slot_net_return"] is None
    assert not result["cohorts_by_date"][D]["complete"]
