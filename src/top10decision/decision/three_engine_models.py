from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from top10decision.probability_calibration import (
    MONOTONICITY_SCHEMA_VERSION,
    calibrator_monotonicity_evidence,
    monotonicity_evidence_is_valid,
)

from .d_close_features import (
    D_CLOSE_FEATURE_COLUMNS,
    D_CLOSE_FEATURE_CONTRACT_VERSION,
)

THREE_ENGINE_SCHEMA_VERSION = "decision_three_engine_models_v2"
THREE_ENGINE_VALIDATION_SCHEMA = "decision_three_engine_validation_v2"
THREE_ENGINE_CONTRACT_VERSION = "decision_three_rank_v1"
THREE_ENGINE_FEATURE_CONTRACT = (
    "D_CLOSE_RUNTIME_ALIGNED_NO_CROSS_HEAD_OUTPUTS_V2"
)
RUNTIME_FEATURE_CONTRACT_VERSION = D_CLOSE_FEATURE_CONTRACT_VERSION
THREE_ENGINE_TOP_N = 10
CORE_HEADS = ("promotion", "big_loss", "profit")
THREE_ENGINE_VALIDATION_GATE_NAMES = (
    "nonconstant_production_model",
    "nonconstant_oof_rank_scores",
    "pre_holdout_selection_candidate_eligible",
    "pre_holdout_selection_ranking_positive",
    "history_dates",
    "history_rows",
    "oos_dates",
    "oos_rows",
    "final_holdout_calendar_dates",
    "ranking_baseline_available",
    "brier_improvement_positive",
    "brier_bootstrap_lower_positive",
    "ece_at_most_8pct",
    "auc_above_floor",
    "ranking_lift_positive",
    "ranking_bootstrap_lower_positive",
    "stage_support",
    "stage_probability_skill_and_calibration",
    "stage_ranking_nonnegative",
    "chronological_ranking_nonnegative",
    "holdout_brier_improvement_positive",
    "holdout_brier_bootstrap_lower_positive",
    "holdout_ece_at_most_8pct",
    "holdout_auc_above_floor",
    "holdout_ranking_lift_positive",
    "holdout_ranking_bootstrap_lower_positive",
)
CALIBRATION_EPS = 1e-6
FEATURE_SNAPSHOT_SCHEMA = (
    "dc20_three_engine_d_feature_snapshot_v2_quantized12"
)
FEATURE_SNAPSHOT_SIGNIFICANT_DIGITS = 12
_RESEARCH_ONLY_LEGACY_VALIDATION_PATH = (
    "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json"
)
_RESEARCH_ONLY_LEGACY_VALIDATION_SHA256 = (
    "99f89e8bbc40d0f6cc39c3312039156a79c4f45e24114fc4affb900f23a46fe4"
)
_RESEARCH_ONLY_LEGACY_ARTIFACT_SHA256 = {
    "promotion": "72dcbc139c3260a99b9dd6846403a2acd9ebeef8a518cb7b3ddfc75e52b81e5b",
    "big_loss": "9a3ba655f2026fba80cdf73b904278d0bc730c559f8d38eb14d8d547fd8409c6",
    "profit": "0e5e251dc0632ed120baf7e758a4cbfcebd940857fab62e71c57f3c1979891f3",
    "p_fill_shadow": "1b7c52b6e7270e98c25c19d2ccd8131cb97e902aaff62abb059232c086b54f06",
}

PROMOTION_SOURCE_FEATURES = (
    "five_year_stage_board_prior_rate",
    "five_year_stage_prior_rate",
    "five_year_recent_20d_rate",
    "five_year_recent_60d_rate",
    "five_year_prior_samples_log",
    "five_year_recent_60d_samples_log",
    "five_year_regime_delta",
    "five_year_board_stage_delta",
    "five_year_pre_streak_1d_return",
    "five_year_pre_streak_3d_return",
    "five_year_pre_streak_volatility",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "five_year_streak_runup",
    "five_year_price_log",
    "five_year_stock_prior_rate",
    "five_year_stock_prior_samples_log",
)
RUNTIME_PROMOTION_PRIOR_FEATURES = PROMOTION_SOURCE_FEATURES[:8]

TARGET_COLUMNS = frozenset(
    {
        "promotion_hit",
        "big_loss_hit",
        "profit_hit",
        "market_fill",
        "gross_return",
        "net_return",
    }
)
IDENTITY_COLUMNS = frozenset(
    {
        "signal_date",
        "buy_date",
        "target_exit_date",
        "actual_exit_date",
        "ts_code",
        "name",
        "industry",
        "board",
        "fill_reason",
    }
)
FORBIDDEN_FEATURE_COLUMNS = frozenset(
    {
        *TARGET_COLUMNS,
        "t_open",
        "t_close",
        "t_high",
        "t_low",
        "t_amount",
        "t_pct_change",
        "t_turnover_pct",
        "tplus1_open",
        "actual_buy_gap",
        "buy_open",
        "exit_open",
        "actual_open_price",
        "actual_t_close",
        "actual_exit_price",
        "actual_net_return",
        "continuation_limit_up_hit",
        "predicted_promotion_probability",
        "predicted_big_loss_probability",
        "predicted_profit_probability",
        "predicted_fill_probability",
        "promotion_rank",
        "big_loss_safety_rank",
        "profit_rank",
        "observation_rank",
        "trade_rank",
        "promotion_rank_score",
        "big_loss_rank_score",
        "profit_rank_score",
        "top10_selected",
    }
)

RUNTIME_ALIGNED_MARKET_FEATURES = D_CLOSE_FEATURE_COLUMNS
RUNTIME_ALIGNED_D_FEATURES = (
    "d_open",
    "d_close",
    "d_high",
    "d_low",
    "d_volume",
    "d_amount",
    "d_pct_change",
    "d_turnover_pct",
)
RUNTIME_ALIGNED_POOL_FEATURES = (
    "mechanism_limit_pct",
    "focus_pool_size",
    "stage_pool_size",
    "stage2_pool_size",
    "stage3_pool_size",
    "stage_pool_share",
    "same_industry_stage_count",
    "market_max_limit_times",
    "open_board_count",
    "reseal_score",
    "late_withdraw",
)
RUNTIME_ALIGNED_FEATURE_COLUMNS = frozenset(
    {
        *PROMOTION_SOURCE_FEATURES,
        *RUNTIME_ALIGNED_MARKET_FEATURES,
        *RUNTIME_ALIGNED_D_FEATURES,
        *RUNTIME_ALIGNED_POOL_FEATURES,
    }
)


