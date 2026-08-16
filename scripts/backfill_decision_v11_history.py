#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine  # noqa: E402
from top10decision.auction_v3.config import (  # noqa: E402
    TARGET_HISTORY_DATES,
    TARGET_INDEPENDENT_OOS_DATES,
    WALKFORWARD_WARMUP_DATES,
)
from top10decision.data.tushare_minute import (  # noqa: E402
    AUCTION_OPEN_FIELDS,
    TushareClient,
    write_calendar,
)
from top10decision.decision.contracts import (  # noqa: E402
    EXIT_LATEST_TIME,
    EXIT_POLICY_VERSION,
    EXIT_STOP_LOSS_PCT,
    EXIT_TAKE_PROFIT_PCT,
    HISTORY_CONTRACT_VERSION,
)


DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
LIMIT_FIELDS = (
    "ts_code",
    "trade_date",
    "pre_close",
    "up_limit",
    "down_limit",
)
DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "turnover_rate",
    "volume_ratio",
    "total_mv",
    "circ_mv",
)
LIMIT_LIST_FIELDS = (
    "trade_date",
    "ts_code",
    "industry",
    "name",
    "close",
    "pct_chg",
    "amount",
    "float_mv",
    "total_mv",
    "turnover_ratio",
    "fd_amount",
    "first_time",
    "last_time",
    "open_times",
    "limit_times",
    "limit",
)
MARKET_DATA_READY_TIME = clock_time(18, 0)


