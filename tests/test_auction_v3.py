from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts import run_auction_v3 as run_auction_script  # noqa: E402
from scripts.validate_backfill_artifacts import (  # noqa: E402
    EXPECTED_HISTORY_COLUMNS,
)
from top10decision.auction_v3 import (  # noqa: E402
    AuctionV3Config,
    AuctionV3Engine,
    RunResult,
)


class AuctionV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.codes = [f"6000{i:02d}.SH" for i in range(6)]
        dates = pd.bdate_range("2026-01-05", periods=40)
        self.dates = [d.strftime("%Y%m%d") for d in dates]
        self._write_market()
        self._write_candidates()
        self.config = AuctionV3Config(
            root=self.root,
            min_train_dates=8,
            min_train_rows=40,
            promotion_min_dates=12,
            promotion_min_oos_dates=5,
            backtest_block_dates=4,
            calibration_min_dates=4,
            policy_tuning_min_dates=4,
            policy_min_signal_dates=2,
            policy_min_filled_trades=2,
            policy_max_no_signal_streak=20,
            probability_min_eval_dates=2,
            promotion_min_market_regimes=1,
            order_amount_cny=10_000,
            max_auction_participation=0.02,
            observation_validation_start_date=self.dates[0],
            require_intraday_exit_truth=False,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_force_inactive_engine_uses_only_verified_frozen_snapshot(self) -> None:
        frozen = pd.DataFrame([{"signal_date": "20260805", "ts_code": "600000.SH"}])
        audit = {"source": "forced_frozen_snapshot", "rows": 1}
        with mock.patch.object(
            run_auction_script,
            "load_verified_frozen_history_snapshot",
            return_value=(frozen, audit),
        ) as verified, mock.patch.object(
            AuctionV3Engine,
            "build_history",
            side_effect=AssertionError("live history must be unreachable"),
        ):
            engine = run_auction_script.FreezeAwareAuctionV3Engine(
                self.config,
                {"schema_version": "decision_model_freeze_v2"},
                force_inactive=True,
            )
            result = engine.build_history()

        pd.testing.assert_frame_equal(result, frozen)
        self.assertEqual(engine.model_freeze_history_audit, audit)
        verified.assert_called_once_with(
            self.root,
            {"schema_version": "decision_model_freeze_v2"},
        )

    def test_force_inactive_cli_enforces_pins_engine_and_runtime(self) -> None:
        args = SimpleNamespace(
            root=str(self.root),
            signal_date="20260805",
            force_prediction=False,
            order_amount=100_000.0,
            round_trip_cost_bps=35.0,
            slippage_bps_each_side=5.0,
            force_inactive=True,
        )
        result = RunResult(
            signal_date="20260805",
            prediction_path="prediction.csv",
            backtest_path="backtest.json",
            verification_path="verification.csv",
            current_report_path="current.html",
            verification_report_path="verify.html",
            dashboard_path="dashboard.html",
            model_ready=False,
            promoted=False,
            selected_count=0,
            warnings=[],
        )
        engine = mock.Mock()
        engine.run.return_value = result
        engine.model_freeze_history_audit = {"source": "forced_frozen_snapshot"}
        manifest = {"schema_version": "decision_model_freeze_v2"}
        with mock.patch.object(run_auction_script, "parse_args", return_value=args), mock.patch.object(
            run_auction_script, "load_model_freeze", return_value=manifest
        ), mock.patch.object(
            run_auction_script, "model_freeze_active", return_value=False
        ), mock.patch.object(
            run_auction_script, "validate_pinned_files", return_value={"enforced": True}
        ) as pins, mock.patch.object(
            run_auction_script, "FreezeAwareAuctionV3Engine", return_value=engine
        ) as engine_type, mock.patch.object(
            run_auction_script,
            "validate_runtime_artifacts",
            return_value={"validated": True},
        ) as runtime:
            self.assertEqual(run_auction_script.main(), 0)

        resolved_root = self.root.resolve()
        pins.assert_called_once_with(resolved_root, manifest, force_enforcement=True)
        engine_type.assert_called_once()
        runtime.assert_called_once_with(
            resolved_root,
            manifest,
            check_action_plan=False,
            force_enforcement=True,
        )

    def _write_market(self) -> None:
        previous = {code: 10.0 + idx for idx, code in enumerate(self.codes)}
        for day_index, trade_date in enumerate(self.dates):
            rows = []
            limits = []
            auctions = []
            for code_index, code in enumerate(self.codes):
                pre_close = previous[code]
                open_price = round(pre_close * (1.0 + 0.005 * code_index), 2)
                close_price = round(pre_close * 1.10, 2)
                high = close_price
                low = round(min(open_price, close_price) * 0.99, 2)
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "open": open_price,
                        "high": high,
                        "low": low,
                        "close": close_price,
                        "pre_close": pre_close,
                        "vol": 1_000_000,
                        "amount": 50_000_000,
                        "pct_chg": 10.0,
                    }
                )
                limits.append({"ts_code": code, "trade_date": trade_date, "up_limit": round(pre_close * 1.10, 2), "down_limit": round(pre_close * 0.90, 2)})
                auctions.append({"ts_code": code, "trade_date": trade_date, "vol": 500_000, "price": open_price, "amount": 10_000_000})
                previous[code] = close_price
            path = self.root / "data" / "market" / "raw" / trade_date[:4] / trade_date
            path.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(path / "daily.csv", index=False)
            pd.DataFrame(limits).to_csv(path / "stk_limit.csv", index=False)
            pd.DataFrame(auctions).to_csv(path / "stk_auction.csv", index=False)

    def _write_candidates(self) -> None:
        archive = self.root / "data" / "pred" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        for day_index, trade_date in enumerate(self.dates[:-1]):
            verify_date = self.dates[day_index + 1]
            rows = []
            for rank, code in enumerate(self.codes, start=1):
                rows.append(
                    {
                        "trade_date": trade_date,
                        "verify_date": verify_date,
                        "rank": rank,
                        "ts_code": code,
                        "name": f"测试{rank}",
                        "晋阶": f"{min(rank, 6)}→{min(rank + 1, 7)}",
                        "Probability": 0.8 - rank * 0.07,
                        "StrengthScore": 90 - rank * 5,
                        "ThemeBoost": 0.9 - rank * 0.08,
                        "final_score_v2": 0.9 - rank * 0.07,
                        "intraday_quality_score": 0.9 - rank * 0.05,
                        "intraday_risk_score": rank * 0.04,
                        "intraday_hard_risk_flag": 0,
                        "auction_strength_score": 0.85 - rank * 0.04,
                        "stage_quality_weight": 1.0,
                        "stage_risk_weight": 0.01,
                        "limit_times": min(rank, 6),
                    }
                )
            pd.DataFrame(rows).to_csv(archive / f"pred_source_{trade_date}.csv", index=False)
        latest = archive / f"pred_source_{self.dates[-2]}.csv"
        latest_frame = pd.read_csv(latest)
        pred_root = self.root / "data" / "pred"
        latest_frame.to_csv(pred_root / "pred_source_latest.csv", index=False)

    def test_history_and_walkforward_are_dated(self) -> None:
        legacy_path = self.root / "data" / "market" / "raw" / self.dates[0][:4] / self.dates[0] / "daily.csv"
        legacy_daily = pd.read_csv(legacy_path).drop(columns=["pre_close"])
        legacy_daily.to_csv(legacy_path, index=False)
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        self.assertEqual(tuple(history.columns), EXPECTED_HISTORY_COLUMNS)
        self.assertIn(self.dates[0], set(history["signal_date"]))
        self.assertTrue((history["exit_reason"] == "fixed_open_0930").all())
        self.assertTrue(np.allclose(history["gross_return"], 0.10, atol=0.0015))
        self.assertGreaterEqual(history["signal_date"].nunique(), 30)
        oos, metrics = engine.run_backtest(history)
        self.assertFalse(oos.empty)
        self.assertTrue((oos["oos_train_end"] < oos["signal_date"]).all())
        self.assertGreater(metrics["oos_dates"], 0)
        self.assertIn("stage_focus_all", metrics)
        self.assertIn("top10_oos", metrics)
        self.assertIn("rank_bucket_oos", metrics)
        self.assertIn("path_shadow_policies", metrics)

    def test_backtest_persists_formal_gate_and_shadow_audits(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = pd.DataFrame({"signal_date": [self.dates[0], self.dates[1]]})
        oos = pd.DataFrame(
            [
                {
                    "signal_date": self.dates[0],
                    "ts_code": self.codes[0],
                    "selected": 0,
                    "shadow_selected": 1,
                    "stage_focus": 1,
                    "path_label_code": "ACCELERATION_CONSENSUS",
                },
                {
                    "signal_date": self.dates[1],
                    "ts_code": self.codes[1],
                    "selected": 1,
                    "shadow_selected": 1,
                    "stage_focus": 1,
                    "path_label_code": "WEAK_TO_STRONG",
                },
            ]
        )
        with (
            mock.patch.object(engine, "_walkforward_predictions", return_value=oos),
            mock.patch.object(engine, "_portfolio_metrics", return_value={"promoted": False}),
        ):
            returned, metrics = engine.run_backtest(history)

        persisted = pd.read_csv(self.config.metrics_root / "backtest_trades_latest.csv")
        gate_audit = pd.read_csv(
            self.config.metrics_root / "backtest_gate_audit_latest.csv"
        )
        shadow_audit = pd.read_csv(
            self.config.metrics_root / "backtest_shadow_latest.csv"
        )
        all_stage_audit = pd.read_csv(
            self.config.metrics_root / "backtest_stage_focus_all_latest.csv"
        )
        path_audit = pd.read_csv(
            self.config.metrics_root / "backtest_path_focus_latest.csv"
        )
        self.assertEqual(len(returned), 2)
        self.assertFalse(metrics["promoted"])
        self.assertIn("trade_selector", metrics)
        self.assertFalse(metrics["trade_selector"]["promoted"])
        self.assertFalse(metrics["first_layer_promotion"]["promoted"])
        self.assertEqual(persisted["ts_code"].tolist(), [self.codes[1]])
        self.assertEqual(len(gate_audit), 2)
        self.assertEqual(len(shadow_audit), 2)
        self.assertEqual(len(all_stage_audit), 2)
        self.assertEqual(len(path_audit), 2)

    def test_market_shadow_forces_open_truth_and_discloses_buyability(self) -> None:
        engine = AuctionV3Engine(self.config)
        oos = pd.DataFrame(
            [
                {
                    "signal_date": self.dates[0],
                    "shadow_rank": 1,
                    "market_fill": 0,
                    "shadow_cap_accepted": 0,
                    "net_return": -0.05,
                    "continuation_limit_up_hit": 0,
                },
                {
                    "signal_date": self.dates[1],
                    "shadow_rank": 1,
                    "market_fill": 1,
                    "shadow_cap_accepted": 1,
                    "net_return": 0.03,
                    "continuation_limit_up_hit": 1,
                },
            ]
        )

        market = engine._shadow_policy_metrics(
            oos,
            top_n=1,
            respect_limit=False,
        )
        limited = engine._shadow_policy_metrics(
            oos,
            top_n=1,
            respect_limit=True,
        )

        self.assertEqual(market["execution_mode"], "forced_market_open_truth")
        self.assertEqual(market["filled_trades"], 2)
        self.assertEqual(market["fill_rate"], 1.0)
        self.assertEqual(market["market_buyable_trades"], 1)
        self.assertEqual(market["market_buyable_rate"], 0.5)
        self.assertEqual(limited["filled_trades"], 1)

    def test_all_stage_and_path_cohorts_use_every_candidate(self) -> None:
        engine = AuctionV3Engine(self.config)
        oos = pd.DataFrame(
            [
                {
                    "signal_date": self.dates[0],
                    "stage_focus": 1,
                    "stage": "2→3",
                    "path_label_code": "ACCELERATION_CONSENSUS",
                    "market_fill": 1,
                    "net_return": 0.10,
                    "exit_on_time": 1,
                    "continuation_limit_up_hit": 1,
                },
                {
                    "signal_date": self.dates[0],
                    "stage_focus": 1,
                    "stage": "3→4",
                    "path_label_code": "WEAK_TO_STRONG",
                    "market_fill": 1,
                    "net_return": -0.10,
                    "exit_on_time": 1,
                    "continuation_limit_up_hit": 0,
                },
                {
                    "signal_date": self.dates[1],
                    "stage_focus": 1,
                    "stage": "2→3",
                    "path_label_code": "ACCELERATION_CONSENSUS",
                    "market_fill": 0,
                    "net_return": 0.20,
                    "exit_on_time": 1,
                    "continuation_limit_up_hit": 1,
                },
            ]
        )

        all_stage = engine._cohort_policy_metrics(
            oos,
            oos["stage_focus"].eq(1),
            cohort="all_2to3_and_3to4",
        )
        acceleration = engine._cohort_policy_metrics(
            oos,
            oos["path_label_code"].eq("ACCELERATION_CONSENSUS"),
            cohort="ACCELERATION_CONSENSUS",
        )
        weak = engine._cohort_policy_metrics(
            oos,
            oos["path_label_code"].eq("WEAK_TO_STRONG"),
            cohort="WEAK_TO_STRONG",
        )

        self.assertEqual(all_stage["filled_trades"], 3)
        self.assertAlmostEqual(all_stage["cumulative_return"], 0.20)
        self.assertEqual(acceleration["filled_trades"], 2)
        self.assertAlmostEqual(acceleration["cumulative_return"], 0.32)
        self.assertEqual(weak["filled_trades"], 1)
        self.assertAlmostEqual(weak["cumulative_return"], -0.10)

    def test_top10_caps_each_day_without_padding_and_separates_buyable(self) -> None:
        engine = AuctionV3Engine(self.config)
        rows = []
        for rank in range(1, 13):
            rows.append(
                {
                    "signal_date": self.dates[0],
                    "stage_focus": 1,
                    "shadow_rank": rank,
                    "stage": "2→3",
                    "market_fill": int(rank <= 4),
                    "net_return": rank / 100.0,
                    "continuation_limit_up_hit": int(rank % 2 == 0),
                }
            )
        for rank in range(1, 4):
            rows.append(
                {
                    "signal_date": self.dates[1],
                    "stage_focus": 1,
                    "shadow_rank": rank,
                    "stage": "3→4",
                    "market_fill": int(rank <= 2),
                    "net_return": -rank / 100.0,
                    "continuation_limit_up_hit": int(rank == 1),
                }
            )
        oos = pd.DataFrame(rows)

        metrics = engine._top_n_stage_metrics(
            oos,
            oos["stage_focus"].eq(1),
            top_n=10,
        )

        self.assertEqual(metrics["top_n_cap"], 10)
        self.assertEqual(metrics["padding_policy"], "none")
        self.assertEqual(metrics["candidate_days"], 2)
        self.assertEqual(metrics["days_at_cap"], 1)
        self.assertEqual(metrics["days_below_cap"], 1)
        self.assertEqual(metrics["all_candidates"]["signals"], 13)
        self.assertEqual(metrics["market_buyable_only"]["signals"], 6)
        self.assertEqual(
            metrics["market_buyable_only"]["execution_mode"],
            "market_buyable_at_open_truth",
        )

    def test_prediction_is_frozen_and_has_actionable_price(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        _, metrics = engine.run_backtest(history)
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        self.assertIsNotNone(bundle.fill_calibrator)
        self.assertIn("fill", bundle.classifier_selection)
        if bundle.fill_model is None:
            self.assertEqual(
                bundle.classifier_selection["fill"]["selected"],
                "constant",
            )
            self.assertFalse(
                bundle.classifier_selection["fill"]["model_has_information_gain"]
            )
        signal_date = self.dates[1]
        candidates = engine.load_candidates(signal_date)
        first = engine.build_prediction(signal_date, candidates, bundle, metrics)
        self.assertIn("diagnostic_gap", first.columns)
        self.assertIn("recommended_max_gap", first.columns)
        self.assertIn("recommended_max_price", first.columns)
        self.assertIn("observation_max_price", first.columns)
        self.assertIn("observation_rank", first.columns)
        self.assertIn("predicted_continuation_limit_up_probability", first.columns)
        self.assertIn("predicted_mean_return_lcb", first.columns)
        self.assertIn("predicted_mean_return_ucb", first.columns)
        self.assertIn("market_order_allowed", first.columns)
        self.assertIn("feature_contract", first.columns)
        self.assertIn("path_label", first.columns)
        self.assertIn("path_strength_delta", first.columns)
        self.assertIn("stage_pool_size", first.columns)
        self.assertIn("stage_recent_promotion_rate", first.columns)
        self.assertIn("market_sentiment_score", first.columns)
        self.assertIn("market_sentiment_delta", first.columns)
        self.assertIn("market_sentiment_regime_label", first.columns)
        self.assertIn("market_failed_limit_up_rate", first.columns)
        self.assertIn("market_focus_promotion_rate", first.columns)
        self.assertIn("observation_risk_label", first.columns)
        self.assertIn("trade_rank", first.columns)
        self.assertIn("trade_selected", first.columns)
        self.assertIn("trade_selector_artifact_sha256", first.columns)
        self.assertTrue(
            first["feature_contract"]
            .astype(str)
            .str.contains("STREAK_PATH_SENTIMENT")
            .all()
        )
        self.assertTrue(
            first["feature_contract"]
            .astype(str)
            .str.contains("TOP10_META_SELECTOR_NO_T_LEAKAGE")
            .all()
        )
        selected = first[first["selected"].eq(1)]
        self.assertTrue(selected["recommended_max_price"].notna().all())
        self.assertTrue(
            (selected["recommended_max_price"] < selected["estimated_up_limit"]).all()
        )
        observed = first[first["observation_selected"].eq(1)]
        self.assertFalse(observed.empty)
        self.assertLessEqual(len(observed), self.config.max_observation_candidates)
        self.assertTrue(observed["observation_max_price"].notna().all())
        self.assertTrue(observed["observation_risk_label"].astype(str).str.strip().ne("").all())
        self.assertTrue((observed["observation_max_price"] < observed["estimated_up_limit"]).all())
        self.assertTrue((first["market_order_allowed"] == 0).all())
        dated = self.config.prediction_root / f"pred_{signal_date}.csv"
        before = dated.read_bytes()
        second = engine.build_prediction(signal_date, candidates.iloc[::-1], bundle, metrics)
        self.assertEqual(before, dated.read_bytes())
        self.assertEqual(len(first), len(second))
        self.assertEqual(
            {bundle.model_artifact_sha256},
            set(second["model_artifact_sha256"]),
        )
        retrained = replace(
            bundle,
            model_artifact_sha256="f" * 64,
        )
        with mock.patch.object(
            engine,
            "_prediction_revision_allowed",
            return_value=True,
        ):
            revised = engine.build_prediction(
                signal_date,
                candidates,
                retrained,
                metrics,
            )
        self.assertEqual({"f" * 64}, set(revised["model_artifact_sha256"]))
        self.assertTrue(
            (
                self.config.prediction_root
                / (
                    f"pred_{signal_date}_{self.config.model_version}_"
                    f"{bundle.model_artifact_sha256[:12]}.csv"
                )
            ).exists()
        )
        after_revision = dated.read_bytes()
        post_cutoff_bundle = replace(
            bundle,
            model_artifact_sha256="e" * 64,
        )
        with mock.patch.object(
            engine,
            "_prediction_revision_allowed",
            return_value=False,
        ):
            post_cutoff = engine.build_prediction(
                signal_date,
                candidates,
                post_cutoff_bundle,
                metrics,
            )
        self.assertEqual(after_revision, dated.read_bytes())
        self.assertEqual({"f" * 64}, set(post_cutoff["model_artifact_sha256"]))
        legacy = revised.copy()
        legacy["model_version"] = "legacy_test"
        legacy.to_csv(dated, index=False)
        with mock.patch.object(
            engine,
            "_prediction_revision_allowed",
            return_value=True,
        ):
            migrated = engine.build_prediction(
                signal_date,
                candidates,
                retrained,
                metrics,
            )
        self.assertTrue((self.config.prediction_root / f"pred_{signal_date}_legacy_test.csv").exists())
        self.assertEqual({self.config.model_version}, set(migrated["model_version"]))
        latest_fallback = migrated.copy()
        latest_fallback["model_version"] = "latest_only_frozen"
        latest_fallback.to_csv(
            self.config.prediction_root / "pred_latest.csv",
            index=False,
        )
        dated.unlink()
        with mock.patch.object(
            engine,
            "_prediction_revision_allowed",
            return_value=False,
        ):
            recovered = engine.build_prediction(
                signal_date,
                candidates,
                retrained,
                metrics,
            )
        self.assertTrue(dated.exists())
        self.assertEqual(
            {"latest_only_frozen"},
            set(recovered["model_version"]),
        )
        ledger, _ = engine.settle_predictions()
        self.assertEqual(len(recovered), len(ledger))

    def test_market_sentiment_uses_only_d_and_prior_trading_days(self) -> None:
        signal_date = self.dates[2]
        engine = AuctionV3Engine(self.config)
        context = engine._market_context(signal_date)
        self.assertEqual(context["market_eligible_stock_count"], 6.0)
        self.assertEqual(context["market_limit_up_count"], 6.0)
        self.assertEqual(context["market_limit_down_count"], 0.0)
        self.assertEqual(context["market_failed_limit_up_count"], 0.0)
        self.assertAlmostEqual(context["market_prev_limit_up_mean_return"], 0.10)
        self.assertEqual(context["market_prev_limit_up_sample"], 6.0)
        self.assertAlmostEqual(context["market_2_to_3_promotion_rate"], 1.0)
        self.assertEqual(context["market_2_to_3_promotion_samples"], 6.0)
        self.assertTrue(np.isfinite(context["market_sentiment_score"]))
        self.assertIn(
            context["market_sentiment_regime_label"],
            {"冰点", "修复", "震荡", "发酵", "高潮", "高位分歧", "退潮"},
        )

        future_date = self.dates[3]
        future_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / future_date[:4]
            / future_date
        )
        future = pd.read_csv(future_root / "daily.csv")
        future["pct_chg"] = -10.0
        future["close"] = future["pre_close"] * 0.90
        future.to_csv(future_root / "daily.csv", index=False)
        recomputed = AuctionV3Engine(self.config)._market_context(signal_date)
        for field in (
            "market_sentiment_score",
            "market_sentiment_delta",
            "market_limit_up_count",
            "market_2_to_3_promotion_rate",
            "market_prev_limit_up_mean_return",
        ):
            self.assertAlmostEqual(context[field], recomputed[field])

    def test_official_opening_auction_truth_precedes_legacy_proxy(self) -> None:
        trade_date = self.dates[1]
        code = self.codes[0]
        market_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / trade_date[:4]
            / trade_date
        )
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "close": 12.34,
                    "open": 12.30,
                    "high": 12.35,
                    "low": 12.29,
                    "vol": 100_000,
                    "amount": 1_234_000,
                    "vwap": 12.33,
                }
            ]
        ).to_csv(market_root / "stk_auction_o.csv", index=False)
        engine = AuctionV3Engine(self.config)
        _, source = engine._auction_row(trade_date, code)
        self.assertEqual(source, "tushare_stk_auction_o")
        self.assertAlmostEqual(engine._auction_price(trade_date, code), 12.34)

    def test_market_sentiment_derives_missing_pre_close_per_stock(self) -> None:
        signal_date = self.dates[2]
        daily_path = (
            self.root
            / "data"
            / "market"
            / "raw"
            / signal_date[:4]
            / signal_date
            / "daily.csv"
        )
        daily = pd.read_csv(daily_path).drop(columns=["pre_close"])
        daily.to_csv(daily_path, index=False)
        context = AuctionV3Engine(self.config)._market_context(signal_date)
        self.assertTrue(
            np.isfinite(context["market_prev_limit_up_open_gap_mean"])
        )
        self.assertTrue(np.isfinite(context["market_sentiment_score"]))

    def test_market_sentiment_lists_top_ten_limit_up_industries(self) -> None:
        signal_date = self.dates[2]
        market_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / signal_date[:4]
            / signal_date
        )
        extra_codes = [f"60199{index}.SH" for index in range(6)]
        daily = pd.read_csv(market_root / "daily.csv")
        daily = pd.concat(
            [
                daily,
                pd.DataFrame(
                    [
                        {
                            "ts_code": code,
                            "trade_date": signal_date,
                            "open": 10.2,
                            "high": 11.0,
                            "low": 10.1,
                            "close": 11.0,
                            "pre_close": 10.0,
                            "vol": 1_000_000,
                            "amount": 30_000_000,
                            "pct_chg": 10.0,
                        }
                        for code in extra_codes
                    ]
                ),
            ],
            ignore_index=True,
        )
        daily.to_csv(market_root / "daily.csv", index=False)
        limits = pd.read_csv(market_root / "stk_limit.csv")
        limits = pd.concat(
            [
                limits,
                pd.DataFrame(
                    [
                        {
                            "ts_code": code,
                            "trade_date": signal_date,
                            "up_limit": 11.0,
                            "down_limit": 9.0,
                        }
                        for code in extra_codes
                    ]
                ),
            ],
            ignore_index=True,
        )
        limits.to_csv(market_root / "stk_limit.csv", index=False)
        pd.DataFrame(
            {
                "ts_code": [*self.codes, *extra_codes],
                "trade_date": signal_date,
                "industry": [
                    "电力",
                    "电力",
                    "电网设备",
                    "机械",
                    "医药",
                    "化工",
                    "房地产",
                    "汽车",
                    "传媒",
                    "通信",
                    "钢铁",
                    "煤炭",
                ],
                "amount": [50_000_000] * 12,
                "open_times": [0] * 12,
            }
        ).to_csv(market_root / "limit_list_d.csv", index=False)

        engine = AuctionV3Engine(self.config)
        raw = engine._market_sentiment_raw(signal_date)
        leaders = raw["_limit_up_industry_top10"]
        self.assertEqual(len(leaders), 10)
        self.assertEqual(
            leaders[0],
            {
                "rank": 1,
                "industry": "电力",
                "limit_up_count": 2,
                "share": 2 / 12,
            },
        )
        self.assertEqual(
            [item["industry"] for item in leaders[1:]],
            sorted(item["industry"] for item in leaders[1:]),
        )
        self.assertTrue(all(item["rank"] == index for index, item in enumerate(leaders, 1)))
        self.assertEqual(raw["_limit_up_industry_top5"], leaders[:5])
        snapshot = engine.market_close_display_snapshot(signal_date)
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["scope"], "all_a_share_daily_close")
        self.assertEqual(snapshot["stock_count"], 12)
        self.assertEqual(snapshot["limit_up_count"], 12)
        self.assertEqual(snapshot["industry_top10"], leaders)
        self.assertEqual(snapshot["industry_counts"]["电力"], 2)

    def test_continuation_model_audits_sentiment_ablation(self) -> None:
        engine = AuctionV3Engine(self.config)
        bundle = engine.fit_models(engine.build_history())
        self.assertIsNotNone(bundle)
        selection = bundle.classifier_selection["continuation_limit_up"]
        ablation = selection["ablation"]
        self.assertIn(
            selection["feature_set"],
            {
                "baseline_without_streak_path_or_sentiment",
                "streak_path_and_cohort",
                "streak_path_cohort_and_market_sentiment",
            },
        )
        self.assertIn("streak_path_cohort_and_sentiment_brier", ablation)
        self.assertIn("sentiment_brier_improvement", ablation)
        self.assertIn("sentiment_daily_win_rate", ablation)
        self.assertIsInstance(ablation["sentiment_selected"], bool)
        if ablation["sentiment_selected"]:
            self.assertIn(
                "market_sentiment_score",
                bundle.continuation_features,
            )

    def test_opening_limit_up_that_breaks_later_is_not_a_confirmed_fill(self) -> None:
        trade_date = self.dates[5]
        code = self.codes[0]
        market_root = self.root / "data" / "market" / "raw" / trade_date[:4] / trade_date
        daily = pd.read_csv(market_root / "daily.csv")
        limits = pd.read_csv(market_root / "stk_limit.csv")
        auctions = pd.read_csv(market_root / "stk_auction.csv")
        up_limit = float(limits.loc[limits["ts_code"].eq(code), "up_limit"].iloc[0])
        daily.loc[daily["ts_code"].eq(code), ["open", "high", "low", "close"]] = [
            up_limit,
            up_limit,
            round(up_limit * 0.98, 2),
            round(up_limit * 0.99, 2),
        ]
        auctions.loc[auctions["ts_code"].eq(code), "price"] = up_limit
        daily.to_csv(market_root / "daily.csv", index=False)
        auctions.to_csv(market_root / "stk_auction.csv", index=False)

        fill, reason = AuctionV3Engine(self.config)._market_buyable(trade_date, code)
        self.assertEqual(fill, 0)
        self.assertEqual(reason, "opening_auction_limit_up_unconfirmed")

    def test_observation_truth_uses_market_open_even_above_displayed_limit(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        _, metrics = engine.run_backtest(history)
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        signal_date = self.dates[1]
        prediction = engine.build_prediction(
            signal_date,
            engine.load_candidates(signal_date),
            bundle,
            metrics,
        )
        observed = prediction["observation_selected"].eq(1)
        self.assertTrue(observed.any())
        prediction["observation_max_price"] = 0.01
        prediction["recommended_max_price"] = 0.01
        prediction["generated_at_utc"] = "2026-01-01T00:00:00+00:00"
        dated = self.config.prediction_root / f"pred_{signal_date}.csv"
        prediction.to_csv(dated, index=False)

        with mock.patch.object(
            engine,
            "_market_buyable",
            return_value=(0, "diagnostic_only"),
        ):
            verified = engine._verify_observation_prediction_file(
                dated,
                engine.market_dates(),
            )

        self.assertFalse(verified.empty)
        self.assertTrue(verified["observation_fill"].eq(1).all())
        self.assertTrue(verified["observation_limit_accept"].eq(0).all())
        self.assertTrue(verified["observation_price_vs_cap"].gt(0).all())
        self.assertTrue(verified["market_buyable_diagnostic"].eq(0).all())
        self.assertEqual(
            set(verified["observation_fill_reason"]),
            {"filled_market_at_open_proxy"},
        )
        self.assertEqual(
            set(verified["observation_execution_mode"]),
            {"market_at_open_proxy"},
        )

    def test_market_table_rejects_mislabeled_trade_date_partition(self) -> None:
        trade_date = self.dates[-1]
        daily_path = (
            self.root
            / "data"
            / "market"
            / "raw"
            / trade_date[:4]
            / trade_date
            / "daily.csv"
        )
        stale = pd.read_csv(daily_path)
        stale["trade_date"] = self.dates[-2]
        stale.to_csv(daily_path, index=False)

        engine = AuctionV3Engine(self.config)
        self.assertTrue(engine.market_table(trade_date, "daily").empty)
        snapshot = engine.market_close_display_snapshot(trade_date)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["status"], "DAILY_CLOSE_UNAVAILABLE")

    def test_big_loss_probability_is_a_hard_veto(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        bundle.selection_policy = {
            "version": "test_nested_policy",
            "ready": True,
            "max_positions": 1,
            "thresholds": {
                "min_fill_probability": 0.0,
                "min_exit_probability": 0.0,
                "max_big_loss_probability": 0.15,
                "min_mean_return_lcb": -1.0,
                "min_conservative_ev": -1.0,
                "min_selection_score": -1.0,
            },
        }
        bundle.loss_model = None
        bundle.loss_constant = 0.16
        focus_row = history[
            pd.to_numeric(history["limit_times"], errors="coerce").round().isin((2.0, 3.0))
        ].iloc[-1]
        score = engine._score_candidate_at_gaps(focus_row, bundle)
        self.assertIsNotNone(score)
        self.assertEqual(score["risk_gate_pass"], 0)
        self.assertEqual(score["model_reason"], "big_loss_probability_exceeds_cap")
        self.assertTrue(np.isnan(score["recommended_max_gap"]))

    def test_uninformative_profit_probability_cannot_veto_shadow_policy(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        bundle.selection_policy = {
            "version": "test_nested_policy",
            "ready": True,
            "max_positions": 1,
            "thresholds": {
                "min_fill_probability": 0.0,
                "min_exit_probability": 0.0,
                "max_big_loss_probability": 1.0,
                "min_mean_return_lcb": -1.0,
                "min_conservative_ev": -1.0,
                "min_selection_score": -1.0,
            },
        }
        bundle.return_model = None
        bundle.return_constant = 0.02
        bundle.calibration_bias = 0.0
        bundle.profit_model = None
        bundle.profit_constant = 0.01
        bundle.loss_model = None
        bundle.loss_constant = 0.0
        bundle.fill_model = None
        bundle.fill_constant = 1.0
        bundle.exit_model = None
        bundle.exit_constant = 1.0
        focus_row = history[
            pd.to_numeric(
                history["limit_times"],
                errors="coerce",
            ).round().isin((2.0, 3.0))
        ].iloc[-1]
        score = engine._score_candidate_at_gaps(focus_row, bundle)
        self.assertIsNotNone(score)
        self.assertEqual(score["risk_gate_pass"], 1)
        self.assertEqual(score["model_reason"], "ok")
        self.assertAlmostEqual(score["predicted_profit_probability"], 0.01)

    def test_policy_batch_scoring_matches_single_candidate_math(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        sample = history.tail(12).copy()
        batch = engine._score_policy_tuning_candidates(sample, bundle)
        self.assertEqual(len(batch), len(sample))
        for _, row in sample.iterrows():
            policy_row = row.copy()
            policy_row["_policy_tuning"] = 1
            single = engine._score_candidate_at_gaps(
                policy_row,
                bundle,
                apply_policy=False,
            )
            self.assertIsNotNone(single)
            matched = batch[
                batch["signal_date"].astype(str).eq(
                    str(row["signal_date"])
                )
                & batch["ts_code"].astype(str).eq(str(row["ts_code"]))
            ]
            self.assertEqual(len(matched), 1)
            actual = matched.iloc[0]
            for field in (
                "diagnostic_gap",
                "predicted_net_return",
                "predicted_mean_return_lcb",
                "predicted_big_loss_probability",
                "predicted_continuation_limit_up_probability",
                "predicted_fill_probability",
                "predicted_exit_probability",
                "conservative_ev",
                "selection_score",
            ):
                self.assertAlmostEqual(
                    float(actual[field]),
                    float(single[field]),
                    places=10,
                )

    def test_general_batch_scoring_matches_single_candidate_math(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        signal_date = history["signal_date"].astype(str).iloc[-1]
        sample = history[
            history["signal_date"].astype(str).eq(signal_date)
        ].copy()
        batch = engine.score_candidates(
            sample,
            bundle,
            apply_policy=False,
        )
        self.assertEqual(len(batch), len(sample))
        for _, row in sample.iterrows():
            single = engine._score_candidate_at_gaps(
                row,
                bundle,
                apply_policy=False,
            )
            self.assertIsNotNone(single)
            matched = batch[
                batch["signal_date"].astype(str).eq(
                    str(row["signal_date"])
                )
                & batch["ts_code"].astype(str).eq(str(row["ts_code"]))
            ]
            self.assertEqual(len(matched), 1)
            actual = matched.iloc[0]
            for field in (
                "diagnostic_gap",
                "predicted_net_return",
                "predicted_return_lcb",
                "predicted_return_ucb",
                "predicted_mean_return_lcb",
                "predicted_mean_return_ucb",
                "predicted_profit_probability",
                "predicted_big_loss_probability",
                "predicted_continuation_limit_up_probability",
                "predicted_fill_probability",
                "predicted_exit_probability",
                "conservative_ev",
                "selection_score",
            ):
                self.assertAlmostEqual(
                    float(actual[field]),
                    float(single[field]),
                    places=10,
                )

    def test_formal_gate_rejects_candidates_outside_focus_stages(self) -> None:
        engine = AuctionV3Engine(self.config)
        history = engine.build_history()
        bundle = engine.fit_models(history)
        self.assertIsNotNone(bundle)
        outside_focus = history[
            ~pd.to_numeric(history["limit_times"], errors="coerce").round().isin((2.0, 3.0))
        ].iloc[-1]
        score = engine._score_candidate_at_gaps(outside_focus, bundle)
        self.assertIsNotNone(score)
        self.assertEqual(score["stage_focus"], 0)
        self.assertEqual(score["risk_gate_pass"], 0)
        self.assertEqual(
            score["model_reason"],
            "outside_stage_2_to_3_3_to_4_focus",
        )
        self.assertTrue(np.isnan(score["recommended_max_gap"]))

    def test_stage_is_derived_from_consecutive_limit_up_closes(self) -> None:
        engine = AuctionV3Engine(self.config)
        signal_date = self.dates[1]
        self.assertEqual(
            engine._consecutive_limit_up_count(signal_date, self.codes[0]),
            2,
        )
        current = engine._current_base(signal_date, engine.load_candidates(signal_date))
        row = current.loc[current["ts_code"].eq(self.codes[0])].iloc[0]
        self.assertEqual(row["stage"], "2→3")
        self.assertEqual(int(row["limit_times"]), 2)

    def test_streak_path_detects_weak_to_strong_without_future_data(self) -> None:
        code = self.codes[0]
        weak_date, strong_date = self.dates[0], self.dates[1]
        for trade_date, first_time, last_time, open_times, seal_amount, turnover in (
            (weak_date, 143000, 145000, 4, 100_000, 20.0),
            (strong_date, 93500, 93500, 0, 10_000_000, 8.0),
        ):
            market_root = (
                self.root
                / "data"
                / "market"
                / "raw"
                / trade_date[:4]
                / trade_date
            )
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "limit_type": "U",
                        "first_time": first_time,
                        "last_time": last_time,
                        "open_times": open_times,
                        "amount": 50_000_000,
                        "seal_amount": seal_amount,
                        "fd_amount": seal_amount,
                    }
                ]
            ).to_csv(market_root / "limit_list_d.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "turnover_rate": turnover,
                    }
                ]
            ).to_csv(market_root / "daily_basic.csv", index=False)

        strong_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / strong_date[:4]
            / strong_date
        )
        strong_daily = pd.read_csv(strong_root / "daily.csv")
        row_mask = strong_daily["ts_code"].eq(code)
        strong_daily.loc[row_mask, "open"] = (
            strong_daily.loc[row_mask, "pre_close"] * 1.05
        ).round(2)
        strong_daily.to_csv(strong_root / "daily.csv", index=False)

        features = AuctionV3Engine(self.config)._streak_path_features(
            strong_date,
            code,
            self.dates,
        )
        self.assertEqual(features["path_label_code"], "WEAK_TO_STRONG")
        self.assertEqual(features["path_label"], "弱转强")
        self.assertGreater(features["path_strength_delta"], 0.12)
        self.assertLess(features["path_first_seal_slope"], 0)
        self.assertEqual(features["path_weak_to_strong"], 1.0)

    def test_authoritative_limit_list_restores_candidates_missing_from_old_ranking(self) -> None:
        signal_date = self.dates[10]
        market_root = self.root / "data" / "market" / "raw" / signal_date[:4] / signal_date
        daily = pd.read_csv(market_root / "daily.csv")
        limits = pd.read_csv(market_root / "stk_limit.csv")
        detail = daily[["ts_code", "trade_date", "close"]].merge(
            limits[["ts_code", "up_limit"]],
            on="ts_code",
            how="inner",
        )
        detail["name"] = [f"权威{i}" for i in range(len(detail))]
        detail["limit_type"] = "U"
        detail["open_times"] = 0
        detail["fd_amount"] = 1_000_000
        detail["first_time"] = 100000
        detail["last_time"] = 100000
        detail["seal_amount"] = 1_000_000
        detail.to_csv(market_root / "limit_list_d.csv", index=False)

        source_path = self.root / "data" / "pred" / "archive" / f"pred_source_{signal_date}.csv"
        source = pd.read_csv(source_path)
        missing_code = source.iloc[-1]["ts_code"]
        source.iloc[:-1].to_csv(source_path, index=False)

        candidates = AuctionV3Engine(self.config).load_candidates(signal_date)
        self.assertEqual(set(candidates["ts_code"]), set(self.codes))
        self.assertIn(missing_code, set(candidates["ts_code"]))

    def test_true_price_verification_and_reports(self) -> None:
        # This synthetic fixture predates the production public-statistics
        # cutover. Override only its reporting window so the test continues to
        # exercise truth settlement rather than the D28 governance boundary.
        fixture_config = replace(
            self.config,
            public_statistics_start_signal_date=self.dates[0],
            forward_shadow_start_signal_date=self.dates[0],
            top1_promotion_start_signal_date=self.dates[0],
        )
        engine = AuctionV3Engine(fixture_config)
        signal_date = self.dates[1]
        history = engine.build_history()
        oos, metrics = engine.run_backtest(history)
        prediction = engine.build_prediction(signal_date, engine.load_candidates(signal_date), engine.fit_models(history), metrics)
        ledger, cumulative = engine.settle_predictions()
        self.assertFalse(ledger.empty)
        self.assertIn("actual_buy_price", ledger.columns)
        self.assertIn("actual_exit_price", ledger.columns)
        self.assertIn("truth_source", ledger.columns)
        self.assertGreaterEqual(cumulative.get("frozen_predictions", 0), 1)
        self.assertIn("shadow_selected", prediction.columns)
        self.assertGreaterEqual(int(prediction["shadow_selected"].sum()), 1)
        observation, observation_metrics = engine.settle_observations()
        self.assertFalse(observation.empty)
        self.assertIn("market_daily_return", observation.columns)
        self.assertIn("continuation_limit_up_hit", observation.columns)
        self.assertIn("actual_net_return", observation.columns)
        self.assertIn("prediction_timing_status", observation.columns)
        self.assertLessEqual(
            int(observation.groupby("expected_buy_date").size().max()),
            self.config.max_observation_candidates,
        )
        self.assertGreater(observation_metrics.get("t_validated_rows", 0), 0)
        self.assertEqual(
            observation_metrics.get("performance_scope"),
            "premarket_valid_predictions_only",
        )
        self.assertGreater(
            observation_metrics.get("retrospective_truth_rows", 0),
            0,
        )
        from top10decision.auction_v3.reporting import write_reports

        paths = write_reports(
            self.config,
            prediction=prediction,
            ledger=ledger,
            backtest_trades=oos,
            backtest_metrics=metrics,
            cumulative_metrics=cumulative,
        )
        for path in paths.values():
            self.assertTrue(Path(path).exists())
            self.assertIn("Decision", Path(path).read_text(encoding="utf-8"))
        self.assertIn("竞价", Path(paths["current"]).read_text(encoding="utf-8"))

    def test_observation_timing_audit_uses_t_0925_deadline(self) -> None:
        valid = AuctionV3Engine._prediction_timing_status(
            "2026-07-22T13:12:08+00:00",
            "20260723",
        )
        late = AuctionV3Engine._prediction_timing_status(
            "2026-07-23T11:13:00+00:00",
            "20260723",
        )
        self.assertEqual(valid[0:2], ("PREMARKET_VALID", 1))
        self.assertEqual(late[0:2], ("RETROSPECTIVE_LATE_GENERATION", 0))
        self.assertEqual(valid[2], "2026-07-23T01:25:00+00:00")

    def test_forward_shadow_metrics_start_at_public_d28_cutover(self) -> None:
        rows = [
            {
                "signal_date": "20260827",
                "expected_buy_date": "20260828",
                "expected_exit_date": "20260831",
                "ts_code": "600001.SH",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 1,
                "trade_rank": 1,
                "validation_status": "FINAL_VERIFIED",
                "actual_net_return": -0.50,
                "continuation_limit_up_hit": 0,
                "market_buyable_diagnostic": 1,
            },
            {
                "signal_date": "20260828",
                "expected_buy_date": "20260831",
                "expected_exit_date": "20260901",
                "ts_code": "600002.SH",
                "name": "甲",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 1,
                "trade_rank": 1,
                "validation_status": "FINAL_VERIFIED",
                "actual_net_return": 0.10,
                "continuation_limit_up_hit": 1,
                "market_buyable_diagnostic": 1,
            },
            {
                "signal_date": "20260831",
                "expected_buy_date": "20260901",
                "expected_exit_date": "20260902",
                "ts_code": "600003.SH",
                "name": "乙",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 1,
                "trade_rank": 1,
                "validation_status": "FINAL_VERIFIED",
                "actual_net_return": 0.05,
                "continuation_limit_up_hit": 1,
                "market_buyable_diagnostic": 1,
            },
            {
                "signal_date": "20260831",
                "expected_buy_date": "20260901",
                "expected_exit_date": "20260902",
                "ts_code": "600004.SH",
                "name": "丙",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 1,
                "trade_rank": 2,
                "validation_status": "FINAL_VERIFIED",
                "actual_net_return": -0.02,
                "continuation_limit_up_hit": 0,
                "market_buyable_diagnostic": 1,
            },
            {
                "signal_date": "20260901",
                "expected_buy_date": "20260902",
                "expected_exit_date": "20260903",
                "ts_code": "600005.SH",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 0,
                "validation_status": "PENDING_T",
            },
            {
                "signal_date": "20260902",
                "expected_buy_date": "20260903",
                "expected_exit_date": "20260904",
                "ts_code": "600006.SH",
                "name": "丁",
                "prediction_timing_valid": 1,
                "trade_shadow_selected": 1,
                "trade_rank": 1,
                "validation_status": "PENDING_T1",
                "continuation_limit_up_hit": 0,
                "market_buyable_diagnostic": 0,
            },
            {
                "signal_date": "20260903",
                "expected_buy_date": "20260904",
                "expected_exit_date": "20260907",
                "ts_code": "600007.SH",
                "prediction_timing_valid": 0,
                "trade_shadow_selected": 1,
                "trade_rank": 1,
                "validation_status": "FINAL_VERIFIED",
                "actual_net_return": 0.80,
            },
        ]

        metrics = AuctionV3Engine(self.config)._forward_shadow_metrics(
            pd.DataFrame(rows)
        )

        self.assertEqual(metrics["start_signal_date"], "20260828")
        self.assertEqual(metrics["observed_signal_dates"], 4)
        self.assertEqual(metrics["shadow_signal_dates"], 3)
        self.assertEqual(metrics["shadow_entries"], 4)
        self.assertEqual(metrics["final_verified_trades"], 3)
        self.assertEqual(metrics["pending_t1_entries"], 1)
        self.assertEqual(metrics["matured_portfolio_dates"], 2)
        self.assertEqual(metrics["longest_no_signal_streak"], 1)
        self.assertAlmostEqual(metrics["mean_final_net_return"], 0.13 / 3)
        self.assertAlmostEqual(metrics["profit_factor"], 7.5)
        self.assertAlmostEqual(metrics["equal_slot_cumulative_return"], 0.1165)
        self.assertEqual(metrics["equal_slot_max_drawdown"], 0.0)
        self.assertFalse(metrics["sample_sufficient"])
        self.assertEqual(metrics["latest_signal_date"], "20260902")
        self.assertEqual(metrics["rows"][0]["signal_date"], "20260902")

    def test_public_metrics_exclude_history_before_signal_d28(self) -> None:
        rows = [
            {
                "signal_date": "20260827",
                "expected_buy_date": "20260828",
                "ts_code": "600001.SH",
                "observation_rank": 1,
                "promotion_rank": 1,
                "prediction_timing_valid": 1,
                "prediction_timing_status": "PREMARKET_VALID",
                "validation_status": "T_VERIFIED_FILLED",
                "continuation_limit_up_hit": 1,
                "stage_transition": "2→3",
            },
            {
                "signal_date": "20260828",
                "expected_buy_date": "20260831",
                "ts_code": "600002.SH",
                "observation_rank": 2,
                "promotion_rank": 1,
                "prediction_timing_valid": 1,
                "prediction_timing_status": "PREMARKET_VALID",
                "validation_status": "T_VERIFIED_FILLED",
                "continuation_limit_up_hit": 1,
                "stage_transition": "2→3",
            },
            {
                "signal_date": "20260828",
                "expected_buy_date": "20260831",
                "ts_code": "600003.SH",
                "observation_rank": 1,
                "promotion_rank": 2,
                "prediction_timing_valid": 1,
                "prediction_timing_status": "PREMARKET_VALID",
                "validation_status": "T_VERIFIED_FILLED",
                "continuation_limit_up_hit": 0,
                "stage_transition": "2→3",
            },
            {
                "signal_date": "20260831",
                "expected_buy_date": "20260901",
                "ts_code": "600004.SH",
                "observation_rank": 3,
                "promotion_rank": 1,
                "prediction_timing_valid": 1,
                "prediction_timing_status": "PREMARKET_VALID",
                "validation_status": "T_VERIFIED_FILLED",
                "continuation_limit_up_hit": 0,
                "stage_transition": "3→4",
            },
            {
                "signal_date": "20260901",
                "expected_buy_date": "20260902",
                "ts_code": "600005.SH",
                "observation_rank": 1,
                "promotion_rank": 1,
                "prediction_timing_valid": 1,
                "prediction_timing_status": "PREMARKET_VALID",
                "validation_status": "PENDING_T",
                "continuation_limit_up_hit": 1,
                "stage_transition": "2→3",
            },
        ]

        ledger = pd.DataFrame(rows).assign(
            market_daily_return=0.0,
            observation_fill=1,
            observation_limit_accept=1,
            observation_price_vs_cap=0.0,
            observation_t_return=0.0,
            actual_net_return=np.nan,
            truth_source="tushare_stk_auction_o",
        )
        metrics = AuctionV3Engine(self.config)._observation_metrics(ledger)
        top1 = metrics["top1_continuation"]

        self.assertEqual(metrics["public_start_signal_date"], "20260828")
        self.assertTrue(metrics["historical_ledger_retained"])
        self.assertEqual(metrics["excluded_pre_cutover_rows"], 1)
        self.assertEqual(metrics["observation_rows"], 4)
        self.assertEqual(top1["start_signal_date"], "20260828")
        self.assertEqual(top1["rank_field"], "promotion_rank")
        self.assertEqual(top1["rank_value"], 1)
        self.assertEqual(top1["samples"], 2)
        self.assertEqual(top1["hits"], 1)
        self.assertEqual(top1["hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
