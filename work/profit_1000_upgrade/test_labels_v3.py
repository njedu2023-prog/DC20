"""Offline v3 label wiring tests; all auction/minute sources are synthetic."""
from __future__ import annotations

import copy
import csv
import json

import pytest

from work.profit_1000_upgrade import auction_truth, auction_truth_v3, labels_v3 as labels, policy_v2, policy_v3
from work.profit_1000_upgrade.test_labels import CODE, D, T, T1, NEXT, case, daily, binding, write_csv
from work.profit_1000_upgrade.test_labels_v2 import research_minutes


def canonical(root, *, day=T, price=10, vol=2_000_000, amount=20_000_000, pre=10, rows=None, api_code=0):
    data = {"fields": list(auction_truth_v3.FIELDS), "items": [[CODE, day, price, vol, amount, pre]] if rows is None else rows,
            "count": 0, "has_more": False}
    raw = json.dumps({"code": api_code, "msg": "没有权限" if api_code else "",
                      "data": None if api_code else data}).encode()
    bodies = auction_truth_v3.source_bytes(raw, day, request=auction_truth_v3.request_contract(day),
        fetched_at_utc="2026-09-15T08:00:00Z", network_request_performed=True)
    paths = auction_truth_v3.source_paths(root, day)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, bodies):
        path.write_bytes(body)
    return paths


@pytest.fixture
def v3_case(case):
    root, manifest = case
    manifest["rows"][0]["shadow_max_price"] = None
    manifest.update(policy_v3.CONTRACT)
    manifest["plan_version"] = "v3"
    canonical(root)
    research_minutes(root)
    return root, manifest


def result(case, asof=T1):
    return labels.build_labels(case[0], case[1], as_of_date=asof)


def single(case, asof=T1):
    return result(case, asof)["rows"][0]


def test_v3_loss_cost_once_and_same_exit_policy(v3_case):
    report = result(v3_case)
    row = report["rows"][0]
    assert report["schema_version"] == "dc20_profit_1000_research_labels_v3"
    assert row["label_status"] == labels.SETTLED
    assert row["net_return"] == pytest.approx(-.0245)
    assert row["net_return"] == row["conditional_net_return"] == row["slot_net_return"]
    assert row["entry_price"] == 10 and row["entry_price_source"] == "TUSHARE_STK_AUCTION"
    assert row["price_qualified"] and row["capacity_proxy_verified"] and row["capacity_amount"] == 20_000_000
    assert row["label_maturity_at"] == "2026-09-14T10:00:00+08:00"
    assert row["label_available_at"] == "2026-09-14T15:00:00+08:00"
    assert row["basis"] == policy_v3.BASIS and row["known_before_0925"] is False
    assert row["actual_capacity_verified"] is row["actual_execution_claimed"] is row["production_activation_allowed"] is False
    assert row["cohort_complete"] and report["cohorts_by_date"][D]["complete"]
    assert policy_v3.validate_label_contract(row) == dict(policy_v3.CONTRACT)
    assert report["source_policy_contract"] == dict(policy_v3.CONTRACT)


@pytest.mark.parametrize("amount", [None, True, -1, "NaN", 0, 20_000_001, 19_999_999])
def test_unknown_amount_keeps_price_proxy_and_negative_return_not_capacity_no_fill(v3_case, amount):
    canonical(v3_case[0], amount=amount)
    row = single(v3_case)
    assert row["label_status"] == labels.SETTLED and row["net_return"] == pytest.approx(-.0245)
    assert row["entry_price"] == 10 and row["entry_price_fallback_reason"] is None
    assert row["capacity_proxy_verified"] is False and row["capacity_evidence"] == "UNKNOWN"
    assert row["capacity_amount"] is row["entry_price_evidence"]["amount"] is None
    assert row["reported_auction_amount"] == amount
    assert row["proxy_fill"] == 1


@pytest.mark.parametrize("values,expected", [
    ({"price": None}, "PENDING_ENTRY_INVALID_PRICE"), ({"price": 0}, "PENDING_ENTRY_INVALID_PRICE"),
    ({"price": 10.01}, "PENDING_ENTRY_PRICE_DAILY_OPEN_CONFLICT"),
    ({"vol": 100.5}, "PENDING_ENTRY_INVALID_SHARE_VOLUME"),
    ({"vol": True}, "PENDING_ENTRY_INVALID_SHARE_VOLUME"), ({"pre": 0}, "PENDING_ENTRY_INVALID_PRE_CLOSE"),
    ({"vol": 0, "amount": 0, "price": 10}, "PENDING_ENTRY_ZERO_VOLUME_DATA_CONFLICT"),
    ({"vol": 0, "amount": 0, "price": None, "pre": 0}, "PENDING_ENTRY_INVALID_PRE_CLOSE"),
])
def test_pending_candidate_qualification_never_reaches_zero_or_exit(v3_case, values, expected):
    canonical(v3_case[0], **values)
    row = single(v3_case)
    assert row["label_status"] == expected
    assert row["entry_qualification_status"].startswith("PENDING_CANONICAL_")
    assert row["proxy_fill"] is row["slot_net_return"] is row["conditional_net_return"] is row["net_return"] is None
    assert row["minute_source_observed"] is False
    assert row["entry_price_fallback_reason"] is None and not row["cohort_complete"]


