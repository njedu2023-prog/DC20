"""Synthetic v2 epoch tests, with no production activation, writes or imports."""
import copy
import io
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from forward.daybook import (attach_profit_day, freeze_promotion_day, new_epoch,
                             record_day_verification)
from forward.daybook_metrics import GROUPS, statistics_from_daybook
from forward.ledger import _hash
from forward.settlement import verify_day
from .test_daybook import epoch, profit_input, promotion, promotion_input
from .test_ledger import DATES, make_verification


def profit(day):
    supplied = profit_input(day)
    date = day["signal_date"]
    supplied["generated_at_utc"] = f"{date[:4]}-{date[4:6]}-{date[6:]}T13:02:00Z"
    return attach_profit_day(epoch(), day, supplied,
        now_utc=f"{date[:4]}-{date[4:6]}-{date[6:]}T13:02:01Z")


def truth(day, *, status="SETTLED", returns=None, costs=45.0, as_of=None):
    event = make_verification(day, status=status, returns=returns,
                              as_of=as_of or day["exit_date"])
    event["costs_bps"] = costs
    for row in event["rows"]:
        if row["t1_status"] == "SETTLED":
            row["exit_price"] = row["entry_price"] * (1 + row["net_return"] + costs / 10000)
    return record_day_verification(day, event)


def stats(day=None, *, attach=True, sidecar=None, **kwargs):
    day = promotion() if day is None else day
    return statistics_from_daybook(epoch(), [day],
        {day["signal_date"]: profit(day)} if attach else None,
        {day["signal_date"]: sidecar} if sidecar else None, **kwargs)


@pytest.mark.parametrize("active", [False, True])
def test_empty_epoch_is_valid_null_performance_without_legacy_totals(active):
    current = epoch() if active else new_epoch("inactive-empty")
    result = statistics_from_daybook(current, [])
    assert result["epoch_sha256"] == current["epoch_sha256"]
    assert result["coverage"]["provided_promotion_days"] == 0
    assert result["coverage"]["schedule_coverage_verified"] is False
    assert result["legacy_statistics_imported"] is False
    assert result["publication_verified"] is False
    for group in result["groups"].values():
        assert group["frozen_slots"] == 0
        assert group["cumulative_return"] is None
        assert group["promotion_hit_rate"] is None
        assert group["conditional_positive_net_rate"] is None


def test_promotion_top3_statistics_never_wait_for_profit():
    day = promotion()
    result = stats(day, attach=False, sidecar=truth(day, returns=[.1, -.2, .3]))
    groups = result["groups"]
    assert groups["promotion_top1"]["mean_net_return"] == .1
    assert groups["promotion_top2"]["mean_net_return"] == -.2
    assert groups["promotion_top3"]["mean_net_return"] == .3
    assert groups["promotion_top3_combined"]["frozen_slots"] == 3
    assert groups["promotion_top3_combined"]["complete"] is True
    assert groups["profit_top1"]["frozen_slots"] == 0
    assert groups["profit_top1"]["missing_ranking_days"] == 1
    assert groups["profit_top1"]["cumulative_return"] is None
    assert groups["profit_top1"]["complete"] is False
    assert result["coverage"]["profit_missing_dates"] == [day["signal_date"]]
    assert result["coverage"]["complete"] is False


def test_profit_slots_are_their_own_frozen_ranks_not_promotion_top2():
    day = promotion()
    event = make_verification(day, returns=[.1, -.2, .3])
    event["rows"][1]["t_status"] = "NOT_PROMOTED"
    result = stats(day, sidecar=record_day_verification(day, event))
    groups = result["groups"]
    assert groups["profit_top1"]["mean_net_return"] == .3
    assert groups["profit_top2"]["mean_net_return"] == -.2
    assert groups["profit_top2_combined"]["promotion_hit_rate"] == .5
    assert groups["profit_top2_combined"]["cumulative_return"] == pytest.approx(.05)
    assert groups["promotion_top3_combined"]["promotion_hit_rate"] == pytest.approx(2 / 3)
    assert result["coverage"]["complete"] is True


