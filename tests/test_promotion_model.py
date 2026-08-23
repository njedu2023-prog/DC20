from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine
from top10decision.auction_v3.calibration import ProbabilityCalibrator
from top10decision.auction_v3.promotion_model import (
    PROMOTION_D_CONTEXT_FEATURES,
    PROMOTION_PRIOR_FEATURES,
    PROMOTION_SOURCE_FEATURES,
    attach_promotion_source_features,
    build_promotion_context_features,
    fit_promotion_blend,
    load_promotion_validation,
)


class PromotionDContextFeatureTests(unittest.TestCase):
    def test_pure_function_preserves_eight_feature_formulas(self) -> None:
        closes = [10.0, 11.0, 12.0, 13.0, 14.3, 15.73]
        features = build_promotion_context_features(
            2,
            closes,
            [False, True, False, False, True, True],
        )

        self.assertEqual(set(features), set(PROMOTION_D_CONTEXT_FEATURES))
        expected_returns = np.asarray(
            [
                11.0 / 10.0 - 1.0,
                12.0 / 11.0 - 1.0,
                13.0 / 12.0 - 1.0,
            ]
        )
        self.assertAlmostEqual(
            features["five_year_pre_streak_1d_return"],
            13.0 / 12.0 - 1.0,
        )
        self.assertAlmostEqual(
            features["five_year_pre_streak_3d_return"],
            13.0 / 10.0 - 1.0,
        )
        self.assertAlmostEqual(
            features["five_year_pre_streak_volatility"],
            float(np.std(expected_returns, ddof=0)),
        )
        self.assertEqual(
            features["five_year_pre_streak_limit_up_count"], 1.0
        )
        self.assertEqual(features["five_year_recent_limit_up_count"], 3.0)
        self.assertEqual(features["five_year_days_since_prior_limit_up"], 3.0)
        self.assertAlmostEqual(
            features["five_year_streak_runup"], 15.73 / 13.0 - 1.0
        )
        self.assertAlmostEqual(
            features["five_year_price_log"], float(np.log1p(15.73))
        )

    def test_pure_function_handles_missing_prices_without_fabrication(self) -> None:
        features = build_promotion_context_features(
            2,
            [10.0, np.nan, 12.0, 13.0, 14.3, np.nan],
            [False, None, False, False, True, None],
        )

        self.assertAlmostEqual(
            features["five_year_pre_streak_1d_return"],
            13.0 / 12.0 - 1.0,
        )
        self.assertAlmostEqual(
            features["five_year_pre_streak_3d_return"],
            13.0 / 10.0 - 1.0,
        )
        self.assertEqual(features["five_year_pre_streak_volatility"], 0.0)
        self.assertEqual(
            features["five_year_pre_streak_limit_up_count"], 0.0
        )
        self.assertEqual(features["five_year_recent_limit_up_count"], 1.0)
        self.assertEqual(features["five_year_days_since_prior_limit_up"], 6.0)
        self.assertTrue(np.isnan(features["five_year_streak_runup"]))
        self.assertTrue(np.isnan(features["five_year_price_log"]))


