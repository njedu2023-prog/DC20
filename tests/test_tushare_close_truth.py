from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import pandas as pd


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.data.tushare_minute import (  # noqa: E402
    DAILY_LIMIT_LIST_FIELDS,
    TushareClient,
    write_daily_close_snapshot,
)


class TushareCloseTruthTest(unittest.TestCase):
    def test_daily_close_rejects_wrong_source_date(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260724",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "pre_close": 10.0,
                    "vol": 1.0,
                    "amount": 10.0,
                    "pct_chg": 1.0,
                }
            ]
        )
        client = TushareClient(token="test")
        with mock.patch.object(
            TushareClient,
            "call",
            return_value=frame,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "trade_date mismatch",
            ):
                client.daily_close("20260727")

    def test_writer_persists_complete_same_date_partition(self) -> None:
        trade_date = "20260727"
        codes = [f"{index:06d}.SZ" for index in range(3000)]
        daily = pd.DataFrame(
            {
                "ts_code": codes,
                "trade_date": trade_date,
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "pre_close": 10.0,
                "vol": 1.0,
                "amount": 10.0,
                "pct_chg": 1.0,
            }
        )
        limits = pd.DataFrame(
            {
                "ts_code": codes,
                "trade_date": trade_date,
                "up_limit": 11.0,
                "down_limit": 9.0,
            }
        )
        limit_list = pd.DataFrame(
            columns=list(DAILY_LIMIT_LIST_FIELDS)
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, meta_path = write_daily_close_snapshot(
                daily,
                limits,
                limit_list,
                root,
                trade_date,
            )

            written = pd.read_csv(
                paths["daily.csv"],
                encoding="utf-8-sig",
                dtype={"trade_date": str},
            )
            self.assertEqual(len(written), 3000)
            self.assertEqual(set(written["trade_date"]), {trade_date})
            self.assertTrue(paths["stk_limit.csv"].exists())
            self.assertTrue(paths["limit_list_d.csv"].exists())
            self.assertTrue(meta_path.exists())


if __name__ == "__main__":
    unittest.main()
