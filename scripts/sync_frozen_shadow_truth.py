#!/usr/bin/env python3
"""Fill only missing exact-session truth needed by existing frozen Shadow slots.

This never creates a D selection or replaces a present market partition. API
unavailability remains explicit pending evidence. No minute/daily proxy is
substituted for the required opening-auction endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from top10decision.data.tushare_minute import TushareClient, write_auction_open_snapshot
from top10decision.decision.executable_profit_shadow_settlement import (
    _find_market_file,
    _market_rows,
    _strict_as_of_date,
    _strict_open_dates,
    _validate_adjacent_dates,
    load_selection,
    validate_t1_settlement,
)

START_D = "20260828"
MAX_REQUESTS = 24
METHODS = {"daily": "daily_close", "stk_limit": "daily_limits", "stk_auction_o": "opening_auction"}
NUMERIC = {"daily": ("open", "high", "low", "close"), "stk_limit": ("up_limit", "down_limit"), "stk_auction_o": ("close", "amount")}


def required_partitions(root: Path, as_of_date: str) -> dict[tuple[str, str], set[str]]:
    dates = _strict_open_dates(root)
    _strict_as_of_date(dates, as_of_date, signal_date=START_D)
    required: dict[tuple[str, str], set[str]] = {}
    for path in sorted((root / "data/decision_executable_profit/forward/selections").glob("shadow_*.json")):
        match = re.fullmatch(r"shadow_(20\d{6})\.json", path.name)
        if match is None:
            raise ValueError("invalid frozen Shadow selection filename")
        d = match.group(1)
        if d < START_D:
            continue
        _, selection, selected = load_selection(root, d)
        t, exit_date = selection["exec_date"], selection["exit_date"]
        _validate_adjacent_dates(dates, d, t, exit_date)
        codes = {row["ts_code"] for row in selected}
        if d > as_of_date or not codes:
            continue
        settled = root / f"data/decision_executable_profit/forward/settlements/settlement_{d}.json"
        if settled.exists() or settled.is_symlink():
            # Existing settlement inputs are immutable and need no re-fetch.
            if settled.is_symlink() or not settled.is_file():
                raise ValueError("existing Shadow settlement is unsafe")
            payload = json.loads(settled.read_text(encoding="utf-8"))
            validate_t1_settlement(payload)
            if payload.get("signal_date") != d:
                raise ValueError("existing Shadow settlement date drifted")
            continue
        if t <= as_of_date:
            for name in METHODS:
                required.setdefault((t, name), set()).update(codes)
        for exit_session in dates:
            if exit_date <= exit_session <= as_of_date:
                for name in ("daily", "stk_limit"):
                    required.setdefault((exit_session, name), set()).update(codes)
    return required


def _validate_frame(frame: pd.DataFrame, date: str, name: str, codes: set[str]) -> None:
    if frame.empty or not {"ts_code", "trade_date", *NUMERIC[name]}.issubset(frame.columns):
        raise ValueError("SOURCE_MISSING_REQUIRED_COLUMNS_OR_ROWS")
    dates = frame["trade_date"].astype(str)
    if not dates.eq(date).all():
        raise ValueError("SOURCE_EXACT_DATE_MISMATCH")
    values = frame["ts_code"].astype(str)
    if values.duplicated().any() or not values.str.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)").all():
        raise ValueError("SOURCE_CODE_IDENTITY_INVALID")
    selected = frame[values.isin(codes)]
    if set(selected["ts_code"]) != codes:
        raise ValueError("SOURCE_FROZEN_CODES_MISSING")
    for column in NUMERIC[name]:
        numeric = pd.to_numeric(selected[column], errors="coerce")
        if numeric.isna().any() or not numeric.map(math.isfinite).all() or not numeric.gt(0).all():
            raise ValueError(f"SOURCE_INVALID_{column.upper()}")


def _write_missing(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("refusing to replace existing immutable truth")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects a caller from accidentally replacing bytes.
    with path.open("xb") as handle:
        handle.write(payload)


def sync_missing_truth(root: Path, as_of_date: str, *, client=None, max_requests: int = MAX_REQUESTS) -> dict:
    root = root.resolve(strict=True)
    if not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError("truth sync request bound is invalid")
    plan = required_partitions(root, as_of_date)
    result = {"schema_version": "dc20_frozen_shadow_truth_sync_v1", "as_of_date": as_of_date,
              "selection_created": False, "existing_truth_overwritten": False,
              "credential_persisted": False, "network_requests": 0,
              "written_paths": [], "partitions": []}
    for (date, name), codes in sorted(plan.items()):
        entry = {"trade_date": date, "endpoint": name, "frozen_codes": sorted(codes)}
        result["partitions"].append(entry)
        existing = _find_market_file(root, date, name)
        if existing is not None:
            try:
                frame = pd.DataFrame(_market_rows(existing, date).values())
                _validate_frame(frame, date, name, codes)
                entry["status"] = "EXISTING_VALID_NOT_OVERWRITTEN"
            except (RuntimeError, ValueError) as exc:
                entry.update(status="PENDING_EXISTING_INCOMPLETE_NOT_OVERWRITTEN", reason=str(exc))
            continue
        target = root / f"data/market/raw/{date[:4]}/{date}/{name}.csv"
        meta_target = target.with_suffix(".meta.json")
        if root not in target.resolve().parents or any(
            path.is_symlink() for path in (target, *target.parents) if path != root
        ):
            raise ValueError("missing truth target has an unsafe parent")
        if meta_target.exists() or meta_target.is_symlink():
            raise ValueError(f"orphan truth metadata must be reviewed: {meta_target}")
        if result["network_requests"] >= max_requests:
            entry.update(status="PENDING_REQUEST_LIMIT", reason="bounded request budget reached")
            continue
        if client is None:
            if not os.environ.get("TUSHARE_TOKEN", "").strip():
                entry.update(status="PENDING_CREDENTIAL", reason="TUSHARE_TOKEN unavailable")
                continue
            client = TushareClient.from_env(timeout_seconds=20)
        result["network_requests"] += 1
        try:
            frame = getattr(client, METHODS[name])(date)
        except Exception as exc:
            # Do not persist upstream exception text, which may contain a URL
            # or credential; retain only the exception class.
            entry.update(status="PENDING_SOURCE_UNAVAILABLE", reason=type(exc).__name__)
            continue
        try:
            _validate_frame(frame, date, name, codes)
        except ValueError as exc:
            entry.update(status="PENDING_SOURCE_INVALID", reason=str(exc))
            continue
        with tempfile.TemporaryDirectory(prefix="dc20-exact-shadow-truth-") as temp:
            temp_root = Path(temp)
            if name == "stk_auction_o":
                written, metadata = write_auction_open_snapshot(frame, temp_root, date)
                csv_bytes, meta_bytes = written.read_bytes(), metadata.read_bytes()
            else:
                csv_bytes = frame.sort_values("ts_code").to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
                metadata = {"schema_version": "dc20_frozen_shadow_truth_source_v1",
                            "source": f"tushare:{name}", "trade_date": date,
                            "requested_trade_date": date, "rows": len(frame),
                            "sha256": hashlib.sha256(csv_bytes).hexdigest(),
                            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                            "immutable": True, "credential_persisted": False}
                meta_bytes = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
            # Existing repository partitions are never passed to writer helpers.
            _write_missing(target, csv_bytes)
            _write_missing(meta_target, meta_bytes)
        entry["status"] = "EXACT_TRUTH_WRITTEN"
        for path in (target, meta_target):
            result["written_paths"].append({"path": path.relative_to(root).as_posix(),
                                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    result["status"] = "PENDING_TRUTH" if any(row["status"].startswith("PENDING") for row in result["partitions"]) else "COMPLETE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = sync_missing_truth(Path(args.root), args.as_of_date)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