@pytest.mark.parametrize("kind,expected", [("capacity", "NO_FILL_CAPACITY"), ("opening_up", "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"),
                                         ("zero_auction", "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"), ("suspended", "NO_FILL_SUSPENDED")])
def test_only_existing_verified_no_fill_rules_get_slot_zero(v3_case, kind, expected):
    root, _ = v3_case
    if kind == "capacity":
        canonical(root, vol=100_000, amount=1_000_000)
    elif kind == "opening_up":
        daily(root, T, price=11)
        canonical(root, price=11, amount=22_000_000)
    elif kind == "zero_auction":
        canonical(root, price=None, vol=0, amount=0)
    else:
        daily(root, T, volume=0)
        canonical(root, rows=[])
    row = single(v3_case)
    assert row["label_status"] == expected and row["proxy_fill"] == 0 and row["slot_net_return"] == 0
    assert row["conditional_net_return"] is row["net_return"] is None and row["minute_source_observed"] is False
    assert row["cohort_complete"] and row["label_available_date"] == T
    if kind == "zero_auction":
        assert row["capacity_proxy_verified"] is False and row["entry_price"] is None


def test_daily_zero_volume_conflicts_with_positive_observed_auction(v3_case):
    daily(v3_case[0], T, volume=0)
    row = single(v3_case)
    assert row["label_status"] == "PENDING_ENTRY_SOURCE_CONFLICT"
    assert row["proxy_fill"] is row["slot_net_return"] is None


@pytest.mark.parametrize("auction_kind", ["empty", "missing_row", "denied", "zero_trade", "positive_trade"])
@pytest.mark.parametrize("changed_prices", [{"high": 10.01}, {"low": 9.99},
                                          {"close": 10.01, "high": 10.01}])
def test_zero_daily_volume_nonflat_ohlc_is_pending_not_suspended_or_slot_zero(v3_case, auction_kind, changed_prices):
    root, _ = v3_case
    daily(root, T, volume=0)
    daily_path = root / f"data/market/raw/2026/{T}/daily.csv"
    with daily_path.open() as handle:
        rows = list(csv.DictReader(handle))
    rows[0].update(changed_prices)
    write_csv(daily_path, rows)
    if auction_kind == "zero_trade":
        canonical(root, price=None, vol=0, amount=0)
    elif auction_kind != "positive_trade":
        canonical(root, rows=[["600001.SH", T, 10, 100, 1000, 10]] if auction_kind == "missing_row" else [],
                  api_code=2002 if auction_kind == "denied" else 0)
    report = result(v3_case)
    row = report["rows"][0]
    assert row["label_status"] == "PENDING_INVALID_SOURCE"
    assert row["t_daily_volume"] == 0 and row["t_daily_ohlc_flat_at_cent"] is False
    assert row["proxy_fill"] is row["slot_net_return"] is row["net_return"] is row["conditional_net_return"] is None
    assert row["entry_price"] is None and row["minute_source_observed"] is False
    assert not row["cohort_complete"] and report["cohorts_by_date"][D]["terminal_rows"] == 0


@pytest.mark.parametrize("high,expected", [(10.0049, "NO_FILL_SUSPENDED"),
                                          (10.005, "PENDING_INVALID_SOURCE"),
                                          (10.01, "PENDING_INVALID_SOURCE")])
def test_zero_volume_flat_check_uses_existing_half_up_cent_tick(v3_case, high, expected):
    root, _ = v3_case
    daily(root, T, volume=0)
    daily_path = root / f"data/market/raw/2026/{T}/daily.csv"
    with daily_path.open() as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["high"] = high
    write_csv(daily_path, rows)
    canonical(root, rows=[])
    row = single(v3_case)
    assert row["label_status"] == expected
    assert row["t_daily_ohlc_flat_at_cent"] is (expected == "NO_FILL_SUSPENDED")


