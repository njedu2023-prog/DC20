from __future__ import annotations

import hashlib
import csv
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from .canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_execution_projection,
    canonical_frame_fingerprint,
    canonical_float_token,
    canonical_json_bytes,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
)
from .observation import OBSERVATION_TOP_N, rank_observation_rows


LEGACY_FREEZE_SCHEMA_VERSION = "decision_model_freeze_v1"
FREEZE_SCHEMA_VERSION = "decision_model_freeze_v2"
CANONICAL_RUNTIME_SCHEMA_VERSION = "decision_runtime_canonical_contract_v2"
BEHAVIOR_SCHEMA_VERSION = "decision_frozen_behavior_v2"
DEFAULT_FREEZE_PATH = Path("models/decision_model_freeze.json")
DATE_PATTERN = re.compile(r"^20\d{6}$")
CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GITHUB_RUN_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")

# The canonical-V2 Decision baseline predates the independent three-rank
# overlay.  It remains a valid replay target, but it is the *only* complete V2
# freeze allowed to omit ``production.three_rank``.  Every newly signed freeze
# must carry the strict contract below.
LEGACY_PRE_THREE_RANK_FREEZE_ID = (
    "dc20_decision_v13_promotion_oos_d20260815_history20260805"
)
THREE_RANK_FREEZE_SCHEMA_VERSION = "decision_three_rank_production_freeze_v2"
THREE_RANK_VALIDATION_SCHEMA_VERSION = "decision_three_engine_validation_v2"
THREE_RANK_CONTRACT_VERSION = "decision_three_rank_v1"
THREE_RANK_FEATURE_CONTRACT = "D_CLOSE_RUNTIME_ALIGNED_NO_CROSS_HEAD_OUTPUTS_V2"
THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION = "dc20_daily_candidate_d_close_v1"
THREE_RANK_LEDGER_SCHEMA_VERSION = "dc20_three_engine_five_year_ledger_v2"
THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION = (
    "dc20_three_engine_five_year_data_validation_v2"
)
# One already-signed production overlay was activated before the five-year
# ledger acquired the independently audited SSE/context contract.  It is
# accepted only as an exact, immutable bootstrap input while a V2 ledger is
# being generated and signed.  A different freeze id or any change anywhere in
# its three-rank contract must fail closed; this is deliberately not a general
# V1 compatibility mode.
LEGACY_THREE_RANK_LEDGER_SCHEMA_VERSION = (
    "dc20_three_engine_five_year_ledger_v1"
)
LEGACY_THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION = (
    "dc20_three_engine_five_year_data_validation_v1"
)
LEGACY_THREE_RANK_BOOTSTRAP_FREEZE_ID = (
    "dc20_decision_three_rank_v2_partial_d20260814_ea29b6da156162c9"
)
THREE_RANK_V1_TO_V2_BOOTSTRAP_FREEZE_ID = (
    "dc20_decision_three_rank_v2_bootstrap_v1ledger_f3d4e94fd7a5acd0"
)
LEGACY_THREE_RANK_BOOTSTRAP_CONTRACT_SHA256 = (
    "f3d4e94fd7a5acd0be5a56dbcc532eec27c66f2a8184d0c91dc07efcd0005d3d"
)
THREE_RANK_TRAINING_CALENDAR_PATH = "data/market/trade_cal_sse.csv"
THREE_RANK_TRAINING_CALENDAR_SOURCE = "tushare:trade_cal:SSE"
THREE_RANK_TRAINING_EVENT_SEED_PATH = (
    "data/auction_v3/promotion_prior/five_year_event_features.csv.gz"
)
THREE_RANK_DATE_BINDING_RULE = "D/T/T+1 are adjacent strict SSE open sessions"
THREE_RANK_PROMOTION_BAR_CONTEXT_COLUMNS = (
    "five_year_pre_streak_1d_return",
    "five_year_pre_streak_3d_return",
    "five_year_pre_streak_volatility",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "five_year_streak_runup",
    "five_year_price_log",
)
THREE_RANK_STOCK_PRIOR_RULE = (
    "strictly earlier D promotion truth; Beta(2,3); log1p(samples)"
)
THREE_RANK_CONTEXT_MISSINGNESS_POLICY = (
    "preserve_nan_and_model_with_median_plus_missing_indicator"
)
THREE_RANK_REQUIRED_DATA_GATES = frozenset(
    {
        "manifest_schema_v2",
        "owned_event_source_inventory",
        "strict_sse_calendar_contract",
        "strict_sse_d_t_tplus1_adjacency",
        "rebuilt_promotion_context_contract",
        "stock_prior_is_strictly_lagged",
        "promotion_prior_truth_is_strictly_lagged",
    }
)
THREE_RANK_TOP_N = 10
THREE_RANK_RUNTIME_FEATURE_COLUMNS = (
    "returns_1d",
    "high_low_range",
    "candle_body",
    "gap_open",
    "vol",
    "volume_ratio",
    "volatility_5d",
    "volatility_10d",
    "volatility_20d",
    "atr",
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "bid_ask_proxy",
    "spread_proxy",
)
THREE_RANK_CORE_HEADS = ("promotion", "big_loss", "profit")
THREE_RANK_ALL_HEADS = (*THREE_RANK_CORE_HEADS, "p_fill_shadow")
THREE_RANK_CANONICAL_PREIMAGE_COLUMNS = {
    "promotion_rank": "legacy_shadow_promotion_rank",
    "promotion_rank_score": "legacy_shadow_promotion_rank_score",
    "predicted_promotion_probability": (
        "legacy_shadow_predicted_promotion_probability"
    ),
    "predicted_big_loss_probability": (
        "legacy_shadow_predicted_big_loss_probability"
    ),
    "predicted_profit_probability": (
        "legacy_shadow_predicted_profit_probability"
    ),
}
THREE_RANK_RUNTIME_PREIMAGE_MARKERS = frozenset(
    {
        "three_rank_contract_version",
        "three_engine_runtime_status",
        "three_engine_runtime_feature_gate_passed",
        "three_engine_runtime_artifacts_hash_bound",
        "three_engine_runtime_input_pool_complete",
        "three_engine_runtime_failure",
        *THREE_RANK_CANONICAL_PREIMAGE_COLUMNS.values(),
    }
)
THREE_RANK_RELEASE_OVERLAY_COLUMNS = frozenset(
    {
        "feature_snapshot_sha256",
        "top10_selected",
        "promotion_pool_size",
        "top10_members_sha256",
        "promotion_rank",
        "promotion_rank_score",
        "predicted_promotion_probability",
        "promotion_model_status",
        "big_loss_safety_rank",
        "big_loss_rank_score",
        "predicted_big_loss_probability",
        "big_loss_model_status",
        "profit_rank",
        "profit_rank_score",
        "predicted_profit_probability",
        "profit_model_status",
    }
)
THREE_RANK_RELEASE_MODES = ("ALL_CORE_READY", "PROMOTION_READY_PARTIAL")
THREE_RANK_DYNAMIC_ASSET_PATHS = frozenset(
    {
        "data/decision_three_engines/five_year_supervised_ledger.csv.gz",
        "data/decision_three_engines/five_year_ledger_manifest.json",
        "models/decision_three_engine_data_validation.json",
        "models/decision_three_engines/promotion.joblib",
        "models/decision_three_engines/big_loss.joblib",
        "models/decision_three_engines/profit.joblib",
        "models/decision_three_engines/p_fill_shadow.joblib",
        "models/decision_three_engines/validation_latest.json",
        "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
    }
)
THREE_RANK_HISTORY_SOURCE_PIN_PATHS = frozenset(
    {
        "data/market/trade_cal_sse.csv",
        "models/decision_three_rank_history_sources.json",
    }
)
THREE_RANK_TRAINING_SOURCE_PIN_PATHS = frozenset(
    {
        THREE_RANK_TRAINING_CALENDAR_PATH,
        THREE_RANK_TRAINING_EVENT_SEED_PATH,
    }
)
THREE_RANK_BEHAVIOR_PIN_PATHS = frozenset(
    {
        ".github/workflows/train_decision_three_engines.yml",
        "scripts/build_decision_three_rank_history.py",
        "scripts/build_decision_three_rank_snapshot.py",
        "scripts/build_three_engine_five_year_ledger.py",
        "scripts/refreeze_decision_three_rank.py",
        "scripts/train_three_engine_models.py",
        "scripts/validate_three_engine_five_year_ledger.py",
        "src/top10decision/probability_calibration.py",
        "src/top10decision/decision/d_close_features.py",
        "src/top10decision/decision/three_engine_models.py",
        "src/top10decision/decision/three_rank.py",
        "tests/test_auction_v3_three_engine_runtime.py",
        "tests/test_build_decision_three_rank_snapshot.py",
        "tests/test_d_close_features.py",
        "tests/test_decision_three_rank_contract.py",
        "tests/test_decision_three_rank_frontend.py",
        "tests/test_decision_three_rank_history_projection.py",
        "tests/test_three_engine_five_year_ledger.py",
        "tests/test_three_engine_models.py",
        "tests/test_three_engine_training_workflow.py",
        "tests/test_three_rank_freeze.py",
        "tests/test_validate_three_engine_five_year_ledger.py",
    }
)
THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS = frozenset(
    {
        "data/decision_three_engines/recovery/20260821/candidate_pool.csv",
        "data/decision_three_engines/recovery/20260821/daily_bars/000017_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/000710_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/000931_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002038_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002412_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002491_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002903_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/603626_SH.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/603958_SH.csv.gz",
        "data/decision_three_engines/recovery/20260821/manifest.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/big_loss.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/data_validation.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/five_year_ledger_manifest.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/five_year_supervised_ledger.csv.gz",
        "data/decision_three_engines/recovery/20260821/model_snapshot/p_fill_shadow.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/profit.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/promotion.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/three_engine_oof_top10.csv.gz",
        "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json",
        "data/decision_three_engines/recovery/20260821/source_candidates.csv",
        "data/decision_three_engines/recovery/20260821/source_meta.json",
        "data/decision_three_engines/recovery/20260821/stock_priors.csv",
        "outputs/decision/three_rank_top10_20260821.csv",
        "outputs/decision/three_rank_top10_20260821.evidence.json",
        "outputs/decision/three_rank_top10_20260821.json",
    }
)

KNOWN_HISTORY_PATH = "models/decision_v12_frozen_history_20260805.csv.gz"
KNOWN_HISTORY_SHA256 = (
    "77e48be6732a08698a6abf4a0da74cb02b3129c57d14be66fb94679816a5337e"
)
KNOWN_HISTORY_ROWS = 40355
KNOWN_TOP10_ROWS = 4467
KNOWN_TOP10_DATES = 543
KNOWN_OOS_ROWS = 3097
KNOWN_OOS_DATES = 363
KNOWN_NESTED_OOS_SIGNALS = 158
KNOWN_NESTED_OOS_SIGNAL_DATES = 119
KNOWN_NESTED_OOS_FILLED_TRADES = 158
KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES = 25
KNOWN_NESTED_OOS_TRADE_SELECTED = 158
KNOWN_ACTION_SHADOW_ROWS = 2
KNOWN_ACTION_REFERENCE_PATH = "outputs/decision/action_plan_latest.json"
KNOWN_ACTION_REFERENCE_ROWS = 9
KNOWN_ACTION_REFERENCE_SHA256 = (
    "6fb91ba0051aba8a078397df292accdf587b37e847c0eb34b372ab460c5dd9be"
)
KNOWN_REFERENCE_EVIDENCE = {
    "baseline_commit": "c6de497aaab48c40e205aa7fe8401ad6ad9780ad",
    "top10_blob_sha1": "1bbebbbe4a3b94c0a95fd64f4e27b242ea5b0222",
    "trade_selector_oos_blob_sha1": "6afd29e31cf98c434ac6e67183f7005a89663a49",
    "backtest_blob_sha1": "e27511643fc5aa1ee5bdb60f1d3b15b7e90adef4",
    "model_meta_blob_sha1": "9fee4a2bc9904bf703a292b5df3c367c4c39712b",
}
REQUIRED_ACTIVE_PIN_PATHS = frozenset(
    {
        ".github/workflows/backfill_decision_v11_history.yml",
        ".github/workflows/check_tushare_health.yml",
        ".github/workflows/deploy_dc20_pages.yml",
        ".github/workflows/diagnose_decision_fingerprint.yml",
        ".github/workflows/migrate_decision_runtime.yml",
        ".github/workflows/run_auction_v3.yml",
        ".github/workflows/run_decision_daily.yml",
        ".github/workflows/test_decision_core.yml",
        ".github/workflows/verify_decision_observations.yml",
        "decision.html",
        "requirements-dev.lock",
        "requirements.lock",
        "models/decision_executable_profit_forward_settlement_contract.json",
        "models/decision_executable_profit_internal_forward_challenger.json",
        "models/decision_executable_profit_research_projection_contract.json",
        "models/decision_executable_profit_shadow_contract.json",
        "models/decision_source_surface_rotation_20260824.json",
        "models/decision_v12_frozen_history_20260805.csv.gz",
        "scripts/backfill_decision_v11_history.py",
        "scripts/backfill_prediction_window.py",
        "scripts/backfill_topn_targets_validation.py",
        "scripts/build_eret_trainset.py",
        "scripts/build_eret_truth.py",
        "scripts/build_fill_truth.py",
        "scripts/build_market_fs.py",
        "scripts/build_pfill_trainset.py",
        "scripts/check_tushare_health.py",
        "scripts/decision_pages_truth.py",
        "scripts/diagnose_decision_fingerprint.py",
        "scripts/import_legacy_research_context.py",
        "scripts/merge_feedback_to_learning_table.py",
        "scripts/migrate_decision_runtime.py",
        "scripts/mock_jq_feedback.py",
        "scripts/project_decision_executable_profit_research.py",
        "scripts/publish_decision_action.py",
        "scripts/publish_decision_research_context.py",
        "scripts/project_decision_legacy_profit_relative_research.py",
        "scripts/recover_decision_action_gaps.py",
        "scripts/replay_frozen_canonical_v2.py",
        "scripts/resolve_sample_maturity.py",
        "scripts/run_decision_executable_profit_forward_shadow.py",
        "scripts/run_deterministic_numeric.py",
        "scripts/run_auction_v3.py",
        "scripts/run_v2.py",
        "scripts/settle_decision_executable_profit_forward_shadow.py",
        "scripts/sync_from_a_top10.py",
        "scripts/sync_market_raw.py",
        "scripts/sync_pred_source.py",
        "scripts/sync_tushare_daily_close.py",
        "scripts/sync_tushare_minute.py",
        "scripts/train_eret.py",
        "scripts/train_pfill.py",
        "scripts/validate_backfill_artifacts.py",
        "scripts/validate_decision_executable_profit_shadow_contract.py",
        "scripts/validate_decision_model_freeze.py",
        "scripts/validate_io_contract.py",
        "scripts/validate_topn_targets.py",
        "scripts/verify_decision_observations.py",
        "tests/fixtures/decision_model_freeze_v1_46d8.json",
        "tests/test_auction_v3.py",
        "tests/test_canonical_fingerprint.py",
        "tests/test_canonical_runtime_v2.py",
        "tests/test_decision_contract.py",
        "tests/test_dashboard_research_projection.py",
        "tests/test_decision_intraday_costs.py",
        "tests/test_decision_model_freeze.py",
        "tests/test_decision_pfill_calibration.py",
        "tests/test_decision_pages_truth.py",
        "tests/test_decision_research_context.py",
        "tests/test_decision_regime_guardrails.py",
        "tests/test_decision_run_receipt.py",
        "tests/test_decision_trade_selector.py",
        "tests/test_decision_tushare_health.py",
        "tests/test_decision_v8_calibration.py",
        "tests/test_deterministic_numeric_runtime.py",
        "tests/test_eret_safety.py",
        "tests/test_frozen_canonical_v2_replay.py",
        "tests/test_migrate_decision_runtime.py",
        "tests/test_pages_truthfulness_workflow.py",
        "tests/test_pfill_shadow_pages_truth.py",
        "tests/test_promotion_model.py",
        "tests/test_recover_decision_action_gaps.py",
        "tests/test_sync_market_raw.py",
        "tests/test_sync_pred_source.py",
        "tests/test_sync_tushare_minute_fail_closed.py",
        "tests/test_tushare_close_truth.py",
        "tests/test_writer_workflow_hardening.py",
        "src/top10decision/__init__.py",
        "src/top10decision/adapters/__init__.py",
        "src/top10decision/adapters/decisio_adapter.py",
        "src/top10decision/adapters/joinquant/write_latest_signal.py",
        "src/top10decision/auction_v3/__init__.py",
        "src/top10decision/auction_v3/calibration.py",
        "src/top10decision/auction_v3/config.py",
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/auction_v3/promotion_model.py",
        "src/top10decision/auction_v3/reporting.py",
        "src/top10decision/configs.py",
        "src/top10decision/data/__init__.py",
        "src/top10decision/data/tushare_minute.py",
        "src/top10decision/decision/__init__.py",
        "src/top10decision/decision/action_plan.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/contracts.py",
        "src/top10decision/decision/eligibility.py",
        "src/top10decision/decision/executable_profit_research_projection.py",
        "src/top10decision/decision/executable_profit_shadow.py",
        "src/top10decision/decision/executable_profit_shadow_settlement.py",
        "src/top10decision/decision/exit_policy.py",
        "src/top10decision/decision/legacy_profit_relative_research.py",
        "src/top10decision/decision/model_freeze.py",
        "src/top10decision/decision/observation.py",
        "src/top10decision/decision/research_context.py",
        "src/top10decision/decision/run_receipt.py",
        "src/top10decision/decision/trade_selector.py",
        "src/top10decision/decision_p0.py",
        "src/top10decision/engines/eret_engine.py",
        "src/top10decision/engines/pfill_engine.py",
        "src/top10decision/ingest.py",
        "src/top10decision/models/__init__.py",
        "src/top10decision/models/costs.py",
        "src/top10decision/models/fill_model.py",
        "src/top10decision/models/overnight_model.py",
        "src/top10decision/position/allocator.py",
        "src/top10decision/regime/simple_regime.py",
        "src/top10decision/rt_min_contract.py",
        "src/top10decision/reporting/daily_report.py",
        "src/top10decision/risk/guardrails.py",
        "src/top10decision/strategies/base_strategy.py",
        "src/top10decision/strategies/score_router.py",
        "src/top10decision/utils.py",
        "src/top10decision/weights/__init__.py",
        "src/top10decision/weights/engine.py",
        "src/top10decision/writers.py",
        "src/top10decision/writers/__init__.py",
        "src/top10decision/writers/artifacts.py",
        "src/top10decision/writers/filesystem.py",
        "src/top10decision/writers/io_contract.py",
        "src/top10decision/writers/reports.py",
    }
    | THREE_RANK_DYNAMIC_ASSET_PATHS
    | THREE_RANK_HISTORY_SOURCE_PIN_PATHS
    | THREE_RANK_TRAINING_SOURCE_PIN_PATHS
    | THREE_RANK_BEHAVIOR_PIN_PATHS
    | THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS
)

CANONICAL_CONTRACT_KEYS = frozenset(
    {
        "schema",
        "layer",
        "decimals",
        "rounding",
        "execution_mode",
        "raw_execution_preserved",
    }
)
FINGERPRINT_KEYS = frozenset(
    {
        "schema",
        "canonical_version",
        "canonical_contract",
        "provenance_sha256",
        "semantic_sha256",
        "policy_sha256",
        "policy_projection",
        "artifact_sha256",
        "schema_valid",
        "missing_columns",
        "invalid_cell_count",
    }
)

IDENTITY_COLUMNS = ("signal_date", "ts_code")
GATE_DISCRETE_BEHAVIOR_COLUMNS = (
    "gate_policy_ready",
    "gate_stage_focus",
    "gate_exit_probability",
    "gate_fill_probability",
    "gate_big_loss_probability",
    "gate_mean_return_lcb",
    "gate_conservative_ev",
    "gate_selection_score",
    "risk_gate_pass",
)
TOP10_DISCRETE_BEHAVIOR_COLUMNS = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
OOS_DISCRETE_BEHAVIOR_COLUMNS = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "promotion_rank",
    "trade_rank",
    "trade_gate_pass",
    "trade_selected",
    "trade_shadow_selected",
    "trade_model_reason",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    "trade_selector_promoted",
    "trade_selector_globally_promoted",
    "trade_selector_policy_ready",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
TOP10_SCORE_COLUMNS = (
    "predicted_net_return",
    "predicted_return_lcb",
    "predicted_return_ucb",
    "predicted_mean_return_lcb",
    "predicted_mean_return_ucb",
    "predicted_outcome_q10",
    "predicted_outcome_q90",
    "predicted_profit_probability",
    "predicted_big_loss_probability",
    "predicted_continuation_limit_up_probability",
    "predicted_fill_probability",
    "predicted_exit_probability",
    "conservative_ev",
    "selection_score",
    "diagnostic_gap",
    "recommended_max_gap",
    "policy_max_big_loss_probability",
    "policy_min_mean_return_lcb",
    "policy_min_fill_probability",
    "policy_min_exit_probability",
    "policy_min_conservative_ev",
    "policy_min_selection_score",
)
TRADE_SCORE_COLUMNS = (
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_big_loss_probability",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_tail_risk_weight",
    "trade_base_score",
    "trade_score",
)
OOS_SCORE_COLUMNS = (*TOP10_SCORE_COLUMNS, *TRADE_SCORE_COLUMNS)

PREDICTION_FILL_RELATIONSHIP_COLUMNS = (
    "observation_selected",
    "predicted_fill_probability",
    "predicted_public_market_buyable_probability",
    "trade_predicted_fill_probability",
    "trade_predicted_public_market_buyable_probability",
    "actual_order_fill_probability_available",
    "predicted_actual_order_fill_probability",
)

MODEL_PREDICTION_CANONICAL_COLUMNS = (
    "model_canonical_v2_version",
    "model_artifact_v2_sha256",
    "model_canonical_schema",
    "model_canonical_decimals",
    "model_execution_numeric_mode",
    "model_raw_execution_preserved",
)
SELECTOR_PREDICTION_CANONICAL_COLUMNS = (
    "trade_selector_canonical_v2_version",
    "trade_selector_artifact_v2_sha256",
    "trade_selector_canonical_schema",
    "trade_selector_canonical_decimals",
    "trade_selector_execution_numeric_mode",
    "trade_selector_raw_execution_preserved",
)
SELECTOR_PREDICTION_GLOBAL_COLUMNS = tuple(
    column
    for column in SELECTOR_PREDICTION_CANONICAL_COLUMNS
    if column != "trade_selector_artifact_v2_sha256"
)
SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS = (
    "promotion_rank",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_rank",
    "trade_score",
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_big_loss_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_base_score",
    "trade_tail_risk_weight",
)
SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS = (
    "trade_gate_pass",
    "trade_shadow_selected",
    "trade_selected",
    "trade_selector_policy_ready",
)

INTEGER_BEHAVIOR_COLUMNS = frozenset(
    {
        "observation_rank",
        "promotion_rank",
        "trade_rank",
        "shadow_rank",
        "policy_max_positions",
        "observation_risk_tier",
    }
)
BOOLEAN_BEHAVIOR_COLUMNS = frozenset(
    {
        "trade_gate_pass",
        "trade_selected",
        "trade_shadow_selected",
        "shadow_selected",
        "selected",
        "risk_gate_pass",
        "trade_selector_globally_promoted",
        "trade_selector_policy_ready",
        "trade_selector_promoted",
        "stage_focus",
        "observation_selected",
        *GATE_DISCRETE_BEHAVIOR_COLUMNS,
    }
)
TEXT_BEHAVIOR_COLUMNS = frozenset(
    {
        "model_reason",
        "trade_model_reason",
        "observation_risk_label",
        "selection_policy_version",
    }
)

MODEL_POLICY_KEYS = frozenset(
    {"version", "ready", "reason", "max_positions", "thresholds"}
)
MODEL_POLICY_THRESHOLD_KEYS = frozenset(
    {
        "max_big_loss_probability",
        "min_mean_return_lcb",
        "min_fill_probability",
        "min_exit_probability",
        "min_conservative_ev",
        "min_selection_score",
    }
)
SELECTOR_POLICY_KEYS = frozenset(
    {
        "version",
        "ready",
        "reason",
        "max_positions",
        "tail_risk_weight",
        "thresholds",
    }
)
SELECTOR_POLICY_THRESHOLD_KEYS = frozenset(
    {
        "min_trade_score",
        "min_mean_return_lcb",
        "min_fill_probability",
        "max_big_loss_probability",
    }
)


class DecisionModelFreezeError(RuntimeError):
    """Raised when a frozen Decision production contract drifts."""


