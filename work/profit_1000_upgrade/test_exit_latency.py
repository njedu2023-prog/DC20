"""Synthetic causal latency fixtures, not observed trades or provider proof."""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta

import pytest

from work.profit_1000_upgrade import exit_latency as latency

CODE = "600000.SH"
DAY, NEXT, LAST = "20260914", "20260915", "20260916"
DATES = ["20260911", DAY, NEXT, LAST]


def minutes(day=DAY, price=10):
    rows = []
    for start in ("09:31", "13:01"):
        first = datetime.strptime(day + " " + start, "%Y%m%d %H:%M")
        for offset in range(120):
            rows.append({"bar_end": (first + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S"),
                         "open": price, "high": price, "low": price, "close": price, "vol": 100})
    return {"schema_version": "dc20_exit_1000_minutes_v1", "ts_code": CODE,
            "trade_date": day, "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END",
            "interval_seconds": 60, "complete_session": True, "rows": rows,
            "source_files": [{"path": f"research/{day}/minutes.json", "sha256": "a" * 64}]}


def bar(payload, hhmm, price=None, **values):
    row = next(row for row in payload["rows"] if row["bar_end"][11:16] == hhmm)
    if price is not None:
        row.update(open=price, high=price, low=price, close=price)
    row.update(values)
    return row


def tail(payload, hhmm, price):
    for row in payload["rows"]:
        if row["bar_end"][11:16] >= hhmm:
            row.update(open=price, high=price, low=price, close=price)


def records(payload, *, pre=10, up=11, down=9):
    rows = payload["rows"]
    return {"daily": {"ts_code": CODE, "trade_date": payload["trade_date"],
                      "open": rows[0]["open"], "high": max(row["high"] for row in rows),
                      "low": min(row["low"] for row in rows), "close": rows[-1]["close"],
                      "pre_close": pre, "vol": sum(row["vol"] for row in rows)},
            "limits": {"ts_code": CODE, "trade_date": payload["trade_date"],
                       "pre_close": pre, "up_limit": up, "down_limit": down}, "minutes": payload}


def replay(sessions=None, *, asof=DAY, entry=10, t_close=10, dates=DATES, scheduled=DAY):
    sessions = sessions if sessions is not None else {DAY: records(minutes())}
    callbacks = {kind: lambda day, kind=kind: sessions.get(day, {}).get(kind)
                 for kind in ("daily", "limits", "minutes")}
    return latency.compare_exit_latency(dates, scheduled, asof, CODE, entry, t_close,
                                        callbacks["daily"], callbacks["limits"], callbacks["minutes"])


def results(pair):
    assert pair["status"] == "COMPLETE_PAIR"
    return pair["baseline"]["result"], pair["delayed"]["result"]


@pytest.mark.parametrize("first,second,flip", [(10.2, 9.8, True), (9.8, 10.2, True),
                                              (10.2, 10.2, False), (9.8, 9.7, False),
                                              (10.05, 10.10, False)])
def test_fixed_decision_price_changes_and_single_fee(first, second, flip):
    m = minutes()
    bar(m, "10:01", first)
    bar(m, "10:02", second)
    pair = replay({DAY: records(m)})
    baseline, delayed = results(pair)
    assert baseline["decision_time"] == delayed["decision_time"] == "2026-09-14T10:00:00+08:00"
    assert baseline["actual_exit_time"] == "2026-09-14T10:00:00+08:00"
    assert delayed["actual_exit_time"] == delayed["earliest_execution_time"] == "2026-09-14T10:01:00+08:00"
    assert delayed["execution_bar_end"] == "2026-09-14T10:02:00+08:00"
    assert baseline["exit_price"] == first and delayed["exit_price"] == second
    for key, price in (("baseline", first), ("delayed", second)):
        assert pair[key]["net_return_45bp"] == pytest.approx(price / 10 - 1 - .0045)
        assert pair[key]["net_return_90bp"] == pytest.approx(price / 10 - 1 - .009)
    assert pair["delta_net_return_45bp"] == pytest.approx((second - first) / 10)
    assert pair["sign_flip_45bp"] is flip


def test_baseline_dictionary_is_unmodified_pinned_kernel_result():
    session = records(minutes())
    pair = replay({DAY: session})
    expected, status = latency._kernel().resolve_exit_1000(
        DATES, DAY, DAY, CODE, 10, 10, lambda day: session["daily"],
        lambda day: session["limits"], lambda day: session["minutes"])
    assert pair["baseline"]["result"] == expected
    assert pair["baseline"]["status"] == status
    assert "timestamp_confirmed" not in expected


def test_research_envelope_never_claims_bar_start_or_activation():
    pair = replay()
    for obj in (pair, pair["baseline"], pair["delayed"], pair["delayed"]["result"]):
        assert obj["research_only"] is True
        assert obj["timestamp_confirmed"] is obj["production_activation_allowed"] is False
        assert obj["actual_execution_claimed"] is False
    assert pair["bar_start_interpretation_tested"] is pair["nav_computed"] is False
    assert pair["timestamp_semantics"] == "BAR_END_CONDITIONAL_ASSUMPTION"
    assert pair["baseline_kernel_sha256"] == latency.KERNEL_SHA256
    assert len(pair["source_snapshots"]) == 3
    assert pair["source_snapshots_verified_unchanged"] is True
    assert all(len(item["payload_sha256"]) == 64 for item in pair["source_snapshots"])


@pytest.mark.parametrize("kind", ["zero_volume", "down_limit", "two_down_bars"])
def test_existing_liquidity_delay_not_added_again(kind):
    m = minutes()
    if kind == "zero_volume":
        bar(m, "10:01", vol=0)
    else:
        bar(m, "10:01", 9)
    if kind == "two_down_bars":
        bar(m, "10:02", 9)
        bar(m, "10:03", 10.3)
    else:
        bar(m, "10:02", 10.2)
    baseline, delayed = results(replay({DAY: records(m)}))
    assert baseline["exit_price"] == delayed["exit_price"]
    assert baseline["actual_exit_time"] == delayed["actual_exit_time"]


@pytest.mark.parametrize("block", ["zero_volume", "down_limit"])
def test_stress_blocked_after_baseline_waits_for_next_price(block):
    m = minutes()
    bar(m, "10:01", 10.2)
    if block == "zero_volume":
        bar(m, "10:02", 10.2, vol=0)
    else:
        bar(m, "10:02", 9)
    bar(m, "10:03", 9.8)
    baseline, delayed = results(replay({DAY: records(m)}))
    assert baseline["exit_price"] == 10.2 and delayed["exit_price"] == 9.8
    assert delayed["actual_exit_time"].endswith("10:02:00+08:00")


@pytest.mark.parametrize("decision", ["09:41", "10:05", "13:30"])
def test_original_limit_break_kept_after_reseal(decision):
    m = minutes(price=11)
    bar(m, decision, open=11, high=11, low=10.8, close=11)
    baseline, delayed = results(replay({DAY: records(m)}))
    assert baseline["decision_time"] == delayed["decision_time"]
    assert baseline["decision_time"].endswith(decision + ":00+08:00")
    assert baseline["exit_reason"] == delayed["exit_reason"] == "LIMIT_UP_BREAK_NEXT_BAR_OPEN"
    assert baseline["exit_price"] == delayed["exit_price"] == 11
    assert datetime.fromisoformat(delayed["actual_exit_time"]) - datetime.fromisoformat(baseline["actual_exit_time"]) == timedelta(seconds=60)


def test_lunch_gap_already_exceeds_latency_so_same_fill():
    m = minutes(price=11)
    bar(m, "11:30", open=11, high=11, low=10.9, close=10.9)
    baseline, delayed = results(replay({DAY: records(m)}))
    assert baseline["decision_time"] == delayed["decision_time"] == "2026-09-14T11:30:00+08:00"
    assert baseline["actual_exit_time"] == delayed["actual_exit_time"] == "2026-09-14T13:00:00+08:00"


def test_closing_break_overnight_not_skip_next_open():
    m = minutes(price=11)
    bar(m, "15:00", open=11, high=11, low=10.9, close=10.9)
    n = records(minutes(NEXT, 11), pre=10.9, up=11.99, down=9.81)
    baseline, delayed = results(replay({DAY: records(m), NEXT: n}, asof=NEXT))
    assert baseline["decision_time"] == delayed["decision_time"] == "2026-09-14T15:00:00+08:00"
    assert baseline["actual_exit_time"] == delayed["actual_exit_time"] == "2026-09-15T09:30:00+08:00"
    assert baseline["gross_return"] == pytest.approx(delayed["gross_return"])
    assert delayed["held_limit_up_sessions"] == 1


def test_1459_decision_baseline_same_day_stress_next_day():
    m = minutes(price=11)
    bar(m, "14:59", open=11, high=11, low=10.8, close=10.8)
    bar(m, "15:00", 10.7)
    n = records(minutes(NEXT, 10.6), pre=10.7, up=11.77, down=9.63)
    pair = replay({DAY: records(m), NEXT: n}, asof=NEXT)
    baseline, delayed = results(pair)
    assert baseline["actual_exit_time"] == "2026-09-14T14:59:00+08:00"
    assert delayed["actual_exit_time"] == "2026-09-15T09:30:00+08:00"
    assert delayed["gross_return"] == pytest.approx(.06)
    assert len(delayed["wealth_chain"]) == 2
    assert len(delayed["minute_source_files"]) == 2


def cross_day(*, adjusted=False):
    m = minutes()
    bar(m, "10:01", 10.2)
    tail(m, "10:02", 9)
    if adjusted:
        n = records(minutes(NEXT, 4.8), pre=4.5, up=4.95, down=4.05)
    else:
        n = records(minutes(NEXT, 9.8), pre=9, up=9.9, down=8.1)
    return {DAY: records(m), NEXT: n}


@pytest.mark.parametrize("adjusted", [False, True])
def test_stress_crossday_wealth_chain_counts_close_once(adjusted):
    baseline, delayed = results(replay(cross_day(adjusted=adjusted), asof=NEXT))
    assert baseline["gross_return"] == pytest.approx(.02)
    assert delayed["gross_return"] == pytest.approx(-.04 if adjusted else -.02)
    assert delayed["wealth_chain"][0]["status"] == "PENDING_SELL"
    assert delayed["wealth_chain"][1]["previous_close"] == 9
    assert delayed["blocked_exit_sessions"] == 1


@pytest.mark.parametrize("missing", ["daily", "limits", "minutes", "adjustment_limit_pre"])
def test_missing_stress_only_source_never_erases_baseline_or_becomes_zero(missing):
    sessions = cross_day(adjusted=True)
    if missing == "adjustment_limit_pre":
        sessions[NEXT]["limits"].pop("pre_close")
    else:
        sessions[NEXT].pop(missing)
    pair = replay(sessions, asof=NEXT)
    assert pair["status"] == "PENDING_PAIR"
    assert pair["baseline"]["result"] is not None
    assert pair["delayed"]["result"] is None
    assert pair["delayed"]["net_return_45bp"] is pair["delta_net_return_45bp"] is None
    assert pair["sign_flip_45bp"] is None
    if missing == "adjustment_limit_pre":
        assert pair["delayed"]["status"] == "PENDING_EXIT_CORPORATE_ACTION_UNRESOLVED"


@pytest.mark.parametrize("kind", ["daily", "limits", "minutes", "incomplete", "time_conflict", "sealed", "down_limit"])
def test_pending_baseline_remains_two_pending_arms(kind):
    m = minutes(price=11 if kind == "sealed" else 10)
    if kind == "down_limit":
        tail(m, "10:01", 9)
    session = records(m)
    if kind in {"daily", "limits", "minutes"}:
        session.pop(kind)
    elif kind == "incomplete":
        m["rows"].pop()
    elif kind == "time_conflict":
        m["rows"][30]["bar_end"] = m["rows"][29]["bar_end"]
    pair = replay({DAY: session})
    assert pair["status"] == "PENDING_PAIR"
    assert pair["baseline"] == pair["delayed"]
    assert pair["baseline"]["result"] is pair["baseline"]["net_return_45bp"] is None
    assert pair["delta_net_return_90bp"] is pair["sign_flip_90bp"] is None


def test_no_prices_past_asof_are_consumed():
    pair = replay(cross_day(), asof=DAY)
    assert pair["delayed"]["result"] is None
    assert all(row["trade_date"] == DAY for row in pair["source_snapshots"])


@pytest.mark.parametrize("kwargs", [dict(dates=list(reversed(DATES))), dict(dates=DATES + [LAST]),
                                  dict(scheduled="20260913"), dict(entry=0), dict(entry=True),
                                  dict(entry=float("nan")), dict(t_close=-1)])
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        replay(**kwargs)


@pytest.mark.parametrize("kwargs,status", [(dict(asof="20260911"), "PENDING_T1"),
                                         (dict(asof="20260917"), "PENDING_EXIT_CALENDAR_COVERAGE")])
def test_calendar_cutoff_pending_not_guessed(kwargs, status):
    pair = replay(**kwargs)
    assert pair["baseline"]["status"] == pair["delayed"]["status"] == status
    assert pair["source_snapshots"] == []


def test_pinned_kernel_mismatch_refuses_before_callbacks(monkeypatch, tmp_path):
    other = tmp_path / "not_the_kernel.py"
    other.write_bytes(b"raise AssertionError('must not run')")
    monkeypatch.setattr(latency, "KERNEL_PATH", other)
    with pytest.raises(ValueError, match="kernel SHA256 mismatch"):
        replay()


@pytest.mark.parametrize("kind", ["daily", "limits", "minutes"])
def test_changed_callback_payload_fails_closed(kind):
    session = records(minutes())
    calls = 0
    def changing(day):
        nonlocal calls
        calls += 1
        result = copy.deepcopy(session[kind])
        if calls >= 2:
            result["untrusted_revision"] = 2
        return result
    callbacks = {key: (changing if key == kind else lambda day, key=key: session[key])
                 for key in session}
    with pytest.raises(latency.LatencySourceChanged):
        latency.compare_exit_latency(DATES, DAY, DAY, CODE, 10, 10,
                                     callbacks["daily"], callbacks["limits"], callbacks["minutes"])


def test_mutable_return_objects_cannot_change_cached_baseline():
    session = records(minutes())
    pristine = copy.deepcopy(session)
    pair = replay({DAY: session})
    assert session == pristine
    session["minutes"]["rows"][30]["open"] = 123
    assert pair["baseline"]["result"]["exit_price"] == 10


def test_software_errors_are_not_swallowed_as_market_pending():
    def broken(day):
        raise RuntimeError("software failure")
    with pytest.raises(RuntimeError, match="software failure"):
        latency.compare_exit_latency(DATES, DAY, DAY, CODE, 10, 10, broken, broken, broken)


def test_source_path_hash_conflict_across_days_blocks_stress():
    sessions = cross_day()
    sessions[NEXT]["minutes"]["source_files"] = [
        {"path": sessions[DAY]["minutes"]["source_files"][0]["path"], "sha256": "b" * 64}]
    pair = replay(sessions, asof=NEXT)
    assert pair["baseline"]["result"] is not None
    assert pair["delayed"]["status"] == "PENDING_EXIT_SOURCE_CHANGED"
    assert pair["delayed"]["net_return_45bp"] is None


def test_retained_source_binding_hashes_match_canonical_snapshots():
    session = records(minutes())
    pair = replay({DAY: session})
    for item in pair["source_snapshots"]:
        assert item["payload_sha256"] == hashlib.sha256(latency._payload_bytes(session[item["kind"]])).hexdigest()
    assert pair["baseline"]["result"]["minute_source_files"] == pair["delayed"]["result"]["minute_source_files"]


def test_stress_zero_volume_full_day_preserves_order_and_wealth():
    sessions = cross_day()
    zero = records(minutes(NEXT, 9), pre=9, up=9.9, down=8.1)
    zero["daily"]["vol"] = 0
    zero.pop("minutes")
    sessions[NEXT] = zero
    sessions[LAST] = records(minutes(LAST, 9.8), pre=9, up=9.9, down=8.1)
    baseline, delayed = results(replay(sessions, asof=LAST))
    assert baseline["actual_exit_date"] == DAY and delayed["actual_exit_date"] == LAST
    assert delayed["gross_return"] == pytest.approx(-.02)
    assert delayed["suspended_exit_sessions"] == delayed["blocked_exit_sessions"] == 1
    assert delayed["wealth_chain"][1]["status"] == "ZERO_VOLUME_NO_TRADE"
    assert len(delayed["minute_source_files"]) == 2


def test_invalid_stress_full_session_blocks_even_if_execution_bar_present():
    sessions = cross_day()
    sessions[NEXT]["minutes"]["rows"].pop()
    pair = replay(sessions, asof=NEXT)
    assert pair["baseline"]["result"] is not None
    assert pair["delayed"]["result"] is None
    assert pair["delayed"]["status"] == "PENDING_EXIT_INCOMPLETE_MINUTES"


def test_t_close_entry_wealth_basis_not_reset_to_exit_day_open():
    m = minutes(price=10.5)
    bar(m, "10:01", 10.8)
    bar(m, "10:02", 10.6)
    baseline, delayed = results(replay({DAY: records(m, pre=10.7, up=11.77, down=9.63)}, t_close=10.7))
    assert baseline["gross_return"] == pytest.approx(.08)
    assert delayed["gross_return"] == pytest.approx(.06)


def test_lunch_preceding_bar_delay_can_cross_gap_without_extra_skip():
    m = minutes(price=11)
    bar(m, "11:29", open=11, high=11, low=10.9, close=10.9)
    baseline, delayed = results(replay({DAY: records(m)}))
    assert baseline["actual_exit_time"] == "2026-09-14T11:29:00+08:00"
    assert delayed["actual_exit_time"] == "2026-09-14T13:00:00+08:00"
