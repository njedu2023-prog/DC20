from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_frame_fingerprint,
    canonical_policy_fingerprint,
    canonical_mapping_sha256,
    compose_artifact_fingerprint,
)
from .observation import OBSERVATION_TOP_N, rank_observation_rows


TRADE_SELECTOR_VERSION = "trade_selector_v2_nested_oos_top10_promotion_rank"
TRADE_SELECTOR_CANONICAL_V2 = (
    "trade_selector_canonical_v2_raw_execution_q8_fingerprint"
)
RUNTIME_CANONICAL_DECIMALS = 8
SELECTOR_EXECUTABLE_POLICY_THRESHOLDS = (
    "min_trade_score",
    "min_mean_return_lcb",
    "min_fill_probability",
    "max_big_loss_probability",
)
TRADE_SELECTOR_FEATURE_CONTRACT = (
    "OBSERVATION_TOP10_D_CLOSE_ONLY_PROMOTION_AND_CONDITIONAL_RETURN_NO_T_OR_T1_LEAKAGE"
)

# Every feature is frozen at D close. T/T+1 truth, market_fill, actual prices,
# realized return, and verification fields are intentionally excluded.
TRADE_SELECTOR_FEATURES: tuple[str, ...] = (
    "observation_rank",
    "stage_2to3",
    "stage_3to4",
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
    "d_turnover_rate",
    "d_volume_ratio",
    "d_amount_percentile",
    "relative_d_return",
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
    "same_industry_stage_count",
    "stage_pool_share",
    "stage_recent_promotion_rate",
    "market_sentiment_score",
    "market_sentiment_delta",
    "market_failed_limit_up_rate",
    "market_reseal_rate",
    "market_prev_limit_up_mean_return",
    "market_prev_limit_up_positive_rate",
    "market_focus_promotion_rate",
    "market_limit_up_industry_concentration",
    "market_limit_up_amount_top3_share",
    "market_amount_ratio_5d",
    "predicted_net_return",
    "predicted_mean_return_lcb",
    "predicted_profit_probability",
    "predicted_big_loss_probability",
    "predicted_continuation_limit_up_probability",
    "predicted_fill_probability",
    "predicted_exit_probability",
    "conservative_ev",
    "selection_score",
)

FORBIDDEN_TRADE_SELECTOR_FEATURES: frozenset[str] = frozenset(
    {
        "buy_date",
        "target_exit_date",
        "actual_exit_date",
        "buy_open",
        "auction_vwap",
        "auction_amount",
        "auction_truth_source",
        "exit_open",
        "actual_buy_gap",
        "gross_return",
        "net_return",
        "profit_hit",
        "big_loss_hit",
        "continuation_limit_up_hit",
        "exit_on_time",
        "market_fill",
        "fill_reason",
        "exit_reason",
        "actual_open_price",
        "actual_t_close",
        "actual_exit_price",
        "actual_net_return",
    }
)


@dataclass(frozen=True)
class TradeSelectorConfig:
    max_positions: int = 2
    warmup_dates: int = 180
    block_dates: int = 40
    embargo_dates: int = 2
    min_fit_dates: int = 100
    min_fit_buyable_rows: int = 160
    policy_fraction: float = 0.15
    calibration_fraction: float = 0.18
    min_policy_dates: int = 24
    min_policy_trades: int = 16
    min_policy_buyable_trades: int = 6
    min_policy_signal_date_ratio: float = 0.10
    max_policy_no_signal_streak: int = 24
    promotion_min_oos_dates: int = 250
    promotion_min_trades: int = 60
    promotion_min_buyable_trades: int = 24
    promotion_min_signal_date_ratio: float = 0.10
    promotion_max_no_signal_streak: int = 40
    promotion_min_policy_ready_ratio: float = 0.50
    promotion_min_bootstrap_probability: float = 0.90
    promotion_min_tail_mean_return: float = -0.10
    big_loss_threshold: float = -0.03
    tail_risk_weight_grid: tuple[float, ...] = (0.0, 0.25, 0.50, 0.75)
    random_state: int = 20260728


@dataclass
class TradeProbabilityCalibrator:
    method: str
    constant: float
    estimator: Optional[Any] = None

    def transform(self, raw_probability: Sequence[float] | np.ndarray) -> np.ndarray:
        raw = np.clip(np.asarray(raw_probability, dtype=float), 1e-6, 1.0 - 1e-6)
        if self.method == "constant" or self.estimator is None:
            if self.method == "identity":
                return raw
            return np.repeat(float(np.clip(self.constant, 0.0, 1.0)), len(raw))
        if self.method == "isotonic":
            return np.clip(self.estimator.predict(raw), 0.0, 1.0)
        design = np.log(raw / (1.0 - raw)).reshape(-1, 1)
        return np.clip(self.estimator.predict_proba(design)[:, 1], 0.0, 1.0)


@dataclass
class TradeSelectorBundle:
    return_model: Optional[Pipeline]
    return_constant: float
    fill_model: Optional[Pipeline]
    fill_constant: float
    big_loss_model: Optional[Pipeline]
    big_loss_constant: float
    promotion_model: Optional[Pipeline]
    promotion_constant: float
    fill_calibrator: TradeProbabilityCalibrator
    big_loss_calibrator: TradeProbabilityCalibrator
    promotion_calibrator: TradeProbabilityCalibrator
    mean_return_margin: float
    residual_q10: float
    policy: dict[str, Any]
    train_rows: int
    train_dates: int
    return_training_rows: int
    calibration_rows: int
    policy_rows: int
    return_selection: dict[str, Any]
    probability_selection: dict[str, Any]
    artifact_sha256: str
    artifact_v2_sha256: str = ""
    fingerprint_v2: dict[str, Any] = field(default_factory=dict)
    canonical_contract: dict[str, Any] = field(default_factory=dict)


def _runtime_canonical_contract() -> dict[str, Any]:
    return {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "layer": "trade_selector",
        "decimals": RUNTIME_CANONICAL_DECIMALS,
        "rounding": "decimal_string_half_even",
        "execution_mode": "raw_float64",
        "raw_execution_preserved": True,
    }


