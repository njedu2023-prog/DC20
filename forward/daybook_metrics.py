"""Pure v2 daybook statistics, independent of the obsolete combined ledger.

Only supplied, validated epoch records are aggregated. This does not discover
missing scheduled D days or certify publication. A missing/invalid P1 cannot
suppress P0 Top1/2/3 evidence; absent P1 never becomes a successful empty list.
Unresolved slots have no returns. Equal-slot equity is a resolved-cohort
reference only, not realized account compounding (exits can overlap).
"""
from __future__ import annotations

import math

from .daybook import (_validate_epoch, _validate_profit, _validate_promotion,
                      _validate_sidecar)
from .ledger import T_FINAL, T1_FINAL, _date, _hash, _number

GROUPS = {
    "promotion_top1": ("promotion_rank", {1}),
    "promotion_top2": ("promotion_rank", {2}),
    "promotion_top3": ("promotion_rank", {3}),
    "promotion_top3_combined": ("promotion_rank", {1, 2, 3}),
    "profit_top1": ("profit_rank", {1}),
    "profit_top2": ("profit_rank", {2}),
    "profit_top2_combined": ("profit_rank", {1, 2}),
}


def _error(errors, kind, date, reason):
    # Do not publish arbitrary malformed payloads or exception text as evidence.
    errors.append({"kind": kind, "signal_date": date, "reason": reason})


def _auxiliary(kind, supplied, by_date, epoch, errors):
    result = {}
    for date, record in supplied.items():
        try:
            _date(date)
        except (ValueError, TypeError):
            _error(errors, kind, date if isinstance(date, str) else None, "INVALID_DATE_KEY")
            continue
        if date not in by_date:
            _error(errors, kind, date, "ORPHAN_NO_PROVIDED_PROMOTION")
            continue
        if record is None:
            continue  # Explicit absence and a missing map entry have one meaning.
        try:
            if kind == "profit":
                _validate_profit(record, by_date[date], epoch)
            else:
                _validate_sidecar(by_date[date], record)
        except (ValueError, TypeError, KeyError, OverflowError):
            _error(errors, kind, date, "INVALID_RECORD_OR_BINDING")
            continue
        result[date] = record
    return result


