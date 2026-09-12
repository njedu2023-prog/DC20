"""Pure forward scoring for the serialized fixed v3 research candidate.

No project imports, fitting, filesystem, network, freezing or activation occur.
An external caller MUST verify the real model and causal D-only source before
using this result. A matching caller-supplied SHA is not that source authority.
Unknown row columns are not read, copied, serialized or used for ranking.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re


SCHEMA = "dc20_fixed_candidate_forward_inference_20260913_v1"
MODEL_SCHEMA = "dc20_profit_1000_candidate_v3"
ENTRY_POLICY_ID = "research_canonical_price_capacity_split_no_cap_v3"
EXIT_POLICY_ID = "dc20_exit_1000_limit_hold_20260912_v1"
TRAIN_CUTOFF, FORWARD_START, TRAIN_START = "20251111", "20260914", "20221111"
SOURCE_SHA = "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
BASE_ARCHIVE_SHA = "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
NUMERIC_FEATURES = (
    "promotion_probability", "promotion_rank", "path_change", "d_pct_change", "volume_ratio",
    "ret_2d", "ret_5d", "ret_10d", "volatility_5d", "volatility_20d", "stage_pool_share",
    "five_year_board_stage_delta", "five_year_streak_runup", "five_year_pre_streak_1d_return",
    "five_year_recent_20d_rate", "five_year_recent_60d_rate", "five_year_stock_prior_rate",
    "focus_pool_size", "stage2_pool_size", "stage3_pool_size",
)
PATHS = ("持续强势", "弱转强", "加速一致", "强转弱", "持续弱势", "分歧回封", "路径混合")
FEATURES = NUMERIC_FEATURES + ("stage_2", "stage_3") + tuple("path_" + p for p in PATHS)
MISSING_SIGNALS = ("promotion_probability", "path_change", "path_label", "existing_profit_rank")
MODEL_SPEC = {"estimator": "Ridge", "alpha": 10.0, "fit_intercept": True, "solver": "svd",
    "target": "slot_net_return", "hyperparameter_search": False}
OVERLAY_CONTRACT = {
    "overlay_policy_id": "dc20_candidate_auction_label_overlay_20260913_v1",
    "entry_policy_id": ENTRY_POLICY_ID, "exit_policy_id": EXIT_POLICY_ID,
    "auction_source_policy_ids": ["dc20_research_canonical_price_capacity_split_20260912_v3",
        "dc20_research_candidate_auction_source_20260913_v1"],
    "auction_qualification_policy_id": "dc20_research_canonical_price_capacity_split_20260912_v3",
    "minute_source_policy_id": "dc20_research_stk_mins_query_0931_v1",
    "minute_time_semantics": "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED",
    "complete_empty_response_rule": "DAILY_OPEN_PROXY_CAPACITY_UNKNOWN",
    "missing_rejected_invalid_response_rule": "PENDING_NOT_FALLBACK_NOT_ZERO",
    "round_trip_cost_rate": .0045,
}
MODEL_KEYS = frozenset({
    "schema_version", "research_only", "production_activation_allowed", "label_policy_id",
    "specification", "features", "imputation_train_medians", "scaling_train_means", "scaling_train_scales",
    "coefficients", "intercept", "sklearn_version", "numpy_version", "fit_signal_date_min",
    "fit_signal_date_max", "fit_label_available_date_max", "entry_policy_id", "round_trip_cost_rate",
    "frozen_source_sha256", "source_overlay_contract", "input_bindings", "provider_timestamp_semantics_confirmed",
    "training_cutoff_date", "holdout_start_date",
})
BINDING_KEYS = frozenset({"source_sha256", "base_archive_sha256", "label_report_sha256",
    "candidate_collection_receipt_sha256", "development_frozen_rows_sha256", "development_label_rows_sha256",
    "market_collection_receipt_sha256"})
FLAGS = {"research_only": True, "training_performed": False, "files_written": 0, "network_calls_performed": 0,
    "production_activation_allowed": False, "model_source_independently_verified": False,
    "D_source_independently_verified": False, "complete_D_universe_independently_verified": False,
    "frozen_selection_issued": False, "natural_forward_ledger_written": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "provider_timestamp_semantics_confirmed": False, "profitability_improvement_proven": False,
    "future_outcomes_read": False, "holdout_performance_evaluated": False,
    "negative_score_skip_allowed": False, "unavailable_returns_imputed_zero": False,
    "historically_missing_signals_backfilled": False, "fees_subtracted_again": False}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def canonical_sha(value):
    """Same serialization as candidate_v3; a digest is NOT source admission."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _date(value, field):
    require(type(value) is str and re.fullmatch(r"20[0-9]{6}", value), "INVALID_DATE:" + field)
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("INVALID_DATE:" + field) from None
    return value


def _sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "EXPLICIT_EXTERNAL_MODEL_OR_SOURCE_SHA_REQUIRED")
    return value


