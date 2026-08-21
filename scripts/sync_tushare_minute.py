#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
from datetime import datetime, time
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
from top10decision.rt_min_contract import RTMinContractError  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
ACTIVE_MINUTE_START = time(9, 35)
ACTIVE_MINUTE_END = time(15, 30)
POST_CLOSE_TRUTH_START = time(15, 30)


class MinuteSyncError(RuntimeError):
    """The minute sync contract failed and the caller must fail closed."""


def _normal_date(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def _committed_calendar_state(root: Path, trade_date: str) -> int | None:
    calendar = _read_csv(root / "data" / "market" / "trade_cal_sse.csv")
    if calendar.empty or not {"cal_date", "is_open"}.issubset(calendar.columns):
        return None
    dates = calendar["cal_date"].map(_normal_date)
    values = pd.to_numeric(
        calendar.loc[dates.eq(trade_date), "is_open"], errors="coerce"
    ).dropna()
    states = {int(value) for value in values if int(value) in (0, 1)}
    return states.pop() if len(states) == 1 else None


def _active_minute_window(
    now_shanghai: datetime,
    *,
    trade_date: str,
    is_open: bool,
) -> bool:
    local = now_shanghai.astimezone(SHANGHAI)
    clock = local.timetz().replace(tzinfo=None)
    return (
        is_open
        and trade_date == local.strftime("%Y%m%d")
        and ACTIVE_MINUTE_START <= clock <= ACTIVE_MINUTE_END
    )


def _valid_minute_rows(
    frame: pd.DataFrame,
    *,
    trade_date: str,
) -> pd.DataFrame:
    required = {"time", "open", "close"}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame()
    out = frame.copy()
    dates = out["time"].map(_normal_date)
    out["open"] = pd.to_numeric(out["open"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out[dates.eq(trade_date)].dropna(subset=["open", "close"])


def _summary(
    *,
    status: str,
    reason: str,
    trade_date: str,
    signal_date: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "schema_version": "decision_tushare_minute_sync_v2",
        "source": "tushare",
        "status": status,
        "reason": reason,
        "trade_date": trade_date,
        "signal_date": signal_date,
        "token_persisted": False,
        **extra,
    }


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--post-close-truth",
        action="store_true",
        help=(
            "Sync current-session full-day minute truth after 15:30 Shanghai; "
            "never treats historical or closed sessions as live"
        ),
    )
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Allow unavailable data only with --dry-run, --research-mode, or a proven closed session",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate invocation and emit a not-applicable result without network or filesystem writes",
    )
    parser.add_argument(
        "--research-mode",
        action="store_true",
        help="Explicit non-production research path; requires --optional to soften unavailable data",
    )
    return parser.parse_args(argv)


def run_sync(
    args: argparse.Namespace,
    *,
    now_shanghai: datetime | None = None,
) -> dict[str, object]:
    root = Path(args.root).resolve()
    now_shanghai = now_shanghai or datetime.now(SHANGHAI)
    if now_shanghai.tzinfo is None:
        now_shanghai = now_shanghai.replace(tzinfo=SHANGHAI)
    else:
        now_shanghai = now_shanghai.astimezone(SHANGHAI)
    trade_date = _normal_date(args.trade_date) or now_shanghai.strftime("%Y%m%d")
    signal_date = _normal_date(args.signal_date)
    if args.calendar_only and args.auction_only:
        raise MinuteSyncError("calendar_only_and_auction_only_are_mutually_exclusive")
    if args.post_close_truth and (args.calendar_only or args.auction_only):
        raise MinuteSyncError(
            "post_close_truth_is_mutually_exclusive_with_calendar_or_auction_only"
        )
    if args.research_mode and not args.optional:
        raise MinuteSyncError("research_mode_requires_optional")
    if args.dry_run:
        return _summary(
            status="not_applicable",
            reason="dry_run",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=False,
            filesystem_writes=0,
            sync_meta_written=False,
        )

    today = now_shanghai.strftime("%Y%m%d")
    local_clock = now_shanghai.timetz().replace(tzinfo=None)
    current_clock_is_active = (
        trade_date == today
        and ACTIVE_MINUTE_START <= local_clock <= ACTIVE_MINUTE_END
    )
    if args.post_close_truth and trade_date != today:
        return _summary(
            status="not_applicable",
            reason="non_current_trade_date",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=False,
            post_close_truth=True,
            candidate_codes=0,
            minute_files_written=0,
            network_requests=0,
            market_data_network_requests=0,
            market_data_files_written=0,
            filesystem_writes=0,
            sync_meta_written=False,
        )
    if args.post_close_truth and local_clock <= POST_CLOSE_TRUTH_START:
        return _summary(
            status="not_applicable",
            reason="outside_post_close_truth_window",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=False,
            post_close_truth=True,
            candidate_codes=0,
            minute_files_written=0,
            network_requests=0,
            market_data_network_requests=0,
            market_data_files_written=0,
            filesystem_writes=0,
            sync_meta_written=False,
        )
    if (
        trade_date == today
        and not current_clock_is_active
        and not args.calendar_only
        and not args.auction_only
        and not args.post_close_truth
    ):
        return _summary(
            status="not_applicable",
            reason="outside_active_minute_window",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=False,
            candidate_codes=0,
            minute_files_written=0,
            network_requests=0,
            market_data_network_requests=0,
            market_data_files_written=0,
            filesystem_writes=0,
            sync_meta_written=False,
        )

    token = str(os.environ.get("TUSHARE_TOKEN", "") or "").strip()
    if not token:
        committed_state = _committed_calendar_state(root, trade_date)
        if args.optional and args.research_mode:
            return _summary(
                status="not_applicable",
                reason="research_mode_token_unavailable",
                trade_date=trade_date,
                signal_date=signal_date,
                active_window=False,
                filesystem_writes=0,
                sync_meta_written=False,
            )
        if args.optional and committed_state == 0:
            return _summary(
                status="not_applicable",
                reason="exchange_closed_committed_calendar",
                trade_date=trade_date,
                signal_date=signal_date,
                active_window=False,
                filesystem_writes=0,
                sync_meta_written=False,
            )
        raise MinuteSyncError("tushare_token_not_configured")

    client = TushareClient.from_env(timeout_seconds=args.timeout_seconds)
    try:
        calendar = client.trade_calendar(f"{trade_date[:4]}0101", f"{trade_date[:4]}1231")
        calendar_path = write_calendar(calendar, root)
    except Exception as exc:
        calendar_path = root / "data" / "market" / "trade_cal_sse.csv"
        committed_state = _committed_calendar_state(root, trade_date)
        if args.optional and committed_state == 0:
            return _summary(
                status="not_applicable",
                reason="exchange_closed_committed_calendar",
                trade_date=trade_date,
                signal_date=signal_date,
                active_window=False,
                calendar_refresh_error=type(exc).__name__,
                filesystem_writes=0,
                sync_meta_written=False,
            )
        if not (args.optional and args.research_mode and calendar_path.exists()):
            raise MinuteSyncError(
                f"calendar_refresh_failed:{type(exc).__name__}"
            ) from exc
        calendar = _read_csv(calendar_path)
        if calendar.empty or not {"cal_date", "is_open"}.issubset(calendar.columns):
            raise MinuteSyncError("committed_calendar_unavailable") from exc
    calendar_dates = calendar["cal_date"].map(_normal_date)
    calendar_open = pd.to_numeric(calendar["is_open"], errors="coerce")
    open_map = {
        cal_date: int(is_open)
        for cal_date, is_open in zip(calendar_dates, calendar_open)
        if cal_date and pd.notna(is_open) and int(is_open) in (0, 1)
    }
    if trade_date not in open_map:
        raise MinuteSyncError("trade_date_absent_from_calendar")
    is_open = open_map[trade_date] == 1
    active_window = _active_minute_window(
        now_shanghai,
        trade_date=trade_date,
        is_open=is_open,
    )
    if not is_open:
        return _summary(
            status="not_applicable",
            reason="exchange_closed",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=False,
            calendar_rows=int(len(calendar)),
            filesystem_writes=1,
            sync_meta_written=False,
        )
    if args.optional and not args.research_mode:
        raise MinuteSyncError("optional_not_allowed_for_open_production_session")
    if args.calendar_only:
        return _summary(
            status="success",
            reason="calendar_synced",
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=active_window,
            calendar_rows=int(len(calendar)),
            filesystem_writes=1,
            sync_meta_written=False,
        )

    # rt_min_daily is a same-day feed. Refuse to label it as historical data.
    post_close_truth_window = (
        args.post_close_truth
        and trade_date == today
        and is_open
        and local_clock > POST_CLOSE_TRUTH_START
    )
    codes = _collect_codes(
        root,
        trade_date,
        signal_date,
        max(1, int(args.max_codes)),
    )
    if not codes:
        if active_window or args.auction_only:
            raise MinuteSyncError("no_candidate_codes_for_required_sync")
        return _summary(
            status="not_applicable",
            reason=(
                "non_current_trade_date"
                if trade_date != today
                else "outside_active_minute_window"
            ),
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=active_window,
            candidate_codes=0,
            filesystem_writes=1,
            sync_meta_written=False,
        )
    auction_rows = 0
    auction_status = "not_requested"
    market_data_network_requests = 0
    auction_path = auction_open_output_path(root, trade_date)
    if is_open and not post_close_truth_window:
        if auction_path.exists() and auction_path.stat().st_size > 0:
            auction_rows = int(len(_read_csv(auction_path)))
            auction_status = (
                "existing_immutable_partition"
                if auction_rows > 0
                else "existing_partition_has_no_valid_rows"
            )
        else:
            try:
                market_data_network_requests += 1
                auction = client.opening_auction(trade_date)
                if auction.empty or "ts_code" not in auction.columns:
                    auction_status = "source_has_no_valid_rows"
                else:
                    selected = auction[
                        auction["ts_code"].map(normalize_code).isin(set(codes))
                    ].copy()
                    if selected.empty:
                        auction_status = "source_has_no_candidate_rows"
                    else:
                        path, _ = write_auction_open_snapshot(
                            selected,
                            root,
                            trade_date,
                            selected_codes=codes,
                        )
                        auction_rows = int(len(_read_csv(path)))
                        auction_status = "written"
            except Exception as exc:
                auction_status = f"unavailable:{type(exc).__name__}"
                if not (args.optional and args.research_mode):
                    raise MinuteSyncError(auction_status) from exc
    written = 0
    failures: list[dict[str, str]] = []
    if (
        not args.auction_only
        and trade_date == today
        and is_open
        and codes
        and (active_window or post_close_truth_window)
    ):
        market_data_network_requests += len(codes)
        fetched: dict[str, pd.DataFrame] = {}
        hard_contract_failures: list[str] = []

        def fetch_one(
            code: str,
        ) -> tuple[str, pd.DataFrame | None, str, bool]:
            try:
                minute = client.current_minute(code)
                valid = _valid_minute_rows(minute, trade_date=trade_date)
                if valid.empty:
                    return code, None, "no_valid_rows", False
                return code, valid, "", False
            except RTMinContractError as exc:
                return code, None, exc.reason, True
            except Exception as exc:
                return code, None, type(exc).__name__, False

        workers = min(max(1, int(args.workers)), len(codes))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tushare-minute") as pool:
            futures = [pool.submit(fetch_one, code) for code in codes]
            for future in as_completed(futures):
                code, valid, reason, hard_contract_failure = future.result()
                if hard_contract_failure:
                    hard_contract_failures.append(code)
                elif valid is not None:
                    fetched[code] = valid
                else:
                    failures.append({"ts_code": code, "reason": reason})
        if hard_contract_failures:
            raise MinuteSyncError("rt_min_contract_failure")
        for code in codes:
            valid = fetched.get(code)
            if valid is None:
                continue
            write_minute_snapshot(valid, root, trade_date, code)
            written += 1

    if args.auction_only and auction_rows == 0:
        if args.optional and args.research_mode:
            return _summary(
                status="not_applicable",
                reason="research_mode_no_valid_auction_rows",
                trade_date=trade_date,
                signal_date=signal_date,
                active_window=active_window,
                candidate_codes=int(len(codes)),
                auction_truth_status=auction_status,
                sync_meta_written=False,
            )
        raise MinuteSyncError("no_valid_auction_rows")
    if not args.auction_only and active_window and written == 0:
        if args.optional and args.research_mode:
            return _summary(
                status="not_applicable",
                reason="research_mode_no_valid_minute_rows",
                trade_date=trade_date,
                signal_date=signal_date,
                active_window=True,
                candidate_codes=int(len(codes)),
                failures=failures[:20],
                sync_meta_written=False,
            )
        raise MinuteSyncError("no_valid_minute_rows_active_window")
    if post_close_truth_window and written == 0:
        raise MinuteSyncError("no_valid_minute_rows_post_close_truth")
    if not args.auction_only and written == 0:
        return _summary(
            status="not_applicable",
            reason=(
                "non_current_trade_date"
                if trade_date != today
                else "outside_active_minute_window"
            ),
            trade_date=trade_date,
            signal_date=signal_date,
            active_window=active_window,
            candidate_codes=int(len(codes)),
            auction_truth_rows=auction_rows,
            auction_truth_status=auction_status,
            failures=failures[:20],
            sync_meta_written=False,
        )

    partial = bool(failures) or (not args.auction_only and written < len(codes))
    if not post_close_truth_window:
        partial = partial or auction_rows == 0
    summary = _summary(
        status="partial_success" if partial else "success",
        reason=(
            "auction_sync_success"
            if args.auction_only
            else (
                "post_close_truth_partial"
                if partial
                else "post_close_truth_success"
            )
            if post_close_truth_window
            else "minute_partial_success"
            if partial
            else "minute_sync_success"
        ),
        trade_date=trade_date,
        signal_date=signal_date,
        calendar_path=str(calendar_path.relative_to(root)),
        calendar_rows=int(len(calendar)),
        candidate_codes=int(len(codes)),
        request_timeout_seconds=max(1, int(args.timeout_seconds)),
        workers=min(max(1, int(args.workers)), max(1, len(codes))),
        minute_files_written=int(written),
        minute_sync_skipped_non_current_date=trade_date != today,
        auction_truth_source="tushare:stk_auction_o",
        auction_truth_status=auction_status,
        auction_truth_rows=auction_rows,
        auction_truth_path=(
            str(auction_path.relative_to(root))
            if auction_path.exists()
            else ""
        ),
        failures=failures[:20],
        active_window=active_window,
        post_close_truth=post_close_truth_window,
        market_data_network_requests=int(market_data_network_requests),
        market_data_files_written=int(written) + int(auction_status == "written"),
        sync_meta_written=True,
    )
    meta_path = root / "data" / "market" / "minute_1m" / "sync_latest.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(
    argv: list[str] | None = None,
    *,
    now_shanghai: datetime | None = None,
) -> int:
    args = parse_args(argv)
    token = str(os.environ.get("TUSHARE_TOKEN", "") or "").strip()
    effective_now = now_shanghai or datetime.now(SHANGHAI)
    try:
        summary = run_sync(args, now_shanghai=effective_now)
    except Exception as exc:
        reason = str(exc or type(exc).__name__).replace(token, "***") if token else str(exc)
        print(
            json.dumps(
                _summary(
                    status="fail",
                    reason=reason,
                    trade_date=(
                        _normal_date(args.trade_date)
                        or effective_now.astimezone(SHANGHAI).strftime("%Y%m%d")
                    ),
                    signal_date=_normal_date(args.signal_date),
                    token_present=bool(token),
                    sync_meta_written=False,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