def _selector_executable_policy_projection(
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise ValueError("selector executable policy must be a mapping")
    version = policy.get("version")
    reason = policy.get("reason")
    ready = policy.get("ready")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("selector executable policy version is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("selector executable policy reason is required")
    if not isinstance(ready, bool):
        raise ValueError("selector executable policy ready must be boolean")

    def finite_number(name: str, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"selector executable policy {name} is invalid") from exc
        if not math.isfinite(number):
            raise ValueError(f"selector executable policy {name} is non-finite")
        return number

    positions_number = finite_number("max_positions", policy.get("max_positions"))
    if not positions_number.is_integer() or positions_number < 1:
        raise ValueError("selector executable policy max_positions must be a positive integer")
    tail_risk_weight = finite_number(
        "tail_risk_weight",
        policy.get("tail_risk_weight"),
    )
    raw_thresholds = policy.get("thresholds")
    if not isinstance(raw_thresholds, dict):
        raise ValueError("selector executable policy thresholds must be a mapping")
    thresholds: dict[str, float] = {}
    for name in SELECTOR_EXECUTABLE_POLICY_THRESHOLDS:
        if name not in raw_thresholds:
            raise ValueError(f"selector executable policy missing threshold: {name}")
        thresholds[name] = finite_number(name, raw_thresholds[name])
    return {
        "version": version,
        "ready": ready,
        "reason": reason,
        "max_positions": int(positions_number),
        "tail_risk_weight": tail_risk_weight,
        "thresholds": thresholds,
    }


def _selector_policy_fingerprint_v2(
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    projection = _selector_executable_policy_projection(policy)
    fingerprint = canonical_policy_fingerprint(
        projection,
        decimals=RUNTIME_CANONICAL_DECIMALS,
    )
    return {
        **fingerprint,
        # Consumers validate executable types directly.  The hash still uses
        # canonical decimal tokens internally, but the published projection
        # remains the strictly validated raw-float execution contract.
        "projection": projection,
    }


def _safe_metric(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _numeric(frame: pd.DataFrame, name: str, default: float = np.nan) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    values = {
        name: _numeric(frame, name)
        for name in TRADE_SELECTOR_FEATURES
        if name not in {"stage_2to3", "stage_3to4"}
    }
    stage = (
        frame["stage"].astype(str)
        if "stage" in frame.columns
        else pd.Series("", index=frame.index, dtype=str)
    )
    normalized = (
        stage.str.replace("进", "→", regex=False)
        .str.replace("->", "→", regex=False)
        .str.replace("-", "→", regex=False)
    )
    values["stage_2to3"] = normalized.str.contains(
        r"2\s*→\s*3",
        regex=True,
    ).astype(float)
    values["stage_3to4"] = normalized.str.contains(
        r"3\s*→\s*4",
        regex=True,
    ).astype(float)
    return pd.DataFrame(values, index=frame.index)[list(TRADE_SELECTOR_FEATURES)]


def _date_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty or "signal_date" not in frame.columns:
        return np.ones(len(frame), dtype=float)
    dates = frame["signal_date"].astype(str)
    counts = dates.value_counts()
    weights = dates.map(lambda value: 1.0 / max(1, int(counts.get(value, 1))))
    weights = weights.to_numpy(dtype=float)
    return weights / max(float(np.mean(weights)), 1e-9)


def _weighted_mean(values: pd.Series, weights: np.ndarray) -> float:
    clean = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(clean) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return float("nan")
    return float(np.average(clean[valid], weights=weights[valid]))


def _classifier_probability(
    model: Optional[Pipeline],
    constant: float,
    frame: pd.DataFrame,
) -> np.ndarray:
    if model is None:
        return np.repeat(float(np.clip(constant, 0.0, 1.0)), len(frame))
    return np.asarray(model.predict_proba(_features(frame))[:, 1], dtype=float)


def _return_prediction(bundle: TradeSelectorBundle, frame: pd.DataFrame) -> np.ndarray:
    if bundle.return_model is None:
        return np.repeat(float(bundle.return_constant), len(frame))
    return np.asarray(bundle.return_model.predict(_features(frame)), dtype=float)


def _fit_classifier(frame: pd.DataFrame, target: pd.Series) -> tuple[Optional[Pipeline], float]:
    y = pd.to_numeric(target, errors="coerce")
    valid = y.notna()
    sample = frame.loc[valid].copy()
    y = y.loc[valid].astype(int)
    weights = _date_weights(sample)
    constant = _weighted_mean(y, weights)
    if len(sample) < 40 or y.nunique() < 2:
        return None, float(constant if math.isfinite(constant) else 0.5)
    model = Pipeline(
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
                    random_state=20260728,
                ),
            ),
        ]
    )
    model.fit(_features(sample), y, model__sample_weight=weights)
    return model, float(constant)


def _brier(probability: np.ndarray, truth: pd.Series, weights: np.ndarray) -> float:
    y = pd.to_numeric(truth, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(probability) & np.isfinite(y) & np.isfinite(weights)
    if not valid.any():
        return float("nan")
    return float(np.average((probability[valid] - y[valid]) ** 2, weights=weights[valid]))


def _fit_calibrator(
    raw_probability: np.ndarray,
    truth: pd.Series,
    dates: pd.Series,
    constant: float,
) -> tuple[TradeProbabilityCalibrator, dict[str, Any]]:
    y = pd.to_numeric(truth, errors="coerce")
    valid = y.notna() & pd.Series(np.isfinite(raw_probability), index=y.index)
    raw = np.asarray(raw_probability, dtype=float)[valid.to_numpy()]
    y = y.loc[valid].astype(int)
    date_values = dates.loc[valid].astype(str)
    if len(y) < 40 or y.nunique() < 2:
        calibrator = TradeProbabilityCalibrator("constant", constant)
        return calibrator, {
            "method": "constant",
            "rows": int(len(y)),
            "brier": _safe_metric(
                _brier(
                    np.repeat(constant, len(y)),
                    y,
                    np.ones(len(y), dtype=float),
                )
            ),
            "beats_constant": False,
        }

    unique_dates = sorted(date_values.unique())
    split = max(1, int(math.floor(len(unique_dates) * 0.55)))
    fit_dates = set(unique_dates[:split])
    eval_dates = set(unique_dates[min(len(unique_dates), split + 1) :])
    fit_mask = date_values.isin(fit_dates).to_numpy()
    eval_mask = date_values.isin(eval_dates).to_numpy()
    if fit_mask.sum() < 20 or eval_mask.sum() < 10:
        fit_mask = np.arange(len(y)) < max(20, int(len(y) * 0.65))
        eval_mask = ~fit_mask
    fit_weights = _date_weights(
        pd.DataFrame({"signal_date": date_values.iloc[np.flatnonzero(fit_mask)]})
    )
    eval_weights = _date_weights(
        pd.DataFrame({"signal_date": date_values.iloc[np.flatnonzero(eval_mask)]})
    )
    candidates: list[tuple[str, TradeProbabilityCalibrator]] = [
        ("identity", TradeProbabilityCalibrator("identity", constant)),
        ("constant", TradeProbabilityCalibrator("constant", constant)),
    ]
    if np.unique(y.iloc[np.flatnonzero(fit_mask)]).size >= 2:
        design = np.log(
            np.clip(raw[fit_mask], 1e-6, 1.0 - 1e-6)
            / np.clip(1.0 - raw[fit_mask], 1e-6, 1.0)
        ).reshape(-1, 1)
        platt = LogisticRegression(
            C=1.0,
            max_iter=2_000,
            random_state=20260728,
        )
        platt.fit(
            design,
            y.iloc[np.flatnonzero(fit_mask)],
            sample_weight=fit_weights,
        )
        candidates.append(
            ("platt", TradeProbabilityCalibrator("platt", constant, platt))
        )
        if fit_mask.sum() >= 80 and np.unique(raw[fit_mask]).size >= 10:
            isotonic = IsotonicRegression(
                y_min=1e-6,
                y_max=1.0 - 1e-6,
                out_of_bounds="clip",
            )
            isotonic.fit(
                raw[fit_mask],
                y.iloc[np.flatnonzero(fit_mask)],
                sample_weight=fit_weights,
            )
            candidates.append(
                (
                    "isotonic",
                    TradeProbabilityCalibrator(
                        "isotonic",
                        constant,
                        isotonic,
                    ),
                )
            )
    scored: list[tuple[float, str, TradeProbabilityCalibrator]] = []
    eval_truth = y.iloc[np.flatnonzero(eval_mask)]
    for method, calibrator in candidates:
        score = _brier(
            calibrator.transform(raw[eval_mask]),
            eval_truth,
            eval_weights,
        )
        scored.append((score, method, calibrator))
    scored.sort(key=lambda item: (item[0], item[1]))
    best_brier, best_method, _ = scored[0]
    constant_brier = next(
        score for score, method, _ in scored if method == "constant"
    )

    # If calibration has no held-out skill, use an audited constant. This
    # prevents identical noisy probabilities from becoming a fake ranking edge.
    if not math.isfinite(best_brier) or best_brier >= constant_brier - 1e-4:
        best_method = "constant"
    if best_method == "constant":
        final = TradeProbabilityCalibrator("constant", constant)
    elif best_method == "identity":
        final = TradeProbabilityCalibrator("identity", constant)
    elif best_method == "platt":
        design = np.log(
            np.clip(raw, 1e-6, 1.0 - 1e-6)
            / np.clip(1.0 - raw, 1e-6, 1.0)
        ).reshape(-1, 1)
        estimator = LogisticRegression(
            C=1.0,
            max_iter=2_000,
            random_state=20260728,
        )
        estimator.fit(design, y, sample_weight=_date_weights(pd.DataFrame({"signal_date": date_values})))
        final = TradeProbabilityCalibrator("platt", constant, estimator)
    else:
        estimator = IsotonicRegression(
            y_min=1e-6,
            y_max=1.0 - 1e-6,
            out_of_bounds="clip",
        )
        estimator.fit(
            raw,
            y,
            sample_weight=_date_weights(pd.DataFrame({"signal_date": date_values})),
        )
        final = TradeProbabilityCalibrator("isotonic", constant, estimator)
    return final, {
        "method": best_method,
        "rows": int(len(y)),
        "held_out_brier": _safe_metric(best_brier),
        "constant_brier": _safe_metric(constant_brier),
        "beats_constant": bool(
            math.isfinite(best_brier)
            and best_brier < constant_brier - 1e-4
        ),
    }


def prepare_observation_top10(
    oos: pd.DataFrame,
    *,
    limit: int = OBSERVATION_TOP_N,
) -> pd.DataFrame:
    """Recreate the exact daily observation list shown by the Decision page."""
    if oos.empty or "signal_date" not in oos.columns:
        return pd.DataFrame()
    output: list[pd.DataFrame] = []
    for signal_date, group in oos.groupby(oos["signal_date"].astype(str), sort=True):
        source = group.copy().reset_index(drop=False).rename(columns={"index": "_source_index"})
        selected, pool_size = rank_observation_rows(
            source.to_dict(orient="records"),
            limit=limit,
        )
        if not selected:
            continue
        selected_frame = pd.DataFrame(selected)
        selected_frame["signal_date"] = str(signal_date)
        selected_frame["observation_pool_size"] = int(pool_size)
        output.append(selected_frame)
    if not output:
        return pd.DataFrame()
    result = pd.concat(output, ignore_index=True)
    return result.sort_values(
        ["signal_date", "observation_rank", "ts_code"],
        kind="stable",
    ).reset_index(drop=True)


def _split_dates(
    frame: pd.DataFrame,
    config: TradeSelectorConfig,
) -> tuple[list[str], list[str], list[str]]:
    dates = sorted(frame["signal_date"].astype(str).unique())
    policy_dates = max(
        config.min_policy_dates,
        int(math.ceil(len(dates) * config.policy_fraction)),
    )
    calibration_dates = max(
        18,
        int(math.ceil(len(dates) * config.calibration_fraction)),
    )
    policy_start = len(dates) - policy_dates
    calibration_end = policy_start - config.embargo_dates
    calibration_start = calibration_end - calibration_dates
    fit_end = calibration_start - config.embargo_dates
    if fit_end < config.min_fit_dates:
        return [], [], []
    return (
        dates[:fit_end],
        dates[calibration_start:calibration_end],
        dates[policy_start:],
    )


def _fit_return_model(
    fit: pd.DataFrame,
    calibration: pd.DataFrame,
    config: TradeSelectorConfig,
) -> tuple[Optional[Pipeline], float, dict[str, Any], float, float]:
    fit_buyable = fit.loc[_numeric(fit, "market_fill", 0).fillna(0).eq(1)].copy()
    calibration_buyable = calibration.loc[
        _numeric(calibration, "market_fill", 0).fillna(0).eq(1)
    ].copy()
    fit_target = _numeric(fit_buyable, "net_return")
    calibration_target = _numeric(calibration_buyable, "net_return")
    fit_valid = fit_target.notna()
    calibration_valid = calibration_target.notna()
    fit_buyable = fit_buyable.loc[fit_valid]
    fit_target = fit_target.loc[fit_valid]
    calibration_buyable = calibration_buyable.loc[calibration_valid]
    calibration_target = calibration_target.loc[calibration_valid]
    fit_weights = _date_weights(fit_buyable)
    calibration_weights = _date_weights(calibration_buyable)
    constant = _weighted_mean(fit_target, fit_weights)
    if not math.isfinite(constant):
        constant = 0.0
    if (
        len(fit_buyable) < config.min_fit_buyable_rows
        or len(calibration_buyable) < 30
    ):
        return None, constant, {
            "selected": "constant",
            "passed": False,
            "reason": "insufficient_market_buyable_rows",
            "fit_rows": int(len(fit_buyable)),
            "calibration_rows": int(len(calibration_buyable)),
        }, 0.01, -0.05

    candidates: list[tuple[str, Pipeline]] = [
        (
            "ridge",
            Pipeline(
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
                    ("model", Ridge(alpha=12.0)),
                ]
            ),
        ),
        (
            "hist_gradient_boosting",
            Pipeline(
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
                        HistGradientBoostingRegressor(
                            learning_rate=0.035,
                            max_iter=140,
                            max_leaf_nodes=7,
                            min_samples_leaf=28,
                            l2_regularization=5.0,
                            random_state=config.random_state,
                        ),
                    ),
                ]
            ),
        ),
    ]
    evaluations: list[dict[str, Any]] = []
    for name, model in candidates:
        model.fit(
            _features(fit_buyable),
            fit_target,
            model__sample_weight=fit_weights,
        )
        prediction = np.asarray(
            model.predict(_features(calibration_buyable)),
            dtype=float,
        )
        error = prediction - calibration_target.to_numpy(dtype=float)
        rmse = float(
            math.sqrt(np.average(error**2, weights=calibration_weights))
        )
        mae = float(np.average(np.abs(error), weights=calibration_weights))
        evaluations.append(
            {
                "name": name,
                "model": model,
                "rmse": rmse,
                "mae": mae,
                "prediction": prediction,
            }
        )
    constant_error = constant - calibration_target.to_numpy(dtype=float)
    constant_rmse = float(
        math.sqrt(np.average(constant_error**2, weights=calibration_weights))
    )
    evaluations.sort(key=lambda item: (item["rmse"], item["mae"], item["name"]))
    best = evaluations[0]
    improvement = (
        (constant_rmse - best["rmse"]) / constant_rmse
        if constant_rmse > 0
        else 0.0
    )
    passed = bool(improvement >= 0.01)
    selected_prediction = (
        best["prediction"]
        if passed
        else np.repeat(constant, len(calibration_target))
    )
    selected_rmse = best["rmse"] if passed else constant_rmse
    residual = calibration_target.to_numpy(dtype=float) - selected_prediction
    residual_std = float(
        np.sqrt(np.average(residual**2, weights=calibration_weights))
    )
    effective_dates = max(
        10,
        calibration_buyable["signal_date"].astype(str).nunique(),
    )
    mean_margin = max(
        0.0015,
        1.282 * residual_std / math.sqrt(effective_dates),
    )
    residual_q10 = float(np.quantile(residual, 0.10))
    selection = {
        "selected": best["name"] if passed else "constant",
        "rejected_candidate": None if passed else best["name"],
        "passed": passed,
        "reason": (
            "oos_rmse_skill_passed"
            if passed
            else "oos_rmse_skill_below_floor"
        ),
        "relative_rmse_improvement": _safe_metric(improvement),
        "rmse": _safe_metric(selected_rmse),
        "candidate_rmse": _safe_metric(best["rmse"]),
        "constant_rmse": _safe_metric(constant_rmse),
        "fit_rows": int(len(fit_buyable)),
        "calibration_rows": int(len(calibration_buyable)),
        "training_scope": "market_fill_eq_1_only",
    }
    return (
        best["model"] if passed else None,
        constant,
        selection,
        float(mean_margin),
        float(residual_q10),
    )


