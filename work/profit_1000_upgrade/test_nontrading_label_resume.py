"""Pure synthetic continuations; no fixture is a real source/membership audit."""
from collections.abc import Mapping
from copy import deepcopy

import pytest

from work.profit_1000_upgrade import nontrading_label_resume as resume
from work.profit_1000_upgrade import auction_truth_v3 as auction, auction_candidate_scope as candidate
from work.profit_1000_upgrade import candidate_labels_overlay as overlay, policy_v3 as policy
from work.profit_1000_upgrade import nontrading_session_truth as truth
from work.profit_1000_upgrade.test_nontrading_session_truth import composition, args
from work.profit_1000_upgrade.test_nontrading_exit_1000 import session, adjust, legacy

D, T, T1, CODE = "20250606", "20250609", "20250610", "603226.SH"
DATES = ["20250605", D, T, T1, "20250611", "20250612", "20250613", "20250616", "20260911"]


def seed(*, source="base", code=CODE, signal=D, execution=T, scheduled=T1, price=10, amount=None, opening=None):
    raw = {"ts_code": code, "trade_date": execution, "price": price, "vol": 2_000_000,
           "amount": amount, "pre_close": 10}
    opening = float(price) if opening is None else opening
    evidence = auction.qualify_row(raw, execution, code, opening)
    evidence.update(source_policy_id=policy.AUCTION_SOURCE_POLICY_ID,
        entry_price_source="TUSHARE_STK_AUCTION", loaded_source_qualification_claimed=True,
        daily_open=opening, daily_source_binding={"path": "unit/original_daily.csv", "sha256": "1" * 64},
        source_files=[{"path": "unit/original_auction.json", "sha256": "2" * 64}])
    row = {"signal_date": signal, "exec_date": execution, "scheduled_exit_date": scheduled, "ts_code": code,
        "promotion_rank": 1, "features": {"stage": 2}, "feature_as_of_date": signal,
        "label_policy_id": resume.adapter.EXIT_POLICY_ID, **dict(policy.CONTRACT),
        **auction.FLAGS, "shadow_max_price": None, "round_trip_cost_rate": .0045,
        "label_status": "PENDING_EXIT_MISSING_DAILY", "proxy_fill": 1, "entry_price": price,
        "entry_price_source": "TUSHARE_STK_AUCTION", "entry_price_evidence": evidence,
        "entry_qualification_status": evidence["status"], "entry_price_fallback_reason": None,
        "capacity_proxy_verified": evidence["capacity_proxy_verified"], "capacity_amount": evidence["capacity_amount"],
        "capacity_evidence": evidence["capacity_evidence"], "capacity_reason": evidence["capacity_reason"],
        "reported_auction_amount": evidence["reported_auction_amount"], "price_qualified": True,
        "auction_request_receipt_observed": True, "auction_trade_observed": True,
        "minute_source_observed": False, "minute_source_observed_dates": [],
        "t_daily_volume": 24_000, "t_daily_ohlc_flat_at_cent": True, "t_opening_limit_up_observed": False,
        "net_return": None, "conditional_net_return": None, "slot_net_return": None,
        "actual_exit_date": None, "actual_exit_time": None, "decision_time": None,
        "label_available_date": None, "label_available_at": None, "label_maturity_at": None,
        "held_limit_up_sessions": None, "cohort_complete": False,
        "missing_evidence_kind": "daily", "missing_evidence_date": scheduled, "missing_evidence_code": code}
    if source == "candidate":
        row.update(auction_source_policy_id=candidate.SOURCE_POLICY_ID,
            auction_qualification_policy_id=auction.SOURCE_POLICY_ID, source_overlay_policy_id=overlay.OVERLAY_POLICY_ID)
        evidence.update(source_policy_id=candidate.SOURCE_POLICY_ID, qualification_policy_id=auction.SOURCE_POLICY_ID,
            overlay_policy_id=overlay.OVERLAY_POLICY_ID, source_origin="candidate", daily_source_origin="base",
            source_only_metadata_rewritten=False)
    overlay.validate_label_contract(row)
    return row


