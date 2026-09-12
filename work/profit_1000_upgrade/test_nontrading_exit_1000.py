"""No network. Old-kernel regressions plus independently composed proof fixtures."""
import copy
import importlib.util
from pathlib import Path

import pytest

from top10decision.decision import shadow_exit_1000 as original
from work.profit_1000_upgrade import nontrading_exit_1000 as adapter
from work.profit_1000_upgrade import nontrading_session_truth as truth
from work.profit_1000_upgrade.test_nontrading_session_truth import composition, args

_spec = importlib.util.spec_from_file_location("_frozen_kernel_regressions", truth.CHECKOUT / "tests/test_shadow_exit_1000.py")
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
CODE = "603226.SH"
DATES = ["20250609", "20250610", "20250611", "20250612", "20250613", "20250616"]


@pytest.fixture(autouse=True)
def legacy_uses_no_proof_adapter(monkeypatch):
    # The frozen test functions retain their original module globals. Redirect
    # just the public resolver to the new path with a lookup returning None.
    monkeypatch.setattr(legacy, "resolve_exit_1000", lambda *a, **k:
        adapter.resolve_exit_1000_with_nontrading_sessions(*a, **k, load_nontrading_session=lambda _: None))


# Re-run every frozen pure-kernel regression against the mechanical new loop.
for _name, _function in list(vars(legacy).items()):
    if _name.startswith("test_") and callable(_function):
        globals()["test_old_parity__" + _name[5:]] = _function


def session(day, price=10, *, pre_close=10, up=11, down=9):
    payload = legacy.minute_day(day, price)
    payload["ts_code"] = CODE
    payload["source_files"][0]["path"] = f"research/minute/{day}/{CODE}.json"
    value = legacy.records(payload, pre_close=pre_close, up=up, down=down)
    value["daily"]["ts_code"] = value["limits"]["ts_code"] = CODE
    return value


def adjust(value):
    rows = value["minutes"]["rows"]
    value["daily"].update(open=rows[0]["open"], high=max(r["high"] for r in rows),
        low=min(r["low"] for r in rows), close=rows[-1]["close"], vol=sum(r["vol"] for r in rows))
    return value


def proofs(fixture):
    return {p.trade_date: p for p in truth.verify_nontrading_sessions(**args(fixture)) if p.ts_code == CODE}


def replay(sessions, lookup, *, scheduled="20250610", as_of="20250611", entry=10, close=10, minutes_hook=None):
    calls = []
    def load(kind):
        def callback(day):
            calls.append((kind, day))
            if kind == "minutes" and minutes_hook:
                minutes_hook(day)
            return sessions.get(day, {}).get(kind)
        return callback
    def proof(day):
        calls.append(("proof", day))
        return lookup(day) if callable(lookup) else lookup.get(day)
    result = adapter.resolve_exit_1000_with_nontrading_sessions(DATES, scheduled, as_of, CODE,
        entry, close, load("daily"), load("limits"), load("minutes"), load_nontrading_session=proof)
    return result, calls


def test_one_verified_day_advances_without_fabricated_wealth_link(composition):
    proof = proofs(composition)
    (out, status), calls = replay({"20250611": session("20250611", 10.5)}, proof)
    assert status == original.SETTLED_STATUS and out["gross_return"] == pytest.approx(.05)
    assert out["actual_exit_time"] == "2025-06-11T10:00:00+08:00"
    assert out["decision_time"] == out["actual_exit_time"]
    assert out["verified_nontrading_sessions"] == 1 and out["delayed_trading_days"] == 1
    assert out["suspended_exit_sessions"] == out["held_limit_up_sessions"] == out["blocked_exit_sessions"] == 0
    assert [r["trade_date"] for r in out["wealth_chain"]] == ["20250611"]
    assert ("limits", "20250610") not in calls and ("minutes", "20250610") not in calls
    assert "net_return" not in out and "net_return_after_cost" not in out
    assert out["production_activation_allowed"] is out["provider_timestamp_semantics_confirmed"] is False


def test_three_verified_days_preserve_original_calendar_and_full_evidence(composition):
    (out, status), _ = replay({"20250613": session("20250613")}, proofs(composition), as_of="20250613")
    assert status == original.SETTLED_STATUS and out["delayed_trading_days"] == out["verified_nontrading_sessions"] == 3
    assert [p["trade_date"] for p in out["nontrading_session_evidence"]] == DATES[1:4]
    assert [r["trade_date"] for r in out["wealth_chain"]] == ["20250613"]
    assert out["exit_policy_id"] == original.EXIT_POLICY_ID


