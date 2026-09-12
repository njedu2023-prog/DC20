from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

from top10decision.decision.shadow_exit_1000 import (
    EXIT_POLICY_ID, MINUTE_SCHEMA, SETTLED_STATUS, resolve_exit_1000,
)


CODE = "600000.SH"
DATES = ["20260911", "20260914", "20260915", "20260916", "20260917"]
DAY = "20260914"


def minute_day(day=DAY, price=10.0):
    rows = []
    for start in ("09:31", "13:01"):
        first = datetime.strptime(day + " " + start, "%Y%m%d %H:%M")
        for index in range(120):
            rows.append({"bar_end": (first + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S"),
                         "open": price, "high": price, "low": price,
                         "close": price, "vol": 100.0})
    return {"schema_version": MINUTE_SCHEMA, "ts_code": CODE, "trade_date": day,
            "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END",
            "interval_seconds": 60, "complete_session": True, "rows": rows,
            "source_files": [{"path": f"data/market/exit_1000_1m/2026/{day}/600000_SH.csv", "sha256": "a" * 64}]}


def bar(payload, hhmm, **values):
    row = next(row for row in payload["rows"] if row["bar_end"][11:16] == hhmm)
    row.update(values)
    return row


def tail(payload, hhmm, price):
    for row in payload["rows"]:
        if row["bar_end"][11:16] >= hhmm:
            row.update(open=price, high=price, low=price, close=price)


def records(minutes, *, pre_close=10.0, up=11.0, down=9.0):
    rows = minutes["rows"]
    daily = {"ts_code": CODE, "trade_date": minutes["trade_date"],
             "open": rows[0]["open"], "high": max(row["high"] for row in rows),
             "low": min(row["low"] for row in rows), "close": rows[-1]["close"],
             "pre_close": pre_close, "vol": sum(row["vol"] for row in rows)}
    limits = {"ts_code": CODE, "trade_date": minutes["trade_date"],
              "up_limit": up, "down_limit": down, "pre_close": pre_close}
    return {"daily": daily, "limits": limits, "minutes": minutes}


def replay(sessions, *, as_of=DAY, scheduled=DAY, entry=10.0, t_close=10.0):
    calls = []
    def load(kind):
        def callback(day):
            calls.append((kind, day))
            return sessions.get(day, {}).get(kind)
        return callback
    result = resolve_exit_1000(DATES, scheduled, as_of, CODE, entry, t_close,
                              load("daily"), load("limits"), load("minutes"))
    return result, calls


def test_time_exit_is_next_bar_open_not_t1_daily_open_or_current_bar_close():
    m = minute_day()
    bar(m, "10:00", open=10.0, high=10.4, low=10.0, close=10.3)
    bar(m, "10:01", open=10.2, high=10.8, low=10.1, close=10.7)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["exit_policy_id"] == EXIT_POLICY_ID
    assert out["scheduled_exit_date"] == DAY
    assert out["exit_price"] == 10.2 and out["gross_return"] == pytest.approx(0.02)
    assert out["decision_time"] == "2026-09-14T10:00:00+08:00"
    assert out["actual_exit_time"] == out["execution_bar_start"] == out["decision_time"]
    assert out["execution_bar_end"] == "2026-09-14T10:01:00+08:00"
    assert out["exit_time_semantics"] == "NEXT_BAR_OPEN_MINUTE_PROXY"
    assert out["actual_execution_claimed"] is False and out["exit_capacity_verified"] is False
    assert out["research_only"] is True and "exit_open" not in out
    assert "net_return_after_cost" not in out  # Caller deducts its costs once.


def test_prior_completed_seal_then_break_exits_before_1000():
    m = minute_day()
    bar(m, "09:40", open=10, low=10, high=11, close=11)
    bar(m, "09:41", open=11, low=10.8, high=11, close=10.8)
    bar(m, "09:42", open=10.7, low=10.7, high=10.7, close=10.7)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS
    assert out["decision_time"].endswith("09:41:00+08:00")
    assert out["actual_exit_time"].endswith("09:41:00+08:00")
    assert out["exit_price"] == 10.7 and out["exit_reason"] == "LIMIT_UP_BREAK_NEXT_BAR_OPEN"


def test_intrabar_first_touch_alone_cannot_invent_ordered_seal_then_break():
    m = minute_day()
    bar(m, "09:40", open=10, low=10, high=11, close=10.5)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["decision_time"].endswith("10:00:00+08:00")


def test_prior_seal_break_and_reseal_does_not_cancel_the_pending_sell():
    m = minute_day()
    bar(m, "09:40", open=10, low=10, high=11, close=11)
    bar(m, "09:41", open=11, low=10.8, high=11, close=11)
    bar(m, "09:42", open=11, low=11, high=11, close=11)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["exit_price"] == 11
    assert out["decision_time"].endswith("09:41:00+08:00")


def test_one_cent_break_is_not_mistaken_for_still_sealed():
    m = minute_day(price=11)
    tail(m, "09:42", 10.99)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["decision_time"].endswith("09:42:00+08:00")
    assert out["exit_price"] == 10.99


def test_1000_sealed_extends_into_afternoon_until_break_next_bar():
    m = minute_day(price=11)
    tail(m, "13:30", 10.8)
    bar(m, "13:31", open=10.7, high=10.9, low=10.7, close=10.8)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["actual_exit_time"].endswith("13:30:00+08:00")
    assert out["exit_price"] == 10.7


def test_sealed_all_day_remains_pending_not_zero_return():
    (out, status), _ = replay({DAY: records(minute_day(price=11))})
    assert out is None and status == "PENDING_EXIT_LIMIT_UP_HELD"


def test_each_new_day_resets_seal_using_that_days_limit():
    first = records(minute_day(price=11))
    second = records(minute_day("20260915", price=11.5), pre_close=11, up=12.1, down=9.9)
    (out, status), _ = replay({DAY: first, "20260915": second}, as_of="20260915")
    assert status == SETTLED_STATUS and out["actual_exit_date"] == "20260915"
    assert out["decision_time"] == "2026-09-15T10:00:00+08:00"
    assert out["held_limit_up_sessions"] == 1 and out["delayed_trading_days"] == 1
    assert out["gross_return"] == pytest.approx(0.15)


def test_pending_sell_survives_overnight_and_next_day_resealing():
    m = minute_day()
    tail(m, "10:01", 9)
    second = records(minute_day("20260915", price=9.9), pre_close=9, up=9.9, down=8.1)
    (out, status), _ = replay({DAY: records(m), "20260915": second}, as_of="20260915")
    assert status == SETTLED_STATUS and out["decision_time"] == "2026-09-14T10:00:00+08:00"
    assert out["actual_exit_time"] == "2026-09-15T09:30:00+08:00"
    assert out["exit_price"] == 9.9 and out["blocked_exit_sessions"] == 1


def test_execution_bar_open_locked_down_cannot_use_later_intrabar_unlock():
    m = minute_day()
    bar(m, "10:01", open=9, low=9, high=10.5, close=10.5)
    bar(m, "10:02", open=10.4, low=10.4, high=10.4, close=10.4)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["exit_price"] == 10.4
    assert out["actual_exit_time"] == "2026-09-14T10:01:00+08:00"


def test_zero_volume_execution_bar_waits_for_next_positive_volume():
    m = minute_day()
    bar(m, "10:01", vol=0)
    bar(m, "10:02", open=10.3, high=10.3, low=10.3, close=10.3)
    (out, status), _ = replay({DAY: records(m)})
    assert status == SETTLED_STATUS and out["exit_price"] == 10.3
    assert out["actual_exit_time"].endswith("10:01:00+08:00")


def test_no_next_bar_after_close_keeps_order_pending_until_following_session():
    m = minute_day(price=11)
    bar(m, "15:00", open=11, high=11, low=10.9, close=10.9)
    (out, status), _ = replay({DAY: records(m)})
    assert out is None and status == "PENDING_EXIT_UNSELLABLE"
    next_day = records(minute_day("20260915", 11), pre_close=10.9, up=11.99, down=9.81)
    (out, status), _ = replay({DAY: records(m), "20260915": next_day}, as_of="20260915")
    assert status == SETTLED_STATUS and out["actual_exit_time"].endswith("09:30:00+08:00")
    assert out["decision_time"].endswith("15:00:00+08:00")
    assert out["held_limit_up_sessions"] == out["delayed_trading_days"] == 1
    assert out["blocked_exit_sessions"] == out["suspended_exit_sessions"] == 0


def test_sealed_at_1000_then_unsellable_break_counts_one_blocked_session_only():
    m = minute_day(price=11)
    tail(m, "14:59", 9)
    second = records(minute_day("20260915", price=9.5), pre_close=9, up=9.9, down=8.1)
    (out, status), _ = replay({DAY: records(m), "20260915": second}, as_of="20260915")
    assert status == SETTLED_STATUS and out["delayed_trading_days"] == 1
    assert out["blocked_exit_sessions"] == 1
    assert out["held_limit_up_sessions"] == out["suspended_exit_sessions"] == 0


def test_suspended_zero_volume_day_requires_no_minutes_then_continues():
    first = records(minute_day())
    first["daily"]["vol"] = 0
    first["minutes"] = None
    second = records(minute_day("20260915"))
    (out, status), calls = replay({DAY: first, "20260915": second}, as_of="20260915")
    assert status == SETTLED_STATUS and out["suspended_exit_sessions"] == 1
    assert ("minutes", DAY) not in calls and out["delayed_trading_days"] == 1


def test_all_suspended_or_locked_days_are_pending_not_false_settlement():
    first = records(minute_day())
    first["daily"]["vol"] = 0
    (out, status), _ = replay({DAY: first})
    assert out is None and status == "PENDING_EXIT_SUSPENDED"
    m = minute_day(price=9)
    (out, status), _ = replay({DAY: records(m)})
    assert out is None and status == "PENDING_EXIT_UNSELLABLE"


@pytest.mark.parametrize("kind,status", [("daily", "PENDING_EXIT_MISSING_DAILY"), ("limits", "PENDING_EXIT_MISSING_LIMITS"), ("minutes", "PENDING_EXIT_MISSING_MINUTES")])
def test_missing_first_day_cannot_be_skipped_to_a_complete_later_day(kind, status):
    first = records(minute_day())
    first[kind] = None
    (out, actual), calls = replay({DAY: first, "20260915": records(minute_day("20260915"))}, as_of="20260915")
    assert out is None and actual == status
    assert all(day == DAY for _, day in calls)


def test_before_t1_never_reads_market_and_cannot_sell_on_t():
    (out, status), calls = replay({}, as_of="20260911")
    assert out is None and status == "PENDING_T1" and calls == []


def test_asof_cutoff_never_reads_future_sessions_and_inputs_are_unchanged():
    sessions = {DAY: records(minute_day(price=11)), "20260915": records(minute_day("20260915"))}
    before = copy.deepcopy(sessions)
    (out, status), calls = replay(sessions)
    assert out is None and status == "PENDING_EXIT_LIMIT_UP_HELD"
    assert all(day == DAY for _, day in calls) and sessions == before


@pytest.mark.parametrize("mutation,status", [
    ("missing_bar", "PENDING_EXIT_INCOMPLETE_MINUTES"),
    ("duplicate_bar", "PENDING_EXIT_MINUTE_TIME_CONFLICT"),
    ("start_timestamp", "PENDING_EXIT_MINUTE_CONTRACT_INVALID"),
    ("wrong_date", "PENDING_EXIT_MINUTE_CONTRACT_INVALID"),
    ("wrong_code", "PENDING_EXIT_MINUTE_CONTRACT_INVALID"),
    ("wrong_row_date", "PENDING_EXIT_MINUTE_IDENTITY_CONFLICT"),
    ("unordered", "PENDING_EXIT_MINUTE_TIME_CONFLICT"),
    ("lunch_bar", "PENDING_EXIT_MINUTE_TIME_CONFLICT"),
    ("false_complete", "PENDING_EXIT_MINUTE_CONTRACT_INVALID"),
    ("no_provenance", "PENDING_EXIT_MINUTE_PROVENANCE_MISSING"),
    ("bad_sha", "PENDING_EXIT_MINUTE_PROVENANCE_INVALID"),
    ("unsafe_path", "PENDING_EXIT_MINUTE_PROVENANCE_INVALID"),
    ("daily_close_conflict", "PENDING_EXIT_DAILY_MINUTE_CONFLICT"),
])
def test_minute_contract_must_be_complete_dated_and_provenance_bound(mutation, status):
    m = minute_day()
    session = records(m)
    if mutation == "missing_bar": m["rows"].pop(20)
    elif mutation == "duplicate_bar": m["rows"][20] = m["rows"][19].copy()
    elif mutation == "start_timestamp": m["timestamp_semantics"] = "BAR_START"
    elif mutation == "wrong_date": m["trade_date"] = "20260915"
    elif mutation == "wrong_code": m["ts_code"] = "600001.SH"
    elif mutation == "wrong_row_date": m["rows"][0]["trade_date"] = "20260915"
    elif mutation == "unordered": m["rows"][0], m["rows"][1] = m["rows"][1], m["rows"][0]
    elif mutation == "lunch_bar": m["rows"][120]["bar_end"] = "2026-09-14 12:00:00"
    elif mutation == "false_complete": m["complete_session"] = False
    elif mutation == "no_provenance": del m["source_files"]
    elif mutation == "bad_sha": m["source_files"][0]["sha256"] = "not-a-sha"
    elif mutation == "unsafe_path": m["source_files"][0]["path"] = "../data.csv"
    elif mutation == "daily_close_conflict": session["daily"]["close"] = 10.01; session["daily"]["high"] = 10.01
    (out, actual), _ = replay({DAY: session})
    assert out is None and actual == status


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0, None, True, 1e308])
def test_invalid_minute_prices_are_not_tradable(bad):
    session = records(minute_day())
    session["minutes"]["rows"][30]["open"] = bad
    (out, status), _ = replay({DAY: session})
    assert out is None and status == "PENDING_EXIT_INVALID_MINUTE_PRICE"


