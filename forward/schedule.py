"""Strict local calendar and aware-time gates. Never query a calendar online."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")


def aware(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("explicit timestamp with timezone required")
    return value


def read_calendar(path: Path, expected_sha256: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("calendar must be a committed regular file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("SSE calendar hash mismatch")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    dates: set[str] = set()
    opened = []
    for row in rows:
        date = row["cal_date"]
        datetime.strptime(date, "%Y%m%d")
        if row["exchange"] != "SSE" or date in dates or row["is_open"] not in {"0", "1"}:
            raise ValueError("invalid or duplicate SSE calendar record")
        dates.add(date)
        if row["is_open"] == "1":
            opened.append(date)
    if not opened:
        raise ValueError("empty SSE calendar")
    return sorted(opened)


def dates_for(signal_date: str, open_dates: list[str]) -> tuple[str, str, str]:
    if open_dates != sorted(set(open_dates)):
        raise ValueError("open dates must be unique and sorted")
    if signal_date not in open_dates:
        raise ValueError("target D is not an SSE open date")
    position = open_dates.index(signal_date)
    if position + 2 >= len(open_dates):
        raise ValueError("calendar does not cover T and T+1")
    return tuple(open_dates[position:position + 3])


def bind_slot(nominal_slot_utc: str, created_at_utc: str, now_utc: str,
              open_dates: list[str], *, max_delay_hours: int = 12) -> dict:
    """Bind to the intended D, not the date of delayed execution."""
    slot, created, now = map(aware, (nominal_slot_utc, created_at_utc, now_utc))
    if max_delay_hours != 12:
        raise ValueError("unreviewed schedule delay policy")
    if not slot <= created <= now < slot + timedelta(hours=max_delay_hours):
        raise ValueError("schedule outside its original strict 12-hour window")
    local = slot.astimezone(BJ)
    if (local.hour, local.minute, local.second, local.microsecond) != (21, 15, 0, 0):
        raise ValueError("not the approved D generation slot")
    d, t, t1 = dates_for(local.strftime("%Y%m%d"), open_dates)
    cutoff = datetime.strptime(t + "0920", "%Y%m%d%H%M").replace(tzinfo=BJ)
    if now >= cutoff:
        raise ValueError("T 09:20 cutoff reached")
    deadline = local.replace(hour=21, minute=35)
    return {"signal_date": d, "exec_date": t, "exit_date": t1,
            "nominal_slot_utc": slot.astimezone(timezone.utc).isoformat(),
            "deadline_bj": deadline.isoformat(), "late": now > deadline}


def monitor_target(now_utc: str, open_dates: list[str]) -> dict | None:
    """Exact local slots with a five-minute grace; closed dates do no remote work."""
    local = aware(now_utc).astimezone(BJ)
    today = local.strftime("%Y%m%d")
    if today not in open_dates or local.hour not in {0, 18, 20, 21, 22, 23}:
        return None
    if not 35 <= local.minute < 40:
        return None
    d = today
    if local.hour == 0:
        d = (local - timedelta(days=1)).strftime("%Y%m%d")
        if d not in open_dates:
            return None
    signal, execution, exit_date = dates_for(d, open_dates)
    return {"signal_date": signal, "exec_date": execution, "exit_date": exit_date,
            "slot_bj": local.replace(minute=35, second=0, microsecond=0).isoformat()}