def _group(rank_field, ranks, promotions, profits, truths, return_eligible,
           invalid_profit_dates, invalid_truth_dates, method_mismatch_dates):
    profit_group = rank_field == "profit_rank"
    item = {"frozen_slots": 0, "d_days": 0, "t_validated": 0,
            "promotion_hits": 0, "promotion_hit_rate": None,
            "t1_settled": 0, "no_fill_slots": 0, "conditional_trades": 0,
            "conditional_positive_net_rate": None, "mean_net_return": None,
            "resolved_slots": 0, "resolved_group_days": 0,
            "unresolved_slots": 0, "unresolved_group_days": 0,
            "equity_basis": "resolved_cohort_reference_only", "complete": False,
            "cumulative_return": None, "max_drawdown": None, "daily_equity": [],
            "ranking_available_days": 0, "missing_ranking_days": 0,
            "invalid_ranking_days": 0, "missing_truth_days": 0,
            "invalid_truth_days": 0, "methodology_mismatch_days": 0}
    net_returns = []
    equity, peak, worst = 1.0, 1.0, 0.0
    for day in promotions:
        date = day["signal_date"]
        ranking = profits.get(date) if profit_group else day
        if ranking is None:
            key = "invalid_ranking_days" if date in invalid_profit_dates else "missing_ranking_days"
            item[key] += 1
            continue
        item["ranking_available_days"] += 1
        selected = [row for row in ranking["rows"] if row[rank_field] in ranks]
        if not selected:
            continue  # N=0 or an unavailable rank is valid, never padded.
        item["d_days"] += 1
        item["frozen_slots"] += len(selected)
        sidecar = truths.get(date)
        if sidecar is None:
            key = "invalid_truth_days" if date in invalid_truth_dates else "missing_truth_days"
            item[key] += 1
            continue
        truth = {row["ts_code"]: row for row in sidecar["verifications"][-1]["rows"]}
        if date in method_mismatch_dates:
            item["methodology_mismatch_days"] += 1
        resolved = []
        for candidate in selected:
            row = truth[candidate["ts_code"]]
            if row["t_status"] in T_FINAL:
                item["t_validated"] += 1
                item["promotion_hits"] += int(row["t_status"] == "PROMOTED")
            # The sidecar can prove T while its fee contract is inadmissible for
            # our return cohort. Preserve T, exclude *all* T1 return conclusions.
            if date not in return_eligible:
                continue
            if row["t1_status"] in T1_FINAL:
                item["resolved_slots"] += 1
                resolved.append(row["slot_return"])
            if row["t1_status"] == "NO_FILL":
                item["no_fill_slots"] += 1
            elif row["t1_status"] == "SETTLED":
                item["t1_settled"] += 1
                net_returns.append(row["net_return"])
        if len(resolved) == len(selected):
            daily_return = math.fsum(resolved) / len(selected)
            equity *= 1 + daily_return
            if not math.isfinite(equity):
                raise ValueError("cohort equity exceeds finite supported range")
            peak = max(peak, equity)
            drawdown = equity / peak - 1
            worst = min(worst, drawdown)
            item["daily_equity"].append({"signal_date": date, "slots": len(selected),
                "daily_return": daily_return, "equity": equity, "drawdown": drawdown})
    item["conditional_trades"] = len(net_returns)
    if net_returns:
        item["conditional_positive_net_rate"] = sum(value > 0 for value in net_returns) / len(net_returns)
        item["mean_net_return"] = math.fsum(net_returns) / len(net_returns)
    if item["t_validated"]:
        item["promotion_hit_rate"] = item["promotion_hits"] / item["t_validated"]
    item["resolved_group_days"] = len(item["daily_equity"])
    item["unresolved_slots"] = item["frozen_slots"] - item["resolved_slots"]
    item["unresolved_group_days"] = item["d_days"] - item["resolved_group_days"]
    item["complete"] = (item["d_days"] > 0 and item["unresolved_group_days"] == 0
                        and item["missing_ranking_days"] == 0 and item["invalid_ranking_days"] == 0)
    if item["resolved_group_days"]:
        item["cumulative_return"] = equity - 1
        item["max_drawdown"] = worst
    return item


