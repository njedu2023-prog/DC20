from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import numpy as np


CALIBRATION_EPS = 1e-6
MONOTONICITY_SCHEMA_VERSION = "probability_calibrator_monotonicity_v1"
MONOTONICITY_GRID_POINTS = 4_097
MONOTONICITY_TOLERANCE = 1e-12
MINIMUM_CALIBRATION_OUTPUT_SPAN = 1e-6


def _clip_probability(values: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.clip(
        np.asarray(values, dtype=float),
        CALIBRATION_EPS,
        1.0 - CALIBRATION_EPS,
    )


def _grid_monotonicity_evidence(
    calibrator: Any,
    lower: float,
    upper: float,
    *,
    grid_points: int,
) -> dict[str, Any]:
    points = max(3, int(grid_points))
    grid = np.linspace(float(lower), float(upper), points)
    try:
        calibrated = np.asarray(calibrator.transform(grid), dtype=float)
    except (AttributeError, TypeError, ValueError):
        calibrated = np.full(points, np.nan, dtype=float)
    finite = bool(
        calibrated.shape == grid.shape and np.isfinite(calibrated).all()
    )
    differences = (
        np.diff(calibrated) if finite else np.asarray([np.nan], dtype=float)
    )
    violations = (
        int(np.sum(differences < -MONOTONICITY_TOLERANCE))
        if finite
        else points - 1
    )
    minimum_step = (
        float(np.min(differences))
        if finite and differences.size
        else None
    )
    output_span = (
        float(np.max(calibrated) - np.min(calibrated))
        if finite and calibrated.size
        else None
    )
    return {
        "lower": float(lower),
        "upper": float(upper),
        "grid_points": points,
        "finite": finite,
        "minimum_step": minimum_step,
        "output_span": output_span,
        "nonconstant": bool(
            finite
            and output_span is not None
            and output_span > MINIMUM_CALIBRATION_OUTPUT_SPAN
        ),
        "violation_count": violations,
        "nondecreasing": bool(finite and violations == 0),
    }


def calibrator_monotonicity_evidence(
    calibrator: Any,
    raw_probability: Sequence[float] | np.ndarray,
    *,
    grid_points: int = MONOTONICITY_GRID_POINTS,
) -> dict[str, Any]:
    """Prove that calibration preserves the classifier's probability order.

    Calibration may change probability levels, but it must never repair an
    anti-predictive model by reversing its score. The audit combines dense
    numerical grids over the complete probability domain and the observed raw
    support with exact slope/derivative checks for Platt and beta calibration.
    """

    raw_input = np.asarray(raw_probability, dtype=float).reshape(-1)
    finite_raw = raw_input[np.isfinite(raw_input)]
    support_available = bool(finite_raw.size)
    if support_available:
        clipped_support = _clip_probability(finite_raw)
        support_lower = float(np.min(clipped_support))
        support_upper = float(np.max(clipped_support))
    else:
        support_lower = float(CALIBRATION_EPS)
        support_upper = float(1.0 - CALIBRATION_EPS)

    global_grid = _grid_monotonicity_evidence(
        calibrator,
        CALIBRATION_EPS,
        1.0 - CALIBRATION_EPS,
        grid_points=grid_points,
    )
    support_grid = _grid_monotonicity_evidence(
        calibrator,
        support_lower,
        support_upper,
        grid_points=grid_points,
    )

    method = str(getattr(calibrator, "method", "") or "")
    estimator = getattr(calibrator, "estimator", None)
    analytic: dict[str, Any] = {
        "available": method in {"constant", "identity", "isotonic"},
        "nondecreasing": method in {"constant", "identity", "isotonic"},
    }
    if method in {"platt", "beta"}:
        coefficients = np.asarray(
            getattr(estimator, "coef_", np.asarray([])), dtype=float
        ).reshape(-1)
        analytic = {
            "available": bool(
                estimator is not None
                and coefficients.size == (1 if method == "platt" else 2)
                and np.isfinite(coefficients).all()
            ),
            "coefficients": [float(value) for value in coefficients],
            "nondecreasing": False,
        }
        if analytic["available"] and method == "platt":
            slope = float(coefficients[0])
            analytic.update(
                {
                    "platt_slope": slope,
                    # Strict by contract: even a numerically tiny negative
                    # slope reverses model semantics and is not calibration.
                    "nondecreasing": bool(slope >= 0.0),
                }
            )
        elif analytic["available"]:
            beta_global = np.linspace(
                CALIBRATION_EPS,
                1.0 - CALIBRATION_EPS,
                max(3, int(grid_points)),
            )
            beta_support = np.linspace(
                support_lower,
                support_upper,
                max(3, int(grid_points)),
            )

            def _beta_derivative(values: np.ndarray) -> np.ndarray:
                return (
                    float(coefficients[0]) / values
                    + float(coefficients[1]) / (1.0 - values)
                )

            global_derivative = _beta_derivative(beta_global)
            support_derivative = _beta_derivative(beta_support)
            minimum_global = float(np.min(global_derivative))
            minimum_support = float(np.min(support_derivative))
            analytic.update(
                {
                    "beta_minimum_logit_derivative_global": minimum_global,
                    "beta_minimum_logit_derivative_support": minimum_support,
                    # The logistic link is increasing, so this derivative's
                    # sign is exactly the sign of calibrated monotonicity.
                    "nondecreasing": bool(
                        minimum_global >= 0.0 and minimum_support >= 0.0
                    ),
                }
            )

    nondecreasing = bool(
        support_available
        and global_grid["nondecreasing"]
        and support_grid["nondecreasing"]
        and analytic.get("available") is True
        and analytic.get("nondecreasing") is True
    )
    return {
        "schema_version": MONOTONICITY_SCHEMA_VERSION,
        "method": method,
        "grid_points": max(3, int(grid_points)),
        "tolerance": MONOTONICITY_TOLERANCE,
        "raw_support": {
            "available": support_available,
            "rows": int(finite_raw.size),
            "unique": int(np.unique(finite_raw).size),
            "minimum": support_lower if support_available else None,
            "maximum": support_upper if support_available else None,
        },
        "global_grid": global_grid,
        "support_grid": support_grid,
        "analytic": analytic,
        "minimum_output_span": MINIMUM_CALIBRATION_OUTPUT_SPAN,
        "nonconstant": bool(
            global_grid.get("nonconstant") is True
            and support_grid.get("nonconstant") is True
        ),
        "nondecreasing": nondecreasing,
    }


def monotonicity_evidence_is_valid(
    evidence: Any,
    *,
    expected_method: Optional[str] = None,
    require_nonconstant: bool = False,
) -> bool:
    """Validate persisted monotonicity evidence without trusting summaries."""

    if not isinstance(evidence, dict):
        return False
    if evidence.get("schema_version") != MONOTONICITY_SCHEMA_VERSION:
        return False
    if evidence.get("nondecreasing") is not True:
        return False
    method = str(evidence.get("method") or "")
    if method not in {"constant", "identity", "isotonic", "platt", "beta"}:
        return False
    if expected_method is not None and method != str(expected_method):
        return False
    if int(evidence.get("grid_points") or 0) < MONOTONICITY_GRID_POINTS:
        return False
    support = evidence.get("raw_support")
    if not isinstance(support, dict) or support.get("available") is not True:
        return False
    if int(support.get("rows") or 0) < 1 or int(support.get("unique") or 0) < 1:
        return False
    try:
        support_minimum = float(support.get("minimum"))
        support_maximum = float(support.get("maximum"))
    except (TypeError, ValueError):
        return False
    if not (
        math.isfinite(support_minimum)
        and math.isfinite(support_maximum)
        and CALIBRATION_EPS
        <= support_minimum
        <= support_maximum
        <= 1.0 - CALIBRATION_EPS
    ):
        return False
    for name in ("global_grid", "support_grid"):
        grid = evidence.get(name)
        if not isinstance(grid, dict):
            return False
        if (
            grid.get("finite") is not True
            or grid.get("nondecreasing") is not True
            or int(grid.get("violation_count") or 0) != 0
            or int(grid.get("grid_points") or 0) < MONOTONICITY_GRID_POINTS
        ):
            return False
        minimum_step = grid.get("minimum_step")
        if minimum_step is None:
            return False
        try:
            if float(minimum_step) < -MONOTONICITY_TOLERANCE:
                return False
        except (TypeError, ValueError):
            return False
        if require_nonconstant:
            try:
                if (
                    grid.get("nonconstant") is not True
                    or float(grid.get("output_span"))
                    <= MINIMUM_CALIBRATION_OUTPUT_SPAN
                ):
                    return False
            except (TypeError, ValueError):
                return False
    analytic = evidence.get("analytic")
    if not isinstance(analytic, dict):
        return False
    if (
        analytic.get("available") is not True
        or analytic.get("nondecreasing") is not True
    ):
        return False
    if method == "platt":
        try:
            if float(analytic.get("platt_slope")) < 0.0:
                return False
        except (TypeError, ValueError):
            return False
    if method == "beta":
        for name in (
            "beta_minimum_logit_derivative_global",
            "beta_minimum_logit_derivative_support",
        ):
            try:
                if float(analytic.get(name)) < 0.0:
                    return False
            except (TypeError, ValueError):
                return False
    if require_nonconstant and evidence.get("nonconstant") is not True:
        return False
    return True


__all__ = [
    "CALIBRATION_EPS",
    "MINIMUM_CALIBRATION_OUTPUT_SPAN",
    "MONOTONICITY_GRID_POINTS",
    "MONOTONICITY_SCHEMA_VERSION",
    "MONOTONICITY_TOLERANCE",
    "calibrator_monotonicity_evidence",
    "monotonicity_evidence_is_valid",
]
