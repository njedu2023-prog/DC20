from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.run_v2 import _apply_ev_upgrade_v1  # noqa: E402
from scripts.backfill_decision_v11_history import (  # noqa: E402
    _completed_open_dates,
    _latest_target_dates,
    _retry_frame,
)
from scripts.build_eret_truth import infer_eret_label  # noqa: E402
from scripts.build_fill_truth import infer_fill_label  # noqa: E402
from scripts.resolve_sample_maturity import (  # noqa: E402
    resolve_sample_maturity_rows,
    resolve_trade_calendar,
)
from scripts.sync_tushare_minute import _collect_codes  # noqa: E402
from scripts.validate_io_contract import _allows_unpromoted_no_trade  # noqa: E402
from top10decision.data.tushare_minute import (  # noqa: E402
    TushareClient,
    opening_auction_price_from_snapshot,
)
from top10decision.auction_v3.config import (  # noqa: E402
    TARGET_HISTORY_DATES,
    TARGET_INDEPENDENT_OOS_DATES,
    WALKFORWARD_WARMUP_DATES,
)
from top10decision.decision.action_plan import build_action_plan  # noqa: E402
from top10decision.decision.contracts import (  # noqa: E402
    ACTUAL_ORDER_FILL_OBSERVED_COLUMN,
    ACTUAL_ORDER_FILL_TARGET_COLUMN,
    EXIT_LATEST_TIME,
    EXIT_STOP_LOSS_PCT,
    EXIT_TAKE_PROFIT_PCT,
    PFILL_EXECUTION_CONTRACT,
    PREOPEN_AUCTION_GATE_AUDIT,
    PUBLIC_MARKET_BUYABLE_TARGET_COLUMN,
)
from top10decision.decision.eligibility import filter_standard_limit_universe  # noqa: E402
from top10decision.decision.exit_policy import simulate_tplus1_exit  # noqa: E402
from top10decision.decision.observation import rank_observation_rows  # noqa: E402
from top10decision.writers import io_contract  # noqa: E402


class DecisionCalendarContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_calendar_path = io_contract.TRADE_CALENDAR_PATH
        io_contract.TRADE_CALENDAR_PATH = Path(self.temp.name) / "missing_calendar.csv"
        io_contract._load_exchange_calendar.cache_clear()

    def tearDown(self) -> None:
        io_contract.TRADE_CALENDAR_PATH = self.original_calendar_path
        io_contract._load_exchange_calendar.cache_clear()
        self.temp.cleanup()

    def test_official_2026_holidays_are_skipped(self) -> None:
        self.assertEqual(io_contract.choose_exec_date("20260430", "20260501"), "20260506")
        self.assertEqual(io_contract.choose_exit_date("20260506"), "20260507")
        self.assertEqual(io_contract.choose_exec_date("20260618", "20260619"), "20260622")
        self.assertEqual(io_contract.choose_exit_date("20260622"), "20260623")

    def test_synced_calendar_is_authoritative_and_fails_closed(self) -> None:
        calendar_path = Path(self.temp.name) / "trade_cal_sse.csv"
        pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": "20260720", "is_open": 1},
                {"exchange": "SSE", "cal_date": "20260721", "is_open": 0},
                {"exchange": "SSE", "cal_date": "20260722", "is_open": 1},
            ]
        ).to_csv(calendar_path, index=False)
        io_contract.TRADE_CALENDAR_PATH = calendar_path
        io_contract._load_exchange_calendar.cache_clear()

        self.assertEqual(io_contract.choose_exec_date("20260720", "20260721"), "20260722")
        with self.assertRaises(RuntimeError):
            io_contract.is_a_share_trading_day("20260723")

    def test_maturity_uses_only_explicit_exchange_calendar(self) -> None:
        calendar_path = Path(self.temp.name) / "strict_calendar.csv"
        days = [
            ("20260430", 1),
            ("20260501", 0),
            ("20260502", 0),
            ("20260503", 0),
            ("20260504", 0),
            ("20260505", 0),
            ("20260506", 1),
            ("20260507", 1),
        ]
        pd.DataFrame(
            [{"exchange": "SSE", "cal_date": day, "is_open": flag} for day, flag in days]
        ).to_csv(calendar_path, index=False)

        calendar = resolve_trade_calendar(
            raw_trade_dates=["20260430", "20260502", "20260506", "20260507"],
            candidate_trade_dates=["20260430"],
            current_run_date="20260507",
            trade_calendar_file=calendar_path,
        )
        rows = resolve_sample_maturity_rows(
            current_run_date="20260507",
            all_trade_dates_from_raw=["20260430", "20260502", "20260506", "20260507"],
            candidate_trade_dates=["20260430"],
            trade_calendar_dates=calendar,
        )
        self.assertEqual(rows[0].exec_date, "20260506")
        self.assertEqual(rows[0].target_date, "20260507")
        self.assertEqual(rows[0].FULLY_READY, 1)
        self.assertNotIn("20260502", calendar)

    def test_maturity_calendar_gap_fails_closed(self) -> None:
        calendar_path = Path(self.temp.name) / "calendar_with_gap.csv"
        pd.DataFrame(
            [
                {"cal_date": "20260430", "is_open": 1},
                {"cal_date": "20260501", "is_open": 0},
                {"cal_date": "20260502", "is_open": 0},
                {"cal_date": "20260503", "is_open": 0},
                # 20260504 intentionally absent.
                {"cal_date": "20260505", "is_open": 0},
                {"cal_date": "20260506", "is_open": 1},
                {"cal_date": "20260507", "is_open": 1},
            ]
        ).to_csv(calendar_path, index=False)
        with self.assertRaisesRegex(RuntimeError, "存在缺口"):
            resolve_trade_calendar(
                raw_trade_dates=["20260430", "20260506", "20260507"],
                candidate_trade_dates=["20260430"],
                current_run_date="20260507",
                trade_calendar_file=calendar_path,
            )

    def test_two_year_oos_backfill_keeps_training_warmup(self) -> None:
        open_dates = [
            value.strftime("%Y%m%d")
            for value in pd.bdate_range("2023-01-02", periods=750)
        ]
        target_window, missing = _latest_target_dates(
            open_dates,
            {open_dates[102], open_dates[103]},
            max_missing_dates=3,
        )

        self.assertEqual(TARGET_INDEPENDENT_OOS_DATES, 500)
        self.assertEqual(WALKFORWARD_WARMUP_DATES, 200)
        self.assertEqual(TARGET_HISTORY_DATES, 700)
        self.assertEqual(len(target_window), 700)
        self.assertEqual(target_window[0], open_dates[42])
        self.assertEqual(target_window[-1], open_dates[-9])
        self.assertEqual(missing, open_dates[42:45])

    def test_intraday_backfill_excludes_unfinished_current_session(self) -> None:
        dates = ["20260724", "20260727", "20260728"]
        before_close = datetime(
            2026,
            7,
            28,
            13,
            30,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        after_ready = datetime(
            2026,
            7,
            28,
            21,
            15,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(
            _completed_open_dates(dates, now=before_close),
            dates[:-1],
        )
        self.assertEqual(
            _completed_open_dates(dates, now=after_ready),
            dates,
        )

    def test_backfill_retries_transient_empty_required_endpoint(self) -> None:
        responses = [
            pd.DataFrame(),
            pd.DataFrame([{"ts_code": "600000.SH"}]),
        ]

        with mock.patch(
            "scripts.backfill_decision_v11_history.time.sleep"
        ) as sleep:
            result = _retry_frame(
                lambda: responses.pop(0),
                label="daily:20260727",
                required=True,
            )

        self.assertEqual(result["ts_code"].tolist(), ["600000.SH"])
        sleep.assert_called_once_with(2.0)


class DecisionExecutionSemanticsContractTests(unittest.TestCase):
    def test_public_buyability_is_not_claimed_as_actual_order_fill(self) -> None:
        self.assertEqual(
            PUBLIC_MARKET_BUYABLE_TARGET_COLUMN,
            "y_public_market_buyable",
        )
        self.assertEqual(ACTUAL_ORDER_FILL_TARGET_COLUMN, "actual_order_fill")
        self.assertEqual(
            ACTUAL_ORDER_FILL_OBSERVED_COLUMN,
            "actual_order_fill_observed",
        )
        self.assertIn("public_market_fillability_proxy", PFILL_EXECUTION_CONTRACT)

    def test_preopen_microstructure_gate_fails_closed_without_snapshots(self) -> None:
        self.assertFalse(PREOPEN_AUCTION_GATE_AUDIT["enabled"])
        self.assertEqual(
            PREOPEN_AUCTION_GATE_AUDIT["decision_deadline"],
            "T 09:24:50 Asia/Shanghai",
        )
        missing = PREOPEN_AUCTION_GATE_AUDIT["required_missing_fields"]
        self.assertIn("indicative_match_price", missing)
        self.assertIn("order_imbalance", missing)
        self.assertIn("cancel_pressure_0920_092450", missing)


class DecisionStrictSemanticContractTests(unittest.TestCase):
    def test_unpromoted_v8_no_trade_accepts_failed_legacy_learning_gate(self) -> None:
        plan = {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "model": {"promoted": False},
        }
        self.assertTrue(_allows_unpromoted_no_trade(plan, picked=0))

    def test_no_trade_exception_fails_closed_when_any_guard_is_missing(self) -> None:
        plan = {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 1,
            "model": {"promoted": False},
        }
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=0))
        plan["formal_buy_count"] = 0
        plan["model"]["promoted"] = True
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=0))
        plan["model"]["promoted"] = False
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=1))