def statistics_from_daybook(epoch, promotions, profits=None, truths=None, *, expected_costs_bps=45.0):
    """Aggregate immutable P0/P1/truth records without writing or importing v1.

    ``promotions`` is a chronologically ordered list; ``profits``/``truths`` are
    exact-D mappings. Malformed epoch/P0 or argument shapes fail the request.
    Invalid/orphan auxiliaries are isolated in ``auxiliary_errors`` and make
    coverage incomplete; invalid truth never contributes outcomes. A structurally
    valid truth with other fees can still contribute T promotion outcomes, but
    none of its T1 settlement/return conclusions. Fees must be supplied from the
    reviewed configuration by the eventual publisher; 45 bps is its current default.

    Coverage is only for the provided P0 days, including legitimate N=0 days.
    ``coverage.complete`` means all those P1/truth artifacts are present/valid,
    NOT that all truth has matured, the schedule has no missing D, or Pages is live.
    """
    _validate_epoch(epoch)
    if not isinstance(promotions, list):
        raise ValueError("promotions must be an ordered list of frozen P0 records")
    if profits is not None and not isinstance(profits, dict):
        raise ValueError("profits must be an exact-D mapping")
    if truths is not None and not isinstance(truths, dict):
        raise ValueError("truths must be an exact-D mapping")
    profits = {} if profits is None else profits
    truths = {} if truths is None else truths
    _number(expected_costs_bps)
    if expected_costs_bps < 0:
        raise ValueError("expected costs must be nonnegative")
    if promotions or profits or truths:
        _validate_epoch(epoch, active=True)
    by_date = {}
    previous = None
    for day in promotions:
        _validate_promotion(day, epoch)
        date = day["signal_date"]
        if previous is not None and date <= previous:
            raise ValueError("promotion days must be unique and chronological")
        previous = date
        by_date[date] = day
    errors = []
    accepted_profit = _auxiliary("profit", profits, by_date, epoch, errors)
    accepted_truth = _auxiliary("truth", truths, by_date, epoch, errors)
    invalid_profit = {item["signal_date"] for item in errors if item["kind"] == "profit"
                      and item["signal_date"] in by_date}
    invalid_truth = {item["signal_date"] for item in errors if item["kind"] == "truth"
                     and item["signal_date"] in by_date}
    method_mismatch = set()
    for date, sidecar in accepted_truth.items():
        if sidecar["verifications"][-1]["costs_bps"] != expected_costs_bps:
            method_mismatch.add(date)
            _error(errors, "truth_returns", date, "COST_METHODOLOGY_MISMATCH_T_RETAINED")
    return_eligible = set(accepted_truth) - method_mismatch
    groups = {name: _group(field, ranks, promotions, accepted_profit, accepted_truth,
                          return_eligible, invalid_profit, invalid_truth, method_mismatch)
              for name, (field, ranks) in GROUPS.items()}
    dates = list(by_date)
    missing_profit = [date for date in dates if date not in accepted_profit and date not in invalid_profit]
    missing_truth = [date for date in dates if date not in accepted_truth and date not in invalid_truth]
    coverage = {"basis": "provided_frozen_promotion_days_only",
                "schedule_coverage_verified": False, "provided_promotion_days": len(dates),
                "provided_signal_dates": dates,
                "empty_promotion_dates": [date for date in dates if not by_date[date]["rows"]],
                "profit_ready_days": len(accepted_profit), "profit_missing_dates": missing_profit,
                "profit_invalid_dates": sorted(invalid_profit),
                "profit_ready_empty_dates": [date for date in dates if date in accepted_profit
                                             and not accepted_profit[date]["rows"]],
                "truth_available_days": len(accepted_truth), "truth_missing_dates": missing_truth,
                "truth_invalid_dates": sorted(invalid_truth),
                "return_methodology_accepted_days": len(return_eligible),
                "truth_methodology_mismatch_dates": sorted(method_mismatch),
                "complete": not missing_profit and not missing_truth and not errors}
    sources = []
    for day in promotions:
        date = day["signal_date"]
        profit, truth = accepted_profit.get(date), accepted_truth.get(date)
        sources.append({"signal_date": date, "exec_date": day["exec_date"], "exit_date": day["exit_date"],
            "promotion_freeze_sha256": day["freeze_sha256"], "promotion_record_sha256": day["record_sha256"],
            "profit_sha256": profit["profit_sha256"] if profit else None,
            "profit_record_sha256": profit["record_sha256"] if profit else None,
            "truth_sidecar_sha256": truth["sidecar_sha256"] if truth else None,
            "truth_as_of_date": truth["verifications"][-1]["as_of_date"] if truth else None,
            "return_methodology_accepted": date in return_eligible})
    errors.sort(key=lambda item: (item["kind"], item["signal_date"] or "", item["reason"]))
    result = {"schema_version": "dc20_forward_daybook_statistics_v2", "epoch_id": epoch["epoch_id"],
              "epoch_sha256": epoch["epoch_sha256"], "activated_at_utc": epoch["activated_at_utc"],
              "start_signal_date": epoch["start_signal_date"], "groups": groups,
              "methodology": {"costs_bps": expected_costs_bps, "price_basis": "daily_open_proxy",
                              "research_only": True, "equity_basis": "resolved_cohort_reference_only"},
              "coverage": coverage, "auxiliary_errors": errors, "source_records": sources,
              "legacy_statistics_imported": False, "publication_verified": False}
    result["statistics_sha256"] = _hash(result)
    return result