def _clip_probability(values: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.clip(
        np.asarray(values, dtype=float),
        CALIBRATION_EPS,
        1.0 - CALIBRATION_EPS,
    )


def _calibration_design(method: str, probability: np.ndarray) -> np.ndarray:
    probability = _clip_probability(probability)
    if method == "platt":
        return np.log(probability / (1.0 - probability)).reshape(-1, 1)
    if method == "beta":
        return np.column_stack(
            (np.log(probability), -np.log1p(-probability))
        )
    return probability.reshape(-1, 1)


@dataclass
class ProbabilityCalibrator:
    method: str
    constant: float
    estimator: Optional[Any] = None

    def transform(
        self, raw_probability: Sequence[float] | np.ndarray
    ) -> np.ndarray:
        raw = _clip_probability(raw_probability)
        if self.method == "constant" or self.estimator is None:
            if self.method == "identity":
                return np.clip(raw, 0.0, 1.0)
            return np.repeat(float(np.clip(self.constant, 0.0, 1.0)), len(raw))
        if self.method == "isotonic":
            calibrated = self.estimator.predict(raw)
        else:
            calibrated = self.estimator.predict_proba(
                _calibration_design(self.method, raw)
            )[:, 1]
        return np.clip(calibrated, 0.0, 1.0)


def _calibration_rejection_evidence(method: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": MONOTONICITY_SCHEMA_VERSION,
        "method": str(method),
        "nondecreasing": False,
        "rejection_reason": str(reason),
    }


def _fit_probability_calibrator_audited(
    method: str,
    raw_probability: Sequence[float] | np.ndarray,
    truth: Sequence[int] | np.ndarray,
    *,
    sample_weight: Optional[Sequence[float] | np.ndarray] = None,
    constant: float,
) -> tuple[Optional[ProbabilityCalibrator], dict[str, Any]]:
    raw = _clip_probability(raw_probability)
    y = np.asarray(truth, dtype=int)
    weights = (
        np.asarray(sample_weight, dtype=float)
        if sample_weight is not None
        else np.ones(len(y), dtype=float)
    )
    if len(raw) != len(y) or len(y) == 0:
        return None, _calibration_rejection_evidence(
            method, "empty_or_length_mismatch"
        )

    def _audited(
        candidate: ProbabilityCalibrator,
    ) -> tuple[Optional[ProbabilityCalibrator], dict[str, Any]]:
        evidence = calibrator_monotonicity_evidence(candidate, raw)
        valid = (
            evidence.get("nondecreasing") is True
            if candidate.method == "constant"
            else monotonicity_evidence_is_valid(
                evidence,
                expected_method=candidate.method,
                require_nonconstant=True,
            )
        )
        if not valid:
            evidence = dict(evidence)
            evidence["rejection_reason"] = (
                "calibration_mapping_not_order_preserving"
            )
            return None, evidence
        return candidate, evidence

    if method == "constant":
        return _audited(ProbabilityCalibrator("constant", constant))
    if method == "identity":
        return _audited(ProbabilityCalibrator("identity", constant))
    if np.unique(y).size < 2:
        return None, _calibration_rejection_evidence(method, "single_class_truth")
    if method == "isotonic":
        if len(y) < 40 or np.unique(raw).size < 8:
            return None, _calibration_rejection_evidence(
                method, "insufficient_isotonic_support"
            )
        estimator = IsotonicRegression(
            y_min=CALIBRATION_EPS,
            y_max=1.0 - CALIBRATION_EPS,
            out_of_bounds="clip",
        )
        estimator.fit(raw, y, sample_weight=weights)
        return _audited(ProbabilityCalibrator(method, constant, estimator))
    if method not in {"platt", "beta"}:
        raise ValueError(f"unsupported probability calibration method: {method}")
    estimator = LogisticRegression(
        C=1.0,
        max_iter=2_000,
        random_state=20260726,
    )
    estimator.fit(
        _calibration_design(method, raw),
        y,
        sample_weight=weights,
    )
    return _audited(ProbabilityCalibrator(method, constant, estimator))


def fit_probability_calibrator(
    method: str,
    raw_probability: Sequence[float] | np.ndarray,
    truth: Sequence[int] | np.ndarray,
    *,
    sample_weight: Optional[Sequence[float] | np.ndarray] = None,
    constant: float,
) -> Optional[ProbabilityCalibrator]:
    candidate, _ = _fit_probability_calibrator_audited(
        method,
        raw_probability,
        truth,
        sample_weight=sample_weight,
        constant=constant,
    )
    return candidate


def probability_metrics(
    probability: Sequence[float] | np.ndarray,
    truth: Sequence[int] | np.ndarray,
    *,
    sample_weight: Optional[Sequence[float] | np.ndarray] = None,
    bins: int = 10,
) -> dict[str, Any]:
    probability_array = _clip_probability(probability)
    truth_array = np.asarray(truth, dtype=float)
    weights = (
        np.asarray(sample_weight, dtype=float)
        if sample_weight is not None
        else np.ones(len(truth_array), dtype=float)
    )
    if len(probability_array) != len(truth_array) or len(truth_array) == 0:
        return {}
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    if weights.sum() <= 0:
        weights = np.ones(len(truth_array), dtype=float)
    brier = float(
        np.average(
            (probability_array - truth_array) ** 2,
            weights=weights,
        )
    )
    log_loss = float(
        np.average(
            -(
                truth_array * np.log(probability_array)
                + (1.0 - truth_array) * np.log1p(-probability_array)
            ),
            weights=weights,
        )
    )
    edges = np.linspace(0.0, 1.0, max(2, int(bins)) + 1)
    bucket = np.clip(
        np.digitize(probability_array, edges[1:-1], right=False),
        0,
        len(edges) - 2,
    )
    reliability: list[dict[str, Any]] = []
    ece = 0.0
    total_weight = float(weights.sum())
    for index in range(len(edges) - 1):
        mask = bucket == index
        if not mask.any():
            continue
        bucket_weight = float(weights[mask].sum())
        predicted = float(
            np.average(probability_array[mask], weights=weights[mask])
        )
        observed = float(np.average(truth_array[mask], weights=weights[mask]))
        ece += bucket_weight / total_weight * abs(predicted - observed)
        reliability.append(
            {
                "lower": round(float(edges[index]), 6),
                "upper": round(float(edges[index + 1]), 6),
                "samples": int(mask.sum()),
                "weight": round(bucket_weight, 10),
                "predicted": round(predicted, 10),
                "observed": round(observed, 10),
            }
        )
    return {
        "brier": brier,
        "log_loss": log_loss,
        "ece": float(ece),
        "reliability": reliability,
    }


@dataclass(frozen=True)
class HeadSpec:
    name: str
    target: str
    probability_column: str
    raw_score_column: str
    rank_column: str
    ascending: bool
    requires_market_fill: bool
    label_description: str


HEAD_SPECS: dict[str, HeadSpec] = {
    "promotion": HeadSpec(
        name="promotion",
        target="promotion_hit",
        probability_column="predicted_promotion_probability",
        raw_score_column="promotion_rank_score",
        rank_column="promotion_rank",
        ascending=False,
        requires_market_fill=False,
        label_description=(
            "T public-market/exchange daily-bar close limit-rule outcome"
        ),
    ),
    "big_loss": HeadSpec(
        name="big_loss",
        target="big_loss_hit",
        probability_column="predicted_big_loss_probability",
        raw_score_column="big_loss_rank_score",
        rank_column="big_loss_safety_rank",
        ascending=True,
        requires_market_fill=True,
        label_description=(
            "P(T public-market/exchange daily-bar open proxy to T+1 open proxy "
            "net return <= -3% "
            "conditional on market_fill proxy = 1)"
        ),
    ),
    "profit": HeadSpec(
        name="profit",
        target="profit_hit",
        probability_column="predicted_profit_probability",
        raw_score_column="profit_rank_score",
        rank_column="profit_rank",
        ascending=False,
        requires_market_fill=True,
        label_description=(
            "P(T public-market/exchange daily-bar open proxy to T+1 open proxy "
            "net return > 0 "
            "conditional on market_fill proxy = 1)"
        ),
    ),
    "p_fill_shadow": HeadSpec(
        name="p_fill_shadow",
        target="market_fill",
        probability_column="p_fill_shadow_probability",
        raw_score_column="p_fill_shadow_score",
        rank_column="p_fill_shadow_rank",
        ascending=False,
        requires_market_fill=False,
        label_description=(
            "public-market buyability proxy; not actual order fill and not a core rank"
        ),
    ),
}


@dataclass(frozen=True)
class ThreeEngineConfig:
    top_n: int = THREE_ENGINE_TOP_N
    promotion_warmup_dates: int = 300
    outcome_warmup_dates: int = 200
    outer_block_dates: int = 40
    embargo_dates: int = 2
    final_holdout_dates: int = 180
    inner_fit_fraction: float = 0.64
    inner_calibration_fraction: float = 0.17
    minimum_inner_fit_dates: int = 80
    minimum_inner_calibration_dates: int = 24
    minimum_inner_selection_dates: int = 24
    minimum_fit_rows: int = 400
    minimum_class_rows: int = 30
    minimum_history_dates: int = 1_000
    minimum_history_rows: int = 5_000
    minimum_outcome_history_dates: int = 700
    minimum_outcome_history_rows: int = 4_000
    minimum_oos_dates: int = 500
    minimum_oos_rows: int = 1_000
    minimum_stage_oos_rows: int = 100
    maximum_ece: float = 0.08
    minimum_auc: float = 0.50
    minimum_brier_improvement: float = 0.0
    selection_rank1_weight: float = 0.60
    selection_top3_weight: float = 0.30
    selection_ndcg_weight: float = 0.10
    bootstrap_samples: int = 1_000
    bootstrap_block_dates: int = 5
    random_state: int = 20260823
    model_kinds: tuple[str, ...] = ("lr", "hgb", "extra_trees")
    calibration_methods: tuple[str, ...] = (
        "identity",
        "platt",
        "beta",
        "isotonic",
    )
    release_mode: bool = True

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        if self.embargo_dates < 1:
            raise ValueError("embargo_dates must be at least one trading date")
        if self.release_mode and self.final_holdout_dates < 180:
            raise ValueError("final_holdout_dates must be at least 180 trading dates")
        if self.release_mode and self.minimum_oos_dates < 500:
            raise ValueError("minimum_oos_dates must be at least 500")
        if not 0.0 < self.inner_fit_fraction < 1.0:
            raise ValueError("inner_fit_fraction must be in (0, 1)")
        if not 0.0 < self.inner_calibration_fraction < 1.0:
            raise ValueError("inner_calibration_fraction must be in (0, 1)")
        if self.inner_fit_fraction + self.inner_calibration_fraction >= 0.92:
            raise ValueError("inner partitions leave too little independent selection history")
        if not self.model_kinds:
            raise ValueError("at least one non-constant model kind is required")
        if not self.calibration_methods:
            raise ValueError("at least one non-constant calibration method is required")
        if "constant" in self.model_kinds or "constant" in self.calibration_methods:
            raise ValueError("constant models/calibrators cannot create a rank")
        selection_weights = (
            self.selection_rank1_weight,
            self.selection_top3_weight,
            self.selection_ndcg_weight,
        )
        if any(weight < 0.0 for weight in selection_weights) or not math.isclose(
            sum(selection_weights), 1.0, abs_tol=1e-12
        ):
            raise ValueError("pre-registered selection ranking weights must sum to one")


@dataclass(frozen=True)
class DCloseFeatureBuilder:
    numeric_columns: tuple[str, ...]
    feature_names: tuple[str, ...]

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = sorted(set(self.numeric_columns) - set(frame.columns))
        if missing:
            raise ValueError(
                "runtime D snapshot is missing trained feature columns: "
                f"{missing}"
            )
        values: dict[str, pd.Series] = {
            name: pd.to_numeric(
                frame[name],
                errors="coerce",
            ).replace([np.inf, -np.inf], np.nan)
            for name in self.numeric_columns
        }
        if "stage" not in frame.columns or "board" not in frame.columns:
            raise ValueError("runtime D snapshot is missing stage/board")
        stage = pd.to_numeric(frame["stage"], errors="coerce").round()
        board = frame["board"].fillna("").astype(str).str.upper()
        values["stage_2"] = stage.eq(2.0).astype(float)
        values["stage_3"] = stage.eq(3.0).astype(float)
        values["board_sh_main"] = board.eq("SH_MAIN").astype(float)
        values["board_sz_main"] = board.eq("SZ_MAIN").astype(float)
        return pd.DataFrame(values, index=frame.index)[list(self.feature_names)]


@dataclass
class ProbabilityHeadBundle:
    head: str
    target: str
    model_kind: str
    calibration_method: str
    model: Pipeline
    calibrator: ProbabilityCalibrator
    feature_builder: DCloseFeatureBuilder
    training_constant: float
    trained_signal_start: str
    trained_signal_end: str
    training_rows: int
    training_dates: int
    model_fit_rows: int
    calibration_rows: int
    selection_rows: int
    selection_metrics: dict[str, Any] = field(default_factory=dict)

    def predict_components(
        self, frame: pd.DataFrame
    ) -> tuple[np.ndarray, np.ndarray]:
        features = self.feature_builder.transform(frame)
        raw = np.asarray(self.model.predict_proba(features)[:, 1], dtype=float)
        probability = self.calibrator.transform(raw)
        return (
            np.clip(probability, 0.0, 1.0),
            np.clip(raw, 0.0, 1.0),
        )


@dataclass
class HeadTrainingResult:
    spec: HeadSpec
    oof: pd.DataFrame
    development_bundle: Optional[ProbabilityHeadBundle]
    production_bundle: Optional[ProbabilityHeadBundle]
    validation: dict[str, Any]


@dataclass
class ThreeEngineTrainingResult:
    feature_builder: DCloseFeatureBuilder
    promotion: HeadTrainingResult
    big_loss: HeadTrainingResult
    profit: HeadTrainingResult
    p_fill_shadow: HeadTrainingResult
    oof_top10: pd.DataFrame
    validation: dict[str, Any]


class ThreeEngineArtifactError(ValueError):
    """Raised when a persisted model cannot prove its release provenance."""


@dataclass(frozen=True)
class LoadedThreeEngineArtifacts:
    root: Path
    validation_path: Path
    validation: dict[str, Any]
    payloads: dict[str, dict[str, Any]]
    metadata: dict[str, dict[str, Any]]
    runtime_ledger_path: Optional[Path] = None
    runtime_ledger_sha256: str = ""
    runtime_prior_ledger: pd.DataFrame = field(
        default_factory=pd.DataFrame,
        repr=False,
        compare=False,
    )


@dataclass
class ThreeEngineSnapshotScore:
    rows: pd.DataFrame
    status: str
    feature_snapshot_sha256: str
    top10_members_sha256: str
    promotion_pool_size: int
    model_metadata: dict[str, dict[str, Any]]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _normal_date(value: Any) -> str:
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        missing = False
    digits = "".join(
        character for character in str("" if missing else value) if character.isdigit()
    )
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        missing = False
    text = str("" if missing else value).strip().upper()
    if "." in text:
        digits, suffix = text.split(".", 1)
        digits = "".join(character for character in digits if character.isdigit())[:6]
        if len(digits) == 6 and suffix in {"SH", "SZ"}:
            return f"{digits}.{suffix}"
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _feature_snapshot_decimal(value: Any) -> Optional[str]:
    """Return the cross-platform canonical decimal used only by the hash.

    NumPy rolling standard deviation may differ by one or two IEEE-754 ULPs
    between ARM and x86 even when every market input and package version is
    identical.  The model still receives the original float.  Only the
    feature-snapshot fingerprint uses this explicit 12-significant-digit
    projection so equivalent inputs have one stable identity on GitHub.
    """

    number = _safe_float(value)
    if number is None:
        return None
    if number == 0.0:
        return "0"
    return format(number, f".{FEATURE_SNAPSHOT_SIGNIFICANT_DIGITS}g")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NA:
        return None
    return value


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def top10_members_sha256(signal_date: str, codes: Iterable[str]) -> str:
    return _canonical_sha256(
        {
            "schema": "dc20_three_rank_member_set_v1",
            "signal_date": _normal_date(signal_date),
            "members": sorted(
                {
                    _normal_code(code)
                    for code in codes
                    if _normal_code(code)
                }
            ),
        }
    )


def normalize_supervised_ledger(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "signal_date",
        "ts_code",
        "stage",
        "promotion_hit",
        "big_loss_hit",
        "profit_hit",
        "market_fill",
        "net_return",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"three-engine ledger is missing required columns: {missing}")
    output = frame.copy()
    output["signal_date"] = output["signal_date"].map(_normal_date)
    output["ts_code"] = output["ts_code"].map(_normal_code)
    stage_text = output["stage"].astype(str).str.replace("->", "→", regex=False)
    stage_numeric = pd.to_numeric(output["stage"], errors="coerce")
    stage_numeric = stage_numeric.where(
        stage_numeric.notna(),
        pd.to_numeric(stage_text.str.split("→").str[0], errors="coerce"),
    )
    output["stage"] = stage_numeric.round()
    invalid_key = (
        output["signal_date"].str.fullmatch(r"20\d{6}").ne(True)
        | output["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ)").ne(True)
        | ~output["stage"].isin((2.0, 3.0))
    )
    if invalid_key.any():
        raise ValueError(
            f"three-engine ledger contains {int(invalid_key.sum())} invalid key/stage rows"
        )
    if output.duplicated(["signal_date", "ts_code"]).any():
        duplicates = int(output.duplicated(["signal_date", "ts_code"], keep=False).sum())
        raise ValueError(f"three-engine ledger contains {duplicates} duplicate key rows")
    if "board" in output.columns:
        board = output["board"].fillna("").astype(str).str.upper()
        invalid_board = ~board.isin(("SH_MAIN", "SZ_MAIN"))
        if invalid_board.any():
            raise ValueError(
                f"three-engine ledger contains {int(invalid_board.sum())} non-main-board rows"
            )
        output["board"] = board
    else:
        output["board"] = np.where(
            output["ts_code"].str.endswith(".SH"), "SH_MAIN", "SZ_MAIN"
        )
    for target in ("promotion_hit", "big_loss_hit", "profit_hit", "market_fill"):
        values = pd.to_numeric(output[target], errors="coerce")
        invalid = values.notna() & ~values.isin((0.0, 1.0))
        if invalid.any():
            raise ValueError(f"{target} contains non-binary labels")
        output[target] = values
    output["net_return"] = pd.to_numeric(output["net_return"], errors="coerce")
    return output.sort_values(
        ["signal_date", "stage", "ts_code"], kind="stable"
    ).reset_index(drop=True)


def resolve_d_close_feature_builder(frame: pd.DataFrame) -> DCloseFeatureBuilder:
    numeric_columns: list[str] = []
    for name in sorted(frame.columns):
        lowered = name.lower()
        if name in FORBIDDEN_FEATURE_COLUMNS or name in IDENTITY_COLUMNS:
            continue
        if (
            lowered.startswith("predicted_")
            or lowered.endswith("_rank")
            or lowered.endswith("_hit")
            or lowered.startswith("trade_")
            or lowered.startswith("actual_")
            or lowered.startswith("tplus1_")
            or lowered.startswith("t_")
        ):
            continue
        if name not in RUNTIME_ALIGNED_FEATURE_COLUMNS:
            continue
        numeric_columns.append(name)
    forbidden_hits = sorted(set(numeric_columns).intersection(FORBIDDEN_FEATURE_COLUMNS))
    if forbidden_hits:
        raise ValueError(f"forbidden three-engine features selected: {forbidden_hits}")
    informative = [
        name
        for name in numeric_columns
        if pd.to_numeric(frame[name], errors="coerce").notna().any()
    ]
    if len(informative) < 2:
        raise ValueError("three-engine ledger has fewer than two usable D-close numeric features")
    # A column that was entirely missing in training is not a trained feature.
    # Persisting it would make the imputer invent a meaningless runtime
    # dependency and could turn a missing upstream table into silent scoring.
    numeric = tuple(informative)
    return DCloseFeatureBuilder(
        numeric_columns=numeric,
        feature_names=(
            *numeric,
            "stage_2",
            "stage_3",
            "board_sh_main",
            "board_sz_main",
        ),
    )


def date_balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.asarray([], dtype=float)
    dates = frame["signal_date"].astype(str)
    counts = dates.map(dates.value_counts()).clip(lower=1).astype(float)
    weights = (1.0 / counts).to_numpy(dtype=float)
    return weights / max(float(np.mean(weights)), 1e-12)


def _training_sample(frame: pd.DataFrame, spec: HeadSpec) -> pd.DataFrame:
    target = pd.to_numeric(frame.get(spec.target), errors="coerce")
    mask = target.notna()
    if spec.requires_market_fill:
        mask &= pd.to_numeric(frame.get("market_fill"), errors="coerce").eq(1)
    output = frame.loc[mask].copy()
    output[spec.target] = pd.to_numeric(output[spec.target], errors="coerce").astype(int)
    return output


def _inner_partitions(
    frame: pd.DataFrame,
    config: ThreeEngineConfig,
) -> tuple[list[str], list[str], list[str]]:
    dates = sorted(frame["signal_date"].astype(str).unique())
    n_dates = len(dates)
    fit_count = max(
        config.minimum_inner_fit_dates,
        int(math.floor(n_dates * config.inner_fit_fraction)),
    )
    calibration_count = max(
        config.minimum_inner_calibration_dates,
        int(math.floor(n_dates * config.inner_calibration_fraction)),
    )
    calibration_start = fit_count + config.embargo_dates
    calibration_end = calibration_start + calibration_count
    selection_start = calibration_end + config.embargo_dates
    fit_dates = dates[:fit_count]
    calibration_dates = dates[calibration_start:calibration_end]
    selection_dates = dates[selection_start:]
    if (
        len(fit_dates) < config.minimum_inner_fit_dates
        or len(calibration_dates) < config.minimum_inner_calibration_dates
        or len(selection_dates) < config.minimum_inner_selection_dates
    ):
        return [], [], []
    return fit_dates, calibration_dates, selection_dates


def _classifier(kind: str, config: ThreeEngineConfig) -> Pipeline:
    if kind == "lr":
        return Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                ("scaler", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.25,
                        max_iter=2_000,
                        random_state=config.random_state,
                    ),
                ),
            ]
        )
    if kind == "hgb":
        return Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.04,
                        max_iter=160,
                        max_leaf_nodes=15,
                        min_samples_leaf=25,
                        l2_regularization=1.0,
                        random_state=config.random_state,
                    ),
                ),
            ]
        )
    if kind == "extra_trees":
        return Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                (
                    "model",
                    ExtraTreesClassifier(
                        n_estimators=240,
                        min_samples_leaf=20,
                        max_features=0.70,
                        n_jobs=1,
                        random_state=config.random_state,
                    ),
                ),
            ]
        )
    raise ValueError(f"unsupported three-engine classifier: {kind}")


def _weighted_brier(
    probability: np.ndarray,
    truth: np.ndarray,
    weights: np.ndarray,
) -> float:
    valid = (
        np.isfinite(probability)
        & np.isfinite(truth)
        & np.isfinite(weights)
        & (weights > 0)
    )
    if not valid.any():
        return float("nan")
    return float(
        np.average((probability[valid] - truth[valid]) ** 2, weights=weights[valid])
    )


def _selection_ranking_metrics(
    selection: pd.DataFrame,
    spec: HeadSpec,
    probability: np.ndarray,
    raw_score: np.ndarray,
    config: ThreeEngineConfig,
) -> dict[str, Any]:
    scored = selection.copy()
    scored[spec.probability_column] = probability
    scored[spec.raw_score_column] = raw_score
    scored = _rank_scored_rows(scored, spec)
    daily: list[dict[str, float]] = []
    for _, group in scored.groupby("signal_date", sort=True):
        truth = pd.to_numeric(group[spec.target], errors="coerce")
        valid = truth.notna()
        if spec.requires_market_fill:
            valid &= pd.to_numeric(group.get("market_fill"), errors="coerce").eq(1)
        group = group.loc[valid].copy()
        if group.empty:
            continue
        group["_truth"] = truth.loc[valid].astype(float)
        candidate = group.sort_values([spec.rank_column, "ts_code"], kind="stable")
        if spec.name == "promotion":
            baseline_score = _baseline_rank_score(group)
            baseline = group.assign(_baseline_score=baseline_score).sort_values(
                ["_baseline_score", "ts_code"],
                ascending=[False, True],
                kind="stable",
                na_position="last",
            )
            rank1_lift = float(
                candidate.head(1)["_truth"].mean()
                - baseline.head(1)["_truth"].mean()
            )
            top3_lift = float(
                candidate.head(3)["_truth"].mean()
                - baseline.head(3)["_truth"].mean()
            )
            group_truth = group["_truth"].to_numpy(dtype=float)
            candidate_ndcg = _ndcg_against_group_ideal(
                candidate.head(config.top_n)["_truth"].to_numpy(dtype=float),
                group_truth,
                config.top_n,
            )
            baseline_ndcg = _ndcg_against_group_ideal(
                baseline.head(config.top_n)["_truth"].to_numpy(dtype=float),
                group_truth,
                config.top_n,
            )
            ndcg_lift = candidate_ndcg - baseline_ndcg
        else:
            if "promotion_rank" not in group.columns:
                continue
            promotion_rank = pd.to_numeric(
                group["promotion_rank"], errors="coerce"
            )
            if promotion_rank.notna().sum() != len(group):
                # B/C are defined only inside A's frozen Top10.  Without that
                # deterministic upstream order there is no honest relative
                # full-list baseline, so this date cannot support selection.
                continue
            baseline = group.assign(_promotion_rank=promotion_rank).sort_values(
                ["_promotion_rank", "ts_code"], kind="stable"
            )
            rank1 = float(candidate.head(1)["_truth"].mean())
            rank3 = float(candidate.head(3)["_truth"].mean())
            pool = float(group["_truth"].mean())
            if spec.name == "big_loss":
                rank1_lift = pool - rank1
                top3_lift = pool - rank3
                candidate_relevance = (
                    1.0
                    - candidate.head(config.top_n)["_truth"].to_numpy(dtype=float)
                )
                baseline_relevance = (
                    1.0
                    - baseline.head(config.top_n)["_truth"].to_numpy(dtype=float)
                )
                group_relevance = 1.0 - group["_truth"].to_numpy(dtype=float)
            else:
                rank1_lift = rank1 - pool
                top3_lift = rank3 - pool
                candidate_relevance = candidate.head(config.top_n)[
                    "_truth"
                ].to_numpy(dtype=float)
                baseline_relevance = baseline.head(config.top_n)[
                    "_truth"
                ].to_numpy(dtype=float)
                group_relevance = group["_truth"].to_numpy(dtype=float)
            candidate_ndcg = _ndcg_against_group_ideal(
                candidate_relevance,
                group_relevance,
                config.top_n,
            )
            baseline_ndcg = _ndcg_against_group_ideal(
                baseline_relevance,
                group_relevance,
                config.top_n,
            )
            ndcg_lift = candidate_ndcg - baseline_ndcg
        daily.append(
            {
                "rank1_lift": rank1_lift,
                "top3_lift": top3_lift,
                "candidate_ndcg_at_10": candidate_ndcg,
                "baseline_ndcg_at_10": baseline_ndcg,
                "ndcg_lift": ndcg_lift,
            }
        )
    if not daily:
        return {
            "dates": 0,
            "rank1_lift": None,
            "top3_lift": None,
            "candidate_ndcg_at_10": None,
            "baseline_ndcg_at_10": None,
            "ndcg_lift": None,
            "composite_lift": None,
        }
    metrics = pd.DataFrame(daily).mean(numeric_only=True)
    rank1_lift = float(metrics["rank1_lift"])
    top3_lift = float(metrics["top3_lift"])
    candidate_ndcg = float(metrics["candidate_ndcg_at_10"])
    baseline_ndcg = float(metrics["baseline_ndcg_at_10"])
    ndcg_lift = float(metrics["ndcg_lift"])
    composite = (
        config.selection_rank1_weight * rank1_lift
        + config.selection_top3_weight * top3_lift
        + config.selection_ndcg_weight * ndcg_lift
    )
    return {
        "dates": int(len(daily)),
        "rank1_lift": rank1_lift,
        "top3_lift": top3_lift,
        "candidate_ndcg_at_10": candidate_ndcg,
        "baseline_ndcg_at_10": baseline_ndcg,
        "ndcg_lift": ndcg_lift,
        "composite_lift": float(composite),
    }