@pytest.mark.parametrize("status", ["PENDING", "MISSING", "EXIT_BLOCKED"])
def test_unresolved_is_not_zero_return_or_complete_equity(status):
    day = promotion()
    as_of = day["signal_date"] if status == "PENDING" else day["exit_date"]
    result = stats(day, sidecar=truth(day, status=status, as_of=as_of))
    for group in result["groups"].values():
        assert group["frozen_slots"] > 0
        assert group["resolved_slots"] == 0
        assert group["unresolved_slots"] == group["frozen_slots"]
        assert group["cumulative_return"] is None
        assert group["mean_net_return"] is None
        assert group["complete"] is False
    # Presence/validity of artifacts is separate from maturity.
    assert result["coverage"]["complete"] is True


def test_t_result_can_be_counted_before_t1_without_future_market_reads():
    day = promotion(1)
    code = day["rows"][0]["ts_code"]
    market = {day["exec_date"]: [{"ts_code": code, "trade_date": day["exec_date"],
        "open": 10, "high": 11, "low": 10, "close": 11,
        "up_limit": 11, "down_limit": 9, "vol": 100}]}
    event = verify_day(day, market, DATES, as_of_date=day["exec_date"])
    result = stats(day, attach=False, sidecar=record_day_verification(day, event))
    group = result["groups"]["promotion_top1"]
    assert group["t_validated"] == 1
    assert group["promotion_hit_rate"] == 1
    assert group["t1_settled"] == 0
    assert group["mean_net_return"] is None


def test_partial_group_day_never_enters_combined_equity():
    day = promotion()
    event = make_verification(day, status="MISSING")
    event["rows"][0] = make_verification(day, returns=[.1, .2, .3])["rows"][0]
    result = stats(day, sidecar=record_day_verification(day, event))
    combined = result["groups"]["promotion_top3_combined"]
    assert result["groups"]["promotion_top1"]["cumulative_return"] == pytest.approx(.1)
    assert combined["mean_net_return"] == .1
    assert combined["unresolved_slots"] == 2
    assert combined["unresolved_group_days"] == 1
    assert combined["cumulative_return"] is None
    assert combined["equity_basis"] == "resolved_cohort_reference_only"


def test_no_fill_only_zeros_slot_not_conditional_trade_returns():
    day = promotion(2)
    event = make_verification(day, returns=[.2, .2])
    event["rows"][1] = make_verification(day, status="NO_FILL")["rows"][1]
    result = stats(day, sidecar=record_day_verification(day, event))
    combined = result["groups"]["profit_top2_combined"]
    assert combined["resolved_slots"] == 2
    assert combined["no_fill_slots"] == 1
    assert combined["conditional_trades"] == 1
    assert combined["conditional_positive_net_rate"] == 1
    assert combined["mean_net_return"] == .2
    assert combined["cumulative_return"] == pytest.approx(.1)
    assert result["groups"]["profit_top1"]["mean_net_return"] is None
    assert result["groups"]["profit_top1"]["cumulative_return"] == 0


@pytest.mark.parametrize("n", [0, 1, 2, 3, 10])
def test_real_n_and_absent_rank_are_not_padded(n):
    day = promotion(n)
    result = stats(day, sidecar=truth(day))
    assert result["groups"]["promotion_top3_combined"]["frozen_slots"] == min(n, 3)
    assert result["groups"]["profit_top2_combined"]["frozen_slots"] == min(n, 2)
    assert result["groups"]["promotion_top3"]["frozen_slots"] == int(n >= 3)
    if n == 0:
        assert result["coverage"]["empty_promotion_dates"] == [day["signal_date"]]
        assert result["coverage"]["profit_ready_empty_dates"] == [day["signal_date"]]
        assert result["coverage"]["profit_ready_days"] == 1
        assert all(group["cumulative_return"] is None for group in result["groups"].values())
        missing = stats(day, attach=False, sidecar=truth(day))
        assert missing["coverage"]["profit_ready_days"] == 0
        assert missing["coverage"]["profit_ready_empty_dates"] == []
        assert missing["coverage"]["profit_missing_dates"] == [day["signal_date"]]


