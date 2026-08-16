from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.trade_selector import (  # noqa: E402
    FORBIDDEN_TRADE_SELECTOR_FEATURES,
    TRADE_SELECTOR_FEATURES,
    TradeProbabilityCalibrator,
    TradeSelectorBundle,
    TradeSelectorConfig,
    _bundle_hash,
    _fit_return_model,
    _promotion_rank_metrics,
    fit_trade_selector,
    prepare_observation_top10,
    score_trade_selector,
    walkforward_trade_selector,
)


def _constant_bundle(
    *,
    return_value: float = 0.03,
    fill_probability: float = 0.80,
    big_loss_probability: float = 0.10,
    promotion_probability: float = 0.30,
    min_score: float = 0.0,
) -> TradeSelectorBundle:
    return TradeSelectorBundle(
        return_model=None,
        return_constant=return_value,
        fill_model=None,
        fill_constant=fill_probability,
        big_loss_model=None,
        big_loss_constant=big_loss_probability,
        promotion_model=None,
        promotion_constant=promotion_probability,
        fill_calibrator=TradeProbabilityCalibrator(
            "constant",
            fill_probability,
        ),
        big_loss_calibrator=TradeProbabilityCalibrator(
            "constant",
            big_loss_probability,
        ),
        promotion_calibrator=TradeProbabilityCalibrator(
            "constant",
            promotion_probability,
        ),
        mean_return_margin=0.002,
        residual_q10=-0.05,
        policy={
            "ready": True,
            "max_positions": 2,
            "thresholds": {
                "min_trade_score": min_score,
                "min_mean_return_lcb": -0.10,
                "min_fill_probability": 0.0,
                "max_big_loss_probability": 0.50,
            },
        },
        train_rows=100,
        train_dates=50,
        return_training_rows=40,
        calibration_rows=30,
        policy_rows=30,
        return_selection={"selected": "constant", "passed": True},
        probability_selection={},
        artifact_sha256="a" * 64,
    )


