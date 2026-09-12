"""Offline v2 source integration tests; fabricated fixtures are not truth imports."""
from __future__ import annotations

import copy
import csv
import json

import pytest

from work.profit_1000_upgrade import auction_truth, labels, minute_truth, policy_v2
from work.profit_1000_upgrade.test_labels import (
    CODE, D, NEXT, T, T1, auction, binding, case, daily, write_csv,
)


def canonical(root, *, day=T, price=10, volume=2_000_000, rows=None, response_code=0):
    items = [[CODE, day, price, volume, price * volume, 10]] if rows is None else rows
    data = {"fields": list(auction_truth.FIELDS), "items": items, "count": 0, "has_more": False}
    raw = json.dumps({"code": response_code, "msg": "permission denied" if response_code == 2002 else "",
                      "data": data if response_code == 0 else None}).encode()
    bodies = auction_truth.source_bytes(raw, day, request=auction_truth.request_contract(day),
                                        fetched_at_utc="2026-09-15T08:00:00Z", network_request_performed=True)
    paths = auction_truth.source_paths(root, day)
    for path, body in zip(paths, bodies):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return paths


def research_minutes(root, day=T1, *, code=CODE, price=9.8):
    rows = [{"ts_code": code, "trade_time": stamp, "open": price, "close": price,
             "high": price, "low": price, "vol": 100, "amount": 1000}
            for stamp in minute_truth._minute.expected_bar_ends(day)]
    fields = list(minute_truth.FIELDS)
    raw = json.dumps({"code": 0, "data": {"fields": fields,
                     "items": [[row[key] for key in fields] for row in rows],
                     "count": 0, "has_more": False}}).encode()
    bodies = minute_truth.source_bytes(raw, day, code,
                                       request_params=minute_truth.request_parameters(day, code),
                                       fetched_at_utc="2026-09-15T08:00:00Z")
    paths = minute_truth.paths(root, day, code)
    for path, body in zip(paths, bodies):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return paths


@pytest.fixture
def v2_case(case):
    root, manifest = case
    manifest["rows"][0]["shadow_max_price"] = None
    manifest.update(policy_v2.CONTRACT)
    manifest["plan_version"] = "v2"
    canonical(root)
    research_minutes(root)
    return root, manifest


def row_for(case, asof=T1):
    return labels.build_labels(case[0], case[1], as_of_date=asof)["rows"][0]


def test_negative_new_policy_cost_and_actual_maturity(v2_case):
    root, manifest = v2_case
    result = labels.build_labels(root, manifest, as_of_date=T1)
    row = result["rows"][0]
    assert row["label_status"] == labels.SETTLED
    assert row["net_return"] == pytest.approx(-0.0245)
    assert row["conditional_net_return"] == row["slot_net_return"] == row["net_return"]
    assert row["entry_price_source"] == "TUSHARE_STK_AUCTION"
    assert row["entry_price"] == 10 and row["auction_trade_observed"] is True
    assert row["auction_request_receipt_observed"] is True
    assert row["capacity_proxy_verified"] and not row["actual_capacity_verified"]
    assert row["minute_source_observed"] is True and row["minute_source_observed_dates"] == [T1]
    assert row["label_maturity_at"] == "2026-09-14T10:00:00+08:00"
    assert row["label_available_at"] == "2026-09-14T15:00:00+08:00"
    assert row["cohort_complete"]
    for key, value in policy_v2.CONTRACT.items():
        assert result[key] == row[key] == value
    assert result["source_policy_contract"] == dict(policy_v2.CONTRACT)
    assert result["production_activation_allowed"] is row["production_activation_allowed"] is False
    assert result["natural_forward_ledger_rewritten"] is result["old_open_exit_labels_consumed"] is False
    paths = {b["path"] for b in result["source_files"]}
    for pair in (auction_truth.source_paths(root, T), minute_truth.paths(root, T1, CODE)):
        assert {binding(root, path)["path"] for path in pair} <= paths
    assert all("stk_auction_o" not in path and "exit_1000_1m/" not in path for path in paths)