class DecisionExecutionTruthTests(unittest.TestCase):
    def test_exit_contract_is_fixed_tplus1_open_0930(self) -> None:
        self.assertIsNone(EXIT_TAKE_PROFIT_PCT)
        self.assertIsNone(EXIT_STOP_LOSS_PCT)
        self.assertEqual(EXIT_LATEST_TIME, "09:30")

    def test_historical_minute_normalizes_tushare_pro_bar(self) -> None:
        response = pd.DataFrame(
                [
                    {
                        "ts_code": "600000.SH",
                        "trade_time": "2026-07-22 11:00:00",
                        "open": 10.2,
                        "high": 10.3,
                        "low": 10.1,
                        "close": 10.25,
                        "vol": 100,
                        "amount": 1025,
                    },
                    {
                        "ts_code": "600000.SH",
                        "trade_time": "2026-07-22 09:30:00",
                        "open": 10.0,
                        "high": 10.1,
                        "low": 9.9,
                        "close": 10.05,
                        "vol": 200,
                        "amount": 2010,
                    },
                ]
            )
        client = TushareClient(token="secret")
        with mock.patch.object(
            TushareClient,
            "call",
            autospec=True,
            return_value=response,
        ) as call:
            frame = client.historical_minute(
                "600000.SH",
                "20260722",
                latest_time="11:00",
            )

        self.assertEqual(frame["time"].tolist(), [
            "2026-07-22 09:30:00",
            "2026-07-22 11:00:00",
        ])
        _, api_name, params, fields = call.call_args.args
        self.assertEqual(api_name, "stk_mins")
        self.assertEqual(params["freq"], "1min")
        self.assertTrue(str(params["end_date"]).endswith("11:00:59"))
        self.assertIn("trade_time", fields)

    def test_minute_sync_cap_prioritizes_formal_and_stage_watch_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prediction_root = root / "outputs" / "auction_v3" / "predictions"
            prediction_root.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "ts_code": "600002.SH",
                        "name": "普通观察",
                        "expected_buy_date": "20260721",
                        "selected": 0,
                        "stage_focus": 0,
                        "predicted_continuation_limit_up_probability": 0.2,
                        "conservative_ev": -0.01,
                    },
                    {
                        "ts_code": "600003.SH",
                        "name": "二进三",
                        "expected_buy_date": "20260721",
                        "selected": 0,
                        "stage_focus": 1,
                        "predicted_continuation_limit_up_probability": 0.5,
                        "conservative_ev": 0.01,
                    },
                    {
                        "ts_code": "600004.SH",
                        "name": "正式信号",
                        "expected_buy_date": "20260721",
                        "selected": 1,
                        "stage_focus": 1,
                        "predicted_continuation_limit_up_probability": 0.4,
                        "conservative_ev": 0.02,
                    },
                ]
            ).to_csv(prediction_root / "pred_20260720.csv", index=False)

            codes = _collect_codes(root, "20260721", "", max_codes=2)

        self.assertEqual(codes, ["600004.SH", "600003.SH"])

    def test_later_intraday_break_does_not_count_as_auction_fill(self) -> None:
        label = infer_fill_label(
            pd.Series(
                {
                    "open_t1": 11.0,
                    "up_limit_t1": 11.0,
                    "open_times_t1": 3,
                    "break_open_times_t1": 3,
                }
            )
        )
        self.assertEqual(label[0], 0)
        self.assertEqual(label[1], "strong_opening_auction_limit_up_unconfirmed")

    def test_eret_gap_up_uses_fixed_open_not_hindsight_close(self) -> None:
        label = infer_eret_label(
            pd.Series(
                {
                    "y_fill": 1,
                    "entry_price_proxy_t1": 10.0,
                    "auction_price_t2": 12.0,
                    "open_t2": 12.0,
                    "high_t2": 20.0,
                    "low_t2": 12.0,
                    "close_t2": 20.0,
                }
            )
        )
        self.assertAlmostEqual(float(label[0]), 0.20)
        self.assertEqual(label[2], 1)
        self.assertEqual(label[3], 12.0)
        self.assertEqual(label[6], "fixed_open_0930")

    def test_daily_high_low_and_close_do_not_change_fixed_open_exit(self) -> None:
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=10.0,
            high_price=11.6,
            low_price=9.4,
            close_price=10.2,
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.exit_price, 10.0)
        self.assertEqual(result.reason, "fixed_open_0930")

    def test_intraday_minutes_do_not_change_fixed_open_exit(self) -> None:
        minute = pd.DataFrame(
            [
                {"time": "2026-07-22 09:30:00", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4},
                {"time": "2026-07-22 09:31:00", "open": 10.4, "high": 11.6, "low": 10.3, "close": 11.5},
                {"time": "2026-07-22 09:32:00", "open": 11.5, "high": 11.5, "low": 9.4, "close": 9.8},
            ]
        )
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=10.0,
            high_price=11.6,
            low_price=9.4,
            close_price=9.8,
            minute_frame=minute,
        )
        self.assertEqual(result.exit_price, 10.0)
        self.assertEqual(result.reason, "fixed_open_0930")

    def test_minute_0930_open_is_only_a_fallback_when_daily_open_is_missing(self) -> None:
        minute = pd.DataFrame(
            [
                {
                    "time": "2026-07-22 09:30:00",
                    "open": 10.2,
                    "high": 10.2,
                    "low": 10.2,
                    "close": 10.2,
                },
            ]
        )
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=float("nan"),
            high_price=10.2,
            low_price=10.2,
            close_price=10.2,
            minute_frame=minute,
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.exit_price, 10.2)
        self.assertEqual(result.source, "minute_0930_open_fallback")
        self.assertEqual(result.latest_exit_time, "09:30")

    def test_one_price_limit_down_is_not_an_executable_tplus1_exit(self) -> None:
        label = infer_eret_label(
            pd.Series(
                {
                    "y_fill": 1,
                    "entry_price_proxy_t1": 10.0,
                    "open_t2": 9.0,
                    "high_t2": 9.0,
                    "low_t2": 9.0,
                    "close_t2": 9.0,
                    "down_limit_t2": 9.0,
                }
            )
        )
        self.assertIsNone(label[0])
        self.assertEqual(label[1], "blocked_one_price_limit_down")
        self.assertEqual(label[2], 0)
        self.assertEqual(label[5], 0)

    def test_minute_snapshot_uses_first_0930_open(self) -> None:
        frame = pd.DataFrame(
            [
                {"time": "2026-07-21 09:30:00", "open": 10.2, "close": 10.3},
                {"time": "2026-07-21 09:31:00", "open": 10.4, "close": 10.5},
            ]
        )
        self.assertEqual(opening_auction_price_from_snapshot(frame), 10.2)


