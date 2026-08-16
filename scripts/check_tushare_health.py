#!/usr/bin/env python3
"""Read-only, fail-hard Tushare credential and entitlement health check."""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
import json
import os
import re
import sys
from typing import Any, Callable, Iterable
from urllib import error, request
from zoneinfo import ZoneInfo


API_URL = "https://api.tushare.pro"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_PROBE_CODES = ("600000.SH", "000001.SZ", "600519.SH")
AUCTION_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "vol",
    "amount",
    "vwap",
)
REALTIME_FIELDS = (
    "ts_code",
    "freq",
    "time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)
REALTIME_CLOCK_SKEW = timedelta(minutes=2)
ApiCall = Callable[
    [str, dict[str, Any], Iterable[str], str, int],
    tuple[list[str], list[list[Any]]],
]


class HealthCheckError(RuntimeError):
    """A health contract failed and the workflow must fail."""


def _safe_text(value: object, token: str) -> str:
    text = str(value or "")
    if token:
        text = text.replace(token, "***")
    return re.sub(r"[\r\n]+", " ", text)[:500]


def _api_call(
    api_name: str,
    params: dict[str, Any],
    fields: Iterable[str],
    token: str,
    timeout_seconds: int,
) -> tuple[list[str], list[list[Any]]]:
    payload = json.dumps(
        {
            "api_name": api_name,
            "token": token,
            "params": params,
            "fields": ",".join(fields),
        }
    ).encode("utf-8")
    http_request = request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "dc2.0-tushare-health/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw = response.read()
    except error.HTTPError as exc:
        raise HealthCheckError(f"{api_name}: HTTP {exc.code}") from exc
    except error.URLError as exc:
        reason = _safe_text(getattr(exc, "reason", "network error"), token)
        raise HealthCheckError(f"{api_name}: network error: {reason}") from exc

    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthCheckError(f"{api_name}: invalid JSON response") from exc
    if not isinstance(result, dict):
        raise HealthCheckError(f"{api_name}: invalid response object")

    try:
        code = int(result.get("code", -1))
    except (TypeError, ValueError) as exc:
        raise HealthCheckError(f"{api_name}: invalid API status code") from exc
    if code != 0:
        message = _safe_text(result.get("msg") or "request rejected", token)
        raise HealthCheckError(f"{api_name}: API code={code}: {message}")

    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise HealthCheckError(f"{api_name}: invalid data object")
    response_fields = list(data.get("fields") or [])
    rows = list(data.get("items") or [])
    if not all(isinstance(row, list) for row in rows):
        raise HealthCheckError(f"{api_name}: invalid data rows")
    return response_fields, rows


def _digits_date(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _calendar_map(
    fields: list[str], rows: list[list[Any]]
) -> dict[str, int]:
    if not {"exchange", "cal_date", "is_open"}.issubset(fields):
        raise HealthCheckError("trade_cal: required fields are missing")
    exchange_index = fields.index("exchange")
    date_index = fields.index("cal_date")
    open_index = fields.index("is_open")
    calendar: dict[str, int] = {}
    for row in rows:
        if max(exchange_index, date_index, open_index) >= len(row):
            continue
        if str(row[exchange_index] or "").strip().upper() != "SSE":
            continue
        cal_date = _digits_date(row[date_index])
        try:
            is_open = int(row[open_index])
        except (TypeError, ValueError):
            continue
        if cal_date and is_open in (0, 1):
            if cal_date in calendar and calendar[cal_date] != is_open:
                raise HealthCheckError(
                    f"trade_cal: conflicting is_open values for {cal_date}"
                )
            calendar[cal_date] = is_open
    if not calendar:
        raise HealthCheckError("trade_cal: no valid SSE calendar rows")
    return calendar


def _valid_rows(
    api_name: str,
    fields: list[str],
    rows: list[list[Any]],
    required_fields: Iterable[str],
) -> list[list[Any]]:
    """Reject partial schemas and rows missing any required value."""
    required = tuple(required_fields)
    missing = [field for field in required if field not in fields]
    if missing:
        raise HealthCheckError(
            f"{api_name}: required fields are missing: {','.join(missing)}"
        )
    indexes = [fields.index(field) for field in required]
    valid = [
        row
        for row in rows
        if max(indexes, default=-1) < len(row)
        and all(str(row[index] if row[index] is not None else "").strip() for index in indexes)
    ]
    if not valid:
        raise HealthCheckError(f"{api_name}: no valid rows")
    return valid


def _latest_completed_open_date(
    calendar: dict[str, int], now_shanghai: datetime
) -> str:
    today_text = now_shanghai.strftime("%Y%m%d")
    completed = sorted(
        cal_date
        for cal_date, is_open in calendar.items()
        if is_open == 1 and cal_date < today_text
    )
    if not completed:
        raise HealthCheckError("stk_auction_o: no completed open session in calendar window")
    return completed[-1]


def _parse_realtime_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        digits = "".join(ch for ch in text if ch.isdigit())
        parsed = None
        for width, date_format in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M")):
            if len(digits) >= width:
                try:
                    parsed = datetime.strptime(digits[:width], date_format)
                except ValueError:
                    continue
                break
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _valid_realtime_rows(
    fields: list[str],
    rows: list[list[Any]],
    *,
    probe_code: str,
    now_shanghai: datetime,
) -> list[list[Any]]:
    candidates = _valid_rows(
        "rt_min_daily", fields, rows, REALTIME_FIELDS
    )
    if len(candidates) != len(rows):
        raise HealthCheckError("rt_min_daily: one or more rows are incomplete")
    code_index = fields.index("ts_code")
    freq_index = fields.index("freq")
    time_index = fields.index("time")
    expected_code = str(probe_code or "").strip().upper()
    today = now_shanghai.date()
    latest_allowed = now_shanghai + REALTIME_CLOCK_SKEW
    for row in candidates:
        if str(row[code_index] or "").strip().upper() != expected_code:
            raise HealthCheckError("rt_min_daily: returned ts_code differs from probe")
        if str(row[freq_index] or "").strip().upper() != "1MIN":
            raise HealthCheckError("rt_min_daily: returned freq is not 1MIN")
        observed = _parse_realtime_timestamp(row[time_index])
        if observed is None:
            raise HealthCheckError("rt_min_daily: returned time is not a timestamp")
        if observed.date() != today:
            raise HealthCheckError("rt_min_daily: returned date is not Shanghai today")
        if observed > latest_allowed:
            raise HealthCheckError("rt_min_daily: returned time is later than allowed skew")
    return candidates


def _realtime_probe_applicable(now_shanghai: datetime, today_open: bool) -> tuple[bool, str]:
    if not today_open:
        return False, "exchange_closed_today"
    local_time = now_shanghai.timetz().replace(tzinfo=None)
    if not (time(9, 35) <= local_time <= time(15, 30)):
        return False, "outside_09:35_15:30_shanghai_window"
    return True, ""


def run_health_check(
    *,
    token: str,
    now_shanghai: datetime,
    timeout_seconds: int = 15,
    probe_codes: Iterable[str] = DEFAULT_PROBE_CODES,
    api_call: ApiCall | None = None,
) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        raise HealthCheckError("TUSHARE_TOKEN is not configured")
    probe_codes_tuple = tuple(probe_codes)
    api_call = api_call or _api_call
    if now_shanghai.tzinfo is None:
        now_shanghai = now_shanghai.replace(tzinfo=SHANGHAI)
    else:
        now_shanghai = now_shanghai.astimezone(SHANGHAI)

    today = now_shanghai.date()
    start = today - timedelta(days=45)
    end = today + timedelta(days=7)
    calendar_fields, calendar_rows = api_call(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        },
        ("exchange", "cal_date", "is_open", "pretrade_date"),
        token,
        timeout_seconds,
    )
    calendar = _calendar_map(calendar_fields, calendar_rows)
    today_text = today.strftime("%Y%m%d")
    if today_text not in calendar:
        raise HealthCheckError(f"trade_cal: today {today_text} is absent")

    checks: list[dict[str, Any]] = [
        {
            "name": "trade_cal",
            "status": "pass",
            "row_count": len(calendar),
            "today": today_text,
            "today_is_open": bool(calendar[today_text]),
        }
    ]

    auction_date = _latest_completed_open_date(calendar, now_shanghai)
    auction_fields, auction_rows = api_call(
        "stk_auction_o",
        {"trade_date": auction_date},
        AUCTION_FIELDS,
        token,
        timeout_seconds,
    )
    valid_auction_rows = _valid_rows(
        "stk_auction_o", auction_fields, auction_rows, AUCTION_FIELDS
    )
    auction_trade_date_index = auction_fields.index("trade_date")
    auction_code_index = auction_fields.index("ts_code")
    matching_auction_rows = [
        row
        for row in valid_auction_rows
        if _digits_date(row[auction_trade_date_index]) == auction_date
        and str(row[auction_code_index] or "").strip()
    ]
    if not matching_auction_rows:
        raise HealthCheckError(
            f"stk_auction_o: no valid rows for completed session {auction_date}"
        )
    checks.append(
        {
            "name": "stk_auction_o",
            "status": "pass",
            "trade_date": auction_date,
            "row_count": len(matching_auction_rows),
        }
    )

    applicable, reason = _realtime_probe_applicable(
        now_shanghai, bool(calendar[today_text])
    )
    if not applicable:
        entitlement_code = probe_codes_tuple[0] if probe_codes_tuple else ""
        if not entitlement_code:
            raise HealthCheckError("rt_min_daily: no entitlement probe code configured")
        _, entitlement_rows = api_call(
            "rt_min_daily",
            {"ts_code": entitlement_code, "freq": "1MIN"},
            REALTIME_FIELDS,
            token,
            timeout_seconds,
        )
        checks.append(
            {
                "name": "rt_min_daily",
                "status": "pass",
                "data_status": "not_applicable",
                "reason": reason,
                "ts_code": entitlement_code,
                "row_count": len(entitlement_rows),
            }
        )
    else:
        attempts: list[dict[str, Any]] = []
        realtime_rows = 0
        passing_code = ""
        for probe_code in probe_codes_tuple:
            realtime_fields, rows = api_call(
                "rt_min_daily",
                {"ts_code": probe_code, "freq": "1MIN"},
                REALTIME_FIELDS,
                token,
                timeout_seconds,
            )
            try:
                valid_realtime_rows = _valid_realtime_rows(
                    realtime_fields,
                    rows,
                    probe_code=probe_code,
                    now_shanghai=now_shanghai,
                )
            except HealthCheckError as exc:
                attempts.append(
                    {
                        "ts_code": probe_code,
                        "row_count": 0,
                        "status": _safe_text(exc, token),
                    }
                )
                continue
            attempts.append(
                {
                    "ts_code": probe_code,
                    "row_count": len(valid_realtime_rows),
                    "status": "pass",
                }
            )
            realtime_rows = len(valid_realtime_rows)
            passing_code = probe_code
            break
        if not realtime_rows:
            raise HealthCheckError(
                "rt_min_daily: no valid rows for all probe codes during the active window"
            )
        checks.append(
            {
                "name": "rt_min_daily",
                "status": "pass",
                "ts_code": passing_code,
                "row_count": realtime_rows,
                "attempts": attempts,
            }
        )

    return {
        "schema_version": 1,
        "system": "DC2.0",
        "overall_status": "pass",
        "checked_at_shanghai": now_shanghai.isoformat(),
        "checks": checks,
        "token_present": True,
        "token_persisted": False,
        "filesystem_writes": 0,
    }


def _parse_now(value: str) -> datetime:
    if not value:
        return datetime.now(SHANGHAI)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expect ISO-8601 date-time") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-hard, read-only DC2.0 Tushare health check")
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument(
        "--now-shanghai",
        default="",
        help="Test-only ISO time override; defaults to current Asia/Shanghai time",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = str(os.environ.get("TUSHARE_TOKEN", "") or "").strip()
    try:
        summary = run_health_check(
            token=token,
            now_shanghai=_parse_now(args.now_shanghai),
            timeout_seconds=max(1, int(args.timeout_seconds)),
        )
    except Exception as exc:
        error_text = _safe_text(exc, token)
        if not isinstance(exc, HealthCheckError):
            error_text = f"unexpected {type(exc).__name__}: {error_text}"
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "system": "DC2.0",
                    "overall_status": "fail",
                    "error": error_text,
                    "token_present": bool(token),
                    "token_persisted": False,
                    "filesystem_writes": 0,
                },
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