def _fit_head_inner(
    frame: pd.DataFrame,
    spec: HeadSpec,
    feature_builder: DCloseFeatureBuilder,
    config: ThreeEngineConfig,
    *,
    locked_model_kind: str = "",
    locked_calibration_method: str = "",
) -> Optional[ProbabilityHeadBundle]:
    sample = _training_sample(frame, spec)
    fit_dates, calibration_dates, selection_dates = _inner_partitions(sample, config)
    if not fit_dates:
        return None
    fit = sample[sample["signal_date"].astype(str).isin(fit_dates)].copy()
    calibration = sample[
        sample["signal_date"].astype(str).isin(calibration_dates)
    ].copy()
    selection = sample[
        sample["signal_date"].astype(str).isin(selection_dates)
    ].copy()
    fit_values = fit[spec.target].astype(int)
    if (
        len(fit) < config.minimum_fit_rows
        or fit_values.nunique() < 2
        or int(fit_values.value_counts().min()) < config.minimum_class_rows
        or calibration[spec.target].nunique() < 2
        or selection[spec.target].nunique() < 2
    ):
        return None
    fit_weights = date_balanced_weights(fit)
    calibration_weights = date_balanced_weights(calibration)
    selection_weights = date_balanced_weights(selection)
    constant = float(
        np.average(fit_values.to_numpy(dtype=float), weights=fit_weights)
    )
    baseline_probability = np.repeat(constant, len(selection))
    baseline_brier = _weighted_brier(
        baseline_probability,
        selection[spec.target].to_numpy(dtype=float),
        selection_weights,
    )
    model_kinds = (
        (locked_model_kind,) if locked_model_kind else config.model_kinds
    )
    calibration_methods = (
        (locked_calibration_method,)
        if locked_calibration_method
        else config.calibration_methods
    )
    candidates: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    fit_features = feature_builder.transform(fit)
    calibration_features = feature_builder.transform(calibration)
    selection_features = feature_builder.transform(selection)
    for kind in model_kinds:
        model = _classifier(kind, config)
        try:
            model.fit(
                fit_features,
                fit_values,
                model__sample_weight=fit_weights,
            )
            raw_calibration = np.asarray(
                model.predict_proba(calibration_features)[:, 1], dtype=float
            )
            raw_selection = np.asarray(
                model.predict_proba(selection_features)[:, 1], dtype=float
            )
        except (TypeError, ValueError):
            continue
        for method in calibration_methods:
            calibrator = fit_probability_calibrator(
                method,
                raw_calibration,
                calibration[spec.target].astype(int).to_numpy(),
                sample_weight=calibration_weights,
                constant=constant,
            )
            if calibrator is None or calibrator.method == "constant":
                continue
            monotonicity = calibrator_monotonicity_evidence(
                calibrator,
                raw_calibration,
            )
            if not monotonicity_evidence_is_valid(
                monotonicity,
                expected_method=calibrator.method,
                require_nonconstant=True,
            ):
                # Defensive duplicate of the fitter's fail-closed rule.  The
                # evidence is recomputed at the exact candidate boundary so a
                # future calibrator implementation cannot bypass selection.
                continue
            probability = calibrator.transform(raw_selection)
            metrics = probability_metrics(
                probability,
                selection[spec.target].astype(int).to_numpy(),
                sample_weight=selection_weights,
            )
            brier = float(metrics.get("brier", float("inf")))
            ece = float(metrics.get("ece", float("inf")))
            key = f"{kind}+{method}"
            candidate_metrics = {
                "brier": _safe_float(brier),
                "ece": _safe_float(ece),
                "baseline_brier": _safe_float(baseline_brier),
                "brier_improvement": _safe_float(baseline_brier - brier),
                "calibration_monotonicity": monotonicity,
            }
            ranking_metrics = _selection_ranking_metrics(
                selection,
                spec,
                probability,
                raw_selection,
                config,
            )
            candidate_metrics["ranking"] = ranking_metrics
            composite = _safe_float(ranking_metrics.get("composite_lift"))
            candidate_metrics["selection_eligible"] = bool(
                math.isfinite(brier)
                and math.isfinite(ece)
                and baseline_brier - brier > config.minimum_brier_improvement
                and ece <= config.maximum_ece
                and composite is not None
                and composite > 0.0
                and int(ranking_metrics.get("dates") or 0)
                >= config.minimum_inner_selection_dates
            )
            audit[key] = candidate_metrics
            if math.isfinite(brier) and math.isfinite(ece):
                candidates.append(
                    {
                        "brier": brier,
                        "ece": ece,
                        "kind": kind,
                        "method": method,
                        "model": model,
                        "calibrator": calibrator,
                        "metrics": candidate_metrics,
                        "composite": composite,
                        "eligible": candidate_metrics["selection_eligible"],
                    }
                )
    if not candidates:
        return None
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    if eligible:
        best = min(
            eligible,
            key=lambda item: (
                -float(item["composite"]),
                float(item["brier"]),
                float(item["ece"]),
                str(item["kind"]),
                str(item["method"]),
            ),
        )
    else:
        # Preserve an unbiased outer OOF prediction even if this historical
        # fold has no releasable candidate.  The audit and release gate retain
        # the failure; this fallback can never manufacture READY status.
        best = min(
            candidates,
            key=lambda item: (
                float(item["brier"]),
                float(item["ece"]),
                str(item["kind"]),
                str(item["method"]),
            ),
        )
    best_kind = str(best["kind"])
    best_method = str(best["method"])
    best_model = best["model"]
    best_calibrator = best["calibrator"]
    best_metrics = dict(best["metrics"])
    return ProbabilityHeadBundle(
        head=spec.name,
        target=spec.target,
        model_kind=best_kind,
        calibration_method=best_method,
        model=best_model,
        calibrator=best_calibrator,
        feature_builder=feature_builder,
        training_constant=constant,
        trained_signal_start=fit_dates[0],
        trained_signal_end=selection_dates[-1],
        training_rows=int(len(sample)),
        training_dates=int(sample["signal_date"].astype(str).nunique()),
        model_fit_rows=int(len(fit)),
        calibration_rows=int(len(calibration)),
        selection_rows=int(len(selection)),
        selection_metrics={
            **best_metrics,
            "fit_dates": len(fit_dates),
            "calibration_dates": len(calibration_dates),
            "selection_dates": len(selection_dates),
            "candidates": audit,
            "eligible_candidate_count": int(len(eligible)),
            "chosen_candidate_eligible": bool(best["eligible"]),
            "selection_policy": (
                "brier_improvement_and_ece_eligible_then_maximize_"
                "pre_registered_weighted_ranking_lift"
            ),
            "selection_weights": {
                "rank1": config.selection_rank1_weight,
                "top3": config.selection_top3_weight,
                "ndcg": config.selection_ndcg_weight,
            },
            "final_holdout_used_for_selection": False,
            "constant_rank_forbidden": True,
        },
    )


def _truth_available_before(
    frame: pd.DataFrame,
    spec: HeadSpec,
    test_start: str,
) -> pd.DataFrame:
    if spec.name == "promotion":
        column = "buy_date" if "buy_date" in frame.columns else "signal_date"
    elif spec.name in {"big_loss", "profit"}:
        column = (
            "target_exit_date"
            if "target_exit_date" in frame.columns
            else "signal_date"
        )
    else:
        column = "buy_date" if "buy_date" in frame.columns else "signal_date"
    availability = frame[column].map(_normal_date)
    return frame.loc[availability.lt(test_start)].copy()


def _rank_scored_rows(
    scored: pd.DataFrame,
    spec: HeadSpec,
) -> pd.DataFrame:
    output = scored.copy()
    output[spec.rank_column] = np.nan
    ascending = [True, spec.ascending, spec.ascending, True]
    ordered = output.assign(
        _signal_date=output["signal_date"].astype(str),
        _probability=pd.to_numeric(
            output[spec.probability_column], errors="coerce"
        ),
        _raw_score=pd.to_numeric(output[spec.raw_score_column], errors="coerce"),
        _ts_code=output["ts_code"].astype(str),
    ).sort_values(
        ["_signal_date", "_probability", "_raw_score", "_ts_code"],
        ascending=ascending,
        kind="stable",
        na_position="last",
    )
    output.loc[ordered.index, spec.rank_column] = (
        ordered.groupby("_signal_date", sort=False).cumcount() + 1
    ).to_numpy()
    output[spec.rank_column] = output[spec.rank_column].astype("Int64")
    return output


def _score_bundle(
    frame: pd.DataFrame,
    bundle: ProbabilityHeadBundle,
    spec: HeadSpec,
) -> pd.DataFrame:
    output = frame.copy()
    probability, raw = bundle.predict_components(output)
    output[spec.probability_column] = probability
    output[spec.raw_score_column] = raw
    output[f"{spec.name}_baseline_probability"] = bundle.training_constant
    return _rank_scored_rows(output, spec)


def _walkforward_head(
    frame: pd.DataFrame,
    spec: HeadSpec,
    feature_builder: DCloseFeatureBuilder,
    config: ThreeEngineConfig,
    *,
    warmup_dates: int,
    freeze_top10: bool,
) -> tuple[pd.DataFrame, Optional[ProbabilityHeadBundle]]:
    if frame.empty or "signal_date" not in frame.columns:
        return frame.head(0).copy(), None
    dates = sorted(frame["signal_date"].astype(str).unique())
    if len(dates) <= warmup_dates + config.final_holdout_dates:
        return pd.DataFrame(), None
    holdout_start_index = len(dates) - config.final_holdout_dates
    output: list[pd.DataFrame] = []
    fold = 0
    for block_start in range(
        warmup_dates,
        holdout_start_index,
        config.outer_block_dates,
    ):
        test_dates = dates[
            block_start : min(
                block_start + config.outer_block_dates,
                holdout_start_index,
            )
        ]
        if not test_dates:
            continue
        train_end = block_start - config.embargo_dates
        if train_end <= 0:
            continue
        train = frame[frame["signal_date"].astype(str).isin(dates[:train_end])].copy()
        train = _truth_available_before(train, spec, test_dates[0])
        bundle = _fit_head_inner(train, spec, feature_builder, config)
        if bundle is None:
            continue
        test = frame[frame["signal_date"].astype(str).isin(test_dates)].copy()
        scored = _score_bundle(test, bundle, spec)
        fold += 1
        scored[f"{spec.name}_oof_fold"] = fold
        scored[f"{spec.name}_oof_fold_kind"] = "development_walkforward"
        scored[f"{spec.name}_oof_train_end"] = bundle.trained_signal_end
        scored[f"{spec.name}_oof_model_kind"] = bundle.model_kind
        scored[f"{spec.name}_oof_calibration"] = bundle.calibration_method
        scored[f"{spec.name}_oof_selection_eligible"] = bool(
            bundle.selection_metrics.get("chosen_candidate_eligible")
        )
        scored[f"{spec.name}_oof_selection_composite_lift"] = _safe_float(
            bundle.selection_metrics.get("ranking", {}).get("composite_lift")
        )
        output.append(scored)

    holdout_dates = dates[holdout_start_index:]
    holdout_train_end = holdout_start_index - config.embargo_dates
    holdout_train = frame[
        frame["signal_date"].astype(str).isin(dates[:holdout_train_end])
    ].copy()
    holdout_train = _truth_available_before(
        holdout_train, spec, holdout_dates[0]
    )
    development_bundle = _fit_head_inner(
        holdout_train,
        spec,
        feature_builder,
        config,
    )
    if development_bundle is not None:
        holdout = frame[
            frame["signal_date"].astype(str).isin(holdout_dates)
        ].copy()
        holdout_scored = _score_bundle(
            holdout, development_bundle, spec
        )
        fold += 1
        holdout_scored[f"{spec.name}_oof_fold"] = fold
        holdout_scored[f"{spec.name}_oof_fold_kind"] = "final_independent_holdout"
        holdout_scored[f"{spec.name}_oof_train_end"] = (
            development_bundle.trained_signal_end
        )
        holdout_scored[f"{spec.name}_oof_model_kind"] = (
            development_bundle.model_kind
        )
        holdout_scored[f"{spec.name}_oof_calibration"] = (
            development_bundle.calibration_method
        )
        holdout_scored[f"{spec.name}_oof_selection_eligible"] = bool(
            development_bundle.selection_metrics.get("chosen_candidate_eligible")
        )
        holdout_scored[f"{spec.name}_oof_selection_composite_lift"] = _safe_float(
            development_bundle.selection_metrics.get("ranking", {}).get(
                "composite_lift"
            )
        )
        output.append(holdout_scored)
    oof = pd.concat(output, ignore_index=True) if output else pd.DataFrame()
    if oof.empty:
        return oof, development_bundle
    oof = oof.sort_values(
        ["signal_date", spec.rank_column, "ts_code"], kind="stable"
    ).reset_index(drop=True)
    if freeze_top10:
        pool_size = oof.groupby("signal_date")["ts_code"].transform("size")
        oof["promotion_pool_size"] = pool_size.astype(int)
        oof["top10_selected"] = (
            pd.to_numeric(oof[spec.rank_column], errors="coerce")
            .le(np.minimum(config.top_n, pool_size))
            .astype(int)
        )
        hashes: dict[str, str] = {}
        for date, group in oof[oof["top10_selected"].eq(1)].groupby(
            "signal_date", sort=True
        ):
            hashes[str(date)] = top10_members_sha256(
                str(date), group["ts_code"].astype(str)
            )
        oof["top10_members_sha256"] = oof["signal_date"].astype(str).map(hashes)
    return oof, development_bundle


