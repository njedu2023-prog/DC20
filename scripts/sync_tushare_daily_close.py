#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.data.tushare_minute import (  # noqa: E402
    DAILY_LIMIT_LIST_FIELDS,
    TushareClient,
    write_daily_close_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sync a strict same-date Tushare daily close partition for "
            "Decision truth validation"
        )
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Keep the workflow pending when the close source is unavailable",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    trade_date = str(args.trade_date or "").strip()
    if not re.fullmatch(r"20\d{6}", trade_date):
        raise ValueError(f"invalid trade_date={trade_date}")

    if not str(os.environ.get("TUSHARE_TOKEN", "") or "").strip():
        if args.optional:
            print(
                "[tushare-close] TUSHARE_TOKEN unavailable; "
                "Decision truth remains pending"
            )
            return 0
        raise RuntimeError("TUSHARE_TOKEN is not configured")

    try:
        client = TushareClient.from_env(
            timeout_seconds=max(1, int(args.timeout_seconds))
        )
        calendar = client.trade_calendar(trade_date, trade_date)
        is_open = bool(
            len(calendar)
            and str(calendar.iloc[-1]["cal_date"]) == trade_date
            and int(calendar.iloc[-1]["is_open"]) == 1
        )
        if not is_open:
            raise RuntimeError(
                f"{trade_date} is not an open SSE session"
            )

        daily = client.daily_close(trade_date)
        limits = client.daily_limits(trade_date)
        limit_list_error = ""
        try:
            limit_list = client.daily_limit_list(trade_date)
        except Exception as exc:
            limit_list = pd.DataFrame(
                columns=list(DAILY_LIMIT_LIST_FIELDS)
            )
            limit_list_error = type(exc).__name__

        paths, meta_path = write_daily_close_snapshot(
            daily,
            limits,
            limit_list,
            root,
            trade_date,
        )
        summary = {
            "status": "written",
            "source": "tushare",
            "trade_date": trade_date,
            "daily_rows": int(len(daily)),
            "limit_rows": int(len(limits)),
            "limit_list_rows": int(len(limit_list)),
            "limit_list_optional_error": limit_list_error,
            "paths": {
                name: str(path.relative_to(root))
                for name, path in paths.items()
            },
            "meta_path": str(meta_path.relative_to(root)),
            "token_persisted": False,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        if not args.optional:
            raise
        print(
            json.dumps(
                {
                    "status": "pending",
                    "source": "tushare",
                    "trade_date": trade_date,
                    "reason": type(exc).__name__,
                    "message": str(exc),
                    "token_persisted": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
