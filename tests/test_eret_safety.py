from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from top10decision.engines import eret_engine
from top10decision.decision.contracts import ERET_HOLDING_MODE, ERET_TRUTH_VERSION


def _load_train_module():
    path = ROOT / "scripts" / "train_eret.py"
    spec = importlib.util.spec_from_file_location("train_eret_for_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


train_eret = _load_train_module()


def _accepted_payload(anchor: str = "20260710") -> dict:
    return {
        "eret": {
            "anchor_trade_date": anchor,
            "status": "trained",
            "eret_truth_version": ERET_TRUTH_VERSION,
            "return_holding_mode": ERET_HOLDING_MODE,
            "loaded_trade_dates": 20,
            "selected_model": "lr",
            "selected_model_pass": True,
            "acceptance_pass": True,
            "selected_model_metrics": {
                "daily_spearman_corr_mean": 0.12,
                "rmse_skill_vs_train_mean": 0.08,
            },
        }
    }


def _model_meta(anchor: str = "20260710") -> dict:
    return {
        "anchor_trade_date": anchor,
        "status": "trained",
        "selected_model": "lr",
        "eret_truth_version": ERET_TRUTH_VERSION,
        "return_holding_mode": ERET_HOLDING_MODE,
        "window": {"n_loaded_dates": 20},
        "features": {
            "feature_cols": ["stable_feature"],
            "numeric_cols": ["stable_feature"],
            "categorical_cols": [],
        },
    }


class ERetInferenceSafetyTests(unittest.TestCase):
    def test_rejected_model_falls_back_to_rule_without_loading_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models").mkdir()
            (root / "outputs" / "learning").mkdir(parents=True)
            (root / "models" / "eret_lr.joblib").write_bytes(b"not-a-model")
            (root / "models" / "eret_meta.json").write_text(
                json.dumps(_model_meta()), encoding="utf-8"
            )
            payload = _accepted_payload()
            payload["eret"]["acceptance_pass"] = False
            (root / "outputs" / "learning" / "learning_acceptance_latest.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

            original_rule = eret_engine.overnight_model_rule
            eret_engine.overnight_model_rule = lambda df, regime="RISK_ON": pd.Series(
                0.02, index=df.index
            )
            try:
                result = eret_engine.apply_eret_engine(
                    pd.DataFrame({"stable_feature": [1.0, 2.0]}), project_root=root
                )
            finally:
                eret_engine.overnight_model_rule = original_rule

            self.assertTrue((result["eret_pred_src"] == "rule").all())
            self.assertTrue(np.allclose(result["e_ret_pred"], 0.02))
            self.assertTrue(
                result["eret_degrade_reason"].str.contains("acceptance_pass_false").all()
            )

    def test_acceptance_requires_exact_anchor_and_quality_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "outputs" / "learning" / "learning_acceptance_latest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(_accepted_payload()), encoding="utf-8")

            accepted, reason, _ = eret_engine._model_acceptance_status(root, _model_meta())
            self.assertTrue(accepted)
            self.assertEqual(reason, "")

            accepted, reason, _ = eret_engine._model_acceptance_status(
                root, _model_meta(anchor="20260709")
            )
            self.assertFalse(accepted)
            self.assertEqual(reason, "acceptance_anchor_mismatch")

    def test_model_specific_missing_value_contract(self):
        frame = pd.DataFrame({"x": [1.0, np.nan, np.inf]})
        lr_input = eret_engine._prepare_model_input(frame, "lr")
        gbm_input = eret_engine._prepare_model_input(frame, "lgbm")
        self.assertTrue(lr_input["x"].isna().iloc[1:].all())
        self.assertTrue((gbm_input["x"].iloc[1:] == -999.0).all())

    def test_unavailable_intraday_placeholders_become_missing(self):
        frame = pd.DataFrame(
            {
                "intraday_available": [0, 1],
                "reseal_score": [0.5, 0.8],
                "auction_strength_score": [0.4, 0.7],
            }
        )
        masked = eret_engine._mask_unavailable_intraday_features(frame)
        self.assertTrue(pd.isna(masked.loc[0, "reseal_score"]))
        self.assertEqual(masked.loc[1, "reseal_score"], 0.8)
        self.assertEqual(masked.loc[0, "auction_strength_score"], 0.4)


class ERetTrainingSafetyTests(unittest.TestCase):
    def test_unstable_and_date_broadcast_features_are_excluded(self):
        frame = pd.DataFrame(
            {
                "stable_feature": [1.0, 2.0],
                "symbol": [600001, 600002],
                "hgt_market": [100.0, 100.0],
                "south_money_market": [200.0, 200.0],
                "market_regime": [1.0, 1.0],
                "prior_prob_prior": [np.nan, np.nan],
                "intraday_missing_reason": [np.nan, np.nan],
                "realized_ret_t1_to_t2": [0.01, -0.01],
                "realized_ret_open_to_tplus1_open_0930": [0.01, -0.01],
                "exit_price_tplus1_timed": [10.1, 9.9],
                "exit_reason": ["fixed_open_0930", "fixed_open_0930"],
            }
        )
        self.assertEqual(train_eret.select_feature_columns(frame), ["stable_feature"])

    def test_each_trade_date_receives_equal_total_weight(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["20260701", "20260701", "20260702"],
                "sample_weight": [1.0, 1.0, 1.0],
            }
        )
        weights = train_eret.build_date_balanced_sample_weight(frame)
        day_one = float(weights[:2].sum())
        day_two = float(weights[2:].sum())
        self.assertAlmostEqual(day_one, day_two)
        self.assertAlmostEqual(float(weights.mean()), 1.0)

    def test_validation_holds_out_multiple_latest_dates(self):
        rows = []
        for day in range(1, 21):
            trade_date = f"202606{day:02d}"
            rows.extend({"trade_date": trade_date, "x": float(i)} for i in range(5))
        frame = pd.DataFrame(rows)
        train, valid, mode, small = train_eret.split_train_valid(
            frame, min_train_rows=10, min_valid_rows=10
        )
        self.assertFalse(small)
        self.assertIsNotNone(valid)
        self.assertEqual(valid["trade_date"].nunique(), 4)
        self.assertTrue(set(train["trade_date"]).isdisjoint(set(valid["trade_date"])))
        self.assertTrue(mode.endswith(":4d"))

    def test_model_selection_prioritizes_multiday_rank_quality(self):
        selected, _ = train_eret.choose_selected_model(
            {
                "lr": {
                    "daily_spearman_corr_mean": 0.05,
                    "rmse_skill_vs_train_mean": 0.10,
                    "rmse": 0.05,
                    "mae": 0.04,
                    "directional_acc": 0.60,
                },
                "lgbm": {
                    "daily_spearman_corr_mean": 0.20,
                    "rmse_skill_vs_train_mean": 0.02,
                    "rmse": 0.06,
                    "mae": 0.05,
                    "directional_acc": 0.55,
                },
            }
        )
        self.assertEqual(selected, "lgbm")

    def test_model_selection_rejects_rank_model_that_loses_to_mean_baseline(self):
        selected, audit = train_eret.choose_selected_model(
            {
                "lr": {
                    "daily_spearman_corr_mean": 0.10,
                    "daily_spearman_valid_dates": 5,
                    "rmse_skill_vs_train_mean": 0.02,
                    "rmse": 0.07,
                    "mae": 0.05,
                    "directional_acc": 0.60,
                },
                "lgbm": {
                    "daily_spearman_corr_mean": 0.20,
                    "daily_spearman_valid_dates": 5,
                    "rmse_skill_vs_train_mean": -0.01,
                    "rmse": 0.08,
                    "mae": 0.06,
                    "directional_acc": 0.58,
                },
            }
        )
        self.assertEqual(selected, "lr")
        self.assertTrue(audit["selected_model_acceptance_eligible"])


if __name__ == "__main__":
    unittest.main()