def _locked_production_bundle(
    frame: pd.DataFrame,
    spec: HeadSpec,
    feature_builder: DCloseFeatureBuilder,
    config: ThreeEngineConfig,
    development_bundle: Optional[ProbabilityHeadBundle],
) -> tuple[Optional[ProbabilityHeadBundle], dict[str, Any]]:
    if development_bundle is None:
        return None, _calibration_rejection_evidence(
            "", "missing_development_bundle"
        )
    sample = _training_sample(frame, spec)
    dates = sorted(sample["signal_date"].astype(str).unique())
    audit_count = config.minimum_inner_selection_dates
    audit_start = len(dates) - audit_count
    calibration_count = max(
        config.minimum_inner_calibration_dates,
        int(math.floor(len(dates) * config.inner_calibration_fraction)),
    )
    calibration_end = audit_start - config.embargo_dates
    calibration_start = calibration_end - calibration_count
    fit_end = calibration_start - config.embargo_dates
    if (
        audit_count < config.minimum_inner_selection_dates
        or audit_start <= 0
        or calibration_end <= calibration_start
        or fit_end < config.minimum_inner_fit_dates
    ):
        return None, _calibration_rejection_evidence(
            development_bundle.calibration_method,
            "insufficient_production_partition",
        )
    fit_dates = dates[:fit_end]
    calibration_dates = dates[calibration_start:calibration_end]
    audit_dates = dates[audit_start:]
    fit = sample[sample["signal_date"].astype(str).isin(fit_dates)].copy()
    calibration = sample[
        sample["signal_date"].astype(str).isin(calibration_dates)
    ].copy()
    audit = sample[sample["signal_date"].astype(str).isin(audit_dates)].copy()
    fit_values = fit[spec.target].astype(int)
    if (
        len(fit) < config.minimum_fit_rows
        or fit_values.nunique() < 2
        or int(fit_values.value_counts().min()) < config.minimum_class_rows
        or calibration[spec.target].nunique() < 2
    ):
        return None, _calibration_rejection_evidence(
            development_bundle.calibration_method,
            "insufficient_production_class_support",
        )
    fit_weights = date_balanced_weights(fit)
    calibration_weights = date_balanced_weights(calibration)
    constant = float(np.average(fit_values.to_numpy(dtype=float), weights=fit_weights))
    model = _classifier(development_bundle.model_kind, config)
    try:
        model.fit(
            feature_builder.transform(fit),
            fit_values,
            model__sample_weight=fit_weights,
        )
        raw_calibration = np.asarray(
            model.predict_proba(feature_builder.transform(calibration))[:, 1],
            dtype=float,
        )
    except (TypeError, ValueError):
        return None, _calibration_rejection_evidence(
            development_bundle.calibration_method,
            "production_model_fit_or_score_failed",
        )
    calibrator, monotonicity = _fit_probability_calibrator_audited(
        development_bundle.calibration_method,
        raw_calibration,
        calibration[spec.target].astype(int).to_numpy(),
        sample_weight=calibration_weights,
        constant=constant,
    )
    if calibrator is None or calibrator.method == "constant":
        if calibrator is not None and calibrator.method == "constant":
            monotonicity = dict(monotonicity)
            monotonicity["nondecreasing"] = False
            monotonicity["rejection_reason"] = (
                "constant_production_calibrator_forbidden"
            )
        return None, monotonicity
    calibrated = calibrator.transform(raw_calibration)
    metrics = probability_metrics(
        calibrated,
        calibration[spec.target].astype(int).to_numpy(),
        sample_weight=calibration_weights,
    )
    bundle = ProbabilityHeadBundle(
        head=spec.name,
        target=spec.target,
        model_kind=development_bundle.model_kind,
        calibration_method=development_bundle.calibration_method,
        model=model,
        calibrator=calibrator,
        feature_builder=feature_builder,
        training_constant=constant,
        trained_signal_start=fit_dates[0],
        trained_signal_end=calibration_dates[-1],
        training_rows=int(len(sample)),
        training_dates=int(len(dates)),
        model_fit_rows=int(len(fit)),
        calibration_rows=int(len(calibration)),
        selection_rows=0,
        selection_metrics={
            "release_refit": True,
            "family_locked_before_final_holdout": True,
            "model_fit_dates": int(len(fit_dates)),
            "embargo_dates": int(config.embargo_dates),
            "calibration_dates": int(len(calibration_dates)),
            "independent_rank_audit_dates": int(len(audit_dates)),
            "calibration_metrics": _json_safe(metrics),
            "constant_rank_forbidden": True,
        },
    )
    audit_scored = _score_bundle(audit, bundle, spec)
    audit_variation = _rank_variation(audit_scored, spec)
    audit_valid = bool(
        int(audit_variation.get("eligible_dates") or 0)
        >= config.minimum_inner_selection_dates
        and float(audit_variation.get("nonconstant_date_fraction") or 0.0)
        >= 0.90
    )
    monotonicity = dict(monotonicity)
    monotonicity["independent_production_rank_audit"] = {
        "truth_or_performance_used": False,
        "fit_or_calibration_rows_used": False,
        "embargo_dates": int(config.embargo_dates),
        "start": audit_dates[0] if audit_dates else "",
        "end": audit_dates[-1] if audit_dates else "",
        "calendar_dates": int(len(audit_dates)),
        "rows": int(len(audit_scored)),
        **audit_variation,
        "minimum_eligible_dates": int(config.minimum_inner_selection_dates),
        "minimum_nonconstant_date_fraction": 0.90,
        "valid": audit_valid,
    }
    if not audit_valid:
        monotonicity["rejection_reason"] = (
            "production_rank_is_constant_on_independent_audit"
        )
        return None, monotonicity
    bundle.selection_metrics["calibration_monotonicity"] = _json_safe(
        monotonicity
    )
    return bundle, monotonicity


def _rank_variation(
    sample: pd.DataFrame,
    spec: HeadSpec,
) -> dict[str, Any]:
    if sample.empty or spec.probability_column not in sample.columns:
        return {
            "eligible_dates": 0,
            "nonconstant_dates": 0,
            "nonconstant_date_fraction": None,
        }
    eligible = 0
    nonconstant = 0
    for _, group in sample.groupby("signal_date", sort=True):
        probability = pd.to_numeric(
            group[spec.probability_column], errors="coerce"
        ).dropna()
        if len(probability) < 2:
            continue
        eligible += 1
        nonconstant += int(probability.nunique(dropna=True) > 1)
    return {
        "eligible_dates": eligible,
        "nonconstant_dates": nonconstant,
        "nonconstant_date_fraction": (
            float(nonconstant / eligible) if eligible else None
        ),
    }


def _ece(probability: np.ndarray, truth: np.ndarray, weights: np.ndarray) -> float:
    metrics = probability_metrics(
        probability,
        truth,
        sample_weight=weights,
    )
    return float(metrics.get("ece", float("nan")))