def report(rows=None):
    return {"rows": [seed()] if rows is None else rows, "as_of_date": truth.AS_OF_DATE,
        "source_overlay_contract": deepcopy(overlay.CONTRACT), "round_trip_cost_rate": .0045,
        "research_only": True, "production_activation_allowed": False,
        "source_files": [{"origin": "base", "path": "unit/frozen.csv", "sha256": "a" * 64}],
        "cohorts_by_date": {}, "immutable_original_note": "retained"}


def full_session(day, price=10, **kwargs):
    value = session(day, price, **kwargs)
    value["minutes"].update(time_semantics=policy.MINUTE_TIME_SEMANTICS,
        provider_timestamp_semantics_confirmed=False, production_activation_allowed=False, research_only=True)
    return value


def run(value=None, *, sessions=None, proof_map=None, assertion=None, hooks=None):
    value = report() if value is None else value
    sessions = {T: full_session(T), T1: full_session(T1, 10.5)} if sessions is None else sessions
    calls, checks = [], []
    hooks = hooks or {}
    def loader(kind):
        def read(day, code):
            calls.append((kind, day, code))
            if kind in hooks: return hooks[kind](day, code)
            return sessions.get(day, {}).get(kind)
        return read
    def proof(day, code):
        calls.append(("proof", day, code))
        return (proof_map or {}).get((day, code))
    def check():
        checks.append(1)
        if assertion is not None: return assertion()
    out = resume.resume_missing_daily_labels(value, open_dates=DATES, as_of_date=truth.AS_OF_DATE,
        load_daily=loader("daily"), load_limits=loader("limits"), load_minutes=loader("minutes"),
        load_nontrading_session=proof, assert_sources_unchanged=check)
    return out, calls, checks


def frozen_proofs(fixture):
    return {(p.trade_date, p.ts_code): p for p in truth.verify_nontrading_sessions(**args(fixture))}


@pytest.mark.parametrize("source", ["base", "candidate"])
@pytest.mark.parametrize("exit_price", [9.5, 10, 10.5])
def test_success_same_original_entry_and_ids_and_fee_once(source, exit_price):
    original = report([seed(source=source)])
    untouched = deepcopy(original)
    out, calls, checks = run(original, sessions={T: full_session(T), T1: full_session(T1, exit_price)})
    row = out["rows"][0]
    assert row["label_status"] == policy.SETTLED
    assert row["net_return"] == row["conditional_net_return"] == row["slot_net_return"] == pytest.approx(exit_price / 10 - 1 - .0045)
    assert row["entry_price"] == untouched["rows"][0]["entry_price"]
    assert row["entry_price_evidence"] == untouched["rows"][0]["entry_price_evidence"]
    assert row["auction_source_policy_id"] == untouched["rows"][0]["auction_source_policy_id"]
    assert row["label_available_at"] == "2025-06-10T15:00:00+08:00"
    assert row["label_maturity_at"] == row["actual_exit_time"] == "2025-06-10T10:00:00+08:00"
    assert row["minute_source_observed_dates"] == [T1]
    assert row["nontrading_exit_supplement"]["nontrading_session_evidence"] == []
    assert out["source_files"] == original["source_files"] and original == untouched
    assert len(checks) == 2 and not any(k == "proof" for k, _, _ in calls)
    overlay.validate_label_contract(row)


def test_nontrading_halt_resumes_three_sessions_without_fabricated_marks(composition):
    out, calls, _ = run(sessions={T: full_session(T), "20250613": full_session("20250613", 9.5)},
                        proof_map=frozen_proofs(composition))
    row = out["rows"][0]
    assert row["net_return"] == pytest.approx(-.0545)
    assert row["actual_exit_date"] == "20250613"
    assert [v["trade_date"] for v in row["exit_evidence"]["wealth_chain"]] == ["20250613"]
    assert row["exit_evidence"]["delayed_trading_days"] == 3
    assert len(row["nontrading_exit_supplement"]["nontrading_session_evidence"]) == 3
    assert row["missing_evidence_date"] is row["missing_evidence_kind"] is None
    assert not any(kind == "minutes" and day in (T1, "20250611", "20250612") for kind, day, _ in calls)