class PromotionSourceFeatureTests(unittest.TestCase):
    def _write_source(self, root: Path, same_day_hits: int, include_future: bool) -> None:
        target = root / "data" / "auction_v3" / "promotion_prior"
        target.mkdir(parents=True)
        rows = [
            {"signal_date": 20240101, "stage": 2, "board": "SH_MAIN", "samples": 10, "hits": 4},
            {"signal_date": 20240102, "stage": 2, "board": "SH_MAIN", "samples": 10, "hits": same_day_hits},
        ]
        if include_future:
            rows.append(
                {"signal_date": 20240103, "stage": 2, "board": "SH_MAIN", "samples": 500, "hits": 500}
            )
        pd.DataFrame(rows).to_csv(target / "five_year_daily_stage_board.csv", index=False)
        event_columns = [
            "signal_date",
            "ts_code",
            "stage",
            "board",
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
        ]
        pd.DataFrame(columns=event_columns).to_csv(
            target / "five_year_event_features.csv.gz",
            index=False,
            compression="gzip",
        )

    @staticmethod
    def _row() -> pd.DataFrame:
        return pd.DataFrame(
            [{"signal_date": 20240102, "ts_code": "600001.SH", "limit_times": 2, "stage": "2→3"}]
        )

    def test_same_day_and_future_truth_do_not_change_prior(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            self._write_source(first_root, same_day_hits=0, include_future=False)
            self._write_source(second_root, same_day_hits=10, include_future=True)
            first_features = attach_promotion_source_features(self._row(), first_root)
            second_features = attach_promotion_source_features(self._row(), second_root)
            np.testing.assert_allclose(
                first_features[PROMOTION_PRIOR_FEATURES].to_numpy(dtype=float),
                second_features[PROMOTION_PRIOR_FEATURES].to_numpy(dtype=float),
                equal_nan=True,
            )

    def test_scoring_entrypoint_always_attaches_promotion_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = AuctionV3Engine(AuctionV3Config(root=Path(temporary)))
            base = pd.DataFrame(
                [
                    {
                        "signal_date": "20260805",
                        "ts_code": "600001.SH",
                        "limit_times": 2,
                    }
                ]
            )
            with patch.object(
                engine,
                "_score_candidates_batch",
                side_effect=lambda frame, bundle, *, apply_policy: frame,
            ):
                scored = engine.score_candidates(base, object())
            self.assertTrue(
                set(PROMOTION_SOURCE_FEATURES).issubset(scored.columns)
            )


class PromotionValidationTests(unittest.TestCase):
    def test_validation_requires_every_strict_oos_gate(self) -> None:
        required = {
            "strict_oos_dates_at_least_500": True,
            "brier_improvement_positive": True,
            "challenger_ece_not_worse_and_at_most_8pct": True,
            "auc_improvement_positive": True,
            "top1_hit_rate_improvement_positive": True,
            "top3_row_hit_rate_improvement_positive": True,
            "top3_any_hit_rate_improvement_positive": True,
            "brier_bootstrap_lower_bound_positive": True,
        }
        payload = {
            "direct_promotion_pass": True,
            "gate_checks": required,
            "challenger": {"dates": 514},
            "comparison": {
                "top1_hit_rate_improvement": 0.001,
                "top3_row_hit_rate_improvement": 0.001,
            },
            "bootstrap": {"brier_improvement": {"ci95_low": 0.0001}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "models" / "decision_promotion_v13_validation.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(load_promotion_validation(root)["validated"])
            payload["gate_checks"]["top3_any_hit_rate_improvement_positive"] = False
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(load_promotion_validation(root)["validated"])

    def test_integer_signal_dates_reach_nested_calibration(self) -> None:
        fit_rows = []
        calibration_rows = []
        for date in range(20240101, 20240131):
            for offset in range(4):
                fit_rows.append(
                    {
                        "signal_date": date,
                        "x": float(offset),
                        "z": float((date + offset) % 5),
                        "continuation_limit_up_hit": int(offset >= 2),
                    }
                )
        for date in range(20240201, 20240231):
            for offset in range(4):
                calibration_rows.append(
                    {
                        "signal_date": date,
                        "x": float(offset),
                        "z": float((date + offset) % 5),
                        "continuation_limit_up_hit": int(offset >= 2),
                    }
                )
        fit_frame = pd.DataFrame(fit_rows)
        calibration_frame = pd.DataFrame(calibration_rows)
        incumbent = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("model", LogisticRegression(max_iter=1_000)),
            ]
        ).fit(fit_frame[["x"]], fit_frame["continuation_limit_up_hit"])
        with patch(
            "top10decision.auction_v3.promotion_model.MODEL_KINDS",
            ("lr",),
        ):
            result = fit_promotion_blend(
                incumbent_model=incumbent,
                incumbent_calibrator=ProbabilityCalibrator("identity", 0.5),
                incumbent_features=("x",),
                constant=0.5,
                fit_frame=fit_frame,
                calibration_frame=calibration_frame,
                target="continuation_limit_up_hit",
                feature_sets={"challenger": ("x", "z")},
            )
        self.assertNotEqual(
            "empty_nested_calibration",
            result.selection.get("reason"),
        )


if __name__ == "__main__":
    unittest.main()
