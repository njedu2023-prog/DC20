#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.data.tushare_minute import (  # noqa: E402
    TushareClient,
    auction_open_output_path,
    normalize_code,
    write_auction_open_snapshot,
    write_calendar,
    write_minute_snapshot,
)
from top10decision.decision.eligibility import filter_standard_limit_universe  # noqa: E402


def _normal_date(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def _collect_codes(root: Path, trade_date: str, signal_date: str, max_codes: int) -> list[str]:
    codes: list[str] = []

    prediction_root = root / "outputs" / "auction_v3" / "predictions"
    for path in sorted(prediction_root.glob("pred_20*.csv")):
        frame = _read_csv(path)
        if frame.empty or "ts_code" not in frame.columns:
            continue
        frame, _ = filter_standard_limit_universe(frame, code_col="ts_code", name_col="name")
        if frame.empty:
            continue
        buy_dates = frame.get("expected_buy_date", pd.Series("", index=frame.index)).map(_normal_date)
        exit_dates = frame.get("expected_exit_date", pd.Series("", index=frame.index)).map(_normal_date)
        needed = frame[buy_dates.eq(trade_date) | exit_dates.eq(trade_date)]
        sort_columns = [
            column
            for column in ("selected", "stage_focus", "predicted_continuation_limit_up_probability", "conservative_ev")
            if column in needed.columns
        ]
        if sort_columns:
            needed = needed.sort_values(sort_columns, ascending=[False] * len(sort_columns), kind="stable")
        codes.extend(normalize_code(value) for value in needed["ts_code"])

    # Existing model outputs are first so formal actions and 2->3/3->4 watch names
    # cannot be displaced when an unusually large limit-up pool hits the cap.
    pred_source = root / "data" / "pred" / "pred_source_latest.csv"
    source = _read_csv(pred_source)
    if not source.empty:
        code_col = next((column for column in ("ts_code", "code", "代码") if column in source.columns), "")
        if code_col:
            source, _ = filter_standard_limit_universe(source, code_col=code_col, name_col="name")
        source_date = _normal_date(source.get("trade_date", pd.Series([""])).iloc[0])
        if (not signal_date or source_date == signal_date) and code_col:
            codes.extend(normalize_code(value) for value in source[code_col])

    return list(dict.fromkeys(code for code in codes if code))[:max_codes]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Tushare calendar and current 1-minute Decision truth")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--trade-date", default="", help="Current China-market date; defaults to Asia/Shanghai today")
    parser.add_argument("--signal-date", default="", help="Optional D signal date used to select current candidates")
    parser.add_argument("--max-codes", type=int, default=80)
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--calendar-only", action="store_true", help="Sync the strict SSE calendar without minute requests")
    parser.add_argument(
        "--auction-only",
        action="store_true",
        help="Sync official 9:30 opening-auction truth without minute requests",
    )
    parser.add_argument("--optional", action="store_true", help="Exit successfully when the secret or minute data is unavailable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    trade_date = _normal_date(args.trade_date) or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    signal_date = _normal_date(args.signal_date)

    if not str(os.environ.get("TUSHARE_TOKEN", "") or "").strip():
        if args.optional:
            print("[tushare-minute] TUSHARE_TOKEN unavailable; optional sync skipped")
            return 0
        raise RuntimeError("TUSHARE_TOKEN is not configured")

    client = TushareClient.from_env(timeout_seconds=args.timeout_seconds)
    try:
        calendar = client.trade_calendar(f"{trade_date[:4]}0101", f"{trade_date[:4]}1231")
        calendar_path = write_calendar(calendar, root)
    except Exception as exc:
        calendar_path = root / "data" / "market" / "trade_cal_sse.csv"
        if not args.optional or not calendar_path.exists():
            raise
        calendar = _read_csv(calendar_path)
        if calendar.empty or not {"cal_date", "is_open"}.issubset(calendar.columns):
            raise RuntimeError("committed A-share calendar is unavailable after Tushare failure") from exc
        print(
            f"[tushare-minute] calendar refresh failed ({type(exc).__name__}); "
            "using committed strict SSE calendar"
        )
    open_map = dict(zip(calendar["cal_date"].astype(str), calendar["is_open"].astype(int)))

    # rt_min_daily is a same-day feed. Refuse to label it as historical data.
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    codes = (
        []
        if args.calendar_only
        else _collect_codes(
            root,
            trade_date,
            signal_date,
            max(1, int(args.max_codes)),
        )
    )
    auction_rows = 0
    auction_status = "calendar_only"
    auction_path = auction_open_output_path(root, trade_date)
    if not args.calendar_only and open_map.get(trade_date, 0) == 1 and codes:
        if auction_path.exists() and auction_path.stat().st_size > 0:
            auction_rows = int(len(_read_csv(auction_path)))
            auction_status = "existing_immutable_partition"
        else:
            try:
                auction = client.opening_auction(trade_date)
                if auction.empty:
                    auction_status = "source_pending_or_empty"
                else:
                    path, _ = write_auction_open_snapshot(
                        auction,
                        root,
                        trade_date,
                        selected_codes=codes,
                    )
                    auction_rows = int(len(_read_csv(path)))
                    auction_status = "written"
            except Exception as exc:
                auction_status = f"unavailable:{type(exc).__name__}"
                if not args.optional:
                    raise
    written = 0
    failures: list[dict[str, str]] = []
    if (
        not args.auction_only
        and trade_date == today
        and open_map.get(trade_date, 0) == 1
        and codes
    ):
        def fetch_one(code: str) -> tuple[str, bool, str]:
            try:
                minute = client.current_minute(code)
                if minute.empty:
                    return code, False, "empty"
                write_minute_snapshot(minute, root, trade_date, code)
                return code, True, ""
            except Exception as exc:
                return code, False, type(exc).__name__

        workers = min(max(1, int(args.workers)), len(codes))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tushare-minute") as pool:
            futures = [pool.submit(fetch_one, code) for code in codes]
            for future in as_completed(futures):
                code, success, reason = future.result()
                if success:
                    written += 1
                else:
                    failures.append({"ts_code": code, "reason": reason})

    summary = {
        "source": "tushare",
        "trade_date": trade_date,
        "signal_date": signal_date,
        "calendar_path": str(calendar_path.relative_to(root)),
        "calendar_rows": int(len(calendar)),
        "candidate_codes": int(len(codes)),
        "request_timeout_seconds": max(1, int(args.timeout_seconds)),
        "workers": min(max(1, int(args.workers)), max(1, len(codes))),
        "minute_files_written": int(written),
        "minute_sync_skipped_non_current_date": trade_date != today,
        "auction_truth_source": "tushare:stk_auction_o",
        "auction_truth_status": auction_status,
        "auction_truth_rows": auction_rows,
        "auction_truth_path": (
            str(auction_path.relative_to(root))
            if auction_path.exists()
            else ""
        ),
        "failures": failures[:20],
        "token_persisted": False,
    }
    meta_path = root / "data" / "market" / "minute_1m" / "sync_latest.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