def test_shared_proof_context_rechecked_without_rescanning_per_day(composition, monkeypatch):
    proofs = frozen_proofs(composition)
    calls, original = [], truth._Context.assert_unchanged
    def check(context):
        calls.append(1)
        original(context)
    monkeypatch.setattr(truth._Context, "assert_unchanged", check)
    run(sessions={T: full_session(T), "20250613": full_session("20250613")}, proof_map=proofs)
    # Library and resolver each check before/after; shared context is not
    # re-scanned for every skipped date inside either boundary.
    assert len(calls) == 4


def test_resume_uses_real_T_close_wealth_not_T_open_or_rebuy(composition):
    t = full_session(T)
    t["daily"].update(close=11, high=11)
    later = full_session("20250613", 11.1, pre_close=11, up=12.1, down=9.9)
    out, _, _ = run(sessions={T: t, "20250613": later}, proof_map=frozen_proofs(composition))
    row = out["rows"][0]
    assert row["entry_price"] == 10 and row["net_return"] == pytest.approx(.1055)
    assert row["exit_evidence"]["wealth_chain"][0]["previous_close"] == 11


def test_preserves_irrevocable_decision_across_halt_and_reseal(composition):
    row = seed(signal="20250605", execution=D, scheduled=T)
    first = full_session(T, 9)
    later = full_session("20250613", 9.9, pre_close=9, up=9.9, down=8.1)
    out, _, _ = run(report([row]), sessions={D: full_session(D), T: first, "20250613": later}, proof_map=frozen_proofs(composition))
    updated = out["rows"][0]
    assert updated["decision_time"] == "2025-06-09T10:00:00+08:00"
    assert updated["actual_exit_time"] == "2025-06-13T09:30:00+08:00"
    assert updated["net_return"] == pytest.approx(-.0145)


@pytest.mark.parametrize("missing,kind,status", [("daily", "daily", "PENDING_EXIT_MISSING_DAILY"),
    ("limits", "stk_limit", "PENDING_EXIT_MISSING_LIMITS"),
    ("minutes", resume.MINUTE_KIND, "PENDING_EXIT_MISSING_MINUTES")])
def test_next_real_missing_source_is_exact_and_never_zero(composition, missing, kind, status):
    later = full_session("20250613"); later[missing] = None
    out, _, _ = run(sessions={T: full_session(T), "20250613": later}, proof_map=frozen_proofs(composition))
    row = out["rows"][0]
    assert row["label_status"] == status and row["missing_evidence_date"] == "20250613"
    assert row["missing_evidence_code"] == CODE and row["missing_evidence_kind"] == kind
    assert row["net_return"] is row["conditional_net_return"] is row["slot_net_return"] is None
    assert row["actual_exit_date"] is row["label_available_date"] is None
    assert len(row["nontrading_exit_supplement"]["nontrading_session_evidence"]) == 3


def test_missing_proof_stops_on_original_gap_without_future_reads():
    out, calls, _ = run(sessions={T: full_session(T), "20250613": full_session("20250613", 11)})
    row = out["rows"][0]
    assert row["label_status"] == "PENDING_EXIT_MISSING_DAILY" and row["missing_evidence_date"] == T1
    assert all(day <= T1 for _, day, _ in calls)


@pytest.mark.parametrize("value", [True, {}, object()])
def test_plain_boolean_or_duck_proof_stays_pending(value):
    out, _, _ = run(sessions={T: full_session(T)}, proof_map={(T1, CODE): value})
    row = out["rows"][0]
    assert row["label_status"] == "PENDING_EXIT_NONTRADING_EVIDENCE_INVALID"
    assert row["net_return"] is None and not row["nontrading_exit_supplement"]["nontrading_session_evidence"]