def test_corrupt_profit_is_isolated_while_promotion_truth_survives():
    day = promotion()
    bad = profit(day)
    bad["rows"][0]["profit_score"] += 1
    result = statistics_from_daybook(epoch(), [day], {day["signal_date"]: bad},
        {day["signal_date"]: truth(day)})
    assert result["coverage"]["profit_invalid_dates"] == [day["signal_date"]]
    assert result["coverage"]["profit_missing_dates"] == []
    assert result["coverage"]["complete"] is False
    assert result["groups"]["promotion_top1"]["cumulative_return"] == pytest.approx(.1)
    assert result["groups"]["promotion_top1"]["complete"] is True
    assert result["groups"]["profit_top1"]["invalid_ranking_days"] == 1
    assert result["groups"]["profit_top1"]["mean_net_return"] is None
    assert result["source_records"][0]["profit_sha256"] is None


@pytest.mark.parametrize("field", ["freeze_sha256", "epoch_sha256", "exec_date", "rows"])
def test_invalid_truth_binding_is_not_counted_as_a_success_or_loss(field):
    day = promotion()
    bad = truth(day)
    if field == "rows":
        bad["verifications"][-1]["rows"][0]["net_return"] = 99
    else:
        bad[field] = "20260909" if field == "exec_date" else "0" * 64
    bad["sidecar_sha256"] = _hash({key: value for key, value in bad.items() if key != "sidecar_sha256"})
    result = stats(day, sidecar=bad)
    assert result["coverage"]["truth_invalid_dates"] == [day["signal_date"]]
    assert result["coverage"]["complete"] is False
    for group in result["groups"].values():
        assert group["t_validated"] == 0
        assert group["mean_net_return"] is None
        assert group["cumulative_return"] is None
        assert group["invalid_truth_days"] == 1


def test_different_fee_policy_preserves_t_but_excludes_t1_returns():
    day = promotion()
    sidecar = truth(day, costs=75)
    before = copy.deepcopy(sidecar)
    result = stats(day, sidecar=sidecar)
    assert sidecar == before
    assert result["coverage"]["truth_methodology_mismatch_dates"] == [day["signal_date"]]
    assert result["coverage"]["truth_available_days"] == 1
    assert result["coverage"]["return_methodology_accepted_days"] == 0
    assert result["auxiliary_errors"][0]["reason"] == "COST_METHODOLOGY_MISMATCH_T_RETAINED"
    for group in result["groups"].values():
        assert group["t_validated"] == group["frozen_slots"]
        assert group["promotion_hit_rate"] == 1
        assert group["t1_settled"] == 0
        assert group["mean_net_return"] is None
        assert group["cumulative_return"] is None
        assert group["methodology_mismatch_days"] == 1
    accepted = stats(day, sidecar=sidecar, expected_costs_bps=75)
    assert accepted["coverage"]["complete"] is True
    assert accepted["groups"]["promotion_top1"]["mean_net_return"] == .1


def test_latest_truth_is_not_counted_twice_or_erased_by_later_missing():
    day = promotion()
    sidecar = truth(day)
    incoming = make_verification(day, status="MISSING", as_of="20260910")
    sidecar = record_day_verification(day, incoming, existing=sidecar)
    result = stats(day, sidecar=sidecar)
    assert result["groups"]["promotion_top3_combined"]["t1_settled"] == 3
    assert result["groups"]["promotion_top3_combined"]["cumulative_return"] == pytest.approx(.1)
    assert result["source_records"][0]["truth_as_of_date"] == "20260910"


def test_multiple_days_are_chronological_reference_equity_not_capital_claim():
    first = promotion(1)
    second = freeze_promotion_day(epoch(), promotion_input(1, signal="20260908", generated="2026-09-08T13:00:00Z"),
        open_dates=DATES, now_utc="2026-09-08T13:00:01Z")
    result = statistics_from_daybook(epoch(), [first, second],
        {day["signal_date"]: profit(day) for day in (first, second)},
        {first["signal_date"]: truth(first, returns=[.1]), second["signal_date"]: truth(second, returns=[-.2])})
    group = result["groups"]["profit_top1"]
    assert group["resolved_group_days"] == 2
    assert group["cumulative_return"] == pytest.approx(-.12)
    assert group["max_drawdown"] == pytest.approx(-.2)
    assert group["mean_net_return"] == pytest.approx(-.05)
    assert group["equity_basis"] == "resolved_cohort_reference_only"
    assert result["coverage"]["basis"] == "provided_frozen_promotion_days_only"
    missing_second_profit = statistics_from_daybook(epoch(), [first, second],
        {first["signal_date"]: profit(first)},
        {first["signal_date"]: truth(first, returns=[.1]), second["signal_date"]: truth(second, returns=[-.2])})
    partial = missing_second_profit["groups"]["profit_top1"]
    assert partial["cumulative_return"] == pytest.approx(.1)
    assert partial["missing_ranking_days"] == 1
    assert partial["complete"] is False
    assert missing_second_profit["groups"]["promotion_top1"]["complete"] is True
    assert missing_second_profit["coverage"]["complete"] is False
    with pytest.raises(ValueError, match="chronological"):
        statistics_from_daybook(epoch(), [second, first])
    with pytest.raises(ValueError, match="unique"):
        statistics_from_daybook(epoch(), [first, first])


