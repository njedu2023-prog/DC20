#!/usr/bin/env python3
"""Gated offline execution-proxy research. Never changes P0 or production weights."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

HERE = Path(__file__).resolve().parent
KEY = ["signal_date", "ts_code"]
PARAMETERS = {"max_iter": 200, "learning_rate": .05, "max_leaf_nodes": 7, "min_samples_leaf": 40, "l2_regularization": 10., "early_stopping": False, "random_state": 20260906}
TERMINAL_STATUSES = ["SETTLED_OPEN_PROXY", "NO_FILL_OPEN_LIMIT_UP_PROXY", "NO_FILL_ZERO_VOLUME_PROXY"]
SCORES = ["direct_slot_net", "direct_slot_net_downside"]
TRAINING_CONTRACT = {"min_train_complete_dates": 252, "min_train_rows": 1000, "evaluation_complete_dates": 180, "walk_forward_block_dates": 40, "feature_count": 48, "terminal_label_statuses": TERMINAL_STATUSES, "scores": SCORES, "downside_penalty": .5}
FEATURES = ["atr", "bid_ask_proxy", "candle_body", "d_close", "d_high", "d_low", "d_open", "d_pct_change", "d_volume", "five_year_board_stage_delta", "five_year_days_since_prior_limit_up", "five_year_pre_streak_1d_return", "five_year_pre_streak_3d_return", "five_year_pre_streak_limit_up_count", "five_year_pre_streak_volatility", "five_year_price_log", "five_year_prior_samples_log", "five_year_recent_20d_rate", "five_year_recent_60d_rate", "five_year_recent_60d_samples_log", "five_year_recent_limit_up_count", "five_year_regime_delta", "five_year_stage_board_prior_rate", "five_year_stage_prior_rate", "five_year_stock_prior_rate", "five_year_stock_prior_samples_log", "five_year_streak_runup", "focus_pool_size", "gap_open", "high_low_range", "mechanism_limit_pct", "ret_10d", "ret_2d", "ret_5d", "returns_1d", "spread_proxy", "stage2_pool_size", "stage3_pool_size", "stage_pool_share", "vol", "volatility_10d", "volatility_20d", "volatility_5d", "volume_ratio", "stage_2", "stage_3", "board_sh_main", "board_sz_main"]
LABEL_COLUMNS = ["exec_date", "scheduled_exit_date", "label_status", "proxy_fill", "slot_net_return", "slot_net_return_stress", "conditional_net_return", "label_available_date", "actual_exit_date", "blocked_exit_sessions"]
LEDGER_PATH = "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz"
LEDGER_SHA = "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
RESULT_FILES = ["training_candidate_predictions.csv.gz", "training_selected_records.csv.gz", "training_daily_returns.csv.gz", "training_comparison.json"]


def expect(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reject_symlink_chain(path):
    path = Path(path).absolute()
    for component in [path, *path.parents]:
        expect(not component.is_symlink(), "SYMLINK_PATH_FORBIDDEN:" + str(component))


def safe_input(root, relative):
    # Root may arrive through macOS's conventional /tmp alias; once canonical,
    # every component below it must be a real directory/file, not a redirect.
    root, relative = Path(root).resolve(strict=True), Path(relative)
    expect(not relative.is_absolute() and ".." not in relative.parts, "UNSAFE_INPUT_RELATIVE_PATH")
    path = root / relative
    reject_symlink_chain(path)
    expect(path.is_file(), "MISSING_INPUT_FILE:" + str(relative))
    return path


def output_directory():
    target = HERE / "outputs"
    reject_symlink_chain(target)
    target.mkdir(exist_ok=True)
    expect(target.resolve().parent == HERE, "OUTPUT_ESCAPED_RESEARCH_DIRECTORY")
    return target


def safe_output_file(path):
    path = Path(path).absolute()
    expect(path.parent == HERE / "outputs", "ONLY_FIXED_RESEARCH_OUTPUTS_ARE_WRITABLE")
    expect(path.name in RESULT_FILES + ["training_readiness.json"], "OUTPUT_FILENAME_NOT_ALLOWED")
    reject_symlink_chain(path)
    if path.exists():
        expect(path.is_file() and path.stat().st_nlink == 1, "SHARED_OR_NONREGULAR_OUTPUT_FORBIDDEN")
    return path


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    return value


def write_json(path, value):
    safe_output_file(path).write_text(json.dumps(safe(value), sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def csv_gz(path, frame):
    with safe_output_file(path).open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(frame.to_csv(index=False, lineterminator="\n", float_format="%.17g").encode())


def validate_plan(plan):
    expect(plan.get("hgb_parameters") == PARAMETERS, "PLAN_HGB_PARAMETER_SET_NOT_FIXED")
    supplied = plan.get("training", {})
    for key, value in TRAINING_CONTRACT.items():
        expect(supplied.get(key) == value, "PLAN_TRAINING_CONTRACT_MISMATCH:" + key)


def find_repo(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    for parent in HERE.parents:
        if (parent / LEDGER_PATH).is_file():
            return parent
    raise ValueError("REPOSITORY_NOT_FOUND_USE_REPO_ARGUMENT")


def read_csv(path):
    return pd.read_csv(path, low_memory=False, dtype={c: str for c in KEY + ["exec_date", "scheduled_exit_date", "label_available_date", "actual_exit_date"]})


def merge_labels(frozen, labels):
    """Preserve the entire frozen universe; missing rows become UNKNOWN, not zero."""
    expect(set(KEY + LABEL_COLUMNS).issubset(labels.columns), "LABEL_COLUMNS_MISSING")
    expect(not labels.duplicated(KEY).any() and not frozen.duplicated(KEY).any(), "DUPLICATE_CANDIDATE_KEYS")
    expect(set(map(tuple, labels[KEY].to_numpy())).issubset(set(map(tuple, frozen[KEY].to_numpy()))), "LABEL_KEYS_OUTSIDE_FIXED_FEATURE_PANEL")
    expect(set(FEATURES).issubset(frozen.columns), "FIXED_D_FEATURES_MISSING")
    keep = KEY + ["exec_date", "scheduled_exit_date", "promotion_rank"] + FEATURES
    # No old fill/return or 108 old outcome priors survive this projection.
    merged = frozen[keep].merge(labels[KEY + LABEL_COLUMNS], on=KEY, how="left", validate="one_to_one", suffixes=("", "_label"))
    supplied = merged.label_status.notna()
    for column in ["exec_date", "scheduled_exit_date"]:
        expect(merged.loc[supplied, column].eq(merged.loc[supplied, column + "_label"]).all(), "LABEL_DATE_BINDING_MISMATCH:" + column)
        merged = merged.drop(columns=[column + "_label"])
    merged["label_status"] = merged.label_status.fillna("MISSING_LABEL_ROW")
    for column in ["label_available_date", "actual_exit_date"]:
        merged[column] = merged[column].fillna("").astype(str)
    for column in ["proxy_fill", "slot_net_return", "slot_net_return_stress", "conditional_net_return", "blocked_exit_sessions"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    terminal = merged.label_status.isin(TERMINAL_STATUSES)
    settled = merged.label_status.eq("SETTLED_OPEN_PROXY")
    nofill = merged.label_status.isin(TERMINAL_STATUSES[1:])
    expect(merged.loc[terminal, "label_available_date"].str.fullmatch(r"20\d{6}").all(), "TERMINAL_LABEL_AVAILABILITY_DATE_INVALID")
    expect(np.isfinite(merged.loc[terminal, ["slot_net_return", "slot_net_return_stress", "proxy_fill"]].to_numpy()).all(), "TERMINAL_LABEL_TRUTH_MISSING")
    expect(merged.loc[nofill, "proxy_fill"].eq(0).all(), "NO_FILL_STATUS_NOT_ZERO_FILL")
    expect(merged.loc[nofill, ["slot_net_return", "slot_net_return_stress"]].eq(0).all().all(), "KNOWN_NO_FILL_NOT_CASH_ZERO")
    expect(merged.loc[nofill, "conditional_net_return"].isna().all(), "NO_FILL_HAS_CONDITIONAL_RETURN")
    expect(merged.loc[nofill, "actual_exit_date"].eq("").all(), "NO_FILL_HAS_FAKE_EXIT")
    expect(merged.loc[nofill, "label_available_date"].ge(merged.loc[nofill, "exec_date"]).all(), "NO_FILL_KNOWN_BEFORE_EXECUTION")
    expect(merged.loc[settled, "proxy_fill"].eq(1).all(), "SETTLED_STATUS_NOT_FILLED")
    expect(merged.loc[settled, "actual_exit_date"].str.fullmatch(r"20\d{6}").all(), "SETTLED_ACTUAL_EXIT_DATE_MISSING")
    expect(merged.loc[settled, "actual_exit_date"].ge(merged.loc[settled, "scheduled_exit_date"]).all(), "EXIT_PRECEDES_SCHEDULED_EXIT")
    expect(merged.loc[settled, "label_available_date"].ge(merged.loc[settled, "actual_exit_date"]).all(), "LABEL_AVAILABLE_BEFORE_ACTUAL_EXIT")
    expect(np.allclose(merged.loc[settled, "slot_net_return"], merged.loc[settled, "conditional_net_return"], rtol=0, atol=1e-12), "SETTLED_SLOT_CONDITIONAL_MISMATCH")
    expect(merged.loc[settled, "blocked_exit_sessions"].ge(0).all(), "BLOCKED_EXIT_SESSION_COUNT_MISSING")
    expect(merged.loc[terminal, "slot_net_return_stress"].le(merged.loc[terminal, "slot_net_return"] + 1e-12).all(), "STRESS_RETURN_EXCEEDS_BASE")
    expect(np.allclose(merged.loc[settled, "slot_net_return"] - merged.loc[settled, "slot_net_return_stress"], .0045, rtol=0, atol=1e-10), "SETTLED_STRESS_COST_DIFFERENCE_NOT_45BP")
    expect(not np.isinf(merged[FEATURES].to_numpy(dtype=float)).any(), "INFINITE_D_FEATURE")
    # Nonterminal numbers, even when provided, never make a row trainable.
    merged["_trainable"] = terminal
    return merged.sort_values(KEY, kind="stable").reset_index(drop=True)


def validate_gross_prices(labels):
    required = ["entry_price_proxy", "exit_price_proxy", "gross_return_proxy"]
    expect(set(required).issubset(labels.columns), "PRICE_AND_GROSS_AUDIT_COLUMNS_MISSING")
    settled = labels.label_status.eq("SETTLED_OPEN_PROXY")
    prices = labels.loc[settled, required].apply(pd.to_numeric, errors="coerce").astype(float)
    expect(np.isfinite(prices.to_numpy(dtype=float)).all(), "SETTLED_GROSS_PRICE_MISSING")
    expect(prices.entry_price_proxy.gt(0).all() and prices.exit_price_proxy.gt(0).all(), "NONPOSITIVE_SETTLED_PRICE")
    gross = prices.exit_price_proxy / prices.entry_price_proxy - 1
    expect(np.allclose(gross, prices.gross_return_proxy, rtol=0, atol=1e-10), "GROSS_PRICE_FORMULA_MISMATCH")
    expect(np.allclose(gross - .0045, labels.loc[settled, "slot_net_return"], rtol=0, atol=1e-10), "BASE_COST_NOT_45BP_FROM_GROSS")
    expect(np.allclose(gross - .009, labels.loc[settled, "slot_net_return_stress"], rtol=0, atol=1e-10), "STRESS_COST_NOT_90BP_FROM_GROSS")


def validate_calendar(frame, opened, asof):
    expect(opened == sorted(set(opened)), "STRICT_SSE_CALENDAR_NOT_UNIQUE_SORTED")
    expect(isinstance(asof, str) and asof in opened, "AS_OF_NOT_STRICT_SSE_SESSION")
    positions = {date: i for i, date in enumerate(opened)}
    for row in frame[["signal_date", "exec_date", "scheduled_exit_date"]].drop_duplicates().itertuples(index=False):
        expect(row.signal_date in positions and positions[row.signal_date] + 2 < len(opened), "D_OUTSIDE_STRICT_SSE_CALENDAR")
        i = positions[row.signal_date]
        expect(row.exec_date == opened[i + 1] and row.scheduled_exit_date == opened[i + 2], "D_T_T1_NOT_ADJACENT_SSE_SESSIONS")
    terminal = frame.loc[frame._trainable]
    expect(terminal.label_available_date.isin(opened).all(), "TERMINAL_AVAILABILITY_NOT_SSE_SESSION")
    expect(terminal.label_available_date.le(asof).all(), "LABEL_AVAILABILITY_AFTER_AS_OF_DATE")
    settled = terminal.loc[terminal.label_status.eq("SETTLED_OPEN_PROXY")]
    expect(settled.actual_exit_date.isin(opened).all(), "ACTUAL_EXIT_NOT_SSE_SESSION")
    expect(settled.actual_exit_date.le(asof).all(), "ACTUAL_EXIT_AFTER_AS_OF_DATE")


def validate_manifest_metadata(manifest, plan, plan_sha, builder_sha):
    expect(manifest.get("plan_sha256") == plan_sha, "LABEL_MANIFEST_PLAN_SHA_MISMATCH")
    expect(manifest.get("source_commit") == plan.get("source_commit"), "LABEL_MANIFEST_SOURCE_COMMIT_MISMATCH")
    expect(manifest.get("as_of_date") == plan.get("as_of_date"), "LABEL_MANIFEST_AS_OF_MISMATCH")
    expect(manifest.get("source_inputs") == plan.get("source_inputs"), "LABEL_MANIFEST_SOURCE_INPUTS_MISMATCH")
    expect(manifest.get("builder_sha256") == builder_sha, "LABEL_BUILDER_SHA_MISMATCH")
    expect(manifest.get("identity_unchanged") is True and manifest.get("missing_as_zero") is False, "LABEL_IDENTITY_OR_MISSINGNESS_CLAIM_INVALID")
    expect(manifest.get("actual_execution_claimed") is False, "ACTUAL_EXECUTION_CLAIM_NOT_ALLOWED")


def complete_dates(frame, cutoff=None):
    valid = frame._trainable.copy()
    if cutoff is not None:
        valid &= frame.label_available_date.lt(cutoff)
    good = valid.groupby(frame.signal_date).all()
    return sorted(good.index[good].tolist())


def training_at(frame, cutoff):
    dates = complete_dates(frame, cutoff)
    train = frame.loc[frame.signal_date.isin(dates)].copy()
    expect(not len(train) or train.label_available_date.max() < cutoff, "TRAINING_LABEL_REACHES_CUTOFF")
    return train


def assess_readiness(frame, plan):
    validate_plan(plan)
    available = complete_dates(frame)
    evaluation = available[-180:] if len(available) >= 180 else []
    masks = []
    for date, group in frame.groupby("signal_date", sort=True):
        if not group._trainable.all():
            masks.append({"signal_date": date, "frozen_candidates": len(group), "terminal_candidates": int(group._trainable.sum()), "unavailable_statuses": group.loc[~group._trainable, "label_status"].value_counts().to_dict()})
    reasons, folds = [], []
    if len(available) < 432:
        reasons.append("INSUFFICIENT_COMPLETE_DATES_FOR_252_TRAIN_PLUS_180_EVALUATION")
    if not evaluation:
        reasons.append("FEWER_THAN_180_COMPLETE_EVALUATION_DATES")
    for offset in range(0, len(evaluation), 40):
        dates = evaluation[offset:offset + 40]
        train = training_at(frame, dates[0])
        info = {"fold": offset // 40 + 1, "first_evaluation_D": dates[0], "last_evaluation_D": dates[-1], "evaluation_dates": len(dates), "train_complete_dates": train.signal_date.nunique(), "train_rows": len(train), "maximum_train_label_available_date": train.label_available_date.max() if len(train) else None}
        folds.append(info)
        if info["train_complete_dates"] < 252 or len(train) < 1000:
            reasons.append("FOLD_" + str(info["fold"]) + "_TRAINING_BELOW_252_COMPLETE_DAYS_OR_1000_ROWS")
    return {"schema_version": "dc20_execution_v2_training_readiness_v1", "status": "BLOCKED_INSUFFICIENT_EXECUTION_LABELS" if reasons else "READY_OFFLINE_RESEARCH_ONLY", "ready": not reasons, "reasons": reasons, "fixed_training_contract": TRAINING_CONTRACT, "frozen_rows": len(frame), "frozen_candidate_signal_dates": frame.signal_date.nunique(), "terminal_rows": int(frame._trainable.sum()), "complete_signal_dates": len(available), "complete_D_dates": available, "evaluation_D_dates": evaluation, "excluded_incomplete_D_dates": masks, "label_status_counts": frame.label_status.value_counts().to_dict(), "folds": folds, "models_fit": 0, "model_weights_saved": False, "result_artifacts_valid": False, "valid_output_files": ["training_readiness.json"], "no_fill_is_known_cash_zero": True, "missing_truth_as_zero": False, "release_allowed": False, "production_changed": False, "P0_ranking_changed": False, "new_forward_evidence": False, "evidence_role": "RETROSPECTIVE_EXECUTION_PROXY_RESEARCH_NOT_ACTUAL_TRADES", "existing_mixed_same_window_comparability": "NOT_ESTABLISHED_PRODUCTION_TRAINING_CUTOFF_CANNOT_BE_BACKCAST"}


def fit_heads(train):
    expect(train.signal_date.nunique() >= 252 and len(train) >= 1000, "FIT_BELOW_MINIMUM_TRAINING_SUPPORT")
    expect(train._trainable.all(), "FIT_CONTAINS_NONTERMINAL_LABELS")
    direct = HistGradientBoostingRegressor(loss="squared_error", **PARAMETERS)
    downside = HistGradientBoostingRegressor(loss="squared_error", **PARAMETERS)
    direct.fit(train[FEATURES], train.slot_net_return)
    downside.fit(train[FEATURES], np.maximum(-train.slot_net_return, 0.))
    return direct, downside


def predict_heads(models, target):
    direct, downside = models
    predicted = target.copy()
    predicted["direct_prediction"] = direct.predict(target[FEATURES])
    predicted["downside_prediction"] = np.maximum(downside.predict(target[FEATURES]), 0.)
    predicted["direct_slot_net"] = predicted.direct_prediction
    predicted["direct_slot_net_downside"] = predicted.direct_prediction - .5 * predicted.downside_prediction
    predicted["promotion_top2"] = -predicted.promotion_rank
    expect(np.isfinite(predicted[SCORES].to_numpy()).all(), "NONFINITE_PREDICTION")
    return predicted


def select_fixed_top2(frame, policy):
    selected, daily = [], []
    for date, group in frame.groupby("signal_date", sort=True):
        chosen = group.sort_values([policy, "ts_code"], ascending=[False, True], kind="stable").head(2).copy()
        chosen["policy"], chosen["slot"] = policy, np.arange(1, len(chosen) + 1)
        selected.append(chosen)
        rows = []
        for slot in range(2):
            candidate = chosen.iloc[slot] if slot < len(chosen) else None
            item = {"signal_date": date, "policy": policy, "slot": "Top" + str(slot + 1), "net": float(candidate.slot_net_return) if candidate is not None else 0., "stress": float(candidate.slot_net_return_stress) if candidate is not None else 0., "fill": float(candidate.proxy_fill) if candidate is not None else 0., "candidate_slots": int(candidate is not None), "absent_cash_slots": int(candidate is None)}
            daily.append(item)
            rows.append(item)
        daily.append({"signal_date": date, "policy": policy, "slot": "equal_Top2", **{key: sum(row[key] for row in rows) / (1 if key in ["candidate_slots", "absent_cash_slots"] else 2) for key in ["net", "stress", "fill", "candidate_slots", "absent_cash_slots"]}})
    return pd.concat(selected, ignore_index=True), pd.DataFrame(daily)


def metric(values):
    a = np.asarray(values, dtype=float)
    return {"mean_net": a.mean(), "positive_rate": (a > 0).mean(), "mean_win": a[a > 0].mean() if (a > 0).any() else None, "mean_loss": a[a < 0].mean() if (a < 0).any() else None, "worst_day": a.min(), "worst_decile_mean": np.sort(a)[:max(1, math.ceil(len(a) * .1))].mean()}


def evaluate_frames(frame, plan):
    """No fit is even instantiated until every fold passes the readiness gate."""
    readiness = assess_readiness(frame, plan)
    if not readiness["ready"]:
        return readiness, None
    predicted = []
    for fold in readiness["folds"]:
        first, last = fold["first_evaluation_D"], fold["last_evaluation_D"]
        cohort = [d for d in readiness["evaluation_D_dates"] if first <= d <= last]
        train = training_at(frame, first)
        target = frame.loc[frame.signal_date.isin(cohort)]
        prediction = predict_heads(fit_heads(train), target)
        prediction["fold"] = fold["fold"]
        predicted.append(prediction)
        readiness["models_fit"] += 2
        fold["training_keys_sha256"] = hashlib.sha256(train[KEY].to_csv(index=False).encode()).hexdigest()
    predictions = pd.concat(predicted, ignore_index=True)
    all_selected, all_daily = [], []
    for policy in SCORES + ["promotion_top2"]:
        selected, daily = select_fixed_top2(predictions, policy)
        all_selected.append(selected)
        all_daily.append(daily)
    selected, daily = pd.concat(all_selected, ignore_index=True), pd.concat(all_daily, ignore_index=True)
    summary = {policy: {slot: {"complete_dates": len(group), "candidate_slots": group.candidate_slots.sum(), "absent_cash_slots": group.absent_cash_slots.sum(), "proxy_fill_coverage": group.fill.mean(), "base_cost": metric(group.net), "stress_cost": metric(group.stress)} for slot, group in policy_group.groupby("slot")} for policy, policy_group in daily.groupby("policy")}
    report = {"schema_version": "dc20_execution_v2_candidate_evaluation_v1", "status": "RETROSPECTIVE_RESEARCH_ONLY_NO_RELEASE", "results": summary, "training_readiness": readiness, "feature_count": 48, "old_108_outcome_priors_used": False, "direct_slot_multiplied_by_fill": False, "same_training_set_for_direct_and_downside": True, "parameters": PARAMETERS, "python_model_library": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__}, "promotion_comparison": "SAME_FROZEN_D_CANDIDATES_AND_EXECUTION_LABELS", "existing_mixed_comparison": "NOT_ESTABLISHED_NO_BACKCAST_OF_PRODUCTION_WEIGHTS", "release_allowed": False, "model_weights_saved": False, "actual_executable_return_claim": False, "real_portfolio_NAV_claim": False, "independent_forward_validation_required": True, "policy_retuning_after_results": False}
    keep = KEY + ["exec_date", "scheduled_exit_date", "promotion_rank", "label_status", "proxy_fill", "slot_net_return", "slot_net_return_stress", "label_available_date", "actual_exit_date", "blocked_exit_sessions", "direct_prediction", "downside_prediction", "fold"] + SCORES
    return readiness, (report, predictions[keep], selected[keep + ["policy", "slot"]], daily)


def replay_payload(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def verify_label_replay(repo, plan, labels_path, manifest):
    # Rebuild from the pinned market observations, rather than treating a
    # self-reported manifest/hash as proof that every numeric label is true.
    spec = importlib.util.spec_from_file_location("dc20_execution_v2_readonly_label_replay", HERE / "build_labels.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    rows, recomputed = builder.build(repo, plan)
    expect(replay_payload(rows, builder.IDENTITY + builder.VALUE_COLUMNS) == gzip.decompress(labels_path.read_bytes()), "LABEL_REPLAY_BYTES_MISMATCH")
    for key in ["rows", "signal_dates", "terminal_rows", "complete_signal_dates", "status_counts", "market_source_files"]:
        expect(recomputed[key] == manifest.get(key), "LABEL_REPLAY_MANIFEST_MISMATCH:" + key)


def load_inputs(repo, output, plan_path):
    expect(output == HERE / "outputs" and plan_path == HERE / "PLAN.json", "INPUTS_MUST_USE_FIXED_RESEARCH_PATHS")
    plan_path = safe_input(HERE, "PLAN.json")
    plan = json.loads(plan_path.read_text())
    validate_plan(plan)
    label_manifest_path = safe_input(HERE, "outputs/label_manifest.json")
    manifest = json.loads(label_manifest_path.read_text())
    labels_path = safe_input(HERE, "outputs/execution_labels.csv.gz")
    builder_path = safe_input(HERE, "build_labels.py")
    validate_manifest_metadata(manifest, plan, sha(plan_path), sha(builder_path))
    expect(manifest.get("labels_sha256") == sha(labels_path), "EXECUTION_LABELS_SHA_MISMATCH")
    for spec in plan["source_inputs"].values():
        expect(sha(safe_input(repo, spec["path"])) == spec["sha256"], "PINNED_SOURCE_SHA_MISMATCH:" + spec["path"])
    calendar_path = safe_input(repo, plan["source_inputs"]["calendar"]["path"])
    calendar = pd.read_csv(calendar_path, dtype=str)
    opened = calendar.loc[calendar.exchange.eq("SSE") & calendar.is_open.eq("1"), "cal_date"].tolist()
    expect(plan["as_of_date"] in opened, "AS_OF_NOT_STRICT_SSE_SESSION")
    for relative, checksum in manifest["market_source_files"].items():
        match = re.fullmatch(r"data/market/raw/(20\d{2})/(20\d{6})/(?:daily|stk_limit)\.csv", relative)
        expect(match is not None, "UNEXPECTED_MARKET_SOURCE_PATH")
        day = match.group(2)
        expect(match.group(1) == day[:4] and day in opened and day <= plan["as_of_date"], "FUTURE_OR_INVALID_MARKET_SOURCE_DATE")
        expect(sha(safe_input(repo, relative)) == checksum, "MARKET_SOURCE_SHA_MISMATCH:" + relative)
    ledger_path = safe_input(repo, LEDGER_PATH)
    expect(sha(ledger_path) == LEDGER_SHA, "FROZEN_FEATURE_LEDGER_SHA_MISMATCH")
    fm_path = safe_input(repo, "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json")
    fm = json.loads(fm_path.read_text())
    expect(fm["feature_contract"]["columns"] == FEATURES, "D_FEATURE_CONTRACT_CHANGED")
    labels = read_csv(labels_path)
    validate_gross_prices(labels)
    frame = merge_labels(read_csv(ledger_path), labels)
    validate_calendar(frame, opened, plan["as_of_date"])
    verify_label_replay(repo, plan, labels_path, manifest)
    provenance = {"plan_sha256": sha(plan_path), "label_manifest_sha256": sha(label_manifest_path), "labels_sha256": sha(labels_path), "frozen_feature_ledger_sha256": LEDGER_SHA, "frozen_feature_manifest_sha256": sha(fm_path), "training_script_sha256": sha(__file__), "feature_columns_sha256": hashlib.sha256(json.dumps(FEATURES, separators=(",", ":")).encode()).hexdigest()}
    provenance["read_only_market_to_label_replay_verified"] = True
    return plan, frame, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    args = parser.parse_args()
    output, plan_path = HERE / "outputs", HERE / "PLAN.json"
    phase = "load_inputs"
    try:
        output = output_directory()
        existing = [name for name in RESULT_FILES if (output / name).exists() or (output / name).is_symlink()]
        expect(not existing, "EXISTING_TRAINING_RESULTS_REFUSE_OVERWRITE:" + ",".join(existing))
        plan, frame, provenance = load_inputs(find_repo(args.repo), output, plan_path)
        phase = "evaluation"
        with threadpool_limits(limits=2):
            readiness, bundle = evaluate_frames(frame, plan)
        expect(sha(safe_input(HERE, "PLAN.json")) == provenance["plan_sha256"], "PLAN_CHANGED_DURING_EVALUATION")
        readiness["provenance"] = provenance
        if bundle is not None:
            report, predictions, selected, daily = bundle
            report["provenance"] = provenance
            csv_gz(output / "training_candidate_predictions.csv.gz", predictions)
            csv_gz(output / "training_selected_records.csv.gz", selected)
            csv_gz(output / "training_daily_returns.csv.gz", daily)
            write_json(output / "training_comparison.json", report)
            readiness["result_artifacts_valid"] = True
            readiness["valid_output_files"] = RESULT_FILES + ["training_readiness.json"]
        write_json(output / "training_readiness.json", readiness)
        print(json.dumps({"status": readiness["status"], "models_fit": readiness["models_fit"], "release_allowed": False}), flush=True)
        return 0
    except (ValueError, KeyError, OSError, TypeError) as error:
        # An error after the gate might follow a fit. Never falsely report zero
        # fitted models merely because evaluation did not reach its final return.
        failure = {"schema_version": "dc20_execution_v2_training_readiness_v1", "status": "BLOCKED_INPUT_OR_CONTRACT" if phase == "load_inputs" else "ERROR_IN_OFFLINE_EVALUATION", "ready": False, "reasons": [str(error)], "models_fit": 0 if phase == "load_inputs" else None, "model_weights_saved": False, "result_artifacts_valid": False, "valid_output_files": ["training_readiness.json"], "release_allowed": False, "production_changed": False}
        try:
            write_json(output / "training_readiness.json", failure)
        except (ValueError, OSError):
            # A symlink or redirect must not become writable merely because an
            # error report would be convenient. Console-only failure is honest.
            failure["valid_output_files"] = []
            print(json.dumps({"readiness_not_written": "UNSAFE_OR_UNWRITABLE_OUTPUT"}), flush=True)
        print(json.dumps({"status": failure["status"], "reason": str(error), "release_allowed": False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