def _block_bootstrap_mean_ci(
    daily_values: pd.Series,
    config: ThreeEngineConfig,
    *,
    seed_offset: int,
) -> dict[str, Any]:
    clean = pd.to_numeric(daily_values, errors="coerce").dropna()
    if clean.empty:
        return {"samples": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    values = clean.to_numpy(dtype=float)
    block_size = max(1, min(config.bootstrap_block_dates, len(values)))
    blocks = [values[index : index + block_size] for index in range(0, len(values), block_size)]
    rng = np.random.default_rng(config.random_state + seed_offset)
    estimates = np.empty(config.bootstrap_samples, dtype=float)
    for index in range(config.bootstrap_samples):
        pieces: list[np.ndarray] = []
        while sum(len(piece) for piece in pieces) < len(values):
            pieces.append(blocks[int(rng.integers(0, len(blocks)))])
        sample = np.concatenate(pieces)[: len(values)]
        estimates[index] = float(np.mean(sample))
    return {
        "samples": int(len(values)),
        "mean": float(np.mean(values)),
        "ci95_low": float(np.quantile(estimates, 0.025)),
        "ci95_high": float(np.quantile(estimates, 0.975)),
        "bootstrap_samples": int(config.bootstrap_samples),
        "block_dates": int(block_size),
    }


def _baseline_rank_score(frame: pd.DataFrame) -> pd.Series:
    for name in (
        "five_year_stage_board_prior_rate",
        "five_year_recent_60d_rate",
        "five_year_stage_prior_rate",
        "five_year_stock_prior_rate",
        "five_year_recent_limit_up_count",
        "five_year_streak_runup",
    ):
        if name in frame.columns:
            values = pd.to_numeric(frame[name], errors="coerce")
            if values.notna().any() and values.nunique(dropna=True) > 1:
                return values
    return pd.Series(np.nan, index=frame.index, dtype=float)


def _ndcg_against_group_ideal(
    ranked_truth: np.ndarray,
    group_truth: np.ndarray,
    limit: int,
) -> float:
    ranked_truth = np.asarray(ranked_truth, dtype=float)[:limit]
    group_truth = np.asarray(group_truth, dtype=float)
    if not len(ranked_truth):
        return float("nan")
    discounts = 1.0 / np.log2(np.arange(len(ranked_truth), dtype=float) + 2.0)
    dcg = float(np.sum(ranked_truth * discounts))
    ideal = np.sort(group_truth)[::-1][: len(ranked_truth)]
    ideal_dcg = float(np.sum(ideal * discounts))
    return dcg / ideal_dcg if ideal_dcg > 0 else 1.0


def _promotion_ranking_metrics(
    sample: pd.DataFrame,
    config: ThreeEngineConfig,
) -> dict[str, Any]:
    daily_rows: list[dict[str, Any]] = []
    for date, group in sample.groupby("signal_date", sort=True):
        truth = pd.to_numeric(group["promotion_hit"], errors="coerce")
        valid = truth.notna()
        group = group.loc[valid].copy()
        if group.empty:
            continue
        group["_truth"] = truth.loc[valid].astype(float)
        candidate = group.sort_values(
            ["promotion_rank", "ts_code"], kind="stable"
        )
        baseline_score = _baseline_rank_score(group)
        baseline = group.assign(_baseline_score=baseline_score).sort_values(
            ["_baseline_score", "ts_code"],
            ascending=[False, True],
            kind="stable",
            na_position="last",
        )
        top1_candidate = float(candidate.head(1)["_truth"].mean())
        top1_baseline = float(baseline.head(1)["_truth"].mean())
        top3_candidate = float(candidate.head(3)["_truth"].mean())
        top3_baseline = float(baseline.head(3)["_truth"].mean())
        candidate_truth = candidate.head(config.top_n)["_truth"].to_numpy(dtype=float)
        baseline_truth = baseline.head(config.top_n)["_truth"].to_numpy(dtype=float)
        group_truth = group["_truth"].to_numpy(dtype=float)
        daily_rows.append(
            {
                "signal_date": str(date),
                "top1_candidate": top1_candidate,
                "top1_baseline": top1_baseline,
                "top1_lift": top1_candidate - top1_baseline,
                "top3_candidate": top3_candidate,
                "top3_baseline": top3_baseline,
                "top3_lift": top3_candidate - top3_baseline,
                "ndcg_candidate": _ndcg_against_group_ideal(
                    candidate_truth, group_truth, config.top_n
                ),
                "ndcg_baseline": _ndcg_against_group_ideal(
                    baseline_truth, group_truth, config.top_n
                ),
            }
        )
    daily = pd.DataFrame(daily_rows)
    if daily.empty:
        return {"dates": 0, "ranking_lift": None, "bootstrap": {}}
    daily["ndcg_lift"] = daily["ndcg_candidate"] - daily["ndcg_baseline"]
    return {
        "dates": int(len(daily)),
        "top1_hit_rate": float(daily["top1_candidate"].mean()),
        "baseline_top1_hit_rate": float(daily["top1_baseline"].mean()),
        "top1_lift": float(daily["top1_lift"].mean()),
        "top3_row_hit_rate": float(daily["top3_candidate"].mean()),
        "baseline_top3_row_hit_rate": float(daily["top3_baseline"].mean()),
        "top3_lift": float(daily["top3_lift"].mean()),
        "ndcg_at_10": float(daily["ndcg_candidate"].mean()),
        "baseline_ndcg_at_10": float(daily["ndcg_baseline"].mean()),
        "ranking_lift": float(daily["ndcg_lift"].mean()),
        "bootstrap": _block_bootstrap_mean_ci(
            daily.set_index("signal_date")["ndcg_lift"],
            config,
            seed_offset=11,
        ),
    }


def _outcome_ranking_metrics(
    sample: pd.DataFrame,
    spec: HeadSpec,
    config: ThreeEngineConfig,
) -> dict[str, Any]:
    daily_rows: list[dict[str, Any]] = []
    for date, group in sample.groupby("signal_date", sort=True):
        truth = pd.to_numeric(group[spec.target], errors="coerce")
        valid = truth.notna()
        if spec.requires_market_fill:
            valid &= pd.to_numeric(
                group.get("market_fill"), errors="coerce"
            ).eq(1)
        group = group.loc[valid].copy()
        if group.empty:
            continue
        group["_truth"] = truth.loc[valid].astype(float)
        ordered = group.sort_values([spec.rank_column, "ts_code"], kind="stable")
        rank1 = float(ordered.head(1)["_truth"].mean())
        rank3 = float(ordered.head(3)["_truth"].mean())
        pool = float(group["_truth"].mean())
        if spec.name == "big_loss":
            rank1_lift = pool - rank1
            rank3_lift = pool - rank3
        else:
            rank1_lift = rank1 - pool
            rank3_lift = rank3 - pool
        daily_rows.append(
            {
                "signal_date": str(date),
                "rank1_rate": rank1,
                "rank3_rate": rank3,
                "pool_rate": pool,
                "rank1_lift": rank1_lift,
                "rank3_lift": rank3_lift,
            }
        )
    daily = pd.DataFrame(daily_rows)
    if daily.empty:
        return {"dates": 0, "ranking_lift": None, "bootstrap": {}}
    return {
        "dates": int(len(daily)),
        "rank1_target_rate": float(daily["rank1_rate"].mean()),
        "rank3_target_rate": float(daily["rank3_rate"].mean()),
        "pool_target_rate": float(daily["pool_rate"].mean()),
        "ranking_lift": float(daily["rank1_lift"].mean()),
        "rank3_lift": float(daily["rank3_lift"].mean()),
        "bootstrap": _block_bootstrap_mean_ci(
            daily.set_index("signal_date")["rank1_lift"],
            config,
            seed_offset=23 if spec.name == "big_loss" else 37,
        ),
    }


def _probability_validation(
    sample: pd.DataFrame,
    spec: HeadSpec,
    config: ThreeEngineConfig,
) -> dict[str, Any]:
    truth = pd.to_numeric(sample[spec.target], errors="coerce")
    probability = pd.to_numeric(sample[spec.probability_column], errors="coerce")
    baseline = pd.to_numeric(
        sample[f"{spec.name}_baseline_probability"], errors="coerce"
    )
    valid = truth.notna() & probability.notna() & baseline.notna()
    if spec.requires_market_fill:
        valid &= pd.to_numeric(sample.get("market_fill"), errors="coerce").eq(1)
    evaluated = sample.loc[valid].copy()
    if evaluated.empty:
        return {
            "rows": 0,
            "dates": 0,
            "brier": None,
            "baseline_brier": None,
            "brier_improvement": None,
            "ece": None,
            "auc": None,
            "bootstrap": {},
        }
    y = truth.loc[valid].to_numpy(dtype=float)
    p = probability.loc[valid].to_numpy(dtype=float)
    base = baseline.loc[valid].to_numpy(dtype=float)
    weights = date_balanced_weights(evaluated)
    brier = _weighted_brier(p, y, weights)
    baseline_brier = _weighted_brier(base, y, weights)
    daily = pd.DataFrame(
        {
            "signal_date": evaluated["signal_date"].astype(str).to_numpy(),
            "improvement": (base - y) ** 2 - (p - y) ** 2,
        }
    ).groupby("signal_date")["improvement"].mean()
    auc = float("nan")
    if len(np.unique(y)) >= 2:
        try:
            auc = float(roc_auc_score(y, p, sample_weight=weights))
        except ValueError:
            auc = float("nan")
    return {
        "rows": int(len(evaluated)),
        "dates": int(evaluated["signal_date"].astype(str).nunique()),
        "positive_rate": float(np.average(y, weights=weights)),
        "brier": _safe_float(brier),
        "baseline_brier": _safe_float(baseline_brier),
        "brier_improvement": _safe_float(baseline_brier - brier),
        "relative_brier_improvement": _safe_float(
            (baseline_brier - brier) / baseline_brier
            if baseline_brier > 0
            else float("nan")
        ),
        "ece": _safe_float(_ece(p, y, weights)),
        "auc": _safe_float(auc),
        "bootstrap": _block_bootstrap_mean_ci(
            daily,
            config,
            seed_offset=3 if spec.name == "promotion" else 5 if spec.name == "big_loss" else 7,
        ),
    }


def _stage_breakdown(
    sample: pd.DataFrame,
    spec: HeadSpec,
    config: ThreeEngineConfig,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for stage in (2, 3):
        cohort = sample.loc[
            pd.to_numeric(sample["stage"], errors="coerce").round().eq(float(stage))
        ].copy()
        probability = _probability_validation(cohort, spec, config)
        ranking = (
            _promotion_ranking_metrics(cohort, config)
            if spec.name == "promotion"
            else _outcome_ranking_metrics(cohort, spec, config)
        )
        result[f"{stage}_to_{stage + 1}"] = {
            "rows": int(len(cohort)),
            "dates": int(cohort["signal_date"].astype(str).nunique()) if not cohort.empty else 0,
            "probability": probability,
            "ranking": ranking,
        }
    return result


def _chronological_stability(
    sample: pd.DataFrame,
    spec: HeadSpec,
    config: ThreeEngineConfig,
) -> list[dict[str, Any]]:
    dates = np.asarray(sorted(sample["signal_date"].astype(str).unique()))
    output: list[dict[str, Any]] = []
    for index, segment_dates in enumerate(np.array_split(dates, 3), start=1):
        if not len(segment_dates):
            continue
        segment = sample[sample["signal_date"].astype(str).isin(segment_dates)].copy()
        ranking = (
            _promotion_ranking_metrics(segment, config)
            if spec.name == "promotion"
            else _outcome_ranking_metrics(segment, spec, config)
        )
        output.append(
            {
                "segment": index,
                "start": str(segment_dates[0]),
                "end": str(segment_dates[-1]),
                "dates": int(len(segment_dates)),
                "ranking_lift": _safe_float(ranking.get("ranking_lift")),
            }
        )
    return output


def _validate_head(
    oof: pd.DataFrame,
    spec: HeadSpec,
    config: ThreeEngineConfig,
    *,
    history: pd.DataFrame,
    development_bundle: Optional[ProbabilityHeadBundle],
    production_bundle: Optional[ProbabilityHeadBundle],
    production_calibration_monotonicity: Mapping[str, Any],
    core_head: bool,
) -> dict[str, Any]:
    probability = _probability_validation(oof, spec, config) if not oof.empty else {}
    ranking = (
        _promotion_ranking_metrics(oof, config)
        if spec.name == "promotion" and not oof.empty
        else _outcome_ranking_metrics(oof, spec, config)
        if not oof.empty
        else {}
    )
    stages = _stage_breakdown(oof, spec, config) if not oof.empty else {}
    stability = _chronological_stability(oof, spec, config) if not oof.empty else []
    rank_variation = _rank_variation(oof, spec)
    holdout = (
        oof[
            oof.get(
                f"{spec.name}_oof_fold_kind",
                pd.Series("", index=oof.index),
            ).eq("final_independent_holdout")
        ].copy()
        if not oof.empty
        else pd.DataFrame()
    )
    holdout_probability = (
        _probability_validation(holdout, spec, config) if not holdout.empty else {}
    )
    holdout_ranking = (
        _promotion_ranking_metrics(holdout, config)
        if spec.name == "promotion" and not holdout.empty
        else _outcome_ranking_metrics(holdout, spec, config)
        if not holdout.empty
        else {}
    )
    bootstrap = probability.get("bootstrap") or {}
    ranking_bootstrap = ranking.get("bootstrap") or {}
    history_minimum_dates = (
        config.minimum_history_dates
        if spec.name == "promotion"
        else config.minimum_outcome_history_dates
    )
    history_minimum_rows = (
        config.minimum_history_rows
        if spec.name == "promotion"
        else config.minimum_outcome_history_rows
    )
    baseline_available = bool(
        spec.name != "promotion"
        or _baseline_rank_score(oof).notna().any()
    )
    stage_support = bool(stages) and all(
        int(item.get("probability", {}).get("rows") or 0)
        >= config.minimum_stage_oos_rows
        for item in stages.values()
    )
    stage_lifts = [
        _safe_float(item.get("ranking", {}).get("ranking_lift"))
        for item in stages.values()
    ]
    stage_nonnegative = bool(stage_lifts) and all(
        value is not None and value >= 0.0 for value in stage_lifts
    )
    stage_probability_skill = bool(stages) and all(
        (
            _safe_float(item.get("probability", {}).get("brier_improvement"))
            is not None
            and _safe_float(item.get("probability", {}).get("brier_improvement"))
            > config.minimum_brier_improvement
            and _safe_float(item.get("probability", {}).get("ece")) is not None
            and _safe_float(item.get("probability", {}).get("ece"))
            <= config.maximum_ece
            and _safe_float(item.get("probability", {}).get("auc")) is not None
            and _safe_float(item.get("probability", {}).get("auc"))
            > config.minimum_auc
        )
        for item in stages.values()
    )
    stability_lifts = [_safe_float(item.get("ranking_lift")) for item in stability]
    chronological_nonnegative = bool(stability_lifts) and all(
        value is not None and value >= 0.0 for value in stability_lifts
    )
    holdout_calendar_dates = int(
        holdout["signal_date"].astype(str).nunique()
    ) if not holdout.empty else 0
    pre_holdout_selection = (
        development_bundle.selection_metrics if development_bundle else {}
    )
    pre_holdout_ranking = pre_holdout_selection.get("ranking", {})
    pre_holdout_composite = _safe_float(
        pre_holdout_ranking.get("composite_lift")
    )
    brier_improvement = _safe_float(probability.get("brier_improvement"))
    brier_ci_low = _safe_float(bootstrap.get("ci95_low"))
    ece = _safe_float(probability.get("ece"))
    auc = _safe_float(probability.get("auc"))
    ranking_lift = _safe_float(ranking.get("ranking_lift"))
    ranking_ci_low = _safe_float(ranking_bootstrap.get("ci95_low"))
    holdout_bootstrap = holdout_probability.get("bootstrap") or {}
    holdout_ranking_bootstrap = holdout_ranking.get("bootstrap") or {}
    holdout_brier_improvement = _safe_float(
        holdout_probability.get("brier_improvement")
    )
    holdout_brier_ci_low = _safe_float(holdout_bootstrap.get("ci95_low"))
    holdout_ece = _safe_float(holdout_probability.get("ece"))
    holdout_auc = _safe_float(holdout_probability.get("auc"))
    holdout_ranking_lift = _safe_float(holdout_ranking.get("ranking_lift"))
    holdout_ranking_ci_low = _safe_float(
        holdout_ranking_bootstrap.get("ci95_low")
    )
    fold_columns = [
        f"{spec.name}_oof_fold",
        f"{spec.name}_oof_model_kind",
        f"{spec.name}_oof_calibration",
        f"{spec.name}_oof_selection_eligible",
        f"{spec.name}_oof_selection_composite_lift",
    ]
    available_fold_columns = [column for column in fold_columns if column in oof.columns]
    fold_audit = (
        oof[available_fold_columns]
        .drop_duplicates(subset=[f"{spec.name}_oof_fold"])
        .sort_values(f"{spec.name}_oof_fold", kind="stable")
        .to_dict(orient="records")
        if available_fold_columns and f"{spec.name}_oof_fold" in available_fold_columns
        else []
    )
    checks = {
        "nonconstant_production_model": bool(
            production_bundle is not None
            and monotonicity_evidence_is_valid(
                production_calibration_monotonicity,
                expected_method=production_bundle.calibration_method,
                require_nonconstant=True,
            )
            and production_calibration_monotonicity.get(
                "independent_production_rank_audit", {}
            ).get("valid") is True
        ),
        "nonconstant_oof_rank_scores": float(
            rank_variation.get("nonconstant_date_fraction") or 0.0
        )
        >= 0.90,
        "pre_holdout_selection_candidate_eligible": bool(
            pre_holdout_selection.get("chosen_candidate_eligible")
        ),
        "pre_holdout_selection_ranking_positive": bool(
            pre_holdout_composite is not None and pre_holdout_composite > 0.0
        ),
        "history_dates": int(history["signal_date"].astype(str).nunique())
        >= history_minimum_dates,
        "history_rows": int(len(history)) >= history_minimum_rows,
        "oos_dates": int(probability.get("dates") or 0) >= config.minimum_oos_dates,
        "oos_rows": int(probability.get("rows") or 0) >= config.minimum_oos_rows,
        "final_holdout_calendar_dates": holdout_calendar_dates
        >= config.final_holdout_dates,
        "ranking_baseline_available": baseline_available,
        "brier_improvement_positive": bool(
            brier_improvement is not None
            and brier_improvement > config.minimum_brier_improvement
        ),
        "brier_bootstrap_lower_positive": bool(
            brier_ci_low is not None and brier_ci_low > 0.0
        ),
        "ece_at_most_8pct": bool(ece is not None and ece <= config.maximum_ece),
        "auc_above_floor": bool(auc is not None and auc > config.minimum_auc),
        "ranking_lift_positive": bool(
            ranking_lift is not None and ranking_lift > 0.0
        ),
        "ranking_bootstrap_lower_positive": bool(
            ranking_ci_low is not None and ranking_ci_low > 0.0
        ),
        "stage_support": stage_support,
        "stage_probability_skill_and_calibration": stage_probability_skill,
        "stage_ranking_nonnegative": stage_nonnegative,
        "chronological_ranking_nonnegative": chronological_nonnegative,
        "holdout_brier_improvement_positive": bool(
            holdout_brier_improvement is not None
            and holdout_brier_improvement > 0.0
        ),
        "holdout_brier_bootstrap_lower_positive": bool(
            holdout_brier_ci_low is not None and holdout_brier_ci_low > 0.0
        ),
        "holdout_ece_at_most_8pct": bool(
            holdout_ece is not None and holdout_ece <= config.maximum_ece
        ),
        "holdout_auc_above_floor": bool(
            holdout_auc is not None and holdout_auc > config.minimum_auc
        ),
        "holdout_ranking_lift_positive": bool(
            holdout_ranking_lift is not None and holdout_ranking_lift > 0.0
        ),
        "holdout_ranking_bootstrap_lower_positive": bool(
            holdout_ranking_ci_low is not None and holdout_ranking_ci_low > 0.0
        ),
    }
    if tuple(checks) != THREE_ENGINE_VALIDATION_GATE_NAMES:
        raise RuntimeError("three-engine validation gate inventory drifted")
    promoted = bool(core_head and all(checks.values()))
    status = "READY" if promoted else "NOT_READY_VALIDATION_GATE"
    return {
        "schema_version": "decision_three_engine_head_validation_v1",
        "head": spec.name,
        "target": spec.target,
        "label_description": spec.label_description,
        "training_scope": (
            "complete_2_to_3_3_to_4_pool"
            if spec.name == "promotion"
            else "historical_promotion_oof_top10_market_fill_proxy_eq_1"
            if spec.name in {"big_loss", "profit"}
            else "historical_promotion_oof_top10_shadow_only"
        ),
        "execution_truth_claim": {
            "entry": "T public-market/exchange daily-bar open price proxy",
            "exit": "T+1 public-market/exchange daily-bar open price proxy",
            "market_fill": "public-market one-price-limit proxy",
            "actual_order_fill_observed": False,
            "actual_execution_claimed": False,
        },
        "probability": probability,
        "ranking": ranking,
        "stage_breakdown": stages,
        "chronological_stability": stability,
        "rank_variation": rank_variation,
        "pre_holdout_selection": _json_safe(pre_holdout_selection),
        "outer_fold_selection_audit": _json_safe(fold_audit),
        "final_independent_holdout": {
            "minimum_dates": config.final_holdout_dates,
            "calendar_dates": holdout_calendar_dates,
            "labeled_dates": int(holdout_probability.get("dates") or 0),
            "model_refit_within_holdout": False,
            "model_family_and_calibrator_locked_before_holdout": True,
            "probability": holdout_probability,
            "ranking": holdout_ranking,
        },
        "production": {
            "bundle_present": production_bundle is not None,
            "model_kind": production_bundle.model_kind if production_bundle else "",
            "calibration_method": (
                production_bundle.calibration_method if production_bundle else ""
            ),
            "training_rows": production_bundle.training_rows if production_bundle else 0,
            "training_dates": production_bundle.training_dates if production_bundle else 0,
            "model_fit_rows": production_bundle.model_fit_rows if production_bundle else 0,
            "calibration_rows": (
                production_bundle.calibration_rows if production_bundle else 0
            ),
            "selection_rows": (
                production_bundle.selection_rows if production_bundle else 0
            ),
            "trained_signal_start": (
                production_bundle.trained_signal_start if production_bundle else ""
            ),
            "trained_signal_end": (
                production_bundle.trained_signal_end if production_bundle else ""
            ),
            "constant_rank_forbidden": True,
            "calibration_monotonicity": _json_safe(
                production_calibration_monotonicity
            ),
            "calibration_monotonicity_valid": bool(
                production_bundle is not None
                and monotonicity_evidence_is_valid(
                    production_calibration_monotonicity,
                    expected_method=production_bundle.calibration_method,
                    require_nonconstant=True,
                )
            ),
            "independent_rank_audit": _json_safe(
                production_calibration_monotonicity.get(
                    "independent_production_rank_audit", {}
                )
            ),
            "independent_rank_audit_valid": bool(
                production_calibration_monotonicity.get(
                    "independent_production_rank_audit", {}
                ).get("valid") is True
            ),
            "post_gate_locked_family_refit": bool(
                production_bundle
                and production_bundle.selection_metrics.get("release_refit") is True
            ),
        },
        "gate_checks": checks,
        "gate_failures": [name for name, passed in checks.items() if not passed],
        "promoted": promoted,
        "status": status,
    }


def _merge_outcome_oof(
    top10: pd.DataFrame,
    oof: pd.DataFrame,
    spec: HeadSpec,
) -> pd.DataFrame:
    if top10.empty:
        return top10.copy()
    output = top10.copy()
    if oof.empty:
        for column in (
            spec.probability_column,
            spec.raw_score_column,
            spec.rank_column,
            f"{spec.name}_baseline_probability",
            f"{spec.name}_oof_fold",
            f"{spec.name}_oof_fold_kind",
            f"{spec.name}_oof_train_end",
            f"{spec.name}_oof_model_kind",
            f"{spec.name}_oof_calibration",
            f"{spec.name}_oof_selection_eligible",
            f"{spec.name}_oof_selection_composite_lift",
        ):
            output[column] = np.nan
        return output
    columns = [
        "signal_date",
        "ts_code",
        spec.probability_column,
        spec.raw_score_column,
        spec.rank_column,
        f"{spec.name}_baseline_probability",
        f"{spec.name}_oof_fold",
        f"{spec.name}_oof_fold_kind",
        f"{spec.name}_oof_train_end",
        f"{spec.name}_oof_model_kind",
        f"{spec.name}_oof_calibration",
        f"{spec.name}_oof_selection_eligible",
        f"{spec.name}_oof_selection_composite_lift",
    ]
    return output.merge(
        oof[columns],
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )


def _top10_integrity(oof_top10: pd.DataFrame, top_n: int) -> dict[str, Any]:
    if oof_top10.empty:
        return {
            "valid": False,
            "dates": 0,
            "rows": 0,
            "failures": ["empty_oof_top10"],
        }
    failures: list[str] = []
    for date, group in oof_top10.groupby("signal_date", sort=True):
        pool_size = int(pd.to_numeric(group["promotion_pool_size"], errors="coerce").iloc[0])
        expected = min(top_n, pool_size)
        ranks = sorted(
            pd.to_numeric(group["promotion_rank"], errors="coerce")
            .dropna()
            .astype(int)
            .tolist()
        )
        if len(group) != expected or ranks != list(range(1, expected + 1)):
            failures.append(f"noncontiguous_or_wrong_count:{date}")
        expected_hash = top10_members_sha256(str(date), group["ts_code"].astype(str))
        claims = set(group["top10_members_sha256"].dropna().astype(str))
        if claims != {expected_hash}:
            failures.append(f"member_hash_mismatch:{date}")
    return {
        "valid": not failures,
        "dates": int(oof_top10["signal_date"].astype(str).nunique()),
        "rows": int(len(oof_top10)),
        "failures": failures[:100],
        "dataset_sha256": _canonical_sha256(
            oof_top10[
                [
                    "signal_date",
                    "ts_code",
                    "promotion_rank",
                    "top10_members_sha256",
                ]
            ].to_dict(orient="records")
        ),
    }


def train_three_engine_models(
    ledger: pd.DataFrame,
    *,
    config: Optional[ThreeEngineConfig] = None,
) -> ThreeEngineTrainingResult:
    config = config or ThreeEngineConfig()
    history = normalize_supervised_ledger(ledger)
    feature_builder = resolve_d_close_feature_builder(history)
    forbidden_hits = sorted(
        set(feature_builder.numeric_columns).intersection(FORBIDDEN_FEATURE_COLUMNS)
    )
    if forbidden_hits:
        raise ValueError(f"cross-head/future features entered training: {forbidden_hits}")
    non_runtime_research_columns = sorted(
        name
        for name in history.columns
        if name.startswith("five_year_")
        and name not in RUNTIME_ALIGNED_FEATURE_COLUMNS
    )
    unusable_allowed_columns = sorted(
        name
        for name in history.columns
        if name in RUNTIME_ALIGNED_FEATURE_COLUMNS
        and not pd.to_numeric(history[name], errors="coerce").notna().any()
    )
    feature_nonnull_fraction = {
        name: float(pd.to_numeric(history[name], errors="coerce").notna().mean())
        for name in feature_builder.numeric_columns
    }

    promotion_spec = HEAD_SPECS["promotion"]
    promotion_oof, promotion_development = _walkforward_head(
        history,
        promotion_spec,
        feature_builder,
        config,
        warmup_dates=config.promotion_warmup_dates,
        freeze_top10=True,
    )
    promotion_production, promotion_production_monotonicity = (
        _locked_production_bundle(
            history,
            promotion_spec,
            feature_builder,
            config,
            promotion_development,
        )
    )
    promotion_validation = _validate_head(
        promotion_oof,
        promotion_spec,
        config,
        history=history,
        development_bundle=promotion_development,
        production_bundle=promotion_production,
        production_calibration_monotonicity=(
            promotion_production_monotonicity
        ),
        core_head=True,
    )
    oof_top10 = (
        promotion_oof[promotion_oof["top10_selected"].eq(1)].copy()
        if not promotion_oof.empty
        else history.head(0).copy()
    )
    if not oof_top10.empty:
        oof_top10 = oof_top10.sort_values(
            ["signal_date", "promotion_rank", "ts_code"], kind="stable"
        ).reset_index(drop=True)

    head_results: dict[str, HeadTrainingResult] = {}
    for name in ("big_loss", "profit", "p_fill_shadow"):
        spec = HEAD_SPECS[name]
        oof, development = _walkforward_head(
            oof_top10,
            spec,
            feature_builder,
            config,
            warmup_dates=config.outcome_warmup_dates,
            freeze_top10=False,
        )
        production, production_monotonicity = _locked_production_bundle(
            oof_top10,
            spec,
            feature_builder,
            config,
            development,
        )
        validation = _validate_head(
            oof,
            spec,
            config,
            history=oof_top10,
            development_bundle=development,
            production_bundle=production,
            production_calibration_monotonicity=production_monotonicity,
            core_head=name in {"big_loss", "profit"},
        )
        if name == "p_fill_shadow":
            validation["promoted"] = False
            validation["status"] = (
                "SHADOW_READY"
                if all(validation.get("gate_checks", {}).values())
                else "SHADOW_NOT_READY"
            )
            validation["cannot_change_core_members_or_ranks"] = True
        head_results[name] = HeadTrainingResult(
            spec=spec,
            oof=oof,
            development_bundle=development,
            production_bundle=production,
            validation=validation,
        )
        oof_top10 = _merge_outcome_oof(oof_top10, oof, spec)

    top10_integrity = _top10_integrity(oof_top10, config.top_n)
    independence = {
        "feature_contract": THREE_ENGINE_FEATURE_CONTRACT,
        "runtime_feature_contract_version": RUNTIME_FEATURE_CONTRACT_VERSION,
        "shared_input_only": "same immutable D-close raw feature snapshot",
        "promotion_training_scope": "complete eligible 2-to-3/3-to-4 pool",
        "outcome_selection_scope": "historical promotion OOF Top10 only",
        "big_loss_and_profit_models_are_distinct_objects": bool(
            head_results["big_loss"].production_bundle is not None
            and head_results["profit"].production_bundle is not None
            and head_results["big_loss"].production_bundle.model
            is not head_results["profit"].production_bundle.model
            and head_results["big_loss"].production_bundle.calibrator
            is not head_results["profit"].production_bundle.calibrator
        ),
        "cross_head_output_features": forbidden_hits,
        "cross_head_output_features_absent": not forbidden_hits,
        "runtime_aligned_allowlist_only": set(feature_builder.numeric_columns)
        <= RUNTIME_ALIGNED_FEATURE_COLUMNS,
        "excluded_research_only_columns": non_runtime_research_columns,
        "p_fill_shadow_cannot_change_core_ranks": True,
    }
    heads_validation = {
        "promotion": promotion_validation,
        "big_loss": head_results["big_loss"].validation,
        "profit": head_results["profit"].validation,
        "p_fill_shadow": head_results["p_fill_shadow"].validation,
    }
    all_core_promoted = bool(
        top10_integrity["valid"]
        and independence["cross_head_output_features_absent"]
        and independence["runtime_aligned_allowlist_only"]
        and all(heads_validation[name].get("promoted") is True for name in CORE_HEADS)
    )
    validation = {
        "schema_version": THREE_ENGINE_VALIDATION_SCHEMA,
        "contract_version": THREE_ENGINE_CONTRACT_VERSION,
        "feature_contract": THREE_ENGINE_FEATURE_CONTRACT,
        "runtime_feature_contract_version": RUNTIME_FEATURE_CONTRACT_VERSION,
        "configuration": _json_safe(asdict(config)),
        "source": {
            "rows": int(len(history)),
            "dates": int(history["signal_date"].astype(str).nunique()),
            "start": str(history["signal_date"].min()),
            "end": str(history["signal_date"].max()),
            "feature_columns": list(feature_builder.feature_names),
            "feature_columns_sha256": _canonical_sha256(
                list(feature_builder.feature_names)
            ),
            "runtime_aligned_raw_feature_allowlist": sorted(
                RUNTIME_ALIGNED_FEATURE_COLUMNS
            ),
            "runtime_aligned_raw_feature_allowlist_sha256": _canonical_sha256(
                sorted(RUNTIME_ALIGNED_FEATURE_COLUMNS)
            ),
            "excluded_research_only_columns": non_runtime_research_columns,
            "unusable_all_missing_allowed_columns_excluded": unusable_allowed_columns,
            "feature_nonnull_fraction": feature_nonnull_fraction,
        },
        "label_contract": {
            "promotion": "T public-market/exchange daily-bar close limit-rule truth",
            "big_loss": "T-open to T+1-open proxy net return <= -3%, market_fill proxy = 1 only",
            "profit": "T-open to T+1-open proxy net return > 0, market_fill proxy = 1 only",
            "price_source": "public-market/exchange daily-bar open price proxy",
            "actual_order_fill_observed": False,
            "actual_execution_claimed": False,
            "profitability_guaranteed": False,
        },
        "heads": heads_validation,
        "oof_top10": top10_integrity,
        "independence": independence,
        "all_core_heads_promoted": all_core_promoted,
        "ready": all_core_promoted,
        "status": "READY" if all_core_promoted else "NOT_READY_VALIDATION_GATE",
    }
    return ThreeEngineTrainingResult(
        feature_builder=feature_builder,
        promotion=HeadTrainingResult(
            spec=promotion_spec,
            oof=promotion_oof,
            development_bundle=promotion_development,
            production_bundle=promotion_production,
            validation=promotion_validation,
        ),
        big_loss=head_results["big_loss"],
        profit=head_results["profit"],
        p_fill_shadow=head_results["p_fill_shadow"],
        oof_top10=oof_top10,
        validation=validation,
    )


def model_artifact_payload(
    result: ThreeEngineTrainingResult,
    head: str,
) -> dict[str, Any]:
    training = {
        "promotion": result.promotion,
        "big_loss": result.big_loss,
        "profit": result.profit,
        "p_fill_shadow": result.p_fill_shadow,
    }[head]
    bundle = training.production_bundle
    model_as_of_date = bundle.trained_signal_end if bundle else None
    model_version = (
        f"{THREE_ENGINE_SCHEMA_VERSION}:{head}:{model_as_of_date}:"
        f"{bundle.model_kind}:{bundle.calibration_method}"
        if bundle
        else None
    )
    return {
        "schema_version": THREE_ENGINE_SCHEMA_VERSION,
        "contract_version": THREE_ENGINE_CONTRACT_VERSION,
        "feature_contract": THREE_ENGINE_FEATURE_CONTRACT,
        "runtime_feature_contract_version": RUNTIME_FEATURE_CONTRACT_VERSION,
        "head": head,
        "target": training.spec.target,
        "label_description": training.spec.label_description,
        "promoted": training.validation.get("promoted") is True,
        "status": training.validation.get("status"),
        "production_bundle_present": bundle is not None,
        "model_version": model_version,
        "model_as_of_date": model_as_of_date,
        "feature_names": list(result.feature_builder.feature_names),
        "calibration_monotonicity": training.validation.get(
            "production", {}
        ).get("calibration_monotonicity", {}),
        "bundle": bundle,
        "validation": training.validation,
    }


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_source_file(
    value: Any,
    *,
    repository_root: Path,
    label: str,
) -> Path:
    relative = Path(str(value or ""))
    path = (
        relative.resolve()
        if relative.is_absolute()
        else (repository_root / relative).resolve()
    )
    try:
        path.relative_to(repository_root)
    except ValueError as exc:
        raise ThreeEngineArtifactError(
            f"three-engine {label} escaped the repository root"
        ) from exc
    if not path.is_file():
        raise ThreeEngineArtifactError(
            f"three-engine {label} is not a regular repository file"
        )
    return path


def _validate_runtime_feature_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ThreeEngineArtifactError(
            "three-engine runtime feature contract is missing"
        )
    contract = dict(value)
    if contract.get("version") != RUNTIME_FEATURE_CONTRACT_VERSION:
        raise ThreeEngineArtifactError(
            "three-engine runtime feature contract version drifted"
        )
    columns = contract.get("columns")
    if not isinstance(columns, list) or tuple(columns) != tuple(
        RUNTIME_ALIGNED_MARKET_FEATURES
    ):
        raise ThreeEngineArtifactError(
            "three-engine runtime feature column inventory drifted"
        )
    if contract.get("available_by_d_close") is not True:
        raise ThreeEngineArtifactError(
            "three-engine runtime features are not D-close safe"
        )
    if contract.get("future_columns_used") != []:
        raise ThreeEngineArtifactError(
            "three-engine runtime feature contract uses future columns"
        )
    return contract


def _load_hash_bound_runtime_prior_ledger(
    validation: Mapping[str, Any],
    *,
    repository_root: Path,
) -> tuple[Path, str, pd.DataFrame]:
    source = validation.get("source")
    if not isinstance(source, Mapping):
        raise ThreeEngineArtifactError("three-engine validation source is missing")

    ledger_path = _repository_source_file(
        source.get("ledger_path"),
        repository_root=repository_root,
        label="runtime ledger",
    )
    claimed_ledger_sha = str(source.get("ledger_sha256") or "").lower()
    if not _valid_sha256(claimed_ledger_sha):
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger validation SHA256 is invalid"
        )
    actual_ledger_sha = _file_sha256(ledger_path)
    if actual_ledger_sha != claimed_ledger_sha:
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger hash mismatch"
        )

    manifest_path = _repository_source_file(
        source.get("ledger_manifest_path"),
        repository_root=repository_root,
        label="runtime ledger manifest",
    )
    claimed_manifest_sha = str(
        source.get("ledger_manifest_sha256") or ""
    ).lower()
    if not _valid_sha256(claimed_manifest_sha):
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger manifest validation SHA256 is invalid"
        )
    actual_manifest_sha = _file_sha256(manifest_path)
    if actual_manifest_sha != claimed_manifest_sha:
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger manifest hash mismatch"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreeEngineArtifactError(
            "cannot read three-engine runtime ledger manifest"
        ) from exc
    if not isinstance(manifest, Mapping):
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger manifest is invalid"
        )
    if manifest.get("owner") != "njedu2023-prog/DC20" or manifest.get(
        "runtime_dependency_on_top10_decision"
    ) is not False:
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger ownership/isolation is invalid"
        )
    if str(manifest.get("ledger_sha256") or "").lower() != actual_ledger_sha:
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger disagrees with manifest SHA256"
        )
    manifest_ledger_path = str(manifest.get("ledger_path") or "")
    if manifest_ledger_path and Path(manifest_ledger_path).as_posix() != str(
        source.get("ledger_path") or ""
    ):
        raise ThreeEngineArtifactError(
            "three-engine runtime ledger path disagrees with manifest"
        )
    manifest_contract = _validate_runtime_feature_contract(
        manifest.get("runtime_feature_contract")
    )
    source_contract = _validate_runtime_feature_contract(
        source.get("runtime_feature_contract")
    )
    if source_contract != manifest_contract:
        raise ThreeEngineArtifactError(
            "three-engine runtime feature contract disagrees with manifest"
        )
    manifest_source = manifest.get("source")
    if not isinstance(manifest_source, Mapping) or manifest_source.get(
        "prior_grid_truth_cutoff_rule"
    ) != "strictly_before_signal_date":
        raise ThreeEngineArtifactError(
            "three-engine runtime prior cutoff contract is invalid"
        )

    try:
        prior_ledger = pd.read_csv(
            ledger_path,
            usecols=["signal_date", "stage", "board", "promotion_hit"],
            low_memory=False,
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ThreeEngineArtifactError(
            "cannot read hash-bound three-engine runtime prior ledger"
        ) from exc
    if prior_ledger.empty:
        raise ThreeEngineArtifactError(
            "hash-bound three-engine runtime prior ledger is empty"
        )
    return ledger_path, actual_ledger_sha, prior_ledger


def attach_runtime_promotion_priors(
    frame: pd.DataFrame,
    ledger: pd.DataFrame,
    *,
    signal_date: str | None = None,
) -> pd.DataFrame:
    """Overwrite the eight priors from atomic truth strictly before D."""

    output = frame.copy()
    if output.empty:
        for feature in RUNTIME_PROMOTION_PRIOR_FEATURES:
            if feature not in output.columns:
                output[feature] = pd.Series(dtype=float)
        return output
    date = _normal_date(
        signal_date
        or output.get("signal_date", pd.Series("", index=output.index)).iloc[0]
    )
    if not date:
        raise ValueError("runtime promotion-prior signal date is invalid")
    required_frame = {"stage", "board"}
    required_ledger = {
        "signal_date",
        "stage",
        "board",
        "promotion_hit",
    }
    if not required_frame.issubset(output.columns):
        raise ValueError("runtime D snapshot lacks stage/board for prior grid")
    missing_ledger = sorted(required_ledger - set(ledger.columns))
    if missing_ledger:
        raise ValueError(
            f"runtime promotion-prior ledger is missing: {missing_ledger}"
        )

    history = ledger[list(required_ledger)].copy()
    history["signal_date"] = history["signal_date"].map(_normal_date)
    history["stage"] = pd.to_numeric(history["stage"], errors="coerce").round()
    history["board"] = history["board"].fillna("").astype(str).str.upper()
    history["promotion_hit"] = pd.to_numeric(
        history["promotion_hit"], errors="coerce"
    )
    history = history[
        history["signal_date"].lt(date)
        & history["stage"].isin((2.0, 3.0))
        & history["board"].isin(("SH_MAIN", "SZ_MAIN"))
        & history["promotion_hit"].isin((0.0, 1.0))
    ].copy()
    source_dates = sorted(history["signal_date"].unique())
    global_samples = float(len(history))
    global_hits = float(history["promotion_hit"].sum())
    global_prior_rate = (global_hits + 1.0) / (global_samples + 2.0)
    stage = pd.to_numeric(output["stage"], errors="coerce").round()
    board = output["board"].fillna("").astype(str).str.upper()
    if not stage.isin((2.0, 3.0)).all() or not board.isin(
        ("SH_MAIN", "SZ_MAIN")
    ).all():
        raise ValueError("runtime D snapshot escaped prior-grid stage/board scope")

    values: dict[tuple[int, str], dict[str, float]] = {}
    for stage_value in (2, 3):
        stage_history = history[history["stage"].eq(float(stage_value))]
        stage_samples = float(len(stage_history))
        stage_hits = float(stage_history["promotion_hit"].sum())
        stage_prior_rate = (
            stage_hits + 20.0 * global_prior_rate
        ) / (stage_samples + 20.0)
        for board_value in ("SH_MAIN", "SZ_MAIN"):
            cohort = stage_history[stage_history["board"].eq(board_value)]
            cohort_samples = float(len(cohort))
            cohort_hits = float(cohort["promotion_hit"].sum())
            stage_board_rate = (
                cohort_hits + 20.0 * stage_prior_rate
            ) / (cohort_samples + 20.0)
            recent: dict[int, tuple[float, float]] = {}
            for window in (20, 60):
                window_dates = set(source_dates[-window:])
                window_rows = cohort[
                    cohort["signal_date"].isin(window_dates)
                ]
                recent[window] = (
                    float(len(window_rows)),
                    float(window_rows["promotion_hit"].sum()),
                )
            recent_20_samples, recent_20_hits = recent[20]
            recent_60_samples, recent_60_hits = recent[60]
            recent_20_rate = (
                recent_20_hits + 10.0 * stage_board_rate
            ) / (recent_20_samples + 10.0)
            recent_60_rate = (
                recent_60_hits + 15.0 * stage_board_rate
            ) / (recent_60_samples + 15.0)
            values[(stage_value, board_value)] = {
                "five_year_stage_board_prior_rate": stage_board_rate,
                "five_year_stage_prior_rate": stage_prior_rate,
                "five_year_recent_20d_rate": recent_20_rate,
                "five_year_recent_60d_rate": recent_60_rate,
                "five_year_prior_samples_log": math.log1p(cohort_samples),
                "five_year_recent_60d_samples_log": math.log1p(
                    recent_60_samples
                ),
                "five_year_regime_delta": recent_60_rate - stage_board_rate,
                "five_year_board_stage_delta": stage_board_rate - stage_prior_rate,
            }
    for index in output.index:
        cohort_values = values[(int(stage.loc[index]), str(board.loc[index]))]
        for feature in RUNTIME_PROMOTION_PRIOR_FEATURES:
            output.at[index, feature] = cohort_values[feature]
    return output


def _load_three_engine_artifacts(
    validation_path: str | Path,
    *,
    root: str | Path | None = None,
    legacy_ready_allowlist: Mapping[str, str],
) -> LoadedThreeEngineArtifacts:
    """Internal loader; callers choose either the strict or sealed wrapper."""

    validation_file = Path(validation_path).resolve()
    repository_root = (
        Path(root).resolve()
        if root is not None
        else validation_file.parents[2]
    )
    try:
        validation = json.loads(validation_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreeEngineArtifactError(
            f"cannot read three-engine validation manifest: {validation_file}"
        ) from exc
    if validation.get("schema_version") != THREE_ENGINE_VALIDATION_SCHEMA:
        raise ThreeEngineArtifactError("three-engine validation schema is invalid")
    if validation.get("contract_version") != THREE_ENGINE_CONTRACT_VERSION:
        raise ThreeEngineArtifactError("three-engine contract version is invalid")
    records = validation.get("artifacts")
    metadata_source = validation.get("model_metadata")
    heads = validation.get("heads")
    if not isinstance(records, Mapping) or not isinstance(metadata_source, Mapping):
        raise ThreeEngineArtifactError("three-engine artifact inventory is missing")
    if not isinstance(heads, Mapping):
        raise ThreeEngineArtifactError("three-engine head validation is missing")

    runtime_ledger_path, runtime_ledger_sha, runtime_prior_ledger = (
        _load_hash_bound_runtime_prior_ledger(
            validation,
            repository_root=repository_root,
        )
    )

    payloads: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    expected_features = validation.get("source", {}).get("feature_columns")
    for head in (*CORE_HEADS, "p_fill_shadow"):
        record = records.get(head)
        claimed = metadata_source.get(head)
        head_validation = heads.get(head)
        if not isinstance(record, Mapping) or not isinstance(claimed, Mapping):
            raise ThreeEngineArtifactError(f"{head} artifact metadata is missing")
        if not isinstance(head_validation, Mapping):
            raise ThreeEngineArtifactError(f"{head} validation metadata is missing")
        gate_checks = head_validation.get("gate_checks")
        if (
            not isinstance(gate_checks, Mapping)
            or not gate_checks
            or any(
                type(name) is not str
                or not name.strip()
                or type(passed) is not bool
                for name, passed in gate_checks.items()
            )
        ):
            raise ThreeEngineArtifactError(
                f"{head} validation gate_checks must be nonempty booleans"
            )
        if set(gate_checks) != set(THREE_ENGINE_VALIDATION_GATE_NAMES):
            raise ThreeEngineArtifactError(
                f"{head} validation gate inventory drifted"
            )
        expected_gate_failures = {
            name for name, passed in gate_checks.items() if passed is False
        }
        gate_failures = head_validation.get("gate_failures")
        if (
            not isinstance(gate_failures, list)
            or any(type(name) is not str for name in gate_failures)
            or len(gate_failures) != len(set(gate_failures))
            or set(gate_failures) != expected_gate_failures
        ):
            raise ThreeEngineArtifactError(
                f"{head} validation gate_failures disagree with gate_checks"
            )
        validation_gate_pass_count = sum(
            passed is True for passed in gate_checks.values()
        )
        validation_gate_total_count = len(gate_checks)
        validation_gate_score_pct = round(
            100.0
            * validation_gate_pass_count
            / validation_gate_total_count,
            1,
        )
        relative = Path(str(record.get("path") or ""))
        artifact_path = (
            relative.resolve()
            if relative.is_absolute()
            else (repository_root / relative).resolve()
        )
        try:
            artifact_path.relative_to(repository_root)
        except ValueError as exc:
            raise ThreeEngineArtifactError(
                f"{head} artifact escaped the repository root"
            ) from exc
        claimed_sha256 = str(record.get("sha256") or "").lower()
        if not artifact_path.is_file() or not _valid_sha256(claimed_sha256):
            raise ThreeEngineArtifactError(f"{head} artifact path/hash is invalid")
        actual_sha256 = _file_sha256(artifact_path)
        if actual_sha256 != claimed_sha256:
            raise ThreeEngineArtifactError(f"{head} artifact hash mismatch")
        try:
            payload = joblib.load(artifact_path)
        except Exception as exc:  # joblib exposes backend-specific exceptions
            raise ThreeEngineArtifactError(f"cannot load {head} artifact") from exc
        if not isinstance(payload, dict) or payload.get("head") != head:
            raise ThreeEngineArtifactError(f"{head} artifact payload is invalid")
        if payload.get("schema_version") != THREE_ENGINE_SCHEMA_VERSION:
            raise ThreeEngineArtifactError(f"{head} model schema is invalid")
        if payload.get("feature_contract") != THREE_ENGINE_FEATURE_CONTRACT:
            raise ThreeEngineArtifactError(f"{head} feature contract is invalid")
        if payload.get("runtime_feature_contract_version") != (
            RUNTIME_FEATURE_CONTRACT_VERSION
        ):
            raise ThreeEngineArtifactError(
                f"{head} runtime feature contract is invalid"
            )
        if expected_features and payload.get("feature_names") != expected_features:
            raise ThreeEngineArtifactError(f"{head} feature inventory drifted")
        if payload.get("status") != head_validation.get("status"):
            raise ThreeEngineArtifactError(f"{head} status disagrees with validation")
        if claimed.get("status") != head_validation.get("status"):
            raise ThreeEngineArtifactError(
                f"{head} metadata status disagrees with validation"
            )
        payload_promoted = payload.get("promoted")
        claimed_promoted = claimed.get("promoted")
        validation_promoted = head_validation.get("promoted")
        if (
            type(payload_promoted) is not bool
            or type(claimed_promoted) is not bool
            or type(validation_promoted) is not bool
            or not (payload_promoted == claimed_promoted == validation_promoted)
        ):
            raise ThreeEngineArtifactError(
                f"{head} promotion state disagrees with validation"
            )
        all_validation_gates_passed = not expected_gate_failures
        validation_status = head_validation.get("status")
        if head in CORE_HEADS:
            if (
                validation_promoted is not all_validation_gates_passed
                or validation_status
                != (
                    "READY"
                    if all_validation_gates_passed
                    else "NOT_READY_VALIDATION_GATE"
                )
            ):
                raise ThreeEngineArtifactError(
                    f"{head} release state disagrees with validation gates"
                )
        elif (
            validation_promoted is not False
            or (
                all_validation_gates_passed
                and validation_status != "SHADOW_READY"
            )
            or (
                not all_validation_gates_passed
                and not str(validation_status or "").startswith(
                    "SHADOW_NOT_READY"
                )
            )
        ):
            raise ThreeEngineArtifactError(
                f"{head} shadow state disagrees with validation gates"
            )
        if str(claimed.get("artifact_sha256") or "").lower() != actual_sha256:
            raise ThreeEngineArtifactError(f"{head} provenance hash disagrees")
        for identity_key in ("model_version", "model_as_of_date"):
            if identity_key not in payload or identity_key not in claimed:
                raise ThreeEngineArtifactError(
                    f"{head} model provenance key is missing: {identity_key}"
                )
        if payload.get("model_version") != claimed.get("model_version"):
            raise ThreeEngineArtifactError(f"{head} model version disagrees")
        if payload.get("model_as_of_date") != claimed.get("model_as_of_date"):
            raise ThreeEngineArtifactError(f"{head} model as-of date disagrees")
        monotonicity = payload.get("calibration_monotonicity")
        bundle = payload.get("bundle")
        validation_production = head_validation.get("production", {})
        actual_bundle_present = isinstance(bundle, ProbabilityHeadBundle)
        if bundle is not None and not actual_bundle_present:
            raise ThreeEngineArtifactError(
                f"{head} artifact bundle has an invalid type"
            )
        legacy_presence_contract = bool(legacy_ready_allowlist)
        if not legacy_presence_contract:
            payload_bundle_present = payload.get("production_bundle_present")
            claimed_bundle_present = claimed.get("production_bundle_present")
            validation_bundle_present = (
                validation_production.get("bundle_present")
                if isinstance(validation_production, Mapping)
                else None
            )
            if (
                type(payload_bundle_present) is not bool
                or type(claimed_bundle_present) is not bool
                or type(validation_bundle_present) is not bool
                or not (
                    payload_bundle_present
                    == claimed_bundle_present
                    == validation_bundle_present
                    == actual_bundle_present
                )
            ):
                raise ThreeEngineArtifactError(
                    f"{head} production bundle presence disagrees"
                )
        model_version = payload.get("model_version")
        model_as_of_date = payload.get("model_as_of_date")
        status = str(payload.get("status") or "")
        if actual_bundle_present:
            if (
                not isinstance(model_version, str)
                or not model_version
                or not isinstance(model_as_of_date, str)
                or not _normal_date(model_as_of_date)
                or model_as_of_date != bundle.trained_signal_end
            ):
                raise ThreeEngineArtifactError(
                    f"{head} production bundle provenance is missing"
                )
            expected_version = (
                f"{THREE_ENGINE_SCHEMA_VERSION}:{head}:{model_as_of_date}:"
                f"{bundle.model_kind}:{bundle.calibration_method}"
            )
            if model_version != expected_version:
                raise ThreeEngineArtifactError(
                    f"{head} model version is not bound to its production bundle"
                )
        elif model_version is not None or model_as_of_date is not None:
            raise ThreeEngineArtifactError(
                f"{head} bundle-free artifact must have null model provenance"
            )
        if (
            not actual_bundle_present
            and isinstance(monotonicity, Mapping)
            and monotonicity.get("nondecreasing") is True
        ):
            raise ThreeEngineArtifactError(
                f"{head} has positive calibration evidence without a production bundle"
            )
        if head in CORE_HEADS:
            if status == "READY":
                if not payload_promoted or not actual_bundle_present:
                    raise ThreeEngineArtifactError(
                        f"{head} READY artifact has no promoted model"
                    )
            elif not status.startswith("NOT_READY_") or payload_promoted:
                raise ThreeEngineArtifactError(
                    f"{head} core status/promotion state is invalid"
                )
        elif (
            payload_promoted
            or (
                status != "SHADOW_READY"
                and not status.startswith("SHADOW_NOT_READY")
                and not status.startswith("NOT_READY_")
            )
        ):
            raise ThreeEngineArtifactError(
                f"{head} shadow status/promotion state is invalid"
            )
        if status in {"READY", "SHADOW_READY"} and not actual_bundle_present:
            raise ThreeEngineArtifactError(
                f"{head} ready artifact has no production bundle"
            )
        validation_monotonicity = validation_production.get(
            "calibration_monotonicity"
        )
        payload_evidence_present = "calibration_monotonicity" in payload
        validation_evidence_present = bool(
            isinstance(validation_production, Mapping)
            and "calibration_monotonicity" in validation_production
        )
        legacy_missing_evidence_allowed = bool(
            not payload_evidence_present
            and not validation_evidence_present
            and legacy_ready_allowlist.get(head) == actual_sha256
        )
        reported_legacy_ready_exception = bool(
            legacy_missing_evidence_allowed
            and head in CORE_HEADS
            and payload.get("status") == "READY"
        )
        if payload_evidence_present != validation_evidence_present or (
            payload_evidence_present
            and monotonicity != validation_monotonicity
        ):
            raise ThreeEngineArtifactError(
                f"{head} calibration evidence disagrees with validation"
            )
        if (
            head in CORE_HEADS
            and payload.get("status") == "READY"
            and not monotonicity
            and not legacy_missing_evidence_allowed
        ):
            raise ThreeEngineArtifactError(
                f"{head} READY artifact calibration evidence is missing"
            )
        if monotonicity:
            expected_method = (
                bundle.calibration_method
                if isinstance(bundle, ProbabilityHeadBundle)
                else None
            )
            monotonicity_valid = bool(
                expected_method
                and monotonicity_evidence_is_valid(
                    monotonicity,
                    expected_method=expected_method,
                    require_nonconstant=True,
                )
                and monotonicity.get(
                    "independent_production_rank_audit", {}
                ).get("valid") is True
            )
            if monotonicity_valid and isinstance(bundle, ProbabilityHeadBundle):
                support = monotonicity.get("raw_support", {})
                recomputed = calibrator_monotonicity_evidence(
                    bundle.calibrator,
                    [support.get("minimum"), support.get("maximum")],
                )
                monotonicity_valid = monotonicity_evidence_is_valid(
                    recomputed,
                    expected_method=expected_method,
                    require_nonconstant=True,
                )
                bundle_claim = bundle.selection_metrics.get(
                    "calibration_monotonicity"
                )
                monotonicity_valid = bool(
                    monotonicity_valid
                    and bundle_claim == monotonicity
                    and monotonicity_evidence_is_valid(
                        bundle_claim,
                        expected_method=expected_method,
                        require_nonconstant=True,
                    )
                    and bundle_claim.get(
                        "independent_production_rank_audit", {}
                    ).get("valid")
                    is True
                )
            if actual_bundle_present and not monotonicity_valid:
                raise ThreeEngineArtifactError(
                    f"{head} production artifact calibration is not monotonic"
                )
        elif actual_bundle_present and not legacy_missing_evidence_allowed:
            raise ThreeEngineArtifactError(
                f"{head} production artifact calibration evidence is missing"
            )
        payloads[head] = payload
        metadata[head] = {
            "status": str(claimed.get("status") or "NOT_READY_MISSING_STATUS"),
            "version": str(claimed.get("model_version") or ""),
            "as_of_date": _normal_date(claimed.get("model_as_of_date")),
            "artifact_sha256": actual_sha256,
            "path": artifact_path.as_posix(),
            # Strictly derived from the validated bool map above.  Claimed
            # summaries in artifacts, manifests, or inference rows are never
            # accepted as the source of this display metric.
            "validation_gate_pass_count": validation_gate_pass_count,
            "validation_gate_total_count": validation_gate_total_count,
            "validation_gate_score_pct": validation_gate_score_pct,
            "research_only_legacy_calibration_evidence_missing": (
                reported_legacy_ready_exception
            ),
        }
    return LoadedThreeEngineArtifacts(
        root=repository_root,
        validation_path=validation_file,
        validation=validation,
        payloads=payloads,
        metadata=metadata,
        runtime_ledger_path=runtime_ledger_path,
        runtime_ledger_sha256=runtime_ledger_sha,
        runtime_prior_ledger=runtime_prior_ledger,
    )


def load_three_engine_artifacts(
    validation_path: str | Path,
    *,
    root: str | Path | None = None,
) -> LoadedThreeEngineArtifacts:
    """Load current artifacts; every READY head must carry full audit evidence."""

    return _load_three_engine_artifacts(
        validation_path,
        root=root,
        legacy_ready_allowlist={},
    )


def load_research_only_legacy_three_engine_snapshot(
    validation_path: str | Path,
    *,
    root: str | Path,
) -> LoadedThreeEngineArtifacts:
    """Replay the one immutable pre-audit snapshot for research only.

    This is deliberately bound to one repository-relative path, one sealed
    validation SHA-256 and one promotion artifact SHA-256. It cannot authorize
    a current model, another historical snapshot, or an execution path.
    """

    repository_root = Path(root).resolve()
    validation_file = Path(validation_path).resolve()
    expected_file = (
        repository_root / _RESEARCH_ONLY_LEGACY_VALIDATION_PATH
    ).resolve()
    if validation_file != expected_file:
        raise ThreeEngineArtifactError(
            "research-only legacy validation path drifted"
        )
    if (
        not validation_file.is_file()
        or _file_sha256(validation_file)
        != _RESEARCH_ONLY_LEGACY_VALIDATION_SHA256
    ):
        raise ThreeEngineArtifactError(
            "research-only legacy validation SHA-256 drifted"
        )
    loaded = _load_three_engine_artifacts(
        validation_file,
        root=repository_root,
        legacy_ready_allowlist=_RESEARCH_ONLY_LEGACY_ARTIFACT_SHA256,
    )
    if (
        loaded.metadata["promotion"].get(
            "research_only_legacy_calibration_evidence_missing"
        )
        is not True
        or any(
            loaded.metadata[head].get(
                "research_only_legacy_calibration_evidence_missing"
            )
            is True
            for head in ("big_loss", "profit", "p_fill_shadow")
        )
    ):
        raise ThreeEngineArtifactError(
            "research-only legacy compatibility scope drifted"
        )
    return loaded


def _normalize_inference_pool(
    candidates: pd.DataFrame,
    signal_date: str,
) -> pd.DataFrame:
    date = _normal_date(signal_date)
    if not date:
        raise ValueError("three-engine inference signal_date is invalid")
    if candidates.empty:
        output = candidates.copy()
        output["signal_date"] = pd.Series(dtype=str)
        output["ts_code"] = pd.Series(dtype=str)
        output["stage"] = pd.Series(dtype=float)
        output["board"] = pd.Series(dtype=str)
        output["stage_transition"] = pd.Series(dtype=str)
        return output
    required = {"ts_code", "stage"}
    missing = sorted(required - set(candidates.columns))
    if missing:
        raise ValueError(f"three-engine inference pool is missing: {missing}")
    output = candidates.copy()
    if "signal_date" in output.columns:
        row_dates = output["signal_date"].map(_normal_date)
        if not row_dates.eq(date).all():
            raise ValueError("three-engine inference pool mixes signal dates")
    output["signal_date"] = date
    output["ts_code"] = output["ts_code"].map(_normal_code)
    stage_text = output["stage"].astype(str).str.replace("->", "→", regex=False)
    stage = pd.to_numeric(output["stage"], errors="coerce")
    stage = stage.where(
        stage.notna(),
        pd.to_numeric(stage_text.str.split("→").str[0], errors="coerce"),
    ).round()
    output["stage"] = stage
    if output["ts_code"].eq("").any() or not output["stage"].isin((2.0, 3.0)).all():
        raise ValueError("three-engine inference pool escaped 2-to-3/3-to-4 main scope")
    if output.duplicated(["signal_date", "ts_code"]).any():
        raise ValueError("three-engine inference pool contains duplicate keys")
    if "board" not in output.columns:
        output["board"] = np.where(
            output["ts_code"].str.endswith(".SH"), "SH_MAIN", "SZ_MAIN"
        )
    output["board"] = output["board"].fillna("").astype(str).str.upper()
    if not output["board"].isin(("SH_MAIN", "SZ_MAIN")).all():
        raise ValueError("three-engine inference pool contains non-main-board rows")
    output["stage_transition"] = output["stage"].astype(int).map(
        {2: "2→3", 3: "3→4"}
    )
    return output.sort_values(["ts_code"], kind="stable").reset_index(drop=True)


def _feature_snapshot_sha256(
    frame: pd.DataFrame,
    builder: Optional[DCloseFeatureBuilder],
) -> str:
    if frame.empty:
        records: list[dict[str, Any]] = []
    elif builder is None:
        records = [
            {
                "ts_code": str(row.ts_code),
                "stage": int(row.stage),
                "board": str(row.board),
            }
            for row in frame[["ts_code", "stage", "board"]].itertuples(index=False)
        ]
    else:
        features = builder.transform(frame)
        records = []
        for index, row in frame.iterrows():
            records.append(
                {
                    "ts_code": str(row["ts_code"]),
                    "values": {
                        name: _feature_snapshot_decimal(
                            features.at[index, name]
                        )
                        for name in builder.feature_names
                    },
                }
            )
    return _canonical_sha256(
        {
            "schema": FEATURE_SNAPSHOT_SCHEMA,
            "signal_date": str(frame["signal_date"].iloc[0]) if not frame.empty else "",
            "features": records,
        }
    )


def _runtime_metadata(
    loaded: LoadedThreeEngineArtifacts,
    signal_date: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for head in (*CORE_HEADS, "p_fill_shadow"):
        payload = loaded.payloads[head]
        source = loaded.metadata[head]
        status = str(source.get("status") or "NOT_READY_MISSING_STATUS")
        bundle = payload.get("bundle")
        as_of_date = _normal_date(source.get("as_of_date"))
        version = str(source.get("version") or "")
        artifact_sha256 = str(source.get("artifact_sha256") or "")
        ready_status = "READY" if head in CORE_HEADS else "SHADOW_READY"
        if status == ready_status and (
            not isinstance(bundle, ProbabilityHeadBundle)
            or not version
            or not as_of_date
            or as_of_date >= signal_date
            or not _valid_sha256(artifact_sha256)
        ):
            status = "NOT_READY_RUNTIME_PROVENANCE"
        output[head] = {
            "status": status,
            "version": version,
            "as_of_date": as_of_date,
            "artifact_sha256": artifact_sha256,
            "validation_gate_pass_count": source.get(
                "validation_gate_pass_count"
            ),
            "validation_gate_total_count": source.get(
                "validation_gate_total_count"
            ),
            "validation_gate_score_pct": source.get(
                "validation_gate_score_pct"
            ),
        }
    return output


def score_three_engine_snapshot(
    candidates: pd.DataFrame,
    loaded: LoadedThreeEngineArtifacts,
    *,
    signal_date: str,
    top_n: int = THREE_ENGINE_TOP_N,
) -> ThreeEngineSnapshotScore:
    """Score A on the full pool, then B/C only on A's frozen Top10.

    Artifact status is authoritative.  A failed head receives null probability
    and rank fields; it is never replaced by a constant or a code tie-break.
    """

    date = _normal_date(signal_date)
    base = _normalize_inference_pool(candidates, date)
    metadata = _runtime_metadata(loaded, date)
    prior_error = ""
    try:
        if loaded.runtime_prior_ledger.empty:
            raise ValueError("hash-bound runtime prior ledger is unavailable")
        base = attach_runtime_promotion_priors(
            base,
            loaded.runtime_prior_ledger,
            signal_date=date,
        )
    except (TypeError, ValueError, KeyError) as exc:
        prior_error = str(exc)
        if metadata["promotion"]["status"] == "READY":
            metadata["promotion"]["status"] = "NOT_READY_RUNTIME_PRIOR_LEDGER"
        for head in ("big_loss", "profit"):
            if metadata[head]["status"] == "READY":
                metadata[head]["status"] = "NOT_READY_NO_FROZEN_TOP10"
        if metadata["p_fill_shadow"]["status"] == "SHADOW_READY":
            metadata["p_fill_shadow"]["status"] = (
                "SHADOW_NOT_READY_RUNTIME_PRIOR_LEDGER"
            )
    promotion_pool_size = int(len(base))
    builder = next(
        (
            payload.get("bundle").feature_builder
            for payload in loaded.payloads.values()
            if isinstance(payload.get("bundle"), ProbabilityHeadBundle)
        ),
        None,
    )
    missing_features = (
        sorted(set(builder.numeric_columns) - set(base.columns))
        if builder is not None
        else []
    )
    all_missing_features = (
        sorted(
            name
            for name in builder.numeric_columns
            if name in base.columns
            and not pd.to_numeric(base[name], errors="coerce").notna().any()
        )
        if builder is not None and not base.empty
        else []
    )
    if missing_features or all_missing_features:
        failure = (
            "NOT_READY_MISSING_RUNTIME_FEATURES"
            if missing_features
            else "NOT_READY_EMPTY_RUNTIME_FEATURES"
        )
        for head in CORE_HEADS:
            if metadata[head]["status"] == "READY":
                metadata[head]["status"] = failure
        if metadata["p_fill_shadow"]["status"] == "SHADOW_READY":
            metadata["p_fill_shadow"]["status"] = "SHADOW_NOT_READY_RUNTIME_FEATURES"
        snapshot_sha256 = _feature_snapshot_sha256(base, None)
    else:
        snapshot_sha256 = _feature_snapshot_sha256(base, builder)
    output = base.copy()
    output["promotion_pool_size"] = promotion_pool_size
    output["three_rank_contract_version"] = THREE_ENGINE_CONTRACT_VERSION
    output["feature_snapshot_sha256"] = snapshot_sha256
    output["top10_selected"] = 0
    for head in CORE_HEADS:
        spec = HEAD_SPECS[head]
        output[spec.rank_column] = pd.Series(pd.NA, index=output.index, dtype="Int64")
        output[spec.probability_column] = np.nan
        output[spec.raw_score_column] = np.nan
    output["p_fill_shadow_probability"] = np.nan
    output["p_fill_shadow_score"] = np.nan
    output["p_fill_shadow_rank"] = pd.Series(pd.NA, index=output.index, dtype="Int64")

    selected_index: pd.Index = pd.Index([], dtype=int)
    if metadata["promotion"]["status"] == "READY" and not output.empty:
        try:
            scored = _score_bundle(
                base,
                loaded.payloads["promotion"]["bundle"],
                HEAD_SPECS["promotion"],
            )
            for column in (
                "promotion_rank",
                "predicted_promotion_probability",
                "promotion_rank_score",
            ):
                output[column] = scored[column]
            selected_count = min(max(1, int(top_n)), promotion_pool_size)
            selected_index = output.index[
                pd.to_numeric(output["promotion_rank"], errors="coerce").le(
                    selected_count
                )
            ]
            output.loc[selected_index, "top10_selected"] = 1
        except (TypeError, ValueError, KeyError):
            metadata["promotion"]["status"] = "NOT_READY_RUNTIME_SCORE"
            output["promotion_rank"] = pd.Series(
                pd.NA, index=output.index, dtype="Int64"
            )
            output["predicted_promotion_probability"] = np.nan
            output["promotion_rank_score"] = np.nan
            selected_index = pd.Index([], dtype=int)

    if metadata["promotion"]["status"] != "READY":
        for head in ("big_loss", "profit"):
            if metadata[head]["status"] == "READY":
                metadata[head]["status"] = "NOT_READY_NO_FROZEN_TOP10"

    selected_base = base.loc[selected_index].copy()
    for head in ("big_loss", "profit"):
        spec = HEAD_SPECS[head]
        if metadata[head]["status"] != "READY" or selected_base.empty:
            continue
        try:
            scored = _score_bundle(
                selected_base,
                loaded.payloads[head]["bundle"],
                spec,
            )
            for column in (
                spec.rank_column,
                spec.probability_column,
                spec.raw_score_column,
            ):
                output.loc[selected_index, column] = scored[column]
            output[spec.rank_column] = pd.to_numeric(
                output[spec.rank_column], errors="coerce"
            ).astype("Int64")
        except (TypeError, ValueError, KeyError):
            metadata[head]["status"] = "NOT_READY_RUNTIME_SCORE"
            output.loc[selected_index, spec.probability_column] = np.nan
            output.loc[selected_index, spec.raw_score_column] = np.nan
            output[spec.rank_column] = pd.Series(
                pd.NA, index=output.index, dtype="Int64"
            )

    shadow_status = metadata["p_fill_shadow"]["status"]
    if shadow_status == "SHADOW_READY" and not selected_base.empty:
        try:
            scored = _score_bundle(
                selected_base,
                loaded.payloads["p_fill_shadow"]["bundle"],
                HEAD_SPECS["p_fill_shadow"],
            )
            for column in (
                "p_fill_shadow_rank",
                "p_fill_shadow_probability",
                "p_fill_shadow_score",
            ):
                output.loc[selected_index, column] = scored[column]
            output["p_fill_shadow_rank"] = pd.to_numeric(
                output["p_fill_shadow_rank"], errors="coerce"
            ).astype("Int64")
        except (TypeError, ValueError, KeyError):
            metadata["p_fill_shadow"]["status"] = "SHADOW_NOT_READY_RUNTIME_SCORE"

    members_sha256 = top10_members_sha256(
        date,
        output.loc[selected_index, "ts_code"].astype(str),
    )
    output["top10_members_sha256"] = members_sha256
    output["p_fill_shadow_status"] = metadata["p_fill_shadow"]["status"]
    for head in CORE_HEADS:
        meta = metadata[head]
        output[f"{head}_model_status"] = meta["status"]
        output[f"{head}_model_version"] = meta["version"]
        output[f"{head}_model_as_of_date"] = meta["as_of_date"]
        output[f"{head}_model_artifact_sha256"] = meta["artifact_sha256"]
        output[f"{head}_validation_gate_pass_count"] = meta[
            "validation_gate_pass_count"
        ]
        output[f"{head}_validation_gate_total_count"] = meta[
            "validation_gate_total_count"
        ]
        output[f"{head}_validation_gate_score_pct"] = meta[
            "validation_gate_score_pct"
        ]
    shadow_meta = metadata["p_fill_shadow"]
    output["p_fill_shadow_model_version"] = shadow_meta["version"]
    output["p_fill_shadow_model_as_of_date"] = shadow_meta["as_of_date"]
    output["p_fill_shadow_model_artifact_sha256"] = shadow_meta[
        "artifact_sha256"
    ]
    output["p_fill_shadow_validation_gate_pass_count"] = shadow_meta[
        "validation_gate_pass_count"
    ]
    output["p_fill_shadow_validation_gate_total_count"] = shadow_meta[
        "validation_gate_total_count"
    ]
    output["p_fill_shadow_validation_gate_score_pct"] = shadow_meta[
        "validation_gate_score_pct"
    ]

    core_ready = sum(metadata[head]["status"] == "READY" for head in CORE_HEADS)
    status = (
        "READY"
        if core_ready == len(CORE_HEADS)
        else "PARTIAL_MODELS_NOT_READY"
        if metadata["promotion"]["status"] == "READY"
        else "NOT_READY_PROMOTION"
    )
    return ThreeEngineSnapshotScore(
        rows=output.sort_values(
            ["promotion_rank", "ts_code"], kind="stable", na_position="last"
        ).reset_index(drop=True),
        status=status,
        feature_snapshot_sha256=snapshot_sha256,
        top10_members_sha256=members_sha256,
        promotion_pool_size=promotion_pool_size,
        model_metadata={head: dict(metadata[head]) for head in CORE_HEADS},
        diagnostics={
            "feature_contract": THREE_ENGINE_FEATURE_CONTRACT,
            "runtime_feature_contract_version": RUNTIME_FEATURE_CONTRACT_VERSION,
            "missing_feature_columns": missing_features,
            "all_missing_feature_columns": all_missing_features,
            "runtime_feature_gate_passed": not (
                missing_features or all_missing_features
            ),
            "runtime_promotion_priors_attached": not prior_error,
            "runtime_promotion_prior_error": prior_error,
            "runtime_ledger_path": (
                loaded.runtime_ledger_path.as_posix()
                if loaded.runtime_ledger_path is not None
                else ""
            ),
            "runtime_ledger_sha256": loaded.runtime_ledger_sha256,
        },
    )


__all__ = [
    "CORE_HEADS",
    "FORBIDDEN_FEATURE_COLUMNS",
    "HEAD_SPECS",
    "HeadSpec",
    "HeadTrainingResult",
    "LoadedThreeEngineArtifacts",
    "ProbabilityHeadBundle",
    "PROMOTION_SOURCE_FEATURES",
    "RUNTIME_ALIGNED_D_FEATURES",
    "RUNTIME_ALIGNED_FEATURE_COLUMNS",
    "RUNTIME_ALIGNED_MARKET_FEATURES",
    "RUNTIME_ALIGNED_POOL_FEATURES",
    "RUNTIME_FEATURE_CONTRACT_VERSION",
    "RUNTIME_PROMOTION_PRIOR_FEATURES",
    "THREE_ENGINE_CONTRACT_VERSION",
    "THREE_ENGINE_FEATURE_CONTRACT",
    "THREE_ENGINE_SCHEMA_VERSION",
    "THREE_ENGINE_VALIDATION_GATE_NAMES",
    "THREE_ENGINE_VALIDATION_SCHEMA",
    "ThreeEngineConfig",
    "ThreeEngineArtifactError",
    "ThreeEngineSnapshotScore",
    "ThreeEngineTrainingResult",
    "attach_runtime_promotion_priors",
    "date_balanced_weights",
    "model_artifact_payload",
    "load_three_engine_artifacts",
    "normalize_supervised_ledger",
    "resolve_d_close_feature_builder",
    "top10_members_sha256",
    "train_three_engine_models",
    "score_three_engine_snapshot",
]
