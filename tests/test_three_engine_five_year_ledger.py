from __future__ import annotations

import math
import unittest
import tempfile
from pathlib import Path

import pandas as pd

from scripts.build_three_engine_five_year_ledger import (
    EVENT_IDENTITY_COLUMNS,
    EXPECTED_EVENT_SEED_COLUMNS,
    PROMOTION_BAR_CONTEXT_FEATURES,
    RUNTIME_ALIGNED_FEATURE_COLUMNS,
    _attach_d_close_history_features,
    _bars,
    _build_ledger,
    _load_strict_sse_calendar,
    _load_owned_events,
    _recompute_point_in_time_promotion_priors,
)


class FiveYearLedgerTest(unittest.TestCase):
    OPEN_SESSIONS = [
        "20231225",
        "20231226",
        "20231227",
        "20231228",
        "20231229",
        "20240102",
        "20240103",
        "20240104",
    ]

    @staticmethod
    def event(code: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "signal_date": "20240102",
                    "ts_code": code,
                    "stage": 2,
                    "board": "SZ_MAIN",
                    "five_year_stage_prior_rate": 0.35,
                }
            ]
        )

    @staticmethod
    def write_seed(path: Path, rows: list[dict[str, object]]) -> None:
        frame = pd.DataFrame(rows)
        for column in EXPECTED_EVENT_SEED_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame[list(EXPECTED_EVENT_SEED_COLUMNS)].to_csv(
            path, index=False, compression="gzip"
        )

    def test_builds_three_targets_for_buyable_standard_limit_candidate(self) -> None:
        code = "000001.SZ"
        prices = pd.DataFrame(
            [
                # D is an exact 10% close from pre-close 10.00.
                {"ts_code": code, "trade_date": "20240102", "open": 10.5, "close": 11.0, "high": 11.0, "low": 10.4, "amount": 1e8, "pct_change": 10.0, "pre_close": 10.0, "turnover_pct": 8.0},
                # T also closes at the exact limit, but trades below it and is buyable.
                {"ts_code": code, "trade_date": "20240103", "open": 11.2, "close": 12.1, "high": 12.1, "low": 11.1, "amount": 1e8, "pct_change": 10.0, "pre_close": 11.0, "turnover_pct": 9.0},
                {"ts_code": code, "trade_date": "20240104", "open": 10.7, "close": 10.8, "high": 10.9, "low": 10.6, "amount": 1e8, "pct_change": -10.74, "pre_close": 12.1, "turnover_pct": 7.0},
            ]
        )
        ledger = _build_ledger(self.event(code), prices, self.OPEN_SESSIONS)
        self.assertEqual(len(ledger), 1)
        row = ledger.iloc[0]
        self.assertEqual(row["promotion_hit"], 1)
        self.assertEqual(row["market_fill"], 1)
        self.assertEqual(row["big_loss_hit"], 1)
        self.assertEqual(row["profit_hit"], 0)
        self.assertEqual(row["buy_date"], "20240103")
        self.assertEqual(row["target_exit_date"], "20240104")

    def test_nonfill_return_targets_remain_null(self) -> None:
        code = "000001.SZ"
        prices = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": "20240102", "open": 10.5, "close": 11.0, "high": 11.0, "low": 10.4, "amount": 1e8, "pct_change": 10.0, "pre_close": 10.0, "turnover_pct": 8.0},
                # T never opens the 12.10 limit, so an opening limit order is unfilled.
                {"ts_code": code, "trade_date": "20240103", "open": 12.1, "close": 12.1, "high": 12.1, "low": 12.1, "amount": 1e7, "pct_change": 10.0, "pre_close": 11.0, "turnover_pct": 1.0},
                {"ts_code": code, "trade_date": "20240104", "open": 13.0, "close": 13.0, "high": 13.0, "low": 13.0, "amount": 1e8, "pct_change": 7.44, "pre_close": 12.1, "turnover_pct": 4.0},
            ]
        )
        row = _build_ledger(self.event(code), prices, self.OPEN_SESSIONS).iloc[0]
        self.assertEqual(row["market_fill"], 0)
        self.assertTrue(pd.isna(row["net_return"]))
        self.assertTrue(pd.isna(row["big_loss_hit"]))
        self.assertTrue(pd.isna(row["profit_hit"]))

    def test_excludes_nonstandard_limit_source_row(self) -> None:
        code = "000001.SZ"
        prices = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": "20240102", "open": 10.1, "close": 10.5, "high": 10.5, "low": 10.0, "amount": 1e8, "pct_change": 5.0, "pre_close": 10.0, "turnover_pct": 8.0},
                {"ts_code": code, "trade_date": "20240103", "open": 10.6, "close": 10.7, "high": 10.8, "low": 10.5, "amount": 1e8, "pct_change": 1.9, "pre_close": 10.5, "turnover_pct": 5.0},
                {"ts_code": code, "trade_date": "20240104", "open": 10.8, "close": 10.9, "high": 11.0, "low": 10.7, "amount": 1e8, "pct_change": 1.87, "pre_close": 10.7, "turnover_pct": 5.0},
            ]
        )
        self.assertTrue(
            _build_ledger(self.event(code), prices, self.OPEN_SESSIONS).empty
        )

    def test_parses_tencent_bar_contract(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "sz000001": {
                    "day": [
                        ["2024-01-01", "9.90", "10.00", "10.00", "9.80", "100"],
                        ["2024-01-02", "10.50", "11.00", "11.00", "10.40", "100"],
                    ],
                }
            }
        }
        frame = _bars("000001.SZ", payload)
        self.assertEqual(frame.iloc[1]["trade_date"], "20240102")
        self.assertAlmostEqual(float(frame.iloc[1]["pre_close"]), 10.0)

    def test_d_history_features_do_not_change_when_future_bars_change(self) -> None:
        code = "000001.SZ"
        dates = pd.bdate_range("2023-10-02", periods=70)
        rows = []
        previous = 10.0
        for index, date in enumerate(dates):
            close = previous * (1.001 + (index % 5) * 0.0002)
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date.strftime("%Y%m%d"),
                    "open": previous * 1.001,
                    "close": close,
                    "high": max(previous, close) * 1.003,
                    "low": min(previous, close) * 0.997,
                    "pre_close": previous,
                    "volume": 1000 + index * 10,
                }
            )
            previous = close
        signal_date = rows[59]["trade_date"]
        original = pd.DataFrame(rows)
        changed = original.copy()
        changed.loc[changed["trade_date"].gt(signal_date), ["open", "close", "high", "low", "volume"]] *= 7.0
        key = pd.DataFrame([{"ts_code": code, "signal_date": signal_date}])
        left = _attach_d_close_history_features(original, key).iloc[0]
        right = _attach_d_close_history_features(changed, key).iloc[0]
        feature_columns = [
            name
            for name in left.index
            if name.startswith("five_year_")
            or name in RUNTIME_ALIGNED_FEATURE_COLUMNS
        ]
        pd.testing.assert_series_equal(
            left[feature_columns], right[feature_columns], check_names=False
        )

    def test_owned_event_loader_adds_only_new_canonical_hard_range_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seed.csv.gz"
            prediction_root = root / "predictions"
            prediction_root.mkdir()
            self.write_seed(
                seed_path,
                [
                    {
                        "signal_date": "20240102",
                        "ts_code": "000001.SZ",
                        "stage": 2,
                        "board": "SZ_MAIN",
                    },
                    {
                        "signal_date": pd.NA,
                        "ts_code": pd.NA,
                        "stage": pd.NA,
                        "board": pd.NA,
                        "five_year_pre_streak_1d_return": 999.0,
                    },
                ],
            )
            pd.DataFrame(
                [
                    {
                        "signal_date": "20240103",
                        "ts_code": "000002.SZ",
                        "stage": "2→3",
                        "mechanism_limit_pct": 10,
                    },
                    {
                        "signal_date": "20240103",
                        "ts_code": "600002.SH",
                        "stage": "3→4",
                        "mechanism_limit_pct": 10,
                    },
                    {
                        "signal_date": "20240103",
                        "ts_code": "300001.SZ",
                        "stage": "2→3",
                        "mechanism_limit_pct": 20,
                    },
                    {
                        "signal_date": "20240103",
                        "ts_code": "000003.SZ",
                        "stage": "1→2",
                        "mechanism_limit_pct": 10,
                    },
                ]
            ).to_csv(prediction_root / "pred_20240103.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "signal_date": "20240104",
                        "ts_code": "000004.SZ",
                        "stage": "2→3",
                    }
                ]
            ).to_csv(
                prediction_root / "pred_20240104_old_model.csv", index=False
            )
            events, inventory = _load_owned_events(seed_path, prediction_root)
            self.assertEqual(len(events), 3)
            self.assertEqual(set(events["signal_date"]), {"20240102", "20240103"})
            self.assertEqual(set(events["stage"].astype(int)), {2, 3})
            self.assertEqual(inventory["canonical_prediction_file_count"], 1)
            self.assertEqual(inventory["new_eligible_rows_discovered"], 2)
            self.assertEqual(inventory["seed_raw_rows"], 2)
            self.assertEqual(inventory["seed_identity_rows"], 1)
            self.assertEqual(inventory["seed_orphan_rows_quarantined"], 1)
            self.assertEqual(inventory["seed_columns_used"], list(EVENT_IDENTITY_COLUMNS))
            self.assertFalse(inventory["seed_context_source_used"])
            self.assertEqual(list(events.columns), list(EVENT_IDENTITY_COLUMNS))
            self.assertRegex(
                inventory["canonical_prediction_files"][0]["sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_owned_event_loader_fails_closed_on_partial_invalid_or_duplicate_identity(self) -> None:
        cases = (
            [
                {"signal_date": "20240102", "ts_code": "000001.SZ", "stage": 2, "board": pd.NA}
            ],
            [
                {"signal_date": "not-a-date", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN"}
            ],
            [
                {"signal_date": "20240102", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN"},
                {"signal_date": "20240102", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN"},
            ],
        )
        for rows in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                seed_path = root / "seed.csv.gz"
                prediction_root = root / "predictions"
                prediction_root.mkdir()
                self.write_seed(seed_path, rows)
                with self.assertRaisesRegex(ValueError, "identity"):
                    _load_owned_events(seed_path, prediction_root)

    def test_owned_event_loader_rejects_unexpected_seed_schema_or_orphan_prior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seed.csv.gz"
            prediction_root = root / "predictions"
            prediction_root.mkdir()
            pd.DataFrame(
                [{"signal_date": "20240102", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN"}]
            ).to_csv(seed_path, index=False, compression="gzip")
            with self.assertRaisesRegex(ValueError, "14-column"):
                _load_owned_events(seed_path, prediction_root)

            self.write_seed(
                seed_path,
                [
                    {
                        "five_year_pre_streak_1d_return": 0.1,
                        "five_year_stock_prior_rate": 0.4,
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "must not carry stock priors"):
                _load_owned_events(seed_path, prediction_root)

    def test_strict_sse_calendar_validates_natural_days_and_pretrade_chain(self) -> None:
        rows = [
            ("SSE", "20231229", "1", "20231228"),
            ("SSE", "20231230", "0", "20231229"),
            ("SSE", "20231231", "0", "20231229"),
            ("SSE", "20240101", "0", "20231229"),
            ("SSE", "20240102", "1", "20231229"),
            ("SSE", "20240103", "1", "20240102"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trade_cal_sse.csv"
            pd.DataFrame(
                rows, columns=["exchange", "cal_date", "is_open", "pretrade_date"]
            ).to_csv(path, index=False)
            sessions, evidence = _load_strict_sse_calendar(path)
            self.assertEqual(sessions, ["20231229", "20240102", "20240103"])
            self.assertTrue(evidence["strict"])
            self.assertEqual(evidence["source"], "tushare:trade_cal:SSE")

            broken = pd.DataFrame(
                rows, columns=["exchange", "cal_date", "is_open", "pretrade_date"]
            ).drop(index=2)
            broken.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "consecutive natural days"):
                _load_strict_sse_calendar(path)

    def test_weekend_holiday_and_missing_stock_bar_do_not_change_bound_dates(self) -> None:
        code = "000001.SZ"
        sessions = [
            "20231227", "20231228", "20231229", "20240102", "20240103",
            "20240105", "20240108", "20240109",
        ]
        event = pd.DataFrame(
            [{"signal_date": "20240105", "ts_code": code, "stage": 2, "board": "SZ_MAIN"}]
        )
        prices = pd.DataFrame(
            [
                # A stray weekend bar must never become T.
                {"ts_code": code, "trade_date": "20240106", "open": 11.1, "close": 11.2, "high": 11.2, "low": 11.0, "amount": 1e8, "pct_change": 1.0, "pre_close": 11.0, "turnover_pct": 1.0},
                {"ts_code": code, "trade_date": "20240105", "open": 10.5, "close": 11.0, "high": 11.0, "low": 10.4, "amount": 1e8, "pct_change": 10.0, "pre_close": 10.0, "turnover_pct": 8.0},
                # 20240108 is intentionally absent for this suspended stock.
                {"ts_code": code, "trade_date": "20240109", "open": 10.8, "close": 10.9, "high": 11.0, "low": 10.7, "amount": 1e8, "pct_change": -1.0, "pre_close": 11.0, "turnover_pct": 5.0},
            ]
        )
        row = _build_ledger(event, prices, sessions).iloc[0]
        self.assertEqual(row["buy_date"], "20240108")
        self.assertEqual(row["target_exit_date"], "20240109")
        self.assertTrue(pd.isna(row["t_open"]))

    def test_bar_context_is_rebuilt_from_prices_not_seed_values(self) -> None:
        code = "000001.SZ"
        sessions = [
            "20231225", "20231226", "20231227", "20231228", "20231229",
            "20240102", "20240103", "20240104",
        ]
        event = self.event(code)
        for feature in PROMOTION_BAR_CONTEXT_FEATURES:
            event[feature] = 999.0
        closes = [8.0, 8.8, 9.0, 9.2, 10.0, 11.0, 12.1, 12.0]
        prices = pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": close,
                    "close": close,
                    "high": close,
                    "low": close,
                    "volume": 1000.0,
                    "amount": 1e8,
                    "pct_change": 10.0 if index in {1, 5, 6} else 0.0,
                    "pre_close": close / 1.1 if index in {1, 5, 6} else close,
                    "turnover_pct": 5.0,
                }
                for index, (date, close) in enumerate(zip(sessions, closes))
            ]
        )
        row = _build_ledger(event, prices, sessions).iloc[0]
        for feature in PROMOTION_BAR_CONTEXT_FEATURES:
            self.assertNotEqual(row[feature], 999.0)
            self.assertTrue(math.isfinite(float(row[feature])))

        # Production event loading deliberately returns identity columns only;
        # rebuilt context must therefore survive without a same-named seed column.
        identity_only = event[list(EVENT_IDENTITY_COLUMNS)].copy()
        identity_row = _build_ledger(identity_only, prices, sessions).iloc[0]
        for feature in PROMOTION_BAR_CONTEXT_FEATURES:
            self.assertIn(feature, identity_row.index)
            self.assertTrue(math.isfinite(float(identity_row[feature])))

    def test_point_in_time_prior_does_not_use_same_day_truth(self) -> None:
        ledger = pd.DataFrame(
            [
                {
                    "signal_date": date,
                    "ts_code": f"00000{position}.SZ",
                    "stage": 2,
                    "board": "SZ_MAIN",
                    "promotion_hit": hit,
                }
                for position, (date, hit) in enumerate(
                    [
                        ("20240102", 1),
                        ("20240103", 0),
                        ("20240104", 1),
                    ],
                    start=1,
                )
            ]
        )
        original = _recompute_point_in_time_promotion_priors(ledger)
        changed = ledger.copy()
        changed.loc[changed["signal_date"].eq("20240103"), "promotion_hit"] = 1
        revised = _recompute_point_in_time_promotion_priors(changed)
        feature = "five_year_stage_board_prior_rate"
        self.assertEqual(
            original.loc[original["signal_date"].eq("20240103"), feature].iloc[0],
            revised.loc[revised["signal_date"].eq("20240103"), feature].iloc[0],
        )
        self.assertNotEqual(
            original.loc[original["signal_date"].eq("20240104"), feature].iloc[0],
            revised.loc[revised["signal_date"].eq("20240104"), feature].iloc[0],
        )

    def test_stock_beta_prior_uses_only_strictly_earlier_truth(self) -> None:
        ledger = pd.DataFrame(
            [
                {"signal_date": "20240102", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN", "promotion_hit": 1},
                {"signal_date": "20240103", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN", "promotion_hit": 0},
                {"signal_date": "20240104", "ts_code": "000001.SZ", "stage": 2, "board": "SZ_MAIN", "promotion_hit": 1},
            ]
        )
        original = _recompute_point_in_time_promotion_priors(ledger)
        self.assertAlmostEqual(original.iloc[0]["five_year_stock_prior_rate"], 2.0 / 5.0)
        self.assertAlmostEqual(original.iloc[1]["five_year_stock_prior_rate"], 3.0 / 6.0)
        self.assertAlmostEqual(original.iloc[2]["five_year_stock_prior_rate"], 3.0 / 7.0)
        self.assertAlmostEqual(original.iloc[2]["five_year_stock_prior_samples_log"], math.log1p(2.0))
        changed = ledger.copy()
        changed.loc[changed["signal_date"].eq("20240103"), "promotion_hit"] = 1
        revised = _recompute_point_in_time_promotion_priors(changed)
        self.assertEqual(
            original.iloc[1]["five_year_stock_prior_rate"],
            revised.iloc[1]["five_year_stock_prior_rate"],
        )
        self.assertNotEqual(
            original.iloc[2]["five_year_stock_prior_rate"],
            revised.iloc[2]["five_year_stock_prior_rate"],
        )


if __name__ == "__main__":
    unittest.main()