@pytest.mark.parametrize("bad", [float("nan"), -1, None, True, 1e308])
def test_invalid_volume_is_not_assumed_tradable(bad):
    session = records(minute_day())
    session["minutes"]["rows"][30]["vol"] = bad
    (out, status), _ = replay({DAY: session})
    assert out is None and status == "PENDING_EXIT_INVALID_MINUTE_PRICE"


def test_zero_volume_cannot_move_price_or_be_used_as_seal_evidence():
    session = records(minute_day())
    row = session["minutes"]["rows"][29]
    row.update(open=11, high=11, low=11, close=11, vol=0)
    session["daily"]["high"] = 11
    (out, status), _ = replay({DAY: session})
    assert out is None and status == "PENDING_EXIT_ZERO_VOLUME_PRICE_CONFLICT"


def test_official_preclose_wealth_chain_does_not_invent_split_loss():
    session = records(minute_day(price=5), pre_close=5, up=5.5, down=4.5)
    (out, status), _ = replay({DAY: session}, entry=10, t_close=10)
    assert status == SETTLED_STATUS and out["gross_return"] == pytest.approx(0)
    assert out["price_basis"] == "OFFICIAL_DAILY_CLOSE_PRE_CLOSE_WEALTH_CHAIN"
    assert out["wealth_chain"][0]["previous_close"] == 10
    assert out["wealth_chain"][0]["pre_close"] == 5