class DecisionUniverseAndEvTests(unittest.TestCase):
    def test_only_price_limit_mechanisms_at_or_below_ten_percent_survive(self) -> None:
        frame = pd.DataFrame(
            [
                {"ts_code": "600001.SH", "name": "主板A"},
                {"ts_code": "000002.SZ", "name": "主板B"},
                {"ts_code": "002003.SZ", "name": "ST样本"},
                {"ts_code": "300001.SZ", "name": "创业板"},
                {"ts_code": "688001.SH", "name": "科创板"},
                {"ts_code": "920001.BJ", "name": "北交所"},
                {"ts_code": "600010.SH", "name": "新股", "limit_type": "no_limit"},
                {"ts_code": "600011.SH", "name": "价格取整", "pre_close": 3.79, "up_limit": 4.17},
                {"ts_code": "600012.SH", "name": "上市初期", "trade_date": "20260720", "list_date": "20260715"},
            ]
        )
        eligible, audit = filter_standard_limit_universe(frame)
        self.assertEqual(set(eligible["ts_code"]), {"600001.SH", "000002.SZ", "002003.SZ", "600011.SH"})
        self.assertEqual(audit["rejected_rows"], 5)
        self.assertTrue((eligible["decision_limit_pct"] <= 10.0).all())

    def test_ev_cost_and_risk_are_subtracted_once(self) -> None:
        frame = pd.DataFrame(
            [{"p_fill_pred": 0.8, "e_ret_pred": 0.05, "cost_est": 0.004, "risk_penalty": 0.006}]
        )
        result = _apply_ev_upgrade_v1(frame).iloc[0]
        self.assertAlmostEqual(float(result["ev_pred"]), 0.03, places=12)
        self.assertEqual(float(result["ev_penalty_total_extra"]), 0.0)