@pytest.mark.parametrize("change", ["partial", "unknown", "nested", "extra", "plan_only", "cap"])
def test_v2_policy_cannot_fall_through_to_v1(v2_case, change):
    root, manifest = v2_case
    if change == "partial":
        manifest.pop("minute_source_policy_id")
    elif change == "unknown":
        manifest["entry_policy_id"] = "unrecognized_policy"
    elif change == "nested":
        manifest["source_policy_contract"] = {**dict(policy_v2.CONTRACT), "minute_time_semantics": "confirmed"}
    elif change == "extra":
        manifest["unknown_source_policy_id"] = "unknown"
    elif change == "plan_only":
        for key in policy_v2.CONTRACT:
            manifest.pop(key)
    else:
        manifest["rows"][0]["shadow_max_price"] = 10.5
    with pytest.raises(ValueError):
        labels.build_labels(root, manifest, as_of_date=T1)


def test_legacy_outputs_do_not_claim_new_policy(case):
    row = row_for(case)
    assert row["label_status"] == labels.SETTLED
    assert row["entry_policy_id"] == "research_auction_or_open_frozen_cap_v1"
    assert "minute_source_observed" not in row


def test_old_source_loaders_are_never_called_and_old_files_unchanged(v2_case, monkeypatch):
    root, manifest = v2_case
    old = auction(root, price=99)
    old_binding = binding(root, old)
    def forbidden(*args, **kwargs):
        raise AssertionError("old truth must not be consumed by v2")
    monkeypatch.setattr(labels.settlement, "_verified_auction_sources_v2", forbidden)
    monkeypatch.setattr(labels.settlement, "_entry_price_v2", forbidden)
    monkeypatch.setattr(labels, "load_exit_minutes", forbidden)
    assert row_for(v2_case)["label_status"] == labels.SETTLED
    assert binding(root, old) == old_binding


