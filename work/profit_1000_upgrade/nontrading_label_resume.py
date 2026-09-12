"""Pure research label continuation, NOT independent source/membership admission.

The caller must externally verify the report SHA, every price source and the
frozen full candidate universe. A callback is not authority for any of them.
Private nontrading proofs authorize only their exact absence/session fact, not
all prices read here. No network, source writes, training, source-ID projection
or portfolio/ledger mutation. The original entry and 45bp economics stay fixed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from work.profit_1000_upgrade import candidate_labels_overlay as overlay
from work.profit_1000_upgrade import nontrading_exit_1000 as adapter
from work.profit_1000_upgrade import nontrading_session_truth as truth
from work.profit_1000_upgrade import policy_v3 as policy

RESUME_POLICY_ID = "dc20_research_nontrading_label_resume_20260913_v1"
ACCEPTANCE_BASIS = "CALLER_VERIFIED_SOURCES_REQUIRED_NOT_INDEPENDENT_ACCEPTANCE"
MINUTE_KIND = "research_exit_1000_1m_0931"
_PINS = {
    "candidate_labels_overlay.py": "1c1aa1acf316e46fd0c86ed7f7310bf36474fc8bff47dcbcf8b9678ac68cc5f3",
    "nontrading_exit_1000.py": "0f8779d03db675b5c562d93bfec06251a47f9aa7769729e112332ab313c09482",
    "nontrading_session_truth.py": "0ce0ba594e02ce19fd5cf8f8aa48b1ebd40c1704d42361d3d202c88a29e56c48",
}
_SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
FLAGS = {"research_only": True, "independent_acceptance": False,
    "all_price_sources_verified_by_library": False, "frozen_membership_verified_by_library": False,
    "label_gate_passed": False, "training_performed": False, "production_activation_allowed": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "provider_timestamp_semantics_confirmed": False, "source_files_rewritten": False,
    "entry_requalified_or_rebought": False, "missing_values_imputed": False,
    "synthetic_ohlc_created": False}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _guard():
    overlay._guard(); adapter._guard()
    for name, digest in {**_PINS, Path(__file__).name: _SELF_SHA}.items():
        require(truth._sha(Path(__file__).with_name(name)) == digest, "NONTRADING_RESUME_CODE_CHANGED")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _identities(report):
    """All D boundaries first: never copy/hash/read a future row's outcome."""
    require(isinstance(report, Mapping), "LABEL_REPORT_MAPPING_REQUIRED")
    rows = report.get("rows")
    require(type(rows) is list and rows, "COMPLETE_LABEL_ROW_LIST_REQUIRED")
    identities = []
    for row in rows:
        require(isinstance(row, Mapping), "LABEL_ROW_MAPPING_REQUIRED")
        day = adapter._date(row["signal_date"])
        require(day < "20260914", "FORWARD_D_OUTCOMES_MUST_NOT_BE_READ")
        # Only identity fields are inspected until the whole population passes.
        execution = adapter._date(row["exec_date"])
        code = truth.old._code(row["ts_code"])
        require(day < execution, "SIGNAL_EXECUTION_IDENTITY_ORDER_CHANGED")
        identities.append((day, execution, code))
    require(len(identities) == len(set(identities)), "DUPLICATE_LABEL_IDENTITY")
    return tuple(identities)


def _terminal(row):
    return row["label_status"] == policy.SETTLED or row["label_status"] in policy.NO_FILL_STATUSES


def _source_assert(callback):
    require(callback() is None, "SOURCE_ASSERTION_MUST_RETURN_NONE_NOT_AUTHORITY")