class DecisionTradeSelectorTest(unittest.TestCase):
    def test_feature_contract_excludes_every_t_and_tplus1_truth(self) -> None:
        self.assertFalse(
            set(TRADE_SELECTOR_FEATURES)
            & FORBIDDEN_TRADE_SELECTOR_FEATURES
        )

    def test_observation_top10_is_exact_and_never_padded(self) -> None:
        rows = []
        for date, count in (("20260105", 12), ("20260106", 4)):
            for rank in range(1, count + 1):
                rows.append(
                    {
                        "signal_date": date,
                        "ts_code": f"600{rank:03d}.SH",
                        "stage": "2→3" if rank % 2 else "3→4",
                        "mechanism_limit_pct": 10.0,
                        "source_rank": rank,
                        "rank": rank,
                        "predicted_big_loss_probability": rank / 100.0,
                        "predicted_return_lcb": 0.02 - rank / 1000.0,
                        "predicted_exit_probability": 0.95,
                        "conservative_ev": 0.03 - rank / 1000.0,
                        "predicted_continuation_limit_up_probability": 0.60,
                        "d_close": 10.0,
                    }
                )
        top10 = prepare_observation_top10(pd.DataFrame(rows))
        counts = top10.groupby("signal_date").size().to_dict()
        self.assertEqual(counts, {"20260105": 10, "20260106": 4})
        self.assertEqual(int(top10["observation_rank"].max()), 10)
        self.assertEqual(
            top10.loc[
                top10["signal_date"].eq("20260106"),
                "observation_rank",
            ].tolist(),
            [1, 2, 3, 4],
        )

    def test_trade_selector_always_shadows_best_two_but_formal_can_be_zero(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": f"60000{rank}.SH",
                    "observation_rank": rank,
                }
                for rank in range(1, 6)
            ]
        )
        selected = score_trade_selector(
            frame,
            _constant_bundle(),
            globally_promoted=True,
        )
        self.assertEqual(int(selected["trade_selected"].sum()), 2)
        self.assertEqual(int(selected["trade_shadow_selected"].sum()), 2)
        self.assertEqual(
            selected.loc[
                selected["trade_selected"].eq(1),
                "trade_rank",
            ].tolist(),
            [1.0, 2.0],
        )
        no_trade = score_trade_selector(
            frame,
            _constant_bundle(min_score=0.50),
            globally_promoted=True,
        )
        self.assertEqual(int(no_trade["trade_selected"].sum()), 0)
        self.assertEqual(int(no_trade["trade_shadow_selected"].sum()), 2)
        self.assertEqual(
            no_trade.loc[
                no_trade["trade_shadow_selected"].eq(1),
                "trade_rank",
            ].tolist(),
            [1.0, 2.0],
        )
        self.assertTrue(
            no_trade.loc[
                no_trade["trade_shadow_selected"].eq(1),
                "trade_model_reason",
            ].eq("relative_best_two_only").all()
        )
        strict_oos = score_trade_selector(
            frame,
            _constant_bundle(min_score=0.50),
            globally_promoted=True,
            force_relative_best_two=False,
        )
        self.assertEqual(int(strict_oos["trade_shadow_selected"].sum()), 0)

    def test_trade_selector_fallback_still_selects_relative_best_two(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": code,
                    "observation_rank": observation_rank,
                    "promotion_rank": promotion_rank,
                    "predicted_big_loss_probability": big_loss_probability,
                    "predicted_return_lcb": return_lcb,
                    "predicted_continuation_limit_up_probability": continuation,
                }
                for code, observation_rank, promotion_rank, big_loss_probability, return_lcb, continuation in (
                    ("600001.SH", 1, 3, 0.05, 0.03, 0.70),
                    ("600002.SH", 2, 1, 0.20, 0.01, 0.60),
                    ("600003.SH", 3, 1, 0.08, 0.00, 0.80),
                )
            ]
        )
        scored = score_trade_selector(
            frame,
            None,
            globally_promoted=False,
        )
        selected = scored.loc[
            scored["trade_shadow_selected"].eq(1)
        ].sort_values("trade_rank")
        self.assertEqual(selected["ts_code"].tolist(), ["600003.SH", "600002.SH"])
        self.assertEqual(int(scored["trade_selected"].sum()), 0)
        self.assertTrue(
            selected["trade_model_reason"].eq("relative_best_two_fallback").all()
        )

    def test_trade_selector_never_pads_beyond_available_candidates(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": "600001.SH",
                    "observation_rank": 1,
                }
            ]
        )
        scored = score_trade_selector(
            frame,
            _constant_bundle(min_score=0.50),
            globally_promoted=False,
        )
        self.assertEqual(int(scored["trade_shadow_selected"].sum()), 1)

    def test_promotion_rank_is_independent_from_observation_rank(self) -> None:
        class PromotionModel:
            def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
                probability = features["final_score"].to_numpy(dtype=float)
                return np.column_stack([1.0 - probability, probability])

        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": f"60000{rank}.SH",
                    "observation_rank": rank,
                    "final_score": probability,
                }
                for rank, probability in ((1, 0.20), (2, 0.90), (3, 0.55))
            ]
        )
        bundle = _constant_bundle()
        bundle.promotion_model = PromotionModel()
        bundle.promotion_calibrator = TradeProbabilityCalibrator(
            "identity",
            0.30,
        )
        scored = score_trade_selector(
            frame,
            bundle,
            globally_promoted=False,
        )
        promotion_order = scored.sort_values("promotion_rank")[
            "observation_rank"
        ].tolist()
        self.assertEqual(promotion_order, [2, 3, 1])

    def test_promotion_rank_survives_constant_probability_fallback(self) -> None:
        class PromotionModel:
            def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
                probability = features["final_score"].to_numpy(dtype=float)
                return np.column_stack([1.0 - probability, probability])

        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": f"60000{rank}.SH",
                    "observation_rank": rank,
                    "final_score": probability,
                }
                for rank, probability in ((1, 0.20), (2, 0.90), (3, 0.55))
            ]
        )
        bundle = _constant_bundle()
        bundle.promotion_model = PromotionModel()
        bundle.promotion_calibrator = TradeProbabilityCalibrator(
            "constant",
            0.30,
        )
        scored = score_trade_selector(
            frame,
            bundle,
            globally_promoted=False,
        )
        self.assertTrue(
            scored["predicted_promotion_probability"].eq(0.30).all()
        )
        self.assertEqual(
            scored.sort_values("promotion_rank")["observation_rank"].tolist(),
            [2, 3, 1],
        )

    def test_artifact_hash_includes_stage_transition(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": "600001.SH",
                    "stage": "2→3",
                    "observation_rank": 1,
                    "market_fill": 1,
                    "net_return": 0.01,
                }
            ]
        )
        changed = frame.copy()
        changed.loc[0, "stage"] = "3→4"
        self.assertNotEqual(
            _bundle_hash(frame, {"ready": False}),
            _bundle_hash(changed, {"ready": False}),
        )

    def test_promotion_audit_reports_skill_and_time_segments(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "signal_date": date,
                    "ts_code": f"600{date[-2:]}{rank}.SH",
                    "observation_rank": rank,
                    "promotion_rank": 3 - rank,
                    "predicted_promotion_probability": 0.99 if rank == 2 else 0.01,
                    "continuation_limit_up_hit": int(rank == 2),
                }
                for date in (
                    "20260105",
                    "20260106",
                    "20260107",
                    "20260108",
                    "20260109",
                    "20260112",
                )
                for rank in (1, 2)
            ]
        )
        metrics = _promotion_rank_metrics(frame)
        self.assertGreater(metrics["probability_brier_skill"], 0.0)
        self.assertEqual(metrics["top1_head_to_head"]["promotion_wins"], 6)
        self.assertEqual(len(metrics["chronological_stability"]), 3)
        self.assertTrue(metrics["ranking_quality_gate"]["passed"] is False)
        self.assertTrue(metrics["probability_quality_gate"]["passed"])

    def test_tail_loss_severity_reduces_trade_utility_continuously(self) -> None:
        class BigLossModel:
            def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
                probability = features["final_score"].to_numpy(dtype=float)
                return np.column_stack([1.0 - probability, probability])

        frame = pd.DataFrame(
            [
                {
                    "signal_date": "20260105",
                    "ts_code": "600001.SH",
                    "observation_rank": 1,
                    "final_score": 0.10,
                },
                {
                    "signal_date": "20260105",
                    "ts_code": "600002.SH",
                    "observation_rank": 2,
                    "final_score": 0.90,
                },
            ]
        )
        bundle = _constant_bundle()
        bundle.big_loss_model = BigLossModel()
        bundle.big_loss_calibrator = TradeProbabilityCalibrator(
            "identity",
            0.10,
        )
        bundle.policy["tail_risk_weight"] = 0.75
        scored = score_trade_selector(
            frame,
            bundle,
            globally_promoted=False,
        ).set_index("ts_code")
        self.assertGreater(
            scored.loc["600001.SH", "trade_score"],
            scored.loc["600002.SH", "trade_score"],
        )
        self.assertTrue(
            scored["trade_tail_risk_weight"].eq(0.75).all()
        )

    def test_conditional_return_fit_uses_only_market_buyable_rows(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=120)
        rows = []
        for day_index, date in enumerate(dates):
            signal_date = date.strftime("%Y%m%d")
            for rank in range(1, 5):
                market_fill = int(rank % 2 == 0)
                rows.append(
                    {
                        "signal_date": signal_date,
                        "ts_code": f"600{rank:03d}.SH",
                        "observation_rank": rank,
                        "source_rank": rank,
                        "stage": "2→3" if rank <= 2 else "3→4",
                        "market_fill": market_fill,
                        "net_return": (
                            0.015
                            + 0.002 * np.sin(day_index / 5.0)
                            if market_fill
                            else 0.50
                        ),
                        "big_loss_hit": 0,
                        "continuation_limit_up_hit": int(rank == 1),
                        "d_return": 0.10,
                        "path_strength_delta": rank / 100.0,
                        "predicted_net_return": 0.01 - rank / 1000.0,
                        "predicted_fill_probability": 0.50,
                        "predicted_big_loss_probability": 0.10,
                    }
                )
        history = pd.DataFrame(rows)
        config = TradeSelectorConfig(
            min_fit_dates=30,
            min_fit_buyable_rows=40,
            min_policy_dates=12,
            calibration_fraction=0.15,
            policy_fraction=0.15,
        )
        bundle = fit_trade_selector(
            history,
            cost_rate=0.0045,
            config=config,
        )
        self.assertIsNotNone(bundle)
        assert bundle is not None
        fit_dates = sorted(history["signal_date"].unique())[: bundle.train_dates]
        expected_buyable = history[
            history["signal_date"].isin(fit_dates)
            & history["market_fill"].eq(1)
        ]
        self.assertEqual(
            bundle.return_training_rows,
            len(expected_buyable),
        )
        self.assertEqual(
            bundle.return_selection["training_scope"],
            "market_fill_eq_1_only",
        )
        self.assertLess(bundle.return_training_rows, bundle.train_rows)

    def test_failed_return_candidate_falls_back_to_constant(self) -> None:
        fit_rows = []
        calibration_rows = []
        for day_index, date in enumerate(pd.bdate_range("2025-01-02", periods=80)):
            for rank in (1, 2):
                fit_rows.append(
                    {
                        "signal_date": date.strftime("%Y%m%d"),
                        "ts_code": f"60000{rank}.SH",
                        "observation_rank": rank,
                        "market_fill": 1,
                        "net_return": 0.03 if rank == 1 else -0.03,
                    }
                )
        for day_index, date in enumerate(pd.bdate_range("2025-05-01", periods=20)):
            for rank in (1, 2):
                calibration_rows.append(
                    {
                        "signal_date": date.strftime("%Y%m%d"),
                        "ts_code": f"60000{rank}.SH",
                        "observation_rank": rank,
                        "market_fill": 1,
                        "net_return": -0.03 if rank == 1 else 0.03,
                    }
                )
        model, constant, selection, mean_margin, residual_q10 = _fit_return_model(
            pd.DataFrame(fit_rows),
            pd.DataFrame(calibration_rows),
            TradeSelectorConfig(min_fit_buyable_rows=40),
        )
        self.assertIsNone(model)
        self.assertAlmostEqual(constant, 0.0, places=12)
        self.assertFalse(selection["passed"])
        self.assertEqual(selection["selected"], "constant")
        self.assertAlmostEqual(
            selection["rmse"],
            selection["constant_rmse"],
            places=12,
        )
        self.assertGreater(mean_margin, 0.0)
        self.assertAlmostEqual(residual_q10, -0.03, places=12)
        self.assertIn(
            selection["rejected_candidate"],
            {"ridge", "hist_gradient_boosting"},
        )

    def test_zero_trade_model_cannot_be_promoted(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=100)
        rows = []
        for day_index, date in enumerate(dates):
            for rank in range(1, 4):
                rows.append(
                    {
                        "signal_date": date.strftime("%Y%m%d"),
                        "ts_code": f"600{rank:03d}.SH",
                        "observation_rank": rank,
                        "source_rank": rank,
                        "stage": "2→3",
                        "market_fill": 1,
                        "net_return": -0.02 - rank / 1000.0,
                        "big_loss_hit": int(rank == 3),
                        "continuation_limit_up_hit": 0,
                        "d_return": 0.10,
                        "predicted_net_return": -0.01,
                        "predicted_fill_probability": 0.80,
                        "predicted_big_loss_probability": 0.40,
                    }
                )
        config = TradeSelectorConfig(
            warmup_dates=65,
            block_dates=15,
            min_fit_dates=30,
            min_fit_buyable_rows=40,
            min_policy_dates=10,
            min_policy_trades=6,
            min_policy_buyable_trades=4,
            promotion_min_oos_dates=20,
            promotion_min_trades=10,
            promotion_min_buyable_trades=10,
        )
        _, _, metrics = walkforward_trade_selector(
            pd.DataFrame(rows),
            cost_rate=0.0045,
            config=config,
        )
        self.assertFalse(metrics["promoted"])
        self.assertTrue(
            metrics["no_trade_guard"]["zero_trades_cannot_promote"]
        )
        self.assertIn(
            "production_policy_ready",
            metrics["promotion_failures"],
        )


if __name__ == "__main__":
    unittest.main()