def _score_raw(frame: pd.DataFrame, bundle: TradeSelectorBundle) -> pd.DataFrame:
    out = frame.copy()
    conditional_return = _return_prediction(bundle, out)
    raw_fill = _classifier_probability(
        bundle.fill_model,
        bundle.fill_constant,
        out,
    )
    raw_big_loss = _classifier_probability(
        bundle.big_loss_model,
        bundle.big_loss_constant,
        out,
    )
    raw_promotion = _classifier_probability(
        bundle.promotion_model,
        bundle.promotion_constant,
        out,
    )
    fill_probability = bundle.fill_calibrator.transform(raw_fill)
    big_loss_probability = bundle.big_loss_calibrator.transform(raw_big_loss)
    promotion_probability = bundle.promotion_calibrator.transform(raw_promotion)
    mean_lcb = conditional_return - bundle.mean_return_margin
    outcome_q10 = conditional_return + bundle.residual_q10
    tail_loss_proxy = np.minimum(outcome_q10, 0.0) * big_loss_probability
    base_score = fill_probability * mean_lcb
    out["trade_predicted_conditional_net_return"] = conditional_return
    out["trade_predicted_mean_return_lcb"] = mean_lcb
    out["trade_predicted_fill_probability"] = fill_probability
    out["trade_predicted_big_loss_probability"] = big_loss_probability
    out["promotion_rank_score"] = raw_promotion
    out["predicted_promotion_probability"] = promotion_probability
    out["trade_predicted_outcome_q10"] = outcome_q10
    out["trade_tail_loss_proxy"] = tail_loss_proxy
    out["trade_base_score"] = base_score
    out["trade_score"] = base_score
    ordered = out.assign(
        _signal_date=out["signal_date"].astype(str),
        _promotion_score=raw_promotion,
        _big_loss_probability=big_loss_probability,
        _observation_rank=_numeric(
            out,
            "observation_rank",
            999999,
        ).fillna(999999),
    ).sort_values(
        [
            "_signal_date",
            "_promotion_score",
            "_big_loss_probability",
            "_observation_rank",
            "ts_code",
        ],
        ascending=[True, False, True, True, True],
        kind="stable",
    )
    out.loc[ordered.index, "promotion_rank"] = (
        ordered.groupby("_signal_date", sort=False).cumcount() + 1
    ).to_numpy()
    return out


