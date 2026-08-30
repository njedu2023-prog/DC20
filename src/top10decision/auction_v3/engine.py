from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from top10decision.data.tushare_minute import opening_auction_price_from_snapshot
from top10decision.decision.contracts import (
    HISTORY_CONTRACT_VERSION,
    PREOPEN_AUCTION_GATE_AUDIT,
)
from top10decision.decision.canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_execution_projection,
    canonical_frame_fingerprint,
    canonical_mapping_sha256,
    compose_artifact_fingerprint,
)
from top10decision.decision.eligibility import filter_standard_limit_universe
from top10decision.decision.exit_policy import simulate_tplus1_exit
from top10decision.decision.observation import (
    observation_price_contract,
    rank_observation_rows,
)
from top10decision.decision.trade_selector import (
    TRADE_SELECTOR_CANONICAL_V2,
    TRADE_SELECTOR_FEATURE_CONTRACT,
    TRADE_SELECTOR_VERSION,
    TradeSelectorBundle,
    observation_top10_metrics,
    prepare_observation_top10,
    score_trade_selector,
    walkforward_trade_selector,
)
from top10decision.writers.io_contract import (
    choose_exec_date,
    choose_exit_date,
    is_a_share_trading_day,
    next_a_share_trading_day,
)

from .config import (
    AuctionV3Config,
    TARGET_HISTORY_DATES,
    TARGET_INDEPENDENT_OOS_DATES,
)
from .calibration import (
    ProbabilityCalibrator,
    chronological_calibration_split,
    fit_probability_calibrator,
    probability_metrics,
)
from .promotion_model import (
    PROMOTION_CONTEXT_FEATURES,
    PROMOTION_PRIOR_FEATURES,
    PROMOTION_SOURCE_FEATURES,
    attach_promotion_source_features,
    fit_promotion_blend,
    load_promotion_validation,
)


DATE_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
EPS = 1e-9
RUNTIME_CANONICAL_DECIMALS = 8
MODEL_CANONICAL_V2 = "decision_model_canonical_v2_raw_execution_q8_fingerprint"
MODEL_EXECUTABLE_POLICY_THRESHOLDS = (
    "max_big_loss_probability",
    "min_mean_return_lcb",
    "min_fill_probability",
    "min_exit_probability",
    "min_conservative_ev",
    "min_selection_score",
)


def _runtime_canonical_contract(layer: str) -> dict[str, Any]:
    return {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "layer": layer,
        "decimals": RUNTIME_CANONICAL_DECIMALS,
        "rounding": "decimal_string_half_even",
        "execution_mode": "raw_float64",
        "raw_execution_preserved": True,
    }

FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "source_rank": ("rank", "rank_v2", "排名"),
    "prior_probability": ("prob_final", "Probability", "prob", "p_limit_up_calibrated"),
    "strength_score": ("StrengthScore", "strength_score", "强度分"),
    "theme_boost": ("ThemeBoost", "theme_boost", "题材加成"),
    "final_score": ("final_score_v2", "final_score", "rank_score", "最终分"),
    "intraday_quality": ("intraday_quality_score", "limitup_quality_score", "分时质量"),
    "intraday_risk": ("intraday_risk_score", "intraday_soft_risk_score", "软风险"),
    "intraday_hard_risk": ("intraday_hard_risk_flag", "硬风险"),
    "auction_strength": ("auction_strength_score", "竞价强度"),
    "intraday_confidence": ("intraday_confidence_score",),
    "stage_quality": ("stage_quality_weight",),
    "stage_risk": ("stage_risk_weight", "stage_risk_penalty"),
    "stage_prior": ("stage_prior",),
    "limit_times": ("limit_times",),
    "open_board_count": ("open_board_count", "open_times"),
    "reseal_score": ("reseal_score",),
    "late_withdraw": ("late_withdraw_score",),
}

MODEL_FEATURES = [
    "source_rank",
    "prior_probability",
    "strength_score",
    "theme_boost",
    "final_score",
    "intraday_quality",
    "intraday_risk",
    "intraday_hard_risk",
    "auction_strength",
    "intraday_confidence",
    "stage_quality",
    "stage_risk",
    "stage_prior",
    "limit_times",
    "open_board_count",
    "limit_open_times",
    "limit_first_time_minutes",
    "limit_last_time_minutes",
    "limit_fd_amount_log",
    "limit_seal_to_amount",
    "limit_seal_to_float_mv",
    "reseal_score",
    "late_withdraw",
    "d_return",
    "d_range",
    "d_turnover_proxy",
    "d_amount_log",
    "d_amount_percentile",
    "d_turnover_rate",
    "d_volume_ratio",
    "d_float_mv_log",
    "order_to_d_amount",
    "order_to_float_mv",
    "is_hot_board",
    "board_rank",
    "board_limit_up_count",
    "limit_ratio",
    "market_median_return",
    "market_up_ratio",
    "market_return_dispersion",
    "relative_d_return",
    "minute_available",
    "minute_realized_vol",
    "minute_first_30m_return",
    "minute_last_30m_return",
    "minute_vwap_deviation",
    "minute_opening_volume_share",
    "minute_closing_volume_share",
    "minute_close_location",
    "path_days_observed",
    "path_data_coverage",
    "path_strength_latest",
    "path_strength_delta",
    "path_gap_slope",
    "path_first_seal_slope",
    "path_open_times_slope",
    "path_turnover_slope",
    "path_amount_log_slope",
    "path_seal_ratio_slope",
    "path_one_price_ratio",
    "path_weak_to_strong",
    "path_strong_to_weak",
    "path_acceleration_consensus",
    "path_divergence_reseal",
    "stage_pool_size",
    "focus_pool_size",
    "market_max_limit_times",
    "same_industry_stage_count",
    "stage_pool_share",
    "stage_recent_promotion_rate",
    "stage_recent_promotion_samples",
    "proposed_gap",
]
MARKET_SENTIMENT_FEATURES = [
    "market_equal_weight_return",
    "market_down_ratio",
    "market_strong_up_ratio",
    "market_strong_down_ratio",
    "market_limit_up_count_log",
    "market_limit_down_count_log",
    "market_limit_up_down_log_ratio",
    "market_failed_limit_up_rate",
    "market_reseal_rate",
    "market_prev_limit_up_mean_return",
    "market_prev_limit_up_positive_rate",
    "market_prev_limit_up_open_gap_mean",
    "market_focus_promotion_rate",
    "market_limit_up_industry_concentration",
    "market_limit_up_amount_top3_share",
    "market_amount_ratio_5d",
    "market_sentiment_score",
    "market_sentiment_delta",
]
MARKET_SENTIMENT_OUTPUT_FIELDS = MARKET_SENTIMENT_FEATURES + [
    "market_sentiment_coverage",
    "market_sentiment_acceleration",
    "market_sentiment_regime_code",
    "market_sentiment_regime_label",
    "market_sentiment_breadth_score",
    "market_sentiment_limit_ecology_score",
    "market_sentiment_promotion_score",
    "market_sentiment_profit_effect_score",
    "market_sentiment_liquidity_score",
    "market_eligible_stock_count",
    "market_limit_up_count",
    "market_limit_down_count",
    "market_touched_up_count",
    "market_failed_limit_up_count",
    "market_reseal_count",
    "market_prev_limit_up_sample",
    "market_2_to_3_promotion_rate",
    "market_2_to_3_promotion_samples",
    "market_3_to_4_promotion_rate",
    "market_3_to_4_promotion_samples",
    "market_focus_promotion_samples",
    "market_max_streak",
]
CONTINUATION_PATH_COHORT_FEATURES = [
    name for name in MODEL_FEATURES if name != "proposed_gap"
]
CONTINUATION_FEATURES = (
    CONTINUATION_PATH_COHORT_FEATURES + MARKET_SENTIMENT_FEATURES
)
CONTINUATION_FIVE_YEAR_PRIOR_FEATURES = (
    CONTINUATION_FEATURES + PROMOTION_PRIOR_FEATURES
)
CONTINUATION_SOURCE_CONTEXT_FEATURES = (
    CONTINUATION_FEATURES + PROMOTION_CONTEXT_FEATURES
)
CONTINUATION_FIVE_YEAR_FULL_FEATURES = (
    CONTINUATION_FEATURES + PROMOTION_SOURCE_FEATURES
)
STREAK_PATH_FEATURES = [
    "path_days_observed",
    "path_data_coverage",
    "path_strength_latest",
    "path_strength_delta",
    "path_gap_slope",
    "path_first_seal_slope",
    "path_open_times_slope",
    "path_turnover_slope",
    "path_amount_log_slope",
    "path_seal_ratio_slope",
    "path_one_price_ratio",
    "path_weak_to_strong",
    "path_strong_to_weak",
    "path_acceleration_consensus",
    "path_divergence_reseal",
]
COHORT_FEATURES = [
    "stage_pool_size",
    "focus_pool_size",
    "market_max_limit_times",
    "same_industry_stage_count",
    "stage_pool_share",
    "stage_recent_promotion_rate",
    "stage_recent_promotion_samples",
]
CONTINUATION_BASELINE_FEATURES = [
    name
    for name in CONTINUATION_PATH_COHORT_FEATURES
    if name not in set(STREAK_PATH_FEATURES + COHORT_FEATURES)
]

INDUSTRY_ALIASES = ("industry", "industry_tag", "行业", "行业板块", "board")
PATH_LABELS = {
    "WEAK_TO_STRONG": "弱转强",
    "STRONG_TO_WEAK": "强转弱",
    "ACCELERATION_CONSENSUS": "加速一致",
    "DIVERGENCE_RESEAL": "分歧回封",
    "STABLE_STRONG": "持续强势",
    "MIXED": "路径混合",
    "INSUFFICIENT": "路径数据不足",
}
SENTIMENT_REGIME_LABELS = {
    "ICE": "冰点",
    "REPAIR": "修复",
    "NEUTRAL": "震荡",
    "EXPANSION": "发酵",
    "EUPHORIA": "高潮",
    "HIGH_DIVERGENCE": "高位分歧",
    "EBB": "退潮",
    "INSUFFICIENT": "数据不足",
}


@dataclass
class RunResult:
    signal_date: str
    prediction_path: str
    backtest_path: str
    verification_path: str
    current_report_path: str
    verification_report_path: str
    dashboard_path: str
    model_ready: bool
    promoted: bool
    selected_count: int
    warnings: list[str]


@dataclass
class ModelBundle:
    return_model: Optional[Pipeline]
    return_constant: float
    profit_model: Optional[Pipeline]
    loss_model: Optional[Pipeline]
    continuation_model: Optional[Any]
    fill_model: Optional[Pipeline]
    exit_model: Optional[Pipeline]
    profit_calibrator: ProbabilityCalibrator
    loss_calibrator: ProbabilityCalibrator
    continuation_calibrator: ProbabilityCalibrator
    fill_calibrator: ProbabilityCalibrator
    exit_calibrator: ProbabilityCalibrator
    profit_constant: float
    loss_constant: float
    continuation_constant: float
    fill_constant: float
    exit_constant: float
    calibration_bias: float
    expected_return_margin: float
    residual_q10: float
    residual_q90: float
    gap_min: float
    gap_max: float
    train_rows: int
    train_dates: int
    calibration_rows: int
    calibration_dates: int
    return_selection: dict[str, Any]
    classifier_selection: dict[str, dict[str, Any]]
    probability_quality_gate: dict[str, Any]
    selection_policy: dict[str, Any]
    stage_recent_rates: dict[int, float]
    stage_recent_samples: dict[int, int]
    continuation_stage_logit_adjustments: dict[int, float]
    continuation_features: tuple[str, ...]
    conformal_residual_quantiles: dict[str, dict[str, float]]
    model_artifact_sha256: str
    model_artifact_v2_sha256: str = ""
    model_fingerprint_v2: dict[str, Any] = field(default_factory=dict)
    runtime_canonical_contract: dict[str, Any] = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normal_date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        left, right = text.split(".", 1)
        return f"{left.zfill(6)}.{right}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        symbol = digits[-6:]
        suffix = "SH" if symbol.startswith(("5", "6", "9")) else "BJ" if symbol.startswith(("4", "8")) else "SZ"
        return f"{symbol}.{suffix}"
    return text


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False)
        except Exception:
            continue
    return pd.DataFrame()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _write_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except Exception:
        return default
    return result if math.isfinite(result) else default


def _safe_metric(value: Any) -> Optional[float]:
    result = _finite(value)
    return round(result, 10) if math.isfinite(result) else None


def _numeric_from(row: pd.Series, aliases: Sequence[str], default: float = float("nan")) -> float:
    for name in aliases:
        if name not in row.index:
            continue
        value = _finite(row.get(name))
        if math.isfinite(value):
            return value
    return default


def _text_from(row: pd.Series, aliases: Sequence[str], default: str = "") -> str:
    for name in aliases:
        if name not in row.index:
            continue
        value = row.get(name)
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        text = str(value or "").strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return text
    return default


def _pre_close(row: Optional[pd.Series]) -> float:
    if row is None:
        return float("nan")
    direct = _numeric_from(row, ("pre_close", "pre_close_est"))
    if direct > 0:
        return direct
    close_price = _numeric_from(row, ("close",))
    pct_chg = _numeric_from(row, ("pct_chg",))
    denominator = 1.0 + pct_chg / 100.0
    if close_price > 0 and math.isfinite(pct_chg) and denominator > 0:
        return close_price / denominator
    return float("nan")


def _is_close(left: Any, right: Any, tolerance: float = 0.0025) -> bool:
    a, b = _finite(left), _finite(right)
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= max(0.01, abs(b) * tolerance)


def _round_price(value: float) -> float:
    return round(max(0.01, value) + 1e-9, 2)


def _time_to_minutes(value: Any) -> float:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not text:
        return float("nan")
    text = text[-6:].zfill(6)
    hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:6])
    if hour > 23 or minute > 59 or second > 59:
        return float("nan")
    return hour * 60.0 + minute + second / 60.0