@pytest.mark.parametrize("mutation", ["missing_limit_preclose", "conflicting_limit_preclose"])
def test_unexplained_price_basis_jump_blocks_return(mutation):
    session = records(minute_day(price=5), pre_close=5, up=5.5, down=4.5)
    if mutation == "missing_limit_preclose": del session["limits"]["pre_close"]
    else: session["limits"]["pre_close"] = 10
    (out, status), _ = replay({DAY: session})
    assert out is None and status == "PENDING_EXIT_CORPORATE_ACTION_UNRESOLVED"


def test_valid_no_adjustment_can_use_limits_without_preclose_field():
    session = records(minute_day())
    del session["limits"]["pre_close"]
    (out, status), _ = replay({DAY: session})
    assert status == SETTLED_STATUS and out["gross_return"] == 0


def test_known_invalid_loader_is_pending_but_software_errors_are_not_swallowed():
    session = records(minute_day())
    def bad(day): raise ValueError("SHA mismatch")
    args = (DATES, DAY, DAY, CODE, 10, 10, lambda day: session["daily"], lambda day: session["limits"])
    assert resolve_exit_1000(*args, bad) == (None, "PENDING_EXIT_SOURCE_INVALID")
    def bug(day): raise RuntimeError("implementation bug")
    with pytest.raises(RuntimeError, match="implementation bug"):
        resolve_exit_1000(*args, bug)


@pytest.mark.parametrize("dates,scheduled", [(list(reversed(DATES)), DAY), (DATES + [DATES[-1]], DAY), (DATES, "20260913"), ([], DAY)])
def test_invalid_calendar_cannot_enable_sell(dates, scheduled):
    with pytest.raises(ValueError):
        resolve_exit_1000(dates, scheduled, DAY, CODE, 10, 10, lambda d: None, lambda d: None, lambda d: None)


def test_unknown_calendar_future_is_not_reported_as_sealed_or_settled():
    result = resolve_exit_1000(DATES, DAY, "20260918", CODE, 10, 10,
                              lambda d: pytest.fail("must not load"), lambda d: None, lambda d: None)
    assert result == (None, "PENDING_EXIT_CALENDAR_COVERAGE")