def _rank_and_select(
    scored: pd.DataFrame,
    policy: dict[str, Any],
    *,
    policy_ready: bool,
) -> pd.DataFrame:
    out = scored.copy()
    tail_risk_weight = float(policy.get("tail_risk_weight") or 0.0)
    out["trade_tail_risk_weight"] = tail_risk_weight
    base_score = _numeric(out, "trade_base_score")
    if base_score.notna().sum() == 0:
        base_score = _numeric(out, "trade_score")
    out["trade_score"] = (
        base_score
        + tail_risk_weight
        * _numeric(out, "trade_predicted_fill_probability", 0.0).fillna(0.0)
        * _numeric(out, "trade_tail_loss_proxy", 0.0).fillna(0.0)
    )
    thresholds = dict(policy.get("thresholds") or {})
    max_positions = max(1, min(2, int(policy.get("max_positions") or 2)))
    min_score = float(thresholds.get("min_trade_score", -np.inf))
    min_fill = float(thresholds.get("min_fill_probability", 0.0))
    max_big_loss = float(thresholds.get("max_big_loss_probability", 1.0))
    min_lcb = float(thresholds.get("min_mean_return_lcb", -np.inf))
    out["trade_rank"] = np.nan
    out["trade_gate_pass"] = 0
    out["trade_shadow_selected"] = 0
    out["trade_selected"] = 0
    out["trade_model_reason"] = "below_learned_policy"
    ordered = out.assign(
        _signal_date=out["signal_date"].astype(str),
        _observation_rank=_numeric(
            out,
            "observation_rank",
            999999,
        ).fillna(999999),
        _big_loss=_numeric(
            out,
            "trade_predicted_big_loss_probability",
            1.0,
        ).fillna(1.0),
        _promotion_rank=_numeric(
            out,
            "promotion_rank",
            999999,
        ).fillna(999999),
        _score=_numeric(out, "trade_score", -np.inf).fillna(-np.inf),
    ).sort_values(
        [
            "_signal_date",
            "_score",
            "_big_loss",
            "_promotion_rank",
            "_observation_rank",
            "ts_code",
        ],
        ascending=[True, False, True, True, True, True],
        kind="stable",
    )
    out.loc[ordered.index, "trade_rank"] = (
        ordered.groupby("_signal_date", sort=False).cumcount() + 1
    ).to_numpy()
    qualifies = (
        _numeric(out, "trade_score", -np.inf).fillna(-np.inf).ge(min_score)
        & _numeric(
            out,
            "trade_predicted_fill_probability",
            0.0,
        ).fillna(0.0).ge(min_fill)
        & _numeric(
            out,
            "trade_predicted_big_loss_probability",
            1.0,
        ).fillna(1.0).le(max_big_loss)
        & _numeric(
            out,
            "trade_predicted_mean_return_lcb",
            -np.inf,
        ).fillna(-np.inf).ge(min_lcb)
    )
    eligible = (
        out.loc[qualifies]
        .assign(_signal_date=out.loc[qualifies, "signal_date"].astype(str))
        .sort_values(
            ["_signal_date", "trade_rank"],
            kind="stable",
        )
    )
    eligible = eligible.loc[
        eligible.groupby("_signal_date", sort=False).cumcount()
        < max_positions
    ]
    out.loc[eligible.index, "trade_gate_pass"] = 1
    out.loc[eligible.index, "trade_shadow_selected"] = 1
    if policy_ready:
        out.loc[eligible.index, "trade_selected"] = 1
        out.loc[eligible.index, "trade_model_reason"] = "learned_policy_pass"
    elif len(eligible):
        out.loc[eligible.index, "trade_model_reason"] = "shadow_policy_only"
    out["trade_selector_policy_ready"] = int(policy_ready)
    out["trade_selector_version"] = TRADE_SELECTOR_VERSION
    return out


def _no_signal_streak(dates: Sequence[str], signal_dates: set[str]) -> int:
    longest = 0
    current = 0
    for signal_date in dates:
        if signal_date in signal_dates:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _bootstrap_positive_probability(
    daily: pd.Series,
    *,
    block: int = 5,
    samples: int = 500,
) -> float:
    values = pd.to_numeric(daily, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) < block:
        return float("nan")
    rng = np.random.default_rng(20260728)
    starts = np.arange(0, max(1, len(values) - block + 1))
    blocks_needed = int(math.ceil(len(values) / block))
    means: list[float] = []
    for _ in range(samples):
        sample = np.concatenate(
            [
                values[start : start + block]
                for start in rng.choice(starts, blocks_needed, replace=True)
            ]
        )[: len(values)]
        means.append(float(np.mean(sample)))
    return float(np.mean(np.asarray(means) > 0.0))


def _return_metrics(
    selected: pd.DataFrame,
    dates: Sequence[str],
    *,
    cost_rate: float,
    market_buyable_only: bool,
) -> dict[str, Any]:
    sample = selected.copy()
    if "signal_date" not in sample.columns:
        sample["signal_date"] = pd.Series(dtype=str)
    signal_dates = set(sample["signal_date"].astype(str))
    if market_buyable_only:
        sample = sample.loc[
            _numeric(sample, "market_fill", 0).fillna(0).eq(1)
        ].copy()
    returns = _numeric(sample, "net_return").dropna()
    sample = sample.loc[returns.index].copy()
    sample["_return"] = returns
    daily = (
        sample.groupby(sample["signal_date"].astype(str))["_return"]
        .mean()
        .reindex(list(dates), fill_value=0.0)
    )
    nav = (1.0 + daily).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    positives = float(returns[returns > 0].sum())
    negatives = float(-returns[returns < 0].sum())
    tail_count = max(1, int(math.ceil(len(returns) * 0.10))) if len(returns) else 0
    continuation = _numeric(sample, "continuation_limit_up_hit").dropna()
    stage_breakdown: dict[str, Any] = {}
    if "stage" in sample.columns:
        for stage, group in sample.groupby("stage", dropna=False):
            group_returns = _numeric(group, "_return").dropna()
            stage_breakdown[str(stage)] = {
                "trades": int(len(group_returns)),
                "mean_net_return": _safe_metric(group_returns.mean()),
                "win_rate": _safe_metric((group_returns > 0).mean()),
                "continuation_hit_rate": _safe_metric(
                    _numeric(group, "continuation_limit_up_hit").mean()
                ),
            }
    return {
        "execution_mode": (
            "market_buyable_at_open_truth"
            if market_buyable_only
            else "forced_market_open_truth"
        ),
        "signals": int(len(selected)),
        "signal_dates": int(len(signal_dates)),
        "signal_date_ratio": _safe_metric(
            len(signal_dates) / len(dates) if dates else np.nan
        ),
        "max_no_signal_streak": int(_no_signal_streak(dates, signal_dates)),
        "filled_trades": int(len(returns)),
        "mean_trade_net_return": _safe_metric(returns.mean()),
        "median_trade_net_return": _safe_metric(returns.median()),
        "win_rate": _safe_metric((returns > 0).mean()),
        "continuation_hit_rate": _safe_metric(continuation.mean()),
        "realized_big_loss_rate": _safe_metric((returns <= -0.03).mean()),
        "tail_10pct_mean_return": _safe_metric(
            returns.nsmallest(tail_count).mean() if tail_count else np.nan
        ),
        "worst_trade_net_return": _safe_metric(returns.min()),
        "profit_factor": _safe_metric(
            positives / negatives if negatives > 0 else np.nan
        ),
        "mean_daily_return": _safe_metric(daily.mean()),
        "stress_2x_cost_mean_trade_return": _safe_metric(
            (returns - cost_rate).mean()
        ),
        "cumulative_return": _safe_metric(
            nav.iloc[-1] - 1.0 if len(nav) else np.nan
        ),
        "max_drawdown": _safe_metric(drawdown.min() if len(drawdown) else np.nan),
        "bootstrap_probability_mean_positive": _safe_metric(
            _bootstrap_positive_probability(daily)
        ),
        "stage_breakdown": stage_breakdown,
    }


def _selection_metrics(
    frame: pd.DataFrame,
    selected_mask: pd.Series,
    dates: Sequence[str],
    *,
    cost_rate: float,
) -> dict[str, Any]:
    selected = frame.loc[selected_mask.reindex(frame.index, fill_value=False)].copy()
    return {
        "all_candidates": _return_metrics(
            selected,
            dates,
            cost_rate=cost_rate,
            market_buyable_only=False,
        ),
        "market_buyable_only": _return_metrics(
            selected,
            dates,
            cost_rate=cost_rate,
            market_buyable_only=True,
        ),
    }


def _policy_candidates(
    scored: pd.DataFrame,
    *,
    tail_risk_weight: float,
) -> list[dict[str, Any]]:
    score = _numeric(scored, "trade_score").dropna()
    lcb = _numeric(scored, "trade_predicted_mean_return_lcb").dropna()
    fill = _numeric(scored, "trade_predicted_fill_probability").dropna()
    big_loss = _numeric(scored, "trade_predicted_big_loss_probability").dropna()
    policies: list[dict[str, Any]] = []
    for max_positions in (1, 2):
        for score_q, lcb_q, fill_q, big_loss_q in (
            (0.40, 0.35, 0.00, 0.90),
            (0.50, 0.45, 0.25, 0.85),
            (0.60, 0.50, 0.25, 0.75),
            (0.70, 0.60, 0.35, 0.70),
            (0.78, 0.65, 0.45, 0.65),
            (0.85, 0.72, 0.50, 0.55),
        ):
            policies.append(
                {
                    "max_positions": max_positions,
                    "tail_risk_weight": float(tail_risk_weight),
                    "thresholds": {
                        "min_trade_score": float(score.quantile(score_q)),
                        "min_mean_return_lcb": float(lcb.quantile(lcb_q)),
                        "min_fill_probability": (
                            0.0
                            if fill_q == 0.0
                            else float(fill.quantile(fill_q))
                        ),
                        "max_big_loss_probability": float(
                            big_loss.quantile(big_loss_q)
                        ),
                    },
                }
            )
    return policies