def _date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _sha256_frame(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _call_paged(
    client: TushareClient,
    api_name: str,
    params: dict[str, Any],
    fields: Iterable[str],
    *,
    page_size: int = 6_000,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for page in range(3):
        page_params = {
            **params,
            "limit": page_size,
            "offset": page * page_size,
        }
        frame = client.call(api_name, page_params, fields)
        if frame.empty:
            break
        parts.append(frame)
        if len(frame) < page_size:
            break
    if not parts:
        return pd.DataFrame(columns=list(fields))
    out = pd.concat(parts, ignore_index=True)
    if "ts_code" in out.columns:
        out = out.drop_duplicates("ts_code", keep="last")
    return out.reset_index(drop=True)


def _retry_frame(
    fetch: Callable[[], pd.DataFrame],
    *,
    label: str,
    required: bool = False,
    attempts: int = 4,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            frame = fetch()
            if not required or not frame.empty:
                return frame
            last_error = RuntimeError(f"{label} returned no rows")
        except Exception as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(float(attempt * 2))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{label} fetch failed")


def _completed_open_dates(
    open_dates: Iterable[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    dates = [value for value in (_date(item) for item in open_dates) if value]
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    today = current.strftime("%Y%m%d")
    if (
        dates
        and dates[-1] == today
        and current.time().replace(tzinfo=None) < MARKET_DATA_READY_TIME
    ):
        dates = dates[:-1]
    return dates


def _confirmed_limit_up_list(
    daily: pd.DataFrame,
    limits: pd.DataFrame,
    detail: pd.DataFrame,
) -> pd.DataFrame:
    if not detail.empty:
        out = detail.copy()
        if "limit" in out.columns:
            limit_type = out["limit"].astype(str).str.upper().str.strip()
            out = out[limit_type.isin({"U", "UP", "涨停", ""})]
            out = out.rename(columns={"limit": "limit_type"})
        if "limit_type" not in out.columns:
            out["limit_type"] = "U"
    else:
        out = daily.merge(
            limits[["ts_code", "up_limit"]],
            on="ts_code",
            how="inner",
        )
        close = pd.to_numeric(out["close"], errors="coerce")
        up_limit = pd.to_numeric(out["up_limit"], errors="coerce")
        out = out[(close - up_limit).abs().le(0.011)].copy()
        out["limit_type"] = "U"
        out["name"] = ""
        out["industry"] = ""
        out["open_times"] = pd.NA
        out["fd_amount"] = pd.NA
    if out.empty:
        return out
    sort_columns = [
        column
        for column in ("limit_times", "fd_amount", "amount", "ts_code")
        if column in out.columns
    ]
    ascending = [
        column == "ts_code"
        for column in sort_columns
    ]
    if sort_columns:
        out = out.sort_values(
            sort_columns,
            ascending=ascending,
            na_position="last",
            kind="stable",
        )
    out["rank"] = range(1, len(out) + 1)
    return out.drop_duplicates("ts_code", keep="first").reset_index(drop=True)


def _covered_dates(history_root: Path) -> set[str]:
    covered: set[str] = set()
    for path in history_root.glob("training_*.csv"):
        frame = _read_csv(path)
        if "signal_date" in frame.columns:
            covered.update(
                value
                for value in frame["signal_date"].map(_date)
                if value
            )
    return covered


def _latest_target_dates(
    open_dates: list[str],
    covered: set[str],
    *,
    max_missing_dates: int,
    target_sessions: int = TARGET_HISTORY_DATES,
    maturity_lag_sessions: int = 8,
) -> tuple[list[str], list[str]]:
    eligible = (
        open_dates[:-maturity_lag_sessions]
        if len(open_dates) > maturity_lag_sessions
        else []
    )
    window = eligible[-target_sessions:]
    missing = [date for date in window if date not in covered]
    return window, missing[: max(0, int(max_missing_dates))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build strict-calendar Decision V11 history for at least 500 "
            "independent OOS sessions plus a conservative model warmup from "
            "Tushare daily and auction truth"
        )
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--start-date", default="20200101")
    parser.add_argument(
        "--end-date",
        default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"),
    )
    parser.add_argument("--max-missing-dates", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--request-sleep", type=float, default=0.08)
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Commit successful dates and exit zero when an endpoint is unavailable",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    start_date = _date(args.start_date)
    end_date = _date(args.end_date)
    if not start_date or not end_date or start_date > end_date:
        raise ValueError("start/end date must be valid YYYYMMDD")
    if not str(os.environ.get("TUSHARE_TOKEN", "") or "").strip():
        if args.optional:
            print("[decision-v11-backfill] TUSHARE_TOKEN unavailable; skipped")
            return 0
        raise RuntimeError("TUSHARE_TOKEN is not configured")

    client = TushareClient.from_env(
        timeout_seconds=max(1, int(args.timeout_seconds))
    )
    calendar = client.trade_calendar(start_date, end_date)
    write_calendar(calendar, root)
    open_dates = _completed_open_dates(
        calendar.loc[
            calendar["is_open"].eq(1), "cal_date"
        ].astype(str).tolist()
    )
    history_root = AuctionV3Config(root=root).historical_training_root
    history_root.mkdir(parents=True, exist_ok=True)
    covered = _covered_dates(history_root)
    target_window, target_dates = _latest_target_dates(
        open_dates,
        covered,
        max_missing_dates=args.max_missing_dates,
    )
    if not target_dates:
        print(
            json.dumps(
                {
                    "status": "up_to_date",
                    "calendar_open_dates": len(open_dates),
                    "covered_signal_dates": len(covered),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    first_index = max(0, open_dates.index(target_dates[0]) - 5)
    last_index = min(
        len(open_dates),
        open_dates.index(target_dates[-1]) + 9,
    )
    fetch_dates = open_dates[first_index:last_index]
    failures: list[dict[str, str]] = []
    endpoint_rows: dict[str, int] = {
        "daily": 0,
        "stk_limit": 0,
        "daily_basic": 0,
        "limit_list_d": 0,
        "stk_auction_o": 0,
    }

    with tempfile.TemporaryDirectory(
        prefix="decision-v11-backfill-"
    ) as temp_name:
        temp_root = Path(temp_name)
        for index, trade_date in enumerate(fetch_dates, start=1):
            market_root = (
                temp_root
                / "data"
                / "market"
                / "raw"
                / trade_date[:4]
                / trade_date
            )
            market_root.mkdir(parents=True, exist_ok=True)
            try:
                daily = _retry_frame(
                    lambda: _call_paged(
                        client,
                        "daily",
                        {"trade_date": trade_date},
                        DAILY_FIELDS,
                    ),
                    label=f"daily:{trade_date}",
                    required=True,
                )
                time.sleep(max(0.0, float(args.request_sleep)))
                limits = _retry_frame(
                    lambda: _call_paged(
                        client,
                        "stk_limit",
                        {"trade_date": trade_date},
                        LIMIT_FIELDS,
                    ),
                    label=f"stk_limit:{trade_date}",
                    required=True,
                )
                time.sleep(max(0.0, float(args.request_sleep)))
                daily_basic = _retry_frame(
                    lambda: _call_paged(
                        client,
                        "daily_basic",
                        {"trade_date": trade_date},
                        DAILY_BASIC_FIELDS,
                    ),
                    label=f"daily_basic:{trade_date}",
                )
                if "circ_mv" in daily_basic.columns:
                    daily_basic = daily_basic.rename(
                        columns={"circ_mv": "float_mv"}
                    )
                time.sleep(max(0.0, float(args.request_sleep)))
                try:
                    detail = _retry_frame(
                        lambda: _call_paged(
                            client,
                            "limit_list_d",
                            {"trade_date": trade_date},
                            LIMIT_LIST_FIELDS,
                        ),
                        label=f"limit_list_d:{trade_date}",
                    )
                except Exception as exc:
                    detail = pd.DataFrame()
                    failures.append(
                        {
                            "trade_date": trade_date,
                            "endpoint": "limit_list_d",
                            "reason": type(exc).__name__,
                        }
                    )
                detail = _confirmed_limit_up_list(daily, limits, detail)
                time.sleep(max(0.0, float(args.request_sleep)))
                auction = _retry_frame(
                    lambda: client.call(
                        "stk_auction_o",
                        {"trade_date": trade_date},
                        AUCTION_OPEN_FIELDS,
                    ),
                    label=f"stk_auction_o:{trade_date}",
                )
                _write_csv(daily, market_root / "daily.csv")
                _write_csv(limits, market_root / "stk_limit.csv")
                _write_csv(daily_basic, market_root / "daily_basic.csv")
                _write_csv(detail, market_root / "limit_list_d.csv")
                _write_csv(auction, market_root / "stk_auction_o.csv")
                endpoint_rows["daily"] += len(daily)
                endpoint_rows["stk_limit"] += len(limits)
                endpoint_rows["daily_basic"] += len(daily_basic)
                endpoint_rows["limit_list_d"] += len(detail)
                endpoint_rows["stk_auction_o"] += len(auction)
                if index == 1 or index % 10 == 0 or index == len(fetch_dates):
                    print(
                        "[decision-v11-backfill] "
                        f"fetched {index}/{len(fetch_dates)} dependency dates; "
                        f"current={trade_date}",
                        flush=True,
                    )
            except Exception as exc:
                failures.append(
                    {
                        "trade_date": trade_date,
                        "endpoint": "core",
                        "reason": type(exc).__name__,
                    }
                )
                if not args.optional:
                    raise

        temp_calendar = temp_root / "data" / "market" / "trade_cal_sse.csv"
        temp_calendar.parent.mkdir(parents=True, exist_ok=True)
        calendar.to_csv(temp_calendar, index=False, encoding="utf-8-sig")
        config = AuctionV3Config(
            root=temp_root,
            min_train_dates=2,
            min_train_rows=10,
            require_intraday_exit_truth=False,
        )
        engine = AuctionV3Engine(config)
        history = engine.build_history()
        if not history.empty:
            history = history[
                history["signal_date"].astype(str).isin(target_dates)
            ].copy()

    if history.empty:
        summary = {
            "status": "no_training_rows",
            "target_dates": target_dates,
            "fetch_dates": fetch_dates,
            "failures": failures,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if args.optional else 1

    history["history_source"] = "tushare_compact_backfill"
    history["history_contract_version"] = HISTORY_CONTRACT_VERSION
    history["exit_policy_version"] = EXIT_POLICY_VERSION
    history["take_profit_pct"] = EXIT_TAKE_PROFIT_PCT
    history["stop_loss_pct"] = EXIT_STOP_LOSS_PCT
    history["latest_exit_time"] = EXIT_LATEST_TIME
    history["backfill_generated_at_utc"] = (
        datetime.now(ZoneInfo("UTC"))
        .replace(microsecond=0)
        .isoformat()
    )
    history = history.sort_values(
        ["signal_date", "source_rank", "ts_code"],
        kind="stable",
    ).reset_index(drop=True)
    output_path = history_root / (
        f"training_{target_dates[0]}_{target_dates[-1]}.csv"
    )
    if output_path.exists():
        existing = _read_csv(output_path)
        if _sha256_frame(existing) != _sha256_frame(history):
            raise RuntimeError(
                f"immutable compact history conflict: {output_path}"
            )
    else:
        _write_csv(history, output_path)

    all_covered = _covered_dates(history_root)
    official_auction_rows = int(
        history.get(
            "auction_truth_source",
            pd.Series(dtype=str),
        ).eq("tushare_stk_auction_o").sum()
    )
    manifest = {
        "schema_version": "decision_v11_history_manifest_v1",
        "generated_at_utc": datetime.now(ZoneInfo("UTC"))
        .replace(microsecond=0)
        .isoformat(),
        "calendar_source": "tushare:trade_cal:SSE",
        "strict_calendar": True,
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "target_signal_dates": target_dates,
        "target_window_start": target_window[0] if target_window else "",
        "target_window_end": target_window[-1] if target_window else "",
        "target_window_open_sessions": len(target_window),
        "target_history_sessions": TARGET_HISTORY_DATES,
        "walkforward_warmup_sessions": WALKFORWARD_WARMUP_DATES,
        "target_signal_date_count": len(target_dates),
        "produced_signal_dates": int(
            history["signal_date"].astype(str).nunique()
        ),
        "produced_rows": int(len(history)),
        "official_auction_truth_rows": official_auction_rows,
        "auction_truth_coverage": (
            official_auction_rows / len(history)
            if len(history)
            else 0.0
        ),
        "total_compact_signal_dates": len(all_covered),
        "target_independent_dates": TARGET_INDEPENDENT_OOS_DATES,
        "exit_policy": {
            "version": EXIT_POLICY_VERSION,
            "take_profit_pct": EXIT_TAKE_PROFIT_PCT,
            "stop_loss_pct": EXIT_STOP_LOSS_PCT,
            "latest_exit_time": EXIT_LATEST_TIME,
            "requires_intraday_truth": False,
        },
        "output_file": str(output_path.relative_to(root)),
        "output_sha256": _sha256_frame(history),
        "endpoint_rows": endpoint_rows,
        "failures": failures,
        "credential_persisted": False,
    }
    (history_root / "manifest_latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
