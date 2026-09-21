"""Versioned per-stock eligibility; no publication or source authority is issued.

The original complete promotion universe is always validated first. Only a
verified adapter's explicit per-stock history deficiency is isolatable. A bad
hash, malformed feature, date mismatch or duplicate identity remains fatal.
The registered model, frozen promotion ranks and full-pool features do not change.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

SCORER_SHA = "6ada7749e033b5d3e5c39c4d674d835b9113d83b4a37ef9d7e7fbad70ac95719"
SCHEMA = "dc20_fixed_candidate_eligible_inference_v2"
POLICY_ID = "dc20_individual_observed_window_eligibility_20260922_v2"
DAILY_FIELDS = ("d_pct_change", "volume_ratio", "ret_2d", "ret_5d", "ret_10d", "volatility_5d", "volatility_20d")
DEFICIENCIES = {"INSUFFICIENT_OBSERVED_HISTORY", "MISSING_D_OBSERVATION"}


def _scorer():
    path = Path(__file__).with_name("candidate_forward_predict.py")
    if hashlib.sha256(path.read_bytes()).hexdigest() != SCORER_SHA:
        raise ValueError("UNCHANGED_FIXED_SCORER_REQUIRED")
    from work.profit_1000_upgrade import candidate_forward_predict as scorer
    if Path(scorer.__file__).absolute() != path.absolute():
        raise ValueError("FIXED_SCORER_ORIGIN_CHANGED")
    return scorer


def classify_projection(projection):
    """Validate the full original universe, then assign per-code eligibility.

    This checks consistency, not source provenance. Callers still must pass the
    byte-bound source adapter and publication-time checks before any admission.
    """
    scorer = _scorer()
    require = scorer.require
    require(type(projection) is dict, "PLAIN_BOUND_PROJECTION_REQUIRED")
    day = scorer._date(projection.get("signal_date"), "signal_date")
    rows = scorer._project(projection.get("rows"), day)
    require(type(projection.get("candidate_count")) is int and projection["candidate_count"] == len(rows),
            "COMPLETE_ORIGINAL_CANDIDATE_COUNT_REQUIRED")
    diagnostics = projection.get("diagnostics")
    require(type(diagnostics) is list and len(diagnostics) == len(rows), "EXACT_PER_STOCK_DIAGNOSTICS_REQUIRED")
    by_code = {}
    for item in diagnostics:
        require(type(item) is dict and item.get("ts_code") not in by_code,
                "UNIQUE_PER_STOCK_DIAGNOSTICS_REQUIRED")
        by_code[item["ts_code"]] = item
    require(set(by_code) == {row["ts_code"] for row in rows}, "DIAGNOSTIC_UNIVERSE_CHANGED")
    eligibility, blocked = [], []
    for row in rows:
        diagnostic = by_code[row["ts_code"]]
        count = diagnostic.get("observed_bar_count")
        complete = diagnostic.get("observed_window_requirement_met")
        status = diagnostic.get("daily_status")
        require(type(count) is int and 0 <= count <= 21 and type(complete) is bool,
                "EXACT_OBSERVED_WINDOW_DIAGNOSTIC_REQUIRED")
        if complete:
            require(count == 21 and status == "UNVERIFIED_DAILY_FEATURES_COMPUTED",
                    "COMPLETE_HISTORY_DIAGNOSTIC_CONFLICT")
            require(row["d_pct_change"] is not None, "COMPLETE_D_RETURN_REQUIRED")
        else:
            require(status in DEFICIENCIES and (status != "INSUFFICIENT_OBSERVED_HISTORY" or count < 21),
                    "UNSUPPORTED_STOCK_EXCLUSION")
            require(all(row[field] is None for field in DAILY_FIELDS), "INCOMPLETE_HISTORY_MUST_NOT_SUPPLY_FEATURES")
            blocked.append({"ts_code": row["ts_code"], "reason": status})
        eligibility.append({"ts_code": row["ts_code"], "promotion_rank": row["promotion_rank"],
            "eligible": complete, "reason": None if complete else status,
            "observed_bar_count": count, "required_observed_bar_count": 21})
    require(type(projection.get("projection_window_complete")) is bool
            and projection["projection_window_complete"] == (not blocked), "GLOBAL_WINDOW_DIAGNOSTIC_CONFLICT")
    require(projection.get("blocked_candidates") == blocked, "EXCLUSION_LIST_CONFLICT")
    expected_status = "BLOCKED_DAILY_FEATURE_HISTORY" if blocked else (
        "PROJECTED_UNVERIFIED_D_FEATURES" if rows else "EMPTY_P0_CANDIDATE_INPUT")
    require(projection.get("status") == expected_status, "PROJECTION_STATUS_CONFLICT")
    return rows, eligibility


def _slot(rows, index, rank_key):
    row = next((r for r in rows if r[rank_key] == index), None)
    return {"slot": index, "status": "SELECTED" if row is not None else "INSUFFICIENT_CANDIDATES",
            "ts_code": row["ts_code"] if row else None,
            "promotion_rank": row["promotion_rank"] if row else None,
            "candidate_rank": row.get("candidate_rank") if row else None,
            "candidate_score": row.get("candidate_score") if row else None,
            "net_return": None}


def validate_prediction(prediction, projection, *, expected_model_sha256):
    """Read-only structural binding, not independent inference attestation."""
    scorer = _scorer()
    original, eligibility = classify_projection(projection)
    scorer._sha(expected_model_sha256)
    require = scorer.require
    require(type(prediction) is dict, "ELIGIBLE_PREDICTION_REQUIRED")
    allowed = {r["ts_code"] for r in eligibility if r["eligible"]}
    rows = prediction.get("rows")
    require(type(rows) is list and len(rows) == len(allowed), "EXACT_ELIGIBLE_COHORT_REQUIRED")
    by_code = {r["ts_code"]: r for r in original}
    seen = set()
    for rank, row in enumerate(rows, 1):
        require(type(row) is dict and row.get("ts_code") in allowed and row["ts_code"] not in seen,
                "UNIQUE_ELIGIBLE_PREDICTION_REQUIRED")
        seen.add(row["ts_code"])
        base = by_code[row["ts_code"]]
        require(set(row) == set(base) | {"candidate_rank", "candidate_score"}, "ELIGIBLE_ROW_FIELDS_CHANGED")
        scorer._exact({k:row[k] for k in base}, base, "ELIGIBLE_FEATURES_OR_PROMOTION_RANK_CHANGED")
        require(type(row["candidate_rank"]) is int and row["candidate_rank"] == rank, "CONTIGUOUS_PROFIT_RANKS_REQUIRED")
        scorer._number(row["candidate_score"], "candidate_score")
    require(rows == sorted(rows,key=lambda r:(-r["candidate_score"],r["ts_code"])), "ELIGIBLE_SCORE_ORDER_CHANGED")
    expected = {"schema_version": SCHEMA, "eligibility_policy_id": POLICY_ID, "signal_date": projection["signal_date"],
        "status": "SCORED_ELIGIBLE_POOL" if len(rows)>=2 else "INSUFFICIENT_ELIGIBLE_CANDIDATES",
        "model_sha256": expected_model_sha256, "fixed_scorer_sha256": SCORER_SHA,
        "original_candidate_count": len(original), "eligible_candidate_count": len(rows),
        "full_D_rows_sha256": scorer.canonical_sha(original), "eligibility": eligibility,
        "rows": rows, "promotion_rows": original,
        "candidate_slots": [_slot(rows,n,"candidate_rank") for n in (1,2)],
        "promotion_slots": [_slot(original,n,"promotion_rank") for n in (1,2,3)],
        "entry_policy_id": scorer.ENTRY_POLICY_ID, "exit_policy_id": scorer.EXIT_POLICY_ID,
        "round_trip_cost_rate": .0045, **scorer.FLAGS}
    scorer._exact(prediction,expected,"ELIGIBLE_PREDICTION_BINDING_CHANGED")
    return prediction


def predict_eligible(model, projection, *, expected_model_sha256):
    scorer = _scorer()
    rows, eligibility = classify_projection(projection)
    original = deepcopy(projection)
    day = projection["signal_date"]
    parameters = scorer._model(model, expected_model_sha256, day)
    allowed = {r["ts_code"] for r in eligibility if r["eligible"]}
    ranked = []
    # Same binary64 ordered linear arithmetic as the fixed scorer. Excluded
    # stocks are never scored and never receive synthetic replacement values.
    for row in rows:
        if row["ts_code"] not in allowed:
            continue
        terms = []
        for i, value in enumerate(scorer._feature_row(row)):
            imputed = parameters["imputation_train_medians"][i] if value is None else value
            standard = (imputed - parameters["scaling_train_means"][i]) / parameters["scaling_train_scales"][i]
            terms.append(scorer._number(standard * parameters["coefficients"][i], "linear_term"))
        ranked.append({**row, "candidate_score": scorer._number(sum(terms) + parameters["intercept"], "candidate_score")})
    ranked.sort(key=lambda r: (-r["candidate_score"], r["ts_code"]))
    for index, row in enumerate(ranked, 1):
        row["candidate_rank"] = index
    scorer.require(scorer._model(model, expected_model_sha256, day) == parameters
        and projection == original and classify_projection(projection) == (rows, eligibility),
        "MODEL_OR_BOUND_PROJECTION_CHANGED_DURING_INFERENCE")
    result = {"schema_version": SCHEMA, "eligibility_policy_id": POLICY_ID, "signal_date": day,
        "status": "SCORED_ELIGIBLE_POOL" if len(ranked) >= 2 else "INSUFFICIENT_ELIGIBLE_CANDIDATES",
        "model_sha256": expected_model_sha256, "fixed_scorer_sha256": SCORER_SHA,
        "original_candidate_count": len(rows), "eligible_candidate_count": len(ranked),
        "full_D_rows_sha256": scorer.canonical_sha(rows), "eligibility": eligibility,
        "rows": ranked, "promotion_rows": rows,
        "candidate_slots": [_slot(ranked, n, "candidate_rank") for n in (1, 2)],
        "promotion_slots": [_slot(rows, n, "promotion_rank") for n in (1, 2, 3)],
        "entry_policy_id": scorer.ENTRY_POLICY_ID, "exit_policy_id": scorer.EXIT_POLICY_ID,
        "round_trip_cost_rate": .0045, **scorer.FLAGS}
    return validate_prediction(result,projection,expected_model_sha256=expected_model_sha256)
