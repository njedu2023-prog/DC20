"""Fail-closed, research-only chronological candidate for the 10:00 exit rule.

The caller must bind the feature panel to frozen source bytes.  This module never
loads production models, writes a model, selects live trades, or promotes a model.
Historical validation is development evidence, not an untouched test set.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
import re


POLICY_ID = "dc20_exit_1000_limit_hold_20260912_v1"
SCHEMA = "dc20_profit_1000_candidate_v1"
HOLDOUT_START = "20260914"
SETTLED = "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"
DEFAULT_GATES = {
    "min_train_dates": 252,
    "min_train_rows": 1000,
    "min_validation_dates": 60,
    "min_validation_rows": 300,
    "min_validation_filled_rows": 50,
    "min_training_complete_day_fraction": 0.95,
    "min_validation_complete_day_fraction": 1.0,
}
# No auction/T-session feature and no outcome-derived field can enter the model.
NUMERIC_FEATURES = (
    "promotion_probability", "promotion_rank", "path_change",
    "d_pct_change", "volume_ratio", "ret_2d", "ret_5d", "ret_10d",
    "volatility_5d", "volatility_20d", "stage_pool_share",
    "five_year_board_stage_delta", "five_year_streak_runup",
    "five_year_pre_streak_1d_return", "five_year_recent_20d_rate",
    "five_year_recent_60d_rate", "five_year_stock_prior_rate",
    "focus_pool_size", "stage2_pool_size", "stage3_pool_size",
)
PATHS = ("持续强势", "弱转强", "加速一致", "强转弱", "持续弱势", "分歧回封", "路径混合")
FEATURES = NUMERIC_FEATURES + ("stage_2", "stage_3") + tuple("path_" + path for path in PATHS)
MODEL_SPEC = {"estimator": "Ridge", "alpha": 10.0, "fit_intercept": True,
              "solver": "svd", "target": "slot_net_return", "hyperparameter_search": False}


def _expect(condition, message):
    if not condition:
        raise ValueError(message)


def _date(value, field):
    _expect(isinstance(value, str) and re.fullmatch(r"20\d{6}", value), "INVALID_DATE:" + field)
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("INVALID_DATE:" + field) from exc
    return value


def _number(value, field, *, nullable=False):
    if nullable and value in (None, ""):
        return None
    _expect(not isinstance(value, bool), "INVALID_NUMBER:" + field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_NUMBER:" + field) from exc
    _expect(math.isfinite(number), "NONFINITE_NUMBER:" + field)
    return number


def _key(row):
    date = _date(row.get("signal_date"), "signal_date")
    code = row.get("ts_code")
    _expect(isinstance(code, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", code), "INVALID_STOCK_CODE")
    return date, code


def _positive_rank(value, field):
    number = _number(value, field)
    _expect(number >= 1 and number == int(number), "INVALID_RANK:" + field)
    return int(number)


def _prepare(frozen_rows, labels, manifest, as_of, holdout_start):
    _expect(isinstance(manifest, dict), "FROZEN_SOURCE_MANIFEST_REQUIRED")
    _expect(re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("source_sha256", ""))), "FROZEN_SOURCE_SHA_REQUIRED")
    _expect(manifest.get("source_provenance_verified") is True, "FROZEN_SOURCE_PROVENANCE_NOT_VERIFIED")
    _expect(manifest.get("feature_timestamp_semantics") == "RETROSPECTIVE_D_ONLY_BOUND", "D_ONLY_FEATURE_PROVENANCE_REQUIRED")
    _expect(manifest.get("promotion_prediction_provenance") == "HISTORICAL_OOF", "PROMOTION_OOF_PROVENANCE_REQUIRED")
    _expect(manifest.get("entry_policy_id") in {"research_auction_or_open_no_cap_v1", "research_auction_or_open_frozen_cap_v1"}, "REGISTERED_RESEARCH_ENTRY_POLICY_REQUIRED")
    _expect(manifest.get("cost_rate") == 0.0045, "REGISTERED_COST_RATE_MUST_BE_45BP")
    expected = manifest.get("day_candidate_counts")
    _expect(isinstance(expected, dict), "FROZEN_DAY_COUNTS_REQUIRED")
    for date, count in expected.items():
        _date(date, "manifest_day")
        _expect(type(count) is int and count >= 1, "INVALID_FROZEN_DAY_COUNT")
    by_day = defaultdict(list)
    frozen = {}
    ignored_holdout = 0
    for supplied in frozen_rows:
        key = _key(supplied)
        _expect(key not in frozen, "DUPLICATE_FROZEN_KEY")
        frozen[key] = dict(supplied)
        if key[0] >= holdout_start:
            ignored_holdout += 1
            continue
        _expect(key[0] <= as_of, "DEVELOPMENT_SIGNAL_AFTER_AS_OF")
        row = frozen[key]
        feature_as_of = _date(row.get("feature_as_of_date"), "feature_as_of_date")
        _expect(feature_as_of <= key[0], "FEATURE_KNOWN_AFTER_SIGNAL_DATE")
        promotion_train_end = _date(row.get("promotion_oof_train_end"), "promotion_oof_train_end")
        _expect(promotion_train_end < key[0], "PROMOTION_PREDICTION_TRAINED_ON_SIGNAL_OR_FUTURE")
        _expect(row.get("board_stage") in (2, 3), "ONLY_2_TO_3_AND_3_TO_4_ALLOWED")
        row["promotion_rank"] = _positive_rank(row.get("promotion_rank"), "promotion_rank")
        for feature in NUMERIC_FEATURES:
            row[feature] = _number(row.get(feature), feature, nullable=True)
        probability = row["promotion_probability"]
        _expect(probability is None or 0 <= probability <= 1, "PROMOTION_PROBABILITY_NOT_FRACTION")
        path = row.get("path_label")
        _expect(path is None or path in PATHS, "UNKNOWN_PATH_LABEL")
        rank = row.get("existing_profit_rank")
        row["existing_profit_rank"] = None if rank in (None, "") else _positive_rank(rank, "existing_profit_rank")
        by_day[key[0]].append(row)
    expected_development = {date: count for date, count in expected.items() if date < holdout_start}
    _expect(set(expected_development) == set(by_day), "FROZEN_DEVELOPMENT_DATE_SET_MISMATCH")
    for date, rows in by_day.items():
        _expect(len(rows) == expected_development[date], "INCOMPLETE_FROZEN_DAY:" + date)
        _expect(sorted(row["promotion_rank"] for row in rows) == list(range(1, len(rows) + 1)), "FROZEN_PROMOTION_RANKS_NOT_COMPLETE:" + date)
        known_profit = [row["existing_profit_rank"] for row in rows if row["existing_profit_rank"] is not None]
        _expect(len(known_profit) == len(set(known_profit)), "DUPLICATE_FROZEN_PROFIT_RANK:" + date)
    label_map = {}
    for supplied in labels:
        key = _key(supplied)
        _expect(key not in label_map, "DUPLICATE_LABEL_KEY")
        _expect(key in frozen, "LABEL_OUTSIDE_FROZEN_UNIVERSE")
        label_map[key] = supplied
        if key[0] >= holdout_start:
            continue  # Do not inspect future holdout outcomes or use them in any gate.
        _expect(supplied.get("label_policy_id") == POLICY_ID, "OLD_OR_UNKNOWN_EXIT_LABEL_FORBIDDEN")
        _expect(supplied.get("entry_policy_id") == manifest["entry_policy_id"], "MIXED_OR_UNKNOWN_ENTRY_LABEL_FORBIDDEN")
        _expect(type(supplied.get("round_trip_cost_rate")) in (int, float)
                and supplied["round_trip_cost_rate"] == manifest["cost_rate"], "MIXED_OR_UNKNOWN_COST_LABEL_FORBIDDEN")
    prepared = []
    for date in sorted(by_day):
        for row in sorted(by_day[date], key=lambda value: value["ts_code"]):
            label = label_map.get(_key(row))
            row["terminal"] = False
            row["slot_net_return"] = None
            row["proxy_fill"] = None
            row["actual_exit_date"] = None
            row["label_available_date"] = None
            if label is not None:
                status = str(label.get("label_status", ""))
                no_fill = status.startswith("NO_FILL_")
                terminal = status == SETTLED or no_fill
                _expect(terminal or status.startswith(("PENDING_", "MISSING_", "UNRESOLVED_")), "UNSUPPORTED_NEW_LABEL_STATUS")
                if terminal:
                    available = _date(label.get("label_available_date"), "label_available_date")
                    _expect(available > date, "LABEL_AVAILABLE_BEFORE_T_SESSION")
                    net = _number(label.get("slot_net_return"), "slot_net_return")
                    fill = label.get("proxy_fill")
                    _expect(type(fill) is int and fill == (0 if no_fill else 1), "TERMINAL_FILL_MISMATCH")
                    if no_fill:
                        _expect(net == 0, "NO_FILL_SLOT_RETURN_NOT_ZERO")
                        _expect(label.get("actual_exit_date") in (None, ""), "NO_FILL_HAS_EXIT_DATE")
                        _expect(label.get("conditional_net_return") in (None, ""), "NO_FILL_HAS_TRADE_RETURN")
                    else:
                        actual_exit = _date(label.get("actual_exit_date"), "actual_exit_date")
                        _expect(date < actual_exit <= available, "ACTUAL_EXIT_OR_LABEL_MATURITY_INVALID")
                        row["actual_exit_date"] = actual_exit
                    # A terminal artifact from the future is still unknown at this snapshot.
                    row["terminal"] = available <= as_of
                    row["slot_net_return"] = net if row["terminal"] else None
                    row["proxy_fill"] = fill if row["terminal"] else None
                    row["label_available_date"] = available
                else:
                    _expect(label.get("slot_net_return") in (None, ""), "PENDING_LABEL_HAS_RETURN")
            prepared.append(row)
    return prepared, ignored_holdout


def _feature_row(row):
    return [row.get(feature) for feature in NUMERIC_FEATURES] + [
        float(row["board_stage"] == stage) for stage in (2, 3)
    ] + [float(row.get("path_label") == path) for path in PATHS]


def _fit_ridge(training, validation):
    """Import fit dependencies only after the complete data-quality gate passes."""
    import numpy as np
    import sklearn
    from sklearn.linear_model import Ridge

    x = np.array([_feature_row(row) for row in training], dtype=float)
    valid_x = np.array([_feature_row(row) for row in validation], dtype=float)
    medians = np.array([float(np.median(column[np.isfinite(column)])) if np.isfinite(column).any() else 0.0 for column in x.T])
    x = np.where(np.isnan(x), medians, x)
    valid_x = np.where(np.isnan(valid_x), medians, valid_x)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales == 0] = 1.0
    estimator = Ridge(alpha=MODEL_SPEC["alpha"], fit_intercept=True, solver="svd")
    estimator.fit((x - means) / scales, np.array([row["slot_net_return"] for row in training]))
    scores = estimator.predict((valid_x - means) / scales)
    _expect(np.isfinite(scores).all(), "CANDIDATE_NONFINITE_PREDICTION")
    model = {
        "schema_version": SCHEMA, "research_only": True, "production_activation_allowed": False,
        "label_policy_id": POLICY_ID, "specification": dict(MODEL_SPEC),
        "features": list(FEATURES), "imputation_train_medians": medians.tolist(),
        "scaling_train_means": means.tolist(), "scaling_train_scales": scales.tolist(),
        "coefficients": estimator.coef_.tolist(), "intercept": float(estimator.intercept_),
        "sklearn_version": sklearn.__version__, "numpy_version": np.__version__,
        "fit_signal_date_min": min(row["signal_date"] for row in training),
        "fit_signal_date_max": max(row["signal_date"] for row in training),
        "fit_label_available_date_max": max(row["label_available_date"] for row in training),
    }
    return model, [float(value) for value in scores]


def _metrics(selected):
    filled = [row for row in selected if row["proxy_fill"] == 1]
    return {
        "slots": len(selected), "filled_proxy_slots": len(filled),
        "known_no_fill_slots": sum(row["proxy_fill"] == 0 for row in selected),
        "positive_filled_slots": sum(row["slot_net_return"] > 0 for row in filled),
        "filled_proxy_win_rate": sum(row["slot_net_return"] > 0 for row in filled) / len(filled) if filled else None,
        "mean_net_slot_return": sum(row["slot_net_return"] for row in selected) / len(selected) if selected else None,
        "mean_net_filled_proxy_return": sum(row["slot_net_return"] for row in filled) / len(filled) if filled else None,
        "minimum_net_slot_return": min((row["slot_net_return"] for row in selected), default=None),
        "capital_nav_claimed": False, "actual_execution_claimed": False,
    }


def _compare(validation, scores):
    by_day = defaultdict(list)
    for row, score in zip(validation, scores, strict=True):
        by_day[row["signal_date"]].append({**row, "candidate_score": score})
    baseline_complete = all(
        all(row["existing_profit_rank"] is not None for row in rows)
        and sorted(row["existing_profit_rank"] for row in rows) == list(range(1, len(rows) + 1))
        for rows in by_day.values()
    )
    comparisons = {}
    predictions = []
    sorters = {
        "candidate": lambda row: (-row["candidate_score"], row["ts_code"]),
        "frozen_promotion": lambda row: (row["promotion_rank"], row["ts_code"]),
    }
    if baseline_complete:
        sorters["frozen_existing_profit"] = lambda row: (row["existing_profit_rank"], row["ts_code"])
    else:
        comparisons["frozen_existing_profit"] = {"status": "UNAVAILABLE_SAME_UNIVERSE_BASELINE", "reason": "Missing historical frozen profit ranks; no reconstructed substitute."}
    for name, sorter in sorters.items():
        selections = {1: [], 2: []}
        for date, rows in sorted(by_day.items()):
            ordered = sorted(rows, key=sorter)
            for rank, row in enumerate(ordered, 1):
                if rank <= 2:
                    selections[rank].append(row)
                if name == "candidate":
                    predictions.append({
                        "signal_date": date, "ts_code": row["ts_code"],
                        "candidate_rank": rank, "candidate_score": row["candidate_score"],
                        "selected_shadow_slot": rank if rank <= 2 else None,
                        "promotion_rank": row["promotion_rank"],
                        "existing_profit_rank": row["existing_profit_rank"],
                        "slot_net_return": row["slot_net_return"], "proxy_fill": row["proxy_fill"],
                    })
        comparisons[name] = {"status": "DEVELOPMENT_PROXY_COMPARISON", "dates": sorted(by_day),
                             "round_trip_cost_rate": 0.0045,
                             "top1": _metrics(selections[1]), "top2": _metrics(selections[2]),
                             "stress_90bp_same_selections": {
                                 "round_trip_cost_rate": 0.009,
                                 "selection_reoptimized": False,
                                 **{"top" + str(rank): _metrics([
                                     {**row, "slot_net_return": row["slot_net_return"] - (0.0045 if row["proxy_fill"] == 1 else 0)}
                                     for row in selections[rank]]) for rank in (1, 2)
                                 }}}
    improvements = {}
    for name in ("frozen_promotion", "frozen_existing_profit"):
        if comparisons[name]["status"] == "UNAVAILABLE_SAME_UNIVERSE_BASELINE":
            improvements[name] = None
        else:
            improvements[name] = {
                "top" + str(rank): comparisons["candidate"]["top" + str(rank)]["mean_net_slot_return"] - comparisons[name]["top" + str(rank)]["mean_net_slot_return"]
                for rank in (1, 2)
            }
    return comparisons, improvements, predictions


def run_candidate(frozen_rows, label_rows, *, as_of_date, training_cutoff_date,
                  validation_end_date, holdout_start_date=HOLDOUT_START,
                  frozen_manifest=None, gates=None):
    """Fit one fixed Ridge candidate only after immutable full-date checks pass.

    ``frozen_manifest`` is produced by the source-verifying runner, never inferred
    from the candidate labels.  Gate overrides are for deterministic unit tests
    or explicitly registered experiments; all effective gates are reported.
    """
    report = {
        "schema_version": SCHEMA, "label_policy_id": POLICY_ID,
        "research_only": True, "production_activation_allowed": False,
        "training_performed": False, "actual_execution_claimed": False,
        "historical_validation_role": "DEVELOPMENT_PREVIOUSLY_INSPECTED_NOT_UNTOUCHED",
        "holdout_role": "FUTURE_FORWARD_ONLY_NOT_EVALUATED",
        "holdout_start_date": holdout_start_date, "holdout_metrics": None,
        "profitability_improvement_proven": False,
        "engineering_gates_are_profit_guarantee": False,
        "top1_top2_always_selected": True, "negative_score_skip_allowed": False,
        "risk_note": "Minute-price proxies do not prove auction fill, exit capacity, or a capital-constrained portfolio NAV.",
    }
    result = {"status": "BLOCKED_INPUT", "report": report, "candidate_model": None, "predictions": []}
    try:
        for value, field in ((as_of_date, "as_of_date"), (training_cutoff_date, "training_cutoff_date"),
                             (validation_end_date, "validation_end_date"), (holdout_start_date, "holdout_start_date")):
            _date(value, field)
        _expect(holdout_start_date == HOLDOUT_START, "PREREGISTERED_FORWARD_HOLDOUT_DATE_CANNOT_MOVE")
        _expect(training_cutoff_date <= validation_end_date < holdout_start_date, "INVALID_CHRONOLOGICAL_SPLIT")
        _expect(validation_end_date <= as_of_date, "VALIDATION_DATE_AFTER_AS_OF")
        effective_gates = dict(DEFAULT_GATES)
        if gates is not None:
            _expect(isinstance(gates, dict) and set(gates) <= set(DEFAULT_GATES), "UNKNOWN_QUALITY_GATE")
            effective_gates.update(gates)
        for key, value in effective_gates.items():
            if key.endswith("fraction"):
                _expect(type(value) in (int, float) and 0 < value <= 1, "INVALID_QUALITY_GATE:" + key)
            else:
                _expect(type(value) is int and value > 0, "INVALID_QUALITY_GATE:" + key)
        report["quality_gates"] = effective_gates
        report["engineering_defaults_weakened"] = any(effective_gates[key] < value for key, value in DEFAULT_GATES.items())
        rows, ignored_holdout = _prepare(list(frozen_rows), list(label_rows), frozen_manifest, as_of_date, holdout_start_date)
        report["frozen_source_sha256"] = frozen_manifest["source_sha256"]
        report["entry_policy_id"] = frozen_manifest["entry_policy_id"]
        report["round_trip_cost_rate"] = frozen_manifest["cost_rate"]
        report["entry_policy_production_compatibility"] = (
            "ENTRY_POLICY_DIFFERENT_FROM_FORMAL_FROZEN_CAP" if frozen_manifest["entry_policy_id"] == "research_auction_or_open_no_cap_v1"
            else "FORMAL_COMPATIBILITY_NOT_AUTOMATICALLY_ESTABLISHED"
        )
        report["holdout_rows_excluded_without_outcome_evaluation"] = ignored_holdout
        report["feature_coverage"] = {
            feature: {"known_rows": sum(row.get(feature) is not None for row in rows), "rows": len(rows)}
            for feature in NUMERIC_FEATURES + ("path_label",)
        }
        report["promotion_combination"] = "FROZEN_HISTORICAL_OOF_RANK_AS_FEATURE_AND_BASELINE; missing probabilities or paths are not backcast."
        train_days = defaultdict(list)
        validation_days = defaultdict(list)
        for row in rows:
            if row["signal_date"] < training_cutoff_date:
                train_days[row["signal_date"]].append(row)
            elif row["signal_date"] <= validation_end_date:
                validation_days[row["signal_date"]].append(row)
        complete_train = {date: cohort for date, cohort in train_days.items() if all(
            row["terminal"] and row["label_available_date"] < training_cutoff_date
            and (row["actual_exit_date"] is None or row["actual_exit_date"] < training_cutoff_date)
            for row in cohort)}
        complete_validation = {date: cohort for date, cohort in validation_days.items() if all(row["terminal"] for row in cohort)}
        training = [row for date in sorted(complete_train) for row in complete_train[date]]
        validation = [row for date in sorted(complete_validation) for row in complete_validation[date]]
        coverage = {
            "training_expected_dates": len(train_days), "training_complete_dates": len(complete_train),
            "training_complete_rows": len(training),
            "training_complete_day_fraction": len(complete_train) / len(train_days) if train_days else 0.0,
            "training_purged_or_incomplete_dates": sorted(set(train_days) - set(complete_train)),
            "validation_expected_dates": len(validation_days), "validation_complete_dates": len(complete_validation),
            "validation_complete_rows": len(validation),
            "validation_complete_day_fraction": len(complete_validation) / len(validation_days) if validation_days else 0.0,
            "validation_incomplete_dates": sorted(set(validation_days) - set(complete_validation)),
            "validation_filled_rows": sum(row["proxy_fill"] == 1 for row in validation),
        }
        report["coverage"] = coverage
        checks = {
            "min_train_dates": coverage["training_complete_dates"], "min_train_rows": len(training),
            "min_validation_dates": coverage["validation_complete_dates"], "min_validation_rows": len(validation),
            "min_validation_filled_rows": coverage["validation_filled_rows"],
            "min_training_complete_day_fraction": coverage["training_complete_day_fraction"],
            "min_validation_complete_day_fraction": coverage["validation_complete_day_fraction"],
        }
        failures = [key for key, observed in checks.items() if observed < effective_gates[key]]
        if len({row["slot_net_return"] for row in training}) < 2:
            failures.append("TRAINING_TARGET_HAS_NO_VARIATION")
        if any(len(cohort) < 2 for cohort in complete_validation.values()):
            failures.append("VALIDATION_DAY_HAS_FEWER_THAN_TWO_FROZEN_CANDIDATES")
        # Evaluation cannot hide missing rows/days even if a caller lowers a gate.
        if len(complete_validation) != len(validation_days):
            failures.append("INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED")
        report["failed_quality_gates"] = failures
        if failures:
            result["status"] = "BLOCKED_DATA_QUALITY"
            return result
        model, scores = _fit_ridge(training, validation)
        comparisons, improvements, predictions = _compare(validation, scores)
        report.update({"training_performed": True, "training_cutoff_date": training_cutoff_date,
                       "validation_start_date": min(complete_validation), "validation_end_date": max(complete_validation),
                       "comparisons": comparisons, "development_mean_slot_return_deltas": improvements,
                       "model_specification": dict(MODEL_SPEC), "additional_fees_already_in_labels": True})
        report["release_readiness"] = "NOT_ELIGIBLE_UNTOUCHED_FORWARD_HOLDOUT_REQUIRED"
        model.update({"frozen_source_sha256": frozen_manifest["source_sha256"],
                      "entry_policy_id": frozen_manifest["entry_policy_id"], "round_trip_cost_rate": frozen_manifest["cost_rate"],
                      "training_cutoff_date": training_cutoff_date, "holdout_start_date": holdout_start_date})
        result.update({"status": "DEVELOPMENT_ONLY_FITTED", "candidate_model": model, "predictions": predictions})
        return result
    except (ValueError, TypeError, KeyError) as exc:
        report["input_error"] = str(exc)
        return result