@pytest.mark.parametrize("mutation", ["partial", "timestamp", "nonfinite", "provider_claim"])
def test_invalid_minutes_not_counted_as_observed_and_not_zero(mutation):
    values = {T: full_session(T), T1: full_session(T1)}
    payload = values[T1]["minutes"]
    if mutation == "partial": payload["rows"].pop()
    elif mutation == "timestamp": payload["rows"][0]["bar_end"] = "2025-06-10 09:30:00"
    elif mutation == "nonfinite": payload["rows"][30]["open"] = float("nan")
    else: payload["provider_timestamp_semantics_confirmed"] = True
    out, _, _ = run(sessions=values)
    row = out["rows"][0]
    assert row["label_status"].startswith("PENDING_") and row["net_return"] is None
    assert row["missing_evidence_date"] == T1 and row["missing_evidence_kind"] == resume.MINUTE_KIND
    assert row["minute_source_observed_dates"] == [] and row["minute_source_observed"] is False


def terminal_row(code="600000.SH"):
    row = seed(code=code)
    actual = "2025-06-10T10:00:00+08:00"
    row.update(label_status=policy.SETTLED, net_return=-.0245, conditional_net_return=-.0245, slot_net_return=-.0245,
        minute_source_observed=True, minute_source_observed_dates=[T1], actual_exit_date=T1,
        actual_exit_time=actual, decision_time=actual, label_available_date=T1,
        label_available_at="2025-06-10T15:00:00+08:00", label_maturity_at=actual,
        exit_evidence={"gross_return": -.02, "actual_exit_time": actual}, cohort_complete=False)
    overlay.validate_label_contract(row)
    return row


def no_fill_row(code="600001.SH"):
    row = seed(code=code)
    row.update(label_status="NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED", proxy_fill=0, slot_net_return=0.,
        t_opening_limit_up_observed=True, label_available_date=T, label_available_at="2025-06-09T15:00:00+08:00",
        label_maturity_at="2025-06-09T09:25:00+08:00")
    overlay.validate_label_contract(row)
    return row


def test_all_previous_terminals_preserved_and_only_cohort_complete_recalculated():
    before = report([terminal_row(), seed(), no_fill_row()])
    out, calls, _ = run(before)
    for index in (0, 2):
        assert {k: v for k, v in before["rows"][index].items() if k != "cohort_complete"} == {
            k: v for k, v in out["rows"][index].items() if k != "cohort_complete"}
        assert out["rows"][index]["cohort_complete"] is True
    assert all(code == CODE for _, _, code in calls)
    assert out["cohorts_by_date"][D]["expected_rows"] == out["cohorts_by_date"][D]["terminal_rows"] == 3
    assert out["rows"][0]["net_return"] < 0


def test_unprocessed_pending_and_order_preserved():
    other = seed(code="600000.SH"); other["label_status"] = "PENDING_EXIT_MISSING_MINUTES"
    before = report([other, seed(), terminal_row("600002.SH")])
    out, calls, _ = run(before)
    assert out["rows"][0] == before["rows"][0]
    assert [r["ts_code"] for r in out["rows"]] == [r["ts_code"] for r in before["rows"]]
    assert not out["cohorts_by_date"][D]["complete"]
    assert all(code == CODE for _, _, code in calls)


def test_nothing_eligible_does_not_call_price_or_proof_loaders():
    out, calls, checks = run(report([terminal_row(), no_fill_row()]))
    assert not calls and len(checks) == 2
    assert out["nontrading_resume"]["processed_row_count"] == 0


@pytest.mark.parametrize("price", ["10.000", 10.0001])
def test_original_reported_price_value_preserved_no_rounding(price):
    before = report([seed(price=price)])
    out, _, _ = run(before)
    assert type(out["rows"][0]["entry_price"]) is type(price) and out["rows"][0]["entry_price"] == price
    assert out["rows"][0]["net_return"] == pytest.approx(10.5 / float(price) - 1 - .0045)