def _valid_date(value: str) -> bool:
    if not DATE_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _fail(message: str) -> None:
    raise DecisionModelFreezeError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DecisionModelFreezeError(f"JSON artifact missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionModelFreezeError(f"JSON artifact unreadable: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"JSON artifact must be an object: {path}")
    return payload


def _read_json_strict(path: Path, context: str) -> dict[str, Any]:
    """Read one JSON object while rejecting duplicate keys at every depth."""

    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                _fail(f"{context} contains duplicate JSON key {key!r}")
            payload[key] = value
        return payload

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except FileNotFoundError as exc:
        raise DecisionModelFreezeError(f"{context} missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DecisionModelFreezeError(f"{context} unreadable: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"{context} must be a JSON object")
    return payload


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{context} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], context: str
) -> None:
    actual = frozenset(str(key) for key in value)
    if actual != expected:
        _fail(
            f"{context} keys drift: missing={sorted(expected - actual)!r} "
            f"unexpected={sorted(actual - expected)!r}"
        )


def _require_bool(value: Any, context: str) -> bool:
    if type(value) is not bool:
        _fail(f"{context} must be boolean")
    return value


def _require_int(
    value: Any,
    context: str,
    *,
    minimum: int | None = None,
    exact: int | None = None,
) -> int:
    if type(value) is not int:
        _fail(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        _fail(f"{context} must be >= {minimum}")
    if exact is not None and value != exact:
        _fail(f"{context} must equal {exact}")
    return value


def _require_binary_int(value: Any, context: str) -> int:
    number = _require_int(value, context)
    if number not in (0, 1):
        _fail(f"{context} must equal 0 or 1")
    return number


def _require_text(value: Any, context: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context} must be a nonempty string")
    if value != value.strip():
        _fail(f"{context} must not have surrounding whitespace")
    if exact is not None and value != exact:
        _fail(f"{context} must equal {exact!r}")
    return value


def _require_sha256(value: Any, context: str) -> str:
    text = _require_text(value, context)
    if not SHA256_PATTERN.fullmatch(text):
        _fail(f"{context} must be a lowercase 64-hex SHA-256")
    return text


def _require_string_list(
    value: Any,
    context: str,
    *,
    exact: Sequence[str] | None = None,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        _fail(f"{context} must be a list of nonempty strings")
    if not allow_empty and not value:
        _fail(f"{context} must not be empty")
    if len(set(value)) != len(value):
        _fail(f"{context} contains duplicate names")
    if exact is not None and value != list(exact):
        _fail(f"{context} must equal the reviewed exact column list")
    return list(value)


def _safe_repository_path(
    root: Path | str,
    value: Any,
    context: str,
    *,
    suffix: str | None = None,
) -> Path:
    text = _require_text(value, context)
    relative = Path(text)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or "\\" in text
        or "\x00" in text
        or (suffix is not None and not text.endswith(suffix))
    ):
        _fail(f"{context} must be a safe repository-relative path")
    root_path = Path(root).resolve()
    candidate = root_path / relative
    probe = root_path
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            _fail(f"{context} must not traverse a symlink: {text}")
    try:
        candidate.resolve(strict=False).relative_to(root_path)
    except ValueError:
        _fail(f"{context} escapes repository root: {text}")
    return candidate


def frame_columns_sha256(columns: Sequence[str]) -> str:
    payload = json.dumps(
        list(columns), ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_legacy_three_rank_bootstrap(
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool:
    """Authorize only the exact already-signed V1-ledger production overlay."""

    if source.get("schema_version") != LEGACY_THREE_RANK_LEDGER_SCHEMA_VERSION:
        return False
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("legacy three-rank bootstrap requires the outer V2 freeze schema")
    if manifest.get("active") is not True:
        _fail("legacy three-rank bootstrap requires the exact active freeze")
    freeze_id = manifest.get("freeze_id")
    if freeze_id not in {
        LEGACY_THREE_RANK_BOOTSTRAP_FREEZE_ID,
        THREE_RANK_V1_TO_V2_BOOTSTRAP_FREEZE_ID,
    }:
        _fail(
            "V1 three-rank source ledger is allowed only for an exact signed "
            "legacy/bootstrap freeze"
        )
    digest = hashlib.sha256(canonical_json_bytes(contract)).hexdigest()
    if digest != LEGACY_THREE_RANK_BOOTSTRAP_CONTRACT_SHA256:
        _fail("signed legacy three-rank bootstrap contract drifted")
    return True


def _required_active_pins_for_manifest(
    manifest: Mapping[str, Any],
) -> frozenset[str]:
    """Return the active pin inventory, with one exact bootstrap exception."""

    production = manifest.get("production")
    if not isinstance(production, dict):
        return REQUIRED_ACTIVE_PIN_PATHS
    contract = production.get("three_rank")
    if not isinstance(contract, dict):
        return REQUIRED_ACTIVE_PIN_PATHS
    source = contract.get("source_ledger")
    if not isinstance(source, dict):
        return REQUIRED_ACTIVE_PIN_PATHS
    if not _exact_legacy_three_rank_bootstrap(manifest, contract, source):
        return REQUIRED_ACTIVE_PIN_PATHS
    if manifest.get("freeze_id") == THREE_RANK_V1_TO_V2_BOOTSTRAP_FREEZE_ID:
        # The migration freeze is new and therefore must carry the complete
        # current pin inventory, including the independently owned event seed.
        return REQUIRED_ACTIVE_PIN_PATHS
    # The original signed V1-ledger overlay predates this independently owned
    # seed pin.  All other active pins remain mandatory and byte-enforced.
    return frozenset(
        REQUIRED_ACTIVE_PIN_PATHS.difference(
            {THREE_RANK_TRAINING_EVENT_SEED_PATH}
        )
    )


def validate_production_three_rank_contract(
    root: Path | str,
    manifest: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, Any] | None:
    """Validate the independently trained A/B/C production overlay.

    Canonical V2 itself is intentionally unchanged.  The single reviewed
    pre-three-rank freeze may still be replayed byte-for-byte; every later
    complete freeze must carry this exact-key overlay and pin all of its source
    evidence and executable assets.
    """

    root_path = Path(root).resolve()
    production = _require_mapping(manifest.get("production"), "production")
    value = production.get("three_rank")
    if value is None:
        if require_complete and manifest.get("freeze_id") != (
            LEGACY_PRE_THREE_RANK_FREEZE_ID
        ):
            _fail("complete post-baseline V2 freeze requires production.three_rank")
        return None

    context = "production.three_rank"
    contract = _require_mapping(value, context)
    _require_exact_keys(
        contract,
        frozenset(
            {
                "schema_version",
                "contract_version",
                "validation_schema_version",
                "feature_contract",
                "runtime_feature_contract_version",
                "runtime_feature_columns",
                "runtime_feature_columns_sha256",
                "feature_columns_sha256",
                "top_n",
                "eligible_pool",
                "membership_authority",
                "downstream_scope",
                "fail_closed",
                "source_ledger",
                "validation",
                "heads",
                "oof_top10",
                "all_core_heads_promoted",
                "release_mode",
            }
        ),
        context,
    )
    _require_text(
        contract["schema_version"],
        f"{context}.schema_version",
        exact=THREE_RANK_FREEZE_SCHEMA_VERSION,
    )
    _require_text(
        contract["contract_version"],
        f"{context}.contract_version",
        exact=THREE_RANK_CONTRACT_VERSION,
    )
    _require_text(
        contract["validation_schema_version"],
        f"{context}.validation_schema_version",
        exact=THREE_RANK_VALIDATION_SCHEMA_VERSION,
    )
    _require_text(
        contract["feature_contract"],
        f"{context}.feature_contract",
        exact=THREE_RANK_FEATURE_CONTRACT,
    )
    _require_text(
        contract["runtime_feature_contract_version"],
        f"{context}.runtime_feature_contract_version",
        exact=THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION,
    )
    runtime_columns = _require_string_list(
        contract["runtime_feature_columns"],
        f"{context}.runtime_feature_columns",
        exact=THREE_RANK_RUNTIME_FEATURE_COLUMNS,
    )
    runtime_columns_sha = _require_sha256(
        contract["runtime_feature_columns_sha256"],
        f"{context}.runtime_feature_columns_sha256",
    )
    if runtime_columns_sha != frame_columns_sha256(runtime_columns):
        _fail(f"{context}.runtime_feature_columns_sha256 differs from columns")
    _require_sha256(
        contract["feature_columns_sha256"],
        f"{context}.feature_columns_sha256",
    )
    _require_int(contract["top_n"], f"{context}.top_n", exact=THREE_RANK_TOP_N)
    _require_text(
        contract["eligible_pool"],
        f"{context}.eligible_pool",
        exact="hard_stage_2_to_3_and_3_to_4_pool",
    )
    _require_text(
        contract["membership_authority"],
        f"{context}.membership_authority",
        exact="promotion_probability_engine_only",
    )
    _require_text(
        contract["downstream_scope"],
        f"{context}.downstream_scope",
        exact="exact_frozen_promotion_top10",
    )

    fail_closed = _require_mapping(contract["fail_closed"], f"{context}.fail_closed")
    _require_exact_keys(
        fail_closed,
        frozenset(
            {
                "artifact_or_ledger_drift",
                "missing_or_invalid_runtime_feature",
                "promotion_not_ready",
                "unready_secondary_head",
                "shadow_may_change_membership",
                "shadow_may_override_core_ranks",
                "formal_trade_status",
            }
        ),
        f"{context}.fail_closed",
    )
    for key, exact in {
        "artifact_or_ledger_drift": "ZERO_OFFICIAL_CORE_RANKS",
        "missing_or_invalid_runtime_feature": "ZERO_OFFICIAL_CORE_RANKS",
        "promotion_not_ready": "EMPTY_OFFICIAL_TOP10",
        "unready_secondary_head": "NULL_HEAD_FIELDS",
        "formal_trade_status": "NO_TRADE_MODEL_NOT_PROMOTED",
    }.items():
        _require_text(
            fail_closed[key], f"{context}.fail_closed.{key}", exact=exact
        )
    for key in ("shadow_may_change_membership", "shadow_may_override_core_ranks"):
        if _require_bool(fail_closed[key], f"{context}.fail_closed.{key}"):
            _fail(f"{context}.fail_closed.{key} must remain false")

    source = _require_mapping(contract["source_ledger"], f"{context}.source_ledger")
    legacy_v1_bootstrap = _exact_legacy_three_rank_bootstrap(
        manifest,
        contract,
        source,
    )
    legacy_source_keys = frozenset(
        {
            "owner",
            "runtime_dependency_on_top10_decision",
            "schema_version",
            "ledger_path",
            "ledger_sha256",
            "ledger_manifest_path",
            "ledger_manifest_sha256",
            "data_validation_path",
            "data_validation_sha256",
            "data_validation_schema_version",
            "data_validation_status",
            "data_validation_valid",
            "rows",
            "signal_dates",
            "start_signal_date",
            "end_signal_date",
            "prior_truth_cutoff_rule",
            "event_source_inventory_sha256",
            "canonical_prediction_file_count",
        }
    )
    strict_source_keys = legacy_source_keys | frozenset(
        {
            "calendar_path",
            "calendar_sha256",
            "calendar_source",
            "calendar_exchange",
            "strict_calendar",
            "event_seed_path",
            "event_seed_sha256",
            "date_binding_rule",
            "context_source_used",
            "bar_context_rebuild_columns",
            "context_missingness_policy",
            "stock_prior_rule",
        }
    )
    _require_exact_keys(
        source,
        legacy_source_keys if legacy_v1_bootstrap else strict_source_keys,
        f"{context}.source_ledger",
    )
    _require_text(source["owner"], f"{context}.source_ledger.owner", exact="njedu2023-prog/DC20")
    if _require_bool(
        source["runtime_dependency_on_top10_decision"],
        f"{context}.source_ledger.runtime_dependency_on_top10_decision",
    ):
        _fail(f"{context} must not depend on top10-decision at runtime")
    _require_text(
        source["schema_version"],
        f"{context}.source_ledger.schema_version",
        exact=(
            LEGACY_THREE_RANK_LEDGER_SCHEMA_VERSION
            if legacy_v1_bootstrap
            else THREE_RANK_LEDGER_SCHEMA_VERSION
        ),
    )
    exact_source_paths = {
        "ledger_path": "data/decision_three_engines/five_year_supervised_ledger.csv.gz",
        "ledger_manifest_path": "data/decision_three_engines/five_year_ledger_manifest.json",
        "data_validation_path": "models/decision_three_engine_data_validation.json",
    }
    for key, exact in exact_source_paths.items():
        _require_text(source[key], f"{context}.source_ledger.{key}", exact=exact)
    for key in (
        "ledger_sha256",
        "ledger_manifest_sha256",
        "data_validation_sha256",
        "event_source_inventory_sha256",
    ):
        _require_sha256(source[key], f"{context}.source_ledger.{key}")
    _require_text(
        source["data_validation_schema_version"],
        f"{context}.source_ledger.data_validation_schema_version",
        exact=(
            LEGACY_THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION
            if legacy_v1_bootstrap
            else THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION
        ),
    )
    _require_text(
        source["data_validation_status"],
        f"{context}.source_ledger.data_validation_status",
        exact="PASS",
    )
    if not _require_bool(
        source["data_validation_valid"],
        f"{context}.source_ledger.data_validation_valid",
    ):
        _fail(f"{context}.source_ledger.data_validation_valid must be true")
    _require_int(source["rows"], f"{context}.source_ledger.rows", minimum=10_000)
    _require_int(
        source["signal_dates"], f"{context}.source_ledger.signal_dates", minimum=1_100
    )
    for key in ("start_signal_date", "end_signal_date"):
        date = _require_text(source[key], f"{context}.source_ledger.{key}")
        if not _valid_date(date):
            _fail(f"{context}.source_ledger.{key} must be YYYYMMDD")
    if source["start_signal_date"] > source["end_signal_date"]:
        _fail(f"{context}.source_ledger date range is reversed")
    _require_text(
        source["prior_truth_cutoff_rule"],
        f"{context}.source_ledger.prior_truth_cutoff_rule",
        exact="strictly_before_signal_date",
    )
    _require_int(
        source["canonical_prediction_file_count"],
        f"{context}.source_ledger.canonical_prediction_file_count",
        minimum=0,
    )
    if not legacy_v1_bootstrap:
        _require_text(
            source["calendar_path"],
            f"{context}.source_ledger.calendar_path",
            exact=THREE_RANK_TRAINING_CALENDAR_PATH,
        )
        _require_sha256(
            source["calendar_sha256"], f"{context}.source_ledger.calendar_sha256"
        )
        _require_text(
            source["calendar_source"],
            f"{context}.source_ledger.calendar_source",
            exact=THREE_RANK_TRAINING_CALENDAR_SOURCE,
        )
        _require_text(
            source["calendar_exchange"],
            f"{context}.source_ledger.calendar_exchange",
            exact="SSE",
        )
        if not _require_bool(
            source["strict_calendar"], f"{context}.source_ledger.strict_calendar"
        ):
            _fail(f"{context}.source_ledger.strict_calendar must be true")
        _require_text(
            source["event_seed_path"],
            f"{context}.source_ledger.event_seed_path",
            exact=THREE_RANK_TRAINING_EVENT_SEED_PATH,
        )
        _require_sha256(
            source["event_seed_sha256"],
            f"{context}.source_ledger.event_seed_sha256",
        )
        _require_text(
            source["date_binding_rule"],
            f"{context}.source_ledger.date_binding_rule",
            exact=THREE_RANK_DATE_BINDING_RULE,
        )
        if _require_bool(
            source["context_source_used"],
            f"{context}.source_ledger.context_source_used",
        ):
            _fail(f"{context}.source_ledger.context_source_used must remain false")
        if source["bar_context_rebuild_columns"] != list(
            THREE_RANK_PROMOTION_BAR_CONTEXT_COLUMNS
        ):
            _fail(f"{context}.source_ledger.bar_context_rebuild_columns drifted")
        _require_text(
            source["context_missingness_policy"],
            f"{context}.source_ledger.context_missingness_policy",
            exact=THREE_RANK_CONTEXT_MISSINGNESS_POLICY,
        )
        _require_text(
            source["stock_prior_rule"],
            f"{context}.source_ledger.stock_prior_rule",
            exact=THREE_RANK_STOCK_PRIOR_RULE,
        )

    validation = _require_mapping(contract["validation"], f"{context}.validation")
    _require_exact_keys(
        validation,
        frozenset(
            {
                "path",
                "sha256",
                "schema_version",
                "status",
                "ready",
                "generated_at_utc",
            }
        ),
        f"{context}.validation",
    )
    _require_text(
        validation["path"],
        f"{context}.validation.path",
        exact="models/decision_three_engines/validation_latest.json",
    )
    _require_sha256(validation["sha256"], f"{context}.validation.sha256")
    _require_text(
        validation["schema_version"],
        f"{context}.validation.schema_version",
        exact=THREE_RANK_VALIDATION_SCHEMA_VERSION,
    )
    ready = _require_bool(validation["ready"], f"{context}.validation.ready")
    expected_validation_status = "READY" if ready else "NOT_READY_VALIDATION_GATE"
    _require_text(
        validation["status"],
        f"{context}.validation.status",
        exact=expected_validation_status,
    )
    _require_text(
        validation["generated_at_utc"], f"{context}.validation.generated_at_utc"
    )

    heads = _require_mapping(contract["heads"], f"{context}.heads")
    _require_exact_keys(heads, frozenset(THREE_RANK_ALL_HEADS), f"{context}.heads")
    expected_head_paths = {
        head: f"models/decision_three_engines/{head}.joblib"
        for head in THREE_RANK_ALL_HEADS
    }
    promoted: dict[str, bool] = {}
    for head in THREE_RANK_ALL_HEADS:
        head_context = f"{context}.heads.{head}"
        item = _require_mapping(heads[head], head_context)
        head_keys = frozenset(
            {
                "role",
                "status",
                "promoted",
                "model_version",
                "model_as_of_date",
                "artifact_path",
                "artifact_sha256",
            }
        )
        if not legacy_v1_bootstrap:
            head_keys |= frozenset({"production_bundle_present"})
        _require_exact_keys(
            item,
            head_keys,
            head_context,
        )
        role = "core" if head in THREE_RANK_CORE_HEADS else "shadow_only"
        _require_text(item["role"], f"{head_context}.role", exact=role)
        status = _require_text(item["status"], f"{head_context}.status")
        is_promoted = _require_bool(item["promoted"], f"{head_context}.promoted")
        promoted[head] = is_promoted
        if head == "p_fill_shadow":
            if is_promoted:
                _fail(f"{head_context}.promoted must remain false")
            if (
                status != "SHADOW_READY"
                and not status.startswith("SHADOW_NOT_READY")
                and not status.startswith("NOT_READY_")
            ):
                _fail(
                    f"{head_context}.status must be SHADOW_READY, "
                    "SHADOW_NOT_READY*, or NOT_READY_*"
                )
        elif is_promoted != (status == "READY"):
            _fail(f"{head_context} READY and promoted must agree")
        elif status != "READY" and not status.startswith("NOT_READY_"):
            _fail(f"{head_context}.status must be READY or NOT_READY_*")
        bundle_present = (
            True
            if legacy_v1_bootstrap
            else _require_bool(
                item["production_bundle_present"],
                f"{head_context}.production_bundle_present",
            )
        )
        if bundle_present:
            _require_text(item["model_version"], f"{head_context}.model_version")
            as_of = _require_text(
                item["model_as_of_date"],
                f"{head_context}.model_as_of_date",
            )
            if not _valid_date(as_of) or as_of > source["end_signal_date"]:
                _fail(f"{head_context}.model_as_of_date exceeds source truth")
            expected_prefix = f"decision_three_engine_models_v2:{head}:{as_of}:"
            if not item["model_version"].startswith(expected_prefix):
                _fail(f"{head_context}.model_version is not bound to head/as-of")
        else:
            bundle_free_status = (
                status.startswith("NOT_READY_")
                if head in THREE_RANK_CORE_HEADS
                else status.startswith("SHADOW_NOT_READY")
                or status.startswith("NOT_READY_")
            )
            if (
                is_promoted
                or not bundle_free_status
                or item["model_version"] is not None
                or item["model_as_of_date"] is not None
            ):
                _fail(f"{head_context} bundle-free tombstone contract is invalid")
        _require_text(
            item["artifact_path"],
            f"{head_context}.artifact_path",
            exact=expected_head_paths[head],
        )
        _require_sha256(item["artifact_sha256"], f"{head_context}.artifact_sha256")
    if not promoted["promotion"]:
        _fail(f"{context} production freeze requires a READY promotion authority")
    all_core = _require_bool(
        contract["all_core_heads_promoted"],
        f"{context}.all_core_heads_promoted",
    )
    computed_all_core = all(promoted[head] for head in THREE_RANK_CORE_HEADS)
    if all_core != computed_all_core or ready != computed_all_core:
        _fail(f"{context} all-core/READY state is internally inconsistent")
    release_mode = _require_text(
        contract["release_mode"], f"{context}.release_mode"
    )
    expected_release_mode = (
        "ALL_CORE_READY" if computed_all_core else "PROMOTION_READY_PARTIAL"
    )
    if release_mode != expected_release_mode:
        _fail(f"{context}.release_mode is inconsistent with head state")
    if release_mode == "PROMOTION_READY_PARTIAL" and any(
        promoted[head] or not heads[head]["status"].startswith("NOT_READY_")
        for head in ("big_loss", "profit")
    ):
        _fail(f"{context} partial mode requires both B/C heads NOT_READY")

    oof = _require_mapping(contract["oof_top10"], f"{context}.oof_top10")
    _require_exact_keys(
        oof,
        frozenset(
            {
                "path",
                "sha256",
                "dataset_sha256",
                "rows",
                "dates",
                "valid",
            }
        ),
        f"{context}.oof_top10",
    )
    _require_text(
        oof["path"],
        f"{context}.oof_top10.path",
        exact="outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
    )
    _require_sha256(oof["sha256"], f"{context}.oof_top10.sha256")
    _require_sha256(oof["dataset_sha256"], f"{context}.oof_top10.dataset_sha256")
    _require_int(oof["rows"], f"{context}.oof_top10.rows", minimum=1)
    _require_int(oof["dates"], f"{context}.oof_top10.dates", minimum=1)
    if not _require_bool(oof["valid"], f"{context}.oof_top10.valid"):
        _fail(f"{context}.oof_top10.valid must be true")

    pinned = _require_mapping(manifest.get("pinned_files"), "pinned_files")
    contract_hashes = {
        source["ledger_path"]: source["ledger_sha256"],
        source["ledger_manifest_path"]: source["ledger_manifest_sha256"],
        source["data_validation_path"]: source["data_validation_sha256"],
        validation["path"]: validation["sha256"],
        oof["path"]: oof["sha256"],
        **{
            heads[head]["artifact_path"]: heads[head]["artifact_sha256"]
            for head in THREE_RANK_ALL_HEADS
        },
    }
    if set(contract_hashes) != set(THREE_RANK_DYNAMIC_ASSET_PATHS):
        _fail(f"{context} dynamic asset inventory is not exact")
    for path, digest in contract_hashes.items():
        _safe_repository_path(root_path, path, f"{context}.asset[{path!r}]")
        if pinned.get(path) != digest:
            _fail(f"{context} asset hash differs from pinned_files: {path}")
    if not legacy_v1_bootstrap:
        training_source_hashes = {
            source["calendar_path"]: source["calendar_sha256"],
            source["event_seed_path"]: source["event_seed_sha256"],
        }
        if set(training_source_hashes) != set(THREE_RANK_TRAINING_SOURCE_PIN_PATHS):
            _fail(f"{context} training source inventory is not exact")
        for path, digest in training_source_hashes.items():
            _safe_repository_path(
                root_path,
                path,
                f"{context}.training_source[{path!r}]",
            )
            if pinned.get(path) != digest:
                _fail(
                    f"{context} training source hash differs from pinned_files: {path}"
                )
    return dict(contract)


def _validate_canonical_contract(
    value: Any, *, layer: str, context: str
) -> dict[str, Any]:
    contract = _require_mapping(value, context)
    _require_exact_keys(contract, CANONICAL_CONTRACT_KEYS, context)
    _require_text(
        contract["schema"], f"{context}.schema", exact=CANONICAL_FINGERPRINT_SCHEMA
    )
    _require_text(contract["layer"], f"{context}.layer", exact=layer)
    _require_int(contract["decimals"], f"{context}.decimals", exact=8)
    _require_text(
        contract["rounding"],
        f"{context}.rounding",
        exact="decimal_string_half_even",
    )
    _require_text(
        contract["execution_mode"],
        f"{context}.execution_mode",
        exact="raw_float64",
    )
    if not _require_bool(
        contract["raw_execution_preserved"],
        f"{context}.raw_execution_preserved",
    ):
        _fail(f"{context}.raw_execution_preserved must be true")
    return contract


def _validate_policy_projection(
    value: Any,
    *,
    layer: str,
    context: str,
    require_canonical: bool = True,
) -> dict[str, Any]:
    projection = _require_mapping(value, context)
    expected_keys = MODEL_POLICY_KEYS if layer == "model" else SELECTOR_POLICY_KEYS
    threshold_keys = (
        MODEL_POLICY_THRESHOLD_KEYS
        if layer == "model"
        else SELECTOR_POLICY_THRESHOLD_KEYS
    )
    _require_exact_keys(projection, expected_keys, context)
    thresholds = _require_mapping(projection["thresholds"], f"{context}.thresholds")
    _require_exact_keys(thresholds, threshold_keys, f"{context}.thresholds")
    if not isinstance(projection["version"], str) or projection["version"] == "":
        _fail(f"{context}.version must be an exact nonempty string")
    if not isinstance(projection["reason"], str) or projection["reason"] == "":
        _fail(f"{context}.reason must be an exact nonempty string")
    _require_bool(projection["ready"], f"{context}.ready")
    _require_int(
        projection["max_positions"],
        f"{context}.max_positions",
        minimum=0 if layer == "model" else 1,
    )
    numeric_values = list(thresholds.items())
    if layer == "trade_selector":
        numeric_values.append(("tail_risk_weight", projection["tail_risk_weight"]))
    for name, raw in numeric_values:
        if type(raw) is not float or not math.isfinite(raw):
            _fail(f"{context}.{name} must be a finite JSON float")
    if require_canonical:
        canonical = canonical_execution_projection(projection, decimals=8)
        if canonical_json_bytes(canonical) != canonical_json_bytes(projection):
            _fail(f"{context} must be the exact half-even q8 projection")
    return projection


def _live_execution_policy_projection(
    value: Any, *, layer: str, context: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a raw execution policy and derive its canonical q8 envelope.

    Production policies may carry diagnostics beyond the executable contract.
    Those fields remain raw audit data but cannot enter the hard fingerprint.
    """

    source = _require_mapping(value, context)
    threshold_keys = (
        MODEL_POLICY_THRESHOLD_KEYS
        if layer == "model"
        else SELECTOR_POLICY_THRESHOLD_KEYS
    )
    thresholds_source = _require_mapping(
        source.get("thresholds"), f"{context}.thresholds"
    )
    missing = sorted(threshold_keys.difference(thresholds_source))
    if missing:
        _fail(f"{context}.thresholds missing executable keys: {missing!r}")
    raw_projection = {
        "version": source.get("version"),
        "ready": source.get("ready"),
        "reason": source.get("reason"),
        "max_positions": source.get("max_positions"),
        **(
            {"tail_risk_weight": source.get("tail_risk_weight")}
            if layer == "trade_selector"
            else {}
        ),
        "thresholds": {
            name: thresholds_source[name] for name in threshold_keys
        },
    }
    _validate_policy_projection(
        raw_projection,
        layer=layer,
        context=f"{context}.executable_projection",
        require_canonical=False,
    )
    canonical_projection = canonical_execution_projection(
        raw_projection,
        decimals=8,
    )
    _validate_policy_projection(
        canonical_projection,
        layer=layer,
        context=f"{context}.canonical_projection",
    )
    return raw_projection, canonical_projection


def _validate_fingerprint(
    value: Any,
    *,
    layer: str,
    canonical_version: str,
    contract: dict[str, Any],
    artifact_sha256: str,
    context: str,
) -> dict[str, Any]:
    fingerprint = _require_mapping(value, context)
    _require_exact_keys(fingerprint, FINGERPRINT_KEYS, context)
    _require_text(
        fingerprint["schema"],
        f"{context}.schema",
        exact=CANONICAL_FINGERPRINT_SCHEMA,
    )
    _require_text(
        fingerprint["canonical_version"],
        f"{context}.canonical_version",
        exact=canonical_version,
    )
    if fingerprint["canonical_contract"] != contract:
        _fail(f"{context}.canonical_contract differs from its outer contract")
    provenance = _require_sha256(
        fingerprint["provenance_sha256"], f"{context}.provenance_sha256"
    )
    semantic = _require_sha256(
        fingerprint["semantic_sha256"], f"{context}.semantic_sha256"
    )
    policy_sha = _require_sha256(
        fingerprint["policy_sha256"], f"{context}.policy_sha256"
    )
    projection = _validate_policy_projection(
        fingerprint["policy_projection"],
        layer=layer,
        context=f"{context}.policy_projection",
    )
    recomputed_policy = (
        canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=contract["decimals"],
            exact_strings=True,
        )
        if layer == "model"
        else canonical_policy_fingerprint(
            projection, decimals=contract["decimals"]
        )["sha256"]
    )
    if recomputed_policy != policy_sha:
        _fail(f"{context}.policy_sha256 does not match policy_projection")
    actual_artifact = _require_sha256(
        fingerprint["artifact_sha256"], f"{context}.artifact_sha256"
    )
    if actual_artifact != artifact_sha256:
        _fail(f"{context}.artifact_sha256 differs from outer artifact pin")
    artifact_kind = (
        "decision_model_canonical_runtime_v2"
        if layer == "model"
        else "decision_trade_selector_canonical_runtime_v2"
    )
    if compose_artifact_fingerprint(
        artifact_kind=artifact_kind,
        provenance_sha256=provenance,
        semantic_sha256=semantic,
        policy_sha256=policy_sha,
        decimals=contract["decimals"],
    ) != actual_artifact:
        _fail(f"{context}.artifact_sha256 does not match V2 components")
    if not _require_bool(fingerprint["schema_valid"], f"{context}.schema_valid"):
        _fail(f"{context}.schema_valid must be true")
    _require_string_list(
        fingerprint["missing_columns"],
        f"{context}.missing_columns",
        exact=(),
        allow_empty=True,
    )
    _require_int(
        fingerprint["invalid_cell_count"],
        f"{context}.invalid_cell_count",
        exact=0,
    )
    return fingerprint


def _validate_canonical_layer(
    value: Any, *, layer: str, context: str
) -> dict[str, Any]:
    layer_contract = _require_mapping(value, context)
    _require_exact_keys(
        layer_contract,
        frozenset(
            {
                "canonical_v2_version",
                "artifact_v2_sha256",
                "fingerprint_v2",
                "canonical_contract",
            }
        ),
        context,
    )
    version = _require_text(
        layer_contract["canonical_v2_version"], f"{context}.canonical_v2_version"
    )
    artifact = _require_sha256(
        layer_contract["artifact_v2_sha256"], f"{context}.artifact_v2_sha256"
    )
    contract = _validate_canonical_contract(
        layer_contract["canonical_contract"],
        layer=layer,
        context=f"{context}.canonical_contract",
    )
    _validate_fingerprint(
        layer_contract["fingerprint_v2"],
        layer=layer,
        canonical_version=version,
        contract=contract,
        artifact_sha256=artifact,
        context=f"{context}.fingerprint_v2",
    )
    return layer_contract


def _validate_history_manifest(
    root: Path, snapshot_value: Any, *, active: bool
) -> dict[str, Any]:
    snapshot = _require_mapping(snapshot_value, "history_snapshot")
    path = _safe_repository_path(
        root, snapshot.get("path"), "history_snapshot.path", suffix=".csv.gz"
    )
    sha = _require_sha256(snapshot.get("sha256"), "history_snapshot.sha256")
    rows = _require_int(snapshot.get("rows"), "history_snapshot.rows", minimum=1)
    if _require_bool(
        snapshot.get("bootstrap_mode"), "history_snapshot.bootstrap_mode"
    ):
        _fail("history_snapshot.bootstrap_mode must be false in schema V2")
    schema = _require_mapping(snapshot.get("schema"), "history_snapshot.schema")
    _require_exact_keys(
        schema,
        frozenset({"required_columns", "columns_sha256"}),
        "history_snapshot.schema",
    )
    required_columns = _require_string_list(
        schema["required_columns"], "history_snapshot.schema.required_columns"
    )
    for required in IDENTITY_COLUMNS:
        if required not in required_columns:
            _fail(f"history_snapshot requires column {required!r}")
    _require_sha256(
        schema["columns_sha256"], "history_snapshot.schema.columns_sha256"
    )
    if active and (
        path.relative_to(root).as_posix() != KNOWN_HISTORY_PATH
        or sha != KNOWN_HISTORY_SHA256
        or rows != KNOWN_HISTORY_ROWS
    ):
        _fail("active V2 freeze must pin the reviewed 40,355-row SHA77e snapshot")
    return snapshot


def _validate_behavior_dataset_contract(
    root: Path,
    value: Any,
    *,
    name: str,
    discrete_columns: Sequence[str],
    score_columns: Sequence[str],
    expected_decimals: int,
) -> dict[str, Any]:
    context = f"behavior_contract.{name}"
    contract = _require_mapping(value, context)
    _require_exact_keys(
        contract,
        frozenset(
            {
                "path",
                "rows",
                "signal_dates",
                "score_decimals",
                "identity_columns",
                "discrete_columns",
                "score_columns",
                "identity_sha256",
                "date_counts_sha256",
                "discrete_sha256",
                "scores_sha256",
            }
        ),
        context,
    )
    _safe_repository_path(root, contract["path"], f"{context}.path", suffix=".csv")
    _require_int(contract["rows"], f"{context}.rows", minimum=1)
    _require_int(contract["signal_dates"], f"{context}.signal_dates", minimum=1)
    _require_int(
        contract["score_decimals"],
        f"{context}.score_decimals",
        exact=expected_decimals,
    )
    _require_string_list(
        contract["identity_columns"],
        f"{context}.identity_columns",
        exact=IDENTITY_COLUMNS,
    )
    _require_string_list(
        contract["discrete_columns"],
        f"{context}.discrete_columns",
        exact=discrete_columns,
    )
    _require_string_list(
        contract["score_columns"],
        f"{context}.score_columns",
        exact=score_columns,
    )
    for key in (
        "identity_sha256",
        "date_counts_sha256",
        "discrete_sha256",
        "scores_sha256",
    ):
        _require_sha256(contract[key], f"{context}.{key}")
    return contract


ACTION_WATCHLIST_COLUMNS = (
    "ts_code",
    "action",
    "stage_watch_rank",
    "watch_label",
    "target_weight",
)


def _validate_action_watchlist_contract(root: Path, value: Any) -> dict[str, Any]:
    context = "behavior_contract.action_watchlist"
    contract = _require_mapping(value, context)
    _require_exact_keys(
        contract,
        frozenset(
            {
                "path",
                "rows",
                "columns",
                "sha256",
                "unique_codes",
                "shadow_only_rows",
            }
        ),
        context,
    )
    _safe_repository_path(root, contract["path"], f"{context}.path", suffix=".json")
    _require_int(contract["rows"], f"{context}.rows", minimum=0)
    _require_string_list(
        contract["columns"],
        f"{context}.columns",
        exact=ACTION_WATCHLIST_COLUMNS,
    )
    _require_sha256(contract["sha256"], f"{context}.sha256")
    if not _require_bool(contract["unique_codes"], f"{context}.unique_codes"):
        _fail(f"{context}.unique_codes must be true")
    _require_int(
        contract["shadow_only_rows"],
        f"{context}.shadow_only_rows",
        minimum=0,
    )
    return contract


def _validate_precision_evidence(value: Any) -> None:
    context = "production.canonical_v2.precision_evidence"
    evidence = _require_mapping(value, context)
    _require_exact_keys(
        evidence,
        frozenset(
            {
                "baseline_commit",
                "candidate_commit",
                "github_run_ids",
                "probes",
                "identity_and_discrete_changed",
                "formal_no_trade_preserved",
                "material_mutation_probe_passed",
            }
        ),
        context,
    )
    for key in ("baseline_commit", "candidate_commit"):
        commit = _require_text(evidence[key], f"{context}.{key}")
        if not GIT_SHA_PATTERN.fullmatch(commit):
            _fail(f"{context}.{key} must be a 40-hex commit")
    run_ids = evidence["github_run_ids"]
    if (
        not isinstance(run_ids, list)
        or len(run_ids) != 2
        or any(
            type(run_id) is not str
            or GITHUB_RUN_ID_PATTERN.fullmatch(run_id) is None
            for run_id in run_ids
        )
        or len(set(run_ids)) != 2
    ):
        _fail(
            f"{context}.github_run_ids must be a native list of exactly two "
            "distinct canonical positive ASCII decimal strings"
        )
    if evidence["probes"] != [6, 8, 10, 12]:
        _fail(f"{context}.probes must equal [6, 8, 10, 12]")
    _require_int(
        evidence["identity_and_discrete_changed"],
        f"{context}.identity_and_discrete_changed",
        exact=0,
    )
    for key in ("formal_no_trade_preserved", "material_mutation_probe_passed"):
        if not _require_bool(evidence[key], f"{context}.{key}"):
            _fail(f"{context}.{key} must be true")


def _validate_v2_manifest(
    root: Path,
    payload: dict[str, Any],
    *,
    require_complete: bool = False,
) -> None:
    active = _require_bool(payload.get("active"), "model freeze active")
    complete = active or require_complete
    cutoff = _require_text(
        payload.get("training_cutoff_signal_date"),
        "training_cutoff_signal_date",
    )
    if not _valid_date(cutoff):
        _fail("training_cutoff_signal_date must be YYYYMMDD")
    _require_text(payload.get("freeze_id"), "freeze_id")
    _validate_history_manifest(root, payload.get("history_snapshot"), active=complete)

    production = _require_mapping(payload.get("production"), "production")
    _require_text(production.get("model_version"), "production.model_version")
    if _require_bool(production.get("promoted"), "production.promoted"):
        _fail("production.promoted must remain false")
    _require_text(
        production.get("trade_selector_version"),
        "production.trade_selector_version",
    )
    if _require_bool(
        production.get("trade_selector_promoted"),
        "production.trade_selector_promoted",
    ):
        _fail("production.trade_selector_promoted must remain false")
    _require_text(
        production.get("formal_status"),
        "production.formal_status",
        exact="NO_TRADE_MODEL_NOT_PROMOTED",
    )
    _require_int(
        production.get("formal_buy_count"),
        "production.formal_buy_count",
        exact=0,
    )

    legacy = _require_mapping(
        production.get("legacy_v1_audit"), "production.legacy_v1_audit"
    )
    _require_exact_keys(
        legacy,
        frozenset(
            {
                "enforcement",
                "model_artifact_sha256",
                "trade_selector_artifact_sha256",
            }
        ),
        "production.legacy_v1_audit",
    )
    _require_text(
        legacy["enforcement"],
        "production.legacy_v1_audit.enforcement",
        exact="audit_only",
    )
    _require_sha256(
        legacy["model_artifact_sha256"],
        "production.legacy_v1_audit.model_artifact_sha256",
    )
    _require_sha256(
        legacy["trade_selector_artifact_sha256"],
        "production.legacy_v1_audit.trade_selector_artifact_sha256",
    )

    canonical = _require_mapping(
        production.get("canonical_v2"), "production.canonical_v2"
    )
    required_canonical = {
        "schema_version",
        "enforcement",
        "model",
        "trade_selector",
    }
    allowed_canonical = {*required_canonical, "precision_evidence"}
    if complete:
        required_canonical.add("precision_evidence")
    actual_canonical = set(canonical)
    if not required_canonical.issubset(actual_canonical) or not actual_canonical.issubset(
        allowed_canonical
    ):
        _fail(
            "production.canonical_v2 keys drift: "
            f"missing={sorted(required_canonical - actual_canonical)!r} "
            f"unexpected={sorted(actual_canonical - allowed_canonical)!r}"
        )
    _require_text(
        canonical["schema_version"],
        "production.canonical_v2.schema_version",
        exact=CANONICAL_RUNTIME_SCHEMA_VERSION,
    )
    _require_text(
        canonical["enforcement"],
        "production.canonical_v2.enforcement",
        exact="hard",
    )
    model_layer = _validate_canonical_layer(
        canonical["model"], layer="model", context="production.canonical_v2.model"
    )
    selector_layer = _validate_canonical_layer(
        canonical["trade_selector"],
        layer="trade_selector",
        context="production.canonical_v2.trade_selector",
    )
    model_decimals = model_layer["canonical_contract"]["decimals"]
    if model_decimals != selector_layer["canonical_contract"]["decimals"]:
        _fail("model and selector canonical decimals must match")
    if "precision_evidence" in canonical:
        _validate_precision_evidence(canonical["precision_evidence"])

    behavior = _require_mapping(payload.get("behavior_contract"), "behavior_contract")
    _require_exact_keys(
        behavior,
        frozenset(
            {
                "schema_version",
                "canonical_schema",
                "top10",
                "trade_selector_oos",
                "action_watchlist",
                "reference_evidence",
                "nested_oos_research",
                "decision",
            }
        ),
        "behavior_contract",
    )
    reference_evidence = _require_mapping(
        behavior.get("reference_evidence"), "behavior_contract.reference_evidence"
    )
    _require_exact_keys(
        reference_evidence,
        frozenset(KNOWN_REFERENCE_EVIDENCE),
        "behavior_contract.reference_evidence",
    )
    for key, value in reference_evidence.items():
        text = _require_text(value, f"behavior_contract.reference_evidence.{key}")
        if not GIT_SHA_PATTERN.fullmatch(text):
            _fail(f"behavior_contract.reference_evidence.{key} must be 40-hex")
    if reference_evidence != KNOWN_REFERENCE_EVIDENCE:
        _fail("V2 freeze must use the reviewed remote c6 reference blobs")
    nested_oos = _require_mapping(
        behavior.get("nested_oos_research"),
        "behavior_contract.nested_oos_research",
    )
    _require_exact_keys(
        nested_oos,
        frozenset(
            {
                "all_candidates_path",
                "signals",
                "signal_dates",
                "filled_trades",
                "market_buyable_path",
                "market_buyable_filled_trades",
            }
        ),
        "behavior_contract.nested_oos_research",
    )
    _require_text(
        nested_oos["all_candidates_path"],
        "behavior_contract.nested_oos_research.all_candidates_path",
        exact="trade_selector.formal_policy_oos.all_candidates",
    )
    _require_text(
        nested_oos["market_buyable_path"],
        "behavior_contract.nested_oos_research.market_buyable_path",
        exact="trade_selector.formal_policy_oos.market_buyable_only",
    )
    for key in (
        "signals",
        "signal_dates",
        "filled_trades",
        "market_buyable_filled_trades",
    ):
        _require_int(
            nested_oos[key],
            f"behavior_contract.nested_oos_research.{key}",
            minimum=0,
        )
    if complete and (
        nested_oos["signals"] != KNOWN_NESTED_OOS_SIGNALS
        or nested_oos["signal_dates"] != KNOWN_NESTED_OOS_SIGNAL_DATES
        or nested_oos["filled_trades"] != KNOWN_NESTED_OOS_FILLED_TRADES
        or nested_oos["market_buyable_filled_trades"]
        != KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES
    ):
        _fail("complete nested-OOS research metrics must pin 158/119/158 and 25")
    _require_text(
        behavior.get("schema_version"),
        "behavior_contract.schema_version",
        exact=BEHAVIOR_SCHEMA_VERSION,
    )
    _require_text(
        behavior.get("canonical_schema"),
        "behavior_contract.canonical_schema",
        exact=CANONICAL_FINGERPRINT_SCHEMA,
    )
    top10 = _validate_behavior_dataset_contract(
        root,
        behavior.get("top10"),
        name="top10",
        discrete_columns=TOP10_DISCRETE_BEHAVIOR_COLUMNS,
        score_columns=TOP10_SCORE_COLUMNS,
        expected_decimals=model_decimals,
    )
    oos = _validate_behavior_dataset_contract(
        root,
        behavior.get("trade_selector_oos"),
        name="trade_selector_oos",
        discrete_columns=OOS_DISCRETE_BEHAVIOR_COLUMNS,
        score_columns=OOS_SCORE_COLUMNS,
        expected_decimals=model_decimals,
    )
    action_watchlist = _validate_action_watchlist_contract(
        root, behavior.get("action_watchlist")
    )
    if complete and (
        action_watchlist["path"] != KNOWN_ACTION_REFERENCE_PATH
        or action_watchlist["rows"] != KNOWN_ACTION_REFERENCE_ROWS
        or action_watchlist["sha256"] != KNOWN_ACTION_REFERENCE_SHA256
        or action_watchlist["shadow_only_rows"] != KNOWN_ACTION_SHADOW_ROWS
    ):
        _fail("complete activation action reference contract drift detected")
    if complete and (
        top10["rows"] != KNOWN_TOP10_ROWS
        or top10["signal_dates"] != KNOWN_TOP10_DATES
        or oos["rows"] != KNOWN_OOS_ROWS
        or oos["signal_dates"] != KNOWN_OOS_DATES
    ):
        _fail("complete behavior contract must pin 4467/543 and 3097/363")

    decision = _require_mapping(behavior.get("decision"), "behavior_contract.decision")
    _require_exact_keys(
        decision,
        frozenset(
            {
                "status_code",
                "formal_buy_count",
                "top10_selected_count",
                "selector_globally_promoted_count",
                "nested_oos_trade_selected_count",
                "nested_oos_trade_selector_promoted_count",
                "production_backtest_signals",
                "production_backtest_signal_dates",
                "production_backtest_fills",
                "reason_values",
            }
        ),
        "behavior_contract.decision",
    )
    _require_text(
        decision["status_code"],
        "behavior_contract.decision.status_code",
        exact="NO_TRADE_MODEL_NOT_PROMOTED",
    )
    for key in (
        "formal_buy_count",
        "top10_selected_count",
        "selector_globally_promoted_count",
        "production_backtest_signals",
        "production_backtest_signal_dates",
        "production_backtest_fills",
    ):
        _require_int(decision[key], f"behavior_contract.decision.{key}", exact=0)
    _require_int(
        decision["nested_oos_trade_selected_count"],
        "behavior_contract.decision.nested_oos_trade_selected_count",
        minimum=0,
    )
    _require_int(
        decision["nested_oos_trade_selector_promoted_count"],
        "behavior_contract.decision.nested_oos_trade_selector_promoted_count",
        minimum=0,
    )
    if complete and (
        decision["nested_oos_trade_selected_count"]
        != KNOWN_NESTED_OOS_TRADE_SELECTED
        or decision["nested_oos_trade_selector_promoted_count"] != KNOWN_OOS_ROWS
    ):
        _fail(
            "complete nested-OOS research counts must pin "
            "158 selected / 3097 promoted"
        )
    _require_string_list(
        decision["reason_values"],
        "behavior_contract.decision.reason_values",
        exact=("selection_policy_not_ready",),
    )

    pinned = _require_mapping(payload.get("pinned_files"), "pinned_files")
    if complete and not pinned:
        _fail("complete V2 freeze requires nonempty pinned_files")
    if complete:
        required_active_pins = _required_active_pins_for_manifest(payload)
        missing_pins = sorted(required_active_pins.difference(pinned))
        if missing_pins:
            _fail(
                "complete V2 freeze is missing execution-critical pins: "
                f"{missing_pins!r}"
            )
        if payload.get("freeze_id") == THREE_RANK_V1_TO_V2_BOOTSTRAP_FREEZE_ID:
            unexpected_pins = sorted(set(pinned).difference(required_active_pins))
            if unexpected_pins:
                _fail(
                    "V1-to-V2 bootstrap freeze pin inventory must be exact: "
                    f"unexpected={unexpected_pins!r}"
                )
    for relative, expected_sha in pinned.items():
        _safe_repository_path(root, relative, f"pinned_files[{relative!r}]")
        _require_sha256(expected_sha, f"pinned_files[{relative!r}]")
    validate_production_three_rank_contract(
        root,
        payload,
        require_complete=complete,
    )


def _validate_legacy_inactive_manifest(root: Path, payload: dict[str, Any]) -> None:
    active = _require_bool(payload.get("active"), "model freeze active")
    if active:
        _fail("active Decision freeze requires decision_model_freeze_v2")
    snapshot = payload.get("history_snapshot")
    if isinstance(snapshot, dict):
        _safe_repository_path(
            root,
            snapshot.get("path"),
            "history_snapshot.path",
            suffix=".csv.gz",
        )
        _require_bool(snapshot.get("bootstrap_mode"), "history_snapshot.bootstrap_mode")


def load_model_freeze(
    root: Path | str = Path("."), *, required: bool = False
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = root_path / DEFAULT_FREEZE_PATH
    if not path.exists() and not required:
        return {}
    payload = _read_json(path)
    schema = payload.get("schema_version")
    if schema == FREEZE_SCHEMA_VERSION:
        _validate_v2_manifest(root_path, payload)
    elif schema == LEGACY_FREEZE_SCHEMA_VERSION:
        _validate_legacy_inactive_manifest(root_path, payload)
    else:
        _fail(f"unsupported model freeze schema: {schema}")
    return payload


def model_freeze_active(manifest: dict[str, Any]) -> bool:
    return manifest.get("active") is True


def apply_frozen_history_cutoff(
    frame: pd.DataFrame, manifest: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not model_freeze_active(manifest) or frame.empty:
        return frame.copy(), {
            "active": model_freeze_active(manifest),
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "rows_before": int(len(frame)),
            "rows_after": int(len(frame)),
            "rows_removed": 0,
        }
    if "signal_date" not in frame.columns:
        _fail("training history has no signal_date column")
    cutoff = str(manifest.get("training_cutoff_signal_date") or "")
    signal_dates = frame["signal_date"].map(normalize_date)
    valid = signal_dates.map(_valid_date)
    filtered = frame.loc[valid & signal_dates.le(cutoff)].copy().reset_index(drop=True)
    if filtered.empty:
        _fail(f"model freeze removed all training rows at cutoff {cutoff}")
    kept = sorted(filtered["signal_date"].map(normalize_date).unique())
    return filtered, {
        "active": True,
        "freeze_id": str(manifest.get("freeze_id") or ""),
        "training_cutoff_signal_date": cutoff,
        "rows_before": int(len(frame)),
        "rows_after": int(len(filtered)),
        "rows_removed": int(len(frame) - len(filtered)),
        "history_start": kept[0] if kept else "",
        "history_end": kept[-1] if kept else "",
    }


def history_snapshot_bootstrap_mode(manifest: dict[str, Any]) -> bool:
    snapshot = manifest.get("history_snapshot") or {}
    return bool(model_freeze_active(manifest) and snapshot.get("bootstrap_mode"))


def _read_verified_history(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    source: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root_path = Path(root).resolve()
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("verified frozen replay requires decision_model_freeze_v2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=source == "forced_frozen_snapshot",
    )
    snapshot = manifest["history_snapshot"]
    path = _safe_repository_path(
        root_path,
        snapshot["path"],
        "history_snapshot.path",
        suffix=".csv.gz",
    )
    if not path.is_file():
        _fail(f"frozen history snapshot missing: {path}")
    actual_sha = _sha256(path)
    if actual_sha != snapshot["sha256"]:
        _fail(
            "frozen history snapshot drift detected: "
            f"expected={snapshot['sha256']} actual={actual_sha}"
        )
    try:
        frame = pd.read_csv(
            path,
            compression="gzip",
            dtype={
                "signal_date": "string",
                "buy_date": "string",
                "target_exit_date": "string",
                "actual_exit_date": "string",
                "ts_code": "string",
            },
        )
    except (OSError, ValueError) as exc:
        raise DecisionModelFreezeError(
            f"frozen history snapshot unreadable: {path}"
        ) from exc
    if len(frame) != snapshot["rows"]:
        _fail(
            "frozen history snapshot row-count drift: "
            f"expected={snapshot['rows']} actual={len(frame)}"
        )
    schema = snapshot["schema"]
    missing = [name for name in schema["required_columns"] if name not in frame]
    if missing:
        _fail(f"frozen history snapshot missing required columns: {missing!r}")
    actual_columns_sha = frame_columns_sha256(list(frame.columns))
    if actual_columns_sha != schema["columns_sha256"]:
        _fail(
            "frozen history snapshot column-order drift: "
            f"expected={schema['columns_sha256']} actual={actual_columns_sha}"
        )
    dates = frame["signal_date"].astype("string")
    if dates.isna().any() or (~dates.map(lambda value: _valid_date(str(value)))).any():
        _fail("frozen history snapshot contains invalid signal_date values")
    if "ts_code" in schema["required_columns"]:
        codes = frame["ts_code"].astype("string")
        if codes.isna().any() or (
            ~codes.map(lambda value: bool(CODE_PATTERN.fullmatch(str(value))))
        ).any():
            _fail("frozen history snapshot contains noncanonical ts_code values")
    cutoff = manifest["training_cutoff_signal_date"]
    if dates.gt(cutoff).any():
        _fail("frozen history snapshot contains rows beyond its cutoff")
    audit = {
        "active": model_freeze_active(manifest),
        "manifest_active": model_freeze_active(manifest),
        "freeze_id": manifest["freeze_id"],
        "source": source,
        "path": path.relative_to(root_path).as_posix(),
        "sha256": actual_sha,
        "rows": int(len(frame)),
        "columns_sha256": actual_columns_sha,
        "bootstrap_mode": False,
        "training_cutoff_signal_date": cutoff,
        "history_start": min(str(value) for value in dates) if len(dates) else "",
        "history_end": max(str(value) for value in dates) if len(dates) else "",
    }
    return frame, audit


def load_verified_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the manifest-pinned snapshot even while inactive; never use live data."""
    snapshot = manifest.get("history_snapshot") or {}
    if (
        snapshot.get("path") != KNOWN_HISTORY_PATH
        or snapshot.get("sha256") != KNOWN_HISTORY_SHA256
        or snapshot.get("rows") != KNOWN_HISTORY_ROWS
    ):
        _fail(
            "forced frozen replay requires the reviewed 40,355-row SHA77e snapshot"
        )
    return _read_verified_history(root, manifest, source="forced_frozen_snapshot")


def load_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any]
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    if not model_freeze_active(manifest):
        return None, {"active": False, "source": "live_history"}
    frame, audit = _read_verified_history(root, manifest, source="frozen_snapshot")
    return frame, audit


def capture_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any], frame: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    del root, manifest, frame
    _fail("history snapshot capture is permanently disabled in freeze schema V2")


def validate_pinned_files(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    force_enforcement: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    pinned = manifest.get("pinned_files") or {}
    active = model_freeze_active(manifest)
    enforce = active or force_enforcement
    if not enforce:
        return {
            "active": False,
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "pinned_files": int(len(pinned)),
            "validated": True,
            "enforced": False,
            "forced_enforcement": False,
        }
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("pinned-file enforcement requires freeze schema V2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=force_enforcement,
    )
    missing_pins = sorted(
        _required_active_pins_for_manifest(manifest).difference(pinned)
    )
    if missing_pins:
        _fail(f"enforced V2 freeze is missing execution-critical pins: {missing_pins!r}")
    mismatches: list[dict[str, str]] = []
    for relative_path, expected in sorted(pinned.items()):
        path = _safe_repository_path(
            root_path, relative_path, f"pinned_files[{relative_path!r}]"
        )
        actual = _sha256(path) if path.is_file() else "MISSING"
        if actual != expected:
            mismatches.append(
                {"path": relative_path, "expected": expected, "actual": actual}
            )
    if mismatches:
        detail = "; ".join(
            f"{item['path']} expected={item['expected']} actual={item['actual']}"
            for item in mismatches
        )
        _fail(f"frozen file drift detected: {detail}")
    return {
        "active": active,
        "freeze_id": manifest["freeze_id"],
        "pinned_files": int(len(pinned)),
        "validated": True,
        "enforced": True,
        "forced_enforcement": force_enforcement and not active,
    }


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {
            "na",
            "nan",
            "null",
            "none",
            "<na>",
        }
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _behavior_boolean(value: Any, context: str) -> int:
    if _is_missing(value):
        _fail(f"{context} must not be missing")
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (str, bytes)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = math.nan
    else:
        number = math.nan
    if math.isfinite(number):
        if number in (0.0, 1.0):
            return int(number)
    _fail(f"{context} must be a strict binary value")


def _behavior_integer(value: Any, context: str) -> int:
    if _is_missing(value) or isinstance(value, bool):
        _fail(f"{context} must be a nonmissing integer")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{context} must be an integer")
    if not math.isfinite(number) or not number.is_integer():
        _fail(f"{context} must be an integer")
    return int(number)


def _exact_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or value == "":
        _fail(f"{context} must be an exact nonempty string")
    return value


def _normalized_identity_frame(frame: pd.DataFrame, context: str) -> pd.DataFrame:
    missing = [column for column in IDENTITY_COLUMNS if column not in frame]
    if missing:
        _fail(f"{context} missing identity columns: {missing!r}")
    rows: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for row_number, row in frame.loc[:, IDENTITY_COLUMNS].iterrows():
        if _is_missing(row["signal_date"]) or _is_missing(row["ts_code"]):
            _fail(f"{context} has empty identity at row {row_number}")
        if not isinstance(row["signal_date"], str) or not isinstance(
            row["ts_code"], str
        ):
            _fail(f"{context} identity must be exact strings at row {row_number}")
        signal_date = row["signal_date"]
        ts_code = row["ts_code"]
        if not _valid_date(signal_date) or not CODE_PATTERN.fullmatch(ts_code):
            _fail(f"{context} has invalid identity at row {row_number}")
        identity = (signal_date, ts_code)
        if identity in identities:
            _fail(f"{context} has duplicate identity {identity!r}")
        identities.add(identity)
        rows.append({"signal_date": signal_date, "ts_code": ts_code})
    if not rows:
        _fail(f"{context} must not be empty")
    return pd.DataFrame(rows)


def compute_behavior_fingerprints(
    frame: pd.DataFrame,
    contract: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    identity_columns = tuple(contract["identity_columns"])
    discrete_columns = tuple(contract["discrete_columns"])
    score_columns = tuple(contract["score_columns"])
    decimals = int(contract["score_decimals"])
    required = list(dict.fromkeys((*identity_columns, *discrete_columns, *score_columns)))
    missing = [column for column in required if column not in frame]
    if missing:
        _fail(f"{context} missing exact contract columns: {missing!r}")
    identities = _normalized_identity_frame(frame, context)

    identity_sha = canonical_frame_fingerprint(
        identities,
        identity_columns,
        decimals=decimals,
        kinds={"signal_date": "date", "ts_code": "code"},
    )["sha256"]
    date_counts = Counter(identities["signal_date"].tolist())
    date_counts_sha = canonical_mapping_sha256(
        [
            {"signal_date": date, "rows": int(rows)}
            for date, rows in sorted(date_counts.items())
        ],
        decimals=decimals,
    )

    discrete = identities.copy()
    discrete_kinds: dict[str, str] = {
        "signal_date": "date",
        "ts_code": "code",
    }
    for column in discrete_columns:
        values: list[Any] = []
        for row_number, value in enumerate(frame[column]):
            cell = f"{context}.{column}[{row_number}]"
            if column == "stage":
                values.append(_exact_text(value, cell))
                discrete_kinds[column] = "exact_text"
            elif column in BOOLEAN_BEHAVIOR_COLUMNS:
                values.append(_behavior_boolean(value, cell))
                discrete_kinds[column] = "integer"
            elif column in INTEGER_BEHAVIOR_COLUMNS:
                values.append(_behavior_integer(value, cell))
                discrete_kinds[column] = "integer"
            elif column in TEXT_BEHAVIOR_COLUMNS:
                values.append(_exact_text(value, cell))
                discrete_kinds[column] = "exact_text"
            else:
                _fail(f"{context} has unclassified discrete column {column!r}")
        discrete[column] = values
    discrete_sha = canonical_frame_fingerprint(
        discrete,
        (*identity_columns, *discrete_columns),
        decimals=decimals,
        kinds=discrete_kinds,
    )["sha256"]

    scores = identities.copy()
    score_kinds: dict[str, str] = {
        "signal_date": "date",
        "ts_code": "code",
        **{column: "float" for column in score_columns},
    }
    for column in score_columns:
        normalized: list[float | None] = []
        for row_number, value in enumerate(frame[column]):
            if _is_missing(value):
                normalized.append(None)
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                _fail(f"{context}.{column}[{row_number}] is not numeric")
            if not math.isfinite(number):
                _fail(f"{context}.{column}[{row_number}] must be finite or missing")
            normalized.append(number)
        scores[column] = normalized
    if {
        "risk_gate_pass",
        "diagnostic_gap",
        "recommended_max_gap",
    }.issubset(frame.columns):
        for row_number, row in frame.iterrows():
            risk_gate = _behavior_boolean(
                row["risk_gate_pass"], f"{context}.risk_gate_pass[{row_number}]"
            )
            recommended = row["recommended_max_gap"]
            diagnostic = row["diagnostic_gap"]
            if risk_gate == 1:
                if _is_missing(recommended) or _is_missing(diagnostic):
                    _fail(
                        f"{context}.recommended_max_gap must be present when "
                        f"risk_gate_pass=1 at row {row_number}"
                    )
                try:
                    recommended_number = float(recommended)
                    diagnostic_number = float(diagnostic)
                except (TypeError, ValueError, OverflowError):
                    _fail(f"{context} gap relation is nonnumeric at row {row_number}")
                if (
                    not math.isfinite(recommended_number)
                    or not math.isfinite(diagnostic_number)
                    or recommended_number != diagnostic_number
                ):
                    _fail(
                        f"{context}.recommended_max_gap must equal diagnostic_gap "
                        f"when risk_gate_pass=1 at row {row_number}"
                    )
            elif not _is_missing(recommended):
                _fail(
                    f"{context}.recommended_max_gap must be missing when "
                    f"risk_gate_pass=0 at row {row_number}"
                )
    scores_sha = canonical_frame_fingerprint(
        scores,
        (*identity_columns, *score_columns),
        decimals=decimals,
        kinds=score_kinds,
    )["sha256"]
    return {
        "rows": int(len(frame)),
        "signal_dates": int(len(date_counts)),
        "identity_sha256": identity_sha,
        "date_counts_sha256": date_counts_sha,
        "discrete_sha256": discrete_sha,
        "scores_sha256": scores_sha,
        "score_decimals": decimals,
        "identity_unique_nonempty": True,
    }


def _read_csv(path: Path, context: str) -> pd.DataFrame:
    if not path.is_file():
        _fail(f"{context} missing: {path}")
    try:
        return pd.read_csv(
            path,
            low_memory=False,
            float_precision="round_trip",
            dtype={"signal_date": "string", "ts_code": "string"},
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise DecisionModelFreezeError(f"{context} unreadable: {path}") from exc


def _read_csv_exact_text(path: Path, context: str) -> pd.DataFrame:
    if not path.is_file():
        _fail(f"{context} missing: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                _fail(f"{context} is empty: {path}")
            if not header or len(header) != len(set(header)):
                _fail(f"{context} has an empty or duplicate header")
            rows: list[list[str]] = []
            for line_number, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    _fail(
                        f"{context} row width mismatch at line {line_number}"
                    )
                rows.append(row)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DecisionModelFreezeError(f"{context} unreadable: {path}") from exc
    return pd.DataFrame(rows, columns=header, dtype=object)


def _validate_model_policy_columns(
    frame: pd.DataFrame,
    expected_model: Mapping[str, Any],
    *,
    context: str,
) -> None:
    projection = expected_model["fingerprint_v2"]["policy_projection"]
    decimals = expected_model["canonical_contract"]["decimals"]
    required = {
        "selection_policy_version",
        "gate_policy_ready",
        "policy_max_positions",
        *(
            f"policy_{name}"
            for name in MODEL_POLICY_THRESHOLD_KEYS
        ),
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        _fail(f"{context} missing frozen policy columns: {missing!r}")
    expected_ready = bool(projection["ready"])
    expected_version = str(projection["version"])
    expected_positions = projection["max_positions"]
    for row_number, row in frame.iterrows():
        if _exact_text(
            row["selection_policy_version"],
            f"{context}.selection_policy_version[{row_number}]",
        ) != expected_version:
            _fail(f"{context} selection policy version drift at row {row_number}")
        if bool(
            _behavior_boolean(
                row["gate_policy_ready"],
                f"{context}.gate_policy_ready[{row_number}]",
            )
        ) != expected_ready:
            _fail(f"{context} policy ready drift at row {row_number}")
        if _behavior_integer(
            row["policy_max_positions"],
            f"{context}.policy_max_positions[{row_number}]",
        ) != expected_positions:
            _fail(f"{context} max positions drift at row {row_number}")
        for threshold_name in MODEL_POLICY_THRESHOLD_KEYS:
            column = f"policy_{threshold_name}"
            actual = canonical_float_token(row[column], decimals=decimals)
            expected = canonical_float_token(
                projection["thresholds"][threshold_name], decimals=decimals
            )
            if actual != expected:
                _fail(
                    f"{context} threshold {threshold_name} drift at row {row_number}"
                )


def _validate_model_policy_text_surface(
    frame_text: pd.DataFrame,
    *,
    parsed_rows: int,
    raw_projection: Mapping[str, Any],
    context: str,
) -> dict[str, Any]:
    if len(frame_text) != parsed_rows:
        _fail(f"{context} row count differs from parsed prediction")
    thresholds = _require_mapping(
        raw_projection.get("thresholds"), f"{context}.raw_thresholds"
    )
    columns: list[str] = []
    for name in sorted(MODEL_POLICY_THRESHOLD_KEYS):
        column = f"policy_{name}"
        columns.append(column)
        if list(frame_text.columns).count(column) != 1:
            _fail(f"{context} requires exactly one {column} header")
        expected = Decimal(str(thresholds[name]))
        if not expected.is_finite():
            _fail(f"{context} expected {column} is non-finite")
        for row_number, raw in enumerate(frame_text[column].tolist(), start=2):
            if not isinstance(raw, str) or raw == "":
                _fail(f"{context} has blank {column} at row {row_number}")
            try:
                actual = Decimal(raw)
            except (InvalidOperation, ValueError) as exc:
                raise DecisionModelFreezeError(
                    f"{context} has malformed {column} at row {row_number}"
                ) from exc
            if not actual.is_finite():
                _fail(f"{context} has non-finite {column} at row {row_number}")
            if actual != expected:
                _fail(
                    f"{context} {column} differs from raw execution policy "
                    f"at row {row_number}"
                )
    return {
        "rows": parsed_rows,
        "columns": columns,
        "exact_decimal_match": True,
    }


def _prediction_finite_number(value: Any, context: str) -> float:
    if _is_missing(value) or isinstance(value, bool):
        _fail(f"{context} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{context} must be a finite number")
    if not math.isfinite(number):
        _fail(f"{context} must be a finite number")
    return number


def _prediction_text_decimal(
    frame_text: pd.DataFrame,
    position: int,
    column: str,
    *,
    probability: bool = False,
) -> Decimal:
    if column not in frame_text:
        _fail(f"prediction exact-text contract missing column {column!r}")
    raw = frame_text.iloc[position][column]
    context = f"prediction.{column}[{position}]"
    if not isinstance(raw, str) or raw == "" or raw != raw.strip():
        _fail(f"{context} must be an exact finite decimal")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise DecisionModelFreezeError(
            f"{context} must be an exact finite decimal"
        ) from exc
    if not number.is_finite():
        _fail(f"{context} must be an exact finite decimal")
    if probability and not Decimal(0) <= number <= Decimal(1):
        _fail(f"{context} must be within [0,1]")
    return number


def _canonical_prediction_validation_view(
    prediction: pd.DataFrame,
    prediction_text: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Recover the frozen Auction surface from a proven three-rank overlay.

    The independent three-engine runtime intentionally owns the public
    promotion columns and leaves not-ready downstream heads blank.  The
    canonical Auction V2 validator must still verify its original execution
    surface, which is preserved row-for-row in ``legacy_shadow_*`` columns.
    This adapter is deliberately fail-closed: any partial marker/preimage or
    incomplete three-engine projection is rejected rather than interpreted as
    a legacy prediction.
    """

    parsed_columns = set(prediction.columns)
    text_columns = set(prediction_text.columns)
    overlay_present = bool(
        THREE_RANK_RUNTIME_PREIMAGE_MARKERS
        & (parsed_columns | text_columns)
    )
    if not overlay_present:
        return prediction, prediction_text, {
            "active": False,
            "canonical_preimage_validated": False,
            "source": "native_canonical_prediction",
        }

    required = set(THREE_RANK_RUNTIME_PREIMAGE_MARKERS).union(
        THREE_RANK_RELEASE_OVERLAY_COLUMNS
    )
    missing_parsed = sorted(required.difference(parsed_columns))
    missing_text = sorted(required.difference(text_columns))
    if missing_parsed or missing_text:
        _fail(
            "three-rank canonical preimage is incomplete: "
            f"parsed_missing={missing_parsed!r}, text_missing={missing_text!r}"
        )
    if prediction.empty or len(prediction) != len(prediction_text):
        _fail("three-rank canonical preimage row binding is invalid")

    # Import lazily so the canonical freeze module remains the lower-level
    # authority during normal imports.  The projection validator checks the
    # complete D pool, hash-bound runtime evidence and exact TopN membership.
    from .three_rank import _three_engine_projection_is_complete

    signal_dates = prediction_text["signal_date"].tolist()
    if (
        not signal_dates
        or any(
            not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None
            for value in signal_dates
        )
        or len(set(signal_dates)) != 1
        or not _three_engine_projection_is_complete(
            prediction,
            signal_dates[0],
        )
    ):
        _fail("three-rank runtime overlay is incomplete or not hash-bound")

    def exact_uniform_text(column: str) -> str:
        values = prediction_text[column].tolist()
        if (
            not values
            or any(not isinstance(value, str) or value == "" for value in values)
            or len(set(values)) != 1
        ):
            _fail(f"three-rank overlay {column} must be one exact nonempty value")
        return values[0]

    def require_exact_numeric(
        position: int,
        column: str,
        *,
        probability: bool = False,
        positive_integer: bool = False,
    ) -> Decimal:
        exact = _prediction_text_decimal(
            prediction_text,
            position,
            column,
            probability=probability,
        )
        parsed = prediction.iloc[position][column]
        try:
            parsed_number = float(parsed)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecisionModelFreezeError(
                f"prediction.{column}[{position}] must match its exact text"
            ) from exc
        if not math.isfinite(parsed_number) or parsed_number != float(exact):
            _fail(f"prediction.{column}[{position}] must match its exact text")
        if positive_integer and (
            exact != exact.to_integral_value() or exact < 1
        ):
            _fail(f"prediction.{column}[{position}] must be a positive integer")
        return exact

    for binary_column in (
        "three_engine_runtime_feature_gate_passed",
        "three_engine_runtime_artifacts_hash_bound",
        "three_engine_runtime_input_pool_complete",
    ):
        for position in range(len(prediction)):
            if require_exact_numeric(position, binary_column) != 1:
                _fail(f"three-rank overlay {binary_column} must remain one")
    if any(
        value != ""
        for value in prediction_text["three_engine_runtime_failure"].tolist()
    ):
        _fail("three-rank hash-bound overlay must not expose a runtime failure")

    runtime_status = exact_uniform_text("three_engine_runtime_status")
    promotion_status = exact_uniform_text("promotion_model_status")
    big_loss_status = exact_uniform_text("big_loss_model_status")
    profit_status = exact_uniform_text("profit_model_status")
    if promotion_status != "READY":
        _fail("three-rank canonical preimage requires a READY promotion head")

    promotion_ranks: list[int] = []
    for position in range(len(prediction)):
        promotion_ranks.append(
            int(
                require_exact_numeric(
                    position,
                    "promotion_rank",
                    positive_integer=True,
                )
            )
        )
        require_exact_numeric(
            position,
            "promotion_rank_score",
            probability=True,
        )
        require_exact_numeric(
            position,
            "predicted_promotion_probability",
            probability=True,
        )
    if sorted(promotion_ranks) != list(range(1, len(prediction) + 1)):
        _fail("three-rank promotion ranks must cover the complete D pool")

    selected_flags = [
        _behavior_boolean(
            value,
            f"prediction.top10_selected[{position}]",
        )
        for position, value in enumerate(prediction["top10_selected"])
    ]
    selected_count = sum(selected_flags)
    for status, rank_column, score_column, probability_column in (
        (
            big_loss_status,
            "big_loss_safety_rank",
            "big_loss_rank_score",
            "predicted_big_loss_probability",
        ),
        (
            profit_status,
            "profit_rank",
            "profit_rank_score",
            "predicted_profit_probability",
        ),
    ):
        ready = status == "READY"
        head_ranks: list[int] = []
        for position, selected in enumerate(selected_flags):
            expected_value = ready and selected == 1
            for column in (rank_column, score_column, probability_column):
                if expected_value:
                    exact = require_exact_numeric(
                        position,
                        column,
                        probability=column != rank_column,
                        positive_integer=column == rank_column,
                    )
                    if column == rank_column:
                        head_ranks.append(int(exact))
                elif (
                    prediction_text.iloc[position][column] != ""
                    or not _is_missing(prediction.iloc[position][column])
                ):
                    _fail(
                        f"three-rank {status} head exposes an unauthorized "
                        f"value in {column}"
                    )
        if ready and sorted(head_ranks) != list(
            range(1, selected_count + 1)
        ):
            _fail("three-rank READY head ranks must cover its frozen TopN")

    expected_runtime_status = (
        "READY"
        if big_loss_status == profit_status == "READY"
        else "PARTIAL_MODELS_NOT_READY"
    )
    if runtime_status != expected_runtime_status:
        _fail("three-rank aggregate status differs from its head readiness")

    observation_flags = _prediction_observation_flags(prediction)
    for position, selected in enumerate(observation_flags):
        for preimage_column in (
            "legacy_shadow_predicted_big_loss_probability",
            "legacy_shadow_predicted_profit_probability",
        ):
            exact = _prediction_text_decimal(
                prediction_text,
                position,
                preimage_column,
                probability=True,
            )
            parsed = prediction.iloc[position][preimage_column]
            try:
                parsed_number = float(parsed)
            except (TypeError, ValueError, OverflowError) as exc:
                raise DecisionModelFreezeError(
                    f"prediction.{preimage_column}[{position}] must match "
                    "its exact-text canonical preimage"
                ) from exc
            if (
                not math.isfinite(parsed_number)
                or parsed_number != float(exact)
            ):
                _fail(
                    f"prediction.{preimage_column}[{position}] must match "
                    "its exact-text canonical preimage"
                )

        promotion_columns = (
            "legacy_shadow_promotion_rank",
            "legacy_shadow_promotion_rank_score",
            "legacy_shadow_predicted_promotion_probability",
        )
        if selected == 0:
            for preimage_column in promotion_columns:
                exact_value = prediction_text.iloc[position][preimage_column]
                parsed_value = prediction.iloc[position][preimage_column]
                if exact_value != "" or not _is_missing(parsed_value):
                    _fail(
                        f"prediction.{preimage_column}[{position}] must be "
                        "empty outside the frozen observation domain"
                    )
            continue

        promotion_rank = _prediction_text_decimal(
            prediction_text,
            position,
            "legacy_shadow_promotion_rank",
        )
        if promotion_rank != promotion_rank.to_integral_value() or promotion_rank < 1:
            _fail(
                "prediction.legacy_shadow_promotion_rank"
                f"[{position}] must be a positive exact integer"
            )
        for preimage_column in (
            "legacy_shadow_promotion_rank_score",
            "legacy_shadow_predicted_promotion_probability",
        ):
            exact = _prediction_text_decimal(
                prediction_text,
                position,
                preimage_column,
                probability=True,
            )
            parsed = prediction.iloc[position][preimage_column]
            try:
                parsed_number = float(parsed)
            except (TypeError, ValueError, OverflowError) as exc:
                raise DecisionModelFreezeError(
                    f"prediction.{preimage_column}[{position}] must match "
                    "its exact-text canonical preimage"
                ) from exc
            if (
                not math.isfinite(parsed_number)
                or parsed_number != float(exact)
            ):
                _fail(
                    f"prediction.{preimage_column}[{position}] must match "
                    "its exact-text canonical preimage"
                )
        parsed_rank = prediction.iloc[position][
            "legacy_shadow_promotion_rank"
        ]
        try:
            parsed_rank_number = float(parsed_rank)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecisionModelFreezeError(
                "prediction.legacy_shadow_promotion_rank"
                f"[{position}] must match its exact-text canonical preimage"
            ) from exc
        if (
            not math.isfinite(parsed_rank_number)
            or parsed_rank_number != float(promotion_rank)
        ):
            _fail(
                "prediction.legacy_shadow_promotion_rank"
                f"[{position}] must match its exact-text canonical preimage"
            )

    canonical = prediction.copy(deep=True)
    canonical_text = prediction_text.copy(deep=True)
    for canonical_column, preimage_column in (
        THREE_RANK_CANONICAL_PREIMAGE_COLUMNS.items()
    ):
        canonical[canonical_column] = prediction[preimage_column].copy()
        canonical_text[canonical_column] = prediction_text[
            preimage_column
        ].copy()

    return canonical, canonical_text, {
        "active": True,
        "canonical_preimage_validated": True,
        "source": "row_bound_legacy_shadow_preimage",
        "rows": len(canonical),
        "signal_date": signal_dates[0],
        "restored_columns": sorted(
            THREE_RANK_CANONICAL_PREIMAGE_COLUMNS
        ),
    }


def _validate_prediction_policy_gates(
    prediction: pd.DataFrame,
    prediction_text: pd.DataFrame,
    *,
    model_raw_projection: Mapping[str, Any],
    selector_raw_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute current prediction decisions in the raw execution domain."""

    if prediction.empty:
        _fail("prediction policy gates require nonempty rows")
    if len(prediction_text) != len(prediction):
        _fail("prediction exact-text rows differ from parsed prediction")
    model_thresholds = model_raw_projection["thresholds"]
    model_required = {
        "signal_date",
        "ts_code",
        "stage",
        "stage_transition",
        "stage_focus",
        "gate_policy_ready",
        "gate_stage_focus",
        "gate_exit_probability",
        "gate_fill_probability",
        "gate_big_loss_probability",
        "gate_mean_return_lcb",
        "gate_conservative_ev",
        "gate_selection_score",
        "risk_gate_pass",
        "predicted_exit_probability",
        "predicted_fill_probability",
        "predicted_big_loss_probability",
        "predicted_mean_return_lcb",
        "predicted_return_lcb",
        "conservative_ev",
        "selection_score",
        "source_rank",
        "shadow_rank",
        "shadow_selected",
        "first_layer_shadow_selected",
        "first_layer_selected",
        "selected",
        "trade_selected",
        "trade_shadow_selected",
        "trade_selector_promoted",
        "model_reason",
        "model_promoted",
        "action",
        "recommended_max_price",
        "max_auction_change_pct",
        "estimated_up_limit",
        "d_close",
        "guidance_only",
        "broker_connected",
        "market_order_allowed",
        "order_type",
    }
    missing = sorted(model_required.difference(prediction.columns))
    if missing:
        _fail(f"prediction raw model gate contract missing columns: {missing!r}")
    model_risk_rows = 0
    model_threshold_decimals = {
        name: Decimal(str(value)) for name, value in model_thresholds.items()
    }
    model_rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(prediction.iterrows()):
        actual_stage_focus = _behavior_boolean(
            row["stage_focus"], f"prediction.stage_focus[{position}]"
        )
        stage = _exact_text(
            prediction_text.iloc[position]["stage"],
            f"prediction.stage[{position}]",
        )
        stage_match = re.fullmatch(r"(0|[1-9]\d*)\u2192([1-9]\d*)", stage)
        if stage_match is None:
            _fail("prediction stage must be an exact N\u2192N+1 transition")
        stage_number = int(stage_match.group(1))
        if int(stage_match.group(2)) != stage_number + 1:
            _fail("prediction stage must be an exact N\u2192N+1 transition")
        stage_transition = _exact_text(
            prediction_text.iloc[position]["stage_transition"],
            f"prediction.stage_transition[{position}]",
        )
        if stage_transition != stage:
            _fail("prediction stage_transition disagrees with stage")
        stage_focus = int(stage_number in (2, 3))
        if actual_stage_focus != stage_focus:
            _fail("prediction stage_focus disagrees with exact stage transition")
        exit_probability = _prediction_text_decimal(
            prediction_text,
            position,
            "predicted_exit_probability",
            probability=True,
        )
        fill_probability = _prediction_text_decimal(
            prediction_text,
            position,
            "predicted_fill_probability",
            probability=True,
        )
        big_loss_probability = _prediction_text_decimal(
            prediction_text,
            position,
            "predicted_big_loss_probability",
            probability=True,
        )
        mean_return_lcb = _prediction_text_decimal(
            prediction_text, position, "predicted_mean_return_lcb"
        )
        conservative_ev = _prediction_text_decimal(
            prediction_text, position, "conservative_ev"
        )
        selection_score = _prediction_text_decimal(
            prediction_text, position, "selection_score"
        )
        predicted_return_lcb = _prediction_text_decimal(
            prediction_text, position, "predicted_return_lcb"
        )
        source_rank = _behavior_integer(
            row["source_rank"], f"prediction.source_rank[{position}]"
        )
        if source_rank < 1:
            _fail("prediction source_rank must be positive")
        expected = {
            "gate_policy_ready": int(model_raw_projection["ready"] is True),
            "gate_stage_focus": stage_focus,
            "gate_exit_probability": int(
                exit_probability
                >= model_threshold_decimals["min_exit_probability"]
            ),
            "gate_fill_probability": int(
                fill_probability
                >= model_threshold_decimals["min_fill_probability"]
            ),
            "gate_big_loss_probability": int(
                big_loss_probability
                <= model_threshold_decimals["max_big_loss_probability"]
            ),
            "gate_mean_return_lcb": int(
                mean_return_lcb
                >= model_threshold_decimals["min_mean_return_lcb"]
            ),
            "gate_conservative_ev": int(
                conservative_ev
                >= model_threshold_decimals["min_conservative_ev"]
            ),
            "gate_selection_score": int(
                selection_score
                >= model_threshold_decimals["min_selection_score"]
            ),
        }
        for column, expected_value in expected.items():
            actual = _behavior_boolean(
                row[column], f"prediction.{column}[{position}]"
            )
            if actual != expected_value:
                _fail(f"prediction {column} disagrees with raw model policy")
        expected_risk = int(all(expected.values()))
        if _behavior_boolean(
            row["risk_gate_pass"], f"prediction.risk_gate_pass[{position}]"
        ) != expected_risk:
            _fail("prediction risk_gate_pass disagrees with raw model policy")
        reason_order = (
            "outside_stage_2_to_3_3_to_4_focus"
            if stage_focus != 1
            else "selection_policy_not_ready"
            if model_raw_projection["ready"] is not True
            else "exit_probability_below_policy_floor"
            if expected["gate_exit_probability"] != 1
            else "fill_probability_below_policy_floor"
            if expected["gate_fill_probability"] != 1
            else "big_loss_probability_exceeds_cap"
            if expected["gate_big_loss_probability"] != 1
            else "mean_return_lcb_below_policy_floor"
            if expected["gate_mean_return_lcb"] != 1
            else "conservative_ev_below_policy_floor"
            if expected["gate_conservative_ev"] != 1
            else "selection_score_below_policy_cutoff"
            if expected["gate_selection_score"] != 1
            else "ok"
            if expected_risk == 1
            else "selection_policy_rejected"
        )
        if _exact_text(
            row["model_reason"], f"prediction.model_reason[{position}]"
        ) != reason_order:
            _fail("prediction model_reason disagrees with raw model policy")
        shadow = _behavior_boolean(
            row["shadow_selected"], f"prediction.shadow_selected[{position}]"
        )
        first_shadow = _behavior_boolean(
            row["first_layer_shadow_selected"],
            f"prediction.first_layer_shadow_selected[{position}]",
        )
        if shadow != first_shadow:
            _fail("prediction first-layer shadow alias drift")
        shadow_rank = row["shadow_rank"]
        actual_shadow_rank: int | None = None
        if stage_focus == 1:
            actual_shadow_rank = _behavior_integer(
                shadow_rank, f"prediction.shadow_rank[{position}]"
            )
            if actual_shadow_rank < 1:
                _fail("prediction first-layer shadow rank must be positive")
        elif not _is_missing(shadow_rank) or shadow != 0:
            _fail("prediction first-layer shadow must be empty outside stage focus")
        first_selected = _behavior_boolean(
            row["first_layer_selected"],
            f"prediction.first_layer_selected[{position}]",
        )
        signal_date = prediction_text.iloc[position].get("signal_date")
        if not isinstance(signal_date, str) or not DATE_PATTERN.fullmatch(signal_date):
            _fail(f"prediction signal_date invalid at row {position}")
        ts_code = prediction_text.iloc[position].get("ts_code")
        if not isinstance(ts_code, str) or not CODE_PATTERN.fullmatch(ts_code):
            _fail(f"prediction ts_code invalid at row {position}")
        if str(row.get("signal_date")) != signal_date or str(row.get("ts_code")) != ts_code:
            _fail(f"prediction parsed/text identity differs at row {position}")
        model_rows.append(
            {
                "position": position,
                "signal_date": signal_date,
                "ts_code": ts_code,
                "selection_score": selection_score,
                "predicted_return_lcb": predicted_return_lcb,
                "source_rank": source_rank,
                "stage_focus": stage_focus,
                "shadow_rank": actual_shadow_rank,
                "shadow_selected": shadow,
                "risk_gate_pass": expected_risk,
                "first_layer_selected": first_selected,
            }
        )
        selected = _behavior_boolean(
            row["selected"], f"prediction.selected[{position}]"
        )
        trade_selected = _behavior_boolean(
            row["trade_selected"], f"prediction.trade_selected[{position}]"
        )
        if selected != trade_selected:
            _fail("prediction selected must equal final trade_selected")
        model_promoted = _behavior_boolean(
            row["model_promoted"], f"prediction.model_promoted[{position}]"
        )
        selector_promoted = _behavior_boolean(
            row["trade_selector_promoted"],
            f"prediction.trade_selector_promoted[{position}]",
        )
        if model_promoted != selector_promoted:
            _fail("prediction promotion flags disagree across model layers")
        trade_shadow_selected = _behavior_boolean(
            row["trade_shadow_selected"],
            f"prediction.trade_shadow_selected[{position}]",
        )
        expected_action = (
            "BUY"
            if selected == 1 and model_promoted == 1
            else "SHADOW_ONLY"
            if selected == 1 or trade_shadow_selected == 1
            else "WATCH"
            if reason_order == "insufficient_independent_history"
            else "REJECT"
        )
        if _exact_text(
            row["action"], f"prediction.action[{position}]"
        ) != expected_action:
            _fail("prediction action disagrees with selected/shadow/promotion state")
        if _behavior_boolean(
            row["guidance_only"], f"prediction.guidance_only[{position}]"
        ) != 1:
            _fail("prediction guidance_only must remain enabled")
        if _behavior_boolean(
            row["broker_connected"], f"prediction.broker_connected[{position}]"
        ) != 0:
            _fail("prediction broker_connected must remain disabled")
        if _behavior_boolean(
            row["market_order_allowed"],
            f"prediction.market_order_allowed[{position}]",
        ) != 0:
            _fail("prediction market_order_allowed must remain disabled")
        if _exact_text(
            row["order_type"], f"prediction.order_type[{position}]"
        ) != "LIMIT_ONLY_MANUAL":
            _fail("prediction order_type must remain LIMIT_ONLY_MANUAL")

        if expected_risk == 1:
            persisted_change_pct = _prediction_text_decimal(
                prediction_text, position, "max_auction_change_pct"
            )
            persisted_execution_gap = persisted_change_pct / Decimal(100)
            d_close = _prediction_text_decimal(
                prediction_text, position, "d_close"
            )
            estimated_up_limit = _prediction_text_decimal(
                prediction_text, position, "estimated_up_limit"
            )
            expected_price = Decimal(
                str(
                    round(
                        max(
                            0.01,
                            min(
                                float(estimated_up_limit) - 0.01,
                                float(d_close)
                                * (1.0 + float(persisted_execution_gap)),
                            ),
                        )
                        + 1e-9,
                        2,
                    )
                )
            )
            if _prediction_text_decimal(
                prediction_text, position, "recommended_max_price"
            ) != expected_price:
                _fail("prediction recommended_max_price disagrees with limit formula")
        else:
            for column in ("recommended_max_price", "max_auction_change_pct"):
                if not _is_missing(row[column]):
                    _fail(
                        f"prediction {column} must be missing when risk gate fails"
                    )
        model_risk_rows += expected_risk
    model_order = sorted(
        model_rows,
        key=lambda item: (
            item["signal_date"],
            -item["selection_score"],
            -item["predicted_return_lcb"],
            item["source_rank"],
            item["position"],
        ),
    )
    shadow_counts: Counter[str] = Counter()
    for item in model_order:
        if item["stage_focus"] != 1:
            continue
        shadow_counts[item["signal_date"]] += 1
        expected_rank = shadow_counts[item["signal_date"]]
        if item["shadow_rank"] != expected_rank:
            _fail("prediction shadow_rank disagrees with raw first-layer ordering")
        if item["shadow_selected"] != int(expected_rank <= 2):
            _fail("prediction shadow_selected disagrees with raw first-layer rank")
    first_layer_selected_positions: set[int] = set()
    first_layer_counts: Counter[str] = Counter()
    for item in model_order:
        if item["risk_gate_pass"] != 1:
            continue
        if first_layer_counts[item["signal_date"]] < int(
            model_raw_projection["max_positions"]
        ):
            first_layer_selected_positions.add(item["position"])
            first_layer_counts[item["signal_date"]] += 1
    for item in model_rows:
        if item["first_layer_selected"] != int(
            item["position"] in first_layer_selected_positions
        ):
            _fail("prediction first-layer selection disagrees with raw risk gate")

    observation_flags = _prediction_observation_flags(prediction)
    selector_required = {
        "signal_date",
        "ts_code",
        "observation_rank",
        "promotion_rank",
        "trade_score",
        "promotion_rank_score",
        "trade_base_score",
        "trade_predicted_outcome_q10",
        "trade_tail_loss_proxy",
        "trade_tail_risk_weight",
        "trade_predicted_mean_return_lcb",
        "trade_predicted_fill_probability",
        "trade_predicted_big_loss_probability",
        "trade_gate_pass",
        "trade_shadow_selected",
        "trade_selected",
        "trade_selector_policy_ready",
        "trade_selector_promoted",
        "trade_model_reason",
    }
    missing = sorted(selector_required.difference(prediction.columns))
    if missing:
        _fail(f"prediction raw selector gate contract missing columns: {missing!r}")
    positions = [index for index, value in enumerate(observation_flags) if value]
    if not positions:
        _fail("prediction selector raw gate domain must not be empty")
    selector_thresholds = selector_raw_projection["thresholds"]
    selector_threshold_decimals = {
        name: Decimal(str(value)) for name, value in selector_thresholds.items()
    }
    raw_tail = Decimal(str(selector_raw_projection["tail_risk_weight"]))
    selector_rows: list[dict[str, Any]] = []
    for position in positions:
        row = prediction.iloc[position]
        signal_date = prediction_text.iloc[position].get("signal_date")
        ts_code = prediction_text.iloc[position].get("ts_code")
        if not isinstance(signal_date, str) or not DATE_PATTERN.fullmatch(signal_date):
            _fail(f"prediction selector signal_date invalid at row {position}")
        if not isinstance(ts_code, str) or not CODE_PATTERN.fullmatch(ts_code):
            _fail(f"prediction selector ts_code invalid at row {position}")
        if str(row.get("signal_date")) != signal_date or str(row.get("ts_code")) != ts_code:
            _fail(f"prediction selector parsed/text identity differs at row {position}")
        promotion_score = _prediction_text_decimal(
            prediction_text, position, "promotion_rank_score", probability=True
        )
        trade_score = _prediction_text_decimal(
            prediction_text, position, "trade_score"
        )
        mean_lcb = _prediction_text_decimal(
            prediction_text, position, "trade_predicted_mean_return_lcb"
        )
        fill_probability = _prediction_text_decimal(
            prediction_text,
            position,
            "trade_predicted_fill_probability",
            probability=True,
        )
        big_loss_probability = _prediction_text_decimal(
            prediction_text,
            position,
            "trade_predicted_big_loss_probability",
            probability=True,
        )
        base_score = _prediction_text_decimal(
            prediction_text, position, "trade_base_score"
        )
        tail_loss = _prediction_text_decimal(
            prediction_text, position, "trade_tail_loss_proxy"
        )
        outcome_q10 = _prediction_text_decimal(
            prediction_text, position, "trade_predicted_outcome_q10"
        )
        tail_weight = _prediction_text_decimal(
            prediction_text, position, "trade_tail_risk_weight"
        )
        if tail_weight != raw_tail:
            _fail("prediction trade_tail_risk_weight differs from raw selector policy")
        recomputed_base_score = Decimal(
            str(float(fill_probability) * float(mean_lcb))
        )
        if base_score != recomputed_base_score:
            _fail("prediction trade_base_score disagrees with raw selector formula")
        recomputed_tail_loss = Decimal(
            str(min(float(outcome_q10), 0.0) * float(big_loss_probability))
        )
        if tail_loss != recomputed_tail_loss:
            _fail(
                "prediction trade_tail_loss_proxy disagrees with raw selector formula"
            )
        # Mirror the raw float64 execution formula, then compare the exact CSV
        # decimal spelling.  Decimal is used for the serialized contract and
        # float only for the deliberately preserved execution arithmetic.
        recomputed_trade_score = Decimal(
            str(
                float(base_score)
                + float(raw_tail) * float(fill_probability) * float(tail_loss)
            )
        )
        if trade_score != recomputed_trade_score:
            _fail("prediction trade_score disagrees with raw selector formula")
        promotion_rank = _behavior_integer(
            row.get("promotion_rank"), f"prediction.promotion_rank[{position}]"
        )
        observation_rank = _behavior_integer(
            row.get("observation_rank"),
            f"prediction.observation_rank[{position}]",
        )
        if promotion_rank < 1 or observation_rank < 1:
            _fail("prediction selector ranks must be positive")
        qualifies = (
            trade_score >= selector_threshold_decimals["min_trade_score"]
            and mean_lcb >= selector_threshold_decimals["min_mean_return_lcb"]
            and fill_probability
            >= selector_threshold_decimals["min_fill_probability"]
            and big_loss_probability
            <= selector_threshold_decimals["max_big_loss_probability"]
        )
        selector_rows.append(
            {
                "position": position,
                "signal_date": signal_date,
                "ts_code": ts_code,
                "trade_score": trade_score,
                "promotion_score": promotion_score,
                "big_loss_probability": big_loss_probability,
                "promotion_rank": promotion_rank,
                "observation_rank": observation_rank,
                "qualifies": qualifies,
            }
        )
        expected_ready = int(selector_raw_projection["ready"] is True)
        if _behavior_boolean(
            row.get("trade_selector_policy_ready"),
            f"prediction.trade_selector_policy_ready[{position}]",
        ) != expected_ready:
            _fail(
                "prediction trade_selector_policy_ready disagrees with raw selector policy"
            )
    promotion_order = sorted(
        selector_rows,
        key=lambda item: (
            item["signal_date"],
            -item["promotion_score"],
            item["big_loss_probability"],
            item["observation_rank"],
            item["ts_code"],
        ),
    )
    promotion_counts: Counter[str] = Counter()
    for item in promotion_order:
        promotion_counts[item["signal_date"]] += 1
        if item["promotion_rank"] != promotion_counts[item["signal_date"]]:
            _fail("prediction promotion_rank disagrees with raw selector ordering")
    trade_order = sorted(
        selector_rows,
        key=lambda item: (
            item["signal_date"],
            -item["trade_score"],
            item["big_loss_probability"],
            item["promotion_rank"],
            item["observation_rank"],
            item["ts_code"],
        ),
    )
    trade_counts: Counter[str] = Counter()
    for item in trade_order:
        trade_counts[item["signal_date"]] += 1
        expected_rank = trade_counts[item["signal_date"]]
        actual_rank = _behavior_integer(
            prediction.iloc[item["position"]].get("trade_rank"),
            f"prediction.trade_rank[{item['position']}]",
        )
        if actual_rank != expected_rank:
            _fail("prediction trade_rank disagrees with raw selector ordering")
        item["trade_rank"] = actual_rank
    qualified = [item for item in trade_order if item["qualifies"]]
    max_positions = int(selector_raw_projection["max_positions"])
    selected_positions: set[int] = set()
    date_counts: Counter[str] = Counter()
    for item in qualified:
        date = item["signal_date"]
        if date_counts[date] < max_positions:
            selected_positions.add(item["position"])
            date_counts[date] += 1
    relative_positions: set[int] = set()
    relative_counts: Counter[str] = Counter()
    for item in trade_order:
        if relative_counts[item["signal_date"]] < 2:
            relative_positions.add(item["position"])
            relative_counts[item["signal_date"]] += 1
    for item in selector_rows:
        expected_gate = int(item["position"] in selected_positions)
        row = prediction.iloc[item["position"]]
        actual_gate = _behavior_boolean(
            row.get("trade_gate_pass"),
            f"prediction.trade_gate_pass[{item['position']}]",
        )
        if actual_gate != expected_gate:
            _fail("prediction trade_gate_pass disagrees with raw selector policy")
        shadow_selected = _behavior_boolean(
            row.get("trade_shadow_selected"),
            f"prediction.trade_shadow_selected[{item['position']}]",
        )
        expected_shadow = int(item["position"] in relative_positions)
        if shadow_selected != expected_shadow:
            _fail(
                "prediction trade_shadow_selected disagrees with relative-best-two"
            )
        policy_ready = selector_raw_projection["ready"] is True
        globally_promoted = bool(
            _behavior_boolean(
                row.get("trade_selector_promoted"),
                f"prediction.trade_selector_promoted[{item['position']}]",
            )
        )
        expected_selected = int(expected_gate and policy_ready and globally_promoted)
        if _behavior_boolean(
            row.get("trade_selected"),
            f"prediction.trade_selected[{item['position']}]",
        ) != expected_selected:
            _fail("prediction trade_selected disagrees with raw trade gate/readiness")
        # Current production applies the relative-best-two shadow layer after
        # the learned-policy gate.  The two sets are intentionally independent;
        # both are recomputed and validated without conflating research shadow
        # routing with formal threshold eligibility.
        expected_reason = "below_learned_policy"
        if expected_gate:
            expected_reason = (
                "learned_policy_pass" if policy_ready else "shadow_policy_only"
            )
        if expected_gate and policy_ready and not globally_promoted:
            expected_reason = "selector_not_promoted"
        if expected_shadow and expected_selected == 0:
            expected_reason = "relative_best_two_only"
        if _exact_text(
            row.get("trade_model_reason"),
            f"prediction.trade_model_reason[{item['position']}]",
        ) != expected_reason:
            _fail("prediction trade_model_reason disagrees with final selector semantics")
    return {
        "rows": len(prediction),
        "model_risk_gate_pass_rows": model_risk_rows,
        "selector_domain_rows": len(selector_rows),
        "selector_trade_gate_pass_rows": len(selected_positions),
        "promotion_rank_exact": True,
        "trade_rank_exact": True,
        "trade_score_formula_exact": True,
        "trade_base_and_tail_formula_exact": True,
        "tail_risk_weight_exact": True,
        "selection_and_reason_exact": True,
        "raw_thresholds_and_gates_exact": True,
        "stage_transition_exact": True,
        "persisted_execution_surface_exact": True,
    }


def _validate_prediction_observation_contract(
    prediction: pd.DataFrame,
) -> dict[str, Any]:
    required = {
        "ts_code",
        "observation_selected",
        "observation_rank",
        "observation_risk_tier",
        "observation_risk_label",
        "observation_pool_size",
    }
    missing = sorted(required.difference(prediction.columns))
    if missing:
        _fail(f"prediction observation contract missing columns: {missing!r}")
    codes: list[str] = []
    for position, value in enumerate(prediction["ts_code"].tolist()):
        if not isinstance(value, str) or not CODE_PATTERN.fullmatch(value):
            _fail(f"prediction observation ts_code invalid at row {position}")
        if value in codes:
            _fail("prediction observation ts_code must be unique")
        codes.append(value)
    ranked_rows, pool_size = rank_observation_rows(
        prediction.to_dict(orient="records"),
        limit=OBSERVATION_TOP_N,
    )
    expected = {str(row.get("ts_code")): row for row in ranked_rows}
    if len(expected) != len(ranked_rows):
        _fail("prediction observation recomputation produced duplicate codes")
    for position, (_, row) in enumerate(prediction.iterrows()):
        code = codes[position]
        selected = _behavior_boolean(
            row["observation_selected"],
            f"prediction.observation_selected[{position}]",
        )
        if _behavior_integer(
            row["observation_pool_size"],
            f"prediction.observation_pool_size[{position}]",
        ) != pool_size:
            _fail("prediction observation_pool_size disagrees with recomputation")
        expected_row = expected.get(code)
        if expected_row is None:
            if selected != 0:
                _fail("prediction observation_selected includes an outside row")
            for column in ("observation_rank", "observation_risk_tier"):
                if not _is_missing(row[column]):
                    _fail(f"prediction {column} must be missing outside observation")
            label = row["observation_risk_label"]
            if not _is_missing(label) and label != "":
                _fail(
                    "prediction observation_risk_label must be missing outside observation"
                )
            continue
        if selected != 1:
            _fail("prediction observation_selected omitted a ranked row")
        for column in ("observation_rank", "observation_risk_tier"):
            if _behavior_integer(
                row[column], f"prediction.{column}[{position}]"
            ) != int(expected_row[column]):
                _fail(f"prediction {column} disagrees with recomputation")
        if _exact_text(
            row["observation_risk_label"],
            f"prediction.observation_risk_label[{position}]",
        ) != expected_row["observation_risk_label"]:
            _fail("prediction observation_risk_label disagrees with recomputation")
    return {
        "rows": int(len(prediction)),
        "pool_size": int(pool_size),
        "selected_rows": int(len(ranked_rows)),
        "rank_risk_and_membership_exact": True,
    }


def compute_action_watchlist_fingerprint(
    action: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    watchlist = action.get("stage_watchlist")
    if not isinstance(watchlist, list):
        _fail("action_plan.stage_watchlist must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    shadow_only_rows = 0
    for row_number, item in enumerate(watchlist):
        if not isinstance(item, dict):
            _fail(f"action_plan.stage_watchlist[{row_number}] must be an object")
        missing = [column for column in contract["columns"] if column not in item]
        if missing:
            _fail(
                f"action_plan.stage_watchlist[{row_number}] missing columns: {missing!r}"
            )
        code = item["ts_code"]
        if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code) or code in seen:
            _fail(f"action watchlist has invalid/duplicate code at row {row_number}")
        seen.add(code)
        target_weight = item["target_weight"]
        if _is_missing(target_weight):
            _fail(f"action watchlist target_weight missing at row {row_number}")
        if type(target_weight) not in (int, float):
            _fail(f"action watchlist target_weight invalid at row {row_number}")
        try:
            target_weight_number = float(target_weight)
        except (TypeError, ValueError, OverflowError):
            _fail(f"action watchlist target_weight invalid at row {row_number}")
        if not math.isfinite(target_weight_number):
            _fail(f"action watchlist target_weight nonfinite at row {row_number}")
        normalized.append(
            {
                "ts_code": code,
                "action": _exact_text(
                    item["action"], f"action watchlist action[{row_number}]"
                ),
                "stage_watch_rank": _require_int(
                    item["stage_watch_rank"],
                    f"action watchlist stage_watch_rank[{row_number}]",
                    minimum=1,
                ),
                "watch_label": _exact_text(
                    item["watch_label"],
                    f"action watchlist watch_label[{row_number}]",
                ),
                "target_weight": target_weight_number,
            }
        )
        if normalized[-1]["action"] not in {"REJECT", "SHADOW_ONLY"}:
            _fail("NO_TRADE action watchlist contains an unauthorized action")
        if "trade_shadow_selected" not in item:
            _fail(
                f"action_plan.stage_watchlist[{row_number}] missing "
                "trade_shadow_selected"
            )
        shadow_selected = _require_binary_int(
            item["trade_shadow_selected"],
            f"action watchlist trade_shadow_selected[{row_number}]",
        )
        expected_action = "SHADOW_ONLY" if shadow_selected == 1 else "REJECT"
        expected_label = "二筛影子" if shadow_selected == 1 else "仅观察"
        if normalized[-1]["action"] != expected_action:
            _fail("action watchlist SHADOW_ONLY must match relative-best-two flag")
        if normalized[-1]["watch_label"] != expected_label:
            _fail("action watchlist label must match relative-best-two flag")
        shadow_only_rows += shadow_selected
    frame = pd.DataFrame(normalized, columns=ACTION_WATCHLIST_COLUMNS)
    digest = canonical_frame_fingerprint(
        frame,
        ACTION_WATCHLIST_COLUMNS,
        decimals=8,
        kinds={
            "ts_code": "code",
            "action": "exact_text",
            "stage_watch_rank": "integer",
            "watch_label": "exact_text",
            "target_weight": "float",
        },
    )["sha256"]
    return {
        "rows": len(normalized),
        "sha256": digest,
        "unique_codes": True,
        "shadow_only_rows": shadow_only_rows,
    }


def _prediction_unique(
    frame: pd.DataFrame,
    column: str,
    *,
    kind: str,
    context: str,
) -> Any:
    if frame.empty:
        _fail(f"{context} must not be empty")
    if column not in frame:
        _fail(f"{context} missing column {column!r}")
    normalized: list[Any] = []
    for row_number, value in enumerate(frame[column]):
        cell = f"{context}.{column}[{row_number}]"
        if kind == "text":
            normalized.append(_exact_text(value, cell))
        elif kind == "int":
            normalized.append(_behavior_integer(value, cell))
        elif kind == "bool":
            normalized.append(bool(_behavior_boolean(value, cell)))
        else:
            _fail(f"unsupported prediction value kind: {kind}")
    unique = set(normalized)
    if len(unique) != 1:
        _fail(f"{context}.{column} has mixed row values")
    return normalized[0]


def _prediction_probability(value: Any, context: str) -> float:
    if _is_missing(value) or isinstance(value, bool):
        _fail(f"{context} must be a finite probability")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{context} must be a finite probability")
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        _fail(f"{context} must be finite and within [0,1]")
    return number


def _prediction_observation_flags(prediction: pd.DataFrame) -> list[int]:
    if "observation_selected" not in prediction:
        _fail("prediction missing column 'observation_selected'")
    return [
        _behavior_boolean(
            value,
            f"prediction.observation_selected[{row_number}]",
        )
        for row_number, value in enumerate(prediction["observation_selected"])
    ]


def _validate_prediction_fill_relationships(
    prediction: pd.DataFrame,
) -> dict[str, Any]:
    missing = [
        column
        for column in PREDICTION_FILL_RELATIONSHIP_COLUMNS
        if column not in prediction
    ]
    if missing:
        _fail(f"prediction missing fill relationship columns: {missing!r}")
    if prediction.empty:
        _fail("prediction must not be empty")
    observation_flags = _prediction_observation_flags(prediction)
    available_rows = 0
    missing_actual_rows = 0
    for position, (row_number, row) in enumerate(prediction.iterrows()):
        fill = _prediction_probability(
            row["predicted_fill_probability"],
            f"prediction.predicted_fill_probability[{row_number}]",
        )
        public_fill = _prediction_probability(
            row["predicted_public_market_buyable_probability"],
            f"prediction.predicted_public_market_buyable_probability[{row_number}]",
        )
        if public_fill != fill:
            _fail(
                "prediction.predicted_public_market_buyable_probability must "
                f"equal predicted_fill_probability at row {row_number}"
            )
        trade_fill_value = row["trade_predicted_fill_probability"]
        trade_public_fill_value = row[
            "trade_predicted_public_market_buyable_probability"
        ]
        if observation_flags[position] == 1:
            trade_fill = _prediction_probability(
                trade_fill_value,
                f"prediction.trade_predicted_fill_probability[{row_number}]",
            )
            trade_public_fill = _prediction_probability(
                trade_public_fill_value,
                "prediction.trade_predicted_public_market_buyable_probability"
                f"[{row_number}]",
            )
            if trade_public_fill != trade_fill:
                _fail(
                    "prediction.trade_predicted_public_market_buyable_probability "
                    f"must equal trade_predicted_fill_probability at row {row_number}"
                )
        elif not _is_missing(trade_fill_value) or not _is_missing(
            trade_public_fill_value
        ):
            _fail(
                "prediction trade fill probabilities must be missing outside "
                f"the observation domain at row {row_number}"
            )
        availability = _behavior_integer(
            row["actual_order_fill_probability_available"],
            f"prediction.actual_order_fill_probability_available[{row_number}]",
        )
        if availability not in (0, 1):
            _fail(
                "prediction.actual_order_fill_probability_available must be binary"
            )
        actual_value = row["predicted_actual_order_fill_probability"]
        actual_missing = _is_missing(actual_value)
        if (availability == 0) != actual_missing:
            _fail(
                "prediction actual fill availability must be 0 iff "
                f"predicted_actual_order_fill_probability is missing at row {row_number}"
            )
        if availability == 1:
            _prediction_probability(
                actual_value,
                f"prediction.predicted_actual_order_fill_probability[{row_number}]",
            )
            available_rows += 1
        else:
            missing_actual_rows += 1
    return {
        "rows": int(len(prediction)),
        "public_fill_equals_fill": True,
        "trade_public_fill_equals_trade_fill": True,
        "trade_fill_observation_domain_rows": int(sum(observation_flags)),
        "trade_fill_outside_domain_rows": int(
            len(observation_flags) - sum(observation_flags)
        ),
        "actual_fill_available_rows": available_rows,
        "actual_fill_missing_rows": missing_actual_rows,
    }


def _runtime_layer_values(
    container: Mapping[str, Any], *, layer: str, context: str
) -> dict[str, Any]:
    if layer == "model":
        values = {
            "canonical_v2_version": container.get("model_canonical_v2_version"),
            "artifact_v2_sha256": container.get("model_artifact_v2_sha256"),
            "fingerprint_v2": container.get("model_fingerprint_v2"),
            "canonical_contract": container.get("model_canonical_contract"),
        }
    else:
        values = {
            "canonical_v2_version": container.get("canonical_v2_version"),
            "artifact_v2_sha256": container.get("production_artifact_v2_sha256"),
            "fingerprint_v2": container.get("production_fingerprint_v2"),
            "canonical_contract": container.get("canonical_contract"),
        }
    _validate_canonical_layer(values, layer=layer, context=context)
    return values


def _action_layer_values(
    action_model: Mapping[str, Any], *, layer: str, expected: Mapping[str, Any]
) -> dict[str, Any]:
    if layer == "model":
        values = {
            "canonical_v2_version": action_model.get("canonical_v2_version"),
            "artifact_v2_sha256": action_model.get("artifact_v2_sha256"),
            "fingerprint_v2": action_model.get("fingerprint_v2"),
            "canonical_contract": action_model.get("canonical_contract"),
        }
        match_fields = (
            "artifact_v2_fingerprints_match",
            "fingerprint_v2_valid",
            "canonical_v2_versions_match",
            "canonical_contracts_match",
            "canonical_decimals_match",
        )
        ready_field = "canonical_policy_ready"
        decimals_field = "canonical_decimals"
        mode_field = "execution_numeric_mode"
        raw_field = "raw_execution_preserved"
    else:
        values = {
            "canonical_v2_version": action_model.get(
                "trade_selector_canonical_v2_version"
            ),
            "artifact_v2_sha256": action_model.get(
                "trade_selector_artifact_v2_sha256"
            ),
            "fingerprint_v2": action_model.get("trade_selector_fingerprint_v2"),
            "canonical_contract": action_model.get(
                "trade_selector_canonical_contract"
            ),
        }
        match_fields = (
            "trade_selector_artifacts_v2_match",
            "trade_selector_fingerprint_v2_valid",
            "trade_selector_canonical_v2_versions_match",
            "trade_selector_canonical_contracts_match",
            "trade_selector_canonical_decimals_match",
        )
        ready_field = "trade_selector_canonical_policy_ready"
        decimals_field = "trade_selector_canonical_decimals"
        mode_field = "trade_selector_execution_numeric_mode"
        raw_field = "trade_selector_raw_execution_preserved"
    _validate_canonical_layer(
        values, layer=layer, context=f"action_plan.model.{layer}_canonical_v2"
    )
    for field in match_fields:
        if not _require_bool(action_model.get(field), f"action_plan.model.{field}"):
            _fail(f"action_plan.model.{field} must be true")
    expected_ready = bool(expected["fingerprint_v2"]["policy_projection"]["ready"])
    if _require_bool(
        action_model.get(ready_field), f"action_plan.model.{ready_field}"
    ) != expected_ready:
        _fail(f"action_plan.model.{ready_field} differs from frozen policy")
    if _require_int(
        action_model.get(decimals_field), f"action_plan.model.{decimals_field}"
    ) != expected["canonical_contract"]["decimals"]:
        _fail(f"action_plan.model.{decimals_field} differs from frozen contract")
    if _require_text(
        action_model.get(mode_field), f"action_plan.model.{mode_field}"
    ) != expected["canonical_contract"]["execution_mode"]:
        _fail(f"action_plan.model.{mode_field} differs from frozen contract")
    if _require_bool(
        action_model.get(raw_field), f"action_plan.model.{raw_field}"
    ) != expected["canonical_contract"]["raw_execution_preserved"]:
        _fail(f"action_plan.model.{raw_field} differs from frozen contract")
    return values


def _prediction_layer_values(
    prediction: pd.DataFrame, *, layer: str, expected: Mapping[str, Any]
) -> dict[str, Any]:
    prefix = "model_" if layer == "model" else "trade_selector_"
    values = {
        "canonical_v2_version": _prediction_unique(
            prediction, f"{prefix}canonical_v2_version", kind="text", context="prediction"
        ),
        "artifact_v2_sha256": _prediction_unique(
            prediction, f"{prefix}artifact_v2_sha256", kind="text", context="prediction"
        ),
        "canonical_schema": _prediction_unique(
            prediction, f"{prefix}canonical_schema", kind="text", context="prediction"
        ),
        "canonical_decimals": _prediction_unique(
            prediction, f"{prefix}canonical_decimals", kind="int", context="prediction"
        ),
        "execution_numeric_mode": _prediction_unique(
            prediction, f"{prefix}execution_numeric_mode", kind="text", context="prediction"
        ),
        "raw_execution_preserved": _prediction_unique(
            prediction, f"{prefix}raw_execution_preserved", kind="bool", context="prediction"
        ),
    }
    contract = expected["canonical_contract"]
    expected_projection = {
        "canonical_v2_version": expected["canonical_v2_version"],
        "artifact_v2_sha256": expected["artifact_v2_sha256"],
        "canonical_schema": contract["schema"],
        "canonical_decimals": contract["decimals"],
        "execution_numeric_mode": contract["execution_mode"],
        "raw_execution_preserved": contract["raw_execution_preserved"],
    }
    if values != expected_projection:
        _fail(f"prediction {layer} canonical V2 fields differ from manifest")
    return values


def _prediction_selector_domain_values(
    prediction: pd.DataFrame,
    *,
    expected: Mapping[str, Any],
    expected_runtime_v1_artifact_sha256: str,
    expected_selector_version: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {
        *SELECTOR_PREDICTION_CANONICAL_COLUMNS,
        *SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS,
        *SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS,
        "trade_predicted_public_market_buyable_probability",
        "trade_selector_artifact_sha256",
        "trade_selector_promoted",
        "trade_selector_version",
        "trade_model_reason",
    }
    missing = sorted(required.difference(prediction.columns))
    if missing:
        _fail(f"prediction missing selector domain columns: {missing!r}")
    flags = _prediction_observation_flags(prediction)
    observation_positions = [index for index, value in enumerate(flags) if value == 1]
    outside_positions = [index for index, value in enumerate(flags) if value == 0]
    if not observation_positions:
        _fail("prediction selector observation domain must not be empty")
    expected_contract = expected["canonical_contract"]
    global_expected = {
        "trade_selector_canonical_v2_version": expected["canonical_v2_version"],
        "trade_selector_canonical_schema": expected_contract["schema"],
        "trade_selector_canonical_decimals": expected_contract["decimals"],
        "trade_selector_execution_numeric_mode": expected_contract[
            "execution_mode"
        ],
        "trade_selector_raw_execution_preserved": expected_contract[
            "raw_execution_preserved"
        ],
    }
    global_kinds = {
        "trade_selector_canonical_v2_version": "text",
        "trade_selector_canonical_schema": "text",
        "trade_selector_canonical_decimals": "int",
        "trade_selector_execution_numeric_mode": "text",
        "trade_selector_raw_execution_preserved": "bool",
    }
    for column in SELECTOR_PREDICTION_GLOBAL_COLUMNS:
        actual = _prediction_unique(
            prediction,
            column,
            kind=global_kinds[column],
            context="prediction",
        )
        if actual != global_expected[column]:
            _fail(f"prediction {column} differs from the frozen selector contract")
    if _prediction_unique(
        prediction,
        "trade_selector_version",
        kind="text",
        context="prediction",
    ) != expected_selector_version:
        _fail("prediction trade_selector_version drift detected")
    formal_selected = 0
    shadow_selected_count = 0
    promoted_count = 0
    for position, row in prediction.iterrows():
        formal_selected += _behavior_boolean(
            row["trade_selected"], f"prediction.trade_selected[{position}]"
        )
        shadow_selected_count += _behavior_boolean(
            row["trade_shadow_selected"],
            f"prediction.trade_shadow_selected[{position}]",
        )
        promoted_count += _behavior_boolean(
            row["trade_selector_promoted"],
            f"prediction.trade_selector_promoted[{position}]",
        )
    if formal_selected != 0:
        _fail("prediction must preserve zero formal trade_selected rows")
    if promoted_count != 0:
        _fail("prediction must preserve zero trade_selector_promoted rows")
    expected_shadow_count = min(2, len(observation_positions))
    if shadow_selected_count != expected_shadow_count:
        _fail("prediction relative-best-two shadow count drift detected")
    for position in outside_positions:
        row = prediction.iloc[position]
        for column in (
            "trade_selector_artifact_sha256",
            "trade_selector_artifact_v2_sha256",
        ):
            if not _is_missing(row[column]):
                _fail(
                    f"prediction.{column}[{position}] must be missing outside "
                    "the observation domain"
                )
        for column in SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS:
            if not _is_missing(row[column]):
                _fail(
                    f"prediction.{column}[{position}] must be missing outside "
                    "the observation domain"
                )
        for column in SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS:
            if _behavior_boolean(
                row[column], f"prediction.{column}[{position}]"
            ) != 0:
                _fail(
                    f"prediction.{column}[{position}] must be zero outside "
                    "the observation domain"
                )
        if _behavior_boolean(
            row["trade_selector_promoted"],
            f"prediction.trade_selector_promoted[{position}]",
        ) != 0:
            _fail("prediction trade_selector_promoted must remain zero")
        if _exact_text(
            row["trade_model_reason"],
            f"prediction.trade_model_reason[{position}]",
        ) != "outside_observation_top10":
            _fail(
                "prediction trade_model_reason must be exactly "
                "outside_observation_top10 outside the observation domain"
            )
    domain = prediction.iloc[observation_positions]
    values = _prediction_layer_values(
        domain,
        layer="trade_selector",
        expected=expected,
    )
    domain_v1_artifact = _prediction_unique(
        domain,
        "trade_selector_artifact_sha256",
        kind="text",
        context="prediction selector observation domain",
    )
    _require_sha256(
        domain_v1_artifact,
        "prediction selector observation domain V1 artifact",
    )
    if domain_v1_artifact != expected_runtime_v1_artifact_sha256:
        _fail("selector V1 artifact differs across same-run runtime surfaces")
    return values, {
        "observation_domain_rows": len(observation_positions),
        "outside_domain_rows": len(outside_positions),
        "global_selector_v2_declarations_match": True,
        "domain_v2_artifact_manifest_match": True,
        "domain_v1_artifact_same_run_match": True,
        "domain_v1_artifact_sha256": domain_v1_artifact,
        "outside_selector_artifacts_empty": True,
        "outside_trade_semantics_valid": True,
        "formal_trade_selected_count": formal_selected,
        "trade_selector_promoted_count": promoted_count,
        "shadow_selected_count": shadow_selected_count,
    }


def validate_behavior_artifacts(
    root: Path | str, manifest: dict[str, Any]
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    behavior = manifest["behavior_contract"]
    audits: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    for name in ("top10", "trade_selector_oos"):
        contract = behavior[name]
        path = _safe_repository_path(
            root_path,
            contract["path"],
            f"behavior_contract.{name}.path",
            suffix=".csv",
        )
        frame = _read_csv(path, f"behavior artifact {name}")
        actual = compute_behavior_fingerprints(
            frame, contract, context=f"behavior.{name}"
        )
        expected = {
            key: contract[key]
            for key in (
                "rows",
                "signal_dates",
                "score_decimals",
                "identity_sha256",
                "date_counts_sha256",
                "discrete_sha256",
                "scores_sha256",
            )
        }
        mismatches = [key for key, value in expected.items() if actual[key] != value]
        if mismatches:
            _fail(f"frozen {name} behavior drift detected: {', '.join(mismatches)}")
        audits[name] = {**actual, "path": contract["path"], "validated": True}
        frames[name] = frame

    decision = behavior["decision"]
    top10_selected = sum(
        _behavior_boolean(value, f"top10.selected[{row}]")
        for row, value in enumerate(frames["top10"]["selected"])
    )
    oos = frames["trade_selector_oos"]
    selector_globally_promoted = sum(
        _behavior_boolean(value, f"oos.trade_selector_globally_promoted[{row}]")
        for row, value in enumerate(oos["trade_selector_globally_promoted"])
    )
    nested_trade_selected = sum(
        _behavior_boolean(value, f"oos.trade_selected[{row}]")
        for row, value in enumerate(oos["trade_selected"])
    )
    nested_selector_promoted = sum(
        _behavior_boolean(value, f"oos.trade_selector_promoted[{row}]")
        for row, value in enumerate(oos["trade_selector_promoted"])
    )
    reason_values = sorted(
        {
            _exact_text(value, f"top10.model_reason[{row}]")
            for row, value in enumerate(frames["top10"]["model_reason"])
        }
    )
    count_actual = {
        "top10_selected_count": top10_selected,
        "selector_globally_promoted_count": selector_globally_promoted,
        "nested_oos_trade_selected_count": nested_trade_selected,
        "nested_oos_trade_selector_promoted_count": nested_selector_promoted,
    }
    mismatches = [
        key for key, actual in count_actual.items() if actual != decision[key]
    ]
    if mismatches:
        _fail("frozen decision/research count drift: " + ", ".join(mismatches))
    if reason_values != decision["reason_values"]:
        _fail("NO_TRADE reason values drift detected")
    audits["decision_frame_counts"] = {
        **count_actual,
        "reason_values": reason_values,
        "formal_no_trade_note": (
            "nested OOS trade_selected/promoted counts are research evidence; "
            "only top-level selected/global promotion and production backtest fields "
            "define formal NO_TRADE"
        ),
    }
    return audits


def _prediction_runtime_date_chain(
    prediction_text: pd.DataFrame,
) -> dict[str, str]:
    if prediction_text.empty:
        _fail("prediction runtime date chain must not be empty")
    columns = {
        "signal_date": "signal_date",
        "exec_date": "expected_buy_date",
        "exit_date": "expected_exit_date",
    }
    missing = sorted(set(columns.values()).difference(prediction_text.columns))
    if missing:
        _fail(f"prediction runtime date chain missing columns: {missing!r}")
    result: dict[str, str] = {}
    for action_field, prediction_field in columns.items():
        values: set[str] = set()
        for row_number, value in enumerate(prediction_text[prediction_field]):
            if (
                not isinstance(value, str)
                or not DATE_PATTERN.fullmatch(value)
                or not _valid_date(value)
            ):
                _fail(
                    f"prediction.{prediction_field}[{row_number}] must be an "
                    "exact YYYYMMDD date"
                )
            values.add(value)
        if len(values) != 1:
            _fail(f"prediction {prediction_field} must be uniform across all rows")
        result[action_field] = next(iter(values))
    return result


def _parse_decision_report_dates(report_path: Path) -> dict[str, str]:
    """Parse the exact dated header and runtime date bullets from a report."""

    try:
        lines = report_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DecisionModelFreezeError(
            f"same-run action report unreadable: {report_path}"
        ) from exc
    if not lines:
        _fail("same-run action report must not be empty")
    header_match = re.fullmatch(r"# Decision Report \((20\d{6})\)", lines[0])
    if header_match is None or not _valid_date(header_match.group(1)):
        _fail("same-run report header must contain an exact YYYYMMDD report date")
    result = {"report_date": header_match.group(1)}
    for field in ("signal_date", "exec_date", "exit_date"):
        prefix = f"- {field}:"
        field_lines = [line for line in lines if line.lstrip().startswith(prefix)]
        if len(field_lines) != 1:
            _fail(f"same-run report must contain exactly one {field} line")
        match = re.fullmatch(
            rf"- {re.escape(field)}: \*\*(20\d{{6}})\*\*",
            field_lines[0],
        )
        if match is None or not _valid_date(match.group(1)):
            _fail(f"same-run report {field} must be an exact YYYYMMDD date")
        result[field] = match.group(1)
    return result


def _validate_action_plan_runtime_binding(
    root_path: Path,
    action: Mapping[str, Any],
    candidates: list[dict[str, Any]],
    stage_watchlist: list[dict[str, Any]],
    *,
    prediction: pd.DataFrame,
    prediction_text: pd.DataFrame,
    three_rank_overlay_prediction: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if len(prediction) != len(prediction_text) or prediction.empty:
        _fail("parsed and exact-text prediction rows must be identical and nonempty")
    date_chain = _prediction_runtime_date_chain(prediction_text)
    for field, expected in date_chain.items():
        actual = _require_text(action.get(field), f"action_plan.{field}")
        if actual != expected:
            _fail(f"action plan {field} differs from same-run prediction")
    report_date = _require_text(action.get("report_date"), "action_plan.report_date")
    if report_date != date_chain["exec_date"]:
        _fail("action plan report_date must equal the same-run execution date")
    expected_report_file = f"decision_report_{report_date}.md"
    if action.get("report_file") != expected_report_file:
        _fail("action plan report_file differs from its report date")
    action_model = _require_mapping(action.get("model"), "action_plan.model")
    if action_model.get("prediction_matches_report") is not True:
        _fail("action plan must attest prediction_matches_report=true")

    report_path = root_path / "outputs" / "decision" / expected_report_file
    eval_path = root_path / "outputs" / "decision" / f"eval_{report_date}.json"
    for path, label in ((report_path, "report"), (eval_path, "evaluation")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            _fail(f"same-run action {label} must be a nonempty regular file")
    report_dates = _parse_decision_report_dates(report_path)
    expected_report_dates = {"report_date": report_date, **date_chain}
    for field, expected in expected_report_dates.items():
        if report_dates[field] != expected:
            _fail(f"same-run report {field} differs from action/prediction")

    evaluation = _read_json_strict(eval_path, "same-run action evaluation")
    for field, expected in date_chain.items():
        actual = evaluation.get(field)
        if (
            not isinstance(actual, str)
            or not DATE_PATTERN.fullmatch(actual)
            or not _valid_date(actual)
        ):
            _fail(
                f"same-run evaluation {field} must be an exact YYYYMMDD date"
            )
        if actual != expected:
            _fail(f"same-run evaluation {field} differs from action/prediction")

    required_prediction = {
        "ts_code",
        "action",
        "trade_rank",
        "trade_gate_pass",
        "trade_shadow_selected",
        "trade_selected",
        "trade_selector_promoted",
        "market_order_allowed",
        "risk_gate_pass",
        "order_type",
        "recommended_max_price",
        "max_auction_change_pct",
        "observation_selected",
        "observation_rank",
    }
    missing = sorted(required_prediction.difference(prediction.columns))
    if missing:
        _fail(f"prediction/action binding missing columns: {missing!r}")
    if len(candidates) != len(prediction):
        _fail("action candidates must have the same rows as pred_latest")

    binary_fields = (
        "trade_gate_pass",
        "trade_shadow_selected",
        "trade_selected",
        "trade_selector_promoted",
        "market_order_allowed",
        "risk_gate_pass",
    )
    prediction_rows: dict[str, dict[str, Any]] = {}
    for position, (_, row) in enumerate(prediction.iterrows()):
        code = row["ts_code"]
        if (
            not isinstance(code, str)
            or not CODE_PATTERN.fullmatch(code)
            or code in prediction_rows
        ):
            _fail(f"prediction/action binding has invalid code at row {position}")
        trade_rank = (
            0
            if _is_missing(row["trade_rank"])
            else _behavior_integer(row["trade_rank"], f"prediction.trade_rank[{position}]")
        )
        projection = {
            "ts_code": code,
            "rank": position + 1,
            "trade_rank": trade_rank,
            "action": _exact_text(row["action"], f"prediction.action[{position}]"),
            "order_type": _exact_text(
                row["order_type"], f"prediction.order_type[{position}]"
            ),
            **{
                field: _behavior_boolean(
                    row[field], f"prediction.{field}[{position}]"
                )
                for field in binary_fields
            },
        }
        for field in ("recommended_max_price", "max_auction_change_pct"):
            if not _is_missing(row[field]):
                _fail(f"NO_TRADE prediction {field} must remain missing")
        prediction_rows[code] = projection

        candidate = candidates[position]
        if candidate.get("ts_code") != code:
            _fail("action candidates must preserve pred_latest row order and codes")
        candidate_projection = {
            "ts_code": code,
            "rank": _require_int(
                candidate.get("rank"), f"action candidate rank[{position}]", minimum=1
            ),
            "trade_rank": _require_int(
                candidate.get("trade_rank"),
                f"action candidate trade_rank[{position}]",
                minimum=0,
            ),
            "action": _exact_text(
                candidate.get("action"), f"action candidate action[{position}]"
            ),
            "order_type": _exact_text(
                candidate.get("order_type"),
                f"action candidate order_type[{position}]",
            ),
            **{
                field: _require_binary_int(
                    candidate.get(field), f"action candidate {field}[{position}]"
                )
                for field in binary_fields
            },
        }
        if candidate_projection != projection:
            _fail(f"action candidate row {position} differs from same-run prediction")

    observation_audit = _validate_prediction_observation_contract(prediction)
    selected_prediction_rows: list[tuple[int, dict[str, Any]]] = []
    watchlist_rank_source = "frozen_observation_rank"
    expected_watch_pool_size = observation_audit["pool_size"]
    if three_rank_overlay_prediction is not None:
        overlay = three_rank_overlay_prediction
        if len(overlay) != len(prediction) or overlay.empty:
            _fail("three-rank action overlay rows differ from canonical prediction")
        required_overlay = {
            "ts_code",
            "top10_selected",
            "promotion_rank",
            "promotion_pool_size",
        }
        missing_overlay = sorted(required_overlay.difference(overlay.columns))
        if missing_overlay:
            _fail(
                "three-rank action overlay missing columns: "
                f"{missing_overlay!r}"
            )
        if overlay["ts_code"].tolist() != prediction["ts_code"].tolist():
            _fail("three-rank action overlay row identity differs from prediction")
        pool_sizes: set[int] = set()
        for position, (_, row) in enumerate(overlay.iterrows()):
            pool_size = _behavior_integer(
                row["promotion_pool_size"],
                f"prediction.promotion_pool_size[{position}]",
            )
            if pool_size < 1:
                _fail("three-rank action overlay pool size must be positive")
            pool_sizes.add(pool_size)
            selected = _behavior_boolean(
                row["top10_selected"],
                f"prediction.top10_selected[{position}]",
            )
            if selected != 1:
                continue
            promotion_rank = _behavior_integer(
                row["promotion_rank"],
                f"prediction.promotion_rank[{position}]",
            )
            selected_prediction_rows.append(
                (promotion_rank, prediction_rows[str(row["ts_code"])])
            )
        if len(pool_sizes) != 1:
            _fail("three-rank action overlay pool size is not constant")
        expected_watch_pool_size = next(iter(pool_sizes))
        if expected_watch_pool_size != len(overlay):
            _fail("three-rank action overlay pool size differs from prediction rows")
        watchlist_rank_source = "three_engine_promotion_rank"
    else:
        for position, (_, row) in enumerate(prediction.iterrows()):
            selected = _behavior_boolean(
                row["observation_selected"],
                f"prediction.observation_selected[{position}]",
            )
            if selected != 1:
                continue
            observation_rank = _behavior_integer(
                row["observation_rank"],
                f"prediction.observation_rank[{position}]",
            )
            selected_prediction_rows.append(
                (observation_rank, prediction_rows[str(row["ts_code"])])
            )
    selected_prediction_rows.sort(key=lambda item: item[0])
    if [rank for rank, _ in selected_prediction_rows] != list(
        range(1, len(selected_prediction_rows) + 1)
    ):
        _fail("prediction observation ranks must be contiguous")
    if not selected_prediction_rows or len(stage_watchlist) != len(
        selected_prediction_rows
    ):
        _fail("action watchlist must exactly match the prediction observation domain")

    watch_binary_fields = (
        "trade_gate_pass",
        "trade_shadow_selected",
        "trade_selected",
        "trade_selector_promoted",
        "market_order_allowed",
        "risk_gate_pass",
    )
    for position, ((observation_rank, expected), item) in enumerate(
        zip(selected_prediction_rows, stage_watchlist, strict=True)
    ):
        expected_label = (
            "二筛影子" if expected["trade_shadow_selected"] == 1 else "仅观察"
        )
        if item.get("ts_code") != expected["ts_code"]:
            _fail("action watchlist must preserve observation rank order and codes")
        watch_projection = {
            "trade_rank": _require_int(
                item.get("trade_rank"),
                f"action watchlist trade_rank[{position}]",
                minimum=0,
            ),
            "action": _exact_text(
                item.get("action"), f"action watchlist action[{position}]"
            ),
            "order_type": _exact_text(
                item.get("order_type"), f"action watchlist order_type[{position}]"
            ),
            **{
                field: _require_binary_int(
                    item.get(field), f"action watchlist {field}[{position}]"
                )
                for field in watch_binary_fields
            },
        }
        expected_watch = {
            key: expected[key]
            for key in ("trade_rank", "action", "order_type", *watch_binary_fields)
        }
        if watch_projection != expected_watch:
            _fail(f"action watchlist row {position} differs from same-run prediction")
        if _require_int(
            item.get("stage_watch_rank"),
            f"action watchlist stage_watch_rank[{position}]",
            minimum=1,
        ) != observation_rank:
            _fail("action stage_watch_rank differs from its frozen ranking source")
        if item.get("watch_label") != expected_label:
            _fail("action watch label differs from same-run prediction shadow state")

    watchlist_shadow_count = sum(
        row["trade_shadow_selected"] for _, row in selected_prediction_rows
    )
    canonical_shadow_count = sum(
        row["trade_shadow_selected"] for row in prediction_rows.values()
    )
    if (
        three_rank_overlay_prediction is None
        and watchlist_shadow_count != min(2, len(selected_prediction_rows))
    ):
        _fail("same-run prediction does not preserve relative-best-two")
    if three_rank_overlay_prediction is not None and watchlist_shadow_count > 2:
        _fail("three-rank watchlist exposes more than two legacy shadow rows")
    if _require_int(action.get("stage_watch_count"), "action_plan.stage_watch_count") != len(
        selected_prediction_rows
    ):
        _fail("action stage_watch_count differs from the observation domain")
    if _require_int(
        action.get("stage_watch_eligible_count"),
        "action_plan.stage_watch_eligible_count",
    ) != expected_watch_pool_size:
        _fail("action stage_watch_eligible_count differs from prediction")
    if _require_int(
        action.get("stage_watch_display_limit"),
        "action_plan.stage_watch_display_limit",
    ) != OBSERVATION_TOP_N:
        _fail("action stage_watch_display_limit drift detected")
    return {
        "prediction_path": "outputs/auction_v3/predictions/pred_latest.csv",
        "prediction_rows": len(prediction),
        "signal_date": date_chain["signal_date"],
        "exec_date": date_chain["exec_date"],
        "exit_date": date_chain["exit_date"],
        "report_dates_exact": True,
        "evaluation_dates_exact": True,
        "candidate_rows_exact": True,
        "watchlist_rows_exact": True,
        "watchlist_rank_source": watchlist_rank_source,
        "shadow_rows": canonical_shadow_count,
        "watchlist_shadow_rows": watchlist_shadow_count,
    }


def _standalone_prediction_policy_gates(
    root_path: Path,
    *,
    expected_model: Mapping[str, Any],
    expected_selector: Mapping[str, Any],
    prediction: pd.DataFrame,
    prediction_text: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute action-facing prediction policy gates outside full runtime."""

    model_meta = _read_json(
        root_path / "outputs/auction_v3/models/model_meta_latest.json"
    )
    backtest = _read_json(
        root_path / "outputs/auction_v3/metrics/backtest_latest.json"
    )
    meta_selector = _require_mapping(
        model_meta.get("trade_selector"), "model_meta.trade_selector"
    )
    backtest_selector = _require_mapping(
        backtest.get("trade_selector"), "backtest.trade_selector"
    )
    meta_model_raw, meta_model_canonical = _live_execution_policy_projection(
        model_meta.get("selection_policy"),
        layer="model",
        context="model_meta.selection_policy",
    )
    meta_selector_raw, meta_selector_canonical = (
        _live_execution_policy_projection(
            meta_selector.get("production_policy"),
            layer="trade_selector",
            context="model_meta.trade_selector.production_policy",
        )
    )
    backtest_selector_raw, backtest_selector_canonical = (
        _live_execution_policy_projection(
            backtest_selector.get("production_policy"),
            layer="trade_selector",
            context="backtest.trade_selector.production_policy",
        )
    )
    if canonical_json_bytes(meta_selector_raw) != canonical_json_bytes(
        backtest_selector_raw
    ):
        _fail("selector raw execution policy differs across meta/backtest")
    expected_model_projection = expected_model["fingerprint_v2"][
        "policy_projection"
    ]
    expected_selector_projection = expected_selector["fingerprint_v2"][
        "policy_projection"
    ]
    if meta_model_canonical != expected_model_projection:
        _fail("model raw execution policy does not canonicalize to frozen q8")
    if (
        meta_selector_canonical != expected_selector_projection
        or backtest_selector_canonical != expected_selector_projection
    ):
        _fail("selector raw execution policy does not canonicalize to frozen q8")
    return (
        _validate_prediction_policy_gates(
            prediction,
            prediction_text,
            model_raw_projection=meta_model_raw,
            selector_raw_projection=meta_selector_raw,
        ),
        meta_model_raw,
    )


def _validate_action_plan_contract(
    root_path: Path,
    manifest: dict[str, Any],
    *,
    prediction: pd.DataFrame | None = None,
    prediction_text: pd.DataFrame | None = None,
    prediction_policy_gates: Mapping[str, Any] | None = None,
    model_raw_policy: Mapping[str, Any] | None = None,
    three_rank_overlay_prediction: pd.DataFrame | None = None,
    three_rank_preimage_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    production = _require_mapping(manifest.get("production"), "manifest.production")
    canonical = _require_mapping(
        production.get("canonical_v2"), "manifest.production.canonical_v2"
    )
    expected_model = _require_mapping(
        canonical.get("model"), "manifest.production.canonical_v2.model"
    )
    expected_selector = _require_mapping(
        canonical.get("trade_selector"),
        "manifest.production.canonical_v2.trade_selector",
    )
    action_path = root_path / "outputs/decision/action_plan_latest.json"
    if action_path.is_symlink() or not action_path.is_file():
        _fail("frozen action plan must be a regular non-symlink file")
    action = _read_json_strict(action_path, "frozen action plan")
    prediction_path = root_path / "outputs/auction_v3/predictions/pred_latest.csv"
    if prediction_path.is_symlink() or not prediction_path.is_file():
        _fail("same-run prediction must be a regular non-symlink file")
    if prediction is None:
        prediction = _read_csv(prediction_path, "action prediction binding")
    if prediction_text is None:
        prediction_text = _read_csv_exact_text(
            prediction_path, "action prediction exact-text binding"
        )
    action_preimage_audit: dict[str, Any] = dict(
        three_rank_preimage_audit
        or {
            "active": False,
            "canonical_preimage_validated": False,
            "source": "upstream_runtime_prevalidated",
        }
    )
    if prediction_policy_gates is None and model_raw_policy is None:
        raw_prediction = prediction
        prediction, prediction_text, action_preimage_audit = (
            _canonical_prediction_validation_view(
                prediction,
                prediction_text,
            )
        )
        if action_preimage_audit["active"]:
            three_rank_overlay_prediction = raw_prediction
        prediction_policy_gates, model_raw_policy = (
            _standalone_prediction_policy_gates(
                root_path,
                expected_model=expected_model,
                expected_selector=expected_selector,
                prediction=prediction,
                prediction_text=prediction_text,
            )
        )
    elif prediction_policy_gates is None or model_raw_policy is None:
        _fail("prediction policy gates and authoritative model policy must be paired")
    else:
        prediction_policy_gates = _require_mapping(
            prediction_policy_gates, "prediction policy gate audit"
        )
        model_raw_policy = _require_mapping(
            model_raw_policy, "authoritative model raw policy"
        )
    _validate_policy_projection(
        model_raw_policy,
        layer="model",
        context="authoritative model raw policy",
        require_canonical=False,
    )
    for field in (
        "promotion_rank_exact",
        "trade_rank_exact",
        "selection_and_reason_exact",
        "raw_thresholds_and_gates_exact",
    ):
        if prediction_policy_gates.get(field) is not True:
            _fail(f"prediction policy gate audit missing exact result: {field}")
    action_model = _require_mapping(action.get("model"), "action_plan.model")
    action_model_raw_policy, action_model_canonical_policy = (
        _live_execution_policy_projection(
            action_model.get("selection_policy"),
            layer="model",
            context="action_plan.model.selection_policy",
        )
    )
    if canonical_json_bytes(action_model_raw_policy) != canonical_json_bytes(
        model_raw_policy
    ):
        _fail(
            "action plan model selection_policy differs from authoritative model_meta"
        )
    if action_model_canonical_policy != expected_model["fingerprint_v2"][
        "policy_projection"
    ]:
        _fail("action plan model selection_policy does not canonicalize to frozen q8")
    action_model_v2 = _action_layer_values(
        action_model, layer="model", expected=expected_model
    )
    action_selector_v2 = _action_layer_values(
        action_model, layer="trade_selector", expected=expected_selector
    )
    if action_model_v2 != expected_model or action_selector_v2 != expected_selector:
        _fail("action plan canonical V2 differs from manifest")
    if action.get("status_code") != production["formal_status"]:
        _fail("action plan formal status drift detected")
    formal_buy_count = _require_int(
        action.get("formal_buy_count"), "action_plan.formal_buy_count"
    )
    if formal_buy_count != production["formal_buy_count"]:
        _fail("action plan formal buy count drift detected")
    if action.get("guidance_only") is not True:
        _fail("frozen action plan must remain guidance-only")
    if action.get("broker_connected") is not False:
        _fail("frozen action plan must not connect a broker")
    if action.get("order_execution") != "manual_only":
        _fail("frozen action plan must remain manual-only")
    candidates = action.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        _fail("action_plan.candidates must be a nonempty list")
    buy_count = 0
    shadow_count = 0
    candidate_projection: dict[
        str,
        tuple[str, int, float, int, int, int, int, str, Any, Any],
    ] = {}
    for row_number, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            _fail(f"action_plan.candidates[{row_number}] must be an object")
        context = f"action_plan.candidates[{row_number}]"
        candidate_action = _exact_text(candidate.get("action"), f"{context}.action")
        candidate_code = candidate.get("ts_code")
        if (
            not isinstance(candidate_code, str)
            or not CODE_PATTERN.fullmatch(candidate_code)
            or candidate_code in candidate_projection
        ):
            _fail(f"{context} has invalid/duplicate ts_code")
        if candidate_action == "BUY":
            buy_count += 1
        elif candidate_action not in {"REJECT", "SHADOW_ONLY"}:
            _fail("NO_TRADE action plan contains an unauthorized action")
        shadow_selected = _require_binary_int(
            candidate.get("trade_shadow_selected"),
            f"{context}.trade_shadow_selected",
        )
        expected_action = "SHADOW_ONLY" if shadow_selected == 1 else "REJECT"
        if candidate_action != expected_action:
            _fail(
                "NO_TRADE candidate SHADOW_ONLY must match the relative-best-two flag"
            )
        shadow_count += shadow_selected
        trade_selected = _require_binary_int(
            candidate.get("trade_selected"), f"{context}.trade_selected"
        )
        selector_promoted = _require_binary_int(
            candidate.get("trade_selector_promoted"),
            f"{context}.trade_selector_promoted",
        )
        market_order_allowed = _require_binary_int(
            candidate.get("market_order_allowed"),
            f"{context}.market_order_allowed",
        )
        risk_gate_pass = _require_binary_int(
            candidate.get("risk_gate_pass"), f"{context}.risk_gate_pass"
        )
        if any(
            value != 0
            for value in (
                trade_selected,
                selector_promoted,
                market_order_allowed,
                risk_gate_pass,
            )
        ):
            _fail("NO_TRADE action candidate exposes an executable or promoted state")
        if candidate.get("order_type") != "LIMIT_ONLY_MANUAL":
            _fail("NO_TRADE action candidate must remain limit-only/manual")
        if candidate.get("recommended_max_price") is not None:
            _fail("NO_TRADE action candidate must not expose a recommended max price")
        if candidate.get("max_auction_change_pct") is not None:
            _fail("NO_TRADE action candidate must not expose an auction gap limit")
        if type(candidate.get("target_weight")) not in (int, float):
            _fail(f"{context}.target_weight invalid")
        target_weight = float(candidate["target_weight"])
        if not math.isfinite(target_weight) or target_weight != 0.0:
            _fail("NO_TRADE action candidates require zero target_weight")
        candidate_projection[candidate_code] = (
            candidate_action,
            shadow_selected,
            target_weight,
            trade_selected,
            selector_promoted,
            market_order_allowed,
            risk_gate_pass,
            candidate["order_type"],
            candidate.get("recommended_max_price"),
            candidate.get("max_auction_change_pct"),
        )
    if buy_count != 0:
        _fail("NO_TRADE action plan contains BUY candidates")
    if _require_int(action.get("shadow_count"), "action_plan.shadow_count") != shadow_count:
        _fail("action plan shadow count drift detected")
    if action_model.get("version") != production["model_version"]:
        _fail("action plan model version drift detected")
    if action_model.get("promoted") is not False:
        _fail("action plan model must remain not promoted")
    nested_selector = _require_mapping(
        action_model.get("trade_selector"), "action_plan.model.trade_selector"
    )
    if nested_selector.get("version") != production["trade_selector_version"]:
        _fail("action plan selector version drift detected")
    if nested_selector.get("promoted") is not False:
        _fail("action plan selector must remain not promoted")
    watch_contract = manifest["behavior_contract"]["action_watchlist"]
    if action_path.relative_to(root_path).as_posix() != watch_contract["path"]:
        _fail("action watchlist contract path does not name action_plan_latest.json")
    watch_actual = compute_action_watchlist_fingerprint(action, watch_contract)
    if watch_actual["shadow_only_rows"] != shadow_count:
        _fail("action watchlist/candidate relative-best-two drift detected")
    stage_watchlist = action.get("stage_watchlist")
    if not isinstance(stage_watchlist, list) or not stage_watchlist:
        _fail("action_plan.stage_watchlist must be a nonempty list")
    watch_codes: set[str] = set()
    for row_number, item in enumerate(stage_watchlist):
        if not isinstance(item, dict):
            _fail(f"action_plan.stage_watchlist[{row_number}] must be an object")
        code = item.get("ts_code")
        if not isinstance(code, str) or code in watch_codes:
            _fail(f"action_plan.stage_watchlist[{row_number}] has invalid ts_code")
        watch_codes.add(code)
        candidate_values = candidate_projection.get(code)
        if candidate_values is None:
            _fail(f"action watchlist row {row_number} has no matching candidate")
        watch_values = (
            item.get("action"),
            _require_binary_int(
                item.get("trade_shadow_selected"),
                f"action watchlist trade_shadow_selected[{row_number}]",
            ),
            float(item.get("target_weight")),
            _require_binary_int(
                item.get("trade_selected"),
                f"action watchlist trade_selected[{row_number}]",
            ),
            _require_binary_int(
                item.get("trade_selector_promoted"),
                f"action watchlist trade_selector_promoted[{row_number}]",
            ),
            _require_binary_int(
                item.get("market_order_allowed"),
                f"action watchlist market_order_allowed[{row_number}]",
            ),
            _require_binary_int(
                item.get("risk_gate_pass"),
                f"action watchlist risk_gate_pass[{row_number}]",
            ),
            item.get("order_type"),
            item.get("recommended_max_price"),
            item.get("max_auction_change_pct"),
        )
        if watch_values != candidate_values:
            _fail(f"action watchlist row {row_number} differs from its candidate")
    if not watch_codes.issubset(candidate_projection):
        _fail("action watchlist contains an unknown candidate")
    runtime_binding = _validate_action_plan_runtime_binding(
        root_path,
        action,
        candidates,
        stage_watchlist,
        prediction=prediction,
        prediction_text=prediction_text,
        three_rank_overlay_prediction=three_rank_overlay_prediction,
    )
    if runtime_binding["shadow_rows"] != shadow_count:
        _fail("action candidate shadow count differs from same-run prediction")
    return {
        "present": True,
        "status_code": action["status_code"],
        "formal_buy_count": formal_buy_count,
        "buy_candidate_count": buy_count,
        "shadow_candidate_count": shadow_count,
        "model_v2_match": True,
        "selector_v2_match": True,
        "watchlist": watch_actual,
        "model_raw_policy_match": True,
        "prediction_policy_gates": dict(prediction_policy_gates),
        "three_rank_canonical_preimage": action_preimage_audit,
        "runtime_binding": runtime_binding,
        "activation_reference": {
            "path": watch_contract["path"],
            "rows": watch_contract["rows"],
            "sha256": watch_contract["sha256"],
            "runtime_equality_required": False,
        },
    }


def validate_action_plan_artifact(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    force_enforcement: bool = False,
) -> dict[str, Any]:
    enforce = model_freeze_active(manifest) or force_enforcement
    if not enforce:
        return {"active": False, "validated": True, "enforced": False}
    root_path = Path(root).resolve()
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("canonical V2 action enforcement requires freeze schema V2")
    _validate_v2_manifest(root_path, manifest, require_complete=force_enforcement)
    pinned_files = validate_pinned_files(
        root_path,
        manifest,
        force_enforcement=force_enforcement,
    )
    action = _validate_action_plan_contract(root_path, manifest)
    return {
        "active": model_freeze_active(manifest),
        "validated": True,
        "enforced": True,
        "forced_enforcement": force_enforcement
        and not model_freeze_active(manifest),
        "pinned_files": pinned_files,
        "action_plan": action,
    }


def validate_runtime_artifacts(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    check_action_plan: bool = True,
    force_enforcement: bool = False,
) -> dict[str, Any]:
    enforce = model_freeze_active(manifest) or force_enforcement
    if not enforce:
        return {
            "active": False,
            "validated": True,
            "canonical_v2_enforced": False,
            "legacy_v1_enforced": False,
        }
    root_path = Path(root).resolve()
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("canonical V2 runtime enforcement requires freeze schema V2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=force_enforcement,
    )
    pinned_files_audit = validate_pinned_files(
        root_path,
        manifest,
        force_enforcement=force_enforcement,
    )
    if model_freeze_active(manifest):
        _, snapshot_audit = load_frozen_history_snapshot(root_path, manifest)
    else:
        _, snapshot_audit = load_verified_frozen_history_snapshot(
            root_path, manifest
        )
    model_meta = _read_json(
        root_path / "outputs/auction_v3/models/model_meta_latest.json"
    )
    backtest = _read_json(root_path / "outputs/auction_v3/metrics/backtest_latest.json")
    prediction_path = root_path / "outputs/auction_v3/predictions/pred_latest.csv"
    prediction_overlay = _read_csv(prediction_path, "prediction artifact")
    prediction_text_overlay = _read_csv_exact_text(
        prediction_path,
        "prediction exact-text artifact",
    )
    prediction, prediction_text, three_rank_preimage_audit = (
        _canonical_prediction_validation_view(
            prediction_overlay,
            prediction_text_overlay,
        )
    )
    production = manifest["production"]
    expected_canonical = production["canonical_v2"]
    expected_model = expected_canonical["model"]
    expected_selector = expected_canonical["trade_selector"]

    meta_model = _runtime_layer_values(
        model_meta, layer="model", context="model_meta.model_canonical_v2"
    )
    backtest_model = _runtime_layer_values(
        backtest, layer="model", context="backtest.model_canonical_v2"
    )
    meta_selector_raw = _require_mapping(
        model_meta.get("trade_selector"), "model_meta.trade_selector"
    )
    backtest_selector_raw = _require_mapping(
        backtest.get("trade_selector"), "backtest.trade_selector"
    )
    meta_selector = _runtime_layer_values(
        meta_selector_raw,
        layer="trade_selector",
        context="model_meta.trade_selector.canonical_v2",
    )
    backtest_selector = _runtime_layer_values(
        backtest_selector_raw,
        layer="trade_selector",
        context="backtest.trade_selector.canonical_v2",
    )
    if meta_model != expected_model or backtest_model != expected_model:
        _fail("model canonical V2 differs across manifest/meta/backtest")
    if meta_selector != expected_selector or backtest_selector != expected_selector:
        _fail("selector canonical V2 differs across manifest/meta/backtest")
    meta_model_raw_policy, meta_model_canonical_policy = (
        _live_execution_policy_projection(
            model_meta.get("selection_policy"),
            layer="model",
            context="model_meta.selection_policy",
        )
    )
    meta_selector_raw_policy, meta_selector_canonical_policy = (
        _live_execution_policy_projection(
            meta_selector_raw.get("production_policy"),
            layer="trade_selector",
            context="model_meta.trade_selector.production_policy",
        )
    )
    backtest_selector_raw_policy, backtest_selector_canonical_policy = (
        _live_execution_policy_projection(
            backtest_selector_raw.get("production_policy"),
            layer="trade_selector",
            context="backtest.trade_selector.production_policy",
        )
    )
    if canonical_json_bytes(meta_selector_raw_policy) != canonical_json_bytes(
        backtest_selector_raw_policy
    ):
        _fail("selector raw execution policy differs across meta/backtest")
    expected_model_projection = expected_model["fingerprint_v2"][
        "policy_projection"
    ]
    expected_selector_projection = expected_selector["fingerprint_v2"][
        "policy_projection"
    ]
    if meta_model_canonical_policy != expected_model_projection:
        _fail("model raw execution policy does not canonicalize to frozen q8")
    if (
        meta_selector_canonical_policy != expected_selector_projection
        or backtest_selector_canonical_policy != expected_selector_projection
    ):
        _fail("selector raw execution policy does not canonicalize to frozen q8")
    prediction_model = _prediction_layer_values(
        prediction, layer="model", expected=expected_model
    )
    # Top10/OOS artifacts are historical fold behavior. Their policy columns
    # are frozen by the exact discrete/q8 behavior hashes and must not be
    # compared to the final production policy. The current prediction is the
    # final-policy execution surface, so equality is enforced here instead.
    _validate_model_policy_columns(
        prediction, expected_model, context="prediction.final_model_policy"
    )
    model_policy_text_surface = _validate_model_policy_text_surface(
        prediction_text,
        parsed_rows=len(prediction),
        raw_projection=meta_model_raw_policy,
        context="prediction.final_model_raw_policy",
    )
    prediction_policy_gates = _validate_prediction_policy_gates(
        prediction,
        prediction_text,
        model_raw_projection=meta_model_raw_policy,
        selector_raw_projection=meta_selector_raw_policy,
    )
    prediction_observation = _validate_prediction_observation_contract(
        prediction
    )
    selector_v1_meta = _require_sha256(
        meta_selector_raw.get("production_artifact_sha256"),
        "model_meta.trade_selector.production_artifact_sha256",
    )
    selector_v1_backtest = _require_sha256(
        backtest_selector_raw.get("production_artifact_sha256"),
        "backtest.trade_selector.production_artifact_sha256",
    )
    if selector_v1_meta != selector_v1_backtest:
        _fail("selector V1 artifact differs across same-run meta/backtest surfaces")
    prediction_selector, prediction_selector_domain = (
        _prediction_selector_domain_values(
            prediction,
            expected=expected_selector,
            expected_runtime_v1_artifact_sha256=selector_v1_meta,
            expected_selector_version=production["trade_selector_version"],
        )
    )
    prediction_fill_relationships = _validate_prediction_fill_relationships(
        prediction
    )

    expected_model_version = production["model_version"]
    expected_selector_version = production["trade_selector_version"]
    version_checks = {
        "meta_model_version": model_meta.get("model_version") == expected_model_version,
        "backtest_model_version": backtest.get("model_version") == expected_model_version,
        "meta_selector_version": meta_selector_raw.get("version")
        == expected_selector_version,
        "backtest_selector_version": backtest_selector_raw.get("version")
        == expected_selector_version,
        "meta_not_promoted": model_meta.get("promoted") is False,
        "backtest_not_promoted": backtest.get("promoted") is False,
        "meta_selector_not_promoted": meta_selector_raw.get("promoted") is False,
        "backtest_selector_not_promoted": backtest_selector_raw.get("promoted")
        is False,
    }
    failed_versions = [name for name, passed in version_checks.items() if not passed]
    if failed_versions:
        _fail("frozen runtime version/promotion drift: " + ", ".join(failed_versions))

    legacy_expected = production["legacy_v1_audit"]
    legacy_actual = {
        "model_meta": model_meta.get("model_artifact_sha256"),
        "model_backtest": backtest.get("model_artifact_sha256"),
        "selector_meta": meta_selector_raw.get("production_artifact_sha256"),
        "selector_backtest": backtest_selector_raw.get(
            "production_artifact_sha256"
        ),
        "selector_prediction": prediction_selector_domain[
            "domain_v1_artifact_sha256"
        ],
    }
    for name, value in legacy_actual.items():
        _require_sha256(value, f"legacy_v1_audit.actual.{name}")
    legacy_matches = {
        "model_meta": legacy_actual["model_meta"]
        == legacy_expected["model_artifact_sha256"],
        "model_backtest": legacy_actual["model_backtest"]
        == legacy_expected["model_artifact_sha256"],
        "selector_meta": legacy_actual["selector_meta"]
        == legacy_expected["trade_selector_artifact_sha256"],
        "selector_backtest": legacy_actual["selector_backtest"]
        == legacy_expected["trade_selector_artifact_sha256"],
        "selector_prediction": legacy_actual["selector_prediction"]
        == legacy_expected["trade_selector_artifact_sha256"],
    }

    action_checks: dict[str, Any] = {}
    shared_action_checks: dict[str, Any] = {}
    if check_action_plan:
        # Keep standalone Daily action verification and the full runtime gate on
        # one fail-closed contract. The legacy inline projection below is kept
        # temporarily for return-shape compatibility and must only run after
        # this stricter shared validator succeeds.
        shared_action_checks = _validate_action_plan_contract(
            root_path,
            manifest,
            prediction=prediction,
            prediction_text=prediction_text,
            prediction_policy_gates=prediction_policy_gates,
            model_raw_policy=meta_model_raw_policy,
            three_rank_overlay_prediction=(
                prediction_overlay
                if three_rank_preimage_audit["active"]
                else None
            ),
            three_rank_preimage_audit=three_rank_preimage_audit,
        )
        action_path = root_path / "outputs/decision/action_plan_latest.json"
        if not action_path.is_file():
            _fail("frozen action plan is required but missing")
        action = _read_json(action_path)
        action_model = _require_mapping(action.get("model"), "action_plan.model")
        action_model_v2 = _action_layer_values(
            action_model, layer="model", expected=expected_model
        )
        action_selector_v2 = _action_layer_values(
            action_model, layer="trade_selector", expected=expected_selector
        )
        if action_model_v2 != expected_model or action_selector_v2 != expected_selector:
            _fail("action plan canonical V2 differs from manifest")
        if action.get("status_code") != production["formal_status"]:
            _fail("action plan formal status drift detected")
        formal_buy_count = _require_int(
            action.get("formal_buy_count"), "action_plan.formal_buy_count"
        )
        if formal_buy_count != production["formal_buy_count"]:
            _fail("action plan formal buy count drift detected")
        candidates = action.get("candidates")
        if not isinstance(candidates, list):
            _fail("action_plan.candidates must be a list")
        buy_count = 0
        shadow_count = 0
        candidate_projection: dict[str, tuple[str, int, float]] = {}
        for row_number, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                _fail(f"action_plan.candidates[{row_number}] must be an object")
            candidate_action = _exact_text(
                candidate.get("action"),
                f"action_plan.candidates[{row_number}].action",
            )
            candidate_code = candidate.get("ts_code")
            if (
                not isinstance(candidate_code, str)
                or not CODE_PATTERN.fullmatch(candidate_code)
                or candidate_code in candidate_projection
            ):
                _fail(
                    f"action_plan.candidates[{row_number}] has invalid/duplicate ts_code"
                )
            if candidate_action == "BUY":
                buy_count += 1
            elif candidate_action not in {"REJECT", "SHADOW_ONLY"}:
                _fail("NO_TRADE action plan contains an unauthorized action")
            if "trade_shadow_selected" not in candidate:
                _fail(
                    f"action_plan.candidates[{row_number}] missing "
                    "trade_shadow_selected"
                )
            shadow_selected = _require_binary_int(
                candidate["trade_shadow_selected"],
                f"action_plan.candidates[{row_number}].trade_shadow_selected",
            )
            expected_action = "SHADOW_ONLY" if shadow_selected == 1 else "REJECT"
            if candidate_action != expected_action:
                _fail(
                    "NO_TRADE candidate SHADOW_ONLY must match the "
                    "relative-best-two flag"
                )
            shadow_count += shadow_selected
            if "target_weight" not in candidate:
                _fail(
                    f"action_plan.candidates[{row_number}] missing target_weight"
                )
            if type(candidate["target_weight"]) not in (int, float):
                _fail(
                    f"action_plan.candidates[{row_number}].target_weight invalid"
                )
            try:
                target_weight = float(candidate["target_weight"])
            except (TypeError, ValueError, OverflowError):
                _fail(
                    f"action_plan.candidates[{row_number}].target_weight invalid"
                )
            if not math.isfinite(target_weight) or target_weight != 0.0:
                _fail("NO_TRADE action candidates require zero target_weight")
            candidate_projection[candidate_code] = (
                candidate_action,
                shadow_selected,
                target_weight,
            )
        if buy_count != 0:
            _fail("NO_TRADE action plan contains BUY candidates")
        if _require_int(action.get("shadow_count"), "action_plan.shadow_count") != (
            shadow_count
        ):
            _fail("action plan shadow count drift detected")
        if action_model.get("version") != production["model_version"]:
            _fail("action plan model version drift detected")
        if action_model.get("promoted") is not False:
            _fail("action plan model must remain not promoted")
        nested_selector = _require_mapping(
            action_model.get("trade_selector"), "action_plan.model.trade_selector"
        )
        if nested_selector.get("version") != production["trade_selector_version"]:
            _fail("action plan selector version drift detected")
        if nested_selector.get("promoted") is not False:
            _fail("action plan selector must remain not promoted")
        watch_contract = manifest["behavior_contract"]["action_watchlist"]
        if action_path.relative_to(root_path).as_posix() != watch_contract["path"]:
            _fail("action watchlist contract path does not name action_plan_latest.json")
        watch_actual = compute_action_watchlist_fingerprint(action, watch_contract)
        if watch_actual["shadow_only_rows"] != shadow_count:
            _fail("action watchlist/candidate relative-best-two drift detected")
        for row_number, item in enumerate(action.get("stage_watchlist", [])):
            code = item["ts_code"]
            candidate_values = candidate_projection.get(code)
            if candidate_values is None:
                _fail(
                    f"action watchlist row {row_number} has no matching candidate"
                )
            watch_values = (
                item["action"],
                _require_binary_int(
                    item["trade_shadow_selected"],
                    f"action watchlist trade_shadow_selected[{row_number}]",
                ),
                float(item["target_weight"]),
            )
            if watch_values != candidate_values:
                _fail(
                    f"action watchlist row {row_number} differs from its candidate"
                )
        if any(
            float(item.get("target_weight", 0.0)) != 0.0
            for item in action.get("stage_watchlist", [])
            if isinstance(item, dict)
        ):
            _fail("NO_TRADE action watchlist contains nonzero target weight")
        action_checks = {
            "present": True,
            "status_code": action["status_code"],
            "formal_buy_count": formal_buy_count,
            "buy_candidate_count": buy_count,
            "shadow_candidate_count": shadow_count,
            "model_v2_match": True,
            "selector_v2_match": True,
            "watchlist": watch_actual,
        }
        action_checks = shared_action_checks

    behavior_audit = validate_behavior_artifacts(root_path, manifest)
    decision = manifest["behavior_contract"]["decision"]
    nested_contract = manifest["behavior_contract"]["nested_oos_research"]
    formal_policy_oos = _require_mapping(
        backtest_selector_raw.get("formal_policy_oos"),
        "backtest.trade_selector.formal_policy_oos",
    )
    all_candidates = _require_mapping(
        formal_policy_oos.get("all_candidates"),
        "backtest.trade_selector.formal_policy_oos.all_candidates",
    )
    market_buyable_only = _require_mapping(
        formal_policy_oos.get("market_buyable_only"),
        "backtest.trade_selector.formal_policy_oos.market_buyable_only",
    )
    nested_oos_actual = {
        "signals": _require_int(
            all_candidates.get("signals"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.signals",
        ),
        "signal_dates": _require_int(
            all_candidates.get("signal_dates"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.signal_dates",
        ),
        "filled_trades": _require_int(
            all_candidates.get("filled_trades"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.filled_trades",
        ),
        "market_buyable_filled_trades": _require_int(
            market_buyable_only.get("filled_trades"),
            "backtest.trade_selector.formal_policy_oos.market_buyable_only.filled_trades",
        ),
    }
    for key, actual in nested_oos_actual.items():
        if actual != nested_contract[key]:
            _fail(f"nested OOS research metric drift: {key}")
    # These are deliberately the top-level production backtest fields. Nested
    # selector.formal_policy_oos.all_candidates is research evidence (158/119/158),
    # not a formal trade authorization.
    production_backtest_values = {
        "production_backtest_signals": _require_int(
            backtest.get("signals"), "backtest.<root>.signals"
        ),
        "production_backtest_signal_dates": _require_int(
            backtest.get("signal_dates"), "backtest.<root>.signal_dates"
        ),
        "production_backtest_fills": _require_int(
            backtest.get("filled_trades"), "backtest.<root>.filled_trades"
        ),
    }
    for key, actual in production_backtest_values.items():
        if actual != decision[key]:
            _fail(f"{key} drift detected")

    history_end = str(((model_meta.get("data_coverage") or {}).get("history_end")) or "")
    if history_end and (
        not _valid_date(history_end)
        or history_end > manifest["training_cutoff_signal_date"]
    ):
        _fail("frozen runtime history_end exceeds or violates cutoff")
    return {
        "active": model_freeze_active(manifest),
        "forced_enforcement": force_enforcement and not model_freeze_active(manifest),
        "freeze_id": manifest["freeze_id"],
        "validated": True,
        "canonical_v2_enforced": True,
        "legacy_v1_enforced": False,
        "raw_execution_preserved": True,
        "execution_policy_relation": {
            "model_raw_meta_authoritative": True,
            "model_raw_canonical_q8_match": True,
            "selector_raw_meta_backtest_exact": True,
            "selector_raw_canonical_q8_match": True,
        },
        "pinned_files": pinned_files_audit,
        "snapshot": snapshot_audit,
        "model": {
            "manifest": expected_model,
            "meta_match": True,
            "backtest_match": True,
            "prediction": prediction_model,
        },
        "trade_selector": {
            "manifest": expected_selector,
            "meta_match": True,
            "backtest_match": True,
            "prediction": prediction_selector,
            "prediction_domain": prediction_selector_domain,
        },
        "prediction_fill_relationships": prediction_fill_relationships,
        "prediction_model_policy_text_surface": model_policy_text_surface,
        "prediction_policy_gates": prediction_policy_gates,
        "prediction_observation": prediction_observation,
        "three_rank_canonical_preimage": three_rank_preimage_audit,
        "legacy_v1_audit": {
            "enforcement": "audit_only",
            "expected": legacy_expected,
            "actual": legacy_actual,
            "matches": legacy_matches,
            "all_match": all(legacy_matches.values()),
        },
        "version_checks": version_checks,
        "action_plan": action_checks,
        "behavior": behavior_audit,
        "nested_oos_research": {
            **nested_oos_actual,
            "formal_authorization": False,
        },
        "production_backtest_zero_values": production_backtest_values,
        "history_end": history_end,
        "training_cutoff_signal_date": manifest["training_cutoff_signal_date"],
    }


__all__ = [
    "ACTION_WATCHLIST_COLUMNS",
    "BEHAVIOR_SCHEMA_VERSION",
    "CANONICAL_RUNTIME_SCHEMA_VERSION",
    "DecisionModelFreezeError",
    "FREEZE_SCHEMA_VERSION",
    "GATE_DISCRETE_BEHAVIOR_COLUMNS",
    "IDENTITY_COLUMNS",
    "KNOWN_HISTORY_PATH",
    "KNOWN_HISTORY_ROWS",
    "KNOWN_HISTORY_SHA256",
    "KNOWN_REFERENCE_EVIDENCE",
    "LEGACY_PRE_THREE_RANK_FREEZE_ID",
    "OOS_DISCRETE_BEHAVIOR_COLUMNS",
    "OOS_SCORE_COLUMNS",
    "MODEL_PREDICTION_CANONICAL_COLUMNS",
    "PREDICTION_FILL_RELATIONSHIP_COLUMNS",
    "SELECTOR_PREDICTION_CANONICAL_COLUMNS",
    "SELECTOR_PREDICTION_GLOBAL_COLUMNS",
    "SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS",
    "SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS",
    "REQUIRED_ACTIVE_PIN_PATHS",
    "THREE_RANK_ALL_HEADS",
    "THREE_RANK_BEHAVIOR_PIN_PATHS",
    "THREE_RANK_CONTRACT_VERSION",
    "THREE_RANK_CORE_HEADS",
    "THREE_RANK_DYNAMIC_ASSET_PATHS",
    "THREE_RANK_FEATURE_CONTRACT",
    "THREE_RANK_FREEZE_SCHEMA_VERSION",
    "THREE_RANK_HISTORY_SOURCE_PIN_PATHS",
    "THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS",
    "THREE_RANK_RUNTIME_FEATURE_COLUMNS",
    "THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION",
    "THREE_RANK_RELEASE_MODES",
    "THREE_RANK_TOP_N",
    "THREE_RANK_VALIDATION_SCHEMA_VERSION",
    "TOP10_DISCRETE_BEHAVIOR_COLUMNS",
    "TOP10_SCORE_COLUMNS",
    "apply_frozen_history_cutoff",
    "capture_frozen_history_snapshot",
    "compute_action_watchlist_fingerprint",
    "compute_behavior_fingerprints",
    "frame_columns_sha256",
    "history_snapshot_bootstrap_mode",
    "load_frozen_history_snapshot",
    "load_model_freeze",
    "load_verified_frozen_history_snapshot",
    "model_freeze_active",
    "validate_behavior_artifacts",
    "validate_action_plan_artifact",
    "validate_pinned_files",
    "validate_production_three_rank_contract",
    "validate_runtime_artifacts",
]