def _resume_row(seed, dates, as_of, daily_loader, limit_loader, minute_loader, proof_loader):
    row = deepcopy(seed)
    code, execution, scheduled = row["ts_code"], row["exec_date"], row["scheduled_exit_date"]
    entry = row["entry_price"]  # Preserve original raw entry value in the row/evidence.
    adapter._number(entry)
    require(execution in dates and scheduled in dates and dates.index(scheduled) == dates.index(execution) + 1,
            "SCHEDULED_EXIT_MUST_REMAIN_NEXT_MARKET_SESSION")
    require(execution <= as_of and "nontrading_exit_supplement" not in row,
            "UNREGISTERED_NONTRADING_RESUME_CHAIN")
    reads, accepted, returned = [], [], {}
    observed = set(row["minute_source_observed_dates"])
    # Pending input has no realized result; do not inherit stale exit fields.
    for key in ("net_return", "conditional_net_return", "slot_net_return", "actual_exit_date", "actual_exit_time",
                "decision_time", "label_available_date", "label_available_at", "label_maturity_at", "held_limit_up_sessions"):
        row[key] = None
    row.pop("exit_evidence", None)
    row.pop("error_type", None)
    row.update(missing_evidence_kind=None, missing_evidence_date=None, missing_evidence_code=None)
    last_read = None

    def mark(day, kind):
        row.update(missing_evidence_date=day, missing_evidence_code=code, missing_evidence_kind=kind)

    def read(day, kind, loader):
        nonlocal last_read
        adapter._date(day)
        require(day <= as_of, "FUTURE_MARKET_TRUTH_FORBIDDEN")
        last_read = (day, kind)
        item = {"trade_date": day, "ts_code": code, "kind": kind, "result": "CALL_NOT_COMPLETED"}
        reads.append(item)
        try:
            value = loader(day, code)
        except (ValueError, OSError, TypeError):
            item["result"] = "SOURCE_ERROR"; mark(day, kind)
            raise
        item["result"] = "MISSING" if value is None else "SOURCE_RETURNED_NOT_INDEPENDENTLY_VERIFIED"
        if value is None:
            mark(day, kind)
        returned[(day, kind)] = value
        return value

    def minutes(day):
        payload = read(day, MINUTE_KIND, minute_loader)
        if payload is not None:
            require(isinstance(payload, Mapping) and payload.get("time_semantics") == policy.MINUTE_TIME_SEMANTICS
                    and payload.get("provider_timestamp_semantics_confirmed") is False
                    and payload.get("production_activation_allowed") is False and payload.get("research_only") is True,
                    "UNQUALIFIED_RESEARCH_MINUTE_SEMANTICS")
            # This repeats only the frozen pure shape/time/price check, not
            # source admission, so invalid returned bars are not "observed".
            day_daily = adapter._daily(adapter._identity(returned[(day, "daily")], day, code, "DAILY"))
            day_limits = adapter._identity(returned[(day, "stk_limit")], day, code, "LIMITS")
            adapter._minutes(payload, day, code, day_daily,
                adapter._number(day_limits["up_limit"]), adapter._number(day_limits["down_limit"]))
            observed.add(day)
        return payload

    def proof(day):
        value = proof_loader(day, code)
        if value is not None:
            # The frozen adapter itself enforces the private issued type,
            # exact date/code/asof, flags and its independent evidence context.
            adapter._accept(value, day, code, as_of, accepted)
            accepted.append(value)
            if (row["missing_evidence_date"], row["missing_evidence_kind"]) == (day, "daily"):
                row.update(missing_evidence_date=None, missing_evidence_code=None, missing_evidence_kind=None)
        return value

    try:
        t_daily = adapter._daily(adapter._identity(read(execution, "daily", daily_loader), execution, code, "DAILY"))
        # Entry qualification uses the frozen auction Decimal HALF_UP rule,
        # not the exit engine's float-tick helper (e.g. raw "1.005" -> 1.01).
        entry_cent = overlay.qualification._old()._cent
        require(t_daily["vol"] > 0 and entry_cent(t_daily["open"]) == entry_cent(entry),
                "ORIGINAL_ENTRY_AND_T_DAILY_CONFLICT")
        result, status = adapter.resolve_exit_1000_with_nontrading_sessions(
            dates, scheduled, as_of, code, entry, t_daily["close"],
            lambda day: read(day, "daily", daily_loader), lambda day: read(day, "stk_limit", limit_loader),
            minutes, load_nontrading_session=proof)
        row["label_status"] = status
        if result is not None:
            require(status == adapter.SETTLED_STATUS and result["actual_exit_date"] in observed,
                    "EXIT_RESULT_WITHOUT_OBSERVED_MINUTE")
            gross = overlay.base_labels._finite(result["gross_return"])
            net = gross - .0045  # One deduction; never cost-adjust entry or wealth links.
            row.update(label_status=policy.SETTLED, net_return=net, conditional_net_return=net, slot_net_return=net,
                actual_exit_date=result["actual_exit_date"], actual_exit_time=result["actual_exit_time"],
                decision_time=result["decision_time"], held_limit_up_sessions=result["held_limit_up_sessions"],
                label_maturity_at=result["actual_exit_time"], label_available_date=result["actual_exit_date"],
                label_available_at=overlay.base_labels._at(result["actual_exit_date"], "15:00:00"), exit_evidence=result,
                missing_evidence_date=None, missing_evidence_code=None, missing_evidence_kind=None)
        elif row["missing_evidence_date"] is None and status not in {
            "PENDING_T1", "PENDING_EXIT_CALENDAR_COVERAGE", "PENDING_EXIT_UNSELLABLE",
            "PENDING_EXIT_LIMIT_UP_HELD", "PENDING_EXIT_SUSPENDED", "PENDING_EXIT_UNRESOLVED",
        }:
            if last_read is not None:
                mark(*last_read)
    except adapter._DataError as exc:
        row["label_status"] = exc.status
        if last_read is not None: mark(*last_read)
    except (ValueError, OSError, TypeError) as exc:
        row.update(label_status="PENDING_INVALID_SOURCE", error_type=type(exc).__name__)
        if last_read is not None: mark(*last_read)
    # Software errors deliberately surface, and the outer source assertion still runs.
    row.update(minute_source_observed=bool(observed), minute_source_observed_dates=sorted(observed))
    row["nontrading_exit_supplement"] = {
        "resume_policy_id": RESUME_POLICY_ID, "acceptance_basis": ACCEPTANCE_BASIS,
        "original_label_status": seed["label_status"], "result_label_status": row["label_status"],
        "scheduled_exit_date_unchanged": scheduled, "entry_value_and_source_unchanged": True,
        "market_callback_reads": reads,
        "nontrading_session_evidence": [p.evidence() for p in accepted],
        "price_source_binding_responsibility": "CALLER_NOT_THIS_LIBRARY",
        "price_sources_independently_certified_by_nontrading_proof": False, **FLAGS,
    }
    checked_contexts = set()
    for value in accepted:
        if id(value._context) not in checked_contexts:
            value.assert_unchanged()
            checked_contexts.add(id(value._context))
    overlay.validate_label_contract(row)
    return row


