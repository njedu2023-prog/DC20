from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from top10decision.probability_calibration import (
    MINIMUM_CALIBRATION_OUTPUT_SPAN,
    MONOTONICITY_GRID_POINTS,
    MONOTONICITY_SCHEMA_VERSION,
    MONOTONICITY_TOLERANCE,
    calibrator_monotonicity_evidence,
    monotonicity_evidence_is_valid,
)


EPS = 1e-6


def _clip_probability(values: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), EPS, 1.0 - EPS)


def _design(method: str, probability: np.ndarray) -> np.ndarray:
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
    """Transforms raw classifier output without using future prediction dates."""

    method: str
    constant: float
    estimator: Optional[Any] = None

    def transform(self, raw_probability: Sequence[float] | np.ndarray) -> np.ndarray:
        raw = _clip_probability(raw_probability)
        if self.method == "constant" or self.estimator is None:
            if self.method == "identity":
                return np.clip(raw, 0.0, 1.0)
            return np.repeat(float(np.clip(self.constant, 0.0, 1.0)), len(raw))
        if self.method == "isotonic":
            calibrated = self.estimator.predict(raw)
        else:
            calibrated = self.estimator.predict_proba(
                _design(self.method, raw)
            )[:, 1]
        return np.clip(calibrated, 0.0, 1.0)


def fit_probability_calibrator(
    method: str,
    raw_probability: Sequence[float] | np.ndarray,
    truth: Sequence[int] | np.ndarray,
    *,
    sample_weight: Optional[Sequence[float] | np.ndarray] = None,
    constant: float,
) -> Optional[ProbabilityCalibrator]:
    raw = _clip_probability(raw_probability)
    y = np.asarray(truth, dtype=int)
    weights = (
        np.asarray(sample_weight, dtype=float)
        if sample_weight is not None
        else np.ones(len(y), dtype=float)
    )
    if len(raw) != len(y) or len(y) == 0:
        return None
    if method == "constant":
        candidate = ProbabilityCalibrator("constant", constant)
        evidence = calibrator_monotonicity_evidence(candidate, raw)
        return candidate if evidence["nondecreasing"] else None
    if method == "identity":
        candidate = ProbabilityCalibrator("identity", constant)
        evidence = calibrator_monotonicity_evidence(candidate, raw)
        return candidate if monotonicity_evidence_is_valid(
            evidence,
            expected_method=method,
            require_nonconstant=True,
        ) else None
    if np.unique(y).size < 2:
        return None
    if method == "isotonic":
        if len(y) < 40 or np.unique(raw).size < 8:
            return None
        estimator = IsotonicRegression(
            y_min=EPS,
            y_max=1.0 - EPS,
            out_of_bounds="clip",
        )
        estimator.fit(raw, y, sample_weight=weights)
        candidate = ProbabilityCalibrator(method, constant, estimator)
        evidence = calibrator_monotonicity_evidence(candidate, raw)
        return candidate if monotonicity_evidence_is_valid(
            evidence,
            expected_method=method,
            require_nonconstant=True,
        ) else None
    if method not in {"platt", "beta"}:
        raise ValueError(f"unsupported probability calibration method: {method}")
    estimator = LogisticRegression(C=1.0, max_iter=2_000, random_state=20260726)
    estimator.fit(_design(method, raw), y, sample_weight=weights)
    candidate = ProbabilityCalibrator(method, constant, estimator)
    evidence = calibrator_monotonicity_evidence(candidate, raw)
    return candidate if monotonicity_evidence_is_valid(
        evidence,
        expected_method=method,
        require_nonconstant=True,
    ) else None


def probability_metrics(
    probability: Sequence[float] | np.ndarray,
    truth: Sequence[int] | np.ndarray,
    *,
    sample_weight: Optional[Sequence[float] | np.ndarray] = None,
    bins: int = 10,
) -> dict[str, Any]:
    p = _clip_probability(probability)
    y = np.asarray(truth, dtype=float)
    weights = (
        np.asarray(sample_weight, dtype=float)
        if sample_weight is not None
        else np.ones(len(y), dtype=float)
    )
    if len(p) != len(y) or len(y) == 0:
        return {}
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    if weights.sum() <= 0:
        weights = np.ones(len(y), dtype=float)
    brier = float(np.average((p - y) ** 2, weights=weights))
    log_loss = float(
        np.average(-(y * np.log(p) + (1.0 - y) * np.log1p(-p)), weights=weights)
    )
    edges = np.linspace(0.0, 1.0, max(2, int(bins)) + 1)
    bucket = np.clip(np.digitize(p, edges[1:-1], right=False), 0, len(edges) - 2)
    reliability: list[dict[str, Any]] = []
    ece = 0.0
    total_weight = float(weights.sum())
    for index in range(len(edges) - 1):
        mask = bucket == index
        if not mask.any():
            continue
        bucket_weight = float(weights[mask].sum())
        predicted = float(np.average(p[mask], weights=weights[mask]))
        observed = float(np.average(y[mask], weights=weights[mask]))
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


def chronological_calibration_split(
    dates: Sequence[str] | np.ndarray,
    *,
    fit_fraction: float = 0.60,
    embargo_dates: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    date_values = np.asarray([str(value) for value in dates], dtype=object)
    unique_dates = sorted(set(date_values.tolist()))
    if len(unique_dates) < 4:
        return np.zeros(len(date_values), dtype=bool), np.ones(len(date_values), dtype=bool)
    split = int(math.floor(len(unique_dates) * float(fit_fraction)))
    split = min(max(2, split), len(unique_dates) - 2)
    embargo = max(0, int(embargo_dates))
    fit_dates = set(unique_dates[:split])
    eval_start = min(len(unique_dates), split + embargo)
    eval_dates = set(unique_dates[eval_start:])
    if not eval_dates:
        eval_dates = set(unique_dates[split:])
    return (
        np.isin(date_values, list(fit_dates)),
        np.isin(date_values, list(eval_dates)),
    )


__all__ = [
    "MONOTONICITY_GRID_POINTS",
    "MONOTONICITY_SCHEMA_VERSION",
    "MONOTONICITY_TOLERANCE",
    "MINIMUM_CALIBRATION_OUTPUT_SPAN",
    "ProbabilityCalibrator",
    "calibrator_monotonicity_evidence",
    "chronological_calibration_split",
    "fit_probability_calibrator",
    "monotonicity_evidence_is_valid",
    "probability_metrics",
]
