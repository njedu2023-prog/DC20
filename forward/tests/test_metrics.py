import pytest

from forward.ledger import freeze_day, new_ledger, record_verification
from forward.metrics import statistics
from .test_ledger import DATES, make_day, make_ledger, make_verification


def test_empty_epoch_has_null_performance_not_old_totals():
    result = statistics(new_ledger("brand-new"))
    for group in result["groups"].values():
        assert group["frozen_slots"] == 0
        assert group["cumulative_return"] is None
        assert group["promotion_hit_rate"] is None
        assert group["conditional_positive_net_rate"] is None


def test_pending_slots_count_but_do_not_create_returns():
    result = statistics(make_ledger())
    group = result["groups"]["promotion_top3_combined"]
    assert group["frozen_slots"] == 3
    assert group["d_days"] == 1
    assert group["t1_settled"] == 0
    assert group["cumulative_return"] is None


def test_separate_promotion_slots_and_profit_slots():
    ledger = make_ledger()
    verification = make_verification(ledger["days"][0], returns=[0.1, -0.2, 0.3])
    verification["rows"][1]["t_status"] = "NOT_PROMOTED"
    ledger = record_verification(ledger, "20260907", verification)
    groups = statistics(ledger)["groups"]
    assert groups["promotion_top1"]["mean_net_return"] == 0.1
    assert groups["promotion_top2"]["mean_net_return"] == -0.2
    assert groups["promotion_top3"]["mean_net_return"] == 0.3
    assert groups["profit_top1"]["mean_net_return"] == 0.3
    assert groups["profit_top2"]["mean_net_return"] == -0.2
    combined = groups["promotion_top3_combined"]
    assert combined["promotion_hit_rate"] == pytest.approx(2 / 3)
    assert combined["conditional_positive_net_rate"] == pytest.approx(2 / 3)
    assert combined["cumulative_return"] == pytest.approx(0.2 / 3)


def test_partial_group_day_not_in_equity_but_individual_resolved_slot_is():
    ledger = make_ledger()
    verification = make_verification(ledger["days"][0], status="MISSING")
    settled = make_verification(ledger["days"][0], returns=[0.1, 0.2, 0.3])
    verification["rows"][0] = settled["rows"][0]
    groups = statistics(record_verification(ledger, "20260907", verification))["groups"]
    assert groups["promotion_top1"]["cumulative_return"] == pytest.approx(0.1)
    assert groups["promotion_top3_combined"]["t1_settled"] == 1
    assert groups["promotion_top3_combined"]["mean_net_return"] == 0.1
    assert groups["promotion_top3_combined"]["cumulative_return"] is None
    assert groups["promotion_top3_combined"]["unresolved_slots"] == 2
    assert groups["promotion_top3_combined"]["unresolved_group_days"] == 1
    assert groups["promotion_top3_combined"]["equity_basis"] == "resolved_cohort_reference_only"
    assert groups["promotion_top3_combined"]["complete"] is False


def test_no_fill_zero_in_slot_combination_but_not_conditional_trades():
    ledger = make_ledger(2)
    verification = make_verification(ledger["days"][0], returns=[0.2, 0.2])
    verification["rows"][1] = make_verification(ledger["days"][0], status="NO_FILL")["rows"][1]
    groups = statistics(record_verification(ledger, "20260907", verification))["groups"]
    combined = groups["profit_top2_combined"]
    assert combined["resolved_slots"] == 2
    assert combined["t1_settled"] == 1
    assert combined["conditional_trades"] == 1
    assert combined["no_fill_slots"] == 1
    assert combined["mean_net_return"] == 0.2
    assert combined["conditional_positive_net_rate"] == 1
    assert combined["cumulative_return"] == pytest.approx(0.1)
    assert groups["profit_top1"]["mean_net_return"] is None
    assert groups["profit_top1"]["cumulative_return"] == 0


def test_missing_rank_no_padding_and_empty_day_no_equity():
    groups = statistics(make_ledger(1))["groups"]
    assert groups["promotion_top2"]["frozen_slots"] == 0
    assert groups["promotion_top3_combined"]["frozen_slots"] == 1
    assert groups["profit_top2_combined"]["frozen_slots"] == 1
    assert all(group["cumulative_return"] is None for group in statistics(make_ledger(0))["groups"].values())


def test_daily_equal_slot_compounding_and_drawdown():
    ledger = make_ledger(1)
    second = make_day(1, signal="20260908", generated="2026-09-08T13:00:00Z")
    ledger = freeze_day(ledger, second, open_dates=DATES, now_utc="2026-09-08T13:00:01Z")
    ledger = record_verification(ledger, "20260907", make_verification(ledger["days"][0], returns=[0.1]))
    ledger = record_verification(ledger, "20260908", make_verification(ledger["days"][1], as_of="20260910", returns=[-0.2]))
    group = statistics(ledger)["groups"]["profit_top1"]
    assert group["resolved_group_days"] == 2
    assert group["cumulative_return"] == pytest.approx(-0.12)
    assert group["max_drawdown"] == pytest.approx(-0.2)
    assert group["mean_net_return"] == pytest.approx(-0.05)