@pytest.mark.parametrize("source", ["base", "candidate"])
def test_original_decimal_half_up_entry_boundary_is_not_rejected_by_float_tick(source):
    original = report([seed(source=source, price="1.005", opening=1.01)])
    sessions = {T: full_session(T, 1.01, pre_close=1, up=1.1, down=.9),
                T1: full_session(T1, 1.02, pre_close=1.01, up=1.11, down=.91)}
    out, _, _ = run(original, sessions=sessions)
    row = out["rows"][0]
    assert row["label_status"] == policy.SETTLED
    assert row["entry_price"] == "1.005" and row["entry_price_evidence"] == original["rows"][0]["entry_price_evidence"]
    assert row["net_return"] == pytest.approx(1.02 / 1.005 - 1 - .0045)


def test_actual_one_cent_entry_conflict_still_pending():
    sessions = {T: full_session(T, 1.01, pre_close=1, up=1.1, down=.9),
                T1: full_session(T1, 1.02, pre_close=1.01, up=1.11, down=.91)}
    out, _, _ = run(report([seed(price="1.00", opening=1.)]), sessions=sessions)
    assert out["rows"][0]["label_status"] == "PENDING_INVALID_SOURCE"
    assert out["rows"][0]["net_return"] is None


@pytest.mark.parametrize("mutation", ["missing", "wrongcode", "zero", "badclose", "entryconflict"])
def test_invalid_or_missing_T_close_never_uses_entry_as_replacement(mutation):
    values = {T: full_session(T), T1: full_session(T1)}
    if mutation == "missing": values[T]["daily"] = None
    elif mutation == "wrongcode": values[T]["daily"]["ts_code"] = "600000.SH"
    elif mutation == "zero": values[T]["daily"]["vol"] = 0
    elif mutation == "badclose": values[T]["daily"]["close"] = None
    else: values[T]["daily"].update(open=10.2, high=10.2)
    out, calls, _ = run(sessions=values)
    row = out["rows"][0]
    assert row["label_status"].startswith("PENDING_") and row["net_return"] is None
    assert row["missing_evidence_date"] == T and row["missing_evidence_kind"] == "daily"
    assert all(day == T for _, day, _ in calls)


class FutureTripwire(Mapping):
    def __getitem__(self, key):
        if key == "signal_date": return "20260914"
        raise AssertionError("future outcome or other fields must not be touched")
    def __iter__(self): raise AssertionError("future row must not be iterated")
    def __len__(self): raise AssertionError("future row must not be sized")
    def __deepcopy__(self, memo): raise AssertionError("future row must not be copied")


@pytest.mark.parametrize("position", [0, 1])
def test_future_outcomes_rejected_before_any_copy_hash_source_or_prior_outcome(position):
    rows = [seed()]; rows.insert(position, FutureTripwire())
    def forbidden(*_): raise AssertionError("source callback must not run")
    with pytest.raises(ValueError, match="FORWARD_D_OUTCOMES"):
        resume.resume_missing_daily_labels(report(rows), open_dates=DATES, as_of_date=truth.AS_OF_DATE,
            load_daily=forbidden, load_limits=forbidden, load_minutes=forbidden,
            load_nontrading_session=forbidden, assert_sources_unchanged=forbidden)


def test_duplicate_identity_rejected_before_source_calls():
    with pytest.raises(ValueError, match="DUPLICATE_LABEL_IDENTITY"): run(report([seed(), seed()]))


@pytest.mark.parametrize("key,value", [("proxy_fill", True), ("proxy_fill", 1.), ("slot_net_return", 0.),
                                      ("round_trip_cost_rate", .009), ("auction_source_policy_id", "old_v2")])
