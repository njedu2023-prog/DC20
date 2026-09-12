"""Immutable, exact-stock/session minute truth for the 10:00 exit proxy.

No settlement imports and no fallback to daily or legacy minute snapshots.
Tushare's optional 09:30 auction point is retained in source bytes, but is not
one of the 240 continuous-session BAR_END observations passed to the engine.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = "dc20_exit_1000_minutes_v1"
SOURCE_SCHEMA = "dc20_exit_1000_minute_source_v1"
SOURCE_ADAPTER = "tushare_stk_mins_continuous_bar_end_v1"
ROOT_PATH = "data/market/exit_1000_1m"
FIELDS = ("ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount")


def exit_minute_path_is_valid(relative: str) -> bool:
    """Exact publishing allowlist, not a generic raw/minute directory glob."""
    match = re.fullmatch(r"data/market/exit_1000_1m/(20\d{2})/(20\d{6})/(\d{6})_(SH|SZ)(\.csv|\.meta\.json)", relative)
    if match is None:
        return False
    try:
        _identity(match[2], f"{match[3]}.{match[4]}")
    except ValueError:
        return False
    return match[1] == match[2][:4] and match[2] >= "20260914"


def _identity(trade_date: str, code: str) -> tuple[str, str]:
    if not isinstance(trade_date, str) or re.fullmatch(r"20\d{6}", trade_date) is None:
        raise ValueError("minute trade date must be YYYYMMDD")
    if datetime.strptime(trade_date, "%Y%m%d").strftime("%Y%m%d") != trade_date:
        raise ValueError("minute trade date is invalid")
    if not isinstance(code, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", code) is None:
        raise ValueError("minute stock identity is invalid")
    return trade_date, code


def minute_paths(root: Path, trade_date: str, code: str) -> tuple[Path, Path]:
    _identity(trade_date, code)
    path = Path(root) / ROOT_PATH / trade_date[:4] / trade_date / f"{code.replace('.', '_')}.csv"
    return path, path.with_suffix(".meta.json")


def _safe_path(root: Path, path: Path) -> None:
    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents) if p != root):
        raise ValueError("unsafe exit minute source path")
    if path.exists() and not path.is_file():
        raise ValueError("exit minute source must be a regular file")


def expected_bar_ends(trade_date: str) -> list[str]:
    _identity(trade_date, "000001.SZ")
    day = datetime.strptime(trade_date, "%Y%m%d")
    return [(day.replace(hour=hour, minute=minute) + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
            for hour, minute in ((9, 31), (13, 1)) for i in range(120)]


def request_parameters(trade_date: str, code: str) -> dict[str, str]:
    _identity(trade_date, code)
    day = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    return {"ts_code": code, "freq": "1min", "start_date": f"{day} 09:30:00", "end_date": f"{day} 15:00:00"}


def normalize_source_rows(rows: list[dict], trade_date: str, code: str) -> tuple[list[dict], bool]:
    """Validate, never fill/delete duplicates/shift timestamps or infer prices."""
    _identity(trade_date, code)
    expected = expected_bar_ends(trade_date)
    auction_time = expected[0].replace("09:31:00", "09:30:00")
    by_time = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError("minute source required fields missing")
        stamp = row["trade_time"]
        if row["ts_code"] != code or not isinstance(stamp, str) or stamp in by_time:
            raise ValueError("minute source wrong code or duplicate timestamp")
        if stamp not in expected and stamp != auction_time:
            raise ValueError("minute source timestamp is outside exact BAR_END session")
        values = {}
        for key in ("open", "high", "low", "close", "vol", "amount"):
            value = row[key]
            if isinstance(value, bool):
                raise ValueError("minute source boolean numeric value")
            try:
                number = float(value)
            except (ValueError, TypeError):
                raise ValueError("minute source nonnumeric value") from None
            if not math.isfinite(number) or number < 0 or (key in {"open", "high", "low", "close"} and number == 0):
                raise ValueError("minute source invalid price/volume")
            values[key] = number
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError("minute source inconsistent OHLC")
        if values["vol"] == 0 and (len({values[k] for k in ("open", "high", "low", "close")}) != 1 or values["amount"] != 0):
            raise ValueError("minute source zero-volume price/amount conflict")
        if stamp == auction_time and len({values[k] for k in ("open", "high", "low", "close")}) != 1:
            raise ValueError("09:30 source is not an unambiguous auction point")
        by_time[stamp] = {"bar_end": stamp, **values}
    expected_set = set(expected)
    if set(by_time) not in (expected_set, expected_set | {auction_time}):
        raise ValueError("minute source incomplete 240-bar session")
    previous = None
    for stamp in expected:
        row = by_time[stamp]
        if row["vol"] == 0 and previous is not None and row["close"] != previous:
            raise ValueError("minute source zero-volume price changed")
        previous = row["close"]
    return [by_time[stamp] for stamp in expected], auction_time in by_time


def source_bytes(rows: list[dict], trade_date: str, code: str, *, fetched_at_utc: str) -> tuple[bytes, bytes]:
    normalized, has_auction = normalize_source_rows(rows, trade_date, code)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    # Keep every validated raw row, including the optional 09:30 point.
    writer.writerows(sorted(rows, key=lambda row: row["trade_time"]))
    raw = out.getvalue().encode("utf-8")
    meta = {"schema_version": SOURCE_SCHEMA, "source": "tushare:stk_mins", "adapter": SOURCE_ADAPTER,
            "ts_code": code, "trade_date": trade_date, "request": request_parameters(trade_date, code),
            "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END", "interval_seconds": 60,
            "complete_session": True, "continuous_rows": len(normalized), "source_rows": len(rows),
            "auction_point_0930_excluded": has_auction, "sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at_utc": fetched_at_utc, "immutable": True, "credential_persisted": False}
    return raw, (json.dumps(meta, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def load_exit_minutes(repo_root: Path, trade_date: str, code: str) -> dict | None:
    """None means absent; any present unsafe/corrupt/incomplete source raises."""
    root = Path(repo_root).resolve(strict=True)
    path, meta_path = minute_paths(root, trade_date, code)
    for candidate in (path, meta_path):
        _safe_path(root, candidate)
    if not path.exists() and not meta_path.exists():
        return None
    if not path.is_file() or not meta_path.is_file():
        raise ValueError("exit minute source CSV/metadata pair is incomplete")
    raw, meta_raw = path.read_bytes(), meta_path.read_bytes()
    try:
        meta = json.loads(meta_raw)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        if reader.fieldnames != list(FIELDS):
            raise ValueError("exit minute source columns drifted")
        rows, has_auction = normalize_source_rows(list(reader), trade_date, code)
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise ValueError("malformed exit minute source") from exc
    expected = {"schema_version": SOURCE_SCHEMA, "source": "tushare:stk_mins", "adapter": SOURCE_ADAPTER,
                "ts_code": code, "trade_date": trade_date, "request": request_parameters(trade_date, code),
                "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END", "interval_seconds": 60,
                "complete_session": True, "continuous_rows": 240, "source_rows": 240 + int(has_auction),
                "auction_point_0930_excluded": has_auction, "sha256": hashlib.sha256(raw).hexdigest(),
                "immutable": True, "credential_persisted": False}
    if not isinstance(meta, dict) or set(meta) != set(expected) | {"fetched_at_utc"}:
        raise ValueError("exit minute source metadata fields drifted")
    if any(type(meta[key]) is not type(value) or meta[key] != value for key, value in expected.items()):
        raise ValueError("exit minute source metadata/SHA mismatch")
    try:
        fetched = datetime.fromisoformat(meta["fetched_at_utc"].replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            raise ValueError("timezone missing")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("exit minute source fetched timestamp invalid") from exc
    return {"schema_version": SCHEMA, "ts_code": code, "trade_date": trade_date,
            "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END", "interval_seconds": 60,
            "complete_session": True, "rows": rows,
            "source_files": [{"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(b).hexdigest()}
                             for p, b in ((path, raw), (meta_path, meta_raw))]}
