from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3.calibration import (  # noqa: E402
    ProbabilityCalibrator,
    chronological_calibration_split,
    probability_metrics,
)
from top10decision.data.tushare_minute import write_calendar  # noqa: E402
from top10decision.decision.three_engine_models import (  # noqa: E402
    fit_probability_calibrator,
)
from top10decision.probability_calibration import (  # noqa: E402
    MONOTONICITY_GRID_POINTS,
    calibrator_monotonicity_evidence,
    monotonicity_evidence_is_valid,
)


class DecisionV8CalibrationTest(unittest.TestCase):
    def test_chronological_split_embargoes_the_boundary_date(self) -> None:
        dates = np.repeat(
            [
                "20260105",
                "20260106",
                "20260107",
                "20260108",
                "20260109",
                "20260112",
            ],
            2,
        )
        fit_mask, eval_mask = chronological_calibration_split(
            dates,
            fit_fraction=0.5,
            embargo_dates=1,
        )
        fit_dates = sorted(set(dates[fit_mask]))
        eval_dates = sorted(set(dates[eval_mask]))
        self.assertLess(fit_dates[-1], eval_dates[0])
        self.assertNotIn("20260108", fit_dates)
        self.assertNotIn("20260108", eval_dates)
        self.assertFalse(np.any(fit_mask & eval_mask))

    def test_constant_calibrator_is_an_explicit_honest_fallback(self) -> None:
        calibrator = ProbabilityCalibrator("constant", 0.35)
        output = calibrator.transform([0.01, 0.50, 0.99])
        np.testing.assert_allclose(output, [0.35, 0.35, 0.35])

    def test_beta_calibration_stays_inside_probability_bounds(self) -> None:
        raw = np.linspace(0.05, 0.95, 120)
        truth = (raw + 0.10 * np.sin(np.arange(120)) > 0.55).astype(int)
        calibrator = fit_probability_calibrator(
            "beta",
            raw,
            truth,
            constant=float(truth.mean()),
        )
        self.assertIsNotNone(calibrator)
        calibrated = calibrator.transform([0.0, 0.2, 0.8, 1.0])
        self.assertTrue(np.isfinite(calibrated).all())
        self.assertTrue(((calibrated >= 0.0) & (calibrated <= 1.0)).all())
        evidence = calibrator_monotonicity_evidence(calibrator, raw)
        self.assertTrue(
            monotonicity_evidence_is_valid(
                evidence,
                expected_method="beta",
                require_nonconstant=True,
            )
        )
        self.assertGreaterEqual(evidence["grid_points"], MONOTONICITY_GRID_POINTS)

    def test_negative_platt_slope_is_rejected_instead_of_reversing_rank(self) -> None:
        raw = np.linspace(0.05, 0.95, 160)
        truth = (raw < 0.50).astype(int)
        self.assertIsNone(
            fit_probability_calibrator(
                "platt",
                raw,
                truth,
                constant=float(truth.mean()),
            )
        )

    def test_negative_beta_derivative_is_rejected(self) -> None:
        raw = np.linspace(0.05, 0.95, 160)
        truth = (raw < 0.45).astype(int)
        self.assertIsNone(
            fit_probability_calibrator(
                "beta",
                raw,
                truth,
                constant=float(truth.mean()),
            )
        )

    def test_flat_isotonic_mapping_is_not_a_rank_calibrator(self) -> None:
        raw = np.linspace(0.05, 0.95, 160)
        truth = (raw < 0.50).astype(int)
        self.assertIsNone(
            fit_probability_calibrator(
                "isotonic",
                raw,
                truth,
                constant=float(truth.mean()),
            )
        )

    def test_probability_metrics_include_reliability_and_ece(self) -> None:
        metrics = probability_metrics(
            [0.10, 0.20, 0.80, 0.90],
            [0, 0, 1, 1],
            bins=4,
        )
        self.assertLess(metrics["brier"], 0.05)
        self.assertGreaterEqual(metrics["ece"], 0.0)
        self.assertTrue(metrics["reliability"])
        self.assertEqual(
            sum(item["samples"] for item in metrics["reliability"]),
            4,
        )

    def test_calendar_sync_merges_years_instead_of_erasing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            write_calendar(
                pd.DataFrame(
                    [
                        {
                            "exchange": "SSE",
                            "cal_date": "20231229",
                            "is_open": 1,
                            "pretrade_date": "20231228",
                        }
                    ]
                ),
                root,
            )
            path = write_calendar(
                pd.DataFrame(
                    [
                        {
                            "exchange": "SSE",
                            "cal_date": "20260727",
                            "is_open": 1,
                            "pretrade_date": "20260724",
                        }
                    ]
                ),
                root,
            )
            merged = pd.read_csv(path, dtype={"cal_date": str})
            self.assertEqual(
                set(merged["cal_date"]),
                {"20231229", "20260727"},
            )


if __name__ == "__main__":
    unittest.main()