def _tune_policy(
    scored: pd.DataFrame,
    config: TradeSelectorConfig,
    *,
    cost_rate: float,
) -> dict[str, Any]:
    dates = sorted(scored["signal_date"].astype(str).unique())
    candidates: list[dict[str, Any]] = []
    for tail_risk_weight in config.tail_risk_weight_grid:
        ranked = _rank_and_select(
            scored,
            {
                "max_positions": config.max_positions,
                "tail_risk_weight": float(tail_risk_weight),
                "thresholds": {
                    "min_trade_score": -np.inf,
                    "min_mean_return_lcb": -np.inf,
                    "min_fill_probability": 0.0,
                    "max_big_loss_probability": 1.0,
                },
            },
            policy_ready=False,
        )
        candidates.extend(
            _policy_candidates(
                ranked,
                tail_risk_weight=float(tail_risk_weight),
            )
        )
    evaluated: list[tuple[bool, float, dict[str, Any], dict[str, Any]]] = []
    for policy in candidates:
        candidate = _rank_and_select(scored, policy, policy_ready=True)
        mask = _numeric(candidate, "trade_selected", 0).fillna(0).eq(1)
        metrics = _selection_metrics(
            candidate,
            mask,
            dates,
            cost_rate=cost_rate,
        )
        all_metrics = metrics["all_candidates"]
        buyable = metrics["market_buyable_only"]
        checks = {
            "policy_dates": len(dates) >= config.min_policy_dates,
            "trades": int(all_metrics["filled_trades"]) >= config.min_policy_trades,
            "buyable_trades": (
                int(buyable["filled_trades"])
                >= config.min_policy_buyable_trades
            ),
            "signal_date_ratio": (
                float(all_metrics["signal_date_ratio"] or 0.0)
                >= config.min_policy_signal_date_ratio
            ),
            "no_signal_streak": (
                int(all_metrics["max_no_signal_streak"])
                <= config.max_policy_no_signal_streak
            ),
            "forced_open_mean_positive": (
                float(all_metrics["mean_trade_net_return"] or -1.0) > 0.0
            ),
            "buyable_mean_positive": (
                float(buyable["mean_trade_net_return"] or -1.0) > 0.0
            ),
            "buyable_2x_cost_positive": (
                float(buyable["stress_2x_cost_mean_trade_return"] or -1.0)
                > 0.0
            ),
            "buyable_profit_factor": (
                float(buyable["profit_factor"] or 0.0) > 1.0
            ),
        }
        ready = all(checks.values())
        objective = (
            float(buyable["mean_trade_net_return"] or -1.0)
            + 0.50 * float(all_metrics["mean_trade_net_return"] or -1.0)
            + 0.10 * float(all_metrics["signal_date_ratio"] or 0.0)
            - 0.05
            * abs(float(buyable["tail_10pct_mean_return"] or -1.0))
        )
        evaluated.append(
            (
                ready,
                objective,
                {
                    **policy,
                    "ready": ready,
                    "checks": checks,
                    "metrics": metrics,
                },
                metrics,
            )
        )
    if not evaluated:
        return {
            "version": TRADE_SELECTOR_VERSION,
            "ready": False,
            "max_positions": config.max_positions,
            "thresholds": {
                "min_trade_score": -np.inf,
                "min_mean_return_lcb": -np.inf,
                "min_fill_probability": 0.0,
                "max_big_loss_probability": 1.0,
            },
            "reason": "no_policy_candidates",
        }
    feasible = [item for item in evaluated if item[0]]
    pool = feasible if feasible else evaluated
    pool.sort(key=lambda item: (item[1], -item[2]["max_positions"]), reverse=True)
    selected = dict(pool[0][2])
    selected["version"] = TRADE_SELECTOR_VERSION
    selected["reason"] = (
        "chronological_policy_holdout_passed"
        if feasible
        else "best_shadow_policy_failed_profit_or_coverage_gate"
    )
    selected["evaluated_policies"] = int(len(evaluated))
    selected["feasible_policies"] = int(len(feasible))
    return selected


def _bundle_hash(
    frame: pd.DataFrame,
    policy: dict[str, Any],
) -> str:
    columns = list(dict.fromkeys([
        name
        for name in (
            "signal_date",
            "ts_code",
            "stage",
            "observation_rank",
            "market_fill",
            "net_return",
            *TRADE_SELECTOR_FEATURES,
        )
        if name in frame.columns
    ]))
    stable = frame[columns].copy()
    for column in stable.columns:
        if column not in {"signal_date", "ts_code", "stage"}:
            stable[column] = pd.to_numeric(stable[column], errors="coerce")
    digest = hashlib.sha256(
        pd.util.hash_pandas_object(stable, index=False).values.tobytes()
    )
    digest.update(
        json.dumps(
            policy,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )
    digest.update(TRADE_SELECTOR_VERSION.encode("ascii"))
    return digest.hexdigest()


def _bundle_fingerprint_v2(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    config: TradeSelectorConfig,
    *,
    cost_rate: float,
) -> dict[str, Any]:
    semantic = frame.copy()
    executable_features = _features(frame)
    for column in TRADE_SELECTOR_FEATURES:
        semantic[column] = executable_features[column]
    columns = list(
        dict.fromkeys(
            [
                "signal_date",
                "ts_code",
                "stage",
                "observation_rank",
                "market_fill",
                "net_return",
                "big_loss_hit",
                "continuation_limit_up_hit",
                *TRADE_SELECTOR_FEATURES,
            ]
        )
    )
    kinds = {column: "float" for column in columns}
    kinds.update(
        {
            "signal_date": "date",
            "ts_code": "code",
            "stage": "stage",
            "observation_rank": "integer",
            "market_fill": "integer",
            "big_loss_hit": "integer",
            "continuation_limit_up_hit": "integer",
        }
    )
    semantic_fingerprint = canonical_frame_fingerprint(
        semantic,
        columns,
        decimals=RUNTIME_CANONICAL_DECIMALS,
        kinds=kinds,
        strict=True,
    )
    source_paths = (
        Path(__file__),
        Path(__file__).with_name("canonical_fingerprint.py"),
    )
    source_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    contract = _runtime_canonical_contract()
    provenance = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": TRADE_SELECTOR_CANONICAL_V2,
        "canonical_contract": contract,
        "selector_version": TRADE_SELECTOR_VERSION,
        "feature_contract": TRADE_SELECTOR_FEATURE_CONTRACT,
        "features": list(TRADE_SELECTOR_FEATURES),
        "config": asdict(config),
        "cost_rate": float(cost_rate),
        "source_files": source_hashes,
    }
    provenance_sha256 = canonical_mapping_sha256(
        provenance,
        decimals=RUNTIME_CANONICAL_DECIMALS,
        exact_strings=True,
    )
    policy_fingerprint = _selector_policy_fingerprint_v2(policy)
    artifact_sha256 = compose_artifact_fingerprint(
        artifact_kind="decision_trade_selector_canonical_runtime_v2",
        provenance_sha256=provenance_sha256,
        semantic_sha256=semantic_fingerprint["sha256"],
        policy_sha256=policy_fingerprint["sha256"],
        decimals=RUNTIME_CANONICAL_DECIMALS,
    )
    return {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": TRADE_SELECTOR_CANONICAL_V2,
        "canonical_contract": contract,
        "provenance_sha256": provenance_sha256,
        "semantic_sha256": semantic_fingerprint["sha256"],
        "policy_sha256": policy_fingerprint["sha256"],
        "policy_projection": policy_fingerprint["projection"],
        "artifact_sha256": artifact_sha256,
        "schema_valid": semantic_fingerprint["valid"],
        "missing_columns": semantic_fingerprint["missing_columns"],
        "invalid_cell_count": semantic_fingerprint["invalid_cell_count"],
    }


def _finalize_trade_selector_bundle_v2(
    bundle: TradeSelectorBundle,
    frame: pd.DataFrame,
    config: TradeSelectorConfig,
    *,
    cost_rate: float,
) -> None:
    """Mint strict V2 metadata for the single final selector bundle."""

    bundle.canonical_contract = _runtime_canonical_contract()
    fingerprint = _bundle_fingerprint_v2(
        frame,
        bundle.policy,
        config,
        cost_rate=cost_rate,
    )
    bundle.fingerprint_v2 = fingerprint
    bundle.artifact_v2_sha256 = fingerprint["artifact_sha256"]