def test_missing_covered_canonical_does_not_fallback(v2_case):
    root, _ = v2_case
    for path in auction_truth.source_paths(root, T):
        path.unlink()
    row = row_for(v2_case)
    assert row["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION"
    assert row["missing_evidence_kind"] == "canonical_auction_0925"
    assert row["missing_evidence_date"] == T and row["missing_evidence_code"] == CODE
    assert row["proxy_fill"] is row["slot_net_return"] is row["entry_price"] is None
    assert row["minute_source_observed"] is row["auction_request_receipt_observed"] is False


@pytest.mark.parametrize("kind", ["empty", "missing_code", "permission"])
def test_valid_receipt_unavailable_auction_is_unknown_capacity_proxy(v2_case, kind):
    root, _ = v2_case
    if kind == "permission":
        canonical(root, response_code=2002)
    else:
        canonical(root, rows=[] if kind == "empty" else [["600001.SH", T, 10, 2_000_000, 20_000_000, 10]])
    row = row_for(v2_case)
    assert row["label_status"] == labels.SETTLED
    assert row["entry_price_source"] == "DAILY_OPEN_PROXY" and row["entry_price"] == 10
    assert row["capacity_evidence"] == "UNKNOWN" and row["capacity_proxy_verified"] is False
    assert row["actual_capacity_verified"] is False and row["auction_trade_observed"] is None
    assert row["auction_request_receipt_observed"] is True and row["entry_price_fallback_reason"]


def test_zero_auction_volume_is_no_fill_not_daily_open_buy(v2_case):
    root, _ = v2_case
    canonical(root, volume=0)
    minute_truth.paths(root, T1, CODE)[0].write_text("must not read later minutes")
    row = row_for(v2_case)
    assert row["label_status"] == "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"
    assert row["proxy_fill"] == 0 and row["slot_net_return"] == 0
    assert row["entry_price"] is row["net_return"] is row["conditional_net_return"] is None
    assert row["entry_price_source"] == "TUSHARE_STK_AUCTION"
    assert row["auction_trade_observed"] is False and row["minute_source_observed"] is False
    assert row["minute_source_observed_dates"] == [] and row["cohort_complete"]


@pytest.mark.parametrize("kind", ["pair", "sha", "endpoint"])
def test_invalid_canonical_is_pending_never_fallback(v2_case, kind):
    root, _ = v2_case
    path, meta = auction_truth.source_paths(root, T)
    if kind == "pair":
        path.unlink()
    elif kind == "sha":
        path.write_bytes(path.read_bytes().replace(b"20000000", b"30000000"))
    else:
        payload = json.loads(meta.read_bytes())
        payload["endpoint"] = "stk_auction_o"
        meta.write_text(json.dumps(payload))
    row = row_for(v2_case)
    assert row["label_status"] == "PENDING_INVALID_CANONICAL_AUCTION_SOURCE"
    assert row["proxy_fill"] is row["slot_net_return"] is row["entry_price"] is None
    assert row["minute_source_observed"] is False and not row["cohort_complete"]


@pytest.mark.parametrize("kind", ["price", "positive_auction_suspended_daily"])
def test_conflicting_source_is_not_zero_no_fill(v2_case, kind):
    root, _ = v2_case
    if kind == "price":
        canonical(root, price=10.01)
    else:
        daily(root, T, volume=0)
    row = row_for(v2_case)
    assert row["label_status"] == "PENDING_ENTRY_SOURCE_CONFLICT"
    assert row["proxy_fill"] is row["slot_net_return"] is row["net_return"] is None
    assert not row["cohort_complete"] and row["minute_source_observed"] is False


@pytest.mark.parametrize("kind", ["capacity", "opening_limit"])
def test_known_nonfill_has_no_observed_minute_claim(v2_case, kind):
    root, _ = v2_case
    if kind == "capacity":
        canonical(root, volume=100_000)
    else:
        canonical(root, price=11)
        daily(root, T, price=11)
    row = row_for(v2_case)
    assert row["label_status"] == ("NO_FILL_CAPACITY" if kind == "capacity" else "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED")
    assert row["proxy_fill"] == row["slot_net_return"] == 0
    assert row["minute_source_observed"] is False and row["net_return"] is None


def test_missing_new_minutes_never_reuses_valid_old_minutes(v2_case):
    root, _ = v2_case
    for path in minute_truth.paths(root, T1, CODE):
        path.unlink()
    row = row_for(v2_case)
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert row["missing_evidence_kind"] == "research_exit_1000_1m_0931"
    assert row["missing_evidence_date"] == T1 and row["missing_evidence_code"] == CODE
    assert row["proxy_fill"] == 1 and row["slot_net_return"] is None
    assert row["minute_source_observed"] is False


@pytest.mark.parametrize("kind", ["pair", "sha", "semantics"])
def test_invalid_new_minutes_are_precise_pending(v2_case, monkeypatch, kind):
    root, _ = v2_case
    path, meta = minute_truth.paths(root, T1, CODE)
    if kind == "pair":
        path.unlink()
    elif kind == "sha":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        original = minute_truth.load
        def wrong(*args):
            result = original(*args)
            result["provider_timestamp_semantics_confirmed"] = True
            return result
        monkeypatch.setattr(minute_truth, "load", wrong)
    row = row_for(v2_case)
    assert row["label_status"] == "PENDING_EXIT_INVALID_RESEARCH_MINUTE_SOURCE"
    assert row["slot_net_return"] is row["net_return"] is None
    assert not row["cohort_complete"]


def test_no_future_sources_are_read(v2_case, monkeypatch):
    def forbidden(*args):
        raise AssertionError("future source read")
    monkeypatch.setattr(minute_truth, "load", forbidden)
    assert row_for(v2_case, T)["label_status"] == "PENDING_T1"
    monkeypatch.setattr(auction_truth, "load", forbidden)
    row = row_for(v2_case, D)
    assert row["label_status"] == "PENDING_T"
    assert row["minute_source_observed"] is row["auction_request_receipt_observed"] is False


def test_held_then_missing_retains_observed_dates_and_never_zero(v2_case):
    root, _ = v2_case
    daily(root, T1, price=11)
    research_minutes(root, price=11)
    daily(root, NEXT, price=11.5, pre=11, up=12.1, down=9.9)
    row = row_for(v2_case, NEXT)
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert row["missing_evidence_date"] == NEXT
    assert row["minute_source_observed"] is True and row["minute_source_observed_dates"] == [T1]
    assert row["slot_net_return"] is None
    research_minutes(root, NEXT, price=11.5)
    row = row_for(v2_case, NEXT)
    assert row["label_status"] == labels.SETTLED and row["held_limit_up_sessions"] == 1
    assert row["actual_exit_date"] == NEXT and row["minute_source_observed_dates"] == [T1, NEXT]
    assert row["net_return"] == pytest.approx(0.1455)


def test_same_t_full_candidate_cache_keeps_all_rows_and_loads_once(v2_case, monkeypatch):
    root, manifest = v2_case
    second = "000001.SZ"
    manifest["rows"].append({**copy.deepcopy(manifest["rows"][0]), "ts_code": second, "promotion_rank": 2})
    manifest["expected_candidate_codes"][D].append(second)
    for day in (T, T1):
        for name in ("daily", "stk_limit"):
            path = root / f"data/market/raw/2026/{day}/{name}.csv"
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
            write_csv(path, rows + [{**rows[0], "ts_code": second}])
    canonical(root, rows=[[code, T, 10, 2_000_000, 20_000_000, 10] for code in (CODE, second, "600001.SH")])
    original, calls = auction_truth.load, []
    def tracked(*args):
        calls.append(args[1])
        return original(*args)
    monkeypatch.setattr(auction_truth, "load", tracked)
    result = labels.build_labels(root, manifest, as_of_date=T1)
    assert calls == [T]
    assert [row["entry_price_source"] for row in result["rows"]] == ["TUSHARE_STK_AUCTION"] * 2
    assert result["rows"][0]["net_return"] < 0
    assert result["rows"][1]["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert result["cohorts_by_date"][D]["complete"] is False
    assert all(row["cohort_complete"] is False for row in result["rows"])


def test_bound_sources_cannot_change_after_loading(v2_case, monkeypatch):
    root, manifest = v2_case
    original = minute_truth.load
    def changed(*args):
        result = original(*args)
        path = auction_truth.source_paths(root, T)[0]
        path.write_bytes(path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(minute_truth, "load", changed)
    with pytest.raises(ValueError, match="SHA mismatch"):
        labels.build_labels(root, manifest, as_of_date=T1)


@pytest.mark.parametrize("corrupt", [False, True])
def test_precoverage_missing_explicit_fallback_but_present_invalid_source_blocks(v2_case, corrupt):
    root, manifest = v2_case
    signal, buy, sell = "20241226", "20241227", "20241230"
    manifest["rows"][0].update(signal_date=signal, feature_as_of_date=signal,
                               feature_available_at="2024-12-26T23:59:00+08:00")
    manifest["expected_candidate_codes"] = {signal: [CODE]}
    for day, price in ((buy, 10), (sell, 9.8)):
        folder = root / f"data/market/raw/{day[:4]}/{day}"
        write_csv(folder / "daily.csv", [{"ts_code": CODE, "trade_date": day, "open": price,
                  "high": price, "low": price, "close": price, "pre_close": 10, "vol": 24_000}])
        write_csv(folder / "stk_limit.csv", [{"ts_code": CODE, "trade_date": day,
                  "pre_close": 10, "up_limit": 11, "down_limit": 9}])
    research_minutes(root, sell)
    if corrupt:
        canonical(root, day=buy, rows=[])[1].write_text("invalid source must not downgrade to daily open")
    row = row_for(v2_case, sell)
    if corrupt:
        assert row["label_status"] == "PENDING_INVALID_CANONICAL_AUCTION_SOURCE"
        assert row["entry_price"] is row["slot_net_return"] is None
        assert row["minute_source_observed"] is False
    else:
        assert row["label_status"] == labels.SETTLED
        assert row["entry_price_source"] == "DAILY_OPEN_PROXY"
        assert row["entry_price_fallback_reason"] == "HISTORY_BEFORE_CANONICAL_COVERAGE"
        assert row["auction_request_receipt_observed"] is False
        assert row["auction_trade_observed"] is None and row["capacity_evidence"] == "UNKNOWN"
        assert row["capacity_proxy_verified"] is False
        assert row["minute_source_observed_dates"] == [sell]


def test_new_source_bindings_can_lock_exact_repeat(v2_case):
    root, manifest = v2_case
    original = labels.build_labels(root, manifest, as_of_date=T1)
    manifest["truth_sources"] = original["source_files"]
    repeated = labels.build_labels(root, manifest, as_of_date=T1)
    assert repeated["rows"] == original["rows"]
    assert repeated["source_files"] == original["source_files"]
    canonical(root, price=10.01)
    with pytest.raises(ValueError, match="SHA mismatch"):
        labels.build_labels(root, manifest, as_of_date=T1)