def _hash_frame(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _model_artifact_sha256(
    frame: pd.DataFrame,
    config: AuctionV3Config,
) -> str:
    columns = list(
        dict.fromkeys(
            [
                "signal_date",
                "buy_date",
                "target_exit_date",
                "ts_code",
                "net_return",
                "profit_hit",
                "big_loss_hit",
                "continuation_limit_up_hit",
                "exit_on_time",
                "market_fill",
                *MODEL_FEATURES,
                *MARKET_SENTIMENT_FEATURES,
                *PROMOTION_SOURCE_FEATURES,
            ]
        )
    )
    available = [name for name in columns if name in frame.columns]
    training = frame[available].copy()
    sort_columns = [
        name
        for name in ("signal_date", "ts_code", "proposed_gap")
        if name in training.columns
    ]
    if sort_columns:
        training = training.sort_values(sort_columns, kind="stable")
    source_hasher = hashlib.sha256()
    for name in (
        "engine.py",
        "calibration.py",
        "config.py",
        "promotion_model.py",
    ):
        source_hasher.update((Path(__file__).with_name(name)).read_bytes())
    promotion_validation = (
        config.root / "models" / "decision_promotion_v13_validation.json"
    )
    if promotion_validation.exists():
        source_hasher.update(promotion_validation.read_bytes())
    config_payload = {
        key: value
        for key, value in asdict(config).items()
        if key != "root"
    }
    payload = {
        "model_version": config.model_version,
        "training_sha256": _hash_frame(training.reset_index(drop=True)),
        "source_sha256": source_hasher.hexdigest(),
        "config": config_payload,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _model_executable_policy_projection(
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project only values that can change first-layer decisions.

    Values remain the original raw-float execution values.  Eight-decimal
    normalization happens only inside the canonical hasher; diagnostics,
    checks, and reported metrics cannot rotate this policy fingerprint.
    """

    if not isinstance(policy, Mapping):
        raise ValueError("model executable policy must be a mapping")
    source = dict(policy)
    version = source.get("version")
    reason = source.get("reason")
    ready = source.get("ready")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("model executable policy version is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("model executable policy reason is required")
    if not isinstance(ready, bool):
        raise ValueError("model executable policy ready must be boolean")
    raw_positions = source.get("max_positions")
    try:
        positions = float(raw_positions)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("model executable policy max_positions is invalid") from exc
    if (
        not math.isfinite(positions)
        or not positions.is_integer()
        or positions < 0
    ):
        raise ValueError("model executable policy max_positions must be a nonnegative integer")
    raw_thresholds = source.get("thresholds")
    if not isinstance(raw_thresholds, Mapping):
        raise ValueError("model executable policy thresholds must be a mapping")
    thresholds: dict[str, float] = {}
    for name in MODEL_EXECUTABLE_POLICY_THRESHOLDS:
        if name not in raw_thresholds:
            raise ValueError(f"model executable policy missing threshold: {name}")
        try:
            number = float(raw_thresholds[name])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"model executable policy threshold invalid: {name}") from exc
        if not math.isfinite(number):
            raise ValueError(f"model executable policy threshold non-finite: {name}")
        thresholds[name] = number
    return {
        "version": version,
        "ready": ready,
        "reason": reason,
        "max_positions": int(positions),
        "thresholds": thresholds,
    }


def _model_policy_fingerprint_v2(
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw_projection = _model_executable_policy_projection(policy)
    projection = canonical_execution_projection(
        raw_projection,
        decimals=RUNTIME_CANONICAL_DECIMALS,
    )
    return {
        "sha256": canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=RUNTIME_CANONICAL_DECIMALS,
            exact_strings=True,
        ),
        "projection": projection,
    }


def _model_fingerprint_v2(
    frame: pd.DataFrame,
    config: AuctionV3Config,
    selection_policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    columns = list(
        dict.fromkeys(
            [
                "signal_date",
                "buy_date",
                "target_exit_date",
                "ts_code",
                "actual_buy_gap",
                "gross_return",
                "net_return",
                "profit_hit",
                "big_loss_hit",
                "continuation_limit_up_hit",
                "exit_on_time",
                "market_fill",
                "market_sentiment_regime_code",
                *MODEL_FEATURES,
                *MARKET_SENTIMENT_FEATURES,
                *PROMOTION_SOURCE_FEATURES,
            ]
        )
    )
    kinds = {column: "float" for column in columns}
    kinds.update(
        {
            "signal_date": "date",
            "buy_date": "date",
            "target_exit_date": "date",
            "ts_code": "code",
            "market_sentiment_regime_code": "exact_text",
            "profit_hit": "integer",
            "big_loss_hit": "integer",
            "continuation_limit_up_hit": "integer",
            "exit_on_time": "integer",
            "market_fill": "integer",
        }
    )
    semantic = canonical_frame_fingerprint(
        frame,
        columns,
        decimals=RUNTIME_CANONICAL_DECIMALS,
        kinds=kinds,
        strict=True,
    )
    source_paths = (
        Path(__file__),
        Path(__file__).with_name("calibration.py"),
        Path(__file__).with_name("config.py"),
        Path(__file__).with_name("promotion_model.py"),
        Path(__file__).parents[1] / "decision" / "canonical_fingerprint.py",
    )
    source_hashes = {
        str(path.relative_to(Path(__file__).parents[2])): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in source_paths
    }
    validation = config.root / "models" / "decision_promotion_v13_validation.json"
    frozen_inputs: dict[str, str] = {}
    if validation.is_file():
        frozen_inputs[str(validation.relative_to(config.root))] = hashlib.sha256(
            validation.read_bytes()
        ).hexdigest()
    contract = _runtime_canonical_contract("model")
    provenance = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": MODEL_CANONICAL_V2,
        "canonical_contract": contract,
        "model_version": config.model_version,
        "source_files": source_hashes,
        "frozen_inputs": frozen_inputs,
        "config": {
            key: value for key, value in asdict(config).items() if key != "root"
        },
    }
    provenance_sha256 = canonical_mapping_sha256(
        provenance,
        decimals=RUNTIME_CANONICAL_DECIMALS,
        exact_strings=True,
    )
    policy_fingerprint = _model_policy_fingerprint_v2(selection_policy)
    policy_projection = policy_fingerprint["projection"]
    policy_sha256 = policy_fingerprint["sha256"]
    artifact_sha256 = compose_artifact_fingerprint(
        artifact_kind="decision_model_canonical_runtime_v2",
        provenance_sha256=provenance_sha256,
        semantic_sha256=semantic["sha256"],
        policy_sha256=policy_sha256,
        decimals=RUNTIME_CANONICAL_DECIMALS,
    )
    return {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": MODEL_CANONICAL_V2,
        "canonical_contract": contract,
        "provenance_sha256": provenance_sha256,
        "semantic_sha256": semantic["sha256"],
        "policy_sha256": policy_sha256,
        "policy_projection": policy_projection,
        "artifact_sha256": artifact_sha256,
        "schema_valid": semantic["valid"],
        "missing_columns": semantic["missing_columns"],
        "invalid_cell_count": semantic["invalid_cell_count"],
    }


def _finalize_model_bundle_v2(
    bundle: ModelBundle,
    frame: pd.DataFrame,
    config: AuctionV3Config,
) -> None:
    """Mint strict V2 metadata for the single final production bundle."""

    bundle.runtime_canonical_contract = _runtime_canonical_contract("model")
    fingerprint = _model_fingerprint_v2(
        frame,
        config,
        bundle.selection_policy,
    )
    bundle.model_fingerprint_v2 = fingerprint
    bundle.model_artifact_v2_sha256 = fingerprint["artifact_sha256"]


def _probability(
    model: Optional[Any],
    frame: pd.DataFrame,
    constant: float,
    features: Sequence[str] = MODEL_FEATURES,
    calibrator: Optional[ProbabilityCalibrator] = None,
) -> np.ndarray:
    if model is None:
        return np.repeat(float(np.clip(constant, 0.0, 1.0)), len(frame))
    raw = np.clip(
        model.predict_proba(frame[list(features)])[:, 1],
        0.0,
        1.0,
    )
    return calibrator.transform(raw) if calibrator is not None else raw


def _date_balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty or "signal_date" not in frame.columns:
        return np.ones(len(frame), dtype=float)
    date_key = frame["signal_date"].astype(str)
    counts = date_key.groupby(date_key).transform("count").clip(lower=1)
    weights = 1.0 / counts.astype(float)
    return (weights / weights.mean()).to_numpy(dtype=float)


def _weighted_quantile(
    values: Sequence[float] | np.ndarray,
    quantile: float,
    weights: Optional[Sequence[float] | np.ndarray] = None,
) -> float:
    sample = np.asarray(values, dtype=float)
    finite = np.isfinite(sample)
    if not finite.any():
        return float("nan")
    sample = sample[finite]
    if weights is None:
        return float(np.quantile(sample, quantile))
    weight = np.asarray(weights, dtype=float)[finite]
    valid = np.isfinite(weight) & (weight > 0)
    if not valid.any():
        return float(np.quantile(sample, quantile))
    sample = sample[valid]
    weight = weight[valid]
    order = np.argsort(sample)
    sample = sample[order]
    weight = weight[order]
    cumulative = np.cumsum(weight) - 0.5 * weight
    cumulative /= weight.sum()
    return float(np.interp(float(quantile), cumulative, sample))


class AuctionV3Engine:
    """Builds immutable predictions, walk-forward evidence, and matured truth ledgers."""

    def __init__(self, config: AuctionV3Config):
        self.config = config
        self.config.ensure_directories()
        self._market_dates_cache: Optional[list[str]] = None
        self._market_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._minute_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._context_cache: dict[str, dict[str, Any]] = {}
        self._eligible_market_cache: dict[str, pd.DataFrame] = {}
        self._sentiment_raw_cache: dict[str, dict[str, Any]] = {}
        self._streak_count_cache: dict[tuple[str, str], int] = {}
        self._path_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._promotion_context_cache: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._eligibility_audit: dict[str, Any] = {}
        self._trade_selector_bundle: Optional[TradeSelectorBundle] = None
        self._trade_selector_metrics: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Source discovery and point-in-time inputs
    # ------------------------------------------------------------------
    def market_dates(self) -> list[str]:
        if self._market_dates_cache is not None:
            return list(self._market_dates_cache)
        root = self.config.root / "data" / "market" / "raw"
        dates: set[str] = set()
        if root.exists():
            for path in root.rglob("daily.csv"):
                if re.fullmatch(r"20\d{6}", path.parent.name):
                    dates.add(path.parent.name)
            for path in root.glob("daily_*.csv"):
                match = DATE_RE.search(path.name)
                if match:
                    dates.add(match.group(1))
        # Raw folders can exist for a holiday because upstream sync jobs run on
        # weekdays. They are not evidence of an exchange session.
        self._market_dates_cache = sorted(
            date for date in dates if is_a_share_trading_day(date)
        )
        return list(self._market_dates_cache)

    def _market_path(self, trade_date: str, name: str) -> Optional[Path]:
        root = self.config.root / "data" / "market" / "raw"
        candidates = [
            root / trade_date[:4] / trade_date / f"{name}.csv",
            root / trade_date / f"{name}.csv",
            root / f"{name}_{trade_date}.csv",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def market_table(self, trade_date: str, name: str) -> pd.DataFrame:
        key = (trade_date, name)
        if key in self._market_cache:
            return self._market_cache[key]
        path = self._market_path(trade_date, name)
        frame = _read_csv(path) if path else pd.DataFrame()
        if not frame.empty and "trade_date" in frame.columns:
            source_dates = frame["trade_date"].map(_normal_date)
            frame = frame[source_dates.eq(trade_date)].copy()
            if not frame.empty:
                frame["trade_date"] = trade_date
        if not frame.empty and "ts_code" in frame.columns:
            frame = frame.copy()
            frame["ts_code"] = frame["ts_code"].map(_normal_code)
            frame = frame.drop_duplicates("ts_code", keep="last").set_index("ts_code", drop=False)
        self._market_cache[key] = frame
        return frame

    def _minute_path(self, trade_date: str, code: str) -> Path:
        safe_code = _normal_code(code).replace(".", "_")
        return self.config.root / "data" / "market" / "minute_1m" / trade_date[:4] / trade_date / f"{safe_code}.csv"

    def minute_table(self, trade_date: str, code: str) -> pd.DataFrame:
        key = (trade_date, _normal_code(code))
        if key in self._minute_cache:
            return self._minute_cache[key]
        frame = _read_csv(self._minute_path(trade_date, code))
        if not frame.empty:
            frame = frame.copy()
            for column in ("open", "close", "high", "low", "vol", "amount"):
                if column in frame.columns:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if "time" in frame.columns:
                frame["time"] = frame["time"].astype(str).str.strip()
                frame = frame.sort_values("time").drop_duplicates("time", keep="last")
        self._minute_cache[key] = frame
        return frame

    @staticmethod
    def _daily_return_series(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(dtype=float)
        pct = (
            pd.to_numeric(frame["pct_chg"], errors="coerce") / 100.0
            if "pct_chg" in frame.columns
            else pd.Series(np.nan, index=frame.index, dtype=float)
        )
        if pct.notna().sum() < max(20, len(frame) // 2):
            close = (
                pd.to_numeric(frame["close"], errors="coerce")
                if "close" in frame.columns
                else pd.Series(np.nan, index=frame.index, dtype=float)
            )
            if "pre_close" in frame.columns:
                pre_close = pd.to_numeric(frame["pre_close"], errors="coerce")
            elif "pre_close_est" in frame.columns:
                pre_close = pd.to_numeric(frame["pre_close_est"], errors="coerce")
            else:
                pre_close = pd.Series(np.nan, index=frame.index, dtype=float)
            derived = close / pre_close.replace(0.0, np.nan) - 1.0
            pct = pct.where(pct.notna(), derived)
        return pct.replace([np.inf, -np.inf], np.nan)

    @staticmethod
    def _empty_market_close_snapshot(
        trade_date: str,
        status: str,
    ) -> dict[str, Any]:
        return {
            "trade_date": trade_date,
            "available": False,
            "status": status,
            "scope": "all_a_share_daily_close",
            "stock_count": 0,
            "return_coverage": 0.0,
            "up_count": 0,
            "down_count": 0,
            "flat_count": 0,
            "limit_up_count": 0,
            "classified_limit_up_count": 0,
            "industry_top10": [],
            "industry_counts": {},
        }

    def market_close_display_snapshot(
        self,
        trade_date: str,
    ) -> dict[str, Any]:
        """Build a full-market close snapshot for display, never model input."""
        trade_date = str(trade_date or "").strip()
        if not re.fullmatch(r"20\d{6}", trade_date):
            return self._empty_market_close_snapshot(
                trade_date,
                "INVALID_TRADE_DATE",
            )
        try:
            if not is_a_share_trading_day(trade_date):
                return self._empty_market_close_snapshot(
                    trade_date,
                    "EXCHANGE_CLOSED",
                )
        except RuntimeError:
            return self._empty_market_close_snapshot(
                trade_date,
                "TRADE_CALENDAR_UNAVAILABLE",
            )

        daily = self.market_table(trade_date, "daily")
        if daily.empty:
            return self._empty_market_close_snapshot(
                trade_date,
                "DAILY_CLOSE_UNAVAILABLE",
            )

        frame = daily.copy()
        returns = self._daily_return_series(frame)
        valid = returns.dropna()
        stock_count = int(len(frame))
        return_coverage = (
            float(len(valid) / stock_count)
            if stock_count
            else 0.0
        )
        snapshot = self._empty_market_close_snapshot(
            trade_date,
            (
                "FINAL_CLOSE"
                if len(valid) and return_coverage >= 0.80
                else "INCOMPLETE_DAILY_CLOSE"
            ),
        )
        snapshot.update(
            {
                "available": bool(
                    len(valid) and return_coverage >= 0.80
                ),
                "stock_count": stock_count,
                "return_coverage": return_coverage,
                "up_count": int((valid > 0.0).sum()),
                "down_count": int((valid < 0.0).sum()),
                "flat_count": int((valid == 0.0).sum()),
            }
        )
        if not snapshot["available"]:
            return snapshot

        detail = self.market_table(trade_date, "limit_list_d")
        detail_up = pd.DataFrame()
        if not detail.empty and "ts_code" in detail.columns:
            detail_up = detail.copy()
            if "limit_type" in detail_up.columns:
                limit_type = (
                    detail_up["limit_type"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )
                detail_up = detail_up[limit_type.eq("U")].copy()
            detail_up = detail_up.drop_duplicates("ts_code", keep="last")

        limit_up_codes: set[str] = set()
        if not detail_up.empty:
            limit_up_codes = set(
                detail_up["ts_code"].map(_normal_code).astype(str)
            )
        else:
            limits = self.market_table(trade_date, "stk_limit")
            if not limits.empty and "up_limit" in limits.columns:
                close = pd.to_numeric(
                    frame.get("close"),
                    errors="coerce",
                )
                up_limit = pd.to_numeric(
                    limits["up_limit"].reindex(frame.index),
                    errors="coerce",
                )
                tolerance = pd.Series(
                    np.maximum(
                        0.0051,
                        up_limit.abs().fillna(0.0) * 0.00005,
                    ),
                    index=frame.index,
                )
                closed_up = (
                    close.notna()
                    & up_limit.notna()
                    & close.sub(up_limit).abs().le(tolerance)
                )
                limit_up_codes = set(
                    frame.loc[closed_up, "ts_code"]
                    .map(_normal_code)
                    .astype(str)
                )

        industry_counts: dict[str, int] = {}
        if (
            limit_up_codes
            and not detail_up.empty
            and "industry" in detail_up.columns
        ):
            industry_frame = detail_up[
                detail_up["ts_code"].map(_normal_code).isin(limit_up_codes)
            ].copy()
            industries = (
                industry_frame["industry"]
                .fillna("")
                .astype(str)
                .str.strip()
            )
            industries = industries[
                industries.ne("")
                & ~industries.str.lower().isin(
                    {"nan", "none", "null", "未分类"}
                )
            ]
            if len(industries):
                counts = (
                    industries.value_counts()
                    .rename_axis("industry")
                    .reset_index(name="limit_up_count")
                    .sort_values(
                        ["limit_up_count", "industry"],
                        ascending=[False, True],
                        kind="mergesort",
                    )
                )
                industry_counts = {
                    str(row.industry): int(row.limit_up_count)
                    for row in counts.itertuples(index=False)
                }

        limit_up_count = len(limit_up_codes)
        industry_top10 = [
            {
                "rank": rank,
                "industry": industry,
                "limit_up_count": count,
                "share": (
                    float(count / limit_up_count)
                    if limit_up_count
                    else 0.0
                ),
            }
            for rank, (industry, count) in enumerate(
                list(industry_counts.items())[:10],
                start=1,
            )
        ]
        snapshot.update(
            {
                "limit_up_count": limit_up_count,
                "classified_limit_up_count": int(
                    sum(industry_counts.values())
                ),
                "industry_top10": industry_top10,
                "industry_counts": industry_counts,
            }
        )
        return snapshot

    def _eligible_market_daily(self, trade_date: str) -> pd.DataFrame:
        if trade_date in self._eligible_market_cache:
            return self._eligible_market_cache[trade_date]
        daily = self.market_table(trade_date, "daily")
        if daily.empty:
            self._eligible_market_cache[trade_date] = pd.DataFrame()
            return self._eligible_market_cache[trade_date]
        frame = daily.reset_index(drop=True).copy()
        limit = self.market_table(trade_date, "stk_limit")
        if not limit.empty:
            limit_columns = [
                name
                for name in ("ts_code", "up_limit", "down_limit")
                if name in limit.columns
            ]
            frame = frame.merge(
                limit.reset_index(drop=True)[limit_columns],
                on="ts_code",
                how="left",
                suffixes=("", "_limit"),
            )
        if "name" not in frame.columns:
            stock_basic = self.market_table(trade_date, "stock_basic")
            if not stock_basic.empty and "name" in stock_basic.columns:
                names = (
                    stock_basic.reset_index(drop=True)
                    .drop_duplicates("ts_code", keep="last")
                    .set_index("ts_code")["name"]
                )
                frame["name"] = frame["ts_code"].map(names)
            else:
                frame["name"] = ""
        if "trade_date" not in frame.columns:
            frame["trade_date"] = trade_date
        eligible, _ = filter_standard_limit_universe(
            frame,
            code_col="ts_code",
            name_col="name",
        )
        if not eligible.empty:
            eligible["ts_code"] = eligible["ts_code"].map(_normal_code)
            eligible = (
                eligible.drop_duplicates("ts_code", keep="last")
                .set_index("ts_code", drop=False)
            )
        self._eligible_market_cache[trade_date] = eligible
        return eligible

    def _previous_market_date(self, trade_date: str) -> str:
        dates = self.market_dates()
        try:
            index = dates.index(trade_date)
        except ValueError:
            return ""
        if index <= 0:
            return ""
        previous = dates[index - 1]
        return (
            previous
            if next_a_share_trading_day(previous) == trade_date
            else ""
        )

    def _market_sentiment_raw(self, trade_date: str) -> dict[str, Any]:
        if trade_date in self._sentiment_raw_cache:
            return self._sentiment_raw_cache[trade_date]
        frame = self._eligible_market_daily(trade_date)
        empty = {
            "market_equal_weight_return": np.nan,
            "market_down_ratio": np.nan,
            "market_strong_up_ratio": np.nan,
            "market_strong_down_ratio": np.nan,
            "market_limit_up_count_log": np.nan,
            "market_limit_down_count_log": np.nan,
            "market_limit_up_down_log_ratio": np.nan,
            "market_failed_limit_up_rate": np.nan,
            "market_reseal_rate": np.nan,
            "market_prev_limit_up_mean_return": np.nan,
            "market_prev_limit_up_positive_rate": np.nan,
            "market_prev_limit_up_open_gap_mean": np.nan,
            "market_focus_promotion_rate": np.nan,
            "market_limit_up_industry_concentration": np.nan,
            "market_limit_up_amount_top3_share": np.nan,
            "market_amount_ratio_5d": np.nan,
            "market_eligible_stock_count": 0.0,
            "market_limit_up_count": 0.0,
            "market_limit_down_count": 0.0,
            "market_touched_up_count": 0.0,
            "market_failed_limit_up_count": 0.0,
            "market_reseal_count": 0.0,
            "market_prev_limit_up_sample": 0.0,
            "market_2_to_3_promotion_rate": np.nan,
            "market_2_to_3_promotion_samples": 0.0,
            "market_3_to_4_promotion_rate": np.nan,
            "market_3_to_4_promotion_samples": 0.0,
            "market_focus_promotion_samples": 0.0,
            "market_max_streak": np.nan,
            "_closed_up_codes": frozenset(),
            "_limit_up_industry_top10": [],
            "_limit_up_industry_top5": [],
            "_total_amount": np.nan,
        }
        if frame.empty:
            self._sentiment_raw_cache[trade_date] = empty
            return empty

        pct = self._daily_return_series(frame)
        valid = pct.dropna()
        def numeric_column(name: str) -> pd.Series:
            return (
                pd.to_numeric(frame[name], errors="coerce")
                if name in frame.columns
                else pd.Series(np.nan, index=frame.index, dtype=float)
            )

        close = numeric_column("close")
        high = numeric_column("high")
        open_price = numeric_column("open")
        pre_close = (
            numeric_column("pre_close")
            if "pre_close" in frame.columns
            else numeric_column("pre_close_est")
        )
        derived_pre_close = close / (1.0 + pct).replace(0.0, np.nan)
        pre_close = pre_close.where(
            pre_close.gt(0),
            derived_pre_close,
        )
        up_limit = numeric_column("up_limit")
        down_limit = numeric_column("down_limit")
        up_tolerance = pd.Series(
            np.maximum(0.01, up_limit.abs().fillna(0.0) * 0.0025),
            index=frame.index,
        )
        down_tolerance = pd.Series(
            np.maximum(0.01, down_limit.abs().fillna(0.0) * 0.0025),
            index=frame.index,
        )
        closed_up = (
            close.notna()
            & up_limit.notna()
            & close.sub(up_limit).abs().le(up_tolerance)
        )
        closed_down = (
            close.notna()
            & down_limit.notna()
            & close.sub(down_limit).abs().le(down_tolerance)
        )
        touched_up = (
            high.notna()
            & up_limit.notna()
            & high.ge(up_limit.sub(up_tolerance))
        )
        failed_up = touched_up & ~closed_up
        closed_up_codes = frozenset(frame.loc[closed_up, "ts_code"].astype(str))

        detail = self.market_table(trade_date, "limit_list_d")
        detail_up = (
            detail[detail["ts_code"].isin(closed_up_codes)].copy()
            if not detail.empty and "ts_code" in detail.columns
            else pd.DataFrame()
        )
        open_times = (
            pd.to_numeric(detail_up.get("open_times"), errors="coerce").dropna()
            if not detail_up.empty
            else pd.Series(dtype=float)
        )
        reseal_count = int((open_times > 0).sum()) if len(open_times) else 0
        reseal_rate = (
            float((open_times > 0).mean()) if len(open_times) else float("nan")
        )

        industry_concentration = float("nan")
        industry_top10: list[dict[str, Any]] = []
        if not detail_up.empty and "industry" in detail_up.columns:
            industry = (
                detail_up["industry"]
                .fillna("")
                .astype(str)
                .str.strip()
            )
            industry = industry[
                industry.ne("")
                & ~industry.str.lower().isin({"nan", "none", "null", "未分类"})
            ]
            if len(industry):
                industry_counts = (
                    industry.value_counts()
                    .rename_axis("industry")
                    .reset_index(name="limit_up_count")
                    .sort_values(
                        ["limit_up_count", "industry"],
                        ascending=[False, True],
                        kind="mergesort",
                    )
                )
                industry_total = int(industry_counts["limit_up_count"].sum())
                shares = industry_counts["limit_up_count"] / industry_total
                industry_concentration = float((shares**2).sum())
                industry_top10 = [
                    {
                        "rank": rank,
                        "industry": str(row.industry),
                        "limit_up_count": int(row.limit_up_count),
                        "share": float(row.limit_up_count / industry_total),
                    }
                    for rank, row in enumerate(
                        industry_counts.head(10).itertuples(index=False),
                        start=1,
                    )
                ]

        amount_top3_share = float("nan")
        limit_up_amount = (
            pd.to_numeric(detail_up.get("amount"), errors="coerce")
            if not detail_up.empty and "amount" in detail_up.columns
            else pd.Series(dtype=float)
        )
        limit_up_amount = limit_up_amount[limit_up_amount.gt(0)].dropna()
        if limit_up_amount.empty and closed_up_codes and "amount" in frame.columns:
            limit_up_amount = pd.to_numeric(
                frame.loc[list(closed_up_codes), "amount"],
                errors="coerce",
            )
            limit_up_amount = limit_up_amount[limit_up_amount.gt(0)].dropna()
        if len(limit_up_amount) and float(limit_up_amount.sum()) > 0:
            amount_top3_share = float(
                limit_up_amount.nlargest(3).sum() / limit_up_amount.sum()
            )

        previous_date = self._previous_market_date(trade_date)
        previous_raw = (
            self._market_sentiment_raw(previous_date)
            if previous_date
            else {}
        )
        previous_codes = set(previous_raw.get("_closed_up_codes") or ())
        previous_returns = (
            pct.reindex(list(previous_codes)).dropna()
            if previous_codes
            else pd.Series(dtype=float)
        )
        previous_open_gap = (
            (open_price / pre_close.replace(0.0, np.nan) - 1.0)
            .reindex(list(previous_codes))
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
            if previous_codes
            else pd.Series(dtype=float)
        )

        dates = self.market_dates()
        promotion_hits = {2: 0, 3: 0}
        promotion_samples = {2: 0, 3: 0}
        if previous_date and previous_codes:
            for code in previous_codes:
                stage = self._consecutive_limit_up_count(
                    previous_date,
                    code,
                    dates,
                )
                if stage not in promotion_samples:
                    continue
                promotion_samples[stage] += 1
                promotion_hits[stage] += int(code in closed_up_codes)
        focus_samples = promotion_samples[2] + promotion_samples[3]
        focus_hits = promotion_hits[2] + promotion_hits[3]

        streaks = [
            self._consecutive_limit_up_count(trade_date, code, dates)
            for code in closed_up_codes
        ]
        amount = (
            pd.to_numeric(frame["amount"], errors="coerce")
            if "amount" in frame.columns
            else pd.Series(dtype=float)
        )
        total_amount = float(amount[amount.gt(0)].sum()) if len(amount) else float("nan")
        try:
            date_index = dates.index(trade_date)
        except ValueError:
            date_index = -1
        trailing_amounts = []
        if date_index > 0:
            for prior_date in dates[max(0, date_index - 5) : date_index]:
                prior_total = _finite(
                    self._market_sentiment_raw(prior_date).get("_total_amount")
                )
                if prior_total > 0:
                    trailing_amounts.append(prior_total)
        amount_ratio_5d = (
            total_amount / float(np.mean(trailing_amounts))
            if total_amount > 0 and trailing_amounts
            else float("nan")
        )

        limit_up_count = int(closed_up.sum())
        limit_down_count = int(closed_down.sum())
        touched_up_count = int(touched_up.sum())
        failed_count = int(failed_up.sum())
        result = {
            **empty,
            "market_equal_weight_return": (
                float(valid.mean()) if len(valid) else float("nan")
            ),
            "market_down_ratio": (
                float((valid < 0).mean()) if len(valid) else float("nan")
            ),
            "market_strong_up_ratio": (
                float((valid >= 0.05).mean()) if len(valid) else float("nan")
            ),
            "market_strong_down_ratio": (
                float((valid <= -0.05).mean()) if len(valid) else float("nan")
            ),
            "market_limit_up_count_log": math.log1p(limit_up_count),
            "market_limit_down_count_log": math.log1p(limit_down_count),
            "market_limit_up_down_log_ratio": math.log(
                (limit_up_count + 1.0) / (limit_down_count + 1.0)
            ),
            "market_failed_limit_up_rate": (
                failed_count / touched_up_count
                if touched_up_count
                else float("nan")
            ),
            "market_reseal_rate": reseal_rate,
            "market_prev_limit_up_mean_return": (
                float(previous_returns.mean())
                if len(previous_returns)
                else float("nan")
            ),
            "market_prev_limit_up_positive_rate": (
                float((previous_returns > 0).mean())
                if len(previous_returns)
                else float("nan")
            ),
            "market_prev_limit_up_open_gap_mean": (
                float(previous_open_gap.mean())
                if len(previous_open_gap)
                else float("nan")
            ),
            "market_focus_promotion_rate": (
                focus_hits / focus_samples
                if focus_samples
                else float("nan")
            ),
            "market_limit_up_industry_concentration": industry_concentration,
            "market_limit_up_amount_top3_share": amount_top3_share,
            "market_amount_ratio_5d": amount_ratio_5d,
            "market_eligible_stock_count": float(len(valid)),
            "market_limit_up_count": float(limit_up_count),
            "market_limit_down_count": float(limit_down_count),
            "market_touched_up_count": float(touched_up_count),
            "market_failed_limit_up_count": float(failed_count),
            "market_reseal_count": float(reseal_count),
            "market_prev_limit_up_sample": float(len(previous_returns)),
            "market_2_to_3_promotion_rate": (
                promotion_hits[2] / promotion_samples[2]
                if promotion_samples[2]
                else float("nan")
            ),
            "market_2_to_3_promotion_samples": float(promotion_samples[2]),
            "market_3_to_4_promotion_rate": (
                promotion_hits[3] / promotion_samples[3]
                if promotion_samples[3]
                else float("nan")
            ),
            "market_3_to_4_promotion_samples": float(promotion_samples[3]),
            "market_focus_promotion_samples": float(focus_samples),
            "market_max_streak": (
                float(max(streaks)) if streaks else float("nan")
            ),
            "_closed_up_codes": closed_up_codes,
            "_limit_up_industry_top10": industry_top10,
            "_limit_up_industry_top5": industry_top10[:5],
            "_total_amount": total_amount,
        }
        self._sentiment_raw_cache[trade_date] = result
        return result

    @staticmethod
    def _linear_sentiment_score(value: Any, low: float, high: float) -> float:
        number = _finite(value)
        if not math.isfinite(number) or high <= low:
            return float("nan")
        return float(np.clip((number - low) / (high - low), 0.0, 1.0))

    def _sentiment_components(
        self,
        raw: dict[str, Any],
    ) -> dict[str, float]:
        breadth, _ = self._available_weighted_mean(
            (
                (
                    self._linear_sentiment_score(
                        raw.get("market_equal_weight_return"),
                        -0.03,
                        0.03,
                    ),
                    0.55,
                ),
                (
                    1.0 - _finite(raw.get("market_down_ratio")),
                    0.45,
                ),
            )
        )
        limit_ecology, _ = self._available_weighted_mean(
            (
                (
                    self._linear_sentiment_score(
                        raw.get("market_limit_up_down_log_ratio"),
                        -1.5,
                        3.0,
                    ),
                    0.40,
                ),
                (
                    1.0 - _finite(raw.get("market_failed_limit_up_rate")),
                    0.40,
                ),
                (_finite(raw.get("market_reseal_rate")), 0.20),
            )
        )
        promotion = _finite(raw.get("market_focus_promotion_rate"))
        profit_effect, _ = self._available_weighted_mean(
            (
                (
                    self._linear_sentiment_score(
                        raw.get("market_prev_limit_up_mean_return"),
                        -0.05,
                        0.05,
                    ),
                    0.60,
                ),
                (
                    _finite(raw.get("market_prev_limit_up_positive_rate")),
                    0.40,
                ),
            )
        )
        liquidity = self._linear_sentiment_score(
            raw.get("market_amount_ratio_5d"),
            0.75,
            1.25,
        )
        return {
            "market_sentiment_breadth_score": breadth,
            "market_sentiment_limit_ecology_score": limit_ecology,
            "market_sentiment_promotion_score": promotion,
            "market_sentiment_profit_effect_score": profit_effect,
            "market_sentiment_liquidity_score": liquidity,
        }

    def _sentiment_score(
        self,
        raw: dict[str, Any],
    ) -> tuple[float, float, dict[str, float]]:
        components = self._sentiment_components(raw)
        score, coverage = self._available_weighted_mean(
            (
                (components["market_sentiment_breadth_score"], 0.20),
                (components["market_sentiment_limit_ecology_score"], 0.20),
                (components["market_sentiment_promotion_score"], 0.25),
                (components["market_sentiment_profit_effect_score"], 0.25),
                (components["market_sentiment_liquidity_score"], 0.10),
            )
        )
        return score, coverage, components

    @staticmethod
    def _sentiment_regime(score: float, delta: float) -> str:
        if not math.isfinite(score):
            return "INSUFFICIENT"
        change = delta if math.isfinite(delta) else 0.0
        if score >= 0.70 and change <= -0.05:
            return "HIGH_DIVERGENCE"
        if change <= -0.08:
            return "EBB"
        if score < 0.28:
            return "REPAIR" if change >= 0.05 else "ICE"
        if score < 0.42:
            return "REPAIR" if change >= 0.04 else "EBB"
        if change >= 0.06:
            return "REPAIR" if score < 0.55 else "EXPANSION"
        if score >= 0.75:
            return "EUPHORIA"
        if score >= 0.58:
            return "EXPANSION"
        if change <= -0.04:
            return "EBB"
        return "NEUTRAL"

    def _market_context(self, trade_date: str) -> dict[str, Any]:
        if trade_date in self._context_cache:
            return self._context_cache[trade_date]
        daily = self.market_table(trade_date, "daily")
        if daily.empty:
            context = {
                "market_median_return": np.nan,
                "market_up_ratio": np.nan,
                "market_return_dispersion": np.nan,
                "amount_percentile": {},
                "market_sentiment_score": np.nan,
                "market_sentiment_delta": np.nan,
                "market_sentiment_coverage": 0.0,
                "market_sentiment_regime_code": "INSUFFICIENT",
                "market_sentiment_regime_label": SENTIMENT_REGIME_LABELS[
                    "INSUFFICIENT"
                ],
            }
            self._context_cache[trade_date] = context
            return context

        pct = self._daily_return_series(daily)
        valid = pct.dropna()
        amount = (
            pd.to_numeric(daily.get("amount"), errors="coerce")
            if "amount" in daily.columns
            else pd.Series(np.nan, index=daily.index, dtype=float)
        )
        raw = self._market_sentiment_raw(trade_date)
        sentiment_score, sentiment_coverage, components = self._sentiment_score(
            raw
        )
        previous_date = self._previous_market_date(trade_date)
        previous_score = float("nan")
        previous_delta = float("nan")
        if previous_date:
            previous_raw = self._market_sentiment_raw(previous_date)
            previous_score, _, _ = self._sentiment_score(previous_raw)
            previous_previous_date = self._previous_market_date(previous_date)
            if previous_previous_date:
                previous_previous_raw = self._market_sentiment_raw(
                    previous_previous_date
                )
                previous_previous_score, _, _ = self._sentiment_score(
                    previous_previous_raw
                )
                if (
                    math.isfinite(previous_score)
                    and math.isfinite(previous_previous_score)
                ):
                    previous_delta = previous_score - previous_previous_score
        sentiment_delta = (
            sentiment_score - previous_score
            if math.isfinite(sentiment_score) and math.isfinite(previous_score)
            else float("nan")
        )
        sentiment_acceleration = (
            sentiment_delta - previous_delta
            if math.isfinite(sentiment_delta) and math.isfinite(previous_delta)
            else float("nan")
        )
        regime_code = self._sentiment_regime(
            sentiment_score,
            sentiment_delta,
        )
        public_raw = {
            key: value
            for key, value in raw.items()
            if not key.startswith("_")
        }
        context = {
            "market_median_return": (
                float(valid.median()) if len(valid) else np.nan
            ),
            "market_up_ratio": (
                float((valid > 0).mean()) if len(valid) else np.nan
            ),
            "market_return_dispersion": (
                float(valid.std(ddof=0)) if len(valid) else np.nan
            ),
            "amount_percentile": amount.rank(pct=True).to_dict(),
            **public_raw,
            **components,
            "market_sentiment_score": sentiment_score,
            "market_sentiment_delta": sentiment_delta,
            "market_sentiment_acceleration": sentiment_acceleration,
            "market_sentiment_coverage": sentiment_coverage,
            "market_sentiment_regime_code": regime_code,
            "market_sentiment_regime_label": SENTIMENT_REGIME_LABELS[
                regime_code
            ],
        }
        self._context_cache[trade_date] = context
        return context

    def _minute_features(self, trade_date: str, code: str) -> dict[str, float]:
        defaults = {
            "minute_available": 0.0,
            "minute_realized_vol": np.nan,
            "minute_first_30m_return": np.nan,
            "minute_last_30m_return": np.nan,
            "minute_vwap_deviation": np.nan,
            "minute_opening_volume_share": np.nan,
            "minute_closing_volume_share": np.nan,
            "minute_close_location": np.nan,
        }
        frame = self.minute_table(trade_date, code)
        needed = {"time", "open", "close", "high", "low", "vol"}
        if frame.empty or not needed.issubset(frame.columns):
            return defaults
        clean = frame.dropna(subset=["open", "close", "high", "low"]).copy()
        if len(clean) < 5:
            return defaults

        time_text = clean["time"].astype(str)
        hhmm = pd.to_numeric(time_text.str.extract(r"(\d{2}):(\d{2})", expand=True).fillna("0").agg("".join, axis=1), errors="coerce")
        close = pd.to_numeric(clean["close"], errors="coerce")
        open_price = pd.to_numeric(clean["open"], errors="coerce")
        high = pd.to_numeric(clean["high"], errors="coerce")
        low = pd.to_numeric(clean["low"], errors="coerce")
        vol = pd.to_numeric(clean["vol"], errors="coerce").fillna(0.0).clip(lower=0.0)
        returns = np.log(close.replace(0.0, np.nan)).diff().replace([np.inf, -np.inf], np.nan).dropna()
        first = clean[hhmm.le(1000)]
        last = clean[hhmm.ge(1430)]
        total_vol = float(vol.sum())
        vwap = float((close * vol).sum() / total_vol) if total_vol > 0 else np.nan
        price_range = float(high.max() - low.min())

        defaults.update(
            {
                "minute_available": 1.0,
                "minute_realized_vol": float(returns.std(ddof=0)) if len(returns) else np.nan,
                "minute_first_30m_return": float(first["close"].iloc[-1] / first["open"].iloc[0] - 1.0) if len(first) else np.nan,
                "minute_last_30m_return": float(last["close"].iloc[-1] / last["open"].iloc[0] - 1.0) if len(last) else np.nan,
                "minute_vwap_deviation": float(close.iloc[-1] / vwap - 1.0) if vwap > 0 else np.nan,
                "minute_opening_volume_share": float(vol.loc[first.index].sum() / total_vol) if total_vol > 0 and len(first) else np.nan,
                "minute_closing_volume_share": float(vol.loc[last.index].sum() / total_vol) if total_vol > 0 and len(last) else np.nan,
                "minute_close_location": float((close.iloc[-1] - low.min()) / price_range) if price_range > 0 else 0.5,
            }
        )
        return defaults

    def _execution_open_price(self, trade_date: str, code: str, daily_row: Optional[pd.Series] = None) -> float:
        auction = self._auction_price(trade_date, code)
        if auction > 0:
            return auction
        minute = self.minute_table(trade_date, code)
        minute_open = opening_auction_price_from_snapshot(minute)
        if minute_open is not None and minute_open > 0:
            return float(minute_open)
        row = daily_row if daily_row is not None else self._row(self.market_table(trade_date, "daily"), code)
        return _numeric_from(row, ("open",)) if row is not None else float("nan")

    def candidate_snapshots(self) -> dict[str, Path]:
        pred_root = self.config.root / "data" / "pred"
        paths: list[Path] = []
        if pred_root.exists():
            paths.extend(pred_root.rglob("pred_source_*.csv"))
            paths.extend(pred_root.rglob("pred_decisio_*.csv"))
        selected: dict[str, Path] = {}
        for path in sorted(set(paths)):
            frame = _read_csv(path)
            if frame.empty:
                continue
            trade_date = ""
            if "trade_date" in frame.columns:
                values = frame["trade_date"].dropna().map(_normal_date)
                values = values[values.str.len() == 8]
                if not values.empty:
                    trade_date = values.iloc[0]
            if not trade_date:
                match = DATE_RE.search(path.name)
                trade_date = match.group(1) if match else ""
            if not trade_date:
                continue
            old = selected.get(trade_date)
            if old is None or ("archive" in path.parts and "archive" not in old.parts):
                selected[trade_date] = path
        latest = pred_root / "pred_source_latest.csv"
        if latest.exists():
            frame = _read_csv(latest)
            if not frame.empty and "trade_date" in frame.columns:
                td = _normal_date(frame["trade_date"].iloc[0])
                if td:
                    selected[td] = latest
        return dict(sorted(selected.items()))

    def load_candidates(self, signal_date: str, path: Optional[Path] = None) -> pd.DataFrame:
        path = path or self.candidate_snapshots().get(signal_date)
        source = _read_csv(path) if path else pd.DataFrame()
        if not source.empty:
            source = source.copy()
            source_code_col = next(
                (c for c in ("ts_code", "code", "代码") if c in source.columns),
                "",
            )
            if source_code_col:
                source["ts_code"] = source[source_code_col].map(_normal_code)
                source = source[source["ts_code"] != ""].drop_duplicates("ts_code", keep="first")

        authoritative = self.market_table(signal_date, "limit_list_d")
        if not authoritative.empty:
            frame = authoritative.reset_index(drop=True).copy()
            if "limit_type" in frame.columns:
                limit_type = frame["limit_type"].astype(str).str.upper().str.strip()
                frame = frame[limit_type.isin({"U", "UP", "涨停"}) | limit_type.eq("")]
            frame["ts_code"] = frame["ts_code"].map(_normal_code)
            frame = frame[frame["ts_code"] != ""].drop_duplicates("ts_code", keep="first")
            stock_basic = self.market_table(signal_date, "stock_basic")
            if not stock_basic.empty:
                enrich_columns = [name for name in ("name", "industry", "list_date") if name in stock_basic.columns]
                if enrich_columns:
                    frame = frame.set_index("ts_code", drop=False)
                    for name in enrich_columns:
                        values = stock_basic[name]
                        frame[name] = frame[name].where(frame[name].notna(), values) if name in frame.columns else values
                    frame = frame.reset_index(drop=True)
            if not source.empty and "ts_code" in source.columns:
                frame = frame.set_index("ts_code", drop=False)
                source = source.set_index("ts_code", drop=False)
                for name in source.columns:
                    if name == "ts_code":
                        continue
                    values = source[name]
                    if name in frame.columns:
                        frame[name] = values.combine_first(frame[name])
                    else:
                        frame[name] = values
                frame = frame.reset_index(drop=True)
        else:
            frame = source.copy()

        if frame.empty:
            return frame
        code_col = next((c for c in ("ts_code", "code", "代码") if c in frame.columns), "")
        if not code_col:
            return pd.DataFrame()
        frame["ts_code"] = frame[code_col].map(_normal_code)
        frame = frame[frame["ts_code"] != ""].drop_duplicates("ts_code", keep="first")
        frame, self._eligibility_audit = filter_standard_limit_universe(frame, code_col="ts_code", name_col="name")
        rank_col = next((c for c in ("rank", "rank_v2", "排名") if c in frame.columns), "")
        frame["_source_rank"] = pd.to_numeric(frame[rank_col], errors="coerce") if rank_col else np.nan
        missing_rank = frame["_source_rank"].isna()
        rank_start = int(frame["_source_rank"].max()) if frame["_source_rank"].notna().any() else 0
        frame.loc[missing_rank, "_source_rank"] = np.arange(
            rank_start + 1,
            rank_start + 1 + int(missing_rank.sum()),
        )
        frame = frame.sort_values("_source_rank", na_position="last")
        if self.config.max_candidates > 0:
            frame = frame.head(self.config.max_candidates)
        return frame.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Historical truth and feature construction
    # ------------------------------------------------------------------
    def _row(self, frame: pd.DataFrame, code: str) -> Optional[pd.Series]:
        if frame.empty or code not in frame.index:
            return None
        row = frame.loc[code]
        return row.iloc[-1] if isinstance(row, pd.DataFrame) else row

    def _limit_ratio(self, daily_row: Optional[pd.Series], limit_row: Optional[pd.Series]) -> float:
        if daily_row is not None and limit_row is not None:
            pre_close = _pre_close(daily_row)
            up_limit = _numeric_from(limit_row, ("up_limit",))
            if pre_close > 0 and up_limit > 0:
                ratio = up_limit / pre_close - 1.0
                if 0.03 <= ratio <= 0.35:
                    return ratio
        return 0.10

    def _consecutive_limit_up_count(
        self,
        signal_date: str,
        code: str,
        dates: Optional[Sequence[str]] = None,
    ) -> int:
        """Count consecutive close-at-limit sessions ending on D without using future data."""
        cache_key = (signal_date, _normal_code(code))
        if cache_key in self._streak_count_cache:
            return self._streak_count_cache[cache_key]
        trading_dates = list(dates or self.market_dates())
        try:
            index = trading_dates.index(signal_date)
        except ValueError:
            return 0
        count = 0
        newer_date = ""
        for position in range(index, -1, -1):
            trade_date = trading_dates[position]
            if newer_date and next_a_share_trading_day(trade_date) != newer_date:
                break
            daily = self._row(self.market_table(trade_date, "daily"), code)
            limit = self._row(self.market_table(trade_date, "stk_limit"), code)
            up_limit = _numeric_from(limit, ("up_limit",)) if limit is not None else float("nan")
            if daily is None or not math.isfinite(up_limit) or not _is_close(daily.get("close"), up_limit):
                break
            count += 1
            newer_date = trade_date
        self._streak_count_cache[cache_key] = count
        return count

    @staticmethod
    def _path_slope(values: Sequence[Any]) -> float:
        points = [
            (index, _finite(value))
            for index, value in enumerate(values)
            if math.isfinite(_finite(value))
        ]
        if len(points) < 2:
            return float("nan")
        first_index, first_value = points[0]
        last_index, last_value = points[-1]
        distance = last_index - first_index
        return (last_value - first_value) / distance if distance > 0 else float("nan")

    @staticmethod
    def _available_weighted_mean(parts: Sequence[tuple[float, float]]) -> tuple[float, float]:
        available = [
            (value, weight)
            for value, weight in parts
            if math.isfinite(value) and weight > 0
        ]
        if not available:
            return float("nan"), 0.0
        available_weight = sum(weight for _, weight in available)
        total_weight = sum(weight for _, weight in parts if weight > 0)
        score = sum(value * weight for value, weight in available) / available_weight
        coverage = available_weight / total_weight if total_weight > 0 else 0.0
        return float(np.clip(score, 0.0, 1.0)), float(np.clip(coverage, 0.0, 1.0))

    def _limit_session_path_snapshot(self, trade_date: str, code: str) -> dict[str, float]:
        daily = self._row(self.market_table(trade_date, "daily"), code)
        limit = self._row(self.market_table(trade_date, "stk_limit"), code)
        detail = self._row(self.market_table(trade_date, "limit_list_d"), code)
        basic = self._row(self.market_table(trade_date, "daily_basic"), code)
        if daily is None:
            return {}

        pre_close = _pre_close(daily)
        open_price = _numeric_from(daily, ("open",))
        amount_yuan = _numeric_from(detail, ("amount",)) if detail is not None else float("nan")
        if not math.isfinite(amount_yuan) or amount_yuan <= 0:
            daily_amount = _numeric_from(daily, ("amount",))
            amount_yuan = daily_amount * 1_000.0 if daily_amount > 0 else float("nan")
        limit_ratio = self._limit_ratio(daily, limit)
        open_gap = open_price / pre_close - 1.0 if open_price > 0 and pre_close > 0 else float("nan")
        open_times = _numeric_from(detail, ("open_times",)) if detail is not None else float("nan")
        first_seal = _time_to_minutes(detail.get("first_time")) if detail is not None else float("nan")
        last_seal = _time_to_minutes(detail.get("last_time")) if detail is not None else float("nan")
        seal_amount = (
            _numeric_from(detail, ("seal_amount", "fd_amount"))
            if detail is not None
            else float("nan")
        )
        seal_ratio = (
            seal_amount / amount_yuan
            if seal_amount > 0 and amount_yuan > 0
            else float("nan")
        )
        turnover_rate = _numeric_from(basic, ("turnover_rate",)) if basic is not None else float("nan")
        turnover = turnover_rate / 100.0 if math.isfinite(turnover_rate) else float("nan")

        opening_component = (
            float(np.clip((open_gap / max(limit_ratio, 0.03) + 0.25) / 1.25, 0.0, 1.0))
            if math.isfinite(open_gap)
            else float("nan")
        )
        first_seal_component = (
            float(np.clip((900.0 - first_seal) / 330.0, 0.0, 1.0))
            if math.isfinite(first_seal)
            else float("nan")
        )
        stability_component = (
            1.0 / (1.0 + max(0.0, open_times))
            if math.isfinite(open_times)
            else float("nan")
        )
        seal_component = (
            float(np.clip(math.log1p(100.0 * seal_ratio) / math.log(11.0), 0.0, 1.0))
            if math.isfinite(seal_ratio) and seal_ratio >= 0
            else float("nan")
        )
        strength, coverage = self._available_weighted_mean(
            (
                (opening_component, 0.25),
                (first_seal_component, 0.30),
                (stability_component, 0.20),
                (seal_component, 0.25),
            )
        )
        return {
            "trade_date": trade_date,
            "open_gap": open_gap,
            "first_seal": first_seal,
            "last_seal": last_seal,
            "open_times": open_times,
            "turnover": turnover,
            "amount_log": math.log1p(amount_yuan) if amount_yuan > 0 else float("nan"),
            "seal_ratio": seal_ratio,
            "one_price": float(self._one_price_limit(daily, limit, "up")),
            "strength": strength,
            "coverage": coverage,
        }

    def _streak_path_features(
        self,
        signal_date: str,
        code: str,
        dates: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        cache_key = (signal_date, _normal_code(code))
        if cache_key in self._path_cache:
            return dict(self._path_cache[cache_key])

        defaults: dict[str, Any] = {
            "path_days_observed": 0.0,
            "path_data_coverage": 0.0,
            "path_strength_latest": np.nan,
            "path_strength_delta": np.nan,
            "path_gap_slope": np.nan,
            "path_first_seal_slope": np.nan,
            "path_open_times_slope": np.nan,
            "path_turnover_slope": np.nan,
            "path_amount_log_slope": np.nan,
            "path_seal_ratio_slope": np.nan,
            "path_one_price_ratio": np.nan,
            "path_weak_to_strong": 0.0,
            "path_strong_to_weak": 0.0,
            "path_acceleration_consensus": 0.0,
            "path_divergence_reseal": 0.0,
            "path_label_code": "INSUFFICIENT",
            "path_label": PATH_LABELS["INSUFFICIENT"],
            "path_explanation": "连续涨停路径数据不足",
        }
        trading_dates = list(dates or self.market_dates())
        try:
            signal_index = trading_dates.index(signal_date)
        except ValueError:
            self._path_cache[cache_key] = defaults
            return dict(defaults)

        streak_count = self._consecutive_limit_up_count(signal_date, code, trading_dates)
        if streak_count <= 0:
            self._path_cache[cache_key] = defaults
            return dict(defaults)
        streak_dates = trading_dates[
            max(0, signal_index - streak_count + 1) : signal_index + 1
        ][-4:]
        snapshots = [
            self._limit_session_path_snapshot(trade_date, code)
            for trade_date in streak_dates
        ]
        snapshots = [item for item in snapshots if item]
        if not snapshots:
            self._path_cache[cache_key] = defaults
            return dict(defaults)

        strengths = [item.get("strength") for item in snapshots]
        latest_strength = _finite(strengths[-1])
        previous_strength = _finite(strengths[-2]) if len(strengths) >= 2 else float("nan")
        strength_delta = (
            latest_strength - previous_strength
            if math.isfinite(latest_strength) and math.isfinite(previous_strength)
            else float("nan")
        )
        slopes = {
            "path_gap_slope": self._path_slope([item.get("open_gap") for item in snapshots]),
            "path_first_seal_slope": self._path_slope([item.get("first_seal") for item in snapshots]),
            "path_open_times_slope": self._path_slope([item.get("open_times") for item in snapshots]),
            "path_turnover_slope": self._path_slope([item.get("turnover") for item in snapshots]),
            "path_amount_log_slope": self._path_slope([item.get("amount_log") for item in snapshots]),
            "path_seal_ratio_slope": self._path_slope([item.get("seal_ratio") for item in snapshots]),
        }
        coverage_values = [
            _finite(item.get("coverage"))
            for item in snapshots
            if math.isfinite(_finite(item.get("coverage")))
        ]
        one_price_values = [
            _finite(item.get("one_price"))
            for item in snapshots
            if math.isfinite(_finite(item.get("one_price")))
        ]
        coverage = float(np.mean(coverage_values)) if coverage_values else 0.0
        latest = snapshots[-1]
        label_code = "INSUFFICIENT"
        if (
            len(snapshots) >= 2
            and coverage >= 0.35
            and math.isfinite(strength_delta)
        ):
            gap_slope = _finite(slopes["path_gap_slope"], 0.0)
            first_seal_slope = _finite(slopes["path_first_seal_slope"], 0.0)
            open_times_slope = _finite(slopes["path_open_times_slope"], 0.0)
            acceleration_votes = sum(
                (
                    gap_slope >= 0.005,
                    first_seal_slope <= -10.0,
                    open_times_slope <= -0.5,
                )
            )
            if previous_strength >= 0.60 and latest_strength < 0.60 and strength_delta <= -0.12:
                label_code = "STRONG_TO_WEAK"
            elif previous_strength < 0.58 and latest_strength >= 0.58 and strength_delta >= 0.12:
                label_code = "WEAK_TO_STRONG"
            elif (
                previous_strength >= 0.58
                and latest_strength >= 0.70
                and strength_delta >= 0.05
                and acceleration_votes >= 2
            ):
                label_code = "ACCELERATION_CONSENSUS"
            elif (
                _finite(latest.get("open_times"), 0.0) >= 1.0
                and open_times_slope > 0.0
                and latest_strength >= 0.45
            ):
                label_code = "DIVERGENCE_RESEAL"
            elif (
                previous_strength >= 0.65
                and latest_strength >= 0.65
                and abs(strength_delta) < 0.12
            ):
                label_code = "STABLE_STRONG"
            else:
                label_code = "MIXED"

        explanation_parts: list[str] = []
        if math.isfinite(previous_strength) and math.isfinite(latest_strength):
            explanation_parts.append(f"路径强度{previous_strength:.2f}→{latest_strength:.2f}")
        if math.isfinite(slopes["path_gap_slope"]):
            explanation_parts.append(f"竞价斜率{slopes['path_gap_slope'] * 100:+.2f}pct/板")
        if math.isfinite(slopes["path_first_seal_slope"]):
            direction = "提前" if slopes["path_first_seal_slope"] < 0 else "推迟"
            explanation_parts.append(f"首封每板{direction}{abs(slopes['path_first_seal_slope']):.0f}分钟")
        if math.isfinite(slopes["path_open_times_slope"]):
            explanation_parts.append(f"炸板变化{slopes['path_open_times_slope']:+.1f}/板")

        result = {
            **defaults,
            "path_days_observed": float(len(snapshots)),
            "path_data_coverage": coverage,
            "path_strength_latest": latest_strength,
            "path_strength_delta": strength_delta,
            **slopes,
            "path_one_price_ratio": (
                float(np.mean(one_price_values)) if one_price_values else float("nan")
            ),
            "path_weak_to_strong": float(label_code == "WEAK_TO_STRONG"),
            "path_strong_to_weak": float(label_code == "STRONG_TO_WEAK"),
            "path_acceleration_consensus": float(label_code == "ACCELERATION_CONSENSUS"),
            "path_divergence_reseal": float(label_code == "DIVERGENCE_RESEAL"),
            "path_label_code": label_code,
            "path_label": PATH_LABELS[label_code],
            "path_explanation": "；".join(explanation_parts) or "连续涨停路径数据不足",
        }
        self._path_cache[cache_key] = result
        return dict(result)

    def _promotion_source_context_features(
        self,
        signal_date: str,
        code: str,
        dates: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Build the five-year challenger context from D and earlier only."""

        cache_key = (signal_date, _normal_code(code))
        if cache_key in self._promotion_context_cache:
            return dict(self._promotion_context_cache[cache_key])
        defaults = {
            feature: np.nan for feature in PROMOTION_CONTEXT_FEATURES
        }
        trading_dates = list(dates or self.market_dates())
        try:
            signal_index = trading_dates.index(signal_date)
        except ValueError:
            self._promotion_context_cache[cache_key] = defaults
            return dict(defaults)
        if signal_index < 5:
            self._promotion_context_cache[cache_key] = defaults
            return dict(defaults)

        stage = self._consecutive_limit_up_count(
            signal_date,
            code,
            trading_dates,
        )
        if stage not in (2, 3):
            self._promotion_context_cache[cache_key] = defaults
            return dict(defaults)

        window_dates = trading_dates[signal_index - 5 : signal_index + 1]
        closes: list[float] = []
        states: list[str] = []
        for trade_date in window_dates:
            daily = self._row(self.market_table(trade_date, "daily"), code)
            limit = self._row(self.market_table(trade_date, "stk_limit"), code)
            close = _numeric_from(daily, ("close",)) if daily is not None else float("nan")
            up_limit = _numeric_from(limit, ("up_limit",)) if limit is not None else float("nan")
            closes.append(close)
            states.append(
                "limit_up"
                if math.isfinite(close)
                and math.isfinite(up_limit)
                and _is_close(close, up_limit)
                else "other"
            )

        close_values = np.asarray(closes, dtype=float)
        pre_end = 5 - stage
        pre_closes = close_values[: pre_end + 1]
        valid_returns = np.asarray([], dtype=float)
        if len(pre_closes) >= 2:
            with np.errstate(divide="ignore", invalid="ignore"):
                returns = pre_closes[1:] / pre_closes[:-1] - 1.0
            valid_returns = returns[np.isfinite(returns)]
        pre_1d = (
            pre_closes[-1] / pre_closes[-2] - 1.0
            if len(pre_closes) >= 2
            and np.isfinite(pre_closes[-2:]).all()
            and pre_closes[-2] > 0
            else float("nan")
        )
        anchor_index = max(0, pre_end - 3)
        pre_3d = (
            close_values[pre_end] / close_values[anchor_index] - 1.0
            if np.isfinite(close_values[[pre_end, anchor_index]]).all()
            and close_values[anchor_index] > 0
            else float("nan")
        )
        prior_positions = [
            index
            for index, state in enumerate(states[: pre_end + 1])
            if state == "limit_up"
        ]
        result = {
            **defaults,
            "five_year_pre_streak_1d_return": pre_1d,
            "five_year_pre_streak_3d_return": pre_3d,
            "five_year_pre_streak_volatility": (
                float(np.std(valid_returns, ddof=0))
                if len(valid_returns)
                else float("nan")
            ),
            "five_year_pre_streak_limit_up_count": float(len(prior_positions)),
            "five_year_recent_limit_up_count": float(
                sum(state == "limit_up" for state in states)
            ),
            "five_year_days_since_prior_limit_up": (
                float(pre_end - prior_positions[-1] + 1)
                if prior_positions
                else 6.0
            ),
            "five_year_streak_runup": (
                close_values[-1] / close_values[pre_end] - 1.0
                if np.isfinite(close_values[[-1, pre_end]]).all()
                and close_values[pre_end] > 0
                else float("nan")
            ),
            "five_year_price_log": (
                float(np.log1p(close_values[-1]))
                if math.isfinite(close_values[-1]) and close_values[-1] > 0
                else float("nan")
            ),
        }
        self._promotion_context_cache[cache_key] = result
        return dict(result)

    @staticmethod
    def _attach_cohort_features(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        out = frame.copy()
        stage = pd.to_numeric(out.get("limit_times"), errors="coerce").round()
        industry = out.get("industry", pd.Series("", index=out.index)).fillna("").astype(str)
        date_key = (
            out["signal_date"].fillna("").astype(str)
            if "signal_date" in out.columns
            else pd.Series("__single_date__", index=out.index)
        )
        out["_cohort_date"] = date_key
        out["_cohort_stage"] = stage
        out["_cohort_industry"] = industry
        out["stage_pool_size"] = (
            out.groupby(["_cohort_date", "_cohort_stage"])["_cohort_stage"]
            .transform("size")
            .astype(float)
        )
        focus_mask = stage.isin((2.0, 3.0))
        out["focus_pool_size"] = (
            focus_mask.astype(float).groupby(date_key).transform("sum")
        )
        out["market_max_limit_times"] = stage.groupby(date_key).transform("max")
        out["same_industry_stage_count"] = (
            out.groupby(["_cohort_date", "_cohort_stage", "_cohort_industry"])["_cohort_stage"]
            .transform("size")
            .astype(float)
        )
        cohort_size = out.groupby("_cohort_date")["_cohort_date"].transform("size")
        out["stage_pool_share"] = out["stage_pool_size"] / cohort_size.clip(lower=1)
        if "stage_recent_promotion_rate" not in out.columns:
            out["stage_recent_promotion_rate"] = np.nan
        if "stage_recent_promotion_samples" not in out.columns:
            out["stage_recent_promotion_samples"] = 0.0
        return out.drop(columns=["_cohort_date", "_cohort_stage", "_cohort_industry"])

    @classmethod
    def _attach_point_in_time_stage_rates(cls, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "continuation_limit_up_hit" not in frame.columns:
            return frame
        out = frame.copy()
        out["_stage_key"] = pd.to_numeric(out.get("limit_times"), errors="coerce").round()
        daily = (
            out.dropna(subset=["_stage_key"])
            .groupby(["_stage_key", "signal_date"], as_index=False)
            .agg(
                promotion_hits=("continuation_limit_up_hit", "sum"),
                promotion_samples=("continuation_limit_up_hit", "size"),
            )
            .sort_values(["_stage_key", "signal_date"])
        )
        rate_lookup: dict[tuple[float, str], float] = {}
        sample_lookup: dict[tuple[float, str], float] = {}
        for stage, group in daily.groupby("_stage_key", sort=False):
            prior_hits = group["promotion_hits"].shift(1).rolling(20, min_periods=1).sum()
            prior_samples = group["promotion_samples"].shift(1).rolling(20, min_periods=1).sum()
            rates = prior_hits / prior_samples.replace(0.0, np.nan)
            for trade_date, rate, samples in zip(
                group["signal_date"].astype(str),
                rates,
                prior_samples,
            ):
                key = (float(stage), trade_date)
                rate_lookup[key] = _finite(rate)
                sample_lookup[key] = _finite(samples, 0.0)
        keys = list(zip(out["_stage_key"], out["signal_date"].astype(str)))
        out["stage_recent_promotion_rate"] = [rate_lookup.get(key, np.nan) for key in keys]
        out["stage_recent_promotion_samples"] = [sample_lookup.get(key, 0.0) for key in keys]
        return out.drop(columns=["_stage_key"])

    def _one_price_limit(self, daily_row: Optional[pd.Series], limit_row: Optional[pd.Series], side: str) -> bool:
        if daily_row is None or limit_row is None:
            return False
        limit_name = "up_limit" if side == "up" else "down_limit"
        limit_price = _numeric_from(limit_row, (limit_name,))
        if not math.isfinite(limit_price):
            return False
        return all(_is_close(daily_row.get(name), limit_price) for name in ("open", "high", "low", "close"))

    def _auction_row(
        self,
        trade_date: str,
        code: str,
    ) -> tuple[Optional[pd.Series], str]:
        official = self._row(
            self.market_table(trade_date, "stk_auction_o"),
            code,
        )
        if official is not None:
            return official, "tushare_stk_auction_o"
        legacy = self._row(
            self.market_table(trade_date, "stk_auction"),
            code,
        )
        if legacy is not None:
            return legacy, "market_repo_auction"
        return None, ""

    def _auction_amount(self, trade_date: str, code: str) -> float:
        row, _ = self._auction_row(trade_date, code)
        if row is None:
            return float("nan")
        return _numeric_from(row, ("amount", "auction_amount"))

    def _auction_price(self, trade_date: str, code: str) -> float:
        row, _ = self._auction_row(trade_date, code)
        if row is None:
            return float("nan")
        return _numeric_from(
            row,
            ("close", "price", "auction_price", "vwap", "open"),
        )

    def _execution_open_source(self, trade_date: str, code: str) -> str:
        _, source = self._auction_row(trade_date, code)
        if source:
            return source
        if not self.minute_table(trade_date, code).empty:
            return "tushare_minute_0930_proxy"
        if self._row(self.market_table(trade_date, "daily"), code) is not None:
            return "official_daily_open_proxy"
        return "missing"

    def _market_buyable(self, trade_date: str, code: str) -> tuple[int, str]:
        daily = self._row(self.market_table(trade_date, "daily"), code)
        limit = self._row(self.market_table(trade_date, "stk_limit"), code)
        if daily is None:
            return 0, "suspended_or_daily_missing"
        open_price = _numeric_from(daily, ("open",))
        execution_price = self._execution_open_price(trade_date, code, daily)
        if math.isfinite(execution_price) and open_price > 0 and abs(execution_price - open_price) > 0.011:
            return 0, "auction_daily_open_conflict"
        up_limit = _numeric_from(limit, ("up_limit",)) if limit is not None else float("nan")
        if math.isfinite(execution_price) and math.isfinite(up_limit) and abs(execution_price - up_limit) <= 0.011:
            return 0, "opening_auction_limit_up_unconfirmed"
        if self._one_price_limit(daily, limit, "up"):
            return 0, "one_price_limit_up"
        auction_amount = self._auction_amount(trade_date, code)
        if math.isfinite(auction_amount) and auction_amount > 0:
            capacity = auction_amount * self.config.max_auction_participation
            if capacity + EPS < self.config.order_amount_cny:
                return 0, "auction_capacity_insufficient"
        return 1, "market_proxy_buyable"

    def _realized_gross_return(
        self,
        code: str,
        buy_date: str,
        buy_price: float,
        exit_date: str,
        dates: Sequence[str],
        *,
        exit_price: float = float("nan"),
    ) -> float:
        """Chain close/pre-close returns so corporate actions do not create fake PnL."""
        if buy_price <= 0 or buy_date not in dates or exit_date not in dates:
            return float("nan")
        start, end = dates.index(buy_date), dates.index(exit_date)
        buy_row = self._row(self.market_table(buy_date, "daily"), code)
        if buy_row is None:
            return float("nan")
        buy_close = _numeric_from(buy_row, ("close",))
        if buy_close <= 0:
            return float("nan")
        wealth = buy_close / buy_price
        for idx in range(start + 1, end + 1):
            row = self._row(self.market_table(dates[idx], "daily"), code)
            if row is None:
                return float("nan")
            pre_close = _pre_close(row)
            if pre_close <= 0:
                return float("nan")
            if idx == end:
                realized_exit = exit_price if math.isfinite(exit_price) and exit_price > 0 else self._execution_open_price(dates[idx], code, row)
                if realized_exit <= 0:
                    return float("nan")
                wealth *= realized_exit / pre_close
            else:
                close_price = _numeric_from(row, ("close",))
                if close_price <= 0:
                    return float("nan")
                wealth *= close_price / pre_close
        return wealth - 1.0

    def _resolve_exit(
        self,
        code: str,
        start_index: int,
        dates: Sequence[str],
        *,
        entry_price: float = float("nan"),
        buy_date: str = "",
    ) -> tuple[str, float, int, str]:
        buy_daily = self._row(self.market_table(buy_date, "daily"), code) if buy_date else None
        buy_close = _numeric_from(buy_daily, ("close",)) if buy_daily is not None else float("nan")
        for offset, trade_date in enumerate(dates[start_index:], start=0):
            daily = self._row(self.market_table(trade_date, "daily"), code)
            if daily is None:
                continue
            limit = self._row(self.market_table(trade_date, "stk_limit"), code)
            if self._one_price_limit(daily, limit, "down"):
                continue
            if offset == 0 and entry_price > 0:
                timed = simulate_tplus1_exit(
                    entry_price=entry_price,
                    buy_close=buy_close,
                    target_pre_close=_pre_close(daily),
                    open_price=self._execution_open_price(
                        trade_date,
                        code,
                        daily,
                    ),
                    high_price=daily.get("high"),
                    low_price=daily.get("low"),
                    close_price=daily.get("close"),
                    down_limit=limit.get("down_limit") if limit is not None else None,
                    minute_frame=self.minute_table(trade_date, code),
                    take_profit_pct=self.config.take_profit_pct,
                    stop_loss_pct=self.config.stop_loss_pct,
                    latest_exit_time=self.config.latest_exit_time,
                    require_intraday=self.config.require_intraday_exit_truth,
                )
                if timed.executable and timed.exit_price is not None and timed.exit_price > 0:
                    return trade_date, float(timed.exit_price), 0, timed.reason
                if timed.reason == "blocked_one_price_limit_down":
                    continue
                return "", float("nan"), -1, timed.reason or "exit_truth_pending"
            exit_price = self._execution_open_price(trade_date, code, daily)
            if exit_price > 0:
                return trade_date, exit_price, offset, "delayed_first_tradable_open"
        return "", float("nan"), -1, "exit_truth_pending"

    def _feature_dict(
        self,
        candidate: pd.Series,
        d_daily: Optional[pd.Series],
        limit_ratio: float,
        market_context: Optional[dict[str, Any]] = None,
        signal_date: str = "",
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for canonical, aliases in FEATURE_ALIASES.items():
            out[canonical] = _numeric_from(candidate, aliases)
        d_return = np.nan
        if d_daily is None:
            out.update(
                {
                    "d_return": np.nan,
                    "d_range": np.nan,
                    "d_turnover_proxy": np.nan,
                    "d_amount_log": np.nan,
                }
            )
        else:
            open_price = _numeric_from(d_daily, ("open",))
            close_price = _numeric_from(d_daily, ("close",))
            high = _numeric_from(d_daily, ("high",))
            low = _numeric_from(d_daily, ("low",))
            pre_close = _pre_close(d_daily)
            amount = _numeric_from(d_daily, ("amount",))
            d_return = close_price / pre_close - 1.0 if pre_close > 0 and close_price > 0 else _numeric_from(d_daily, ("pct_chg",)) / 100.0
            out["d_return"] = d_return
            out["d_range"] = (high - low) / pre_close if pre_close > 0 and high > 0 and low > 0 else np.nan
            out["d_turnover_proxy"] = abs(close_price - open_price) / pre_close if pre_close > 0 and open_price > 0 else np.nan
            out["d_amount_log"] = math.log1p(amount) if amount > 0 else np.nan
        context = market_context or {}
        market_median = _finite(context.get("market_median_return"))
        code = _normal_code(candidate.get("ts_code"))
        limit_detail = self._row(self.market_table(signal_date, "limit_list_d"), code) if signal_date and code else None
        daily_basic = self._row(self.market_table(signal_date, "daily_basic"), code) if signal_date and code else None
        limit_tag = self._row(self.market_table(signal_date, "limit_up_tags"), code) if signal_date and code else None
        daily_amount_yuan = (
            _numeric_from(d_daily, ("amount",)) * 1_000.0 if d_daily is not None else float("nan")
        )
        detail_amount = _numeric_from(limit_detail, ("amount",)) if limit_detail is not None else float("nan")
        amount_yuan = detail_amount if detail_amount > 0 else daily_amount_yuan
        fd_amount = _numeric_from(limit_detail, ("fd_amount",)) if limit_detail is not None else float("nan")
        seal_amount = _numeric_from(limit_detail, ("seal_amount", "fd_amount")) if limit_detail is not None else float("nan")
        detail_float_mv = _numeric_from(limit_detail, ("float_mv",)) if limit_detail is not None else float("nan")
        basic_float_mv = _numeric_from(daily_basic, ("float_mv",)) if daily_basic is not None else float("nan")
        float_mv_yuan = detail_float_mv if detail_float_mv > 0 else basic_float_mv * 10_000.0
        out["limit_open_times"] = _numeric_from(limit_detail, ("open_times",)) if limit_detail is not None else np.nan
        out["limit_first_time_minutes"] = _time_to_minutes(limit_detail.get("first_time")) if limit_detail is not None else np.nan
        out["limit_last_time_minutes"] = _time_to_minutes(limit_detail.get("last_time")) if limit_detail is not None else np.nan
        out["limit_fd_amount_log"] = math.log1p(fd_amount) if fd_amount > 0 else np.nan
        out["limit_seal_to_amount"] = seal_amount / amount_yuan if seal_amount > 0 and amount_yuan > 0 else np.nan
        out["limit_seal_to_float_mv"] = seal_amount / float_mv_yuan if seal_amount > 0 and float_mv_yuan > 0 else np.nan
        turnover_rate = _numeric_from(daily_basic, ("turnover_rate",)) if daily_basic is not None else float("nan")
        out["d_turnover_rate"] = turnover_rate / 100.0 if math.isfinite(turnover_rate) else np.nan
        out["d_volume_ratio"] = _numeric_from(daily_basic, ("volume_ratio",)) if daily_basic is not None else np.nan
        out["d_float_mv_log"] = math.log1p(float_mv_yuan) if float_mv_yuan > 0 else np.nan
        out["order_to_d_amount"] = (
            self.config.order_amount_cny / amount_yuan
            if amount_yuan > 0
            else np.nan
        )
        out["order_to_float_mv"] = (
            self.config.order_amount_cny / float_mv_yuan
            if float_mv_yuan > 0
            else np.nan
        )
        out["is_hot_board"] = _numeric_from(limit_tag, ("is_hot_board",)) if limit_tag is not None else np.nan
        out["board_rank"] = _numeric_from(limit_tag, ("board_rank",)) if limit_tag is not None else np.nan
        out["board_limit_up_count"] = _numeric_from(limit_tag, ("board_limit_up_count",)) if limit_tag is not None else np.nan
        out["d_amount_percentile"] = _finite((context.get("amount_percentile") or {}).get(code))
        out["market_median_return"] = market_median
        out["market_up_ratio"] = _finite(context.get("market_up_ratio"))
        out["market_return_dispersion"] = _finite(context.get("market_return_dispersion"))
        for name in MARKET_SENTIMENT_OUTPUT_FIELDS:
            value = context.get(name)
            if name in {
                "market_sentiment_regime_code",
                "market_sentiment_regime_label",
            }:
                out[name] = str(value or "")
            else:
                out[name] = _finite(value)
        out["relative_d_return"] = d_return - market_median if math.isfinite(d_return) and math.isfinite(market_median) else np.nan
        out.update(self._minute_features(signal_date, code) if signal_date and code else self._minute_features("", ""))
        out["limit_ratio"] = limit_ratio
        out["proposed_gap"] = np.nan
        return out

    def _historical_training_rows(self) -> pd.DataFrame:
        required = {
            "signal_date",
            "buy_date",
            "target_exit_date",
            "ts_code",
            "net_return",
            "profit_hit",
            "big_loss_hit",
            "continuation_limit_up_hit",
            "exit_on_time",
            "market_fill",
            "proposed_gap",
            "exit_policy_version",
            *MODEL_FEATURES,
            *MARKET_SENTIMENT_FEATURES,
        }
        parts: list[pd.DataFrame] = []
        for path in sorted(
            self.config.historical_training_root.glob(
                "training_*.csv"
            )
        ):
            frame = _read_csv(path)
            if frame.empty or not required.issubset(frame.columns):
                continue
            frame = frame.copy()
            for column in (
                "signal_date",
                "buy_date",
                "target_exit_date",
            ):
                frame[column] = frame[column].map(_normal_date)
            frame["ts_code"] = frame["ts_code"].map(_normal_code)
            valid_rows: list[bool] = []
            for _, row in frame.iterrows():
                signal_date = str(row["signal_date"])
                buy_date = str(row["buy_date"])
                exit_date = str(row["target_exit_date"])
                try:
                    valid = bool(
                        is_a_share_trading_day(signal_date)
                        and buy_date
                        == next_a_share_trading_day(signal_date)
                        and exit_date
                        == next_a_share_trading_day(buy_date)
                    )
                except RuntimeError:
                    valid = False
                valid_rows.append(valid)
            frame = frame[np.asarray(valid_rows, dtype=bool)]
            if frame.empty:
                continue
            frame = frame[
                frame["exit_policy_version"].astype(str).eq(
                    self.config.exit_policy_version
                )
            ].copy()
            if frame.empty:
                continue
            frame["history_source"] = frame.get(
                "history_source",
                "tushare_compact_backfill",
            )
            frame["history_contract_version"] = frame.get(
                "history_contract_version",
                HISTORY_CONTRACT_VERSION,
            )
            parts.append(frame)
        return (
            pd.concat(parts, ignore_index=True)
            if parts
            else pd.DataFrame()
        )

    def build_history(self) -> pd.DataFrame:
        dates = self.market_dates()
        date_index = {date: idx for idx, date in enumerate(dates)}
        snapshots = self.candidate_snapshots()
        records: list[dict[str, Any]] = []
        for signal_date in dates:
            idx = date_index.get(signal_date)
            if idx is None or idx + 2 >= len(dates):
                continue
            buy_date, target_exit_date = dates[idx + 1], dates[idx + 2]
            candidates = self.load_candidates(signal_date, snapshots.get(signal_date))
            if candidates.empty:
                continue
            d_daily_table = self.market_table(signal_date, "daily")
            d_limit_table = self.market_table(signal_date, "stk_limit")
            buy_daily_table = self.market_table(buy_date, "daily")
            buy_limit_table = self.market_table(buy_date, "stk_limit")
            market_context = self._market_context(signal_date)
            for _, candidate in candidates.iterrows():
                code = candidate["ts_code"]
                d_daily = self._row(d_daily_table, code)
                buy_daily = self._row(buy_daily_table, code)
                if d_daily is None or buy_daily is None:
                    continue
                d_close = _numeric_from(d_daily, ("close",))
                buy_open = self._execution_open_price(buy_date, code, buy_daily)
                if d_close <= 0 or buy_open <= 0:
                    continue
                d_limit = self._row(d_limit_table, code)
                limit_ratio = self._limit_ratio(d_daily, d_limit)
                mechanism_limit_pct = _finite(
                    candidate.get("decision_limit_pct"),
                    limit_ratio * 100.0,
                )
                if mechanism_limit_pct > self.config.max_mechanism_limit_pct + EPS:
                    continue
                d_up_limit = _numeric_from(d_limit, ("up_limit",)) if d_limit is not None else float("nan")
                if not math.isfinite(d_up_limit) or not _is_close(d_daily.get("close"), d_up_limit):
                    continue
                buy_limit = self._row(buy_limit_table, code)
                buy_up_limit = _numeric_from(buy_limit, ("up_limit",)) if buy_limit is not None else float("nan")
                continuation_hit = int(math.isfinite(buy_up_limit) and _is_close(buy_daily.get("close"), buy_up_limit))
                market_fill, fill_reason = self._market_buyable(buy_date, code)
                auction_row, auction_truth_source = self._auction_row(
                    buy_date,
                    code,
                )
                if not auction_truth_source:
                    auction_truth_source = self._execution_open_source(
                        buy_date,
                        code,
                    )
                exit_date, exit_price, delay_days, exit_reason = self._resolve_exit(
                    code,
                    idx + 2,
                    dates,
                    entry_price=buy_open,
                    buy_date=buy_date,
                )
                if not exit_date or exit_price <= 0:
                    continue
                gross_return = self._realized_gross_return(
                    code,
                    buy_date,
                    buy_open,
                    exit_date,
                    dates,
                    exit_price=exit_price,
                )
                if not math.isfinite(gross_return):
                    continue
                net_return = gross_return - self.config.cost_rate
                features = self._feature_dict(
                    candidate,
                    d_daily,
                    limit_ratio,
                    market_context=market_context,
                    signal_date=signal_date,
                )
                consecutive_limit_ups = self._consecutive_limit_up_count(
                    signal_date,
                    code,
                    dates,
                )
                features["limit_times"] = float(consecutive_limit_ups)
                features.update(
                    self._streak_path_features(
                        signal_date,
                        code,
                        dates,
                    )
                )
                features.update(
                    self._promotion_source_context_features(
                        signal_date,
                        code,
                        dates,
                    )
                )
                features["proposed_gap"] = buy_open / d_close - 1.0
                industry = _text_from(candidate, INDUSTRY_ALIASES)
                records.append(
                    {
                        "signal_date": signal_date,
                        "buy_date": buy_date,
                        "target_exit_date": target_exit_date,
                        "actual_exit_date": exit_date,
                        "exit_delay_days": delay_days,
                        "ts_code": code,
                        "name": str(candidate.get("name", candidate.get("股票", ""))),
                        "industry": industry,
                        "stage": f"{consecutive_limit_ups}→{consecutive_limit_ups + 1}",
                        "source_rank": features["source_rank"],
                        "d_close": d_close,
                        "buy_open": buy_open,
                        "auction_vwap": (
                            _numeric_from(auction_row, ("vwap",))
                            if auction_row is not None
                            else np.nan
                        ),
                        "auction_amount": (
                            _numeric_from(
                                auction_row,
                                ("amount", "auction_amount"),
                            )
                            if auction_row is not None
                            else np.nan
                        ),
                        "auction_truth_source": auction_truth_source,
                        "exit_open": exit_price,
                        "actual_buy_gap": features["proposed_gap"],
                        "gross_return": gross_return,
                        "net_return": net_return,
                        "profit_hit": int(net_return > 0.0),
                        "big_loss_hit": int(net_return <= self.config.big_loss_threshold),
                        "continuation_limit_up_hit": continuation_hit,
                        "exit_on_time": int(delay_days == 0),
                        "market_fill": market_fill,
                        "public_market_buyable": market_fill,
                        "actual_order_fill_observed": 0,
                        "actual_order_fill": np.nan,
                        "mechanism_limit_pct": mechanism_limit_pct,
                        "fill_reason": fill_reason,
                        "exit_reason": exit_reason,
                        "exit_policy_version": self.config.exit_policy_version,
                        "take_profit_pct": self.config.take_profit_pct,
                        "stop_loss_pct": self.config.stop_loss_pct,
                        "latest_exit_time": self.config.latest_exit_time,
                        "history_source": "repository_market_raw",
                        "history_contract_version": HISTORY_CONTRACT_VERSION,
                        **features,
                    }
                )
        frame = pd.DataFrame(records)
        external = self._historical_training_rows()
        if not external.empty:
            frame = pd.concat(
                [external, frame],
                ignore_index=True,
                sort=False,
            )
        if frame.empty:
            return frame
        frame = frame.drop_duplicates(
            ["signal_date", "ts_code"],
            keep="last",
        )
        frame = self._attach_cohort_features(frame)
        frame = self._attach_point_in_time_stage_rates(frame)
        frame = attach_promotion_source_features(frame, self.config.root)
        frame = frame.sort_values(["signal_date", "source_rank", "ts_code"]).reset_index(drop=True)
        return frame

    # ------------------------------------------------------------------
    # Model fitting, cap optimization, and walk-forward evidence
    # ------------------------------------------------------------------
    def _regression_pipeline(self, kind: str = "hgb") -> Pipeline:
        if kind == "extra_trees":
            return Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                    (
                        "model",
                        ExtraTreesRegressor(
                            n_estimators=200,
                            min_samples_leaf=20,
                            max_features=0.70,
                            n_jobs=1,
                            random_state=20260716,
                        ),
                    ),
                ]
            )
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        loss="absolute_error",
                        learning_rate=0.045,
                        max_iter=180,
                        max_leaf_nodes=15,
                        min_samples_leaf=25,
                        l2_regularization=0.25,
                        random_state=20260716,
                    ),
                ),
            ]
        )

    def _classifier_pipeline(self, kind: str = "hgb") -> Pipeline:
        if kind == "extra_trees":
            return Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                    (
                        "model",
                        ExtraTreesClassifier(
                            n_estimators=200,
                            min_samples_leaf=20,
                            max_features=0.70,
                            n_jobs=1,
                            random_state=20260716,
                        ),
                    ),
                ]
            )
        if kind == "lr":
            return Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                    ("scaler", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            C=0.20,
                            max_iter=2_000,
                            random_state=20260716,
                        ),
                    ),
                ]
            )
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.045,
                        max_iter=160,
                        max_leaf_nodes=15,
                        min_samples_leaf=25,
                        l2_regularization=0.25,
                        random_state=20260716,
                    ),
                ),
            ]
        )

    def _fill_training_grid(
        self,
        all_clean: pd.DataFrame,
    ) -> tuple[pd.DataFrame, int]:
        if all_clean.empty:
            return pd.DataFrame(), 0
        proposal_grid = np.arange(
            self.config.gap_grid_min,
            self.config.gap_grid_max
            + self.config.gap_grid_step / 2.0,
            self.config.gap_grid_step,
        )
        max_gaps = np.minimum(
            self.config.gap_grid_max,
            pd.to_numeric(
                all_clean.get("limit_ratio"),
                errors="coerce",
            )
            .fillna(0.10)
            .to_numpy(dtype=float),
        )
        candidate_positions, gap_positions = np.nonzero(
            proposal_grid[None, :]
            <= max_gaps[:, None] + EPS
        )
        rows_before_sampling = int(len(candidate_positions))
        if rows_before_sampling == 0:
            return pd.DataFrame(), 0
        key = pd.DataFrame(
            {
                "_row_position": candidate_positions,
                "signal_date": (
                    all_clean["signal_date"]
                    .astype(str)
                    .to_numpy()[candidate_positions]
                ),
                "ts_code": (
                    all_clean["ts_code"]
                    .astype(str)
                    .to_numpy()[candidate_positions]
                ),
                "proposed_gap": proposal_grid[gap_positions],
            }
        )
        if len(key) > self.config.fill_max_training_rows:
            key["_sample_hash"] = pd.util.hash_pandas_object(
                key[
                    [
                        "signal_date",
                        "ts_code",
                        "proposed_gap",
                    ]
                ],
                index=False,
            ).astype("uint64")
            date_count_for_fill = max(
                1,
                key["signal_date"].nunique(),
            )
            rows_per_date = max(
                20,
                int(
                    math.ceil(
                        self.config.fill_max_training_rows
                        / date_count_for_fill
                    )
                ),
            )
            key = (
                key.sort_values(
                    ["signal_date", "_sample_hash"],
                    kind="stable",
                )
                .groupby(
                    "signal_date",
                    sort=False,
                    group_keys=False,
                )
                .head(rows_per_date)
                .sort_values("_sample_hash", kind="stable")
                .head(self.config.fill_max_training_rows)
                .drop(columns=["_sample_hash"])
                .reset_index(drop=True)
            )
        expanded = (
            all_clean.iloc[
                key["_row_position"].to_numpy(dtype=int)
            ]
            .copy()
            .reset_index(drop=True)
        )
        expanded["proposed_gap"] = key[
            "proposed_gap"
        ].to_numpy(dtype=float)
        expanded["fill_at_cap"] = (
            pd.to_numeric(
                expanded["market_fill"],
                errors="coerce",
            )
            .fillna(0)
            .eq(1)
            & (
                pd.to_numeric(
                    expanded["actual_buy_gap"],
                    errors="coerce",
                )
                <= expanded["proposed_gap"] + EPS
            )
        ).astype(int)
        return expanded, rows_before_sampling

    def fit_models(
        self,
        history: pd.DataFrame,
        *,
        fast_backtest: bool = False,
    ) -> Optional[ModelBundle]:
        if history.empty:
            return None
        history = attach_promotion_source_features(history, self.config.root)
        all_clean = history.dropna(subset=["net_return", "proposed_gap", "market_fill"]).copy()
        clean = all_clean[all_clean["market_fill"].eq(1)].copy()
        dates = sorted(clean["signal_date"].astype(str).unique())
        date_count = len(dates)
        if len(clean) < self.config.min_train_rows or date_count < self.config.min_train_dates:
            return None

        n_calibration_dates = max(
            self.config.calibration_min_dates,
            int(math.ceil(date_count * self.config.calibration_fraction)),
        )
        n_policy_dates = max(
            self.config.policy_tuning_min_dates,
            int(math.ceil(date_count * self.config.policy_tuning_fraction)),
        )
        minimum_fit_dates = max(8, self.config.min_train_dates // 2)
        embargo = self.config.calibration_embargo_dates
        holdout_budget = date_count - minimum_fit_dates - 2 * embargo
        if holdout_budget < 4:
            return None
        if n_calibration_dates + n_policy_dates > holdout_budget:
            n_policy_dates = max(2, min(n_policy_dates, holdout_budget // 3))
            n_calibration_dates = max(2, holdout_budget - n_policy_dates)
        policy_start = date_count - n_policy_dates
        calibration_end = policy_start - embargo
        calibration_start = calibration_end - n_calibration_dates
        fit_end = calibration_start - embargo
        if fit_end < minimum_fit_dates:
            return None
        fit_dates = set(dates[:fit_end])
        calibration_dates = set(dates[calibration_start:calibration_end])
        policy_dates = set(dates[policy_start:])
        fit = clean[clean["signal_date"].astype(str).isin(fit_dates)].copy()
        calibration = clean[clean["signal_date"].astype(str).isin(calibration_dates)].copy()
        policy_tuning = clean[
            clean["signal_date"].astype(str).isin(policy_dates)
        ].copy()
        if (
            len(fit) < max(20, self.config.min_train_rows // 2)
            or calibration.empty
        ):
            return None

        fit_weights = _date_balanced_weights(fit)
        calibration_weights = _date_balanced_weights(calibration)
        return_constant = float(
            np.average(fit["net_return"].to_numpy(dtype=float), weights=fit_weights)
        )
        constant_error = (
            calibration["net_return"].to_numpy(dtype=float) - return_constant
        )
        constant_rmse = float(
            math.sqrt(np.average(constant_error**2, weights=calibration_weights))
        )
        constant_daily_mse = (
            pd.DataFrame(
                {
                    "signal_date": calibration["signal_date"].astype(str).to_numpy(),
                    "squared_error": constant_error**2,
                }
            )
            .groupby("signal_date")["squared_error"]
            .mean()
        )
        regression_candidates: list[
            tuple[float, float, str, Pipeline, float, float]
        ] = []
        regression_audit: dict[str, Any] = {}
        for kind in ("hgb", "extra_trees"):
            provisional = self._regression_pipeline(kind)
            provisional.fit(
                fit[MODEL_FEATURES],
                fit["net_return"],
                model__sample_weight=fit_weights,
            )
            prediction = provisional.predict(calibration[MODEL_FEATURES])
            error = calibration["net_return"].to_numpy(dtype=float) - prediction
            rmse = float(math.sqrt(np.average(error**2, weights=calibration_weights)))
            rank_frame = calibration[["signal_date", "net_return"]].copy()
            rank_frame["prediction"] = prediction
            daily_rank_ic = []
            for _, group in rank_frame.groupby("signal_date"):
                if len(group) < 3 or group["prediction"].nunique() < 2 or group["net_return"].nunique() < 2:
                    continue
                daily_rank_ic.append(
                    float(group[["prediction", "net_return"]].corr(method="spearman").iloc[0, 1])
                )
            mean_rank_ic = float(np.nanmean(daily_rank_ic)) if daily_rank_ic else 0.0
            candidate_daily_mse = (
                pd.DataFrame(
                    {
                        "signal_date": calibration["signal_date"].astype(str).to_numpy(),
                        "squared_error": error**2,
                    }
                )
                .groupby("signal_date")["squared_error"]
                .mean()
            )
            common_dates = constant_daily_mse.index.intersection(
                candidate_daily_mse.index
            )
            daily_win_rate = (
                float(
                    (
                        candidate_daily_mse.loc[common_dates]
                        < constant_daily_mse.loc[common_dates]
                    ).mean()
                )
                if len(common_dates)
                else float("nan")
            )
            relative_improvement = (
                (constant_rmse - rmse) / constant_rmse
                if constant_rmse > 0
                else float("nan")
            )
            objective = rmse - 0.01 * float(np.clip(mean_rank_ic, -0.5, 0.5))
            regression_candidates.append(
                (
                    objective,
                    rmse,
                    kind,
                    provisional,
                    mean_rank_ic,
                    daily_win_rate,
                )
            )
            regression_audit[kind] = {
                "calibration_rmse": _safe_metric(rmse),
                "daily_spearman": _safe_metric(mean_rank_ic),
                "constant_baseline_rmse": _safe_metric(constant_rmse),
                "relative_rmse_improvement": _safe_metric(relative_improvement),
                "daily_baseline_win_rate": _safe_metric(daily_win_rate),
                "selection_objective": _safe_metric(objective),
            }
        (
            _,
            best_return_rmse,
            best_return_kind,
            provisional,
            best_return_rank_ic,
            best_return_daily_win_rate,
        ) = min(
            regression_candidates,
            key=lambda item: (item[0], item[1], item[2]),
        )
        best_return_relative_improvement = (
            (constant_rmse - best_return_rmse) / constant_rmse
            if constant_rmse > 0
            else float("nan")
        )
        return_model_pass = bool(
            math.isfinite(best_return_relative_improvement)
            and best_return_relative_improvement
            >= self.config.return_min_relative_rmse_improvement
            and math.isfinite(best_return_daily_win_rate)
            and best_return_daily_win_rate >= self.config.return_min_daily_win_rate
        )
        return_model: Optional[Pipeline] = provisional if return_model_pass else None
        calibration_prediction = (
            provisional.predict(calibration[MODEL_FEATURES])
            if return_model_pass
            else np.repeat(return_constant, len(calibration))
        )
        raw_residual = calibration["net_return"].to_numpy(dtype=float) - calibration_prediction
        calibration_bias = float(np.clip(np.median(raw_residual), -0.05, 0.05))
        residual = raw_residual - calibration_bias
        residual_by_date = (
            pd.DataFrame(
                {
                    "signal_date": calibration["signal_date"].astype(str).to_numpy(),
                    "residual": residual,
                }
            )
            .groupby("signal_date")["residual"]
            .mean()
        )
        if len(residual_by_date) > 1:
            standard_error = float(
                residual_by_date.std(ddof=1) / math.sqrt(len(residual_by_date))
            )
        elif len(residual) > 1:
            standard_error = float(np.std(residual, ddof=1) / math.sqrt(len(residual)))
        else:
            standard_error = 0.0
        expected_return_margin = float(
            np.clip(
                self.config.expected_return_confidence_z * standard_error,
                self.config.min_expected_return_margin,
                0.05,
            )
        )
        conformal_frame = calibration[
            ["signal_date", "limit_times", "market_sentiment_regime_code"]
        ].copy()
        conformal_frame["residual"] = residual
        conformal_frame["weight"] = calibration_weights
        conformal_residual_quantiles: dict[str, dict[str, float]] = {
            "global": {
                "samples": int(len(conformal_frame)),
                "q10": _weighted_quantile(
                    conformal_frame["residual"],
                    self.config.lower_confidence_quantile,
                    conformal_frame["weight"],
                ),
                "q90": _weighted_quantile(
                    conformal_frame["residual"],
                    self.config.prediction_interval_upper_quantile,
                    conformal_frame["weight"],
                ),
            }
        }
        stage_values = pd.to_numeric(
            conformal_frame["limit_times"], errors="coerce"
        ).round()
        regime_values = conformal_frame[
            "market_sentiment_regime_code"
        ].astype(str)
        for key, mask in (
            *(
                (f"stage:{stage}", stage_values.eq(float(stage)))
                for stage in (2, 3)
            ),
            *(
                (f"regime:{regime}", regime_values.eq(regime))
                for regime in sorted(
                    value
                    for value in regime_values.unique()
                    if value and value.lower() not in {"nan", "none"}
                )
            ),
        ):
            cohort = conformal_frame[mask]
            if len(cohort) < self.config.conformal_min_cohort_rows:
                continue
            conformal_residual_quantiles[key] = {
                "samples": int(len(cohort)),
                "q10": _weighted_quantile(
                    cohort["residual"],
                    self.config.lower_confidence_quantile,
                    cohort["weight"],
                ),
                "q90": _weighted_quantile(
                    cohort["residual"],
                    self.config.prediction_interval_upper_quantile,
                    cohort["weight"],
                ),
            }

        def fit_classifier(
            target: str,
            features: Sequence[str] = MODEL_FEATURES,
            *,
            model_clean: Optional[pd.DataFrame] = None,
            model_fit: Optional[pd.DataFrame] = None,
            model_calibration: Optional[pd.DataFrame] = None,
            model_kinds: Sequence[str] = ("hgb", "lr"),
        ) -> tuple[
            Optional[Pipeline],
            float,
            dict[str, Any],
            ProbabilityCalibrator,
        ]:
            clean_slice = model_clean if model_clean is not None else clean
            fit_slice = model_fit if model_fit is not None else fit
            calibration_slice = (
                model_calibration
                if model_calibration is not None
                else calibration
            )
            values = clean_slice[target].astype(int)
            fit_values = fit_slice[target].astype(int)
            fit_weights = _date_balanced_weights(fit_slice)
            constant = float(
                np.average(fit_values.to_numpy(dtype=float), weights=fit_weights)
            )
            counts = values.value_counts()
            calibration_values = calibration_slice[target].astype(int)
            constant_calibrator = ProbabilityCalibrator("constant", constant)

            def daily_brier(
                probability: np.ndarray,
                mask: np.ndarray,
            ) -> dict[str, float]:
                if not mask.any():
                    return {}
                squared = (
                    probability[mask]
                    - calibration_values.to_numpy(dtype=float)[mask]
                ) ** 2
                frame = pd.DataFrame(
                    {
                        "signal_date": calibration_slice["signal_date"]
                        .astype(str)
                        .to_numpy()[mask],
                        "squared_error": squared,
                    }
                )
                return {
                    str(date): float(value)
                    for date, value in frame.groupby("signal_date")[
                        "squared_error"
                    ].mean().items()
                }

            if (
                len(counts) < 2
                or int(counts.min()) < 10
                or fit_values.nunique() < 2
                or calibration_slice.empty
            ):
                probability = constant_calibrator.transform(
                    np.zeros(len(calibration_slice))
                )
                metrics = probability_metrics(
                    probability,
                    calibration_values,
                    sample_weight=_date_balanced_weights(calibration_slice),
                )
                return None, constant, {
                    "selected": "constant",
                    "base_model": "constant",
                    "calibration_method": "constant",
                    "calibration_brier": _safe_metric(metrics.get("brier")),
                    "constant_baseline_brier": _safe_metric(metrics.get("brier")),
                    "brier_skill_score": 0.0,
                    "expected_calibration_error": _safe_metric(metrics.get("ece")),
                    "reliability": metrics.get("reliability") or [],
                    "calibration_daily_brier": daily_brier(
                        probability,
                        np.ones(len(calibration_slice), dtype=bool),
                    ),
                    "base_rate": _safe_metric(constant),
                    "features": list(features),
                    "training_rows": int(len(clean_slice)),
                    "training_dates": int(
                        clean_slice["signal_date"].astype(str).nunique()
                    ),
                    "model_has_information_gain": False,
                    "fallback_reason": "insufficient_class_or_calibration_support",
                }, constant_calibrator

            cal_fit_mask, cal_eval_mask = chronological_calibration_split(
                calibration_slice["signal_date"].astype(str).to_numpy(),
                fit_fraction=self.config.calibration_fit_fraction,
                embargo_dates=self.config.calibration_embargo_dates,
            )
            eval_dates = int(
                calibration_slice.loc[
                    cal_eval_mask, "signal_date"
                ].astype(str).nunique()
            )
            truth = calibration_values.to_numpy(dtype=int)
            calibration_weights = _date_balanced_weights(calibration_slice)
            baseline_probability = np.repeat(constant, len(calibration_slice))
            baseline_metrics = probability_metrics(
                baseline_probability[cal_eval_mask],
                truth[cal_eval_mask],
                sample_weight=calibration_weights[cal_eval_mask],
            )
            baseline_daily = daily_brier(
                baseline_probability,
                cal_eval_mask,
            )
            candidates: list[
                tuple[
                    float,
                    str,
                    str,
                    Pipeline,
                    ProbabilityCalibrator,
                    dict[str, Any],
                    dict[str, float],
                ]
            ] = []
            candidate_audit: dict[str, Any] = {}
            for kind in model_kinds:
                provisional_classifier = self._classifier_pipeline(kind)
                provisional_classifier.fit(
                    fit_slice[list(features)],
                    fit_values,
                    model__sample_weight=_date_balanced_weights(fit_slice),
                )
                probability = provisional_classifier.predict_proba(
                    calibration_slice[list(features)]
                )[:, 1]
                for method in ("identity", "platt", "beta", "isotonic"):
                    calibrator = fit_probability_calibrator(
                        method,
                        probability[cal_fit_mask],
                        truth[cal_fit_mask],
                        sample_weight=calibration_weights[cal_fit_mask],
                        constant=constant,
                    )
                    if calibrator is None:
                        continue
                    calibrated = calibrator.transform(probability)
                    metrics = probability_metrics(
                        calibrated[cal_eval_mask],
                        truth[cal_eval_mask],
                        sample_weight=calibration_weights[cal_eval_mask],
                    )
                    candidate_daily = daily_brier(calibrated, cal_eval_mask)
                    common_dates = sorted(
                        set(candidate_daily).intersection(baseline_daily)
                    )
                    daily_win_rate = (
                        float(
                            np.mean(
                                [
                                    candidate_daily[date]
                                    < baseline_daily[date]
                                    for date in common_dates
                                ]
                            )
                        )
                        if common_dates
                        else float("nan")
                    )
                    brier = _finite(metrics.get("brier"), float("inf"))
                    baseline_brier = _finite(
                        baseline_metrics.get("brier"), float("inf")
                    )
                    skill = (
                        (baseline_brier - brier) / baseline_brier
                        if baseline_brier > 0
                        else float("nan")
                    )
                    accepted = bool(
                        eval_dates >= self.config.probability_min_eval_dates
                        and math.isfinite(skill)
                        and skill >= self.config.probability_min_brier_skill
                        and math.isfinite(
                            _finite(metrics.get("ece"))
                        )
                        and _finite(metrics.get("ece"))
                        <= self.config.probability_max_ece
                        and math.isfinite(daily_win_rate)
                        and daily_win_rate
                        >= self.config.probability_min_daily_win_rate
                    )
                    key = f"{kind}+{method}"
                    candidate_audit[key] = {
                        "brier": _safe_metric(brier),
                        "brier_skill_score": _safe_metric(skill),
                        "log_loss": _safe_metric(metrics.get("log_loss")),
                        "expected_calibration_error": _safe_metric(
                            metrics.get("ece")
                        ),
                        "daily_baseline_win_rate": _safe_metric(daily_win_rate),
                        "evaluation_dates": eval_dates,
                        "accepted": accepted,
                    }
                    if accepted:
                        candidates.append(
                            (
                                brier,
                                kind,
                                method,
                                provisional_classifier,
                                calibrator,
                                metrics,
                                candidate_daily,
                            )
                        )
            if not candidates:
                return None, constant, {
                    "selected": "constant",
                    "base_model": "constant",
                    "calibration_method": "constant",
                    "calibration_brier": _safe_metric(
                        baseline_metrics.get("brier")
                    ),
                    "constant_baseline_brier": _safe_metric(
                        baseline_metrics.get("brier")
                    ),
                    "brier_skill_score": 0.0,
                    "expected_calibration_error": _safe_metric(
                        baseline_metrics.get("ece")
                    ),
                    "reliability": baseline_metrics.get("reliability") or [],
                    "calibration_daily_brier": {
                        date: _safe_metric(value)
                        for date, value in baseline_daily.items()
                    },
                    "base_rate": _safe_metric(constant),
                    "features": list(features),
                    "training_rows": int(len(clean_slice)),
                    "training_dates": int(
                        clean_slice["signal_date"].astype(str).nunique()
                    ),
                    "calibration_rows": int(len(calibration_slice)),
                    "calibration_dates": int(
                        calibration_slice["signal_date"].astype(str).nunique()
                    ),
                    "evaluation_dates": eval_dates,
                    "model_has_information_gain": False,
                    "fallback_reason": "no_candidate_beat_date_balanced_constant",
                    "candidates": candidate_audit,
                }, constant_calibrator

            (
                best_brier,
                best_kind,
                best_method,
                model,
                _,
                best_metrics,
                best_daily_brier,
            ) = min(candidates, key=lambda item: (item[0], item[1], item[2]))
            raw_all_calibration = model.predict_proba(
                calibration_slice[list(features)]
            )[:, 1]
            production_calibrator = fit_probability_calibrator(
                best_method,
                raw_all_calibration,
                truth,
                sample_weight=calibration_weights,
                constant=constant,
            )
            if production_calibrator is None:
                production_calibrator = ProbabilityCalibrator(
                    "identity", constant
                )
            baseline_brier = _finite(
                baseline_metrics.get("brier"), float("nan")
            )
            skill = (
                (baseline_brier - best_brier) / baseline_brier
                if baseline_brier > 0
                else float("nan")
            )
            return model, constant, {
                "selected": f"{best_kind}+{best_method}",
                "base_model": best_kind,
                "calibration_method": best_method,
                "calibration_brier": _safe_metric(best_brier),
                "constant_baseline_brier": _safe_metric(baseline_brier),
                "brier_skill_score": _safe_metric(skill),
                "daily_baseline_win_rate": _safe_metric(
                    np.mean(
                        [
                            best_daily_brier[date]
                            < baseline_daily[date]
                            for date in sorted(
                                set(best_daily_brier).intersection(
                                    baseline_daily
                                )
                            )
                        ]
                    )
                    if set(best_daily_brier).intersection(baseline_daily)
                    else np.nan
                ),
                "expected_calibration_error": _safe_metric(
                    best_metrics.get("ece")
                ),
                "log_loss": _safe_metric(best_metrics.get("log_loss")),
                "reliability": best_metrics.get("reliability") or [],
                "calibration_daily_brier": {
                    date: _safe_metric(value)
                    for date, value in best_daily_brier.items()
                },
                "base_rate": _safe_metric(constant),
                "features": list(features),
                "training_rows": int(len(clean_slice)),
                "training_dates": int(clean_slice["signal_date"].astype(str).nunique()),
                "calibration_rows": int(len(calibration_slice)),
                "calibration_dates": int(
                    calibration_slice["signal_date"].astype(str).nunique()
                ),
                "evaluation_dates": eval_dates,
                "model_has_information_gain": True,
                "candidates": candidate_audit,
            }, production_calibrator

        critical_model_kinds = ("hgb",)
        continuation_model_kinds = ("lr",)
        (
            profit_model,
            profit_constant,
            profit_selection,
            profit_calibrator,
        ) = fit_classifier(
            "profit_hit",
            model_kinds=(),
        )
        (
            loss_model,
            loss_constant,
            loss_selection,
            loss_calibrator,
        ) = fit_classifier(
            "big_loss_hit",
            model_kinds=critical_model_kinds,
        )
        focus_clean = clean[
            pd.to_numeric(clean.get("limit_times"), errors="coerce").round().isin((2.0, 3.0))
        ].copy()
        focus_fit = fit[
            pd.to_numeric(fit.get("limit_times"), errors="coerce").round().isin((2.0, 3.0))
        ].copy()
        focus_calibration = calibration[
            pd.to_numeric(calibration.get("limit_times"), errors="coerce").round().isin((2.0, 3.0))
        ].copy()
        focus_counts = focus_clean.get(
            "continuation_limit_up_hit",
            pd.Series(dtype=int),
        ).value_counts()
        use_focus_continuation = (
            len(focus_clean) >= max(120, self.config.min_train_rows // 3)
            and focus_clean["signal_date"].astype(str).nunique()
            >= max(15, self.config.min_train_dates // 2)
            and len(focus_counts) >= 2
            and int(focus_counts.min()) >= 20
            and focus_fit.get(
                "continuation_limit_up_hit",
                pd.Series(dtype=int),
            ).nunique()
            >= 2
            and len(focus_calibration) >= 20
        )
        continuation_clean = focus_clean if use_focus_continuation else clean
        continuation_fit = focus_fit if use_focus_continuation else fit
        continuation_calibration = (
            focus_calibration if use_focus_continuation else calibration
        )
        continuation_scope = (
            "stage_2_to_3_and_3_to_4"
            if use_focus_continuation
            else "all_stages_fallback"
        )
        path_continuation = fit_classifier(
            "continuation_limit_up_hit",
            CONTINUATION_PATH_COHORT_FEATURES,
            model_clean=continuation_clean,
            model_fit=continuation_fit,
            model_calibration=continuation_calibration,
            model_kinds=continuation_model_kinds,
        )
        sentiment_continuation = fit_classifier(
            "continuation_limit_up_hit",
            CONTINUATION_FEATURES,
            model_clean=continuation_clean,
            model_fit=continuation_fit,
            model_calibration=continuation_calibration,
            model_kinds=continuation_model_kinds,
        )
        baseline_continuation = fit_classifier(
            "continuation_limit_up_hit",
            CONTINUATION_BASELINE_FEATURES,
            model_clean=continuation_clean,
            model_fit=continuation_fit,
            model_calibration=continuation_calibration,
            model_kinds=continuation_model_kinds,
        )
        path_brier = _finite(path_continuation[2].get("calibration_brier"), float("inf"))
        baseline_brier = _finite(
            baseline_continuation[2].get("calibration_brier"),
            float("inf"),
        )
        sentiment_brier = _finite(
            sentiment_continuation[2].get("calibration_brier"),
            float("inf"),
        )
        if baseline_brier + 1e-6 < path_brier:
            legacy_continuation = baseline_continuation
            legacy_features = tuple(CONTINUATION_BASELINE_FEATURES)
            legacy_feature_set = "baseline_without_streak_path_or_sentiment"
            legacy_brier = baseline_brier
        else:
            legacy_continuation = path_continuation
            legacy_features = tuple(CONTINUATION_PATH_COHORT_FEATURES)
            legacy_feature_set = "streak_path_and_cohort"
            legacy_brier = path_brier

        legacy_daily = (
            legacy_continuation[2].get("calibration_daily_brier") or {}
        )
        sentiment_daily = (
            sentiment_continuation[2].get("calibration_daily_brier") or {}
        )
        common_daily_dates = sorted(
            set(legacy_daily).intersection(sentiment_daily)
        )
        sentiment_daily_win_rate = (
            float(
                np.mean(
                    [
                        _finite(sentiment_daily[date], float("inf"))
                        < _finite(legacy_daily[date], float("inf"))
                        for date in common_daily_dates
                    ]
                )
            )
            if common_daily_dates
            else float("nan")
        )
        sentiment_improvement = (
            legacy_brier - sentiment_brier
            if math.isfinite(legacy_brier) and math.isfinite(sentiment_brier)
            else float("nan")
        )
        sentiment_relative_improvement = (
            sentiment_improvement / legacy_brier
            if math.isfinite(sentiment_improvement) and legacy_brier > 0
            else float("nan")
        )
        required_improvement = (
            max(
                self.config.sentiment_min_brier_improvement,
                self.config.sentiment_min_relative_brier_improvement
                * legacy_brier,
            )
            if math.isfinite(legacy_brier)
            else self.config.sentiment_min_brier_improvement
        )
        sentiment_selected = (
            math.isfinite(sentiment_brier)
            and (
                not math.isfinite(legacy_brier)
                or (
                    sentiment_improvement >= required_improvement
                    and math.isfinite(sentiment_daily_win_rate)
                    and sentiment_daily_win_rate
                    >= self.config.sentiment_min_daily_win_rate
                )
            )
        )
        if sentiment_selected:
            (
                continuation_model,
                continuation_constant,
                continuation_selection,
                continuation_calibrator,
            ) = sentiment_continuation
            continuation_features = tuple(CONTINUATION_FEATURES)
            feature_set = "streak_path_cohort_and_market_sentiment"
            selection_reason = (
                "sentiment_passed_oos_brier_and_daily_consistency_gate"
            )
        else:
            (
                continuation_model,
                continuation_constant,
                continuation_selection,
                continuation_calibrator,
            ) = legacy_continuation
            continuation_features = legacy_features
            feature_set = legacy_feature_set
            selection_reason = (
                "sentiment_auto_fallback_no_robust_oos_improvement"
            )
        continuation_selection = {
            **continuation_selection,
            "feature_set": feature_set,
            "training_scope": continuation_scope,
            "selection_reason": selection_reason,
            "ablation": {
                "streak_path_and_cohort_brier": _safe_metric(path_brier),
                "baseline_without_streak_path_brier": _safe_metric(baseline_brier),
                "baseline_without_path_or_sentiment_brier": _safe_metric(
                    baseline_brier
                ),
                "streak_path_cohort_and_sentiment_brier": _safe_metric(
                    sentiment_brier
                ),
                "path_selected": feature_set
                != "baseline_without_streak_path_or_sentiment",
                "sentiment_selected": sentiment_selected,
                "sentiment_brier_improvement": _safe_metric(
                    sentiment_improvement
                ),
                "sentiment_relative_brier_improvement": _safe_metric(
                    sentiment_relative_improvement
                ),
                "sentiment_daily_win_rate": _safe_metric(
                    sentiment_daily_win_rate
                ),
                "sentiment_daily_comparison_dates": len(common_daily_dates),
                "sentiment_required_brier_improvement": _safe_metric(
                    required_improvement
                ),
                "sentiment_min_daily_win_rate": (
                    self.config.sentiment_min_daily_win_rate
                ),
            },
        }
        incumbent_continuation_selection = dict(continuation_selection)
        promotion_validation = load_promotion_validation(self.config.root)
        if promotion_validation.get("validated") is True:
            promotion_blend = fit_promotion_blend(
                incumbent_model=continuation_model,
                incumbent_calibrator=continuation_calibrator,
                incumbent_features=continuation_features,
                constant=continuation_constant,
                fit_frame=continuation_fit,
                calibration_frame=continuation_calibration,
                target="continuation_limit_up_hit",
                feature_sets={
                    "path_sentiment_five_year_prior": (
                        CONTINUATION_FIVE_YEAR_PRIOR_FEATURES
                    ),
                    "path_sentiment_source_context": (
                        CONTINUATION_SOURCE_CONTEXT_FEATURES
                    ),
                    "path_sentiment_prior_and_context": (
                        CONTINUATION_FIVE_YEAR_FULL_FEATURES
                    ),
                },
            )
            continuation_model = promotion_blend.model
            continuation_calibrator = promotion_blend.calibrator
            continuation_features = promotion_blend.features
            continuation_selection = {
                **incumbent_continuation_selection,
                "production_head": "five_year_validated_promotion_blend",
                "incumbent_selection": incumbent_continuation_selection,
                "promotion_blend": promotion_blend.selection,
                "strict_oos_validation": promotion_validation,
            }
        else:
            continuation_selection = {
                **incumbent_continuation_selection,
                "production_head": "incumbent_auto_fallback",
                "strict_oos_validation": promotion_validation,
            }
        # V8 removes the former in-sample stage-logit patch. Stage effects must
        # now earn their way through the held-out feature model and calibrator.
        continuation_stage_logit_adjustments: dict[int, float] = {2: 0.0, 3: 0.0}
        (
            exit_model,
            exit_constant,
            exit_selection,
            exit_calibrator,
        ) = fit_classifier(
            "exit_on_time",
            model_kinds=critical_model_kinds,
        )
        (
            fill_train,
            fill_rows_before_sampling,
        ) = self._fill_training_grid(all_clean)
        if fill_train.empty:
            fill_model = None
            fill_constant = 0.0
            fill_calibrator = ProbabilityCalibrator("constant", 0.0)
            fill_selection = {
                "selected": "constant",
                "base_model": "constant",
                "calibration_method": "constant",
                "base_rate": 0.0,
                "rows_before_sampling": fill_rows_before_sampling,
                "rows_after_sampling": 0,
                "model_has_information_gain": False,
                "fallback_reason": "no_fill_training_grid",
            }
        else:
            fill_fit = fill_train[
                fill_train["signal_date"].astype(str).isin(fit_dates)
            ].copy()
            fill_calibration = fill_train[
                fill_train["signal_date"].astype(str).isin(calibration_dates)
            ].copy()
            (
                fill_model,
                fill_constant,
                fill_selection,
                fill_calibrator,
            ) = fit_classifier(
                "fill_at_cap",
                MODEL_FEATURES,
                model_clean=fill_train,
                model_fit=fill_fit,
                model_calibration=fill_calibration,
                model_kinds=critical_model_kinds,
            )
            fill_selection = {
                **fill_selection,
                "rows_before_sampling": fill_rows_before_sampling,
                "rows_after_sampling": int(len(fill_train)),
            }
        probability_quality_gate = {
            "required_models": [
                "big_loss",
                "fill",
            ],
            "optional_models": [
                "profit",
                "continuation_limit_up",
                "exit_on_time",
            ],
            "minimum_brier_skill_score": (
                self.config.probability_min_brier_skill
            ),
            "minimum_daily_baseline_win_rate": (
                self.config.probability_min_daily_win_rate
            ),
            "maximum_expected_calibration_error": (
                self.config.probability_max_ece
            ),
            "models": {
                "profit": bool(
                    profit_selection.get("model_has_information_gain")
                ),
                "big_loss": bool(
                    loss_selection.get("model_has_information_gain")
                ),
                "continuation_limit_up": bool(
                    continuation_selection.get("model_has_information_gain")
                ),
                "fill": bool(
                    fill_selection.get("model_has_information_gain")
                ),
                "exit_on_time": bool(
                    exit_selection.get("model_has_information_gain")
                ),
            },
        }
        probability_quality_gate["passed"] = all(
            probability_quality_gate["models"].get(name) is True
            for name in probability_quality_gate["required_models"]
        )
        recent_dates = set(dates[-20:])
        recent = clean[clean["signal_date"].astype(str).isin(recent_dates)].copy()
        recent_stage = pd.to_numeric(recent.get("limit_times"), errors="coerce").round()
        stage_recent_rates: dict[int, float] = {}
        stage_recent_samples: dict[int, int] = {}
        for stage in (2, 3):
            sample = recent[recent_stage.eq(float(stage))]
            values = pd.to_numeric(
                sample.get("continuation_limit_up_hit"),
                errors="coerce",
            ).dropna()
            stage_recent_rates[stage] = float(values.mean()) if len(values) else float("nan")
            stage_recent_samples[stage] = int(len(values))
        bundle = ModelBundle(
            return_model=return_model,
            return_constant=return_constant,
            profit_model=profit_model,
            loss_model=loss_model,
            continuation_model=continuation_model,
            fill_model=fill_model,
            exit_model=exit_model,
            profit_calibrator=profit_calibrator,
            loss_calibrator=loss_calibrator,
            continuation_calibrator=continuation_calibrator,
            fill_calibrator=fill_calibrator,
            exit_calibrator=exit_calibrator,
            profit_constant=profit_constant,
            loss_constant=loss_constant,
            continuation_constant=continuation_constant,
            fill_constant=fill_constant,
            exit_constant=exit_constant,
            calibration_bias=calibration_bias,
            expected_return_margin=expected_return_margin,
            residual_q10=float(
                conformal_residual_quantiles["global"]["q10"]
            ),
            residual_q90=float(
                conformal_residual_quantiles["global"]["q90"]
            ),
            gap_min=float(clean["proposed_gap"].quantile(0.01)),
            gap_max=float(clean["proposed_gap"].quantile(0.99)),
            train_rows=len(clean),
            train_dates=date_count,
            calibration_rows=len(calibration),
            calibration_dates=len(calibration_dates),
            return_selection={
                "selected": (
                    best_return_kind if return_model_pass else "constant"
                ),
                "calibration_rmse": _safe_metric(best_return_rmse),
                "constant_baseline_rmse": _safe_metric(constant_rmse),
                "relative_rmse_improvement": _safe_metric(
                    best_return_relative_improvement
                ),
                "daily_baseline_win_rate": _safe_metric(
                    best_return_daily_win_rate
                ),
                "daily_spearman": _safe_metric(best_return_rank_ic),
                "model_has_information_gain": return_model_pass,
                "minimum_relative_rmse_improvement": (
                    self.config.return_min_relative_rmse_improvement
                ),
                "minimum_daily_baseline_win_rate": (
                    self.config.return_min_daily_win_rate
                ),
                "candidates": regression_audit,
            },
            classifier_selection={
                "profit": profit_selection,
                "big_loss": loss_selection,
                "continuation_limit_up": continuation_selection,
                "fill": fill_selection,
                "exit_on_time": exit_selection,
            },
            probability_quality_gate=probability_quality_gate,
            selection_policy={},
            stage_recent_rates=stage_recent_rates,
            stage_recent_samples=stage_recent_samples,
            continuation_stage_logit_adjustments=continuation_stage_logit_adjustments,
            continuation_features=continuation_features,
            conformal_residual_quantiles=conformal_residual_quantiles,
            model_artifact_sha256=_model_artifact_sha256(
                all_clean,
                self.config,
            ),
        )
        bundle.selection_policy = self._fit_selection_policy(
            policy_tuning,
            bundle,
        )
        return bundle

    def _score_candidate_at_gaps(
        self,
        row: pd.Series,
        bundle: ModelBundle,
        *,
        apply_policy: bool = True,
    ) -> Optional[dict[str, Any]]:
        limit_ratio = _finite(row.get("limit_ratio"), 0.10)
        low = max(self.config.gap_grid_min, bundle.gap_min)
        high = min(self.config.gap_grid_max, bundle.gap_max, limit_ratio)
        if high < low:
            return None
        gaps = np.arange(low, high + self.config.gap_grid_step / 2.0, self.config.gap_grid_step)
        grid = pd.DataFrame([row.to_dict()] * len(gaps))
        grid["proposed_gap"] = gaps
        limit_times = _finite(row.get("limit_times"), 0.0)
        stage_key = int(round(limit_times))
        if (
            int(_finite(row.get("_policy_tuning"), 0.0)) != 1
            and pd.to_numeric(
                grid.get("stage_recent_promotion_rate"),
                errors="coerce",
            ).isna().all()
        ):
            grid["stage_recent_promotion_rate"] = bundle.stage_recent_rates.get(
                stage_key,
                np.nan,
            )
            grid["stage_recent_promotion_samples"] = float(
                bundle.stage_recent_samples.get(stage_key, 0)
            )
        raw_return = (
            bundle.return_model.predict(grid[MODEL_FEATURES])
            if bundle.return_model is not None
            else np.repeat(bundle.return_constant, len(grid))
        )
        pred = raw_return + bundle.calibration_bias
        p_profit = _probability(
            bundle.profit_model,
            grid,
            bundle.profit_constant,
            calibrator=bundle.profit_calibrator,
        )
        p_loss = _probability(
            bundle.loss_model,
            grid,
            bundle.loss_constant,
            calibrator=bundle.loss_calibrator,
        )
        p_continuation = _probability(
            bundle.continuation_model,
            grid,
            bundle.continuation_constant,
            bundle.continuation_features,
            bundle.continuation_calibrator,
        )
        stage_logit_offset = bundle.continuation_stage_logit_adjustments.get(stage_key, 0.0)
        if abs(stage_logit_offset) > EPS:
            clipped = np.clip(p_continuation, 1e-6, 1.0 - 1e-6)
            logits = np.log(clipped / (1.0 - clipped)) + stage_logit_offset
            p_continuation = 1.0 / (1.0 + np.exp(-logits))
        p_exit = _probability(
            bundle.exit_model,
            grid,
            bundle.exit_constant,
            calibrator=bundle.exit_calibrator,
        )
        # A less aggressive limit price cannot have a higher execution chance.
        p_fill = np.maximum.accumulate(
            _probability(
                bundle.fill_model,
                grid,
                bundle.fill_constant,
                calibrator=bundle.fill_calibrator,
            )
        )
        mean_lower = pred - bundle.expected_return_margin
        mean_upper = pred + bundle.expected_return_margin
        quantile_candidates = [
            bundle.conformal_residual_quantiles.get("global", {})
        ]
        stage_quantile = bundle.conformal_residual_quantiles.get(
            f"stage:{stage_key}"
        )
        if stage_quantile:
            quantile_candidates.append(stage_quantile)
        regime_code = str(row.get("market_sentiment_regime_code") or "")
        regime_quantile = bundle.conformal_residual_quantiles.get(
            f"regime:{regime_code}"
        )
        if regime_quantile:
            quantile_candidates.append(regime_quantile)
        q10_values = [
            _finite(item.get("q10"))
            for item in quantile_candidates
            if math.isfinite(_finite(item.get("q10")))
        ]
        q90_values = [
            _finite(item.get("q90"))
            for item in quantile_candidates
            if math.isfinite(_finite(item.get("q90")))
        ]
        residual_q10 = min(q10_values) if q10_values else bundle.residual_q10
        residual_q90 = max(q90_values) if q90_values else bundle.residual_q90
        outcome_q10 = pred + residual_q10
        outcome_q90 = pred + residual_q90
        lower = outcome_q10
        upper = outcome_q90
        risk_adjusted_return = pred - (
            self.config.tail_risk_aversion * p_loss * abs(self.config.big_loss_threshold)
        ) - ((1.0 - p_exit) * self.config.blocked_exit_loss)
        conservative_ev = p_fill * risk_adjusted_return
        stage_focus = 1.0 if int(round(limit_times)) in (2, 3) else 0.0
        selection_score = risk_adjusted_return + (
            self.config.continuation_score_weight * stage_focus * p_continuation
        ) + (self.config.fill_score_weight * p_fill)
        policy = bundle.selection_policy or {}
        thresholds = policy.get("thresholds") or {}
        policy_ready = bool(policy.get("ready"))
        max_big_loss = _finite(
            thresholds.get("max_big_loss_probability"),
            self.config.policy_big_loss_probability_grid[-1],
        )
        min_mean_lcb = _finite(
            thresholds.get("min_mean_return_lcb"),
            self.config.policy_mean_return_lcb_grid[0],
        )
        min_fill = _finite(
            thresholds.get("min_fill_probability"),
            self.config.policy_fill_probability_grid[0],
        )
        min_exit = _finite(
            thresholds.get("min_exit_probability"),
            self.config.policy_min_exit_probability,
        )
        min_ev = _finite(
            thresholds.get("min_conservative_ev"),
            self.config.policy_conservative_ev_grid[0],
        )
        min_score = _finite(
            thresholds.get("min_selection_score"),
            float("inf"),
        )
        big_loss_ok = p_loss <= max_big_loss
        mean_lcb_ok = mean_lower >= min_mean_lcb
        fill_ok = p_fill >= min_fill
        exit_ok = p_exit >= min_exit
        edge_ok = conservative_ev >= min_ev
        score_ok = selection_score >= min_score
        supported = (
            big_loss_ok
            & mean_lcb_ok
            & fill_ok
            & exit_ok
            & edge_ok
            & score_ok
            & bool(stage_focus)
            & bool(policy_ready)
            & bool(apply_policy)
        )
        if supported.any():
            supported_indices = np.where(supported)[0]
            chosen = int(supported_indices[np.argmax(selection_score[supported_indices])])
            model_reason = "ok"
        else:
            finite = np.where(np.isfinite(selection_score))[0]
            if not len(finite):
                return None
            chosen = int(finite[np.argmax(selection_score[finite])])
            if not stage_focus:
                model_reason = "outside_stage_2_to_3_3_to_4_focus"
            elif apply_policy and not policy_ready:
                model_reason = "selection_policy_not_ready"
            elif not exit_ok[chosen]:
                model_reason = "exit_probability_below_policy_floor"
            elif not fill_ok[chosen]:
                model_reason = "fill_probability_below_policy_floor"
            elif not big_loss_ok[chosen]:
                model_reason = "big_loss_probability_exceeds_cap"
            elif not mean_lcb_ok[chosen]:
                model_reason = "mean_return_lcb_below_policy_floor"
            elif not edge_ok[chosen]:
                model_reason = "conservative_ev_below_policy_floor"
            elif not score_ok[chosen]:
                model_reason = "selection_score_below_policy_cutoff"
            elif not apply_policy:
                model_reason = "policy_tuning_candidate"
            else:
                model_reason = "selection_policy_rejected"
        return {
            "recommended_max_gap": float(gaps[chosen]) if supported[chosen] else np.nan,
            "diagnostic_gap": float(gaps[chosen]),
            "predicted_net_return": float(pred[chosen]),
            "predicted_return_lcb": float(lower[chosen]),
            "predicted_return_ucb": float(upper[chosen]),
            "predicted_mean_return_lcb": float(mean_lower[chosen]),
            "predicted_mean_return_ucb": float(mean_upper[chosen]),
            "predicted_outcome_q10": float(outcome_q10[chosen]),
            "predicted_outcome_q90": float(outcome_q90[chosen]),
            "predicted_profit_probability": float(p_profit[chosen]),
            "predicted_big_loss_probability": float(p_loss[chosen]),
            "predicted_continuation_limit_up_probability": float(p_continuation[chosen]),
            "predicted_fill_probability": float(p_fill[chosen]),
            "predicted_exit_probability": float(p_exit[chosen]),
            "conservative_ev": float(conservative_ev[chosen]),
            "selection_score": float(selection_score[chosen]),
            "stage_focus": int(stage_focus),
            "gate_policy_ready": int(policy_ready),
            "gate_stage_focus": int(stage_focus),
            "gate_exit_probability": int(exit_ok[chosen]),
            "gate_fill_probability": int(fill_ok[chosen]),
            "gate_big_loss_probability": int(big_loss_ok[chosen]),
            "gate_mean_return_lcb": int(mean_lcb_ok[chosen]),
            "gate_conservative_ev": int(edge_ok[chosen]),
            "gate_selection_score": int(score_ok[chosen]),
            "risk_gate_pass": int(supported[chosen]),
            "model_reason": model_reason,
            "selection_policy_version": str(
                policy.get("version") or "nested_temporal_utility_v1"
            ),
            "policy_max_big_loss_probability": max_big_loss,
            "policy_min_mean_return_lcb": min_mean_lcb,
            "policy_min_fill_probability": min_fill,
            "policy_min_exit_probability": min_exit,
            "policy_min_conservative_ev": min_ev,
            "policy_min_selection_score": min_score,
            "policy_max_positions": int(
                max(
                    1,
                    _finite(
                        policy.get("max_positions"),
                        self.config.max_positions,
                    ),
                )
            ),
        }

    def score_candidates(
        self,
        base: pd.DataFrame,
        bundle: Optional[ModelBundle],
        *,
        apply_policy: bool = True,
    ) -> pd.DataFrame:
        base = attach_promotion_source_features(base, self.config.root)
        out = base.copy().reset_index(drop=True)
        score_columns = [
            "recommended_max_gap",
            "diagnostic_gap",
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
            "policy_max_big_loss_probability",
            "policy_min_mean_return_lcb",
            "policy_min_fill_probability",
            "policy_min_exit_probability",
            "policy_min_conservative_ev",
            "policy_min_selection_score",
            "policy_max_positions",
        ]
        for name in score_columns:
            out[name] = np.nan
        if bundle is None:
            out["model_reason"] = "insufficient_independent_history"
            out["risk_gate_pass"] = 0
            out["shadow_rank"] = np.nan
            out["shadow_selected"] = 0
            out["selected"] = 0
            return out
        return self._score_candidates_batch(
            base,
            bundle,
            apply_policy=apply_policy,
        )

    def _score_candidates_batch(
        self,
        base: pd.DataFrame,
        bundle: ModelBundle,
        *,
        apply_policy: bool,
    ) -> pd.DataFrame:
        candidates = base.copy().reset_index(drop=True)
        if candidates.empty:
            return candidates
        candidates["_batch_candidate_index"] = np.arange(
            len(candidates),
            dtype=int,
        )
        candidates["_original_proposed_gap"] = pd.to_numeric(
            candidates.get(
                "proposed_gap",
                pd.Series(np.nan, index=candidates.index),
            ),
            errors="coerce",
        )
        tuning_marker = pd.to_numeric(
            candidates.get(
                "_policy_tuning",
                pd.Series(0, index=candidates.index),
            ),
            errors="coerce",
        ).fillna(0)
        recent_rate = pd.to_numeric(
            candidates.get(
                "stage_recent_promotion_rate",
                pd.Series(np.nan, index=candidates.index),
            ),
            errors="coerce",
        )
        stages_base = (
            pd.to_numeric(
                candidates.get("limit_times"),
                errors="coerce",
            )
            .fillna(0.0)
            .round()
            .astype(int)
        )
        fill_recent = tuning_marker.ne(1) & recent_rate.isna()
        if fill_recent.any():
            candidates.loc[
                fill_recent,
                "stage_recent_promotion_rate",
            ] = stages_base.loc[fill_recent].map(
                bundle.stage_recent_rates
            )
            candidates.loc[
                fill_recent,
                "stage_recent_promotion_samples",
            ] = stages_base.loc[fill_recent].map(
                bundle.stage_recent_samples
            ).fillna(0)

        low = max(self.config.gap_grid_min, bundle.gap_min)
        global_high = min(
            self.config.gap_grid_max,
            bundle.gap_max,
        )
        if global_high < low:
            fallback = candidates.copy()
            fallback["model_reason"] = "no_safe_price"
            fallback["risk_gate_pass"] = 0
            fallback["shadow_rank"] = np.nan
            fallback["shadow_selected"] = 0
            fallback["selected"] = 0
            return fallback.drop(
                columns=[
                    "_batch_candidate_index",
                    "_original_proposed_gap",
                ],
                errors="ignore",
            )
        gap_grid = np.arange(
            low,
            global_high + self.config.gap_grid_step / 2.0,
            self.config.gap_grid_step,
        )
        row_high = np.minimum(
            global_high,
            pd.to_numeric(
                candidates.get("limit_ratio"),
                errors="coerce",
            )
            .fillna(0.10)
            .to_numpy(dtype=float),
        )
        candidate_positions, gap_positions = np.nonzero(
            gap_grid[None, :] <= row_high[:, None] + EPS
        )
        if len(candidate_positions) == 0:
            fallback = candidates.copy()
            fallback["model_reason"] = "no_safe_price"
            fallback["risk_gate_pass"] = 0
            fallback["shadow_rank"] = np.nan
            fallback["shadow_selected"] = 0
            fallback["selected"] = 0
            return fallback.drop(
                columns=[
                    "_batch_candidate_index",
                    "_original_proposed_gap",
                ],
                errors="ignore",
            )
        grid = (
            candidates.iloc[candidate_positions]
            .copy()
            .reset_index(drop=True)
        )
        grid["proposed_gap"] = gap_grid[gap_positions]
        candidate_ids = grid[
            "_batch_candidate_index"
        ].to_numpy(dtype=int)

        raw_return = (
            bundle.return_model.predict(grid[MODEL_FEATURES])
            if bundle.return_model is not None
            else np.repeat(bundle.return_constant, len(grid))
        )
        predicted_return = raw_return + bundle.calibration_bias
        p_profit = _probability(
            bundle.profit_model,
            grid,
            bundle.profit_constant,
            calibrator=bundle.profit_calibrator,
        )
        p_loss = _probability(
            bundle.loss_model,
            grid,
            bundle.loss_constant,
            calibrator=bundle.loss_calibrator,
        )
        p_continuation = _probability(
            bundle.continuation_model,
            grid,
            bundle.continuation_constant,
            bundle.continuation_features,
            bundle.continuation_calibrator,
        )
        stages = (
            pd.to_numeric(
                grid.get("limit_times"),
                errors="coerce",
            )
            .fillna(0.0)
            .round()
            .astype(int)
        )
        offsets = stages.map(
            bundle.continuation_stage_logit_adjustments
        ).fillna(0.0).to_numpy(dtype=float)
        if np.any(np.abs(offsets) > EPS):
            clipped = np.clip(
                p_continuation,
                1e-6,
                1.0 - 1e-6,
            )
            logits = np.log(clipped / (1.0 - clipped)) + offsets
            p_continuation = 1.0 / (1.0 + np.exp(-logits))
        p_exit = _probability(
            bundle.exit_model,
            grid,
            bundle.exit_constant,
            calibrator=bundle.exit_calibrator,
        )
        grid["_raw_fill_probability"] = _probability(
            bundle.fill_model,
            grid,
            bundle.fill_constant,
            calibrator=bundle.fill_calibrator,
        )
        p_fill = (
            grid.groupby(
                "_batch_candidate_index",
                sort=False,
            )["_raw_fill_probability"]
            .cummax()
            .to_numpy(dtype=float)
        )

        mean_lower = predicted_return - bundle.expected_return_margin
        mean_upper = predicted_return + bundle.expected_return_margin
        global_quantile = bundle.conformal_residual_quantiles.get(
            "global",
            {},
        )
        residual_q10 = np.repeat(
            _finite(
                global_quantile.get("q10"),
                bundle.residual_q10,
            ),
            len(grid),
        )
        residual_q90 = np.repeat(
            _finite(
                global_quantile.get("q90"),
                bundle.residual_q90,
            ),
            len(grid),
        )
        for stage in stages.unique():
            quantile = bundle.conformal_residual_quantiles.get(
                f"stage:{int(stage)}"
            )
            if not quantile:
                continue
            mask = stages.eq(int(stage)).to_numpy()
            residual_q10[mask] = np.minimum(
                residual_q10[mask],
                _finite(
                    quantile.get("q10"),
                    bundle.residual_q10,
                ),
            )
            residual_q90[mask] = np.maximum(
                residual_q90[mask],
                _finite(
                    quantile.get("q90"),
                    bundle.residual_q90,
                ),
            )
        regimes = grid.get(
            "market_sentiment_regime_code",
            pd.Series("", index=grid.index),
        ).fillna("").astype(str)
        for regime in regimes.unique():
            quantile = bundle.conformal_residual_quantiles.get(
                f"regime:{regime}"
            )
            if not quantile:
                continue
            mask = regimes.eq(regime).to_numpy()
            residual_q10[mask] = np.minimum(
                residual_q10[mask],
                _finite(
                    quantile.get("q10"),
                    bundle.residual_q10,
                ),
            )
            residual_q90[mask] = np.maximum(
                residual_q90[mask],
                _finite(
                    quantile.get("q90"),
                    bundle.residual_q90,
                ),
            )
        outcome_q10 = predicted_return + residual_q10
        outcome_q90 = predicted_return + residual_q90
        risk_adjusted_return = predicted_return - (
            self.config.tail_risk_aversion
            * p_loss
            * abs(self.config.big_loss_threshold)
        ) - ((1.0 - p_exit) * self.config.blocked_exit_loss)
        conservative_ev = p_fill * risk_adjusted_return
        stage_focus = stages.isin((2, 3)).astype(int).to_numpy()
        selection_score = risk_adjusted_return + (
            self.config.continuation_score_weight
            * stage_focus
            * p_continuation
        ) + (self.config.fill_score_weight * p_fill)

        policy = bundle.selection_policy or {}
        thresholds = policy.get("thresholds") or {}
        policy_ready = bool(policy.get("ready"))
        max_big_loss = _finite(
            thresholds.get("max_big_loss_probability"),
            self.config.policy_big_loss_probability_grid[-1],
        )
        min_mean_lcb = _finite(
            thresholds.get("min_mean_return_lcb"),
            self.config.policy_mean_return_lcb_grid[0],
        )
        min_fill = _finite(
            thresholds.get("min_fill_probability"),
            self.config.policy_fill_probability_grid[0],
        )
        min_exit = _finite(
            thresholds.get("min_exit_probability"),
            self.config.policy_min_exit_probability,
        )
        min_ev = _finite(
            thresholds.get("min_conservative_ev"),
            self.config.policy_conservative_ev_grid[0],
        )
        min_score = _finite(
            thresholds.get("min_selection_score"),
            float("inf"),
        )
        gate_exit = p_exit >= min_exit
        gate_fill = p_fill >= min_fill
        gate_loss = p_loss <= max_big_loss
        gate_mean = mean_lower >= min_mean_lcb
        gate_ev = conservative_ev >= min_ev
        gate_score = selection_score >= min_score
        supported = (
            gate_exit
            & gate_fill
            & gate_loss
            & gate_mean
            & gate_ev
            & gate_score
            & stage_focus.astype(bool)
            & bool(policy_ready)
            & bool(apply_policy)
        )
        finite_score = np.isfinite(selection_score)
        grid["_supported"] = supported
        grid["_finite_score"] = finite_score
        grid["_selection_score"] = selection_score
        has_supported = (
            grid.groupby(
                "_batch_candidate_index",
                sort=False,
            )["_supported"]
            .transform("any")
            .to_numpy(dtype=bool)
        )
        choice_pool = finite_score & (
            supported | ~has_supported
        )
        ranked = grid.loc[choice_pool].copy()
        if ranked.empty:
            fallback = candidates.copy()
            fallback["model_reason"] = "no_safe_price"
            fallback["risk_gate_pass"] = 0
            fallback["shadow_rank"] = np.nan
            fallback["shadow_selected"] = 0
            fallback["selected"] = 0
            return fallback.drop(
                columns=[
                    "_batch_candidate_index",
                    "_original_proposed_gap",
                ],
                errors="ignore",
            )
        chosen_indices = (
            ranked.groupby(
                "_batch_candidate_index",
                sort=False,
            )["_selection_score"]
            .idxmax()
            .to_numpy()
        )
        chosen = grid.loc[chosen_indices].copy()
        positions = chosen.index.to_numpy(dtype=int)
        chosen["diagnostic_gap"] = chosen["proposed_gap"]
        chosen["proposed_gap"] = chosen[
            "_original_proposed_gap"
        ]
        chosen["predicted_net_return"] = predicted_return[positions]
        chosen["predicted_return_lcb"] = outcome_q10[positions]
        chosen["predicted_return_ucb"] = outcome_q90[positions]
        chosen["predicted_mean_return_lcb"] = mean_lower[positions]
        chosen["predicted_mean_return_ucb"] = mean_upper[positions]
        chosen["predicted_outcome_q10"] = outcome_q10[positions]
        chosen["predicted_outcome_q90"] = outcome_q90[positions]
        chosen["predicted_profit_probability"] = p_profit[positions]
        chosen["predicted_big_loss_probability"] = p_loss[positions]
        chosen[
            "predicted_continuation_limit_up_probability"
        ] = p_continuation[positions]
        chosen["predicted_fill_probability"] = p_fill[positions]
        chosen["predicted_exit_probability"] = p_exit[positions]
        chosen["conservative_ev"] = conservative_ev[positions]
        chosen["selection_score"] = selection_score[positions]
        chosen["stage_focus"] = stage_focus[positions]
        chosen["gate_policy_ready"] = int(policy_ready)
        chosen["gate_stage_focus"] = stage_focus[positions]
        chosen["gate_exit_probability"] = gate_exit[positions].astype(int)
        chosen["gate_fill_probability"] = gate_fill[positions].astype(int)
        chosen["gate_big_loss_probability"] = gate_loss[positions].astype(int)
        chosen["gate_mean_return_lcb"] = gate_mean[positions].astype(int)
        chosen["gate_conservative_ev"] = gate_ev[positions].astype(int)
        chosen["gate_selection_score"] = gate_score[positions].astype(int)
        chosen["risk_gate_pass"] = supported[positions].astype(int)
        chosen["recommended_max_gap"] = np.where(
            supported[positions],
            chosen["diagnostic_gap"],
            np.nan,
        )
        chosen["selection_policy_version"] = str(
            policy.get("version") or "nested_temporal_utility_v1"
        )
        chosen["policy_max_big_loss_probability"] = max_big_loss
        chosen["policy_min_mean_return_lcb"] = min_mean_lcb
        chosen["policy_min_fill_probability"] = min_fill
        chosen["policy_min_exit_probability"] = min_exit
        chosen["policy_min_conservative_ev"] = min_ev
        chosen["policy_min_selection_score"] = min_score
        policy_positions = int(
            max(
                1,
                _finite(
                    policy.get("max_positions"),
                    self.config.max_positions,
                ),
            )
        )
        chosen["policy_max_positions"] = policy_positions

        def rejection_reason(row: pd.Series) -> str:
            if int(row["stage_focus"]) != 1:
                return "outside_stage_2_to_3_3_to_4_focus"
            if apply_policy and not policy_ready:
                return "selection_policy_not_ready"
            if int(row["gate_exit_probability"]) != 1:
                return "exit_probability_below_policy_floor"
            if int(row["gate_fill_probability"]) != 1:
                return "fill_probability_below_policy_floor"
            if int(row["gate_big_loss_probability"]) != 1:
                return "big_loss_probability_exceeds_cap"
            if int(row["gate_mean_return_lcb"]) != 1:
                return "mean_return_lcb_below_policy_floor"
            if int(row["gate_conservative_ev"]) != 1:
                return "conservative_ev_below_policy_floor"
            if int(row["gate_selection_score"]) != 1:
                return "selection_score_below_policy_cutoff"
            if not apply_policy:
                return "policy_tuning_candidate"
            return "ok" if int(row["risk_gate_pass"]) == 1 else (
                "selection_policy_rejected"
            )

        chosen["model_reason"] = chosen.apply(
            rejection_reason,
            axis=1,
        )
        missing_ids = sorted(
            set(candidates["_batch_candidate_index"])
            - set(chosen["_batch_candidate_index"])
        )
        if missing_ids:
            missing = candidates[
                candidates["_batch_candidate_index"].isin(missing_ids)
            ].copy()
            missing["model_reason"] = "no_safe_price"
            missing["risk_gate_pass"] = 0
            chosen = pd.concat([chosen, missing], ignore_index=True)

        internal = [
            column
            for column in chosen.columns
            if column.startswith("_")
        ]
        out = chosen.drop(columns=internal, errors="ignore")
        sort_columns = []
        ascending = []
        if "signal_date" in out.columns:
            sort_columns.append("signal_date")
            ascending.append(True)
        sort_columns.extend(
            [
                "selection_score",
                "predicted_return_lcb",
                "source_rank",
            ]
        )
        ascending.extend([False, False, True])
        out = out.sort_values(
            sort_columns,
            ascending=ascending,
            na_position="last",
            kind="stable",
        ).reset_index(drop=True)
        out["shadow_rank"] = np.nan
        focus = pd.to_numeric(
            out.get("stage_focus"),
            errors="coerce",
        ).fillna(0).eq(1)
        if focus.any():
            if "signal_date" in out.columns:
                out.loc[focus, "shadow_rank"] = (
                    out.loc[focus]
                    .groupby("signal_date", sort=False)
                    .cumcount()
                    .add(1)
                    .to_numpy()
                )
            else:
                out.loc[focus, "shadow_rank"] = np.arange(
                    1,
                    int(focus.sum()) + 1,
                )
        out["shadow_selected"] = (
            pd.to_numeric(
                out["shadow_rank"],
                errors="coerce",
            )
            .le(2)
            .fillna(False)
            .astype(int)
        )
        out["selected"] = 0
        if apply_policy:
            eligible = out[
                pd.to_numeric(
                    out.get("risk_gate_pass"),
                    errors="coerce",
                ).fillna(0).eq(1)
            ]
            if "signal_date" in out.columns:
                selected_indices = (
                    eligible.groupby(
                        "signal_date",
                        sort=False,
                        group_keys=False,
                    )
                    .head(policy_positions)
                    .index
                )
            else:
                selected_indices = eligible.head(
                    policy_positions
                ).index
            out.loc[selected_indices, "selected"] = 1
        return out

    def _score_policy_tuning_candidates(
        self,
        policy_tuning: pd.DataFrame,
        bundle: ModelBundle,
    ) -> pd.DataFrame:
        """Batch-score the embargoed policy window without changing its math."""
        base = policy_tuning.copy().reset_index(drop=True)
        if base.empty:
            return base
        expanded: list[pd.DataFrame] = []
        for candidate_index, row in base.iterrows():
            limit_ratio = _finite(row.get("limit_ratio"), 0.10)
            low = max(self.config.gap_grid_min, bundle.gap_min)
            high = min(
                self.config.gap_grid_max,
                bundle.gap_max,
                limit_ratio,
            )
            if high < low:
                continue
            gaps = np.arange(
                low,
                high + self.config.gap_grid_step / 2.0,
                self.config.gap_grid_step,
            )
            part = pd.DataFrame([row.to_dict()] * len(gaps))
            part["proposed_gap"] = gaps
            part["_policy_candidate_index"] = candidate_index
            expanded.append(part)
        if not expanded:
            return pd.DataFrame()

        grid = pd.concat(expanded, ignore_index=True)
        raw_return = (
            bundle.return_model.predict(grid[MODEL_FEATURES])
            if bundle.return_model is not None
            else np.repeat(bundle.return_constant, len(grid))
        )
        predicted_return = raw_return + bundle.calibration_bias
        p_loss = _probability(
            bundle.loss_model,
            grid,
            bundle.loss_constant,
            calibrator=bundle.loss_calibrator,
        )
        p_continuation = _probability(
            bundle.continuation_model,
            grid,
            bundle.continuation_constant,
            bundle.continuation_features,
            bundle.continuation_calibrator,
        )
        stages = (
            pd.to_numeric(
                grid.get("limit_times"),
                errors="coerce",
            )
            .fillna(0.0)
            .round()
            .astype(int)
        )
        offsets = stages.map(
            bundle.continuation_stage_logit_adjustments
        ).fillna(0.0).to_numpy(dtype=float)
        if np.any(np.abs(offsets) > EPS):
            clipped = np.clip(p_continuation, 1e-6, 1.0 - 1e-6)
            logits = np.log(clipped / (1.0 - clipped)) + offsets
            p_continuation = 1.0 / (1.0 + np.exp(-logits))
        p_exit = _probability(
            bundle.exit_model,
            grid,
            bundle.exit_constant,
            calibrator=bundle.exit_calibrator,
        )
        grid["_raw_fill_probability"] = _probability(
            bundle.fill_model,
            grid,
            bundle.fill_constant,
            calibrator=bundle.fill_calibrator,
        )
        p_fill = (
            grid.groupby(
                "_policy_candidate_index",
                sort=False,
            )["_raw_fill_probability"]
            .cummax()
            .to_numpy(dtype=float)
        )
        mean_lower = predicted_return - bundle.expected_return_margin
        risk_adjusted_return = predicted_return - (
            self.config.tail_risk_aversion
            * p_loss
            * abs(self.config.big_loss_threshold)
        ) - ((1.0 - p_exit) * self.config.blocked_exit_loss)
        conservative_ev = p_fill * risk_adjusted_return
        stage_focus = stages.isin((2, 3)).astype(float).to_numpy()
        selection_score = risk_adjusted_return + (
            self.config.continuation_score_weight
            * stage_focus
            * p_continuation
        ) + (self.config.fill_score_weight * p_fill)
        grid["_predicted_net_return"] = predicted_return
        grid["_predicted_mean_return_lcb"] = mean_lower
        grid["_predicted_big_loss_probability"] = p_loss
        grid["_predicted_continuation_probability"] = p_continuation
        grid["_predicted_fill_probability"] = p_fill
        grid["_predicted_exit_probability"] = p_exit
        grid["_conservative_ev"] = conservative_ev
        grid["_selection_score"] = selection_score
        finite_score = np.isfinite(selection_score)
        ranked = grid.loc[finite_score].copy()
        if ranked.empty:
            return pd.DataFrame()
        chosen_indices = (
            ranked.groupby(
                "_policy_candidate_index",
                sort=False,
            )["_selection_score"]
            .idxmax()
            .to_numpy()
        )
        chosen = grid.loc[chosen_indices].copy()
        chosen = chosen.rename(
            columns={
                "proposed_gap": "diagnostic_gap",
                "_predicted_net_return": "predicted_net_return",
                "_predicted_mean_return_lcb": "predicted_mean_return_lcb",
                "_predicted_big_loss_probability": (
                    "predicted_big_loss_probability"
                ),
                "_predicted_continuation_probability": (
                    "predicted_continuation_limit_up_probability"
                ),
                "_predicted_fill_probability": (
                    "predicted_fill_probability"
                ),
                "_predicted_exit_probability": (
                    "predicted_exit_probability"
                ),
                "_conservative_ev": "conservative_ev",
                "_selection_score": "selection_score",
            }
        )
        chosen["stage_focus"] = (
            pd.to_numeric(
                chosen.get("limit_times"),
                errors="coerce",
            )
            .fillna(0.0)
            .round()
            .isin((2.0, 3.0))
            .astype(int)
        )
        chosen["model_reason"] = "policy_tuning_candidate"
        internal = [
            column
            for column in chosen.columns
            if column.startswith("_")
        ]
        return chosen.drop(
            columns=[
                *internal,
                "recommended_max_gap",
            ],
            errors="ignore",
        )

    def _fit_selection_policy(
        self,
        policy_tuning: pd.DataFrame,
        bundle: ModelBundle,
    ) -> dict[str, Any]:
        version = "nested_temporal_utility_v1"
        base_payload: dict[str, Any] = {
            "version": version,
            "ready": False,
            "reason": "insufficient_policy_tuning_history",
            "tuning_rows": int(len(policy_tuning)),
            "tuning_dates": int(
                policy_tuning.get(
                    "signal_date",
                    pd.Series(dtype=str),
                ).astype(str).nunique()
            ),
            "profit_probability_gate_enabled": False,
            "profit_probability_gate_reason": (
                "profit probability is diagnostic only; an uninformative "
                "constant model cannot veto every candidate"
            ),
            "thresholds": {},
            "max_positions": 0,
            "diagnostics": {},
        }
        tuning_dates = sorted(
            policy_tuning.get(
                "signal_date",
                pd.Series(dtype=str),
            ).astype(str).unique()
        )
        if (
            policy_tuning.empty
            or len(tuning_dates) < self.config.policy_tuning_min_dates
        ):
            return base_payload

        scored = self._score_policy_tuning_candidates(
            policy_tuning,
            bundle,
        )
        if scored.empty:
            base_payload["reason"] = "no_finite_policy_score"
            return base_payload
        focus = scored[
            pd.to_numeric(
                scored.get("stage_focus"),
                errors="coerce",
            ).fillna(0).eq(1)
        ].copy()
        focus = focus[
            pd.to_numeric(
                focus.get("selection_score"),
                errors="coerce",
            ).notna()
        ]
        if focus.empty:
            base_payload["reason"] = "no_stage_2_to_3_or_3_to_4_policy_rows"
            return base_payload

        score_values = pd.to_numeric(
            focus["selection_score"],
            errors="coerce",
        ).dropna()
        score_cutoffs = sorted(
            {
                float(score_values.quantile(quantile))
                for quantile in self.config.policy_score_quantiles
                if len(score_values)
            }
        )
        if not score_cutoffs:
            base_payload["reason"] = "no_finite_policy_selection_score"
            return base_payload

        all_dates = sorted(scored["signal_date"].astype(str).unique())
        candidates: list[dict[str, Any]] = []
        for max_positions in self.config.policy_position_grid:
            positions = max(1, int(max_positions))
            for min_fill in self.config.policy_fill_probability_grid:
                for max_big_loss in self.config.policy_big_loss_probability_grid:
                    for min_mean_lcb in self.config.policy_mean_return_lcb_grid:
                        for min_ev in self.config.policy_conservative_ev_grid:
                            for min_score in score_cutoffs:
                                eligible = focus[
                                    pd.to_numeric(
                                        focus["predicted_exit_probability"],
                                        errors="coerce",
                                    ).ge(self.config.policy_min_exit_probability)
                                    & pd.to_numeric(
                                        focus["predicted_fill_probability"],
                                        errors="coerce",
                                    ).ge(min_fill)
                                    & pd.to_numeric(
                                        focus["predicted_big_loss_probability"],
                                        errors="coerce",
                                    ).le(max_big_loss)
                                    & pd.to_numeric(
                                        focus["predicted_mean_return_lcb"],
                                        errors="coerce",
                                    ).ge(min_mean_lcb)
                                    & pd.to_numeric(
                                        focus["conservative_ev"],
                                        errors="coerce",
                                    ).ge(min_ev)
                                    & pd.to_numeric(
                                        focus["selection_score"],
                                        errors="coerce",
                                    ).ge(min_score)
                                ].copy()
                                selected = (
                                    eligible.sort_values(
                                        [
                                            "signal_date",
                                            "selection_score",
                                            "predicted_mean_return_lcb",
                                            "source_rank",
                                        ],
                                        ascending=[True, False, False, True],
                                        kind="stable",
                                    )
                                    .groupby(
                                        "signal_date",
                                        sort=False,
                                        group_keys=False,
                                    )
                                    .head(positions)
                                )
                                signal_date_set = set(
                                    selected["signal_date"].astype(str)
                                )
                                no_signal_streak = 0
                                current_streak = 0
                                for signal_date in all_dates:
                                    if signal_date in signal_date_set:
                                        current_streak = 0
                                    else:
                                        current_streak += 1
                                        no_signal_streak = max(
                                            no_signal_streak,
                                            current_streak,
                                        )
                                cap_accepted = (
                                    pd.to_numeric(
                                        selected.get("actual_buy_gap"),
                                        errors="coerce",
                                    )
                                    <= pd.to_numeric(
                                        selected.get("diagnostic_gap"),
                                        errors="coerce",
                                    )
                                    + EPS
                                )
                                market_fill = pd.to_numeric(
                                    selected.get("market_fill"),
                                    errors="coerce",
                                ).fillna(0).eq(1)
                                filled = selected[
                                    cap_accepted.fillna(False) & market_fill
                                ].copy()
                                daily = (
                                    pd.to_numeric(
                                        filled.get("net_return"),
                                        errors="coerce",
                                    )
                                    .groupby(filled["signal_date"].astype(str))
                                    .sum()
                                    .reindex(all_dates, fill_value=0.0)
                                    / float(positions)
                                )
                                stress_daily = (
                                    (
                                        pd.to_numeric(
                                            filled.get("gross_return"),
                                            errors="coerce",
                                        )
                                        - 2.0 * self.config.cost_rate
                                    )
                                    .groupby(filled["signal_date"].astype(str))
                                    .sum()
                                    .reindex(all_dates, fill_value=0.0)
                                    / float(positions)
                                )
                                nav = (1.0 + daily).cumprod()
                                drawdown = nav / nav.cummax() - 1.0
                                realized = pd.to_numeric(
                                    filled.get("net_return"),
                                    errors="coerce",
                                ).dropna()
                                tail_count = (
                                    max(1, int(math.ceil(len(realized) * 0.10)))
                                    if len(realized)
                                    else 0
                                )
                                tail_mean = (
                                    float(realized.nsmallest(tail_count).mean())
                                    if tail_count
                                    else float("nan")
                                )
                                big_loss_rate = (
                                    float(
                                        (
                                            realized
                                            <= self.config.big_loss_threshold
                                        ).mean()
                                    )
                                    if len(realized)
                                    else float("nan")
                                )
                                signal_dates = len(signal_date_set)
                                signal_ratio = (
                                    signal_dates / len(all_dates)
                                    if all_dates
                                    else float("nan")
                                )
                                mean_daily = (
                                    float(daily.mean())
                                    if len(daily)
                                    else float("nan")
                                )
                                stress_mean = (
                                    float(stress_daily.mean())
                                    if len(stress_daily)
                                    else float("nan")
                                )
                                max_drawdown = (
                                    float(drawdown.min())
                                    if len(drawdown)
                                    else float("nan")
                                )
                                checks = {
                                    "minimum_signal_dates": (
                                        signal_dates
                                        >= self.config.policy_min_signal_dates
                                    ),
                                    "minimum_filled_trades": (
                                        len(filled)
                                        >= self.config.policy_min_filled_trades
                                    ),
                                    "minimum_signal_date_ratio": (
                                        math.isfinite(signal_ratio)
                                        and signal_ratio
                                        >= self.config.policy_min_signal_date_ratio
                                    ),
                                    "maximum_no_signal_streak": (
                                        no_signal_streak
                                        <= self.config.policy_max_no_signal_streak
                                    ),
                                    "positive_mean_daily_return": (
                                        math.isfinite(mean_daily)
                                        and mean_daily > 0.0
                                    ),
                                    "positive_2x_cost_stress": (
                                        math.isfinite(stress_mean)
                                        and stress_mean > 0.0
                                    ),
                                    "realized_big_loss_cap": (
                                        math.isfinite(big_loss_rate)
                                        and big_loss_rate
                                        <= self.config.policy_max_realized_big_loss_rate
                                    ),
                                    "tail_mean_floor": (
                                        math.isfinite(tail_mean)
                                        and tail_mean
                                        >= self.config.policy_min_tail_mean_return
                                    ),
                                }
                                feasible = all(checks.values())
                                objective = (
                                    stress_mean
                                    + 0.10 * min(tail_mean, 0.0)
                                    + 0.02 * min(max_drawdown, 0.0)
                                    if all(
                                        math.isfinite(value)
                                        for value in (
                                            stress_mean,
                                            tail_mean,
                                            max_drawdown,
                                        )
                                    )
                                    else float("-inf")
                                )
                                candidates.append(
                                    {
                                        "feasible": feasible,
                                        "objective": objective,
                                        "max_positions": positions,
                                        "thresholds": {
                                            "min_fill_probability": float(
                                                min_fill
                                            ),
                                            "min_exit_probability": float(
                                                self.config.policy_min_exit_probability
                                            ),
                                            "max_big_loss_probability": float(
                                                max_big_loss
                                            ),
                                            "min_mean_return_lcb": float(
                                                min_mean_lcb
                                            ),
                                            "min_conservative_ev": float(
                                                min_ev
                                            ),
                                            "min_selection_score": float(
                                                min_score
                                            ),
                                        },
                                        "diagnostics": {
                                            "signals": int(len(selected)),
                                            "signal_dates": int(signal_dates),
                                            "signal_date_ratio": _safe_metric(
                                                signal_ratio
                                            ),
                                            "max_no_signal_streak": int(
                                                no_signal_streak
                                            ),
                                            "filled_trades": int(len(filled)),
                                            "fill_rate": _safe_metric(
                                                len(filled) / len(selected)
                                                if len(selected)
                                                else np.nan
                                            ),
                                            "mean_trade_net_return": _safe_metric(
                                                realized.mean()
                                            ),
                                            "mean_daily_return": _safe_metric(
                                                mean_daily
                                            ),
                                            "stress_2x_cost_mean_daily_return": _safe_metric(
                                                stress_mean
                                            ),
                                            "realized_big_loss_rate": _safe_metric(
                                                big_loss_rate
                                            ),
                                            "tail_10pct_mean_return": _safe_metric(
                                                tail_mean
                                            ),
                                            "max_drawdown": _safe_metric(
                                                max_drawdown
                                            ),
                                            "checks": checks,
                                        },
                                    }
                                )

        feasible_candidates = [
            candidate for candidate in candidates if candidate["feasible"]
        ]
        pool = feasible_candidates or candidates
        if not pool:
            base_payload["reason"] = "no_policy_candidate_evaluated"
            return base_payload
        best = max(
            pool,
            key=lambda candidate: (
                _finite(candidate.get("objective"), float("-inf")),
                _finite(
                    candidate["diagnostics"].get(
                        "stress_2x_cost_mean_daily_return"
                    ),
                    float("-inf"),
                ),
                -int(candidate["max_positions"]),
            ),
        )
        return {
            **base_payload,
            "ready": bool(best["feasible"]),
            "reason": (
                "passed_independent_policy_holdout"
                if best["feasible"]
                else "no_policy_passed_independent_holdout"
            ),
            "tuning_start": tuning_dates[0],
            "tuning_end": tuning_dates[-1],
            "candidate_policies_evaluated": int(len(candidates)),
            "feasible_policy_count": int(len(feasible_candidates)),
            "thresholds": best["thresholds"],
            "max_positions": int(best["max_positions"]),
            "diagnostics": best["diagnostics"],
        }

    def _current_base(self, signal_date: str, candidates: pd.DataFrame) -> pd.DataFrame:
        dates = self.market_dates()
        d_daily_table = self.market_table(signal_date, "daily")
        d_limit_table = self.market_table(signal_date, "stk_limit")
        market_context = self._market_context(signal_date)
        rows: list[dict[str, Any]] = []
        for _, candidate in candidates.iterrows():
            code = candidate["ts_code"]
            d_daily = self._row(d_daily_table, code)
            if d_daily is None:
                continue
            d_close = _numeric_from(d_daily, ("close",))
            if d_close <= 0:
                continue
            d_limit = self._row(d_limit_table, code)
            limit_ratio = self._limit_ratio(d_daily, d_limit)
            mechanism_limit_pct = _finite(
                candidate.get("decision_limit_pct"),
                limit_ratio * 100.0,
            )
            if mechanism_limit_pct > self.config.max_mechanism_limit_pct + EPS:
                continue
            d_up_limit = _numeric_from(d_limit, ("up_limit",)) if d_limit is not None else float("nan")
            if not math.isfinite(d_up_limit) or not _is_close(d_daily.get("close"), d_up_limit):
                continue
            features = self._feature_dict(
                candidate,
                d_daily,
                limit_ratio,
                market_context=market_context,
                signal_date=signal_date,
            )
            consecutive_limit_ups = self._consecutive_limit_up_count(signal_date, code, dates)
            features["limit_times"] = float(consecutive_limit_ups)
            features.update(
                self._streak_path_features(
                    signal_date,
                    code,
                    dates,
                )
            )
            features.update(
                self._promotion_source_context_features(
                    signal_date,
                    code,
                    dates,
                )
            )
            rows.append(
                {
                    "signal_date": signal_date,
                    "ts_code": code,
                    "name": str(candidate.get("name", candidate.get("股票", ""))),
                    "industry": _text_from(candidate, INDUSTRY_ALIASES),
                    "stage": f"{consecutive_limit_ups}→{consecutive_limit_ups + 1}",
                    "source_rank": features["source_rank"],
                    "d_close": d_close,
                    "estimated_up_limit": _round_price(d_close * (1.0 + limit_ratio)),
                    "mechanism_limit_pct": mechanism_limit_pct,
                    "decision_universe_rule": str(candidate.get("decision_universe_rule", "a_share_price_limit_le_10_v2")),
                    "decision_universe_reason": str(candidate.get("decision_universe_reason", "eligible")),
                    **features,
                }
            )
        base = self._attach_cohort_features(pd.DataFrame(rows))
        return attach_promotion_source_features(base, self.config.root)

    def _walkforward_predictions(self, history: pd.DataFrame) -> pd.DataFrame:
        if history.empty:
            return pd.DataFrame()
        dates = sorted(history["signal_date"].unique())
        output: list[pd.DataFrame] = []
        start = self.config.min_train_dates + self.config.embargo_dates
        remaining_dates = max(0, len(dates) - start)
        bounded_block_dates = max(
            self.config.backtest_block_dates,
            int(
                math.ceil(
                    remaining_dates
                    / max(1, self.config.backtest_max_refits)
                )
            ),
        )
        for refit_index, block_start in enumerate(
            range(start, len(dates), bounded_block_dates),
            start=1,
        ):
            test_dates = dates[
                block_start : block_start + bounded_block_dates
            ]
            train_end = block_start - self.config.embargo_dates
            train_dates = dates[:train_end]
            if len(train_dates) < self.config.min_train_dates:
                continue
            train = history[history["signal_date"].isin(train_dates)].copy()
            bundle = self.fit_models(
                train,
                fast_backtest=True,
            )
            if bundle is None:
                continue
            test = history[history["signal_date"].isin(test_dates)].copy()
            scored = self.score_candidates(test, bundle)
            scored["oos_train_end"] = train_dates[-1]
            scored["oos_train_dates"] = len(train_dates)
            scored["oos_refit_index"] = refit_index
            scored["oos_block_dates"] = bounded_block_dates
            output.append(scored)
        if not output:
            return pd.DataFrame()
        out = pd.concat(output, ignore_index=True)
        out["cap_accepted"] = (out["actual_buy_gap"] <= out["recommended_max_gap"] + EPS).astype(int)
        out["strategy_filled"] = (
            out["selected"].eq(1) & out["cap_accepted"].eq(1) & out["market_fill"].eq(1)
        ).astype(int)
        policy_positions = (
            pd.to_numeric(
                out.get("policy_max_positions"),
                errors="coerce",
            )
            .fillna(self.config.max_positions)
            .clip(lower=1.0)
        )
        out["strategy_weight"] = np.where(
            out["selected"].eq(1),
            1.0 / policy_positions,
            0.0,
        )
        out["shadow_cap_accepted"] = (
            pd.to_numeric(out["actual_buy_gap"], errors="coerce")
            <= pd.to_numeric(out["diagnostic_gap"], errors="coerce") + EPS
        ).fillna(False).astype(int)
        out["shadow_market_filled"] = (
            out["shadow_selected"].eq(1)
            & pd.to_numeric(out["net_return"], errors="coerce").notna()
        ).astype(int)
        out["shadow_limit_filled"] = (
            out["shadow_selected"].eq(1)
            & out["shadow_cap_accepted"].eq(1)
            & out["market_fill"].eq(1)
        ).astype(int)
        out["strategy_net_return"] = np.where(out["strategy_filled"].eq(1), out["net_return"], np.nan)
        out["strategy_portfolio_return"] = np.where(
            out["strategy_filled"].eq(1),
            pd.to_numeric(out["net_return"], errors="coerce")
            * pd.to_numeric(out["strategy_weight"], errors="coerce"),
            0.0,
        )
        out["shadow_market_net_return"] = np.where(
            out["shadow_market_filled"].eq(1),
            out["net_return"],
            np.nan,
        )
        out["shadow_limit_net_return"] = np.where(
            out["shadow_limit_filled"].eq(1),
            out["net_return"],
            np.nan,
        )
        out["forecast_error"] = np.where(
            out["strategy_filled"].eq(1), out["net_return"] - out["predicted_net_return"], np.nan
        )
        out["direction_success"] = np.where(
            out["strategy_filled"].eq(1),
            ((out["predicted_net_return"] > 0) == (out["net_return"] > 0)).astype(int),
            np.nan,
        )
        out["within_prediction_interval"] = np.where(
            out["strategy_filled"].eq(1),
            (
                (out["net_return"] >= out["predicted_outcome_q10"])
                & (out["net_return"] <= out["predicted_outcome_q90"])
            ).astype(int),
            np.nan,
        )
        return out

    def _block_bootstrap_positive_probability(self, daily: pd.Series, block: int = 5, samples: int = 1000) -> float:
        values = pd.to_numeric(daily, errors="coerce").dropna().to_numpy(dtype=float)
        if len(values) < block:
            return float("nan")
        rng = np.random.default_rng(20260716)
        starts = np.arange(0, max(1, len(values) - block + 1))
        means = []
        blocks_needed = int(math.ceil(len(values) / block))
        for _ in range(samples):
            sample = np.concatenate([values[s : s + block] for s in rng.choice(starts, blocks_needed, replace=True)])[: len(values)]
            means.append(float(np.mean(sample)))
        return float(np.mean(np.asarray(means) > 0.0))

    def _shadow_policy_metrics(
        self,
        oos: pd.DataFrame,
        *,
        top_n: int,
        respect_limit: bool,
    ) -> dict[str, Any]:
        dates = sorted(oos["signal_date"].astype(str).unique())
        rank = pd.to_numeric(
            oos.get(
                "shadow_rank",
                pd.Series(np.nan, index=oos.index),
            ),
            errors="coerce",
        )
        selected = oos[rank.le(float(top_n)).fillna(False)].copy()
        signal_dates = set(selected["signal_date"].astype(str))
        market_buyable = pd.to_numeric(
            selected.get(
                "market_fill",
                pd.Series(0, index=selected.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        if respect_limit:
            filled = selected[
                pd.to_numeric(
                    selected.get(
                        "shadow_cap_accepted",
                        pd.Series(0, index=selected.index),
                    ),
                    errors="coerce",
                ).fillna(0).eq(1)
                & pd.to_numeric(
                    selected.get(
                        "market_fill",
                        pd.Series(0, index=selected.index),
                    ),
                    errors="coerce",
                ).fillna(0).eq(1)
            ].copy()
            execution_mode = "diagnostic_limit_cap"
        else:
            filled = selected[
                pd.to_numeric(
                    selected.get(
                        "net_return",
                        pd.Series(np.nan, index=selected.index),
                    ),
                    errors="coerce",
                ).notna()
            ].copy()
            execution_mode = "forced_market_open_truth"
        realized = pd.to_numeric(
            filled.get(
                "net_return",
                pd.Series(np.nan, index=filled.index),
            ),
            errors="coerce",
        ).dropna()
        daily = (
            pd.to_numeric(
                filled.get(
                    "net_return",
                    pd.Series(np.nan, index=filled.index),
                ),
                errors="coerce",
            )
            .groupby(filled["signal_date"].astype(str))
            .sum()
            .reindex(dates, fill_value=0.0)
            / float(max(1, top_n))
        )
        nav = (1.0 + daily).cumprod()
        drawdown = nav / nav.cummax() - 1.0
        positives = realized[realized > 0].sum()
        negatives = -realized[realized < 0].sum()
        tail_count = (
            max(1, int(math.ceil(len(realized) * 0.10)))
            if len(realized)
            else 0
        )
        no_signal_streak = 0
        current_streak = 0
        for signal_date in dates:
            if signal_date in signal_dates:
                current_streak = 0
            else:
                current_streak += 1
                no_signal_streak = max(no_signal_streak, current_streak)
        return {
            "top_n": int(top_n),
            "execution_mode": execution_mode,
            "signals": int(len(selected)),
            "signal_dates": int(len(signal_dates)),
            "signal_date_ratio": _safe_metric(
                len(signal_dates) / len(dates) if dates else np.nan
            ),
            "max_no_signal_streak": int(no_signal_streak),
            "filled_trades": int(len(realized)),
            "fill_rate": _safe_metric(
                len(realized) / len(selected) if len(selected) else np.nan
            ),
            "market_buyable_trades": int(market_buyable.sum()),
            "market_buyable_rate": _safe_metric(
                market_buyable.mean() if len(selected) else np.nan
            ),
            "mean_trade_net_return": _safe_metric(realized.mean()),
            "median_trade_net_return": _safe_metric(realized.median()),
            "win_rate": _safe_metric((realized > 0).mean()),
            "realized_big_loss_rate": _safe_metric(
                (realized <= self.config.big_loss_threshold).mean()
            ),
            "tail_10pct_mean_return": _safe_metric(
                realized.nsmallest(tail_count).mean()
                if tail_count
                else np.nan
            ),
            "worst_trade_net_return": _safe_metric(realized.min()),
            "profit_factor": _safe_metric(
                positives / negatives if negatives > 0 else np.nan
            ),
            "mean_daily_return": _safe_metric(daily.mean()),
            "cumulative_return": _safe_metric(
                nav.iloc[-1] - 1.0 if len(nav) else np.nan
            ),
            "max_drawdown": _safe_metric(
                drawdown.min() if len(drawdown) else np.nan
            ),
            "bootstrap_probability_mean_positive": _safe_metric(
                self._block_bootstrap_positive_probability(daily)
            ),
            "continuation_hit_rate": _safe_metric(
                pd.to_numeric(
                    filled.get(
                        "continuation_limit_up_hit",
                        pd.Series(np.nan, index=filled.index),
                    ),
                    errors="coerce",
                ).mean()
            ),
        }

    def _cohort_policy_metrics(
        self,
        oos: pd.DataFrame,
        mask: pd.Series,
        *,
        cohort: str,
    ) -> dict[str, Any]:
        """Evaluate every member of a cohort with equal weight inside each day."""
        dates = sorted(oos["signal_date"].astype(str).unique())
        eligible = mask.reindex(oos.index, fill_value=False).fillna(False).astype(bool)
        selected = oos.loc[eligible].copy()
        selected_returns = pd.to_numeric(
            selected.get(
                "net_return",
                pd.Series(np.nan, index=selected.index),
            ),
            errors="coerce",
        )
        filled = selected.loc[selected_returns.notna()].copy()
        filled["_cohort_net_return"] = selected_returns.loc[filled.index]
        signal_dates = set(selected["signal_date"].astype(str))
        daily = (
            filled.groupby(filled["signal_date"].astype(str))["_cohort_net_return"]
            .mean()
            .reindex(dates, fill_value=0.0)
        )
        realized = filled["_cohort_net_return"]
        nav = (1.0 + daily).cumprod()
        drawdown = nav / nav.cummax() - 1.0
        positives = realized[realized > 0].sum()
        negatives = -realized[realized < 0].sum()
        tail_count = (
            max(1, int(math.ceil(len(realized) * 0.10)))
            if len(realized)
            else 0
        )
        no_signal_streak = 0
        current_streak = 0
        for signal_date in dates:
            if signal_date in signal_dates:
                current_streak = 0
            else:
                current_streak += 1
                no_signal_streak = max(no_signal_streak, current_streak)

        market_buyable = pd.to_numeric(
            selected.get(
                "market_fill",
                pd.Series(0, index=selected.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        stages: dict[str, Any] = {}
        if "stage" in filled.columns:
            for stage, group in filled.groupby("stage", dropna=False):
                stage_returns = pd.to_numeric(
                    group["_cohort_net_return"],
                    errors="coerce",
                ).dropna()
                stages[str(stage)] = {
                    "trades": int(len(stage_returns)),
                    "mean_net_return": _safe_metric(stage_returns.mean()),
                    "win_rate": _safe_metric((stage_returns > 0).mean()),
                    "continuation_hit_rate": _safe_metric(
                        pd.to_numeric(
                            group.get(
                                "continuation_limit_up_hit",
                                pd.Series(np.nan, index=group.index),
                            ),
                            errors="coerce",
                        ).mean()
                    ),
                }

        return {
            "cohort": cohort,
            "execution_mode": "forced_market_open_truth",
            "portfolio_weighting": "equal_weight_within_signal_date",
            "signals": int(len(selected)),
            "signal_dates": int(len(signal_dates)),
            "signal_date_ratio": _safe_metric(
                len(signal_dates) / len(dates) if dates else np.nan
            ),
            "max_no_signal_streak": int(no_signal_streak),
            "average_signals_per_signal_date": _safe_metric(
                len(selected) / len(signal_dates) if signal_dates else np.nan
            ),
            "filled_trades": int(len(realized)),
            "truth_coverage": _safe_metric(
                len(realized) / len(selected) if len(selected) else np.nan
            ),
            "market_buyable_trades": int(market_buyable.sum()),
            "market_buyable_rate": _safe_metric(
                market_buyable.mean() if len(selected) else np.nan
            ),
            "exit_on_time_rate": _safe_metric(
                pd.to_numeric(
                    filled.get(
                        "exit_on_time",
                        pd.Series(np.nan, index=filled.index),
                    ),
                    errors="coerce",
                ).mean()
            ),
            "mean_trade_net_return": _safe_metric(realized.mean()),
            "median_trade_net_return": _safe_metric(realized.median()),
            "win_rate": _safe_metric((realized > 0).mean()),
            "realized_big_loss_rate": _safe_metric(
                (realized <= self.config.big_loss_threshold).mean()
            ),
            "tail_10pct_mean_return": _safe_metric(
                realized.nsmallest(tail_count).mean()
                if tail_count
                else np.nan
            ),
            "worst_trade_net_return": _safe_metric(realized.min()),
            "profit_factor": _safe_metric(
                positives / negatives if negatives > 0 else np.nan
            ),
            "mean_daily_return": _safe_metric(daily.mean()),
            "cumulative_return": _safe_metric(
                nav.iloc[-1] - 1.0 if len(nav) else np.nan
            ),
            "max_drawdown": _safe_metric(
                drawdown.min() if len(drawdown) else np.nan
            ),
            "bootstrap_probability_mean_positive": _safe_metric(
                self._block_bootstrap_positive_probability(daily)
            ),
            "continuation_hit_rate": _safe_metric(
                pd.to_numeric(
                    filled.get(
                        "continuation_limit_up_hit",
                        pd.Series(np.nan, index=filled.index),
                    ),
                    errors="coerce",
                ).mean()
            ),
            "stage_breakdown": stages,
        }

    def _top_n_stage_metrics(
        self,
        oos: pd.DataFrame,
        stage_focus: pd.Series,
        *,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Report daily stage-focus TopN without padding short candidate days."""
        dates = sorted(oos["signal_date"].astype(str).unique())
        focus = (
            stage_focus.reindex(oos.index, fill_value=False)
            .fillna(False)
            .astype(bool)
        )
        rank = pd.to_numeric(
            oos.get(
                "shadow_rank",
                pd.Series(np.nan, index=oos.index),
            ),
            errors="coerce",
        )
        selected = focus & rank.between(1, top_n, inclusive="both").fillna(False)
        market_buyable = pd.to_numeric(
            oos.get(
                "market_fill",
                pd.Series(0, index=oos.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        selected_counts = (
            oos.loc[selected]
            .groupby(oos.loc[selected, "signal_date"].astype(str))
            .size()
            .reindex(dates, fill_value=0)
        )
        all_candidates = self._cohort_policy_metrics(
            oos,
            selected,
            cohort=f"daily_top{top_n}_2to3_and_3to4",
        )
        buyable_candidates = self._cohort_policy_metrics(
            oos,
            selected & market_buyable,
            cohort=f"daily_top{top_n}_market_buyable",
        )
        buyable_candidates["execution_mode"] = "market_buyable_at_open_truth"
        buyable_candidates["selection_rule"] = (
            f"shadow_rank_le_{top_n}_and_market_fill_eq_1"
        )
        return {
            "scope": "daily_ranked_2to3_and_3to4",
            "ranking_field": "shadow_rank",
            "top_n_cap": int(top_n),
            "padding_policy": "none",
            "oos_dates": int(len(dates)),
            "candidate_days": int(selected_counts.gt(0).sum()),
            "zero_candidate_days": int(selected_counts.eq(0).sum()),
            "days_below_cap": int(
                selected_counts.between(1, top_n - 1, inclusive="both").sum()
            ),
            "days_at_cap": int(selected_counts.eq(top_n).sum()),
            "average_candidates_per_candidate_day": _safe_metric(
                selected_counts[selected_counts.gt(0)].mean()
                if selected_counts.gt(0).any()
                else np.nan
            ),
            "all_candidates": all_candidates,
            "market_buyable_only": buyable_candidates,
        }

    def _gate_funnel(self, oos: pd.DataFrame) -> dict[str, Any]:
        ordered_gates = (
            ("stage_focus", "gate_stage_focus"),
            ("policy_ready", "gate_policy_ready"),
            ("exit_probability", "gate_exit_probability"),
            ("fill_probability", "gate_fill_probability"),
            ("big_loss_probability", "gate_big_loss_probability"),
            ("mean_return_lcb", "gate_mean_return_lcb"),
            ("conservative_ev", "gate_conservative_ev"),
            ("selection_score", "gate_selection_score"),
            ("combined_risk_gate", "risk_gate_pass"),
        )
        total_rows = int(len(oos))
        total_dates = int(
            oos.get(
                "signal_date",
                pd.Series(dtype=str),
            ).astype(str).nunique()
        )
        marginal: dict[str, Any] = {}
        sequential: dict[str, Any] = {}
        active = pd.Series(True, index=oos.index)
        for label, column in ordered_gates:
            source = (
                oos[column]
                if column in oos.columns
                else pd.Series(0, index=oos.index, dtype=int)
            )
            passed = pd.to_numeric(
                source,
                errors="coerce",
            ).fillna(0).eq(1)
            marginal_rows = int(passed.sum())
            marginal[label] = {
                "rows": marginal_rows,
                "row_rate": _safe_metric(
                    marginal_rows / total_rows if total_rows else np.nan
                ),
                "dates": int(
                    oos.loc[passed, "signal_date"].astype(str).nunique()
                )
                if "signal_date" in oos.columns
                else 0,
            }
            active &= passed
            active_rows = int(active.sum())
            sequential[label] = {
                "rows": active_rows,
                "row_rate": _safe_metric(
                    active_rows / total_rows if total_rows else np.nan
                ),
                "dates": int(
                    oos.loc[active, "signal_date"].astype(str).nunique()
                )
                if "signal_date" in oos.columns
                else 0,
            }
        reason_counts = (
            oos.get(
                "model_reason",
                pd.Series(dtype=str),
            )
            .fillna("unknown")
            .astype(str)
            .value_counts()
        )
        return {
            "rows_total": total_rows,
            "dates_total": total_dates,
            "marginal": marginal,
            "sequential": sequential,
            "rejection_reason_counts": {
                str(reason): int(count)
                for reason, count in reason_counts.items()
            },
        }

    def _portfolio_metrics(self, oos: pd.DataFrame, history_dates: int) -> dict[str, Any]:
        if oos.empty:
            return {
                "status": "insufficient_oos_history",
                "history_dates": history_dates,
                "oos_dates": 0,
                "promoted": False,
                "promotion_failures": ["no_walkforward_predictions"],
            }
        selected = oos[oos["selected"].eq(1)].copy()
        filled = selected[selected["strategy_filled"].eq(1)].copy()
        if "strategy_weight" not in filled.columns:
            filled["strategy_weight"] = 1.0 / float(
                max(1, self.config.max_positions)
            )
        if "strategy_portfolio_return" not in filled.columns:
            filled["strategy_portfolio_return"] = (
                pd.to_numeric(
                    filled.get("strategy_net_return"),
                    errors="coerce",
                )
                * pd.to_numeric(
                    filled["strategy_weight"],
                    errors="coerce",
                )
            )
        stage_focus_filled = filled[pd.to_numeric(filled.get("stage_focus"), errors="coerce").fillna(0).eq(1)]
        dates = sorted(oos["signal_date"].unique())
        signal_date_set = set(selected["signal_date"].astype(str))
        signal_dates = len(signal_date_set)
        signal_date_ratio = signal_dates / len(dates) if dates else float("nan")
        max_no_signal_streak = 0
        current_no_signal_streak = 0
        for date in dates:
            if str(date) in signal_date_set:
                current_no_signal_streak = 0
            else:
                current_no_signal_streak += 1
                max_no_signal_streak = max(max_no_signal_streak, current_no_signal_streak)
        daily = (
            filled.groupby("signal_date")["strategy_portfolio_return"]
            .sum()
            .reindex(dates, fill_value=0.0)
        )
        benchmark_rows = oos[pd.to_numeric(oos["source_rank"], errors="coerce") <= self.config.max_positions].copy()
        benchmark_rows = benchmark_rows[benchmark_rows["market_fill"].eq(1)]
        benchmark = benchmark_rows.groupby("signal_date")["net_return"].sum().reindex(dates, fill_value=0.0)
        benchmark = benchmark / float(self.config.max_positions)
        uncapped_rows = selected[selected["market_fill"].eq(1)].copy()
        uncapped_rows["uncapped_portfolio_return"] = (
            pd.to_numeric(
                uncapped_rows["net_return"],
                errors="coerce",
            )
            * pd.to_numeric(
                uncapped_rows.get(
                    "strategy_weight",
                    pd.Series(0.0, index=uncapped_rows.index),
                ),
                errors="coerce",
            ).fillna(0.0)
        )
        uncapped = (
            uncapped_rows.groupby("signal_date")["uncapped_portfolio_return"]
            .sum()
            .reindex(dates, fill_value=0.0)
        )
        nav = (1.0 + daily).cumprod()
        drawdown = nav / nav.cummax() - 1.0
        stress_15_trade = (
            filled["gross_return"] - 1.5 * self.config.cost_rate
        ) * pd.to_numeric(filled["strategy_weight"], errors="coerce")
        stress_15_daily = (
            stress_15_trade.groupby(filled["signal_date"])
            .sum()
            .reindex(dates, fill_value=0.0)
        )
        stress_trade = (
            filled["gross_return"] - 2.0 * self.config.cost_rate
        ) * pd.to_numeric(filled["strategy_weight"], errors="coerce")
        stress_daily = (
            stress_trade.groupby(filled["signal_date"])
            .sum()
            .reindex(dates, fill_value=0.0)
        )
        positives = filled.loc[filled["strategy_net_return"] > 0, "strategy_net_return"].sum()
        negatives = -filled.loc[filled["strategy_net_return"] < 0, "strategy_net_return"].sum()
        big_loss_rate = float((filled["strategy_net_return"] <= self.config.big_loss_threshold).mean()) if len(filled) else float("nan")
        tail_count = max(1, int(math.ceil(len(filled) * 0.10))) if len(filled) else 0
        tail_mean = float(filled["strategy_net_return"].nsmallest(tail_count).mean()) if tail_count else float("nan")
        rolling = daily.rolling(20).sum().dropna()
        month_keys = pd.Index(dates).str.slice(0, 6)
        monthly = daily.groupby(month_keys).sum()
        positive_month_ratio = float((monthly > 0).mean()) if len(monthly) else float("nan")
        positive_months = monthly[monthly > 0]
        max_month_contribution = float(positive_months.max() / positive_months.sum()) if len(positive_months) and positive_months.sum() > 0 else float("nan")
        mean_daily = float(daily.mean()) if len(daily) else float("nan")
        std_daily = float(daily.std(ddof=1)) if len(daily) > 1 else float("nan")
        sharpe = mean_daily / std_daily * math.sqrt(252.0) if std_daily > 0 else float("nan")
        bootstrap = self._block_bootstrap_positive_probability(daily)
        rank_pair = filled[["predicted_net_return", "strategy_net_return"]].dropna()
        rank_ic = float(rank_pair.corr(method="spearman").iloc[0, 1]) if len(rank_pair) >= 5 else float("nan")
        exit_on_time_rate = float(pd.to_numeric(filled.get("exit_on_time"), errors="coerce").mean()) if len(filled) else float("nan")
        market_regimes = sorted(
            {
                str(value)
                for value in oos.get(
                    "market_sentiment_regime_code",
                    pd.Series(dtype=str),
                ).dropna()
                if str(value).strip()
                and str(value).lower() not in {"nan", "none", "insufficient"}
            }
        )
        calibration: list[dict[str, Any]] = []
        if len(rank_pair) >= 20:
            try:
                bins = pd.qcut(rank_pair["predicted_net_return"], q=min(5, rank_pair["predicted_net_return"].nunique()), duplicates="drop")
                grouped = rank_pair.assign(_bin=bins).groupby("_bin", observed=True)
                for label, group in grouped:
                    calibration.append(
                        {
                            "bucket": str(label),
                            "trades": int(len(group)),
                            "predicted_mean": _safe_metric(group["predicted_net_return"].mean()),
                            "actual_mean": _safe_metric(group["strategy_net_return"].mean()),
                        }
                    )
            except Exception:
                calibration = []
        metrics: dict[str, Any] = {
            "status": "ok",
            "generated_at_utc": _utc_now(),
            "model_version": self.config.model_version,
            "history_dates": history_dates,
            "oos_dates": len(dates),
            "walkforward_refits": int(
                pd.to_numeric(
                    oos.get("oos_refit_index"),
                    errors="coerce",
                ).nunique()
            ),
            "walkforward_block_dates": int(
                pd.to_numeric(
                    oos.get("oos_block_dates"),
                    errors="coerce",
                ).max()
            ),
            "signals": int(len(selected)),
            "signal_dates": int(signal_dates),
            "signal_date_ratio": _safe_metric(signal_date_ratio),
            "max_no_signal_streak": int(max_no_signal_streak),
            "average_signals_per_signal_date": _safe_metric(
                len(selected) / signal_dates if signal_dates else np.nan
            ),
            "filled_trades": int(len(filled)),
            "fill_rate": _safe_metric(len(filled) / len(selected) if len(selected) else np.nan),
            "exit_on_time_rate": _safe_metric(exit_on_time_rate),
            "mean_trade_net_return": _safe_metric(filled["strategy_net_return"].mean()),
            "median_trade_net_return": _safe_metric(filled["strategy_net_return"].median()),
            "win_rate": _safe_metric((filled["strategy_net_return"] > 0).mean()),
            "stage_focus_signals": int(pd.to_numeric(selected.get("stage_focus"), errors="coerce").fillna(0).eq(1).sum()),
            "stage_focus_filled_trades": int(len(stage_focus_filled)),
            "stage_focus_continuation_hit_rate": _safe_metric(
                pd.to_numeric(stage_focus_filled.get("continuation_limit_up_hit"), errors="coerce").mean()
            ),
            "realized_big_loss_rate": _safe_metric(big_loss_rate),
            "big_loss_threshold": self.config.big_loss_threshold,
            "tail_10pct_mean_return": _safe_metric(tail_mean),
            "worst_trade_net_return": _safe_metric(filled["strategy_net_return"].min()),
            "profit_factor": _safe_metric(positives / negatives if negatives > 0 else np.nan),
            "mean_daily_return": _safe_metric(mean_daily),
            "stress_1_5x_cost_mean_daily_return": _safe_metric(stress_15_daily.mean()),
            "stress_2x_cost_mean_daily_return": _safe_metric(stress_daily.mean()),
            "benchmark_mean_daily_return": _safe_metric(benchmark.mean()),
            "uncapped_model_mean_daily_return": _safe_metric(uncapped.mean()),
            "cumulative_return": _safe_metric(nav.iloc[-1] - 1.0 if len(nav) else np.nan),
            "max_drawdown": _safe_metric(drawdown.min() if len(drawdown) else np.nan),
            "sharpe": _safe_metric(sharpe),
            "positive_20d_window_ratio": _safe_metric((rolling > 0).mean() if len(rolling) else np.nan),
            "positive_month_ratio": _safe_metric(positive_month_ratio),
            "max_positive_month_profit_contribution": _safe_metric(max_month_contribution),
            "bootstrap_probability_mean_positive": _safe_metric(bootstrap),
            "forecast_mae": _safe_metric(filled["forecast_error"].abs().mean()),
            "forecast_rmse": _safe_metric(math.sqrt(float((filled["forecast_error"] ** 2).mean())) if len(filled) else np.nan),
            "direction_accuracy": _safe_metric(pd.to_numeric(filled["direction_success"], errors="coerce").mean()),
            "prediction_interval_coverage": _safe_metric(pd.to_numeric(filled["within_prediction_interval"], errors="coerce").mean()),
            "return_rank_ic": _safe_metric(rank_ic),
            "return_calibration": calibration,
            "market_regimes": market_regimes,
            "market_regime_count": len(market_regimes),
        }
        failures: list[str] = []
        checks = {
            "history_dates": history_dates >= self.config.promotion_min_dates,
            "oos_dates": len(dates) >= self.config.promotion_min_oos_dates,
            "market_regime_coverage": (
                len(market_regimes)
                >= self.config.promotion_min_market_regimes
            ),
            "filled_trades": len(filled) >= self.config.promotion_min_filled_trades,
            "stage_focus_filled_trades": (
                len(stage_focus_filled) >= self.config.promotion_min_stage_focus_filled_trades
            ),
            "signal_date_ratio": (
                math.isfinite(signal_date_ratio)
                and signal_date_ratio >= self.config.min_oos_signal_date_ratio
            ),
            "no_signal_streak": max_no_signal_streak <= self.config.max_oos_no_signal_streak,
            "mean_daily_return": math.isfinite(mean_daily) and mean_daily > 0.0,
            "stress_2x_cost": len(stress_daily) > 0 and float(stress_daily.mean()) > 0.0,
            "bootstrap_95": math.isfinite(bootstrap) and bootstrap >= 0.95,
            "rolling_20d_60pct": len(rolling) > 0 and float((rolling > 0).mean()) >= 0.60,
            "positive_months_60pct": len(monthly) >= 6 and positive_month_ratio >= 0.60,
            "month_concentration": math.isfinite(max_month_contribution) and max_month_contribution <= 0.50,
            "beats_source_topn": len(benchmark) > 0 and mean_daily > float(benchmark.mean()),
            "price_cap_not_worse": len(uncapped) > 0 and mean_daily >= float(uncapped.mean()),
            "exit_on_time_90pct": math.isfinite(exit_on_time_rate) and exit_on_time_rate >= self.config.min_exit_probability,
            "big_loss_rate_cap": math.isfinite(big_loss_rate) and big_loss_rate <= self.config.max_big_loss_probability,
            "tail_loss_floor": math.isfinite(tail_mean) and tail_mean >= self.config.min_tail_mean_return,
        }
        failures.extend(name for name, passed in checks.items() if not passed)
        metrics["promotion_checks"] = checks
        metrics["promotion_failures"] = failures
        metrics["promoted"] = not failures
        path_oos: dict[str, Any] = {}
        if "path_label_code" in filled.columns:
            for label_code, group in filled.groupby("path_label_code", dropna=False):
                code = (
                    str(label_code)
                    if not pd.isna(label_code) and str(label_code).strip()
                    else "INSUFFICIENT"
                )
                path_returns = pd.to_numeric(
                    group.get("strategy_net_return"),
                    errors="coerce",
                ).dropna()
                path_hits = pd.to_numeric(
                    group.get("continuation_limit_up_hit"),
                    errors="coerce",
                ).dropna()
                path_oos[code] = {
                    "label": PATH_LABELS.get(code, code),
                    "filled_trades": int(len(path_returns)),
                    "mean_net_return": _safe_metric(path_returns.mean()),
                    "win_rate": _safe_metric((path_returns > 0).mean()),
                    "continuation_hit_rate": _safe_metric(path_hits.mean()),
                }
        metrics["path_oos"] = path_oos
        metrics["gate_funnel"] = self._gate_funnel(oos)
        metrics["shadow_policies"] = {
            "top1_market_at_open": self._shadow_policy_metrics(
                oos,
                top_n=1,
                respect_limit=False,
            ),
            "top2_market_at_open": self._shadow_policy_metrics(
                oos,
                top_n=2,
                respect_limit=False,
            ),
            "top1_diagnostic_limit": self._shadow_policy_metrics(
                oos,
                top_n=1,
                respect_limit=True,
            ),
            "top2_diagnostic_limit": self._shadow_policy_metrics(
                oos,
                top_n=2,
                respect_limit=True,
            ),
        }
        stage_focus = pd.to_numeric(
            oos.get(
                "stage_focus",
                pd.Series(0, index=oos.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        metrics["stage_focus_all"] = self._cohort_policy_metrics(
            oos,
            stage_focus,
            cohort="all_2to3_and_3to4",
        )
        shadow_rank = pd.to_numeric(
            oos.get(
                "shadow_rank",
                pd.Series(np.nan, index=oos.index),
            ),
            errors="coerce",
        )
        metrics["rank_bucket_oos"] = {
            "rank_1": self._cohort_policy_metrics(
                oos,
                stage_focus & shadow_rank.eq(1),
                cohort="rank_1",
            ),
            "rank_2": self._cohort_policy_metrics(
                oos,
                stage_focus & shadow_rank.eq(2),
                cohort="rank_2",
            ),
            "rank_3_to_5": self._cohort_policy_metrics(
                oos,
                stage_focus & shadow_rank.between(3, 5, inclusive="both"),
                cohort="rank_3_to_5",
            ),
            "rank_6_to_10": self._cohort_policy_metrics(
                oos,
                stage_focus & shadow_rank.between(6, 10, inclusive="both"),
                cohort="rank_6_to_10",
            ),
            "rank_11_plus": self._cohort_policy_metrics(
                oos,
                stage_focus & shadow_rank.ge(11),
                cohort="rank_11_plus",
            ),
        }
        metrics["top10_oos"] = self._top_n_stage_metrics(
            oos,
            stage_focus,
            top_n=10,
        )
        path_code = oos.get(
            "path_label_code",
            pd.Series("", index=oos.index),
        ).fillna("").astype(str)
        metrics["path_shadow_policies"] = {
            "ACCELERATION_CONSENSUS": self._cohort_policy_metrics(
                oos,
                stage_focus & path_code.eq("ACCELERATION_CONSENSUS"),
                cohort="ACCELERATION_CONSENSUS",
            ),
            "WEAK_TO_STRONG": self._cohort_policy_metrics(
                oos,
                stage_focus & path_code.eq("WEAK_TO_STRONG"),
                cohort="WEAK_TO_STRONG",
            ),
        }
        metrics["daily_equity"] = [
            {"signal_date": date, "daily_return": _safe_metric(daily.loc[date]), "nav": _safe_metric(nav.loc[date])}
            for date in dates
        ]
        return metrics

    def run_backtest(self, history: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        oos = self._walkforward_predictions(history)
        metrics = self._portfolio_metrics(oos, history["signal_date"].nunique() if not history.empty else 0)
        first_layer_promotion = {
            "promoted": metrics.get("promoted") is True,
            "promotion_checks": dict(metrics.get("promotion_checks") or {}),
            "promotion_failures": list(metrics.get("promotion_failures") or []),
        }
        _write_csv(history, self.config.metrics_root / "training_history_latest.csv")
        trade_audit = oos.copy()
        if "selected" in trade_audit.columns:
            selected = pd.to_numeric(trade_audit["selected"], errors="coerce").fillna(0).eq(1)
            trade_audit = trade_audit.loc[selected].copy()
        _write_csv(trade_audit, self.config.metrics_root / "backtest_trades_latest.csv")
        _write_csv(
            oos,
            self.config.metrics_root / "backtest_gate_audit_latest.csv",
        )
        shadow_audit = oos.copy()
        if "shadow_selected" in shadow_audit.columns:
            shadow_selected = pd.to_numeric(
                shadow_audit["shadow_selected"],
                errors="coerce",
            ).fillna(0).eq(1)
            shadow_audit = shadow_audit.loc[shadow_selected].copy()
        _write_csv(
            shadow_audit,
            self.config.metrics_root / "backtest_shadow_latest.csv",
        )
        stage_focus_audit = oos[
            pd.to_numeric(
                oos.get(
                    "stage_focus",
                    pd.Series(0, index=oos.index),
                ),
                errors="coerce",
            ).fillna(0).eq(1)
        ].copy()
        _write_csv(
            stage_focus_audit,
            self.config.metrics_root / "backtest_stage_focus_all_latest.csv",
        )
        top10_audit = prepare_observation_top10(
            oos,
            limit=self.config.max_observation_candidates,
        )
        all_oos_dates = (
            sorted(oos["signal_date"].astype(str).unique())
            if not oos.empty
            else []
        )
        metrics["top10_oos"] = observation_top10_metrics(
            top10_audit,
            all_oos_dates=all_oos_dates,
            cost_rate=self.config.cost_rate,
        )
        _write_csv(
            top10_audit,
            self.config.metrics_root / "backtest_top10_latest.csv",
        )
        top10_buyable = pd.to_numeric(
            top10_audit.get(
                "market_fill",
                pd.Series(0, index=top10_audit.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        _write_csv(
            top10_audit.loc[top10_buyable].copy(),
            self.config.metrics_root
            / "backtest_top10_market_buyable_latest.csv",
        )
        path_focus_audit = stage_focus_audit[
            stage_focus_audit.get(
                "path_label_code",
                pd.Series("", index=stage_focus_audit.index),
            )
            .fillna("")
            .astype(str)
            .isin(("ACCELERATION_CONSENSUS", "WEAK_TO_STRONG"))
        ].copy()
        _write_csv(
            path_focus_audit,
            self.config.metrics_root / "backtest_path_focus_latest.csv",
        )
        trade_oos, trade_bundle, trade_metrics = walkforward_trade_selector(
            top10_audit,
            cost_rate=self.config.cost_rate,
        )
        self._trade_selector_bundle = trade_bundle
        self._trade_selector_metrics = trade_metrics
        metrics["first_layer_promotion"] = first_layer_promotion
        metrics["trade_selector"] = trade_metrics
        metrics["promoted"] = trade_metrics.get("promoted") is True
        metrics["promotion_checks"] = dict(
            trade_metrics.get("promotion_checks") or {}
        )
        metrics["promotion_failures"] = list(
            trade_metrics.get("promotion_failures") or []
        )
        _write_csv(
            trade_oos,
            self.config.metrics_root
            / "backtest_trade_selector_oos_latest.csv",
        )
        trade_selected = pd.to_numeric(
            trade_oos.get(
                "trade_selected",
                pd.Series(0, index=trade_oos.index),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        _write_csv(
            trade_oos.loc[trade_selected].copy(),
            self.config.metrics_root
            / "backtest_trade_selector_selected_latest.csv",
        )
        _write_json(metrics, self.config.metrics_root / "backtest_latest.json")
        return oos, metrics

    # ------------------------------------------------------------------
    # Immutable current prediction and matured true-price verification
    # ------------------------------------------------------------------
    def _prediction_dates(self, signal_date: str, candidates: pd.DataFrame) -> tuple[str, str]:
        expected_buy = ""
        if "verify_date" in candidates.columns:
            values = candidates["verify_date"].dropna().map(_normal_date)
            values = values[values.str.len() == 8]
            if not values.empty:
                expected_buy = values.iloc[0]
        expected_buy = choose_exec_date(signal_date, expected_buy)
        return expected_buy, choose_exit_date(expected_buy)

    def _prediction_revision_allowed(self, expected_buy: str) -> bool:
        expected_buy = _normal_date(expected_buy)
        if not expected_buy:
            return False
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.strftime("%Y%m%d")
        if today < expected_buy:
            return True
        if today > expected_buy:
            return False
        return (now.hour, now.minute, now.second) < (9, 25, 0)

    def build_prediction(
        self,
        signal_date: str,
        candidates: pd.DataFrame,
        bundle: Optional[ModelBundle],
        backtest_metrics: dict[str, Any],
        *,
        force: bool = False,
    ) -> pd.DataFrame:
        dated_path = self.config.prediction_root / f"pred_{signal_date}.csv"
        latest_path = self.config.prediction_root / "pred_latest.csv"
        expected_buy, expected_exit = self._prediction_dates(
            signal_date,
            candidates,
        )
        if (
            not dated_path.exists()
            and latest_path.exists()
            and not force
        ):
            latest = _read_csv(latest_path)
            latest_signal = (
                _normal_date(latest["signal_date"].iloc[0])
                if not latest.empty
                and "signal_date" in latest.columns
                else ""
            )
            latest_buy = (
                _normal_date(latest["expected_buy_date"].iloc[0])
                if not latest.empty
                and "expected_buy_date" in latest.columns
                else ""
            )
            if (
                latest_signal == _normal_date(signal_date)
                and latest_buy == _normal_date(expected_buy)
                and not self._prediction_revision_allowed(expected_buy)
            ):
                _write_csv(latest, dated_path)
                return latest
        if dated_path.exists() and not force:
            frozen = _read_csv(dated_path)
            if not frozen.empty:
                frozen_version = str(frozen.get("model_version", pd.Series(["legacy"])).iloc[0])
                frozen_artifact = str(
                    frozen.get(
                        "model_artifact_sha256",
                        pd.Series([""]),
                    ).iloc[0]
                    or ""
                ).strip()
                current_artifact = (
                    bundle.model_artifact_sha256
                    if bundle is not None
                    else ""
                )
                same_version = frozen_version == self.config.model_version
                same_artifact = bool(
                    current_artifact
                    and frozen_artifact == current_artifact
                )
                if same_version and (bundle is None or same_artifact):
                    _write_csv(frozen, self.config.prediction_root / "pred_latest.csv")
                    return frozen
                if not self._prediction_revision_allowed(expected_buy):
                    _write_csv(frozen, self.config.prediction_root / "pred_latest.csv")
                    return frozen
                safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", frozen_version).strip("._")[:80] or "legacy"
                archive_suffix = (
                    f"{safe_version}_{frozen_artifact[:12] or 'missing_artifact'}"
                    if same_version
                    else safe_version
                )
                archive_path = (
                    self.config.prediction_root
                    / f"pred_{signal_date}_{archive_suffix}.csv"
                )
                if not archive_path.exists():
                    _write_csv(frozen, archive_path)
        base = self._current_base(signal_date, candidates)
        scored = self.score_candidates(base, bundle)
        promoted = backtest_metrics.get("promoted") is True
        scored["prediction_id"] = [f"{signal_date}-{code}-{self.config.model_version}" for code in scored["ts_code"]]
        scored["expected_buy_date"] = expected_buy
        scored["expected_exit_date"] = expected_exit
        scored["recommended_max_price"] = [
            _round_price(min(row["estimated_up_limit"] - 0.01, row["d_close"] * (1.0 + row["recommended_max_gap"])))
            if math.isfinite(_finite(row["recommended_max_gap"]))
            else np.nan
            for _, row in scored.iterrows()
        ]
        scored["max_auction_change_pct"] = (100.0 * scored["recommended_max_gap"]).round(2)
        scored["stage_transition"] = scored["stage"].where(scored["stage"].astype(str).str.strip().ne(""), "-")
        observation_contracts = [
            observation_price_contract(row.to_dict())
            for _, row in scored.iterrows()
        ]
        scored["observation_max_price"] = [
            item["observation_max_price"] for item in observation_contracts
        ]
        scored["observation_auction_change_pct"] = [
            item["observation_auction_change_pct"] for item in observation_contracts
        ]
        scored["observation_price_basis"] = [
            item["observation_price_basis"] for item in observation_contracts
        ]
        scored["observation_price_is_formal"] = [
            int(item["observation_price_is_formal"]) for item in observation_contracts
        ]
        observation_rows, observation_pool_size = rank_observation_rows(
            scored.to_dict(orient="records"),
            limit=self.config.max_observation_candidates,
        )
        observation_lookup = {
            str(row.get("ts_code")): row
            for row in observation_rows
        }
        scored["observation_rank"] = scored["ts_code"].map(
            lambda code: observation_lookup.get(str(code), {}).get("observation_rank")
        )
        scored["observation_risk_tier"] = scored["ts_code"].map(
            lambda code: observation_lookup.get(str(code), {}).get("observation_risk_tier")
        )
        scored["observation_risk_label"] = scored["ts_code"].map(
            lambda code: observation_lookup.get(str(code), {}).get("observation_risk_label", "")
        )
        scored["observation_selected"] = scored["observation_rank"].notna().astype(int)
        scored["observation_pool_size"] = int(observation_pool_size)
        scored["first_layer_selected"] = pd.to_numeric(
            scored.get("selected"),
            errors="coerce",
        ).fillna(0).astype(int)
        scored["first_layer_shadow_selected"] = pd.to_numeric(
            scored.get("shadow_selected"),
            errors="coerce",
        ).fillna(0).astype(int)
        trade_fields: dict[str, Any] = {
            "promotion_rank": np.nan,
            "promotion_rank_score": np.nan,
            "predicted_promotion_probability": np.nan,
            "trade_rank": np.nan,
            "trade_score": np.nan,
            "trade_predicted_conditional_net_return": np.nan,
            "trade_predicted_mean_return_lcb": np.nan,
            "trade_predicted_fill_probability": np.nan,
            "trade_predicted_big_loss_probability": np.nan,
            "trade_predicted_outcome_q10": np.nan,
            "trade_tail_loss_proxy": np.nan,
            "trade_base_score": np.nan,
            "trade_tail_risk_weight": np.nan,
            "trade_gate_pass": 0,
            "trade_shadow_selected": 0,
            "trade_selected": 0,
            "trade_selector_policy_ready": 0,
            "trade_selector_promoted": int(promoted),
            "trade_selector_version": TRADE_SELECTOR_VERSION,
            "trade_selector_artifact_sha256": "",
            "trade_selector_canonical_v2_version": TRADE_SELECTOR_CANONICAL_V2,
            "trade_selector_artifact_v2_sha256": "",
            "trade_selector_canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
            "trade_selector_canonical_decimals": RUNTIME_CANONICAL_DECIMALS,
            "trade_selector_execution_numeric_mode": "raw_float64",
            "trade_selector_raw_execution_preserved": True,
            "trade_model_reason": "outside_observation_top10",
        }
        for name, value in trade_fields.items():
            scored[name] = value
        observation_mask = scored["observation_selected"].eq(1)
        if observation_mask.any():
            trade_scored = score_trade_selector(
                scored.loc[observation_mask].copy(),
                self._trade_selector_bundle,
                globally_promoted=promoted,
            )
            for name in trade_fields:
                if name in trade_scored.columns:
                    scored.loc[trade_scored.index, name] = trade_scored[name]
        promotion_audit = (
            ((backtest_metrics.get("trade_selector") or {}).get("promotion_rank_oos"))
            or {}
        )
        scored["promotion_rank_quality_ready"] = int(
            ((promotion_audit.get("ranking_quality_gate") or {}).get("passed"))
            is True
        )
        scored["promotion_probability_quality_ready"] = int(
            ((promotion_audit.get("probability_quality_gate") or {}).get("passed"))
            is True
        )
        scored["selected"] = pd.to_numeric(
            scored["trade_selected"],
            errors="coerce",
        ).fillna(0).astype(int)
        scored["take_profit_pct"] = np.nan
        scored["stop_loss_pct"] = np.nan
        scored["take_profit_price"] = np.nan
        scored["stop_loss_price"] = np.nan
        scored["latest_exit_time"] = self.config.latest_exit_time
        scored["exit_policy_version"] = self.config.exit_policy_version
        scored["entry_rule"] = "系统仅供人工参考：T日9:25前仅用限价单参与集合竞价；不得使用无上限市价单，高于上限或未成交均放弃"
        scored["exit_rule"] = "T+1固定按9:30开盘集合竞价成交价退出；一字跌停无法成交时顺延至首个可成交开盘"
        scored["guidance_only"] = 1
        scored["broker_connected"] = 0
        scored["predicted_public_market_buyable_probability"] = pd.to_numeric(
            scored.get("predicted_fill_probability"),
            errors="coerce",
        )
        scored["trade_predicted_public_market_buyable_probability"] = pd.to_numeric(
            scored.get("trade_predicted_fill_probability"),
            errors="coerce",
        )
        scored["predicted_actual_order_fill_probability"] = np.nan
        scored["actual_order_fill_probability_available"] = 0
        scored["order_type"] = "LIMIT_ONLY_MANUAL"
        scored["market_order_allowed"] = 0
        scored["max_big_loss_probability"] = pd.to_numeric(
            scored.get("policy_max_big_loss_probability"),
            errors="coerce",
        ).fillna(self.config.max_big_loss_probability)
        scored["big_loss_threshold"] = float(self.config.big_loss_threshold)
        scored["min_return_lcb"] = pd.to_numeric(
            scored.get("policy_min_mean_return_lcb"),
            errors="coerce",
        ).fillna(self.config.min_return_lcb)
        scored["model_version"] = self.config.model_version
        scored["model_artifact_sha256"] = (
            bundle.model_artifact_sha256
            if bundle is not None
            else ""
        )
        scored["model_canonical_v2_version"] = MODEL_CANONICAL_V2
        scored["model_artifact_v2_sha256"] = (
            bundle.model_artifact_v2_sha256
            if bundle is not None
            else ""
        )
        scored["model_canonical_schema"] = CANONICAL_FINGERPRINT_SCHEMA
        scored["model_canonical_decimals"] = RUNTIME_CANONICAL_DECIMALS
        scored["model_execution_numeric_mode"] = "raw_float64"
        scored["model_raw_execution_preserved"] = True
        scored["model_ready"] = int(bundle is not None)
        scored["model_promoted"] = int(promoted)
        scored["first_layer_model_promoted"] = int(
            (backtest_metrics.get("first_layer_promotion") or {}).get(
                "promoted"
            )
            is True
        )
        scored["action"] = np.where(
            scored["selected"].eq(1),
            "BUY" if promoted else "SHADOW_ONLY",
            np.where(
                pd.to_numeric(
                    scored.get("trade_shadow_selected"),
                    errors="coerce",
                ).fillna(0).eq(1),
                "SHADOW_ONLY",
                np.where(
                    scored["model_reason"].eq(
                        "insufficient_independent_history"
                    ),
                    "WATCH",
                    "REJECT",
                ),
            ),
        )
        scored["price_action"] = np.where(
            scored["recommended_max_price"].notna(),
            "仅限人工限价单；竞价不高于上限价，超过即放弃" if promoted else "影子验证限价；当前不得买入",
            "没有安全买入价格，放弃",
        )
        scored["generated_at_utc"] = _utc_now()
        scored["source_snapshot_sha256"] = _hash_frame(candidates)
        scored["feature_contract"] = (
            "D_CLOSE_STREAK_PATH_SENTIMENT_V12_OPEN0930_TOP10_META_SELECTOR_NO_T_LEAKAGE"
        )
        ordered = [
            "prediction_id",
            "signal_date",
            "expected_buy_date",
            "expected_exit_date",
            "ts_code",
            "name",
            "industry",
            "limit_times",
            "stage",
            "stage_transition",
            "stage_focus",
            "path_label_code",
            "path_label",
            "path_explanation",
            "path_days_observed",
            "path_data_coverage",
            "path_strength_latest",
            "path_strength_delta",
            "path_gap_slope",
            "path_first_seal_slope",
            "path_open_times_slope",
            "path_turnover_slope",
            "path_amount_log_slope",
            "path_seal_ratio_slope",
            "path_one_price_ratio",
            "path_weak_to_strong",
            "path_strong_to_weak",
            "path_acceleration_consensus",
            "path_divergence_reseal",
            "stage_pool_size",
            "focus_pool_size",
            "market_max_limit_times",
            "same_industry_stage_count",
            "stage_pool_share",
            "stage_recent_promotion_rate",
            "stage_recent_promotion_samples",
            "market_median_return",
            "market_up_ratio",
            "market_return_dispersion",
            *MARKET_SENTIMENT_OUTPUT_FIELDS,
            "source_rank",
            "d_close",
            "estimated_up_limit",
            "diagnostic_gap",
            "recommended_max_gap",
            "recommended_max_price",
            "max_auction_change_pct",
            "observation_max_price",
            "observation_auction_change_pct",
            "observation_price_basis",
            "observation_price_is_formal",
            "observation_rank",
            "observation_risk_tier",
            "observation_risk_label",
            "observation_selected",
            "observation_pool_size",
            "promotion_rank",
            "promotion_rank_score",
            "predicted_promotion_probability",
            "promotion_rank_quality_ready",
            "promotion_probability_quality_ready",
            "trade_rank",
            "trade_score",
            "trade_predicted_conditional_net_return",
            "trade_predicted_mean_return_lcb",
            "trade_predicted_fill_probability",
            "trade_predicted_public_market_buyable_probability",
            "trade_predicted_big_loss_probability",
            "trade_predicted_outcome_q10",
            "trade_tail_loss_proxy",
            "trade_base_score",
            "trade_tail_risk_weight",
            "trade_gate_pass",
            "trade_shadow_selected",
            "trade_selected",
            "trade_selector_policy_ready",
            "trade_selector_promoted",
            "trade_selector_version",
            "trade_selector_artifact_sha256",
            "trade_selector_canonical_v2_version",
            "trade_selector_artifact_v2_sha256",
            "trade_selector_canonical_schema",
            "trade_selector_canonical_decimals",
            "trade_selector_execution_numeric_mode",
            "trade_selector_raw_execution_preserved",
            "trade_model_reason",
            "take_profit_pct",
            "stop_loss_pct",
            "take_profit_price",
            "stop_loss_price",
            "latest_exit_time",
            "exit_policy_version",
            "predicted_net_return",
            "predicted_return_lcb",
            "predicted_return_ucb",
            "predicted_mean_return_lcb",
            "predicted_mean_return_ucb",
            "predicted_outcome_q10",
            "predicted_outcome_q90",
            "predicted_fill_probability",
            "predicted_public_market_buyable_probability",
            "predicted_actual_order_fill_probability",
            "actual_order_fill_probability_available",
            "predicted_exit_probability",
            "predicted_profit_probability",
            "predicted_big_loss_probability",
            "predicted_continuation_limit_up_probability",
            "max_big_loss_probability",
            "big_loss_threshold",
            "min_return_lcb",
            "conservative_ev",
            "selection_score",
            "shadow_rank",
            "shadow_selected",
            "gate_policy_ready",
            "gate_stage_focus",
            "gate_exit_probability",
            "gate_fill_probability",
            "gate_big_loss_probability",
            "gate_mean_return_lcb",
            "gate_conservative_ev",
            "gate_selection_score",
            "selection_policy_version",
            "policy_max_big_loss_probability",
            "policy_min_mean_return_lcb",
            "policy_min_fill_probability",
            "policy_min_exit_probability",
            "policy_min_conservative_ev",
            "policy_min_selection_score",
            "policy_max_positions",
            "risk_gate_pass",
            "first_layer_selected",
            "first_layer_shadow_selected",
            "selected",
            "action",
            "price_action",
            "entry_rule",
            "exit_rule",
            "guidance_only",
            "broker_connected",
            "order_type",
            "market_order_allowed",
            "mechanism_limit_pct",
            "decision_universe_rule",
            "decision_universe_reason",
            "minute_available",
            "model_ready",
            "model_promoted",
            "first_layer_model_promoted",
            "model_reason",
            "model_version",
            "model_artifact_sha256",
            "model_canonical_v2_version",
            "model_artifact_v2_sha256",
            "model_canonical_schema",
            "model_canonical_decimals",
            "model_execution_numeric_mode",
            "model_raw_execution_preserved",
            "generated_at_utc",
            "source_snapshot_sha256",
            "feature_contract",
        ]
        scored = scored[[name for name in ordered if name in scored.columns]]
        _write_csv(scored, dated_path)
        _write_csv(scored, self.config.prediction_root / "pred_latest.csv")
        return scored

    def _manual_feedback(self) -> pd.DataFrame:
        frame = _read_csv(self.config.manual_feedback_path)
        if frame.empty:
            return frame
        frame = frame.copy()
        if "ts_code" in frame.columns:
            frame["ts_code"] = frame["ts_code"].map(_normal_code)
        if "signal_date" in frame.columns:
            frame["signal_date"] = frame["signal_date"].map(_normal_date)
        return frame

    def _manual_feedback_row(self, feedback: pd.DataFrame, signal_date: str, code: str) -> Optional[pd.Series]:
        if feedback.empty or not {"signal_date", "ts_code"}.issubset(feedback.columns):
            return None
        hit = feedback[(feedback["signal_date"] == signal_date) & (feedback["ts_code"] == code)]
        return hit.iloc[-1] if not hit.empty else None

    def _verify_prediction_file(self, path: Path, dates: Sequence[str], feedback: pd.DataFrame) -> pd.DataFrame:
        pred = _read_csv(path)
        if pred.empty:
            return pd.DataFrame()
        signal_date = _normal_date(pred.get("signal_date", pd.Series([""])).iloc[0])
        later = [date for date in dates if date > signal_date]
        records: list[dict[str, Any]] = []
        for _, row in pred.iterrows():
            code = _normal_code(row.get("ts_code"))
            selected = int(_finite(row.get("selected"), 0)) == 1
            base = row.to_dict()
            base.update(
                {
                    "actual_buy_date": "",
                    "actual_buy_price": np.nan,
                    "actual_exit_date": "",
                    "actual_exit_price": np.nan,
                    "actual_gross_return": np.nan,
                    "actual_net_return": np.nan,
                    "truth_source": "pending",
                    "verification_status": "PENDING_BUY",
                    "actual_fill": np.nan,
                    "actual_fill_reason": "truth_pending",
                    "price_guidance_success": np.nan,
                    "direction_success": np.nan,
                    "trade_success": np.nan,
                    "risk_prediction_success": np.nan,
                    "forecast_error": np.nan,
                    "correct_rejection": np.nan,
                    "missed_opportunity": np.nan,
                }
            )
            if not later:
                records.append(base)
                continue
            expected_buy = _normal_date(row.get("expected_buy_date"))
            buy_date = expected_buy or later[0]
            if buy_date not in dates:
                records.append(base)
                continue
            buy_daily = self._row(self.market_table(buy_date, "daily"), code)
            if buy_daily is None:
                base.update({"actual_buy_date": buy_date, "verification_status": "NO_FILL", "actual_fill": 0, "actual_fill_reason": "suspended_or_daily_missing"})
                records.append(base)
                continue
            buy_price = self._execution_open_price(buy_date, code, buy_daily)
            max_price = _finite(row.get("recommended_max_price"))
            market_fill, fill_reason = self._market_buyable(buy_date, code)
            cap_accept = math.isfinite(max_price) and buy_price <= max_price + 0.005
            actual_fill = int(selected and cap_accept and market_fill == 1)
            base.update(
                {
                    "actual_buy_date": buy_date,
                    "actual_buy_price": buy_price,
                    "actual_fill": actual_fill,
                    "actual_fill_reason": "filled_market_proxy" if actual_fill else ("price_above_cap" if not cap_accept else fill_reason),
                    "price_guidance_success": int((actual_fill == 1 and cap_accept) or (actual_fill == 0 and not cap_accept)),
                    "truth_source": self._execution_open_source(
                        buy_date,
                        code,
                    ),
                    "verification_status": "PENDING_EXIT" if actual_fill else "NO_FILL",
                }
            )
            expected_exit = _normal_date(row.get("expected_exit_date"))
            if not expected_exit or expected_exit not in dates:
                records.append(base)
                continue
            date_index = dates.index(expected_exit)
            exit_date, exit_price, _, exit_reason = self._resolve_exit(
                code,
                date_index,
                dates,
                entry_price=buy_price,
                buy_date=buy_date,
            )
            if not exit_date:
                records.append(base)
                continue
            gross = self._realized_gross_return(
                code,
                buy_date,
                buy_price,
                exit_date,
                dates,
                exit_price=exit_price,
            )
            net = gross - self.config.cost_rate if math.isfinite(gross) else np.nan
            predicted = _finite(row.get("predicted_net_return"))
            predicted_loss = _finite(row.get("predicted_big_loss_probability"))
            base.update(
                {
                    "actual_buy_price": buy_price,
                    "actual_exit_date": exit_date,
                    "actual_exit_price": exit_price,
                    "actual_gross_return": gross,
                    "actual_net_return": net,
                    "exit_reason": exit_reason,
                    "verification_status": "VERIFIED" if int(base["actual_fill"]) == 1 else "COUNTERFACTUAL_READY",
                    "direction_success": int((predicted > 0) == (net > 0)) if math.isfinite(predicted) else np.nan,
                    "trade_success": int(net > 0) if int(base["actual_fill"]) == 1 else np.nan,
                    "risk_prediction_success": int(net > self.config.big_loss_threshold) if math.isfinite(predicted_loss) else np.nan,
                    "forecast_error": net - predicted if math.isfinite(predicted) else np.nan,
                    "correct_rejection": int(net <= 0) if selected is False else np.nan,
                    "missed_opportunity": int(net > 0) if selected is False else np.nan,
                }
            )
            records.append(base)
        return pd.DataFrame(records)

    def _verify_observation_prediction_file(
        self,
        path: Path,
        dates: Sequence[str],
    ) -> pd.DataFrame:
        pred = _read_csv(path)
        if pred.empty:
            return pd.DataFrame()
        ranked, pool_size = rank_observation_rows(
            pred.to_dict(orient="records"),
            limit=self.config.max_observation_candidates,
        )
        records: list[dict[str, Any]] = []
        for row in ranked:
            signal_date = _normal_date(row.get("signal_date"))
            buy_date = _normal_date(row.get("expected_buy_date"))
            exit_date = _normal_date(row.get("expected_exit_date"))
            if not buy_date or buy_date < self.config.observation_validation_start_date:
                continue
            code = _normal_code(row.get("ts_code"))
            timing_status, timing_valid, prediction_deadline = (
                self._prediction_timing_status(
                    row.get("generated_at_utc"),
                    buy_date,
                )
            )
            base = dict(row)
            base.update(
                {
                    "signal_date": signal_date,
                    "expected_buy_date": buy_date,
                    "expected_exit_date": exit_date,
                    "observation_pool_size": int(pool_size),
                    "observation_rank": int(row.get("observation_rank") or 0),
                    "validation_mode": "market_at_open_proxy",
                    "observation_execution_mode": "market_at_open_proxy",
                    "prediction_timing_status": timing_status,
                    "prediction_timing_valid": timing_valid,
                    "prediction_deadline_utc": prediction_deadline,
                    "validation_status": "PENDING_T",
                    "actual_buy_date": "",
                    "actual_open_price": np.nan,
                    "actual_t_close": np.nan,
                    "market_daily_return": np.nan,
                    "observation_fill": np.nan,
                    "observation_fill_reason": "truth_pending",
                    "observation_limit_accept": np.nan,
                    "observation_price_vs_cap": np.nan,
                    "market_buyable_diagnostic": np.nan,
                    "market_buyable_reason": "truth_pending",
                    "observation_t_return": np.nan,
                    "continuation_limit_up_hit": np.nan,
                    "actual_exit_date": "",
                    "actual_exit_price": np.nan,
                    "actual_gross_return": np.nan,
                    "actual_net_return": np.nan,
                    "exit_reason": "",
                    "truth_source": "pending",
                    "truth_generated_at_utc": _utc_now(),
                }
            )
            if buy_date not in dates:
                records.append(base)
                continue
            buy_daily = self._row(self.market_table(buy_date, "daily"), code)
            if buy_daily is None:
                base.update(
                    {
                        "actual_buy_date": buy_date,
                        "observation_fill": 0,
                        "observation_fill_reason": "suspended_or_daily_missing",
                        "validation_status": "FINAL_NO_FILL",
                        "truth_source": "daily_missing",
                    }
                )
                records.append(base)
                continue

            open_price = self._execution_open_price(buy_date, code, buy_daily)
            close_price = _numeric_from(buy_daily, ("close",))
            daily_pct = _numeric_from(buy_daily, ("pct_chg",))
            d_close = _finite(row.get("d_close"))
            market_return = (
                daily_pct / 100.0
                if math.isfinite(daily_pct)
                else close_price / d_close - 1.0
                if close_price > 0 and d_close > 0
                else np.nan
            )
            max_price = _finite(row.get("observation_max_price"))
            market_fill, market_reason = self._market_buyable(buy_date, code)
            cap_accept = math.isfinite(max_price) and open_price > 0 and open_price <= max_price + 0.005
            price_vs_cap = (
                open_price / max_price - 1.0
                if open_price > 0 and math.isfinite(max_price) and max_price > 0
                else np.nan
            )
            # Observation truth uses a market-at-open proxy. The displayed cap
            # remains a risk diagnostic and does not gate the validation fill.
            observation_fill = int(open_price > 0)
            fill_reason = (
                "filled_market_at_open_proxy"
                if observation_fill
                else "invalid_market_open_price"
            )
            limit_row = self._row(self.market_table(buy_date, "stk_limit"), code)
            up_limit = _numeric_from(limit_row, ("up_limit",)) if limit_row is not None else float("nan")
            continuation_hit = (
                int(_is_close(close_price, up_limit))
                if close_price > 0 and math.isfinite(up_limit)
                else np.nan
            )
            t_return = (
                close_price / open_price - 1.0
                if observation_fill and close_price > 0 and open_price > 0
                else np.nan
            )
            base.update(
                {
                    "actual_buy_date": buy_date,
                    "actual_open_price": open_price,
                    "actual_t_close": close_price,
                    "market_daily_return": market_return,
                    "observation_fill": observation_fill,
                    "observation_fill_reason": fill_reason,
                    "observation_limit_accept": int(cap_accept),
                    "observation_price_vs_cap": price_vs_cap,
                    "market_buyable_diagnostic": int(market_fill),
                    "market_buyable_reason": market_reason,
                    "observation_t_return": t_return,
                    "continuation_limit_up_hit": continuation_hit,
                    "validation_status": "T_VERIFIED_FILLED" if observation_fill else "T_VERIFIED_NO_FILL",
                    "truth_source": self._execution_open_source(
                        buy_date,
                        code,
                    ),
                }
            )
            if not observation_fill:
                base["validation_status"] = "FINAL_NO_FILL"
                records.append(base)
                continue
            if not exit_date or exit_date not in dates:
                base["validation_status"] = "PENDING_T1"
                records.append(base)
                continue

            date_index = dates.index(exit_date)
            actual_exit_date, actual_exit_price, _, exit_reason = self._resolve_exit(
                code,
                date_index,
                dates,
                entry_price=open_price,
                buy_date=buy_date,
            )
            if not actual_exit_date:
                base["validation_status"] = "PENDING_EXIT_TRUTH"
                records.append(base)
                continue
            gross = self._realized_gross_return(
                code,
                buy_date,
                open_price,
                actual_exit_date,
                dates,
                exit_price=actual_exit_price,
            )
            net = gross - self.config.cost_rate if math.isfinite(gross) else np.nan
            base.update(
                {
                    "actual_exit_date": actual_exit_date,
                    "actual_exit_price": actual_exit_price,
                    "actual_gross_return": gross,
                    "actual_net_return": net,
                    "exit_reason": exit_reason,
                    "validation_status": "FINAL_VERIFIED",
                }
            )
            records.append(base)
        return pd.DataFrame(records)

    @staticmethod
    def _prediction_timing_status(
        generated_at_utc: Any,
        buy_date: str,
    ) -> tuple[str, int, str]:
        normalized_buy_date = _normal_date(buy_date)
        if not normalized_buy_date:
            return "UNKNOWN_BUY_DATE", 0, ""
        deadline = datetime.strptime(normalized_buy_date, "%Y%m%d").replace(
            hour=9,
            minute=25,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        raw_generated = str(generated_at_utc or "").strip()
        if not raw_generated:
            return "UNKNOWN_GENERATION_TIME", 0, deadline.astimezone(timezone.utc).isoformat()
        try:
            generated = datetime.fromisoformat(raw_generated.replace("Z", "+00:00"))
        except ValueError:
            return "UNKNOWN_GENERATION_TIME", 0, deadline.astimezone(timezone.utc).isoformat()
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        valid = generated <= deadline
        return (
            "PREMARKET_VALID" if valid else "RETROSPECTIVE_LATE_GENERATION",
            int(valid),
            deadline.astimezone(timezone.utc).isoformat(),
        )

    @staticmethod
    def _wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
        if total <= 0:
            return None, None
        z = 1.959963984540054
        rate = successes / total
        denominator = 1.0 + z * z / total
        centre = (rate + z * z / (2.0 * total)) / denominator
        radius = (
            z
            * math.sqrt(rate * (1.0 - rate) / total + z * z / (4.0 * total * total))
            / denominator
        )
        return _safe_metric(max(0.0, centre - radius)), _safe_metric(min(1.0, centre + radius))

    def _forward_shadow_metrics(self, ledger: pd.DataFrame) -> dict[str, Any]:
        start_signal_date = _normal_date(
            self.config.forward_shadow_start_signal_date
        )
        payload: dict[str, Any] = {
            "schema_version": "decision_forward_shadow_validation_v1",
            "start_signal_date": start_signal_date,
            "performance_scope": "premarket_frozen_trade_shadow_selected_only",
            "minimum_final_trades": 20,
            "observed_signal_dates": 0,
            "shadow_signal_dates": 0,
            "shadow_signal_rate": None,
            "shadow_entries": 0,
            "t_validated_entries": 0,
            "pending_t_entries": 0,
            "pending_t1_entries": 0,
            "finalized_entries": 0,
            "final_verified_trades": 0,
            "market_buyable_entries": 0,
            "mean_final_net_return": None,
            "median_final_net_return": None,
            "final_win_rate": None,
            "final_win_rate_95ci_low": None,
            "final_win_rate_95ci_high": None,
            "profit_factor": None,
            "worst_final_net_return": None,
            "tail_10pct_mean_return": None,
            "realized_big_loss_rate": None,
            "continuation_samples": 0,
            "continuation_hits": 0,
            "continuation_hit_rate": None,
            "matured_portfolio_dates": 0,
            "equal_slot_cumulative_return": None,
            "equal_slot_max_drawdown": None,
            "equal_slot_mean_daily_return": None,
            "longest_no_signal_streak": 0,
            "latest_signal_date": "",
            "latest_final_signal_date": "",
            "sample_sufficient": False,
            "daily_portfolio": [],
            "rows": [],
        }
        if ledger.empty or not start_signal_date:
            return payload

        frame = ledger.copy()
        frame["signal_date"] = frame.get(
            "signal_date",
            pd.Series("", index=frame.index, dtype=str),
        ).map(_normal_date)
        timing_valid = pd.to_numeric(
            frame.get(
                "prediction_timing_valid",
                pd.Series(0, index=frame.index, dtype=float),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        frame = frame[
            timing_valid
            & frame["signal_date"].ge(start_signal_date)
        ].copy()
        if frame.empty:
            return payload

        observed_dates = sorted(
            date
            for date in frame["signal_date"].dropna().astype(str).unique()
            if date
        )
        shadow_selected = pd.to_numeric(
            frame.get(
                "trade_shadow_selected",
                pd.Series(0, index=frame.index, dtype=float),
            ),
            errors="coerce",
        ).fillna(0).eq(1)
        selected = frame[shadow_selected].copy()
        shadow_dates = set(selected["signal_date"].astype(str))
        streak = 0
        longest_streak = 0
        for signal_date in observed_dates:
            if signal_date in shadow_dates:
                streak = 0
            else:
                streak += 1
                longest_streak = max(longest_streak, streak)

        payload.update(
            {
                "observed_signal_dates": int(len(observed_dates)),
                "shadow_signal_dates": int(len(shadow_dates)),
                "shadow_signal_rate": _safe_metric(
                    len(shadow_dates) / len(observed_dates)
                    if observed_dates
                    else np.nan
                ),
                "shadow_entries": int(len(selected)),
                "longest_no_signal_streak": int(longest_streak),
                "latest_signal_date": max(observed_dates) if observed_dates else "",
            }
        )
        if selected.empty:
            return payload

        statuses = selected.get(
            "validation_status",
            pd.Series("", index=selected.index, dtype=str),
        ).fillna("").astype(str)
        pending_t = statuses.eq("PENDING_T")
        pending_t1 = statuses.isin(("PENDING_T1", "PENDING_EXIT_TRUTH"))
        finalized = statuses.str.startswith("FINAL_")
        t_validated = statuses.ne("") & ~pending_t
        final = selected[statuses.eq("FINAL_VERIFIED")].copy()
        final_returns = pd.to_numeric(
            final.get(
                "actual_net_return",
                pd.Series(np.nan, index=final.index, dtype=float),
            ),
            errors="coerce",
        ).dropna()
        positive = float(final_returns[final_returns > 0].sum())
        negative = float(-final_returns[final_returns < 0].sum())
        continuation = pd.to_numeric(
            selected.loc[
                t_validated,
                "continuation_limit_up_hit",
            ]
            if "continuation_limit_up_hit" in selected.columns
            else pd.Series(dtype=float),
            errors="coerce",
        ).dropna()
        market_buyable = pd.to_numeric(
            selected.loc[
                t_validated,
                "market_buyable_diagnostic",
            ]
            if "market_buyable_diagnostic" in selected.columns
            else pd.Series(dtype=float),
            errors="coerce",
        ).dropna()
        win_low, win_high = self._wilson_interval(
            int((final_returns > 0).sum()),
            int(len(final_returns)),
        )

        daily_records: list[dict[str, Any]] = []
        for signal_date, group in selected.groupby("signal_date", sort=True):
            group_statuses = group.get(
                "validation_status",
                pd.Series("", index=group.index, dtype=str),
            ).fillna("").astype(str)
            if group_statuses.empty or not group_statuses.str.startswith("FINAL_").all():
                continue
            slot_returns = pd.to_numeric(
                group.get(
                    "actual_net_return",
                    pd.Series(np.nan, index=group.index, dtype=float),
                ),
                errors="coerce",
            ).fillna(0.0)
            daily_records.append(
                {
                    "signal_date": str(signal_date),
                    "shadow_slots": int(len(group)),
                    "verified_trades": int(group_statuses.eq("FINAL_VERIFIED").sum()),
                    "equal_slot_net_return": float(slot_returns.mean()),
                }
            )
        daily = pd.DataFrame(daily_records)
        if not daily.empty:
            daily["nav"] = (1.0 + daily["equal_slot_net_return"]).cumprod()
            peak = daily["nav"].cummax().clip(lower=1.0)
            drawdown = daily["nav"] / peak - 1.0
            cumulative_return = float(daily["nav"].iloc[-1] - 1.0)
            max_drawdown = float(drawdown.min())
        else:
            cumulative_return = float("nan")
            max_drawdown = float("nan")

        rows: list[dict[str, Any]] = []
        ordered = selected.assign(
            _trade_rank=pd.to_numeric(
                selected.get(
                    "trade_rank",
                    pd.Series(np.nan, index=selected.index, dtype=float),
                ),
                errors="coerce",
            )
        ).sort_values(
            ["signal_date", "_trade_rank", "ts_code"],
            ascending=[False, True, True],
            na_position="last",
        )
        status_labels = {
            "PENDING_T": "等待T日收盘",
            "PENDING_T1": "等待T+1",
            "PENDING_EXIT_TRUTH": "等待退出真值",
            "FINAL_VERIFIED": "已完成",
            "FINAL_NO_FILL": "最终无成交",
        }
        for _, row in ordered.iterrows():
            status = str(row.get("validation_status") or "")
            trade_rank = _finite(row.get("trade_rank"))
            rows.append(
                {
                    "signal_date": _normal_date(row.get("signal_date")),
                    "expected_buy_date": _normal_date(row.get("expected_buy_date")),
                    "expected_exit_date": _normal_date(row.get("expected_exit_date")),
                    "trade_rank": int(trade_rank) if math.isfinite(trade_rank) else None,
                    "ts_code": str(row.get("ts_code") or ""),
                    "name": str(row.get("name") or ""),
                    "industry": str(row.get("industry") or ""),
                    "stage_transition": str(row.get("stage_transition") or ""),
                    "path_label": str(row.get("path_label") or "路径数据不足"),
                    "actual_open_price": _safe_metric(row.get("actual_open_price")),
                    "actual_t_close": _safe_metric(row.get("actual_t_close")),
                    "observation_t_return": _safe_metric(row.get("observation_t_return")),
                    "continuation_limit_up_hit": _safe_metric(
                        row.get("continuation_limit_up_hit")
                    ),
                    "actual_net_return": _safe_metric(row.get("actual_net_return")),
                    "market_buyable_diagnostic": _safe_metric(
                        row.get("market_buyable_diagnostic")
                    ),
                    "validation_status": status,
                    "validation_status_label": status_labels.get(status, status or "待验证"),
                }
            )

        payload.update(
            {
                "t_validated_entries": int(t_validated.sum()),
                "pending_t_entries": int(pending_t.sum()),
                "pending_t1_entries": int(pending_t1.sum()),
                "finalized_entries": int(finalized.sum()),
                "final_verified_trades": int(len(final_returns)),
                "market_buyable_entries": int(market_buyable.eq(1).sum()),
                "mean_final_net_return": _safe_metric(final_returns.mean()),
                "median_final_net_return": _safe_metric(final_returns.median()),
                "final_win_rate": _safe_metric((final_returns > 0).mean()),
                "final_win_rate_95ci_low": win_low,
                "final_win_rate_95ci_high": win_high,
                "profit_factor": _safe_metric(
                    positive / negative if negative > 0 else np.nan
                ),
                "worst_final_net_return": _safe_metric(final_returns.min()),
                "tail_10pct_mean_return": _safe_metric(
                    final_returns.nsmallest(
                        max(1, math.ceil(len(final_returns) * 0.10))
                    ).mean()
                    if len(final_returns)
                    else np.nan
                ),
                "realized_big_loss_rate": _safe_metric(
                    final_returns.le(self.config.big_loss_threshold).mean()
                ),
                "continuation_samples": int(len(continuation)),
                "continuation_hits": int(continuation.sum()) if len(continuation) else 0,
                "continuation_hit_rate": _safe_metric(continuation.mean()),
                "matured_portfolio_dates": int(len(daily)),
                "equal_slot_cumulative_return": _safe_metric(cumulative_return),
                "equal_slot_max_drawdown": _safe_metric(max_drawdown),
                "equal_slot_mean_daily_return": _safe_metric(
                    daily["equal_slot_net_return"].mean()
                    if not daily.empty
                    else np.nan
                ),
                "latest_final_signal_date": (
                    max(daily["signal_date"]) if not daily.empty else ""
                ),
                "sample_sufficient": bool(len(final_returns) >= 20),
                "daily_portfolio": [
                    {
                        "signal_date": str(row["signal_date"]),
                        "shadow_slots": int(row["shadow_slots"]),
                        "verified_trades": int(row["verified_trades"]),
                        "equal_slot_net_return": _safe_metric(
                            row["equal_slot_net_return"]
                        ),
                        "nav": _safe_metric(row["nav"]),
                    }
                    for _, row in daily.iterrows()
                ],
                "rows": rows,
            }
        )
        return payload

    def _observation_metrics(self, ledger: pd.DataFrame) -> dict[str, Any]:
        public_start_signal_date = _normal_date(
            self.config.public_statistics_start_signal_date
        )
        if not public_start_signal_date:
            raise ValueError("public statistics signal-D cutoff is invalid")
        source_rows = int(len(ledger))
        frame = ledger.copy()
        if not frame.empty:
            frame["signal_date"] = frame["signal_date"].map(_normal_date)
            frame = frame[
                frame["signal_date"].ge(public_start_signal_date)
            ].copy()
        excluded_pre_cutover_rows = source_rows - int(len(frame))
        if frame.empty:
            return {
                "schema_version": "decision_observation_validation_v4_auction_truth",
                "status": "no_observation_predictions",
                "generated_at_utc": _utc_now(),
                "validation_start_exec_date": self.config.observation_validation_start_date,
                "public_start_signal_date": public_start_signal_date,
                "historical_ledger_retained": True,
                "excluded_pre_cutover_rows": excluded_pre_cutover_rows,
                "observation_dates": 0,
                "observation_rows": 0,
                "t_validated_rows": 0,
                "t_pending_rows": 0,
                "premarket_valid_rows": 0,
                "premarket_validated_rows": 0,
                "retrospective_truth_rows": 0,
                "unknown_timing_truth_rows": 0,
                "official_auction_truth_rows": 0,
                "minute_proxy_truth_rows": 0,
                "daily_open_proxy_truth_rows": 0,
                "final_verified_trades": 0,
                "matured_portfolio_dates": 0,
                "stage_2_to_3": {"samples": 0, "hits": 0, "hit_rate": None},
                "stage_3_to_4": {"samples": 0, "hits": 0, "hit_rate": None},
                "top1_continuation": {
                    "start_signal_date": public_start_signal_date,
                    "rank_field": "promotion_rank",
                    "rank_value": 1,
                    "samples": 0,
                    "hits": 0,
                    "hit_rate": None,
                },
                "top3_continuation": {"samples": 0, "hits": 0, "hit_rate": None},
                "all_truth_summary": {
                    "t_validated_rows": 0,
                    "fillable_rows": 0,
                    "final_verified_trades": 0,
                    "final_win_rate": None,
                    "mean_final_net_return": None,
                },
                "daily_portfolio": [],
                "path_performance": {},
                "trading_date_windows": {
                    label: {
                        "portfolio_dates": 0,
                        "filled_trades": 0,
                        "mean_net_return": None,
                        "win_rate": None,
                        "equal_slot_cumulative_return": None,
                    }
                    for label in ("20", "60", "all")
                },
                "forward_shadow": self._forward_shadow_metrics(frame),
            }
        frame["expected_buy_date"] = frame["expected_buy_date"].map(_normal_date)
        frame["observation_rank"] = pd.to_numeric(frame.get("observation_rank"), errors="coerce")
        frame["promotion_rank"] = pd.to_numeric(
            frame.get(
                "promotion_rank",
                pd.Series(np.nan, index=frame.index, dtype=float),
            ),
            errors="coerce",
        )
        timing_valid = pd.to_numeric(
            frame.get("prediction_timing_valid"), errors="coerce"
        ).fillna(0).eq(1)
        eligible_frame = frame[timing_valid].copy()
        all_t_validated = frame[frame["validation_status"].ne("PENDING_T")].copy()
        t_validated = eligible_frame[
            eligible_frame["validation_status"].ne("PENDING_T")
        ].copy()
        retrospective_truth = all_t_validated[
            all_t_validated["prediction_timing_status"].eq(
                "RETROSPECTIVE_LATE_GENERATION"
            )
        ]
        unknown_timing_truth = all_t_validated[
            all_t_validated["prediction_timing_status"].astype(str).str.startswith(
                "UNKNOWN_"
            )
        ]
        market_returns = pd.to_numeric(t_validated.get("market_daily_return"), errors="coerce").dropna()
        fill_flags = pd.to_numeric(t_validated.get("observation_fill"), errors="coerce").dropna()
        limit_accept_flags = pd.to_numeric(
            t_validated.get("observation_limit_accept"), errors="coerce"
        ).dropna()
        price_vs_cap = pd.to_numeric(
            t_validated.get("observation_price_vs_cap"), errors="coerce"
        ).dropna()
        t_fill_returns = pd.to_numeric(
            t_validated.loc[
                pd.to_numeric(t_validated.get("observation_fill"), errors="coerce").eq(1),
                "observation_t_return",
            ],
            errors="coerce",
        ).dropna()
        continuation = pd.to_numeric(
            t_validated.get("continuation_limit_up_hit"), errors="coerce"
        ).dropna()
        final = eligible_frame[
            eligible_frame["validation_status"].eq("FINAL_VERIFIED")
        ].copy()
        final_returns = pd.to_numeric(final.get("actual_net_return"), errors="coerce").dropna()
        all_final = frame[frame["validation_status"].eq("FINAL_VERIFIED")].copy()
        all_final_returns = pd.to_numeric(
            all_final.get("actual_net_return"), errors="coerce"
        ).dropna()
        positive = float(final_returns[final_returns > 0].sum())
        negative = float(-final_returns[final_returns < 0].sum())

        daily_records: list[dict[str, Any]] = []
        for exec_date, group in eligible_frame.groupby("expected_buy_date", sort=True):
            statuses = set(group["validation_status"].astype(str))
            if not statuses or any(not status.startswith("FINAL_") for status in statuses):
                continue
            slot_returns = pd.to_numeric(group.get("actual_net_return"), errors="coerce").fillna(0.0)
            daily_records.append(
                {
                    "exec_date": exec_date,
                    "observation_slots": int(len(group)),
                    "filled_slots": int(pd.to_numeric(group.get("observation_fill"), errors="coerce").fillna(0).sum()),
                    "equal_slot_net_return": float(slot_returns.sum() / max(len(group), 1)),
                }
            )
        daily = pd.DataFrame(daily_records)
        if not daily.empty:
            daily["nav"] = (1.0 + daily["equal_slot_net_return"]).cumprod()
            peak = daily["nav"].cummax().clip(lower=1.0)
            drawdown = daily["nav"] / peak - 1.0
            cumulative_return = float(daily["nav"].iloc[-1] - 1.0)
            max_drawdown = float(drawdown.min())
        else:
            cumulative_return = float("nan")
            max_drawdown = float("nan")

        win_low, win_high = self._wilson_interval(
            int((final_returns > 0).sum()),
            int(len(final_returns)),
        )
        continuation_low, continuation_high = self._wilson_interval(
            int(continuation.sum()),
            int(len(continuation)),
        )
        payload: dict[str, Any] = {
            "schema_version": "decision_observation_validation_v4_auction_truth",
            "status": "ok",
            "generated_at_utc": _utc_now(),
            "validation_start_exec_date": self.config.observation_validation_start_date,
            "public_start_signal_date": public_start_signal_date,
            "historical_ledger_retained": True,
            "excluded_pre_cutover_rows": excluded_pre_cutover_rows,
            "validation_mode": "market_at_open_proxy",
            "market_open_fill_assumption": True,
            "displayed_limit_affects_fill": False,
            "performance_scope": "premarket_valid_predictions_only",
            "prediction_deadline": "T 09:25 Asia/Shanghai",
            "top_n": int(self.config.max_observation_candidates),
            "observation_dates": int(frame["expected_buy_date"].nunique()),
            "observation_rows": int(len(frame)),
            "t_validated_rows": int(len(all_t_validated)),
            "t_pending_rows": int(len(frame) - len(all_t_validated)),
            "premarket_valid_rows": int(timing_valid.sum()),
            "premarket_validated_rows": int(len(t_validated)),
            "retrospective_truth_rows": int(len(retrospective_truth)),
            "unknown_timing_truth_rows": int(len(unknown_timing_truth)),
            "official_auction_truth_rows": int(
                all_t_validated.get(
                    "truth_source",
                    pd.Series(dtype=str),
                ).eq("tushare_stk_auction_o").sum()
            ),
            "minute_proxy_truth_rows": int(
                all_t_validated.get(
                    "truth_source",
                    pd.Series(dtype=str),
                ).eq("tushare_minute_0930_proxy").sum()
            ),
            "daily_open_proxy_truth_rows": int(
                all_t_validated.get(
                    "truth_source",
                    pd.Series(dtype=str),
                ).eq("official_daily_open_proxy").sum()
            ),
            "market_positive_rate": _safe_metric((market_returns > 0).mean()),
            "mean_market_daily_return": _safe_metric(market_returns.mean()),
            "median_market_daily_return": _safe_metric(market_returns.median()),
            "fillable_rows": int(fill_flags.sum()) if len(fill_flags) else 0,
            "market_filled_rows": int(fill_flags.sum()) if len(fill_flags) else 0,
            "observation_fill_rate": _safe_metric(fill_flags.mean()),
            "display_limit_met_rows": (
                int(limit_accept_flags.sum()) if len(limit_accept_flags) else 0
            ),
            "above_display_limit_rows": (
                int(limit_accept_flags.eq(0).sum()) if len(limit_accept_flags) else 0
            ),
            "display_limit_met_rate": _safe_metric(limit_accept_flags.mean()),
            "mean_open_vs_display_limit": _safe_metric(price_vs_cap.mean()),
            "mean_t_observation_return": _safe_metric(t_fill_returns.mean()),
            "median_t_observation_return": _safe_metric(t_fill_returns.median()),
            "continuation_hits": int(continuation.sum()) if len(continuation) else 0,
            "continuation_samples": int(len(continuation)),
            "continuation_hit_rate": _safe_metric(continuation.mean()),
            "continuation_hit_rate_95ci_low": continuation_low,
            "continuation_hit_rate_95ci_high": continuation_high,
            "final_verified_trades": int(len(final_returns)),
            "final_win_rate": _safe_metric((final_returns > 0).mean()),
            "final_win_rate_95ci_low": win_low,
            "final_win_rate_95ci_high": win_high,
            "mean_final_net_return": _safe_metric(final_returns.mean()),
            "median_final_net_return": _safe_metric(final_returns.median()),
            "worst_final_net_return": _safe_metric(final_returns.min()),
            "tail_10pct_mean_return": _safe_metric(
                final_returns.nsmallest(max(1, math.ceil(len(final_returns) * 0.10))).mean()
                if len(final_returns)
                else np.nan
            ),
            "profit_factor": _safe_metric(positive / negative if negative > 0 else np.nan),
            "matured_portfolio_dates": int(len(daily)),
            "equal_slot_cumulative_return": _safe_metric(cumulative_return),
            "equal_slot_max_drawdown": _safe_metric(max_drawdown),
            "equal_slot_mean_daily_return": _safe_metric(
                daily["equal_slot_net_return"].mean() if not daily.empty else np.nan
            ),
            "latest_t_validated_exec_date": (
                max(t_validated["expected_buy_date"]) if not t_validated.empty else ""
            ),
            "latest_all_truth_exec_date": (
                max(all_t_validated["expected_buy_date"])
                if not all_t_validated.empty
                else ""
            ),
            "latest_final_exec_date": (
                max(daily["exec_date"]) if not daily.empty else ""
            ),
            "daily_portfolio": [
                {
                    "exec_date": str(row["exec_date"]),
                    "observation_slots": int(row["observation_slots"]),
                    "filled_slots": int(row["filled_slots"]),
                    "equal_slot_net_return": _safe_metric(row["equal_slot_net_return"]),
                    "nav": _safe_metric(row["nav"]),
                }
                for _, row in daily.iterrows()
            ],
            "all_truth_summary": {
                "t_validated_rows": int(len(all_t_validated)),
                "fillable_rows": int(
                    pd.to_numeric(
                        all_t_validated.get("observation_fill"),
                        errors="coerce",
                    )
                    .fillna(0)
                    .sum()
                ),
                "final_verified_trades": int(len(all_final_returns)),
                "final_win_rate": _safe_metric(
                    (all_final_returns > 0).mean()
                ),
                "mean_final_net_return": _safe_metric(all_final_returns.mean()),
            },
        }

        for transition, key in (("2→3", "stage_2_to_3"), ("3→4", "stage_3_to_4")):
            sample = t_validated[t_validated["stage_transition"].eq(transition)]
            hits = pd.to_numeric(sample.get("continuation_limit_up_hit"), errors="coerce").dropna()
            payload[key] = {
                "samples": int(len(hits)),
                "hits": int(hits.sum()) if len(hits) else 0,
                "hit_rate": _safe_metric(hits.mean()),
            }
        for cutoff in (1, 3, 10):
            sample = t_validated[t_validated["observation_rank"].le(cutoff)]
            hits = pd.to_numeric(sample.get("continuation_limit_up_hit"), errors="coerce").dropna()
            payload[f"top{cutoff}_continuation"] = {
                "samples": int(len(hits)),
                "hits": int(hits.sum()) if len(hits) else 0,
                "hit_rate": _safe_metric(hits.mean()),
            }
        top1_start_signal_date = _normal_date(
            self.config.top1_promotion_start_signal_date
        )
        top1_sample = t_validated[
            t_validated["signal_date"].ge(top1_start_signal_date)
            & t_validated["promotion_rank"].eq(1)
        ].copy()
        top1_sample = top1_sample.sort_values(
            ["signal_date", "ts_code"],
            kind="stable",
        ).drop_duplicates("signal_date", keep="first")
        top1_hits = pd.to_numeric(
            top1_sample.get("continuation_limit_up_hit"),
            errors="coerce",
        ).dropna()
        payload["top1_continuation"] = {
            "start_signal_date": top1_start_signal_date,
            "rank_field": "promotion_rank",
            "rank_value": 1,
            "samples": int(len(top1_hits)),
            "hits": int(top1_hits.sum()) if len(top1_hits) else 0,
            "hit_rate": _safe_metric(top1_hits.mean()),
        }
        path_performance: dict[str, Any] = {}
        if "path_label_code" in t_validated.columns:
            for label_code, group in t_validated.groupby("path_label_code", dropna=False):
                code = (
                    str(label_code)
                    if not pd.isna(label_code) and str(label_code).strip()
                    else "INSUFFICIENT"
                )
                hits = pd.to_numeric(
                    group.get("continuation_limit_up_hit"),
                    errors="coerce",
                ).dropna()
                matured = group[group["validation_status"].eq("FINAL_VERIFIED")]
                returns = pd.to_numeric(
                    matured.get("actual_net_return"),
                    errors="coerce",
                ).dropna()
                path_performance[code] = {
                    "label": PATH_LABELS.get(code, code),
                    "t_validated_rows": int(len(group)),
                    "continuation_hit_rate": _safe_metric(hits.mean()),
                    "final_verified_trades": int(len(returns)),
                    "mean_final_net_return": _safe_metric(returns.mean()),
                    "win_rate": _safe_metric((returns > 0).mean()),
                }
        payload["path_performance"] = path_performance

        rolling: dict[str, Any] = {}
        for label, size in (("20", 20), ("60", 60), ("all", 0)):
            sample_daily = daily.tail(size) if size else daily
            dates_in_window = set(sample_daily["exec_date"].astype(str)) if not sample_daily.empty else set()
            sample_trades = final[final["expected_buy_date"].isin(dates_in_window)]
            sample_returns = pd.to_numeric(sample_trades.get("actual_net_return"), errors="coerce").dropna()
            rolling[label] = {
                "portfolio_dates": int(len(sample_daily)),
                "filled_trades": int(len(sample_returns)),
                "mean_net_return": _safe_metric(sample_returns.mean()),
                "win_rate": _safe_metric((sample_returns > 0).mean()),
                "equal_slot_cumulative_return": _safe_metric(
                    (1.0 + sample_daily["equal_slot_net_return"]).prod() - 1.0
                    if not sample_daily.empty
                    else np.nan
                ),
            }
        payload["trading_date_windows"] = rolling
        payload["forward_shadow"] = self._forward_shadow_metrics(frame)
        return payload

    def settle_observations(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        dates = self.market_dates()
        parts: list[pd.DataFrame] = []
        for path in sorted(self.config.prediction_root.glob("pred_20*.csv")):
            if not re.fullmatch(r"pred_20\d{6}\.csv", path.name):
                continue
            verified = self._verify_observation_prediction_file(path, dates)
            if not verified.empty:
                parts.append(verified)
        ledger = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if not ledger.empty:
            ledger = ledger.sort_values(
                ["expected_buy_date", "observation_rank", "ts_code"],
                kind="stable",
            ).reset_index(drop=True)
            for exec_date, group in ledger.groupby("expected_buy_date", sort=True):
                _write_csv(
                    group.reset_index(drop=True),
                    self.config.verification_root / f"observation_{exec_date}.csv",
                )
        _write_csv(
            ledger,
            self.config.verification_root / "observation_latest.csv",
        )
        metrics = self._observation_metrics(ledger)
        _write_json(
            metrics,
            self.config.metrics_root / "observation_cumulative_latest.json",
        )
        return ledger, metrics

    def settle_predictions(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        dates = self.market_dates()
        feedback = self._manual_feedback()
        parts: list[pd.DataFrame] = []
        for path in sorted(self.config.prediction_root.glob("pred_20*.csv")):
            if not re.fullmatch(r"pred_20\d{6}\.csv", path.name):
                continue
            verified = self._verify_prediction_file(path, dates, feedback)
            if verified.empty:
                continue
            signal_date = _normal_date(verified["signal_date"].iloc[0])
            _write_csv(verified, self.config.verification_root / f"verify_{signal_date}.csv")
            parts.append(verified)
        ledger = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        _write_csv(ledger, self.config.verification_root / "verify_latest.csv")
        metrics = self._verification_metrics(ledger)
        _write_json(metrics, self.config.metrics_root / "cumulative_latest.json")
        self.settle_manual_actuals()
        self.settle_observations()
        return ledger, metrics

    def settle_manual_actuals(
        self,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        feedback = self._manual_feedback()
        records: list[dict[str, Any]] = []
        for _, row in feedback.iterrows():
            buy_price = _numeric_from(
                row,
                ("buy_price", "actual_buy_price"),
            )
            sell_price = _numeric_from(
                row,
                ("sell_price", "actual_exit_price"),
            )
            quantity = _numeric_from(row, ("quantity", "qty"), 0.0)
            fees = _numeric_from(row, ("fees", "total_fees"), 0.0)
            if buy_price <= 0 or sell_price <= 0:
                continue
            gross_return = sell_price / buy_price - 1.0
            fee_rate = (
                fees / (buy_price * quantity)
                if quantity > 0 and fees >= 0
                else self.config.cost_rate
            )
            net_return = gross_return - fee_rate
            records.append(
                {
                    **row.to_dict(),
                    "signal_date": _normal_date(row.get("signal_date")),
                    "ts_code": _normal_code(row.get("ts_code")),
                    "actual_buy_date": _normal_date(
                        row.get("buy_date")
                        or row.get("actual_buy_date")
                    ),
                    "actual_buy_price": buy_price,
                    "actual_exit_date": _normal_date(
                        row.get("sell_date")
                        or row.get("actual_exit_date")
                    ),
                    "actual_exit_price": sell_price,
                    "actual_gross_return": gross_return,
                    "actual_net_return": net_return,
                    "actual_fee_rate": fee_rate,
                    "truth_source": "manual_actual",
                    "verification_status": "VERIFIED",
                }
            )
        ledger = pd.DataFrame(records)
        if not ledger.empty:
            ledger = ledger.sort_values(
                ["signal_date", "ts_code"],
                kind="stable",
            ).reset_index(drop=True)
        _write_csv(
            ledger,
            self.config.verification_root / "manual_actual_latest.csv",
        )
        returns = pd.to_numeric(
            ledger.get("actual_net_return", pd.Series(dtype=float)),
            errors="coerce",
        ).dropna()
        positive = returns[returns > 0].sum()
        negative = -returns[returns < 0].sum()
        metrics = {
            "schema_version": "decision_manual_actual_v1",
            "generated_at_utc": _utc_now(),
            "truth_source": "manual_actual",
            "trades": int(len(returns)),
            "win_rate": _safe_metric((returns > 0).mean()),
            "mean_net_return": _safe_metric(returns.mean()),
            "cumulative_return": _safe_metric(
                (1.0 + returns).prod() - 1.0
                if len(returns)
                else np.nan
            ),
            "profit_factor": _safe_metric(
                positive / negative if negative > 0 else np.nan
            ),
        }
        _write_json(
            metrics,
            self.config.metrics_root
            / "manual_actual_cumulative_latest.json",
        )
        return ledger, metrics

    def _verification_metrics(self, ledger: pd.DataFrame) -> dict[str, Any]:
        if ledger.empty:
            return {"status": "no_frozen_predictions", "generated_at_utc": _utc_now(), "verified_trades": 0}
        selected = ledger[pd.to_numeric(ledger["selected"], errors="coerce").fillna(0).eq(1)]
        verified = selected[selected["verification_status"].eq("VERIFIED")].copy()
        returns = pd.to_numeric(verified["actual_net_return"], errors="coerce").dropna()
        positive = returns[returns > 0].sum()
        negative = -returns[returns < 0].sum()
        payload: dict[str, Any] = {
            "status": "ok",
            "generated_at_utc": _utc_now(),
            "frozen_predictions": int(len(ledger)),
            "selected_predictions": int(len(selected)),
            "verified_trades": int(len(verified)),
            "pending_or_no_fill": int(len(selected) - len(verified)),
            "official_auction_truth_trades": int(
                (
                    verified.get(
                        "truth_source",
                        pd.Series(dtype=str),
                    )
                    == "tushare_stk_auction_o"
                ).sum()
            ),
            "minute_proxy_trades": int(
                (
                    verified.get(
                        "truth_source",
                        pd.Series(dtype=str),
                    )
                    == "tushare_minute_0930_proxy"
                ).sum()
            ),
            "daily_open_proxy_trades": int(
                (
                    verified.get(
                        "truth_source",
                        pd.Series(dtype=str),
                    )
                    == "official_daily_open_proxy"
                ).sum()
            ),
            "fill_rate": _safe_metric(pd.to_numeric(selected.get("actual_fill"), errors="coerce").mean()),
            "price_guidance_accuracy": _safe_metric(pd.to_numeric(selected.get("price_guidance_success"), errors="coerce").mean()),
            "win_rate": _safe_metric((returns > 0).mean()),
            "realized_big_loss_rate": _safe_metric((returns <= self.config.big_loss_threshold).mean()),
            "big_loss_avoidance_rate": _safe_metric((returns > self.config.big_loss_threshold).mean()),
            "mean_actual_net_return": _safe_metric(returns.mean()),
            "cumulative_trade_return": _safe_metric((1.0 + returns).prod() - 1.0 if len(returns) else np.nan),
            "profit_factor": _safe_metric(positive / negative if negative > 0 else np.nan),
            "forecast_mae": _safe_metric(pd.to_numeric(verified.get("forecast_error"), errors="coerce").abs().mean()),
            "direction_accuracy": _safe_metric(pd.to_numeric(verified.get("direction_success"), errors="coerce").mean()),
            "risk_prediction_accuracy": _safe_metric(pd.to_numeric(verified.get("risk_prediction_success"), errors="coerce").mean()),
            "correct_rejection_rate": _safe_metric(pd.to_numeric(ledger.get("correct_rejection"), errors="coerce").mean()),
            "missed_opportunity_rate": _safe_metric(pd.to_numeric(ledger.get("missed_opportunity"), errors="coerce").mean()),
        }
        windows: dict[str, Any] = {}
        if "signal_date" in verified.columns:
            verified = verified.sort_values("signal_date")
            for size in (20, 60, 120):
                sample = pd.to_numeric(verified.tail(size)["actual_net_return"], errors="coerce").dropna()
                windows[str(size)] = {
                    "trades": int(len(sample)),
                    "mean_net_return": _safe_metric(sample.mean()),
                    "win_rate": _safe_metric((sample > 0).mean()),
                }
        payload["rolling_trade_windows"] = windows
        return payload

    # ------------------------------------------------------------------
    # Top-level run
    # ------------------------------------------------------------------
    def run(self, signal_date: str = "", *, force_prediction: bool = False) -> RunResult:
        from .reporting import write_reports

        snapshots = self.candidate_snapshots()
        signal_date = _normal_date(signal_date) or (sorted(snapshots)[-1] if snapshots else "")
        if not signal_date or signal_date not in snapshots:
            raise RuntimeError("cannot resolve a dated pred_source candidate snapshot")
        candidates = self.load_candidates(signal_date, snapshots[signal_date])
        current_eligibility_audit = dict(self._eligibility_audit)
        if candidates.empty:
            raise RuntimeError(f"candidate snapshot has no <=10% price-limit stocks for {signal_date}")

        history = self.build_history()
        oos, backtest_metrics = self.run_backtest(history)
        bundle = self.fit_models(history)
        if bundle is not None:
            production_fingerprint_frame = attach_promotion_source_features(
                history,
                self.config.root,
            ).dropna(
                subset=["net_return", "proposed_gap", "market_fill"]
            ).copy()
            _finalize_model_bundle_v2(
                bundle,
                production_fingerprint_frame,
                self.config,
            )
        model_quality_failures: list[str] = []
        model_quality_warnings: list[str] = []
        if bundle is None:
            model_quality_failures.append("model_bundle_not_ready")
        elif bundle.probability_quality_gate.get("passed") is not True:
            model_quality_warnings.append(
                "first_layer_probability_models_did_not_pass_brier_skill_gate"
            )
        if model_quality_failures:
            existing_failures = list(
                backtest_metrics.get("promotion_failures", []) or []
            )
            backtest_metrics["promotion_failures"] = list(
                dict.fromkeys(existing_failures + model_quality_failures)
            )
            checks = dict(backtest_metrics.get("promotion_checks") or {})
            checks["first_layer_model_ready"] = False
            backtest_metrics["promotion_checks"] = checks
            backtest_metrics["promoted"] = False
            trade_metrics = dict(
                backtest_metrics.get("trade_selector") or {}
            )
            trade_checks = dict(
                trade_metrics.get("promotion_checks") or {}
            )
            trade_checks["first_layer_model_ready"] = False
            trade_metrics["promotion_checks"] = trade_checks
            trade_failures = list(
                trade_metrics.get("promotion_failures") or []
            )
            trade_metrics["promotion_failures"] = list(
                dict.fromkeys(
                    trade_failures + ["first_layer_model_ready"]
                )
            )
            trade_metrics["promoted"] = False
            backtest_metrics["trade_selector"] = trade_metrics
        backtest_metrics["model_quality_warnings"] = model_quality_warnings
        backtest_metrics["model_artifact_sha256"] = (
            bundle.model_artifact_sha256
            if bundle is not None
            else ""
        )
        backtest_metrics["model_canonical_v2_version"] = MODEL_CANONICAL_V2
        backtest_metrics["model_artifact_v2_sha256"] = (
            bundle.model_artifact_v2_sha256
            if bundle is not None
            else ""
        )
        backtest_metrics["model_fingerprint_v2"] = (
            bundle.model_fingerprint_v2
            if bundle is not None
            else {}
        )
        backtest_metrics["model_canonical_contract"] = (
            bundle.runtime_canonical_contract
            if bundle is not None
            else _runtime_canonical_contract("model")
        )
        # Publish the untouched raw execution policy on both same-run JSON
        # surfaces.  Runtime validation compares these bytes/type-sensitively,
        # then verifies that each maps to the stable canonical q8 envelope.
        backtest_metrics["selection_policy"] = (
            bundle.selection_policy if bundle is not None else {}
        )
        _write_json(
            backtest_metrics,
            self.config.metrics_root / "backtest_latest.json",
        )
        prediction = self.build_prediction(
            signal_date,
            candidates,
            bundle,
            backtest_metrics,
            force=force_prediction,
        )
        ledger, cumulative_metrics = self.settle_predictions()
        report_paths = write_reports(
            self.config,
            prediction=prediction,
            ledger=ledger,
            backtest_trades=oos,
            backtest_metrics=backtest_metrics,
            cumulative_metrics=cumulative_metrics,
        )
        current_sentiment: dict[str, Any] = {}
        if not prediction.empty:
            sentiment_row = prediction.iloc[0]
            for name in MARKET_SENTIMENT_OUTPUT_FIELDS:
                value = sentiment_row.get(name)
                if name in {
                    "market_sentiment_regime_code",
                    "market_sentiment_regime_label",
                }:
                    current_sentiment[name] = str(value or "")
                else:
                    current_sentiment[name] = _safe_metric(value)
        sentiment_raw = self._market_sentiment_raw(signal_date)
        industry_top10 = list(
            sentiment_raw.get("_limit_up_industry_top10")
            or sentiment_raw.get("_limit_up_industry_top5")
            or []
        )
        current_sentiment["market_limit_up_industry_top10"] = industry_top10
        current_sentiment["market_limit_up_industry_top5"] = industry_top10[:5]
        history_dates = (
            sorted(history["signal_date"].astype(str).unique())
            if not history.empty
            else []
        )
        auction_sources = (
            history.get(
                "auction_truth_source",
                pd.Series(dtype=str),
            )
            .fillna("missing")
            .astype(str)
            .value_counts()
            .to_dict()
        )
        official_auction_rows = int(
            auction_sources.get("tushare_stk_auction_o", 0)
        )
        history_sources = (
            history.get(
                "history_source",
                pd.Series(dtype=str),
            )
            .fillna("unknown")
            .astype(str)
            .value_counts()
            .to_dict()
        )
        backfill_manifest_path = (
            self.config.historical_training_root
            / "manifest_latest.json"
        )
        try:
            backfill_manifest = json.loads(
                backfill_manifest_path.read_text(encoding="utf-8")
            )
        except Exception:
            backfill_manifest = {}
        manual_metrics_path = (
            self.config.metrics_root
            / "manual_actual_cumulative_latest.json"
        )
        observation_metrics_path = (
            self.config.metrics_root
            / "observation_cumulative_latest.json"
        )
        try:
            manual_metrics = json.loads(
                manual_metrics_path.read_text(encoding="utf-8")
            )
        except Exception:
            manual_metrics = {}
        try:
            observation_metrics = json.loads(
                observation_metrics_path.read_text(encoding="utf-8")
            )
        except Exception:
            observation_metrics = {}
        independent_equity = backtest_metrics.get("daily_equity") or []
        independent_dates = [
            str(item.get("signal_date") or "")
            for item in independent_equity
            if str(item.get("signal_date") or "")
        ]
        model_meta = {
            "generated_at_utc": _utc_now(),
            "model_version": self.config.model_version,
            "model_artifact_sha256": (
                bundle.model_artifact_sha256
                if bundle is not None
                else ""
            ),
            "model_canonical_v2_version": MODEL_CANONICAL_V2,
            "model_artifact_v2_sha256": (
                bundle.model_artifact_v2_sha256
                if bundle is not None
                else ""
            ),
            "model_fingerprint_v2": (
                bundle.model_fingerprint_v2
                if bundle is not None
                else {}
            ),
            "model_canonical_contract": (
                bundle.runtime_canonical_contract
                if bundle is not None
                else _runtime_canonical_contract("model")
            ),
            "ready": bundle is not None,
            "promoted": backtest_metrics.get("promoted") is True,
            "first_layer_promoted": (
                backtest_metrics.get("first_layer_promotion") or {}
            ).get("promoted")
            is True,
            "trade_selector": (
                backtest_metrics.get("trade_selector") or {}
            ),
            "training_rows": bundle.train_rows if bundle else 0,
            "training_dates": bundle.train_dates if bundle else 0,
            "calibration_rows": bundle.calibration_rows if bundle else 0,
            "calibration_dates": bundle.calibration_dates if bundle else 0,
            "calibration_bias": _safe_metric(bundle.calibration_bias if bundle else np.nan),
            "expected_return_margin": _safe_metric(
                bundle.expected_return_margin if bundle else np.nan
            ),
            "return_selection": bundle.return_selection if bundle else {},
            "classifier_selection": bundle.classifier_selection if bundle else {},
            "probability_quality_gate": (
                bundle.probability_quality_gate if bundle else {}
            ),
            "selection_policy": (
                bundle.selection_policy if bundle else {}
            ),
            "conformal_residual_quantiles": (
                bundle.conformal_residual_quantiles if bundle else {}
            ),
            "data_coverage": {
                "target_independent_dates": TARGET_INDEPENDENT_OOS_DATES,
                "target_history_dates": TARGET_HISTORY_DATES,
                "independent_oos_dates": int(
                    backtest_metrics.get("oos_dates") or 0
                ),
                "independent_oos_start": (
                    independent_dates[0] if independent_dates else ""
                ),
                "independent_oos_end": (
                    independent_dates[-1] if independent_dates else ""
                ),
                "history_rows": int(len(history)),
                "history_dates": len(history_dates),
                "history_start": history_dates[0] if history_dates else "",
                "history_end": history_dates[-1] if history_dates else "",
                "calendar": "SSE_strict_exchange_calendar",
                "history_sources": {
                    str(key): int(value)
                    for key, value in history_sources.items()
                },
                "auction_truth_sources": {
                    str(key): int(value)
                    for key, value in auction_sources.items()
                },
                "official_auction_truth_rows": official_auction_rows,
                "official_auction_truth_coverage": _safe_metric(
                    official_auction_rows / len(history)
                    if len(history)
                    else np.nan
                ),
                "backfill_manifest": backfill_manifest,
            },
            "truth_ledgers": {
                "formal_limit_proxy": {
                    "path": "outputs/auction_v3/verification/verify_latest.csv",
                    "metrics": cumulative_metrics,
                },
                "market_open_observation": {
                    "path": "outputs/auction_v3/verification/observation_latest.csv",
                    "metrics": observation_metrics,
                },
                "manual_actual": {
                    "path": "outputs/auction_v3/verification/manual_actual_latest.csv",
                    "metrics": manual_metrics,
                },
            },
            "execution_capabilities": {
                "preopen_auction_microstructure_gate": dict(
                    PREOPEN_AUCTION_GATE_AUDIT
                ),
            },
            "current_market_sentiment": current_sentiment,
            "stage_recent_promotion_rate": (
                {
                    str(stage): _safe_metric(rate)
                    for stage, rate in bundle.stage_recent_rates.items()
                }
                if bundle
                else {}
            ),
            "stage_recent_promotion_samples": (
                {
                    str(stage): int(samples)
                    for stage, samples in bundle.stage_recent_samples.items()
                }
                if bundle
                else {}
            ),
            "continuation_stage_logit_adjustments": (
                {
                    str(stage): _safe_metric(offset)
                    for stage, offset in bundle.continuation_stage_logit_adjustments.items()
                }
                if bundle
                else {}
            ),
            "residual_q10": _safe_metric(bundle.residual_q10 if bundle else np.nan),
            "residual_q90": _safe_metric(bundle.residual_q90 if bundle else np.nan),
            "exit_on_time_base_rate": _safe_metric(bundle.exit_constant if bundle else np.nan),
            "continuation_limit_up_base_rate": _safe_metric(
                bundle.continuation_constant if bundle else np.nan
            ),
            "gap_support": {
                "min": _safe_metric(bundle.gap_min if bundle else np.nan),
                "max": _safe_metric(bundle.gap_max if bundle else np.nan),
            },
            "backtest_gate": backtest_metrics,
            "universe_eligibility": current_eligibility_audit,
            "contract": {
                "signal": "D close",
                "candidate_pool": "D-day confirmed limit-up candidates only",
                "stage_focus": "risk-first 2-to-3 and 3-to-4 ranking with point-in-time streak-path, cohort, and market-sentiment features",
                "streak_path": "quantified weak-to-strong, strong-to-weak, acceleration-consensus, divergence-reseal, and stable-strong paths",
                "market_sentiment": "D-close-only eligible-main-board breadth, limit-up ecology and industry Top10, failed-board/reseal quality, prior-limit-up profit effect, realized 2-to-3/3-to-4 promotion, crowding, and liquidity; enabled only after held-out Brier ablation",
                "observation_ranking": "all 2-to-3 and 3-to-4 candidates receive forced-open counterfactual validation; rank buckets and path cohorts are reported separately",
                "trade_selector": "an independent second layer is trained only on chronologically out-of-sample daily observation Top10 rows; E_ret is conditional on market_fill=1, P_fill is modeled separately, at most two rows can pass, and zero rows is valid",
                "trade_selector_feature_contract": TRADE_SELECTOR_FEATURE_CONTRACT,
                "guidance_only": True,
                "broker_connected": False,
                "entry": "manual limit order only before T 09:25 opening-auction cutoff with a frozen maximum price; market order forbidden",
                "exit": "manual fixed T+1 09:30 opening-auction exit; one-price limit-down delays to the first tradable open",
                "exit_policy_version": self.config.exit_policy_version,
                "take_profit_pct": self.config.take_profit_pct,
                "stop_loss_pct": self.config.stop_loss_pct,
                "latest_exit_time": self.config.latest_exit_time,
                "cost_rate": self.config.cost_rate,
                "maximum_price_limit_mechanism_pct": self.config.max_mechanism_limit_pct,
                "maximum_big_loss_probability": (
                    (bundle.selection_policy.get("thresholds") or {}).get(
                        "max_big_loss_probability",
                        self.config.max_big_loss_probability,
                    )
                    if bundle
                    else self.config.max_big_loss_probability
                ),
                "big_loss_threshold": self.config.big_loss_threshold,
                "minimum_return_lcb": (
                    (bundle.selection_policy.get("thresholds") or {}).get(
                        "min_mean_return_lcb",
                        self.config.min_return_lcb,
                    )
                    if bundle
                    else self.config.min_return_lcb
                ),
                "auction_truth": "Tushare stk_auction_o 9:30 opening-auction close/volume/amount/VWAP is authoritative when available; 09:30 minute and official daily open are labeled fallback proxies",
                "probability_calibration": "chronological calibration with embargo; big-loss and fill probabilities must beat a date-balanced constant baseline, while profit, continuation and near-degenerate exit labels may use an audited constant fallback",
                "return_uncertainty": "outcome conformal q10/q90 remains a tail-risk diagnostic; formal authorization uses a separately tuned mean-return lower-confidence floor and conservative expected utility",
                "selection_policy": "models are fit first, probabilities are calibrated on a later embargoed window, and thresholds/position count are selected on a still later policy holdout; no feasible policy means no formal trade",
                "profit_probability": "diagnostic only because an uninformative constant classifier cannot veto the full universe",
                "truth_ledgers": "formal limit-order proxy, Top1/Top2 market-at-open shadow, diagnostic-limit shadow, and manual actual fills are stored and accumulated separately",
                "future_features_forbidden": True,
            },
        }
        _write_json(model_meta, self.config.model_root / "model_meta_latest.json")
        warnings: list[str] = []
        if bundle is None:
            warnings.append("模型独立交易日或样本不足，仅生成审计结果，不给出正式买入指令")
        if backtest_metrics.get("promoted") is not True:
            warnings.append("第二层交易排序未达到严格样本外晋级门槛，当前仅显示观察与影子交易排名")
        warnings.extend(model_quality_warnings)
        return RunResult(
            signal_date=signal_date,
            prediction_path=str(self.config.prediction_root / f"pred_{signal_date}.csv"),
            backtest_path=str(self.config.metrics_root / "backtest_latest.json"),
            verification_path=str(self.config.verification_root / "verify_latest.csv"),
            current_report_path=report_paths["current"],
            verification_report_path=report_paths["verification"],
            dashboard_path=report_paths["dashboard"],
            model_ready=bundle is not None,
            promoted=backtest_metrics.get("promoted") is True,
            selected_count=int(
                pd.to_numeric(
                    prediction.get("trade_selected"),
                    errors="coerce",
                ).fillna(0).sum()
            ),
            warnings=warnings,
        )