@pytest.mark.parametrize("flat_flag", [None, False, 1])
def test_contract_rejects_zero_volume_no_fill_without_flat_ohlc_evidence(v3_case, flat_flag):
    daily(v3_case[0], T, volume=0)
    canonical(v3_case[0], rows=[])
    row = single(v3_case)
    assert row["label_status"] == "NO_FILL_SUSPENDED"
    row["t_daily_ohlc_flat_at_cent"] = flat_flag
    with pytest.raises(ValueError, match="flat T OHLC"):
        policy_v3.validate_label_contract(row)


def test_original_noncent_auction_price_not_replaced_by_daily_open(v3_case):
    canonical(v3_case[0], price="9.99999", amount=None)
    row = single(v3_case)
    assert row["label_status"] == labels.SETTLED
    assert row["entry_price"] == row["entry_price_evidence"]["reported_auction_price"] == "9.99999"
    assert row["net_return"] == pytest.approx(9.8 / 9.99999 - 1 - .0045)
    assert row["price_reporting_precision_confirmed"] is False


def test_missing_minutes_keep_pending_not_open_exit_or_zero(v3_case):
    for path in labels.minute_truth.paths(v3_case[0], T1, CODE):
        path.unlink()
    row = single(v3_case)
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert row["proxy_fill"] == 1 and row["net_return"] is row["slot_net_return"] is None


def test_limit_hold_to_actual_later_day_keeps_original_exit_timing(v3_case):
    root, _ = v3_case
    daily(root, T1, price=11)
    research_minutes(root, price=11)
    assert single(v3_case)["label_status"] == "PENDING_EXIT_LIMIT_UP_HELD"
    daily(root, NEXT, price=11.5, pre=11, up=12.1, down=9.9)
    research_minutes(root, NEXT, price=11.5)
    row = single(v3_case, NEXT)
    assert row["label_status"] == labels.SETTLED and row["held_limit_up_sessions"] == 1
    assert row["actual_exit_date"] == row["label_available_date"] == NEXT
    assert row["net_return"] == pytest.approx(.1455)


def test_complete_candidate_cohort_keeps_bad_member_and_frozen_features(v3_case):
    root, manifest = v3_case
    other = "600001.SH"
    second = copy.deepcopy(manifest["rows"][0])
    second.update(ts_code=other, promotion_rank=2)
    manifest["rows"].append(second)
    manifest["expected_candidate_codes"][D].append(other)
    for name in ("daily", "stk_limit"):
        path = root / f"data/market/raw/2026/{T}/{name}.csv"
        with path.open() as handle:
            existing = list(csv.DictReader(handle))
        extra = dict(existing[0], ts_code=other)
        write_csv(path, existing + [extra])
    canonical(root, rows=[[CODE, T, 10, 2_000_000, 20_000_000, 10], [other, T, None, 100, 1000, 10]])
    report = result(v3_case)
    assert len(report["rows"]) == report["cohorts_by_date"][D]["expected_rows"] == 2
    assert report["cohorts_by_date"][D]["terminal_rows"] == 1 and not report["cohorts_by_date"][D]["complete"]
    assert [r["promotion_rank"] for r in report["rows"]] == [1, 2]
    assert [r["features"] for r in report["rows"]] == [source["features"] for source in manifest["rows"]]
    assert report["rows"][1]["label_status"] == "PENDING_ENTRY_INVALID_PRICE"


@pytest.mark.parametrize("kind", ["empty", "denied", "missing_row"])
def test_qualified_unavailability_only_daily_open_fallback(v3_case, kind):
    canonical(v3_case[0], rows=[] if kind == "empty" else [["600001.SH", T, 10, 100, 1000, 10]],
              api_code=2002 if kind == "denied" else 0)
    row = single(v3_case)
    assert row["label_status"] == labels.SETTLED and row["entry_price_source"] == "DAILY_OPEN_PROXY"
    assert row["capacity_proxy_verified"] is row["price_qualified"] is False


