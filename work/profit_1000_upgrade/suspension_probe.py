#!/usr/bin/env python3
"""Ten fixed, read-only calls diagnosing historical missing daily observations.

Official contracts reviewed 2026-09-12:
https://tushare.pro/document/2?doc_id=27 (daily: no rows during suspension)
https://tushare.pro/document/2?doc_id=214 (suspend_d: S/R and suspend_timing)

No OHLC is synthesized, no archived source is amended, and no label is settled.
Original response data tables are preserved without normalization; arbitrary
server envelope messages are never persisted. The complete HTTP body is bound
by SHA256 only, because an error/message can echo a credential.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib import error, request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from probe import classify

SCHEMA = "dc20_fixed_suspension_diagnostic_v1"
MAX_CALLS = 10
TIMEOUT_SECONDS = 20
MAX_SECONDS = 300
REQUEST_INTERVAL_SECONDS = 1.0
MAX_RESPONSE_BYTES = 1_000_000
CALENDAR_SHA = "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
ARCHIVE_SHA = "e56fcaf1ee54bd8ff562d1eae832a2d46397de256588c4aa097556533e0410c7"
CASES = (
    ("20240425", "600234.SH", ("20240429",), "20240430"),
    ("20250606", "603226.SH", ("20250610", "20250611", "20250612"), "20250613"),
    ("20251113", "603122.SH", ("20251117", "20251118", "20251119"), "20251120"),
    ("20260703", "603580.SH", ("20260707", "20260708", "20260709", "20260710", "20260713"), "20260714"),
    ("20260721", "002036.SZ", ("20260723", "20260724", "20260727", "20260728", "20260729"), "20260730"),
)
DAILY_FIELDS = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg")
SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _date(value):
    if not isinstance(value, str) or re.fullmatch(r"20\d{6}", value) is None:
        raise ValueError("invalid date")
    datetime.strptime(value, "%Y%m%d")
    return value


def contract():
    calendar = ROOT / "data/market/trade_cal_sse.csv"
    if any(p.is_symlink() for p in (calendar, *calendar.parents)):
        raise ValueError("aliased calendar")
    if _sha(calendar.read_bytes()) != CALENDAR_SHA:
        raise ValueError("calendar binding changed")
    with calendar.open(encoding="utf-8-sig", newline="") as handle:
        dates = [r["cal_date"] for r in csv.DictReader(handle) if r["exchange"] == "SSE" and r["is_open"] == "1"]
    cases = []
    for day, code, missing, resume in CASES:
        index = dates.index(day)
        if dates[index + 2] != missing[0] or tuple(d for d in dates if missing[0] <= d < resume) != missing:
            raise ValueError("fixed D/T/gap calendar binding changed")
        if resume not in dates or resume > "20260911":
            raise ValueError("fixed diagnostic date beyond snapshot")
        cases.append({"signal_date": day, "ts_code": code, "first_missing_date": missing[0],
                      "missing_exchange_sessions": list(missing), "observed_resumption_date": resume,
                      "original_archive_member": "candidate_sources/" + missing[0] + "/daily.csv"})
    return {"schema_version": SCHEMA, "max_api_calls": MAX_CALLS, "timeout_seconds": TIMEOUT_SECONDS,
            "max_seconds": MAX_SECONDS, "request_interval_seconds": REQUEST_INTERVAL_SECONDS,
            "calendar_sha256": CALENDAR_SHA, "original_archive_sha256": ARCHIVE_SHA,
            "cases": cases, "production_writes": False, "synthesize_ohlc": False,
            "automatic_settlement": False, "purchase_permission": False,
            "documentation": ["https://tushare.pro/document/2?doc_id=27", "https://tushare.pro/document/2?doc_id=214"]}


def _fresh_output(output, runner_temp):
    if not runner_temp:
        raise ValueError("RUNNER_TEMP is required")
    base, out = Path(runner_temp), Path(output)
    for value in (base, out):
        if ".." in value.parts or any(p.is_symlink() for p in (value, *value.parents)):
            raise ValueError("aliased output is forbidden")
    base = base.resolve(strict=True)
    out = out.resolve()
    if not base.is_dir() or base not in out.parents:
        raise ValueError("output must be a new child of RUNNER_TEMP")
    if out == ROOT or ROOT in out.parents or out in ROOT.parents:
        raise ValueError("output must be outside checkout")
    out.mkdir(parents=True, exist_ok=False)
    return out


def _write(out, relative, raw, token):
    if token and token.encode() in raw:
        raise ValueError("credential-like artifact forbidden")
    path = out / relative
    if out not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("aliased artifact forbidden")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
    return {"path": relative, "bytes": len(raw), "sha256": _sha(raw)}


def official_call(endpoint, params, fields, token, timeout):
    body = json.dumps({"api_name": endpoint, "token": token, "params": params, "fields": ",".join(fields)}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return raw


def _rows(payload, endpoint, case):
    fields = DAILY_FIELDS if endpoint == "daily" else SUSPEND_FIELDS
    data = payload.get("data")
    if not isinstance(data, dict) or not {"fields", "items"} <= set(data) or set(data) - {"fields", "items", "has_more", "count"}:
        raise ValueError("invalid table")
    if "has_more" in data and (type(data["has_more"]) is not bool or data["has_more"]):
        raise ValueError("truncated response")
    if "count" in data and (type(data["count"]) is not int or data["count"] < 0):
        raise ValueError("invalid table count")
    columns, items = data["fields"], data["items"]
    if not isinstance(columns, list) or len(columns) != len(fields) or set(columns) != set(fields):
        raise ValueError("invalid columns")
    if not isinstance(items, list) or len(items) > (1 if endpoint == "daily" else 64):
        raise ValueError("unexpected row count")
    rows, identities = [], set()
    for item in items:
        if not isinstance(item, list) or len(item) != len(columns):
            raise ValueError("invalid row")
        row = dict(zip(columns, item))
        date = _date(row["trade_date"])
        if row["ts_code"] != case["ts_code"]:
            raise ValueError("unexpected code")
        if endpoint == "daily":
            if date != case["first_missing_date"]:
                raise ValueError("unexpected daily date")
            numbers = {}
            for key in fields[2:]:
                value = row[key]
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise ValueError("invalid numeric truth")
                number = float(value)
                if not math.isfinite(number) or (key != "pct_chg" and number < 0) or (key in {"open", "high", "low", "close", "pre_close"} and number <= 0):
                    raise ValueError("invalid numeric truth")
                numbers[key] = number
            if not numbers["low"] <= min(numbers["open"], numbers["close"]) <= max(numbers["open"], numbers["close"]) <= numbers["high"]:
                raise ValueError("invalid OHLC")
        else:
            timing = row["suspend_timing"]
            if not case["first_missing_date"] <= date <= case["observed_resumption_date"] or row["suspend_type"] not in {"S", "R"}:
                raise ValueError("unexpected suspension date or type")
            if timing is not None and (not isinstance(timing, str) or len(timing) > 64 or re.fullmatch(r"[0-9: ,;\-]*", timing) is None):
                raise ValueError("unrecognized suspension timing")
            identity = date, row["suspend_type"], timing
            if identity in identities:
                raise ValueError("duplicate suspension row")
            identities.add(identity)
        rows.append(row)
    return data, rows


def _diagnosis(case, receipts, observations):
    daily, suspension = receipts
    events = observations[1]
    recorded, intraday, resumed = set(), set(), set()
    for row in events:
        if row["suspend_type"] == "R":
            resumed.add(row["trade_date"])
        elif row["suspend_timing"] is None or not row["suspend_timing"].strip():
            recorded.add(row["trade_date"])
        else:
            intraday.add(row["trade_date"])
    wanted = set(case["missing_exchange_sessions"])
    missing = wanted - recorded
    contradictory = wanted & (intraday | resumed)
    covered = bool(events) and not missing and not contradictory
    first = case["first_missing_date"]
    if daily["status"] == "DAILY_PRESENT" and first in recorded:
        status = "CONFLICTING_DAILY_AND_SUSPENSION_EVIDENCE"
    elif daily["status"] == "DAILY_PRESENT":
        status = "DAILY_ROW_NOW_AVAILABLE_REVIEW_ARCHIVE_DIFFERENCE"
    elif daily["status"] == "DAILY_ABSENT" and covered:
        status = "SUSPENSION_RECORDED_FOR_GAP_REQUIRES_EVENT_REVIEW"
    elif contradictory:
        status = "INTRADAY_OR_RESUMPTION_NOT_FULL_DAY_PROOF"
    else:
        status = "UNRESOLVED_MISSING_DAILY_OR_SUSPENSION_EVIDENCE"
    return {**case, "status": status, "daily_request_status": daily["status"],
            "suspension_request_status": suspension["status"],
            "timing_unspecified_S_dates": sorted(recorded), "intraday_S_dates": sorted(intraday),
            "R_dates": sorted(resumed), "gap_dates_without_unspecified_S": sorted(missing),
            "gap_event_coverage_complete": covered, "full_day_suspension_automatically_confirmed": False,
            "synthetic_ohlc_created": False, "settlement_allowed": False}


def probe(output, *, token, runner_temp=None, call=official_call, clock=time.monotonic, sleep=time.sleep):
    plan = contract()
    out = _fresh_output(output, runner_temp or os.environ.get("RUNNER_TEMP"))
    files = [_write(out, "request_contract.json", _json(plan), token)]
    report = {"schema_version": SCHEMA, "run_commit": os.environ.get("GITHUB_SHA"),
              "run_id": os.environ.get("GITHUB_RUN_ID"), "contract_sha256": _sha(_json(plan)),
              "source_sha256": _sha(Path(__file__).read_bytes()), "api_calls": 0,
              "max_api_calls": MAX_CALLS, "max_seconds": MAX_SECONDS,
              "cases": [], "requests": [], "source_files": files,
              "production_writes": False, "credential_persisted": False,
              "server_messages_persisted": False, "synthetic_ohlc_created": False,
              "training_performed": False, "settlement_performed": False,
              "release_allowed": False, "purchase_permission": False}
    started, next_request = clock(), clock()
    denied_endpoints = set()
    credential_failed = False
    for case in plan["cases"]:
        case_receipts, observations = [], []
        for endpoint, params, fields in (
            ("daily", {"ts_code": case["ts_code"], "trade_date": case["first_missing_date"]}, DAILY_FIELDS),
            ("suspend_d", {"ts_code": case["ts_code"], "start_date": case["first_missing_date"], "end_date": case["observed_resumption_date"]}, SUSPEND_FIELDS),
        ):
            receipt = {"query_number": len(report["requests"]) + 1, "signal_date": case["signal_date"],
                       "endpoint": endpoint, "params": params, "fields": list(fields),
                       "status": "PENDING_UNKNOWN", "network_request_performed": False,
                       "source_files": []}
            rows = []
            if not token.strip():
                receipt["status"] = "CREDENTIAL_ABSENT"
            elif credential_failed:
                receipt["status"] = "SKIPPED_AFTER_CREDENTIAL_FAILURE"
            elif endpoint in denied_endpoints:
                receipt["status"] = "SKIPPED_AFTER_ENDPOINT_ENTITLEMENT_FAILURE"
            elif report["api_calls"] >= MAX_CALLS or max(clock(), next_request) - started + TIMEOUT_SECONDS > MAX_SECONDS:
                receipt["status"] = "SKIPPED_BUDGET_EXHAUSTED"
            else:
                if next_request > clock():
                    sleep(next_request - clock())
                if clock() - started + TIMEOUT_SECONDS > MAX_SECONDS:
                    receipt["status"] = "SKIPPED_BUDGET_EXHAUSTED"
                else:
                    report["api_calls"] += 1
                    receipt["network_request_performed"] = True
                    next_request = clock() + REQUEST_INTERVAL_SECONDS
                    receipt["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
                    try:
                        raw = call(endpoint, params, fields, token, TIMEOUT_SECONDS)
                        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError("invalid response bytes")
                        receipt.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw))
                        payload = json.loads(raw)
                        status = classify(payload)
                        receipt["status"] = status
                        if status == "ENTITLEMENT_DENIED":
                            denied_endpoints.add(endpoint)
                        elif status == "CREDENTIAL_REJECTED":
                            credential_failed = True
                        elif status == "SUCCESS":
                            data, rows = _rows(payload, endpoint, case)
                            body = _json(data)
                            stem = "responses/" + str(receipt["query_number"]).zfill(2) + "_" + endpoint
                            binding = _write(out, stem + ".data.json", body, token)
                            metadata = {"schema_version": "dc20_diagnostic_data_table_v1", "source": "tushare:" + endpoint,
                                        "params": params, "fields": list(fields), "data_sha256": binding["sha256"],
                                        "http_response_sha256": receipt["http_response_sha256"],
                                        "fetched_at_utc": receipt["fetched_at_utc"], "row_count": len(rows),
                                        "encoding": "ORIGINAL_DATA_TABLE_CANONICAL_JSON_NO_ENVELOPE_MESSAGES",
                                        "data_values_modified": False, "immutable": True,
                                        "diagnostic_only": True, "credential_persisted": False}
                            pair = [binding, _write(out, stem + ".meta.json", _json(metadata), token)]
                            receipt["source_files"] = pair
                            files.extend(pair)
                            receipt["row_count"] = len(rows)
                            receipt["status"] = ("DAILY_PRESENT" if rows else "DAILY_ABSENT") if endpoint == "daily" else ("SUSPENSION_EVENTS_PRESENT" if rows else "SUSPENSION_EVIDENCE_ABSENT")
                    except error.HTTPError as exc:
                        receipt.update(status="HTTP_ERROR", http_status=exc.code)
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        receipt["status"] = "INVALID_RESPONSE_NOT_INTERPRETED"
                        rows = []
                    except Exception:
                        receipt["status"] = "NETWORK_OR_RESPONSE_ERROR"
                        rows = []
            case_receipts.append(receipt)
            observations.append(rows)
            report["requests"].append(receipt)
            files.append(_write(out, "receipts/" + str(receipt["query_number"]).zfill(2) + ".json", _json(receipt), token))
        report["cases"].append(_diagnosis(case, case_receipts, observations))
    report["elapsed_seconds"] = round(clock() - started, 3)
    report["status_counts"] = dict(Counter(r["status"] for r in report["cases"]))
    report["status"] = "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT" if all(r["status"] in {"DAILY_PRESENT", "DAILY_ABSENT", "SUSPENSION_EVENTS_PRESENT", "SUSPENSION_EVIDENCE_ABSENT"} for r in report["requests"]) else "DIAGNOSTIC_PARTIAL_NOT_SETTLEMENT"
    _write(out, "receipt.json", _json(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps({"status": result["status"], "api_calls": result["api_calls"],
                      "status_counts": result["status_counts"], "settlement_performed": False}, sort_keys=True))
    return 0 if result["status"] == "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