def _number(value, field, *, nullable=False):
    if nullable and value is None:
        return None
    require(type(value) in (int, float), "EXACT_FINITE_NUMBER_REQUIRED:" + field)
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError("FINITE_NUMBER_REQUIRED:" + field) from None
    require(math.isfinite(number), "FINITE_NUMBER_REQUIRED:" + field)
    return number


def _exact(value, expected, reason):
    """Type-exact fixed JSON contract, without coercing opaque objects."""
    require(type(value) is type(expected), reason)
    if type(expected) is dict:
        require(set(value) == set(expected), reason)
        for key in expected:
            _exact(value[key], expected[key], reason)
    elif type(expected) is list:
        require(len(value) == len(expected), reason)
        for actual, fixed in zip(value, expected):
            _exact(actual, fixed, reason)
    else:
        require(value == expected, reason)


def _model(model, expected_sha, signal_date):
    _sha(expected_sha)
    require(type(model) is dict and set(model) == MODEL_KEYS, "EXACT_SERIALIZED_V3_MODEL_KEYS_REQUIRED")
    for field, expected in {"schema_version": MODEL_SCHEMA, "research_only": True,
            "production_activation_allowed": False, "label_policy_id": EXIT_POLICY_ID,
            "entry_policy_id": ENTRY_POLICY_ID, "round_trip_cost_rate": .0045,
            "frozen_source_sha256": SOURCE_SHA, "provider_timestamp_semantics_confirmed": False,
            "training_cutoff_date": TRAIN_CUTOFF, "holdout_start_date": FORWARD_START}.items():
        _exact(model[field], expected, "FIXED_V3_MODEL_METADATA_CHANGED:" + field)
    _exact(model["specification"], MODEL_SPEC, "FIXED_RIDGE_SPECIFICATION_CHANGED")
    _exact(model["features"], list(FEATURES), "FIXED_29_FEATURE_ORDER_CHANGED")
    _exact(model["source_overlay_contract"], OVERLAY_CONTRACT, "FIXED_SOURCE_OVERLAY_CONTRACT_CHANGED")
    start, end, mature = (_date(model[field], field) for field in
        ("fit_signal_date_min", "fit_signal_date_max", "fit_label_available_date_max"))
    require(TRAIN_START <= start <= end < mature < TRAIN_CUTOFF and end < signal_date,
        "TRAINING_AND_LABEL_MATURITY_MUST_PRECEDE_FIXED_CUTOFF_AND_D")
    bindings = model["input_bindings"]
    require(type(bindings) is dict and set(bindings) == BINDING_KEYS, "EXACT_DECLARED_INPUT_BINDINGS_REQUIRED")
    for key in BINDING_KEYS:
        if key == "market_collection_receipt_sha256" and bindings[key] is None:
            continue  # Explicit base-only provenance; not upgraded to a receipt.
        _sha(bindings[key])
    require(bindings["source_sha256"] == SOURCE_SHA and bindings["base_archive_sha256"] == BASE_ARCHIVE_SHA,
        "FIXED_DEVELOPMENT_SOURCE_BINDINGS_CHANGED")
    for field in ("sklearn_version", "numpy_version"):
        value = model[field]
        require(type(value) is str and 1 <= len(value) <= 64 and re.fullmatch(r"[0-9][A-Za-z0-9.+_-]*", value),
            "SERIALIZED_LIBRARY_VERSION_REQUIRED")
    vectors = {}
    for field in ("imputation_train_medians", "scaling_train_means", "scaling_train_scales", "coefficients"):
        values = model[field]
        require(type(values) is list and len(values) == 29, "EXACT_29_PARAMETER_VECTOR_REQUIRED:" + field)
        vectors[field] = tuple(_number(x, field) for x in values)
    require(all(x > 0 for x in vectors["scaling_train_scales"]), "STRICTLY_POSITIVE_SCALING_REQUIRED")
    vectors["intercept"] = _number(model["intercept"], "intercept")
    require(canonical_sha(model) == expected_sha, "SERIALIZED_MODEL_SHA_MISMATCH")
    return vectors