def test_asof_ends_during_verified_halt_is_pending_never_zero(composition):
    (out, status), calls = replay({}, proofs(composition), as_of="20250612")
    assert out is None and status == "PENDING_EXIT_SUSPENDED"
    assert all(day <= "20250612" for _, day in calls)


def test_unproven_first_gap_stops_before_later_true_prices(composition):
    proof = proofs(composition); proof.pop("20250610")
    (out, status), calls = replay({"20250611": session("20250611")}, proof)
    assert out is None and status == "PENDING_EXIT_MISSING_DAILY"
    assert calls == [("daily", "20250610"), ("proof", "20250610")]


def test_later_missing_evidence_not_cleared_by_earlier_valid_halt(composition):
    proof = proofs(composition); proof.pop("20250611")
    (out, status), calls = replay({"20250612": session("20250612")}, proof, as_of="20250612")
    assert out is None and status == "PENDING_EXIT_MISSING_DAILY"
    assert not any(day == "20250612" for _, day in calls)


def test_pending_sell_survives_halt_and_next_day_reseal(composition):
    first = session("20250609")
    legacy.tail(first["minutes"], "10:01", 9); adjust(first)
    second = session("20250611", 9.9, pre_close=9, up=9.9, down=8.1)
    (out, status), _ = replay({"20250609": first, "20250611": second}, proofs(composition), scheduled="20250609")
    assert status == original.SETTLED_STATUS
    assert out["decision_time"] == "2025-06-09T10:00:00+08:00"
    assert out["actual_exit_time"] == "2025-06-11T09:30:00+08:00"
    assert out["blocked_exit_sessions"] == out["verified_nontrading_sessions"] == 1
    assert out["delayed_trading_days"] == 2 and out["gross_return"] == pytest.approx(-.01)
    assert [r["trade_date"] for r in out["wealth_chain"]] == ["20250609", "20250611"]


def test_1500_break_pending_decision_survives_halt(composition):
    first = session("20250609", 11)
    legacy.bar(first["minutes"], "15:00", open=11, high=11, low=10.9, close=10.9); adjust(first)
    second = session("20250611", 11, pre_close=10.9, up=11.99, down=9.81)
    (out, status), _ = replay({"20250609": first, "20250611": second}, proofs(composition), scheduled="20250609")
    assert status == original.SETTLED_STATUS and out["decision_time"] == "2025-06-09T15:00:00+08:00"
    assert out["actual_exit_time"] == "2025-06-11T09:30:00+08:00"
    assert out["held_limit_up_sessions"] == 1 and out["blocked_exit_sessions"] == 0
    assert out["gross_return"] == pytest.approx(.1)


def test_pending_priority_during_halt_preserved(composition):
    first = session("20250609", 9)
    (out, status), _ = replay({"20250609": first}, proofs(composition), scheduled="20250609", as_of="20250610")
    assert out is None and status == "PENDING_EXIT_UNSELLABLE"
    first = session("20250609", 11)
    (out, status), _ = replay({"20250609": first}, proofs(composition), scheduled="20250609", as_of="20250610")
    assert out is None and status == "PENDING_EXIT_LIMIT_UP_HELD"


def test_corporate_action_basis_after_halt_uses_original_crosscheck_once(composition):
    first = session("20250609", 9)
    second = session("20250611", 4.75, pre_close=4.5, up=4.95, down=4.05)
    proof = proofs(composition)
    (out, status), _ = replay({"20250609": first, "20250611": second}, proof, scheduled="20250609")
    assert status == original.SETTLED_STATUS and out["gross_return"] == pytest.approx(-.05)
    assert out["wealth_chain"][-1]["previous_close"] == 9
    assert out["wealth_chain"][-1]["pre_close"] == 4.5
    del second["limits"]["pre_close"]
    (out, status), _ = replay({"20250609": first, "20250611": second}, proof, scheduled="20250609")
    assert out is None and status == "PENDING_EXIT_CORPORATE_ACTION_UNRESOLVED"


@pytest.mark.parametrize("bad", [True, {}, object()])
def test_no_ducktyped_proof_or_boolean_admission(bad):
    (out, status), _ = replay({}, {"20250610": bad})
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_INVALID"