def resume_missing_daily_labels(label_report, *, open_dates, as_of_date, load_daily, load_limits,
                               load_minutes, load_nontrading_session, assert_sources_unchanged):
    """Continue eligible pending rows; caller owns full membership/source acceptance.

    Price loaders receive (day, code) and return the original exact-day row or
    engine-compatible minute payload. None is genuine missing evidence, never
    zero. The source-assertion callback must raise on changes and return None;
    even successful calls do NOT confer independent acceptance on this report.
    """
    identities = _identities(label_report)
    _guard()
    require(as_of_date == truth.AS_OF_DATE and label_report.get("as_of_date") == as_of_date,
            "FIXED_HISTORICAL_ASOF_REQUIRED")
    dates = list(open_dates)
    require(dates and dates == sorted(set(dates)) and all(adapter._date(d) == d for d in dates)
            and as_of_date <= dates[-1], "STRICT_COMPLETE_OPEN_CALENDAR_REQUIRED")
    require(all(callable(c) for c in (load_daily, load_limits, load_minutes, load_nontrading_session, assert_sources_unchanged)),
            "EXPLICIT_SOURCE_CALLBACKS_REQUIRED")
    require(label_report.get("source_overlay_contract") == overlay.CONTRACT
            and label_report.get("round_trip_cost_rate") == .0045
            and label_report.get("research_only") is True
            and label_report.get("production_activation_allowed") is False,
            "FROZEN_RESEARCH_ECONOMIC_CONTRACT_REQUIRED")
    require("nontrading_resume" not in label_report, "UNREGISTERED_NONTRADING_RESUME_CHAIN")
    before_digest = _digest(label_report)
    result = deepcopy(label_report)
    originals = deepcopy(result["rows"])
    for row in originals:
        overlay.validate_label_contract(row)
    _source_assert(assert_sources_unchanged)
    processed = []
    try:
        for index, row in enumerate(originals):
            if row["label_status"] != "PENDING_EXIT_MISSING_DAILY" or type(row.get("proxy_fill")) is not int or row["proxy_fill"] != 1:
                continue
            result["rows"][index] = _resume_row(row, dates, as_of_date, load_daily, load_limits, load_minutes, load_nontrading_session)
            processed.append(index)
        cohorts = {}
        for day in sorted({i[0] for i in identities}):
            rows = [r for r in result["rows"] if r["signal_date"] == day]
            complete = all(_terminal(row) for row in rows)
            for row in rows:
                row["cohort_complete"] = complete
            cohorts[day] = {"expected_rows": len(rows), "terminal_rows": sum(_terminal(r) for r in rows),
                "complete": complete, "statuses": dict(Counter(r["label_status"] for r in rows)),
                "label_available_date": max(r["label_available_date"] for r in rows) if complete else None}
        require(_identities(result) == identities, "LABEL_MEMBERSHIP_OR_ORDER_CHANGED")
        for index, (before, after) in enumerate(zip(originals, result["rows"])):
            overlay.validate_label_contract(after)
            if index not in processed:
                require({k: v for k, v in before.items() if k != "cohort_complete"}
                        == {k: v for k, v in after.items() if k != "cohort_complete"}, "UNTOUCHED_LABEL_CHANGED")
            else:
                for key in before:
                    if key.startswith("entry_") or key in {"auction_source_policy_id", "source_overlay_policy_id", "auction_qualification_policy_id",
                            "capacity_amount", "capacity_proxy_verified", "capacity_evidence", "reported_auction_amount", "source_files", "proxy_fill"}:
                        require(before[key] == after[key], "FROZEN_ENTRY_OR_SOURCE_ID_CHANGED")
        result["cohorts_by_date"] = cohorts
        result["nontrading_resume"] = {"resume_policy_id": RESUME_POLICY_ID, "acceptance_basis": ACCEPTANCE_BASIS,
            "input_content_sha256": before_digest, "input_row_count": len(originals), "output_row_count": len(originals),
            "cohort_count": len(cohorts), "processed_row_count": len(processed),
            "original_terminal_row_count": sum(_terminal(r) for r in originals),
            "output_status_counts": dict(Counter(r["label_status"] for r in result["rows"])),
            "processed_identities": [list(identities[i]) for i in processed],
            "source_assertion_is_not_authority": True,
            "source_overlay_economic_contract_unchanged": deepcopy(overlay.CONTRACT), **FLAGS}
        return result
    finally:
        _source_assert(assert_sources_unchanged)
        _guard()
        # A callback might even replace the caller's input with a future row.
        # Recheck identities before the final content hash, including after
        # the final caller assertion. Never inspect that row's outcomes.
        require(_identities(label_report) == identities, "INPUT_REPORT_IDENTITIES_CHANGED_DURING_REPLAY")
        require(_digest(label_report) == before_digest, "INPUT_REPORT_MUTATED_DURING_REPLAY")


__all__ = ["resume_missing_daily_labels", "RESUME_POLICY_ID", "ACCEPTANCE_BASIS"]
