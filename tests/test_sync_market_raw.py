from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "sync_market_raw.py"
)
SPEC = importlib.util.spec_from_file_location(
    "decision_sync_market_raw",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
sync_market_raw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_market_raw
SPEC.loader.exec_module(sync_market_raw)


class SyncMarketRawTest(unittest.TestCase):
    def test_explicit_date_rejects_stale_latest_fallback(self) -> None:
        urls = [
            "https://example.test/dated/daily.csv",
            "https://example.test/latest/daily.csv",
        ]
        stale = (
            "\ufefftrade_date,ts_code,open,close\n"
            "20260724,000001.SZ,10.0,10.1\n"
        )

        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            side_effect=[
                (False, "", 404),
                (True, stale, 200),
            ],
        ):
            url, text, code, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    urls,
                    expected_trade_date="20260727",
                    date_scoped=True,
                )
            )

        self.assertIsNone(url)
        self.assertIsNone(text)
        self.assertEqual(code, 200)
        self.assertIsNone(source_date)
        self.assertEqual(
            error,
            "trade_date_mismatch:requested=20260727,actual=20260724",
        )

    def test_explicit_date_accepts_only_matching_snapshot(self) -> None:
        urls = [
            "https://example.test/latest/daily.csv",
            "https://example.test/root/daily.csv",
        ]
        stale = (
            "ts_code,trade_date,open,close\n"
            "000001.SZ,20260724,10.0,10.1\n"
        )
        current = (
            "ts_code,trade_date,open,close\n"
            "000001.SZ,20260727,10.2,10.3\n"
        )

        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            side_effect=[
                (True, stale, 200),
                (True, current, 200),
            ],
        ):
            url, text, code, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    urls,
                    expected_trade_date="20260727",
                    date_scoped=True,
                )
            )

        self.assertEqual(url, urls[1])
        self.assertEqual(text, current)
        self.assertEqual(code, 200)
        self.assertEqual(source_date, "20260727")
        self.assertEqual(error, "")

    def test_static_reference_table_can_use_latest_snapshot(self) -> None:
        static = (
            "ts_code,name,industry\n"
            "000001.SZ,平安银行,银行\n"
        )
        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            return_value=(True, static, 200),
        ):
            url, text, _, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    ["https://example.test/latest/stock_basic.csv"],
                    expected_trade_date="20260727",
                    date_scoped=False,
                )
            )

        self.assertIsNotNone(url)
        self.assertEqual(text, static)
        self.assertIsNone(source_date)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