def test_orphan_and_invalid_auxiliary_keys_are_reported_not_imported():
    day = promotion()
    result = statistics_from_daybook(epoch(), [day],
        {"20260904": profit(day), "not-a-date": {}, 123: {}},
        {"20260910": truth(day)})
    assert len(result["auxiliary_errors"]) == 4
    assert result["coverage"]["profit_ready_days"] == 0
    assert result["coverage"]["truth_available_days"] == 0
    assert result["coverage"]["complete"] is False
    assert result["groups"]["promotion_top3_combined"]["frozen_slots"] == 3
    assert result["groups"]["profit_top1"]["frozen_slots"] == 0


def test_wrong_epoch_and_mutated_promotion_are_hard_failures():
    day = promotion()
    for current in (new_epoch("inactive"), new_epoch("another", "2026-09-07T07:00:00Z")):
        with pytest.raises(ValueError):
            statistics_from_daybook(current, [day])
    mutated = copy.deepcopy(day)
    mutated["rows"][0]["promotion_rank"] = True
    with pytest.raises(ValueError):
        statistics_from_daybook(epoch(), [mutated])
    mutated = copy.deepcopy(day)
    mutated["promotion_top3"][0]["name"] = "wrong"
    with pytest.raises(ValueError):
        statistics_from_daybook(epoch(), [mutated])


def test_inactive_epoch_cannot_import_auxiliary_history_even_without_promotions():
    with pytest.raises(ValueError, match="inactive"):
        statistics_from_daybook(new_epoch("inactive"), [], {"20260907": profit(promotion())})


@pytest.mark.parametrize("fees", [True, "45", -1, float("nan"), float("inf")])
def test_fee_policy_is_explicit_finite_and_nonnegative(fees):
    with pytest.raises(ValueError):
        statistics_from_daybook(epoch(), [], expected_costs_bps=fees)


@pytest.mark.parametrize("args", [((), None, None), ([], [], None), ([], None, [])])
def test_argument_shapes_are_not_silently_reinterpreted(args):
    with pytest.raises(ValueError):
        statistics_from_daybook(epoch(), *args)


def test_results_are_hash_bound_detached_pure_and_deterministic():
    day = promotion()
    current, days = epoch(), [day]
    profits, truths = {day["signal_date"]: profit(day)}, {day["signal_date"]: truth(day)}
    before = copy.deepcopy((current, days, profits, truths))
    with patch("builtins.open", side_effect=AssertionError("no I/O")), \
         patch.object(io, "open", side_effect=AssertionError("no I/O")), \
         patch.object(Path, "write_bytes", side_effect=AssertionError("no write")), \
         patch.object(socket.socket, "connect", side_effect=AssertionError("no network")):
        result = statistics_from_daybook(current, days, profits, truths)
    assert (current, days, profits, truths) == before
    assert set(result["groups"]) == set(GROUPS)
    expected = _hash({key: value for key, value in result.items() if key != "statistics_sha256"})
    assert result["statistics_sha256"] == expected
    assert statistics_from_daybook(current, days, profits, truths) == result
    record = result["source_records"][0]
    assert record["promotion_freeze_sha256"] == day["freeze_sha256"]
    assert record["profit_sha256"] == profits[day["signal_date"]]["profit_sha256"]
    assert record["truth_sidecar_sha256"] == truths[day["signal_date"]]["sidecar_sha256"]
    result["coverage"]["provided_signal_dates"].append("20990101")
    assert (current, days, profits, truths) == before