def fit_trade_selector(
    top10_history: pd.DataFrame,
    *,
    cost_rate: float,
    config: Optional[TradeSelectorConfig] = None,
) -> Optional[TradeSelectorBundle]:
    config = config or TradeSelectorConfig()
    if top10_history.empty:
        return None
    fit_dates, calibration_dates, policy_dates = _split_dates(
        top10_history,
        config,
    )
    if not fit_dates or not calibration_dates or not policy_dates:
        return None
    fit = top10_history.loc[
        top10_history["signal_date"].astype(str).isin(fit_dates)
    ].copy()
    calibration = top10_history.loc[
        top10_history["signal_date"].astype(str).isin(calibration_dates)
    ].copy()
    policy_frame = top10_history.loc[
        top10_history["signal_date"].astype(str).isin(policy_dates)
    ].copy()
    return_model, return_constant, return_selection, mean_margin, residual_q10 = (
        _fit_return_model(fit, calibration, config)
    )
    fill_model, fill_constant = _fit_classifier(
        fit,
        _numeric(fit, "market_fill", 0),
    )
    fit_buyable = fit.loc[_numeric(fit, "market_fill", 0).fillna(0).eq(1)].copy()
    big_loss_model, big_loss_constant = _fit_classifier(
        fit_buyable,
        _numeric(fit_buyable, "big_loss_hit", 0),
    )
    promotion_model, promotion_constant = _fit_classifier(
        fit,
        _numeric(fit, "continuation_limit_up_hit", 0),
    )
    raw_fill = _classifier_probability(
        fill_model,
        fill_constant,
        calibration,
    )
    fill_calibrator, fill_selection = _fit_calibrator(
        raw_fill,
        _numeric(calibration, "market_fill", 0),
        calibration["signal_date"].astype(str),
        fill_constant,
    )
    calibration_buyable = calibration.loc[
        _numeric(calibration, "market_fill", 0).fillna(0).eq(1)
    ].copy()
    raw_big_loss = _classifier_probability(
        big_loss_model,
        big_loss_constant,
        calibration_buyable,
    )
    big_loss_calibrator, big_loss_selection = _fit_calibrator(
        raw_big_loss,
        _numeric(calibration_buyable, "big_loss_hit", 0),
        calibration_buyable["signal_date"].astype(str),
        big_loss_constant,
    )
    raw_promotion = _classifier_probability(
        promotion_model,
        promotion_constant,
        calibration,
    )
    promotion_calibrator, promotion_selection = _fit_calibrator(
        raw_promotion,
        _numeric(calibration, "continuation_limit_up_hit", 0),
        calibration["signal_date"].astype(str),
        promotion_constant,
    )
    provisional = TradeSelectorBundle(
        return_model=return_model,
        return_constant=return_constant,
        fill_model=fill_model,
        fill_constant=fill_constant,
        big_loss_model=big_loss_model,
        big_loss_constant=big_loss_constant,
        promotion_model=promotion_model,
        promotion_constant=promotion_constant,
        fill_calibrator=fill_calibrator,
        big_loss_calibrator=big_loss_calibrator,
        promotion_calibrator=promotion_calibrator,
        mean_return_margin=mean_margin,
        residual_q10=residual_q10,
        policy={},
        train_rows=int(len(fit)),
        train_dates=int(len(fit_dates)),
        return_training_rows=int(
            _numeric(fit, "market_fill", 0).fillna(0).eq(1).sum()
        ),
        calibration_rows=int(len(calibration)),
        policy_rows=int(len(policy_frame)),
        return_selection=return_selection,
        probability_selection={
            "fill": fill_selection,
            "big_loss": big_loss_selection,
            "promotion": promotion_selection,
        },
        artifact_sha256="",
    )
    policy = _tune_policy(
        _score_raw(policy_frame, provisional),
        config,
        cost_rate=cost_rate,
    )
    provisional.policy = policy
    provisional.artifact_sha256 = _bundle_hash(top10_history, policy)
    return provisional