def _project(rows, signal_date):
    require(type(rows) is list and len(rows) <= 10, "ZERO_TO_TEN_SAME_D_CANDIDATES_REQUIRED")
    seen, ranks, projected = set(), set(), []
    # Pass all identities before touching any feature value.
    for row in rows:
        require(type(row) is dict, "EXACT_D_ROW_REQUIRED")
        require(_date(row.get("signal_date"), "signal_date") == signal_date, "ALL_ROWS_MUST_HAVE_THE_REQUESTED_D")
        code = row.get("ts_code")
        require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code), "EXACT_A_SHARE_CODE_REQUIRED")
        require(code not in seen, "DUPLICATE_CANDIDATE_CODE")
        seen.add(code)
    for row in rows:
        stage, rank = row.get("board_stage"), row.get("promotion_rank")
        require(type(stage) is int and stage in (2, 3), "ONLY_TWO_TO_THREE_OR_THREE_TO_FOUR")
        require(type(rank) is int and 1 <= rank <= len(rows) and rank not in ranks, "COMPLETE_CAUSAL_PROMOTION_RANKS_REQUIRED")
        ranks.add(rank)
        require(_date(row.get("feature_as_of_date"), "feature_as_of_date") == signal_date, "FEATURES_MUST_BE_AS_OF_D")
        trained_until = _date(row.get("promotion_oof_train_end"), "promotion_oof_train_end")
        require(trained_until < signal_date, "PROMOTION_MODEL_TRAIN_END_MUST_PRECEDE_D")
        for field in MISSING_SIGNALS:
            require(field in row and row[field] is None, "HISTORICALLY_MISSING_SIGNAL_MUST_REMAIN_NONE:" + field)
        selected = {"signal_date": signal_date, "ts_code": row["ts_code"], "board_stage": stage,
            "promotion_rank": rank, "feature_as_of_date": signal_date, "promotion_oof_train_end": trained_until,
            "path_label": None, "existing_profit_rank": None}
        for field in NUMERIC_FEATURES:
            require(field in row, "EXPLICIT_D_FEATURE_OR_NONE_REQUIRED:" + field)
            selected[field] = rank if field == "promotion_rank" else _number(row[field], field, nullable=True)
        projected.append(selected)
    require(ranks == set(range(1, len(rows) + 1)), "COMPLETE_CAUSAL_PROMOTION_RANKS_REQUIRED")
    return projected


def _feature_row(row):
    return [row[feature] for feature in NUMERIC_FEATURES] + [
        float(row["board_stage"] == stage) for stage in (2, 3)
    ] + [float(row["path_label"] == path) for path in PATHS]


def predict_forward(model, rows, *, expected_model_sha256, signal_date):
    """Return full ranks and two explicit slots, with no execution permission.

    The caller must separately verify complete D membership, causal promotion
    rank and real model provenance. Extra row values (including T prices and
    future outcomes) are deliberately never accessed. Scores are linear-model
    outputs, not probabilities, guarantees or independently accepted returns.
    """
    day = _date(signal_date, "signal_date")
    require(day >= FORWARD_START, "FORWARD_D_MUST_START_AT_20260914")
    parameters = _model(model, expected_model_sha256, day)
    selected = _project(rows, day)
    selected_sha = canonical_sha(selected)
    ranked = []
    for row in selected:
        features = _feature_row(row)
        terms = []
        for i, value in enumerate(features):
            imputed = parameters["imputation_train_medians"][i] if value is None else value
            standard = (imputed - parameters["scaling_train_means"][i]) / parameters["scaling_train_scales"][i]
            terms.append(_number(standard * parameters["coefficients"][i], "linear_term"))
        score = _number(sum(terms) + parameters["intercept"], "candidate_score")
        ranked.append({**row, "candidate_score": score})
    ranked.sort(key=lambda row: (-row["candidate_score"], row["ts_code"]))
    for rank, row in enumerate(ranked, 1):
        row["candidate_rank"] = rank
    slots = {}
    for index in range(2):
        row = ranked[index] if index < len(ranked) else None
        slots[f"top{index + 1}"] = {"status": "SCORED_CANDIDATE" if row is not None else "MISSING_CANDIDATE",
            "ts_code": None if row is None else row["ts_code"],
            "candidate_rank": None if row is None else row["candidate_rank"],
            "candidate_score": None if row is None else row["candidate_score"],
            "promotion_rank": None if row is None else row["promotion_rank"], "net_return": None}
    require(_model(model, expected_model_sha256, day) == parameters and canonical_sha(_project(rows, day)) == selected_sha,
        "MODEL_OR_WHITELIST_INPUT_CHANGED_DURING_INFERENCE")
    return {"schema_version": SCHEMA, "status": "SCORED" if ranked else "EMPTY_CANDIDATE_INPUT",
        "signal_date": day, "model_sha256": expected_model_sha256, "whitelisted_D_rows_sha256": selected_sha,
        "candidate_count": len(ranked), "rows": ranked, "slots": slots,
        "entry_policy_id": ENTRY_POLICY_ID, "label_policy_id": EXIT_POLICY_ID, "round_trip_cost_rate": .0045,
        "source_overlay_contract_sha256": canonical_sha(OVERLAY_CONTRACT),
        "ranking_rule": "DESC_CANDIDATE_SCORE_THEN_ASC_TS_CODE_SAME_AS_FIXED_CANDIDATE",
        "score_kind": "LINEAR_RESEARCH_SLOT_RETURN_SCORE_NOT_PROBABILITY_OR_GUARANTEED_RETURN",
        "floating_point_semantics": "BINARY64_ORDERED_SUM_NOT_BITWISE_BLAS_EQUIVALENCE_CLAIM",
        "model_source_requirement": "CALLER_MUST_INDEPENDENTLY_VERIFY_REAL_MODEL_AND_SOURCE_AUTHORITY",
        "input_binding_scope": "DECLARED_MODEL_SHA_AND_WHITELIST_VALUES_NOT_SOURCE_ADMISSION", **FLAGS}


__all__ = ["predict_forward"]
