"""Pure, immutable P0 -> research candidate D feature projection.

Inputs are decoded native scalars from a caller-checked exact dated P0 bundle.
This module does not open the four files, verify their bytes/publication, or
issue source authority. The runtime index is only a cross-checked declaration.
Unknown columns, including P0 path/profit outputs and future outcomes, are not
read, copied or hashed. No model, scoring, fitting, network or writes occur.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
import re
from types import MappingProxyType

from work.profit_1000_upgrade import candidate_live_feature_math as feature_math

SCHEMA = "dc20_unverified_candidate_d_feature_projection_20260913_v1"
TRAIN_END_KIND = "LIVE_PERSISTED_TRAIN_END"
FIVE_YEAR_RETAINED = ("five_year_board_stage_delta", "five_year_streak_runup",
    "five_year_pre_streak_1d_return", "five_year_recent_20d_rate", "five_year_recent_60d_rate")
MISSING_SIGNALS = ("promotion_probability", "path_change", "path_label", "existing_profit_rank")
DAILY_FEATURES = ("d_pct_change", "volume_ratio", "ret_2d", "ret_5d", "ret_10d",
    "volatility_5d", "volatility_20d")
MATH_SHA = "922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68"
INDEX_SCHEMA = "dc20_primary_d_runtime_index_v1"
FLAGS = {"research_only": True, "source_independently_verified": False,
    "four_file_bytes_verified_by_projection": False, "source_completeness_verified": False,
    "point_in_time_availability_verified": False, "natural_freeze_verified": False,
    "production_activation_allowed": False, "scoring_authorized": False,
    "model_predictions_computed": False, "model_training_performed": False,
    "source_authority_issued": False, "forward_selection_issued": False,
    "P0_rows_or_ranks_changed": False, "pool_recomputed_within_top10": False,
    "future_outcomes_read": False, "historical_OOF_rank_claimed": False,
    "historically_missing_signals_backfilled": False, "unavailable_returns_imputed_zero": False,
    "network_calls_performed": 0, "files_written": 0,
    "dependency_bytes_verified_by_projection": False}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _date(value):
    require(type(value) is str and re.fullmatch(r"20[0-9]{6}", value), "EXACT_DATE_REQUIRED")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("VALID_DATE_REQUIRED") from None
    return value


def _code(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", value), "EXACT_STOCK_CODE_REQUIRED")
    return value


def _number(value, *, nullable=False):
    if nullable and value is None:
        return None
    require(type(value) in (int, float), "EXACT_FINITE_SCALAR_REQUIRED")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("FINITE_SCALAR_REQUIRED") from None
    require(math.isfinite(result), "FINITE_SCALAR_REQUIRED")
    return result


def _sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "DECLARED_SHA_REQUIRED")
    return value


def _integer(value, low, high, reason):
    require(type(value) is int and low <= value <= high, reason)
    return value


def _stage(value):
    # The frozen CSV producer writes stage as 2.0/3.0, rank as native integer.
    require(type(value) in (int, float) and value in (2, 3), "EXACT_STAGE_2_OR_3_REQUIRED")
    return int(value)


def _freeze(value):
    # Only our scalar-only whitelist and locally created containers reach here.
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _math_contract():
    require(feature_math.SCHEMA == "dc20_candidate_live_feature_math_20260913_v1"
        and feature_math.DAILY_FEATURES == DAILY_FEATURES and feature_math.FIXED_HISTORY_CEILING == "20260814"
        and feature_math.REQUIRED_OBSERVED_BARS == 21, "HISTORICAL_MATH_CONTRACT_CHANGED")


def _identity_scope(runtime, contract, index, history, bars, day, trained_until, train_end_kind):
    """Every supplied identity/date precedes any numeric feature or truth read."""
    require(type(runtime) is list and type(history) is list and type(bars) is dict,
        "NATIVE_RUNTIME_HISTORY_LISTS_AND_BARS_BY_CODE_REQUIRED")
    require(type(contract) is dict and type(index) is dict, "EXACT_P0_CONTRACT_AND_INDEX_REQUIRED")
    require(train_end_kind == TRAIN_END_KIND and type(train_end_kind) is str,
        "EXPLICIT_LIVE_PERSISTED_TRAIN_END_REQUIRED")
    require(_date(trained_until) < day, "PROMOTION_TRAIN_END_MUST_PRECEDE_D")
    require(_date(contract.get("signal_date")) == day and _date(contract.get("feature_as_of_date")) == day
        and _date(index.get("latest_signal_date")) == day, "P0_EXACT_D_REQUIRED")
    exec_day, exit_day = _date(contract.get("exec_date")), _date(contract.get("exit_date"))
    require(day < exec_day < exit_day and _date(index.get("latest_exec_date")) == exec_day
        and _date(index.get("latest_exit_date")) == exit_day, "P0_D_T_TPLUS1_ORDER_REQUIRED")
    models = contract.get("models")
    require(type(models) is dict and type(models.get("promotion")) is dict, "P0_PROMOTION_MODEL_REQUIRED")
    require(_date(models["promotion"].get("model_as_of_date")) == trained_until,
        "CONTRACT_PERSISTED_TRAIN_END_MISMATCH")
    selected = contract.get("rows")
    require(type(selected) is list and len(selected) <= 10, "EXACT_P0_TOPN_LIST_REQUIRED")
    runtime_codes, top_codes = [], []
    for row in runtime:
        require(type(row) is dict, "EXACT_RUNTIME_ROW_REQUIRED")
        require(_date(row.get("signal_date")) == day, "RUNTIME_ROW_OUTSIDE_REQUESTED_D")
        require(_date(row.get("promotion_model_as_of_date")) == trained_until,
            "RUNTIME_PERSISTED_TRAIN_END_MISMATCH")
        runtime_codes.append(_code(row.get("ts_code")))
    for row in selected:
        require(type(row) is dict, "EXACT_P0_TOPN_ROW_REQUIRED")
        top_codes.append(_code(row.get("ts_code")))
    require(len(set(runtime_codes)) == len(runtime_codes) and len(set(top_codes)) == len(top_codes),
        "DUPLICATE_P0_STOCK_IDENTITY")
    history_keys = set()
    for row in history:
        require(type(row) is dict, "EXACT_HISTORY_ROW_REQUIRED")
        d, code = _date(row.get("signal_date")), _code(row.get("ts_code"))
        require(d < day and d <= "20260814" and d < "20260914", "HISTORY_OUTSIDE_FIXED_PRIOR_SCOPE")
        require((d, code) not in history_keys, "DUPLICATE_HISTORY_IDENTITY")
        history_keys.add((d, code))
    for code, rows in bars.items():
        _code(code)
        require(type(rows) is list, "EXACT_OBSERVED_BAR_LIST_REQUIRED")
        dates = set()
        for row in rows:
            require(type(row) is dict, "EXACT_BAR_ROW_REQUIRED")
            d, stock = _date(row.get("trade_date")), _code(row.get("ts_code"))
            require(d <= day and stock == code, "BAR_OUTSIDE_D_ONLY_IDENTITY_SCOPE")
            require(d not in dates, "DUPLICATE_OBSERVED_BAR")
            dates.add(d)
    require(set(bars) == set(top_codes), "EXACT_SELECTED_STOCK_BAR_COLLECTIONS_REQUIRED")
    return selected, models["promotion"], exec_day, exit_day


def _snapshot(runtime, contract, index, history, bars, day, trained_until, kind):
    selected, model, exec_day, exit_day = _identity_scope(
        runtime, contract, index, history, bars, day, trained_until, kind)
    require(index.get("schema_version") == INDEX_SCHEMA
        and index.get("index_kind") == "dated_primary_d_runtime_pointer_only"
        and index.get("data_alias") is False, "P0_RUNTIME_INDEX_CONTRACT_REQUIRED")
    require(contract.get("schema_version") == "decision_three_rank_top10_v1"
        and contract.get("artifact_kind") == "d_close_independent_three_rank_top10"
        and contract.get("membership_authority") == "promotion_probability_engine_only"
        and contract.get("downstream_scope") == "exact_frozen_promotion_top10",
        "FROZEN_P0_MEMBERSHIP_CONTRACT_REQUIRED")
    require(model.get("status") == "READY", "READY_PERSISTED_PROMOTION_MODEL_REQUIRED")
    artifact = _sha(model.get("artifact_sha256"))
    snapshot = _sha(contract.get("feature_snapshot_sha256"))
    members = _sha(contract.get("top10_members_sha256"))
    bundle = _sha(contract.get("bundle_sha256"))
    for key, value in (("latest_feature_snapshot_sha256", snapshot),
            ("latest_top10_members_sha256", members), ("latest_bundle_sha256", bundle)):
        require(index.get(key) == value, "INDEX_CONTRACT_DIGEST_MISMATCH:" + key)
    _sha(index.get("runtime_identity_sha256"))
    declarations = {}
    for suffix, name in (("receipt", f"primary_d_receipt_{day}.json"),
            ("runtime_features", f"primary_d_runtime_features_{day}.csv"),
            ("three_rank_json", f"three_rank_top10_{day}.json"),
            ("three_rank_csv", f"three_rank_top10_{day}.csv")):
        path = "outputs/decision/" + name
        require(index.get(f"latest_{suffix}_url") == path, "EXACT_DATED_P0_PATH_REQUIRED")
        declarations[suffix] = {"path": path, "sha256": _sha(index.get(f"latest_{suffix}_sha256"))}
    pool_count, count = len(runtime), min(10, len(runtime))
    for source, key, expected in ((contract, "promotion_pool_size", pool_count), (contract, "top10_count", count),
            (index, "runtime_feature_row_count", pool_count), (index, "runtime_selected_count", count)):
        require(type(source.get(key)) is int and source[key] == expected, "EXACT_FULL_POOL_TOPN_COUNTS_REQUIRED")
    require(len(selected) == count, "TOPN_MEMBERSHIP_COUNT_MISMATCH")
    # Rank/stage identities are checked over the complete pool, not its Top10.
    identity_rows = []
    for row in runtime:
        code, stage = row["ts_code"], _stage(row.get("stage"))
        rank = _integer(row.get("promotion_rank"), 1, pool_count, "EXACT_FULL_POOL_RANK_REQUIRED")
        chosen = _integer(row.get("top10_selected"), 0, 1, "EXACT_P0_SELECTION_FLAG_REQUIRED")
        require(chosen == int(rank <= count), "P0_SELECTION_IS_NOT_FROZEN_TOPN")
        require(row.get("stage_transition") == f"{stage}→{stage+1}"
            and row.get("identity") == f"{day}|{code}|{stage}→{stage+1}", "P0_STAGE_IDENTITY_MISMATCH")
        require(row.get("feature_snapshot_sha256") == snapshot
            and row.get("promotion_model_artifact_sha256") == artifact, "RUNTIME_SNAPSHOT_OR_MODEL_MISMATCH")
        identity_rows.append((code, stage, rank, chosen))
    require(sorted(x[2] for x in identity_rows) == list(range(1, pool_count + 1)), "COMPLETE_FULL_POOL_RANKS_REQUIRED")
    top = sorted((x for x in identity_rows if x[3]), key=lambda x: x[2])
    for row, (code, stage, rank, _) in zip(selected, top):
        require(row["ts_code"] == code and type(row.get("promotion_rank")) is int
            and row["promotion_rank"] == rank and row.get("stage_transition") == f"{stage}→{stage+1}"
            and type(row.get("top10_selected")) is int and row["top10_selected"] == 1,
            "FROZEN_TOPN_ORDER_RANK_OR_STAGE_MISMATCH")
    stages = Counter(stage for _, stage, _, _ in identity_rows)
    retained = []
    for row, (code, stage, rank, chosen) in zip(runtime, identity_rows):
        expected_pool = {"focus_pool_size": float(pool_count), "stage2_pool_size": float(stages[2]),
            "stage3_pool_size": float(stages[3]), "stage_pool_share": stages[stage] / pool_count}
        require(all(key in row and _number(row[key]) == value for key, value in expected_pool.items()),
            "P0_FULL_POOL_VALUES_DISAGREE_WITH_COMPLETE_POOL")
        values = {}
        if chosen:
            for key in FIVE_YEAR_RETAINED:
                require(key in row, "EXPLICIT_RETAINED_FIVE_YEAR_VALUE_OR_NONE_REQUIRED:" + key)
                values[key] = _number(row[key], nullable=True)
            retained.append({"signal_date": day, "ts_code": code, "board_stage": stage,
                "promotion_rank": rank, "feature_as_of_date": day, "promotion_oof_train_end": trained_until,
                "promotion_train_end_kind": kind, **expected_pool, **values})
    retained.sort(key=lambda row: row["promotion_rank"])
    # The math module uses these same strict scalar projections. Its second
    # local pass cannot inspect extra P0 fields or original outcome columns.
    safe_history = [{"signal_date": d, "ts_code": c, "promotion_hit": hit}
        for d, c, hit in feature_math._history_projection(history, day)]
    safe_bars = {code: [{"trade_date": d, "ts_code": code, "close": close, "volume": volume}
        for d, close, volume in feature_math._bar_projection(rows, day, code)] for code, rows in bars.items()}
    return {"rows": retained, "history": safe_history, "bars": safe_bars,
        "pool_identity": tuple(identity_rows), "exec_date": exec_day, "exit_date": exit_day,
        "source_declarations": declarations, "runtime_identity_sha256": index["runtime_identity_sha256"],
        "feature_snapshot_sha256": snapshot, "top10_members_sha256": members, "bundle_sha256": bundle,
        "promotion_model_artifact_sha256": artifact, "stage2_count": stages[2], "stage3_count": stages[3]}


def project_candidate_d_features(runtime_rows, primary_contract, runtime_index, history_rows, bars_by_code,
        *, signal_date, promotion_train_end, promotion_train_end_kind):
    """Cross-check declared P0 inputs, then return deeply immutable features.

    The caller must first run the existing four-file runtime checker and must
    independently verify source bytes, full-pool completeness, persisted model
    train cutoff and timely publication. No boolean supplied here grants any of
    these authorities. Native input decoding is explicit; CSV strings are not
    silently coerced. Mapping-proxy rows are intentionally not issued scorer
    inputs; the future source/activation layer remains a separate obligation.
    """
    day = _date(signal_date)
    _math_contract()
    args = (runtime_rows, primary_contract, runtime_index, history_rows, bars_by_code,
        day, promotion_train_end, promotion_train_end_kind)
    snapshot = _snapshot(*args)
    rows, diagnostics, blocked = [], [], []
    for row in snapshot["rows"]:
        code = row["ts_code"]
        daily = feature_math.compute_daily_features(snapshot["bars"][code], signal_date=day, ts_code=code)
        prior = feature_math.compute_stock_prior(snapshot["history"], signal_date=day, ts_code=code)
        require(daily["signal_date"] == prior["signal_date"] == day
            and daily["ts_code"] == prior["ts_code"] == code
            and set(daily["features"]) == set(DAILY_FEATURES)
            and set(prior["features"]) == {"five_year_stock_prior_rate"}, "MATH_RESULT_IDENTITY_OR_FIELDS_CHANGED")
        values = {key: _number(value, nullable=True) for key, value in daily["features"].items()}
        values["five_year_stock_prior_rate"] = _number(prior["features"]["five_year_stock_prior_rate"])
        complete = daily["observed_window_requirement_met"] is True
        require(complete == (daily["status"] == "UNVERIFIED_DAILY_FEATURES_COMPUTED"), "MATH_WINDOW_STATUS_MISMATCH")
        if not complete:
            blocked.append({"ts_code": code, "reason": daily["status"]})
        rows.append({**row, **values, **dict.fromkeys(MISSING_SIGNALS)})
        diagnostics.append({"ts_code": code, "daily_status": daily["status"],
            "observed_bar_count": daily["observed_bar_count"], "observed_window_requirement_met": complete,
            "prior_hits": prior["prior_hits"], "prior_known_samples": prior["prior_known_samples"],
            "prior_unknown_samples": prior["prior_unknown_samples"], "cold_prior_applied": prior["cold_prior_applied"],
            "supplied_pre_close_used": False})
    # Recheck original callers, with the same whole-input identity-before-value
    # order, after all mathematical callbacks. Never hash an unknown column.
    require(_snapshot(*args) == snapshot, "WHITELIST_INPUT_CHANGED_DURING_PROJECTION")
    _math_contract()
    history_end = max((row["signal_date"] for row in snapshot["history"]), default=None)
    return _freeze({"schema_version": SCHEMA, "status": "BLOCKED_DAILY_FEATURE_HISTORY" if blocked else
        ("PROJECTED_UNVERIFIED_D_FEATURES" if rows else "EMPTY_P0_CANDIDATE_INPUT"),
        "signal_date": day, "exec_date": snapshot["exec_date"], "exit_date": snapshot["exit_date"],
        "candidate_count": len(rows), "full_pool_size": len(runtime_rows), "stage2_pool_size": snapshot["stage2_count"],
        "stage3_pool_size": snapshot["stage3_count"], "projection_window_complete": not blocked,
        "rows": rows, "diagnostics": diagnostics, "blocked_candidates": blocked,
        "promotion_train_end": promotion_train_end, "promotion_train_end_kind": promotion_train_end_kind,
        "legacy_promotion_oof_train_end_field_meaning": TRAIN_END_KIND,
        "four_file_declarations": snapshot["source_declarations"],
        "runtime_identity_sha256": snapshot["runtime_identity_sha256"],
        "feature_snapshot_sha256": snapshot["feature_snapshot_sha256"],
        "top10_members_sha256": snapshot["top10_members_sha256"], "bundle_sha256": snapshot["bundle_sha256"],
        "promotion_model_artifact_sha256": snapshot["promotion_model_artifact_sha256"],
        "history_row_count": len(snapshot["history"]), "observed_history_end": history_end,
        "fixed_history_ceiling": "20260814", "fixed_history_predates_D": day > "20260814",
        "source_age_calendar_days": None if history_end is None else
            (datetime.strptime(day, "%Y%m%d") - datetime.strptime(history_end, "%Y%m%d")).days,
        "dependency_required_sha256": {"candidate_live_feature_math.py": MATH_SHA},
        "source_requirement": "CALLER_MUST_VERIFY_EXACT_D_P0_BYTES_FULL_POOL_HISTORY_BARS_AND_PUBLICATION",
        **FLAGS})


__all__ = ["project_candidate_d_features"]