class DecisionObservationContractTests(unittest.TestCase):
    def test_watchlist_is_capped_and_legacy_price_is_reproducible(self) -> None:
        rows = [
            {
                "ts_code": f"600{i:03d}.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0 + i,
                "estimated_up_limit": 11.0 + i,
                "predicted_continuation_limit_up_probability": 0.9 - i * 0.01,
                "predicted_big_loss_probability": 0.1 + i * 0.01,
                "conservative_ev": 0.02 - i * 0.001,
                "rank": i + 1,
            }
            for i in range(12)
        ]
        ranked, total = rank_observation_rows(rows)
        self.assertEqual(total, 12)
        self.assertEqual(len(ranked), 10)
        self.assertEqual([row["observation_rank"] for row in ranked], list(range(1, 11)))
        self.assertEqual(ranked[0]["observation_max_price"], 10.0)
        self.assertEqual(ranked[0]["observation_price_basis"], "legacy_d_close_cap")

    def test_watchlist_places_safe_candidate_before_high_probability_high_risk(self) -> None:
        rows = [
            {
                "ts_code": "600001.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0,
                "estimated_up_limit": 11.0,
                "risk_gate_pass": 0,
                "predicted_continuation_limit_up_probability": 0.95,
                "predicted_big_loss_probability": 0.60,
                "predicted_return_lcb": -0.08,
                "predicted_exit_probability": 0.70,
                "conservative_ev": 0.03,
                "rank": 1,
            },
            {
                "ts_code": "600002.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0,
                "estimated_up_limit": 11.0,
                "risk_gate_pass": 1,
                "predicted_continuation_limit_up_probability": 0.70,
                "predicted_big_loss_probability": 0.10,
                "predicted_return_lcb": 0.01,
                "predicted_exit_probability": 0.95,
                "conservative_ev": 0.01,
                "rank": 2,
            },
        ]
        ranked, _ = rank_observation_rows(rows)
        self.assertEqual(ranked[0]["ts_code"], "600002.SH")
        self.assertEqual(ranked[0]["observation_risk_label"], "正式安全门槛")
        self.assertEqual(ranked[1]["observation_risk_label"], "高风险观察")


class DecisionActionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "outputs" / "decision").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "predictions").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "metrics").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "models").mkdir(parents=True)
        (self.root / "data" / "decision").mkdir(parents=True)
        (self.root / "outputs" / "decision" / "decision_report_20260721.md").write_text("# report\n", encoding="utf-8")
        (self.root / "outputs" / "decision" / "eval_20260721.json").write_text(
            json.dumps(
                {
                    "signal_date": "20260720",
                    "exec_date": "20260721",
                    "exit_date": "20260722",
                    "risk_budget": 0.6,
                    "stop_trading": False,
                    "paths": {"candidates": "data/decision/decision_candidates_20260720.csv"},
                }
            ),
            encoding="utf-8",
        )
        pd.DataFrame(
            [
                {"ts_code": "600001.SH", "name": "主板", "industry": "银行", "p_fill_pred": 0.8, "e_ret_pred": 0.02, "ev_pred": 0.01},
                {"ts_code": "300001.SZ", "name": "创业板", "industry": "软件", "p_fill_pred": 0.9, "e_ret_pred": 0.03, "ev_pred": 0.02},
            ]
        ).to_csv(self.root / "data" / "decision" / "decision_candidates_20260720.csv", index=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_market_close_snapshot(
        self,
        trade_date: str,
        returns: list[float],
        limit_up_industries: list[str],
    ) -> None:
        market_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / trade_date[:4]
            / trade_date
        )
        market_root.mkdir(parents=True, exist_ok=True)
        codes = [f"600{100 + index:03d}.SH" for index in range(len(returns))]
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 11.0 if index < len(limit_up_industries) else 10.2,
                    "low": 9.8,
                    "close": 11.0 if index < len(limit_up_industries) else 10.0 * (1.0 + value),
                    "pre_close": 10.0,
                    "pct_chg": 10.0 if index < len(limit_up_industries) else value * 100.0,
                    "vol": 1_000_000,
                    "amount": 20_000_000,
                }
                for index, (code, value) in enumerate(zip(codes, returns))
            ]
        ).to_csv(market_root / "daily.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
                for code in codes
            ]
        ).to_csv(market_root / "stk_limit.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ts_code": codes[index],
                    "trade_date": trade_date,
                    "limit_type": "U",
                    "industry": industry,
                }
                for index, industry in enumerate(limit_up_industries)
            ]
        ).to_csv(market_root / "limit_list_d.csv", index=False)

    def _write_model_artifacts(
        self,
        *,
        promoted: bool,
        version: str = "auction_v12_top10_trade_selector_oos_1",
        artifact: str = "a" * 64,
    ) -> None:
        trade_artifact = "c" * 64
        pd.DataFrame(
            [
                {
                    "signal_date": "20260720",
                    "expected_buy_date": "20260721",
                    "expected_exit_date": "20260722",
                    "ts_code": "600001.SH",
                    "name": "主板",
                    "industry": "银行",
                    "selected": 1,
                    "trade_selected": 1,
                    "trade_shadow_selected": int(promoted),
                    "trade_rank": 1,
                    "trade_selector_promoted": int(promoted),
                    "trade_selector_artifact_sha256": trade_artifact,
                    "action": "BUY",
                    "recommended_max_price": 10.5,
                    "mechanism_limit_pct": 10.036,
                    "predicted_fill_probability": 0.8,
                    "predicted_exit_probability": 0.95,
                    "predicted_big_loss_probability": 0.05,
                    "predicted_continuation_limit_up_probability": 0.60,
                    "predicted_net_return": 0.02,
                    "predicted_return_lcb": 0.005,
                    "conservative_ev": 0.004,
                    "risk_gate_pass": 1,
                    "stage": "2→3",
                    "stage_focus": 1,
                    "order_type": "LIMIT_ONLY_MANUAL",
                    "market_order_allowed": 0,
                    "model_ready": 1,
                    "model_promoted": int(promoted),
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                },
                {
                    "signal_date": "20260720",
                    "expected_buy_date": "20260721",
                    "expected_exit_date": "20260722",
                    "ts_code": "300001.SZ",
                    "name": "创业板",
                    "selected": 1,
                    "trade_selected": 1,
                    "trade_shadow_selected": int(promoted),
                    "trade_rank": 2,
                    "trade_selector_promoted": int(promoted),
                    "trade_selector_artifact_sha256": trade_artifact,
                    "action": "BUY",
                    "model_ready": 1,
                    "model_promoted": int(promoted),
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                },
            ]
        ).to_csv(self.root / "outputs" / "auction_v3" / "predictions" / "pred_latest.csv", index=False)
        (self.root / "outputs" / "auction_v3" / "metrics" / "backtest_latest.json").write_text(
            json.dumps(
                {
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "promoted": promoted,
                    "promotion_failures": [],
                    "trade_selector": {
                        "promoted": promoted,
                        "production_artifact_sha256": trade_artifact,
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json").write_text(
            json.dumps(
                {
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "ready": True,
                    "promoted": promoted,
                    "trade_selector": {
                        "promoted": promoted,
                        "production_artifact_sha256": trade_artifact,
                    },
                    "current_market_sentiment": {
                        "market_limit_up_industry_top10": [
                            {
                                "rank": 1,
                                "industry": "银行",
                                "limit_up_count": 3,
                                "share": 0.3,
                            },
                            {
                                "rank": 2,
                                "industry": "软件",
                                "limit_up_count": 2,
                                "share": 0.2,
                            },
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_unpromoted_model_can_never_emit_formal_buy(self) -> None:
        self._write_model_artifacts(promoted=False)
        plan = build_action_plan(self.root)
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 1)
        self.assertFalse(any(row["action"] == "BUY" for row in plan["candidates"]))
        self.assertEqual(plan["stage_watch_count"], 1)
        self.assertEqual(plan["stage_watchlist"][0]["watch_label"], "二筛影子")
        self.assertEqual(
            plan["schema_version"],
            "decision_action_plan_v12_top10_trade_selector",
        )
        self.assertEqual(plan["stage_watchlist"][0]["observation_max_price"], 10.5)
        self.assertIn("observation_statistics", plan)
        self.assertIn("market_sentiment", plan)
        self.assertEqual(
            plan["market_sentiment"]["limit_up_industry_top10"],
            [
                {
                    "rank": 1,
                    "industry": "银行",
                    "limit_up_count": 3,
                    "share": 0.3,
                },
                {
                    "rank": 2,
                    "industry": "软件",
                    "limit_up_count": 2,
                    "share": 0.2,
                },
            ],
        )
        self.assertEqual(
            plan["market_sentiment"]["limit_up_industry_top5"],
            plan["market_sentiment"]["limit_up_industry_top10"][:5],
        )
        self.assertIn("market_close_comparison", plan)
        self.assertFalse(plan["market_close_comparison"]["model_input"])
        self.assertFalse(plan["market_close_comparison"]["t"]["available"])

    def test_unpromoted_plan_selects_exactly_two_relative_best_candidates(self) -> None:
        self._write_model_artifacts(promoted=False)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        template = prediction.iloc[0].copy()
        additions = []
        for code, name, trade_rank, big_loss in (
            ("600002.SH", "主板二", 2, 0.06),
            ("600003.SH", "主板三", 3, 0.07),
            ("600004.SH", "Top10外诊断票", None, 0.01),
        ):
            row = template.copy()
            row["ts_code"] = code
            row["name"] = name
            row["trade_rank"] = trade_rank
            row["trade_shadow_selected"] = 0
            row["trade_selected"] = 0
            row["selected"] = 0
            row["predicted_big_loss_probability"] = big_loss
            additions.append(row)
        pd.concat([prediction, pd.DataFrame(additions)], ignore_index=True).to_csv(
            prediction_path,
            index=False,
        )

        plan = build_action_plan(self.root)

        shadow = sorted(
            [
                row
                for row in plan["candidates"]
                if row["action"] == "SHADOW_ONLY"
            ],
            key=lambda row: row["trade_rank"],
        )
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 2)
        self.assertEqual(
            [row["ts_code"] for row in shadow],
            ["600001.SH", "600002.SH"],
        )
        outside = next(
            row for row in plan["candidates"] if row["ts_code"] == "600004.SH"
        )
        self.assertEqual(outside["action"], "REJECT")
        self.assertEqual(outside["trade_rank"], 0)

    def test_pending_plan_uses_relative_best_two_without_formal_buy(self) -> None:
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "name": name,
                    "industry": "银行",
                    "advance_stage": "2→3",
                    "decision_limit_pct": 10.0,
                    "promotion_rank": promotion_rank,
                    "predicted_big_loss_probability": big_loss,
                    "predicted_return_lcb": return_lcb,
                }
                for code, name, promotion_rank, big_loss, return_lcb in (
                    ("600001.SH", "主板一", 3, 0.05, 0.03),
                    ("600002.SH", "主板二", 1, 0.20, 0.01),
                    ("600003.SH", "主板三", 1, 0.08, 0.00),
                )
            ]
        ).to_csv(
            self.root / "data" / "decision" / "decision_candidates_20260720.csv",
            index=False,
        )

        plan = build_action_plan(self.root)

        shadow = sorted(
            [
                row
                for row in plan["candidates"]
                if row["action"] == "SHADOW_ONLY"
            ],
            key=lambda row: row["trade_rank"],
        )
        self.assertEqual(plan["status_code"], "PENDING_AUCTION_MODEL")
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 2)
        self.assertEqual(
            [row["ts_code"] for row in shadow],
            ["600003.SH", "600002.SH"],
        )

    def test_market_close_comparison_waits_for_complete_t_snapshot(self) -> None:
        self._write_model_artifacts(promoted=False)
        self._write_market_close_snapshot(
            "20260720",
            [0.10, 0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04, 0.01],
            ["电力", "电网设备"],
        )
        self._write_market_close_snapshot(
            "20260721",
            [0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04],
            ["电力"],
        )
        incomplete = build_action_plan(self.root)["market_close_comparison"]
        self.assertFalse(incomplete["t"]["available"])
        self.assertEqual(
            incomplete["t"]["maturity_status"],
            "INCOMPLETE_T_CLOSE",
        )

        self._write_market_close_snapshot(
            "20260721",
            [0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04, 0.01],
            ["电力"],
        )
        complete = build_action_plan(self.root)["market_close_comparison"]
        self.assertTrue(complete["d"]["available"])
        self.assertTrue(complete["t"]["available"])
        self.assertEqual(complete["t"]["maturity_status"], "FINAL_T_CLOSE")
        self.assertAlmostEqual(complete["t"]["coverage_against_d"], 0.9)
        self.assertEqual(complete["d"]["up_count"], 6)
        self.assertEqual(complete["d"]["down_count"], 3)
        self.assertEqual(complete["d"]["flat_count"], 1)
        self.assertEqual(complete["d"]["industry_counts"]["电力"], 1)
        self.assertEqual(complete["t"]["industry_counts"]["电力"], 1)

    def test_promoted_model_still_rejects_above_ten_percent_board(self) -> None:
        self._write_model_artifacts(promoted=True)
        plan = build_action_plan(self.root)
        actions = {row["ts_code"]: row["action"] for row in plan["candidates"]}
        self.assertEqual(actions["600001.SH"], "BUY")
        self.assertEqual(actions["300001.SZ"], "REJECT")
        self.assertEqual(plan["formal_buy_count"], 1)
        main_board = next(row for row in plan["candidates"] if row["ts_code"] == "600001.SH")
        self.assertEqual(main_board["mechanism_limit_pct"], 10.0)
        self.assertEqual(main_board["stage_transition"], "2→3")
        self.assertEqual(main_board["market_order_allowed"], 0)
        self.assertFalse(plan["broker_connected"])
        self.assertEqual(plan["stage_watchlist"][0]["watch_label"], "正式买入")
        self.assertLessEqual(plan["stage_watch_count"], 10)

    def test_artifact_version_mismatch_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = self.root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
        meta_path.write_text(json.dumps({"model_version": "stale", "ready": True, "promoted": True}), encoding="utf-8")
        plan = build_action_plan(self.root)
        self.assertFalse(plan["model"]["promoted"])
        self.assertFalse(plan["model"]["artifact_versions_match"])
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_artifact_fingerprint_mismatch_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["model_artifact_sha256"] = "b" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        plan = build_action_plan(self.root)
        self.assertFalse(plan["model"]["promoted"])
        self.assertFalse(plan["model"]["artifact_versions_match"])
        self.assertFalse(plan["model"]["artifact_fingerprints_match"])
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_selector_fingerprint_ignores_non_top10_blank_rows(self) -> None:
        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction.loc[0, "trade_selector_artifact_sha256"] = ""
        prediction.to_csv(prediction_path, index=False)

        plan = build_action_plan(self.root)

        self.assertTrue(plan["model"]["trade_selector_artifacts_match"])
        self.assertTrue(plan["model"]["promoted"])
        self.assertEqual(plan["formal_buy_count"], 1)


class DecisionWorkflowSerializationTests(unittest.TestCase):
    def test_all_decision_main_writers_share_one_non_cancelling_lock(self) -> None:
        workflow_root = ROOT / ".github" / "workflows"
        for name in (
            "run_decision_daily.yml",
            "run_auction_v3.yml",
            "backfill_decision_v11_history.yml",
        ):
            text = (workflow_root / name).read_text(encoding="utf-8")
            self.assertIn("group: decision-auction-main-writer", text)
            self.assertIn("cancel-in-progress: false", text)

    def test_learning_migration_has_time_and_avoids_shared_pred_meta(self) -> None:
        text = (
            ROOT / ".github" / "workflows" / "run_decision_daily.yml"
        ).read_text(encoding="utf-8")
        learning = text.split("  pfill_learning:", 1)[1].split(
            "  decision_refresh_after_learning:",
            1,
        )[0]
        self.assertIn("timeout-minutes: 120", learning)
        self.assertNotIn(
            "git add data/pred/_pred_source_meta.json",
            learning,
        )


if __name__ == "__main__":
    unittest.main()