def test_object_new_forgery_is_not_in_issued_registry():
    forged = object.__new__(truth.VerifiedNoTradingSession)
    (out, status), _ = replay({}, {"20250610": forged})
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_INVALID"


def test_proof_wrong_date_or_code_rejected(composition):
    proof = proofs(composition)
    (out, status), _ = replay({}, {"20250610": proof["20250611"]})
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_IDENTITY_CONFLICT"
    other = next(p for p in truth.verify_nontrading_sessions(**args(composition)) if p.ts_code != CODE)
    (out, status), _ = replay({}, {"20250610": other})
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_IDENTITY_CONFLICT"


def test_proof_does_not_authorize_later_asof_or_forward_holdout(composition):
    proof = proofs(composition)["20250610"]
    out, status = adapter.resolve_exit_1000_with_nontrading_sessions(
        DATES + ["20260914"], "20250610", "20260914", CODE, 10, 10,
        lambda _: None, lambda _: None, lambda _: None, load_nontrading_session=lambda _: proof)
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_IDENTITY_CONFLICT"


@pytest.mark.parametrize("key,value", [("can_advance_holding_day", False), ("market_absence_verified", 1),
    ("production_activation_allowed", True), ("actual_capacity_verified", True),
    ("provider_timestamp_semantics_confirmed", True), ("source_policy_id", "old_unqualified_event")])
def test_even_reflectively_tampered_issued_proof_flags_are_rejected(composition, key, value):
    proof = proofs(composition)["20250610"]
    object.__setattr__(proof, key, value)  # Deliberate adversarial reflection, not normal mutation.
    (out, status), _ = replay({}, {"20250610": proof})
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_INVALID"


@pytest.mark.parametrize("bad", [{}, {"ts_code": CODE, "trade_date": "20250610", "open": None}, False])
def test_only_actual_None_not_invalid_or_empty_daily_can_consume_proof(composition, bad):
    proof = proofs(composition)
    (out, status), calls = replay({"20250610": {"daily": bad}}, proof)
    assert out is None and status.startswith("PENDING_EXIT_")
    assert ("proof", "20250610") not in calls


def test_zero_volume_daily_retains_old_path_and_counter(composition):
    first = session("20250610"); first["daily"]["vol"] = 0
    first["minutes"] = None
    (out, status), calls = replay({"20250610": first, "20250611": session("20250611")}, proofs(composition))
    assert status == original.SETTLED_STATUS and out["suspended_exit_sessions"] == 1
    assert "verified_nontrading_sessions" not in out
    assert ("proof", "20250610") not in calls


def test_source_changed_before_consumption_stays_pending(composition):
    proof = proofs(composition)
    composition["_sentinel"].write_bytes(b"changed")
    (out, status), _ = replay({}, proof)
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_INVALID"


def test_source_changed_during_exit_disallows_the_otherwise_settled_return(composition):
    proof = proofs(composition)
    def change(_): composition["_sentinel"].write_bytes(b"changed")
    (out, status), _ = replay({"20250611": session("20250611")}, proof, minutes_hook=change)
    assert out is None and status == "PENDING_EXIT_NONTRADING_EVIDENCE_CHANGED"


def test_no_proof_exact_output_parity_and_no_extra_fields():
    value = session("20250610", 10.25)
    callbacks = [lambda day, k=k: value[k] if day == "20250610" else None for k in ("daily", "limits", "minutes")]
    inputs = (DATES, "20250610", "20250610", CODE, 10, 10, *callbacks)
    expected = original.resolve_exit_1000(*inputs)
    assert adapter.resolve_exit_1000_with_nontrading_sessions(*inputs) == expected
    assert adapter.resolve_exit_1000_with_nontrading_sessions(*inputs, load_nontrading_session=lambda _: None) == expected


def test_pinned_kernel_change_rejected_before_any_market_callback(monkeypatch):
    monkeypatch.setattr(adapter, "KERNEL_SHA256", "0" * 64)
    def forbidden(_): raise AssertionError("source callback must not run")
    with pytest.raises(ValueError, match="DEPENDENCY_CHANGED"):
        adapter.resolve_exit_1000_with_nontrading_sessions(DATES, "20250610", "20250610", CODE, 10, 10,
            forbidden, forbidden, forbidden)
