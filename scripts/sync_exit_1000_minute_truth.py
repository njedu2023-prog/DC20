#!/usr/bin/env python3
"""Bounded exact-stock/day minute collection for verified open proxy positions.

Missing permissions, partial API data and absent bars remain explicit pending.
This does not create selections, mutate entries, settle trades or replace truth.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from top10decision.data.tushare_minute import TushareClient
from top10decision.decision.shadow_exit_minute_truth import (
    FIELDS, _safe_path, exit_minute_path_is_valid, load_exit_minutes, minute_paths, request_parameters, source_bytes,
)

EFFECTIVE_EXIT_DATE = "20260914"
MAX_REQUESTS = 48
REPORT_SCHEMA = "dc20_exit_1000_minute_sync_v1"


def validated_written_paths(root: Path, report: dict, as_of_date: str) -> list[str]:
    """Revalidate the exact fresh CSV/meta pairs before Verify can stage them."""
    root = root.resolve(strict=True)
    if (report.get("schema_version") != REPORT_SCHEMA or report.get("as_of_date") != as_of_date
            or report.get("effective_scheduled_exit_date") != EFFECTIVE_EXIT_DATE
            or any(report.get(key) is not False for key in
                   ("selection_created", "existing_truth_overwritten", "credential_persisted"))):
        raise ValueError("exit minute sync report contract drifted")
    planned = set()
    for row in report.get("partitions", []):
        key = (row.get("trade_date"), row.get("ts_code"))
        if row.get("endpoint") != "stk_mins" or key in planned:
            raise ValueError("exit minute sync report duplicate/wrong endpoint")
        minute_paths(root, *key)
        if not EFFECTIVE_EXIT_DATE <= key[0] <= as_of_date:
            raise ValueError("exit minute sync report date drifted")
        planned.add(key)
    expected = {}
    for row in report.get("partitions", []):
        if row.get("status") == "EXACT_TRUTH_WRITTEN":
            payload = load_exit_minutes(root, row["trade_date"], row["ts_code"])
            if payload is None:
                raise ValueError("reported exit minute source missing")
            expected.update({item["path"]: item["sha256"] for item in payload["source_files"]})
    observed = {}
    for item in report.get("written_paths", []):
        relative = item.get("path", "")
        if not exit_minute_path_is_valid(relative) or relative in observed:
            raise ValueError("exit minute staged path is not exact")
        observed[relative] = item.get("sha256")
    if observed != expected:
        raise ValueError("exit minute staged source pair/SHA drifted")
    return sorted(expected)


def required_partitions(root: Path, as_of_date: str) -> set[tuple[str, str]]:
    from scripts.settle_primary_observations import plan_exit_minute_requests
    from top10decision.decision.executable_profit_shadow_settlement import (
        _resolve_public_exit_1000, _strict_as_of_date, _strict_open_dates,
        _validate_adjacent_dates, build_t_verification, load_selection,
    )

    dates = _strict_open_dates(root)
    _strict_as_of_date(dates, as_of_date, signal_date="20260828")
    if as_of_date < EFFECTIVE_EXIT_DATE:
        return set()
    required = {(row["trade_date"], row["ts_code"])
                for row in plan_exit_minute_requests(root, as_of_date)}
    for path in sorted((root / "data/decision_executable_profit/forward/selections").glob("shadow_*.json")):
        match = re.fullmatch(r"shadow_(20\d{6})\.json", path.name)
        if match is None:
            raise ValueError("invalid frozen Shadow selection filename")
        d = match[1]
        if not "20260828" <= d <= as_of_date:
            continue
        _, selection, _ = load_selection(root, d)
        t, exit_date = selection["exec_date"], selection["exit_date"]
        _validate_adjacent_dates(dates, d, t, exit_date)
        if not EFFECTIVE_EXIT_DATE <= exit_date <= as_of_date:
            continue
        # Reuses canonical immutable T truth when present and verifies every
        # bound source SHA; otherwise computes the same BUY policy read-only.
        verification, _ = build_t_verification(root, d, as_of_date=as_of_date)
        if verification is None:
            continue
        for row in verification["rows"]:
            if row["proxy_fill"] != 1:
                continue
            code = row["ts_code"]
            exit_truth, _, _ = _resolve_public_exit_1000(
                repo_root=root, open_dates=dates, scheduled_exit_date=exit_date,
                as_of_date=as_of_date, code=code, entry_price=row["entry_open_price"],
                t_close_price=row["t_close_price"], table_cache={})
            if exit_truth is None:
                required.update((day, code) for day in dates if exit_date <= day <= as_of_date)
    for date, code in required:
        if date not in dates or not EFFECTIVE_EXIT_DATE <= date <= as_of_date:
            raise ValueError("minute request plan contains future/pre-policy/non-session truth")
        minute_paths(root, date, code)  # strict stock/date identity
    return required


def sync_missing_minutes(root: Path, as_of_date: str, *, client=None, max_requests: int = MAX_REQUESTS) -> dict:
    root = root.resolve(strict=True)
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError("minute request budget is invalid")
    plan = required_partitions(root, as_of_date)
    result = {"schema_version": REPORT_SCHEMA, "as_of_date": as_of_date,
              "effective_scheduled_exit_date": EFFECTIVE_EXIT_DATE,
              "selection_created": False, "existing_truth_overwritten": False,
              "credential_persisted": False, "network_requests": 0,
              "written_paths": [], "partitions": []}
    for date, code in sorted(plan):
        entry = {"trade_date": date, "ts_code": code, "endpoint": "stk_mins"}
        result["partitions"].append(entry)
        # Existing corrupt truth is never overwritten; downstream remains
        # pending and the diagnostic makes the necessary repair explicit.
        try:
            existing = load_exit_minutes(root, date, code)
        except ValueError as exc:
            entry.update(status="PENDING_EXISTING_INVALID_NOT_OVERWRITTEN", reason=str(exc))
            continue
        if existing is not None:
            entry["status"] = "EXISTING_VALID_NOT_OVERWRITTEN"
            continue
        if result["network_requests"] >= max_requests:
            entry.update(status="PENDING_REQUEST_LIMIT", reason="bounded stock/day request budget reached")
            continue
        if client is None:
            if not os.environ.get("TUSHARE_TOKEN", "").strip():
                entry.update(status="PENDING_CREDENTIAL", reason="TUSHARE_TOKEN unavailable")
                continue
            client = TushareClient.from_env(timeout_seconds=20)
        result["network_requests"] += 1
        try:
            # Do not use historical_minute: it silently drops duplicate bars
            # and missing OHLC, which are invalid settlement evidence here.
            frame = client.call("stk_mins", request_parameters(date, code), FIELDS)
        except Exception as exc:
            entry.update(status="PENDING_SOURCE_UNAVAILABLE", reason=type(exc).__name__)
            continue
        try:
            if len(frame.columns) != len(FIELDS) or set(frame.columns) != set(FIELDS):
                raise ValueError("minute source duplicate/missing/unexpected columns")
            raw, metadata = source_bytes(frame.to_dict("records"), date, code,
                fetched_at_utc=datetime.now(timezone.utc).isoformat())
        except ValueError as exc:
            entry.update(status="PENDING_SOURCE_INVALID", reason=str(exc))
            continue
        paths = minute_paths(root, date, code)
        for path in paths:
            _safe_path(root, path)
            if path.exists() or path.is_symlink():
                raise ValueError("refusing to overwrite immutable exit minutes")
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        for path, content in zip(paths, (raw, metadata)):
            with path.open("xb") as handle:
                handle.write(content)
        validated = load_exit_minutes(root, date, code)
        assert validated is not None
        result["written_paths"].extend(validated["source_files"])
        entry["status"] = "EXACT_TRUTH_WRITTEN"
    result["status"] = "PENDING_TRUTH" if any(p["status"].startswith("PENDING") for p in result["partitions"]) else "COMPLETE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = sync_missing_minutes(args.root, args.as_of_date)
    text = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