def score_trade_selector(
    frame: pd.DataFrame,
    bundle: Optional[TradeSelectorBundle],
    *,
    globally_promoted: bool,
    force_relative_best_two: bool = True,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if bundle is None:
        out = frame.copy()
        fallback = out.assign(
            _signal_date=out["signal_date"].astype(str),
            _promotion_rank=_numeric(
                out,
                "promotion_rank",
                999999,
            ).fillna(999999),
            _big_loss=_numeric(
                out,
                "predicted_big_loss_probability",
                1.0,
            ).fillna(1.0),
            _return_lcb=_numeric(
                out,
                "predicted_return_lcb",
                -np.inf,
            ).fillna(-np.inf),
            _continuation=_numeric(
                out,
                "predicted_continuation_limit_up_probability",
                -np.inf,
            ).fillna(-np.inf),
            _observation_rank=_numeric(
                out,
                "observation_rank",
                999999,
            ).fillna(999999),
        ).sort_values(
            [
                "_signal_date",
                "_promotion_rank",
                "_big_loss",
                "_return_lcb",
                "_continuation",
                "_observation_rank",
                "ts_code",
            ],
            ascending=[True, True, True, False, False, True, True],
            kind="stable",
        )
        out["trade_rank"] = np.nan
        out.loc[fallback.index, "trade_rank"] = (
            fallback.groupby("_signal_date", sort=False).cumcount() + 1
        ).to_numpy()
        if "promotion_rank" not in out.columns:
            out["promotion_rank"] = _numeric(out, "observation_rank")
        out["promotion_rank_score"] = np.nan
        out["predicted_promotion_probability"] = np.nan
        out["trade_score"] = np.nan
        out["trade_predicted_conditional_net_return"] = np.nan
        out["trade_predicted_mean_return_lcb"] = np.nan
        out["trade_predicted_fill_probability"] = np.nan
        out["trade_predicted_big_loss_probability"] = np.nan
        out["trade_predicted_outcome_q10"] = np.nan
        out["trade_tail_loss_proxy"] = np.nan
        out["trade_base_score"] = np.nan
        out["trade_tail_risk_weight"] = np.nan
        out["trade_gate_pass"] = 0
        out["trade_shadow_selected"] = 0
        if force_relative_best_two:
            shadow_selected = fallback.loc[
                fallback.groupby("_signal_date", sort=False).cumcount() < 2
            ]
            out.loc[shadow_selected.index, "trade_shadow_selected"] = 1
        out["trade_selected"] = 0
        out["trade_selector_policy_ready"] = 0
        out["trade_selector_promoted"] = 0
        out["trade_selector_version"] = TRADE_SELECTOR_VERSION
        out["trade_selector_artifact_sha256"] = ""
        out["trade_selector_canonical_v2_version"] = (
            TRADE_SELECTOR_CANONICAL_V2
        )
        out["trade_selector_artifact_v2_sha256"] = ""
        out["trade_selector_canonical_schema"] = CANONICAL_FINGERPRINT_SCHEMA
        out["trade_selector_canonical_decimals"] = RUNTIME_CANONICAL_DECIMALS
        out["trade_selector_execution_numeric_mode"] = "raw_float64"
        out["trade_selector_raw_execution_preserved"] = True
        out["trade_model_reason"] = "insufficient_nested_oos_history"
        out.loc[
            out["trade_shadow_selected"].eq(1),
            "trade_model_reason",
        ] = "relative_best_two_fallback"
        return out
    scored = _score_raw(frame, bundle)
    policy_ready = bool(bundle.policy.get("ready") is True)
    out = _rank_and_select(
        scored,
        bundle.policy,
        policy_ready=policy_ready,
    )
    if not globally_promoted:
        formal = _numeric(out, "trade_selected", 0).fillna(0).eq(1)
        out.loc[formal, "trade_model_reason"] = "selector_not_promoted"
        out["trade_selected"] = 0
    if force_relative_best_two:
        ranked = out.assign(
            _signal_date=out["signal_date"].astype(str),
            _trade_rank=_numeric(out, "trade_rank", 999999).fillna(999999),
            _observation_rank=_numeric(
                out,
                "observation_rank",
                999999,
            ).fillna(999999),
        ).sort_values(
            ["_signal_date", "_trade_rank", "_observation_rank", "ts_code"],
            ascending=[True, True, True, True],
            kind="stable",
        )
        relative_best = ranked.loc[
            ranked.groupby("_signal_date", sort=False).cumcount() < 2
        ]
        out["trade_shadow_selected"] = 0
        out.loc[relative_best.index, "trade_shadow_selected"] = 1
        shadow_only = (
            out["trade_shadow_selected"].eq(1)
            & out["trade_selected"].eq(0)
        )
        out.loc[shadow_only, "trade_model_reason"] = "relative_best_two_only"
    out["trade_selector_promoted"] = int(globally_promoted)
    out["trade_selector_artifact_sha256"] = bundle.artifact_sha256
    out["trade_selector_canonical_v2_version"] = TRADE_SELECTOR_CANONICAL_V2
    out["trade_selector_artifact_v2_sha256"] = bundle.artifact_v2_sha256
    out["trade_selector_canonical_schema"] = CANONICAL_FINGERPRINT_SCHEMA
    out["trade_selector_canonical_decimals"] = RUNTIME_CANONICAL_DECIMALS
    out["trade_selector_execution_numeric_mode"] = "raw_float64"
    out["trade_selector_raw_execution_preserved"] = True
    return out


def _baseline_metrics(
    frame: pd.DataFrame,
    dates: Sequence[str],
    top_n: int,
    *,
    cost_rate: float,
) -> dict[str, Any]:
    rank = _numeric(frame, "observation_rank")
    mask = rank.between(1, top_n, inclusive="both").fillna(False)
    return _selection_metrics(
        frame,
        mask,
        dates,
        cost_rate=cost_rate,
    )


def _promotion_rank_metrics(audit: pd.DataFrame) -> dict[str, Any]:
    if audit.empty or "continuation_limit_up_hit" not in audit.columns:
        return {
            "target": "T_close_continuation_limit_up_hit",
            "rows": 0,
            "dates": 0,
            "promotion_rank": {},
            "observation_rank": {},
        }
    sample = audit.copy()
    sample["_truth"] = _numeric(sample, "continuation_limit_up_hit")
    sample = sample.loc[sample["_truth"].notna()].copy()
    if sample.empty:
        return {
            "target": "T_close_continuation_limit_up_hit",
            "rows": 0,
            "dates": 0,
            "promotion_rank": {},
            "observation_rank": {},
        }

    def summarize(rank_field: str, frame: Optional[pd.DataFrame] = None) -> dict[str, Any]:
        source = sample if frame is None else frame
        rank = _numeric(source, rank_field)
        top1 = source.loc[rank.eq(1), "_truth"]
        top3 = source.loc[rank.between(1, 3, inclusive="both"), "_truth"]
        ndcg_values: list[float] = []
        for _, group in source.assign(_rank=rank).groupby(
            source["signal_date"].astype(str),
            sort=True,
        ):
            ordered = group.sort_values(
                ["_rank", "ts_code"],
                kind="stable",
            ).head(3)
            truth = ordered["_truth"].to_numpy(dtype=float)
            discounts = 1.0 / np.log2(np.arange(len(truth), dtype=float) + 2.0)
            dcg = float(np.sum(truth * discounts))
            ideal = np.sort(group["_truth"].to_numpy(dtype=float))[::-1][:3]
            ideal_discounts = 1.0 / np.log2(
                np.arange(len(ideal), dtype=float) + 2.0
            )
            ideal_dcg = float(np.sum(ideal * ideal_discounts))
            ndcg_values.append(dcg / ideal_dcg if ideal_dcg > 0 else 1.0)
        return {
            "top1_promotion_rate": _safe_metric(top1.mean()),
            "top3_promotion_rate": _safe_metric(top3.mean()),
            "ndcg_at_3": _safe_metric(np.mean(ndcg_values)),
        }

    promotion = summarize("promotion_rank")
    observation = summarize("observation_rank")
    probability = _numeric(sample, "predicted_promotion_probability")
    valid_probability = probability.notna()
    truth = sample.loc[valid_probability, "_truth"].to_numpy(dtype=float)
    predicted = probability.loc[valid_probability].to_numpy(dtype=float)
    brier = (
        float(np.mean((predicted - truth) ** 2))
        if valid_probability.any()
        else float("nan")
    )
    prevalence = float(np.mean(truth)) if len(truth) else float("nan")
    constant_brier = (
        float(np.mean((prevalence - truth) ** 2))
        if len(truth)
        else float("nan")
    )
    brier_skill = (
        1.0 - brier / constant_brier
        if math.isfinite(brier) and constant_brier > 0
        else float("nan")
    )
    calibration_error = float("nan")
    if len(truth):
        bins = np.linspace(0.0, 1.0, 6)
        bucket = np.clip(np.digitize(predicted, bins[1:-1]), 0, 4)
        calibration_error = float(
            sum(
                (mask.sum() / len(truth))
                * abs(float(np.mean(predicted[mask])) - float(np.mean(truth[mask])))
                for index in range(5)
                if (mask := bucket == index).any()
            )
        )

    promotion_top1 = sample.loc[
        _numeric(sample, "promotion_rank").eq(1),
        ["signal_date", "_truth"],
    ].drop_duplicates("signal_date")
    observation_top1 = sample.loc[
        _numeric(sample, "observation_rank").eq(1),
        ["signal_date", "_truth"],
    ].drop_duplicates("signal_date")
    head_to_head = promotion_top1.merge(
        observation_top1,
        on="signal_date",
        suffixes=("_promotion", "_observation"),
    )
    chronological_stability: list[dict[str, Any]] = []
    unique_dates = np.asarray(sorted(sample["signal_date"].astype(str).unique()))
    for index, segment_dates in enumerate(np.array_split(unique_dates, 3), start=1):
        if not len(segment_dates):
            continue
        segment = sample.loc[
            sample["signal_date"].astype(str).isin(segment_dates)
        ].copy()
        segment_promotion = summarize("promotion_rank", segment)
        segment_observation = summarize("observation_rank", segment)
        chronological_stability.append(
            {
                "segment": index,
                "start": str(segment_dates[0]),
                "end": str(segment_dates[-1]),
                "dates": int(len(segment_dates)),
                "promotion_top1_rate": segment_promotion["top1_promotion_rate"],
                "observation_top1_rate": segment_observation["top1_promotion_rate"],
                "promotion_top3_rate": segment_promotion["top3_promotion_rate"],
                "observation_top3_rate": segment_observation["top3_promotion_rate"],
            }
        )
    stage_breakdown: dict[str, dict[str, Any]] = {}
    stage_values = sample.get(
        "stage",
        pd.Series("", index=sample.index, dtype=str),
    ).astype(str)
    for stage in ("2→3", "3→4"):
        stage_sample = sample.loc[stage_values.eq(stage)].copy()
        if stage_sample.empty:
            continue
        stage_sample["_promotion_stage_rank"] = stage_sample.groupby(
            stage_sample["signal_date"].astype(str),
        )["promotion_rank"].rank(method="first", ascending=True)
        stage_sample["_observation_stage_rank"] = stage_sample.groupby(
            stage_sample["signal_date"].astype(str),
        )["observation_rank"].rank(method="first", ascending=True)
        stage_breakdown[stage] = {
            "rows": int(len(stage_sample)),
            "dates": int(stage_sample["signal_date"].astype(str).nunique()),
            "promotion_rank": summarize(
                "_promotion_stage_rank",
                stage_sample,
            ),
            "observation_rank": summarize(
                "_observation_stage_rank",
                stage_sample,
            ),
        }

    chronological_lifts = [
        float(item["promotion_top1_rate"] or 0.0)
        - float(item["observation_top1_rate"] or 0.0)
        for item in chronological_stability
    ]
    stage_lifts = [
        float(item["promotion_rank"]["top1_promotion_rate"] or 0.0)
        - float(item["observation_rank"]["top1_promotion_rate"] or 0.0)
        for item in stage_breakdown.values()
    ]
    ranking_checks = {
        "minimum_rows": len(sample) >= 1_000,
        "minimum_dates": sample["signal_date"].astype(str).nunique() >= 250,
        "positive_top3_lift": (
            float(promotion.get("top3_promotion_rate") or 0.0)
            > float(observation.get("top3_promotion_rate") or 0.0)
        ),
        "positive_each_chronological_segment": bool(chronological_lifts)
        and min(chronological_lifts) > 0.0,
        "nonnegative_each_stage": bool(stage_lifts) and min(stage_lifts) >= 0.0,
    }
    probability_checks = {
        "positive_brier_skill": math.isfinite(brier_skill) and brier_skill > 0.0,
        "ece_at_most_8pct": (
            math.isfinite(calibration_error) and calibration_error <= 0.08
        ),
    }
    return {
        "target": "T_close_continuation_limit_up_hit",
        "rows": int(len(sample)),
        "dates": int(sample["signal_date"].astype(str).nunique()),
        "probability_brier": _safe_metric(brier),
        "constant_probability_brier": _safe_metric(constant_brier),
        "probability_brier_skill": _safe_metric(brier_skill),
        "probability_ece_5bin": _safe_metric(calibration_error),
        "promotion_rank": promotion,
        "observation_rank": observation,
        "top1_head_to_head": {
            "dates": int(len(head_to_head)),
            "promotion_wins": int(
                (head_to_head["_truth_promotion"] > head_to_head["_truth_observation"]).sum()
            ),
            "observation_wins": int(
                (head_to_head["_truth_promotion"] < head_to_head["_truth_observation"]).sum()
            ),
            "ties": int(
                (head_to_head["_truth_promotion"] == head_to_head["_truth_observation"]).sum()
            ),
        },
        "chronological_stability": chronological_stability,
        "stage_breakdown": stage_breakdown,
        "ranking_quality_gate": {
            "passed": all(ranking_checks.values()),
            "checks": ranking_checks,
        },
        "probability_quality_gate": {
            "passed": all(probability_checks.values()),
            "checks": probability_checks,
        },
        "top3_rate_lift": _safe_metric(
            float(promotion.get("top3_promotion_rate") or 0.0)
            - float(observation.get("top3_promotion_rate") or 0.0)
        ),
    }


def _selector_metrics(
    audit: pd.DataFrame,
    top10_history: pd.DataFrame,
    production_bundle: Optional[TradeSelectorBundle],
    *,
    cost_rate: float,
    config: TradeSelectorConfig,
) -> dict[str, Any]:
    dates = sorted(audit["signal_date"].astype(str).unique()) if not audit.empty else []
    formal_mask = _numeric(audit, "trade_selected", 0).fillna(0).eq(1)
    shadow_mask = _numeric(audit, "trade_shadow_selected", 0).fillna(0).eq(1)
    formal = _selection_metrics(
        audit,
        formal_mask,
        dates,
        cost_rate=cost_rate,
    )
    shadow = _selection_metrics(
        audit,
        shadow_mask,
        dates,
        cost_rate=cost_rate,
    )
    top1 = _baseline_metrics(audit, dates, 1, cost_rate=cost_rate)
    top2 = _baseline_metrics(audit, dates, 2, cost_rate=cost_rate)
    all_formal = formal["all_candidates"]
    buyable_formal = formal["market_buyable_only"]
    baseline_buyable_means = [
        float(
            item["market_buyable_only"].get("mean_trade_net_return")
            or -1.0
        )
        for item in (top1, top2)
    ]
    policy_ready_ratio = _safe_metric(
        _numeric(audit, "trade_selector_policy_ready", 0).fillna(0).mean()
    )
    checks = {
        "production_policy_ready": bool(
            production_bundle is not None
            and production_bundle.policy.get("ready") is True
        ),
        "return_model_chronological_skill": bool(
            production_bundle is not None
            and production_bundle.return_selection.get("passed") is True
        ),
        "oos_dates": len(dates) >= config.promotion_min_oos_dates,
        "selected_trades": (
            int(all_formal["filled_trades"]) >= config.promotion_min_trades
        ),
        "market_buyable_trades": (
            int(buyable_formal["filled_trades"])
            >= config.promotion_min_buyable_trades
        ),
        "signal_date_ratio": (
            float(all_formal["signal_date_ratio"] or 0.0)
            >= config.promotion_min_signal_date_ratio
        ),
        "no_signal_streak": (
            int(all_formal["max_no_signal_streak"])
            <= config.promotion_max_no_signal_streak
        ),
        "policy_ready_fold_ratio": (
            float(policy_ready_ratio or 0.0)
            >= config.promotion_min_policy_ready_ratio
        ),
        "forced_open_mean_positive": (
            float(all_formal["mean_trade_net_return"] or -1.0) > 0.0
        ),
        "market_buyable_mean_positive": (
            float(buyable_formal["mean_trade_net_return"] or -1.0) > 0.0
        ),
        "market_buyable_2x_cost_positive": (
            float(
                buyable_formal["stress_2x_cost_mean_trade_return"] or -1.0
            )
            > 0.0
        ),
        "market_buyable_profit_factor": (
            float(buyable_formal["profit_factor"] or 0.0) > 1.0
        ),
        "tail_loss_floor": (
            float(buyable_formal["tail_10pct_mean_return"] or -1.0)
            >= config.promotion_min_tail_mean_return
        ),
        "bootstrap_probability": (
            float(
                buyable_formal["bootstrap_probability_mean_positive"] or 0.0
            )
            >= config.promotion_min_bootstrap_probability
        ),
        "beats_observation_top1_and_top2": (
            float(buyable_formal["mean_trade_net_return"] or -1.0)
            > max(baseline_buyable_means)
        ),
    }
    promoted = all(checks.values())
    return {
        "schema_version": "decision_trade_selector_backtest_v1",
        "version": TRADE_SELECTOR_VERSION,
        "feature_contract": TRADE_SELECTOR_FEATURE_CONTRACT,
        "target": (
            "conditional net return from actual T auction/open fill to "
            "predeclared T+1 09:30 exit after costs"
        ),
        "return_training_scope": "market_fill_eq_1_only",
        "top10_contract": "exact_daily_observation_rank_without_padding",
        "max_positions": config.max_positions,
        "oos_dates": int(len(dates)),
        "oos_start": dates[0] if dates else "",
        "oos_end": dates[-1] if dates else "",
        "oos_rows": int(len(audit)),
        "walkforward_refits": int(
            _numeric(audit, "trade_oos_refit_index").nunique()
        ),
        "policy_ready_fold_ratio": policy_ready_ratio,
        "formal_policy_oos": formal,
        "shadow_policy_oos": shadow,
        "observation_top1_baseline": top1,
        "observation_top2_baseline": top2,
        "promotion_rank_oos": _promotion_rank_metrics(audit),
        "production_policy": (
            production_bundle.policy if production_bundle is not None else {}
        ),
        "production_return_selection": (
            production_bundle.return_selection
            if production_bundle is not None
            else {}
        ),
        "production_probability_selection": (
            production_bundle.probability_selection
            if production_bundle is not None
            else {}
        ),
        "production_artifact_sha256": (
            production_bundle.artifact_sha256
            if production_bundle is not None
            else ""
        ),
        "canonical_v2_version": TRADE_SELECTOR_CANONICAL_V2,
        "canonical_contract": (
            production_bundle.canonical_contract
            if production_bundle is not None
            else _runtime_canonical_contract()
        ),
        "production_artifact_v2_sha256": (
            production_bundle.artifact_v2_sha256
            if production_bundle is not None
            else ""
        ),
        "production_fingerprint_v2": (
            production_bundle.fingerprint_v2
            if production_bundle is not None
            else {}
        ),
        "promotion_checks": checks,
        "promotion_failures": [
            name for name, passed in checks.items() if not passed
        ],
        "promoted": promoted,
        "no_trade_guard": {
            "zero_trades_cannot_promote": True,
            "minimum_signal_date_ratio": config.promotion_min_signal_date_ratio,
            "maximum_no_signal_streak": config.promotion_max_no_signal_streak,
            "minimum_selected_trades": config.promotion_min_trades,
            "daily_zero_allowed": True,
        },
        "history_rows": int(len(top10_history)),
        "history_dates": int(
            top10_history["signal_date"].astype(str).nunique()
            if not top10_history.empty
            else 0
        ),
    }


def walkforward_trade_selector(
    top10_history: pd.DataFrame,
    *,
    cost_rate: float,
    config: Optional[TradeSelectorConfig] = None,
) -> tuple[pd.DataFrame, Optional[TradeSelectorBundle], dict[str, Any]]:
    config = config or TradeSelectorConfig()
    if top10_history.empty:
        return pd.DataFrame(), None, _selector_metrics(
            pd.DataFrame(),
            top10_history,
            None,
            cost_rate=cost_rate,
            config=config,
        )
    dates = sorted(top10_history["signal_date"].astype(str).unique())
    output: list[pd.DataFrame] = []
    refit = 0
    for block_start in range(config.warmup_dates, len(dates), config.block_dates):
        train_end = block_start - config.embargo_dates
        train_dates = dates[:train_end]
        test_dates = dates[block_start : block_start + config.block_dates]
        if len(train_dates) < config.min_fit_dates or not test_dates:
            continue
        train = top10_history.loc[
            top10_history["signal_date"].astype(str).isin(train_dates)
        ].copy()
        bundle = fit_trade_selector(
            train,
            cost_rate=cost_rate,
            config=config,
        )
        test = top10_history.loc[
            top10_history["signal_date"].astype(str).isin(test_dates)
        ].copy()
        refit += 1
        scored = score_trade_selector(
            test,
            bundle,
            globally_promoted=True,
            force_relative_best_two=False,
        )
        scored["trade_oos_train_end"] = train_dates[-1]
        scored["trade_oos_train_dates"] = int(len(train_dates))
        scored["trade_oos_refit_index"] = int(refit)
        scored["trade_oos_block_dates"] = int(config.block_dates)
        output.append(scored)
    audit = (
        pd.concat(output, ignore_index=True)
        if output
        else pd.DataFrame()
    )
    production_bundle = fit_trade_selector(
        top10_history,
        cost_rate=cost_rate,
        config=config,
    )
    if production_bundle is not None:
        _finalize_trade_selector_bundle_v2(
            production_bundle,
            top10_history,
            config,
            cost_rate=cost_rate,
        )
    metrics = _selector_metrics(
        audit,
        top10_history,
        production_bundle,
        cost_rate=cost_rate,
        config=config,
    )
    if not audit.empty:
        audit["trade_selector_globally_promoted"] = int(
            metrics.get("promoted") is True
        )
    return audit, production_bundle, metrics


def observation_top10_metrics(
    top10: pd.DataFrame,
    *,
    all_oos_dates: Sequence[str],
    cost_rate: float,
) -> dict[str, Any]:
    dates = sorted({str(value) for value in all_oos_dates})
    selected_counts = (
        top10.groupby(top10["signal_date"].astype(str))
        .size()
        .reindex(dates, fill_value=0)
        if not top10.empty and "signal_date" in top10.columns
        else pd.Series(0, index=dates, dtype=int)
    )
    all_metrics = _return_metrics(
        top10,
        dates,
        cost_rate=cost_rate,
        market_buyable_only=False,
    )
    buyable_metrics = _return_metrics(
        top10,
        dates,
        cost_rate=cost_rate,
        market_buyable_only=True,
    )
    return {
        "scope": "exact_daily_observation_rank_2to3_and_3to4",
        "ranking_field": "observation_rank",
        "top_n_cap": OBSERVATION_TOP_N,
        "padding_policy": "none",
        "oos_dates": int(len(dates)),
        "candidate_days": int(selected_counts.gt(0).sum()),
        "zero_candidate_days": int(selected_counts.eq(0).sum()),
        "days_below_cap": int(
            selected_counts.between(
                1,
                OBSERVATION_TOP_N - 1,
                inclusive="both",
            ).sum()
        ),
        "days_at_cap": int(selected_counts.eq(OBSERVATION_TOP_N).sum()),
        "average_candidates_per_candidate_day": _safe_metric(
            selected_counts[selected_counts.gt(0)].mean()
            if selected_counts.gt(0).any()
            else np.nan
        ),
        "all_candidates": all_metrics,
        "market_buyable_only": buyable_metrics,
    }


__all__ = [
    "FORBIDDEN_TRADE_SELECTOR_FEATURES",
    "TRADE_SELECTOR_FEATURES",
    "TRADE_SELECTOR_FEATURE_CONTRACT",
    "TRADE_SELECTOR_VERSION",
    "TradeSelectorBundle",
    "TradeSelectorConfig",
    "fit_trade_selector",
    "observation_top10_metrics",
    "prepare_observation_top10",
    "score_trade_selector",
    "walkforward_trade_selector",
]