def test_invalid_pending_contract_not_misclassified_or_rebought(key, value):
    before = report(); before["rows"][0][key] = value
    with pytest.raises(ValueError): run(before)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_source_assertion_failure_aborts_report_instead_of_accepting(stage):
    checked = []
    def assertion():
        checked.append(1)
        if len(checked) == (1 if stage == "before" else 2): raise ValueError("changed source")
    with pytest.raises(ValueError, match="changed source"): run(assertion=assertion)


@pytest.mark.parametrize("returned", [True, False, {}, "verified"])
def test_assertion_callback_return_value_is_not_source_authority(returned):
    with pytest.raises(ValueError, match="NOT_AUTHORITY"): run(assertion=lambda: returned)


def test_missing_callback_or_noncallable_assertion_is_not_authority():
    with pytest.raises(ValueError, match="CALLBACKS"):
        resume.resume_missing_daily_labels(report(), open_dates=DATES, as_of_date=truth.AS_OF_DATE,
            load_daily=lambda *_: None, load_limits=lambda *_: None, load_minutes=lambda *_: None,
            load_nontrading_session=lambda *_: None, assert_sources_unchanged=True)


def test_software_error_surfaces_and_final_assertion_still_runs():
    checks = []
    def broken(*_): raise RuntimeError("software bug")
    with pytest.raises(RuntimeError, match="software bug"):
        run(hooks={"daily": broken}, assertion=lambda: checks.append(1))
    assert len(checks) == 2


def test_market_error_text_not_copied_to_output():
    def unavailable(*_): raise OSError("FAKE_SECRET_OR_PROVIDER_FREEFORM_MESSAGE")
    out, _, _ = run(hooks={"daily": unavailable})
    assert "FAKE_SECRET" not in str(out)
    assert out["rows"][0]["error_type"] == "OSError"
    assert out["rows"][0]["slot_net_return"] is None


def test_output_explicitly_does_not_certify_membership_all_prices_or_activation():
    out, _, _ = run()
    for flags in (out["nontrading_resume"], out["rows"][0]["nontrading_exit_supplement"]):
        assert flags["acceptance_basis"] == resume.ACCEPTANCE_BASIS
        for key in ("independent_acceptance", "all_price_sources_verified_by_library", "frozen_membership_verified_by_library",
                    "label_gate_passed", "training_performed", "production_activation_allowed", "actual_execution_claimed",
                    "actual_capacity_verified", "provider_timestamp_semantics_confirmed"):
            assert flags[key] is False
    assert out["source_overlay_contract"] == overlay.CONTRACT


def test_input_mutation_by_callback_detected():
    original = report()
    def mutate(day, code):
        original["immutable_original_note"] = "changed"
        return full_session(day)["daily"]
    with pytest.raises(ValueError, match="INPUT_REPORT_MUTATED"): run(original, hooks={"daily": mutate})


@pytest.mark.parametrize("where", ["market", "final_assertion"])
def test_late_future_row_injection_is_identity_checked_before_final_hash(where):
    original, checks = report(), []
    def mutate(day, code):
        original["rows"].append(FutureTripwire())
        return full_session(day)["daily"]
    def assertion():
        checks.append(1)
        if len(checks) == 2:
            original["rows"].append(FutureTripwire())
    with pytest.raises(ValueError, match="FORWARD_D_OUTCOMES"):
        run(original, hooks={"daily": mutate} if where == "market" else {},
            assertion=assertion if where == "final_assertion" else None)


def test_no_repeat_unregistered_resume_chain():
    out, _, _ = run()
    with pytest.raises(ValueError, match="UNREGISTERED_NONTRADING_RESUME_CHAIN"): run(out)


def test_no_cost_90bp_or_source_policy_change_from_caller():
    original = report(); original["source_overlay_contract"]["round_trip_cost_rate"] = .009
    with pytest.raises(ValueError, match="ECONOMIC_CONTRACT"): run(original)


def test_module_pin_failure_does_not_continue(monkeypatch):
    monkeypatch.setitem(resume._PINS, "nontrading_exit_1000.py", "0" * 64)
    with pytest.raises(ValueError, match="CODE_CHANGED"): run()