def test_missing_v3_source_does_not_consume_v2_or_old_price(v3_case):
    root, _ = v3_case
    for path in auction_truth_v3.source_paths(root, T):
        path.unlink()
    old_paths = auction_truth.source_paths(root, T)
    old_paths[0].parent.mkdir(parents=True)
    for path in old_paths:
        path.write_bytes(b"must not read old source")
    row = single(v3_case)
    assert row["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION" and row["entry_price"] is None


@pytest.mark.parametrize("change", ["old", "missing", "partial", "nested", "cap", "wrong_plan"])
def test_v3_contract_cannot_fall_through_to_v2_or_cap(v3_case, change):
    manifest = v3_case[1]
    if change == "old":
        manifest.update(policy_v2.CONTRACT)
    elif change == "missing":
        for key in policy_v3.CONTRACT:
            manifest.pop(key)
    elif change == "partial":
        manifest.pop("minute_source_policy_id")
    elif change == "nested":
        manifest["source_policy_contract"] = dict(policy_v2.CONTRACT)
    elif change == "cap":
        manifest["rows"][0]["shadow_max_price"] = 10.5
    else:
        manifest["plan_version"] = "v2"
    with pytest.raises(ValueError):
        result(v3_case)


def test_future_truth_not_read_and_minute_policy_unchanged(v3_case):
    root, _ = v3_case
    for path in auction_truth_v3.source_paths(root, T):
        path.write_bytes(b"future corrupt")
    row = single(v3_case, D)
    assert row["label_status"] == "PENDING_T" and row["net_return"] is None
    assert policy_v3.CONTRACT["minute_time_semantics"] == policy_v2.CONTRACT["minute_time_semantics"]
    assert policy_v3.CONTRACT["minute_source_policy_id"] == policy_v2.CONTRACT["minute_source_policy_id"]


def test_bound_truth_sources_detect_replaced_source(v3_case):
    root, manifest = v3_case
    report = result(v3_case)
    manifest["truth_sources"] = report["source_files"]
    canonical(root, amount=20_000_001)
    with pytest.raises(ValueError, match="SHA"):
        result(v3_case)


@pytest.mark.parametrize("field,value", [("actual_capacity_verified", True), ("actual_execution_claimed", True),
                                         ("known_before_0925", True), ("price_reporting_precision_confirmed", True),
                                         ("production_activation_allowed", True), ("round_trip_cost_rate", .009),
                                         ("minute_source_observed", False), ("label_available_at", "2026-09-14T10:00:00+08:00"),
                                         ("label_maturity_at", "2026-09-14T09:30:00+08:00"),
                                         ("label_status", "NO_FILL_SOURCE_ERROR"), ("capacity_amount", None)])
def test_policy_rejects_forged_observation_cost_timing_and_unknown_no_fill(v3_case, field, value):
    row = single(v3_case)
    row[field] = value
    with pytest.raises(ValueError):
        policy_v3.validate_label_contract(row)


def test_policy_unknown_capacity_cannot_be_recast_as_no_fill(v3_case):
    canonical(v3_case[0], amount=None)
    row = single(v3_case, T)
    row.update(label_status="NO_FILL_CAPACITY", proxy_fill=0, slot_net_return=0,
               label_available_date=T, label_available_at="2026-09-11T15:00:00+08:00",
               label_maturity_at="2026-09-11T09:25:00+08:00")
    with pytest.raises(ValueError, match="capacity"):
        policy_v3.validate_label_contract(row)


@pytest.mark.parametrize("change", ["loaded", "source", "capacity_reason", "float_fill", "entry_pending", "qualified"])
def test_policy_rejects_mismatched_or_unbound_entry_qualification(v3_case, change):
    row = single(v3_case)
    if change == "loaded":
        row["entry_price_evidence"]["loaded_source_qualification_claimed"] = False
    elif change == "source":
        row["entry_price_evidence"]["entry_price_source"] = "DAILY_OPEN_PROXY"
    elif change == "capacity_reason":
        row["capacity_reason"] = row["entry_price_evidence"]["capacity_reason"] = "UNKNOWN"
    elif change == "float_fill":
        row["proxy_fill"] = 1.0
    elif change == "entry_pending":
        row["entry_qualification_status"] = row["entry_price_evidence"]["status"] = "PENDING_CANONICAL_INVALID_PRICE"
    else:
        row["price_qualified"] = row["entry_price_evidence"]["price_qualified"] = False
    with pytest.raises(ValueError):
        policy_v3.validate_label_contract(row)


@pytest.mark.parametrize("status", ["NO_FILL_SOURCE_ERROR", "NO_FILL_ABOVE_FROZEN_CAP"])
def test_unknown_kernel_status_cannot_complete_cohort(v3_case, monkeypatch, status):
    monkeypatch.setattr(labels, "resolve_exit_1000", lambda *args: (None, status))
    with pytest.raises(ValueError, match="unknown status"):
        result(v3_case)


def test_legacy_settlement_entry_helpers_are_never_called(v3_case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("old entry path forbidden")
    monkeypatch.setattr(labels.settlement, "_verified_auction_sources_v2", forbidden)
    monkeypatch.setattr(labels.settlement, "_entry_price_v2", forbidden)
    assert single(v3_case)["label_status"] == labels.SETTLED
