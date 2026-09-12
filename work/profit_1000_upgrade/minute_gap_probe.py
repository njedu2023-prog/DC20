#!/usr/bin/env python3
"""Six exact, read-only 09:31 queries following the bound first diagnostic.

Keep the original safe data table and complete response-body SHA, never the
server envelope/message. No grid filling, timestamp shifting, source import,
label settlement, training, purchase, or production write is permitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib import error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import suspension_probe as safe
import minute_truth as research_adapter
from probe import classify

SCHEMA = "dc20_fixed_minute_gap_diagnostic_v2"
MAX_CALLS, TIMEOUT_SECONDS, MAX_SECONDS = 6, 20, 180
REQUEST_INTERVAL_SECONDS = 1.0
CALENDAR_SHA = "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
ADAPTER_SHA = "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65"
ARCHIVE_SHA = "d004f6decba35d6148082333764ba0988bc3fa25062224492d485ac031fe2a29"
PREVIOUS_PROBE_SHA = "5e2dc0eb7ef3ca6f198c96010eabe7bcb120f77717cedba75a18ed536ac9e775"
TIME_SEMANTICS = "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED"
CASES = (("20221124", "600302.SH"), ("20230109", "002401.SZ"),
         ("20240103", "605118.SH"), ("20250102", "002868.SZ"),
         ("20260106", "603667.SH"), ("20260106", "001299.SZ"))
ADAPTER_PATH = ROOT / "src/top10decision/decision/shadow_exit_minute_truth.py"
SPEC = importlib.util.spec_from_file_location("minute_gap_strict_adapter", ADAPTER_PATH)
minute = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(minute)

# Persist only our exact, locally defined messages; never arbitrary exceptions.
ADAPTER_ERRORS = {
    "minute source required fields missing": "FIELD_SCHEMA",
    "minute source wrong code or duplicate timestamp": "CODE_OR_DUPLICATE_TIMESTAMP",
    "minute source timestamp is outside exact BAR_END session": "TIMESTAMP_OUTSIDE_GRID",
    "minute source boolean numeric value": "BOOLEAN_NUMERIC",
    "minute source nonnumeric value": "NONNUMERIC_VALUE",
    "minute source invalid price/volume": "INVALID_PRICE_OR_VOLUME",
    "minute source inconsistent OHLC": "INCONSISTENT_OHLC",
    "minute source zero-volume price/amount conflict": "ZERO_VOLUME_PRICE_AMOUNT_CONFLICT",
    "09:30 source is not an unambiguous auction point": "AMBIGUOUS_0930_POINT",
    "minute source incomplete 240-bar session": "INCOMPLETE_240_BAR_SESSION",
    "minute source zero-volume price changed": "ZERO_VOLUME_PRICE_CHANGED",
}


def _expected_contract():
    return {"schema_version": SCHEMA, "request_id": "six_strict_0931_minute_queries_20260912_v2",
            "endpoint": "stk_mins", "max_api_calls": MAX_CALLS,
            "timeout_seconds": TIMEOUT_SECONDS, "max_seconds": MAX_SECONDS,
            "request_interval_seconds": REQUEST_INTERVAL_SECONDS,
            "calendar_sha256": CALENDAR_SHA, "minute_adapter_sha256": ADAPTER_SHA,
            "original_history_archive_sha256": ARCHIVE_SHA,
            "previous_probe_archive_sha256": PREVIOUS_PROBE_SHA,
            "previous_probe_run_id": "34674667545",
            "previous_probe_commit": "9c5da90fdd11d4bbbd429e704a425e8c0b0cae95",
            "query_window": {"start_time": "09:31:00", "end_time": "15:00:00", "exact_continuous_rows": 240},
            "time_semantics": TIME_SEMANTICS,
            "original_request_status": "PENDING_INVALID_RESPONSE_NOT_IMPUTED",
            "cases": [{"trade_date": day, "ts_code": code} for day, code in CASES],
            "production_writes": False, "purchase_permission": False,
            "synthesize_minutes": False, "automatic_settlement": False}


def contract():
    path = HERE / "MINUTE_GAP_REQUEST.json"
    calendar = ROOT / "data/market/trade_cal_sse.csv"
    for source in (path, calendar, ADAPTER_PATH):
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError("aliased diagnostic contract")
    plan = research_adapter._parse(path.read_bytes())
    if plan != _expected_contract():
        raise ValueError("fixed diagnostic request changed")
    if safe._sha(calendar.read_bytes()) != CALENDAR_SHA or safe._sha(ADAPTER_PATH.read_bytes()) != ADAPTER_SHA:
        raise ValueError("calendar or minute adapter binding changed")
    with calendar.open(encoding="utf-8-sig", newline="") as handle:
        dates = {r["cal_date"] for r in csv.DictReader(handle) if r["exchange"] == "SSE" and r["is_open"] == "1"}
    if any(day not in dates or day > "20260911" for day, _ in CASES):
        raise ValueError("fixed diagnostic date invalid")
    return plan


def request_parameters(trade_date, code):
    """Narrow only this diagnostic's HTTP query; never change the old adapter."""
    params = minute.request_parameters(trade_date, code)
    params["start_date"] = params["start_date"].replace(" 09:30:00", " 09:31:00")
    return params


def _safe_data(payload):
    """Preserve malformed table values for diagnosis, but no envelope/messages."""
    data = payload.get("data")
    if not isinstance(data, dict) or not {"fields", "items"} <= set(data) or set(data) - {"fields", "items", "has_more", "count"}:
        raise ValueError("unsafe data table")
    fields, items = data["fields"], data["items"]
    if (not isinstance(fields, list) or len(fields) > 64
            or any(not isinstance(v, str) or re.fullmatch(r"[A-Za-z0-9_]{1,64}", v) is None for v in fields)
            or not isinstance(items, list) or len(items) > 1000):
        raise ValueError("unsafe table shape")
    for row in items:
        if not isinstance(row, list) or len(row) > 64:
            raise ValueError("unsafe row shape")
        for value in row:
            if not (value is None or isinstance(value, (bool, int, float, str))):
                raise ValueError("unsafe nested data value")
            if isinstance(value, str) and len(value) > 128:
                raise ValueError("oversized table string")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("nonfinite table value")
    if "has_more" in data and type(data["has_more"]) is not bool:
        raise ValueError("unsafe pagination value")
    if "count" in data and (type(data["count"]) is not int or not 0 <= data["count"] <= 10000):
        raise ValueError("unsafe count value")
    return data


def diagnose(data, case):
    """Invent no truth: report observed structure plus the unchanged validator."""
    day, code = case["trade_date"], case["ts_code"]
    fields, items = data["fields"], data["items"]
    issues = set()
    result = {"row_count": len(items), "strict_adapter_accepts": False,
              "table_contract_valid": False, "query_window_valid": False,
              "source_contract_accepts": False, "time_semantics": TIME_SEMANTICS,
              "strict_adapter_error_category": None, "strict_adapter_error": None,
              "source_values_modified": False, "source_import_allowed": False,
              "settlement_allowed": False}
    if data.get("has_more") is True or (data.get("count", 0) > 0 and data["count"] != len(items)):
        issues.add("PAGINATION_OR_COUNT_MISMATCH")
    if len(fields) != len(minute.FIELDS) or set(fields) != set(minute.FIELDS):
        result.update(issues=sorted(issues | {"FIELD_SCHEMA"}), strict_adapter_error_category="FIELD_SCHEMA")
        return result
    if any(len(row) != len(fields) for row in items):
        result.update(issues=sorted(issues | {"ROW_SHAPE"}), strict_adapter_error_category="ROW_SHAPE")
        return result
    result["table_contract_valid"] = "PAGINATION_OR_COUNT_MISMATCH" not in issues
    rows = [dict(zip(fields, row)) for row in items]
    expected = set(minute.expected_bar_ends(day))
    iso_day = datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%d")
    auction_stamp = iso_day + " 09:30:00"
    timestamps = [r["trade_time"] for r in rows if isinstance(r["trade_time"], str)]
    counts = Counter(timestamps)
    safe_stamps = {s for s in timestamps if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", s)}
    duplicate = sorted(s for s in safe_stamps if counts[s] > 1)
    missing = sorted(expected - set(timestamps))
    unexpected = sorted(safe_stamps - expected - {auction_stamp})
    lunch = sorted(s for s in safe_stamps if s[:10] == iso_day and "11:30:00" < s[11:] < "13:01:00")
    wrong_code_count = sum(r["ts_code"] != code for r in rows)
    wrong_date_count = sum(s[:10] != iso_day for s in safe_stamps)
    result["query_window_valid"] = all(isinstance(r["trade_time"], str) and r["trade_time"] in expected for r in rows) and wrong_code_count == 0
    if not result["query_window_valid"]:
        issues.add("QUERY_WINDOW_MISMATCH")
    if not rows:
        issues.add("EMPTY_TABLE")
    if missing:
        issues.add("INCOMPLETE_240_BAR_SESSION")
    if duplicate:
        issues.add("DUPLICATE_TIMESTAMP")
    if unexpected or len(safe_stamps) != len(set(timestamps)) or len(timestamps) != len(rows):
        issues.add("TIMESTAMP_OUTSIDE_GRID")
    if lunch:
        issues.add("LUNCH_SESSION_TIMESTAMP")
    if wrong_code_count:
        issues.add("WRONG_CODE")
    if wrong_date_count:
        issues.add("WRONG_DATE")
    numeric = {}
    invalid_numeric = inconsistent_ohlc = zero_volume_conflict = auction_nonflat = 0
    for row in rows:
        try:
            values = {k: float(row[k]) for k in ("open", "high", "low", "close", "vol", "amount")}
            if any(isinstance(row[k], bool) or not math.isfinite(v) or v < 0 or (k in {"open", "high", "low", "close"} and v == 0) for k, v in values.items()):
                raise ValueError("invalid numeric")
        except (TypeError, ValueError, OverflowError):
            invalid_numeric += 1
            continue
        ohlc = {values[k] for k in ("open", "high", "low", "close")}
        inconsistent_ohlc += not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]
        zero_volume_conflict += values["vol"] == 0 and (len(ohlc) != 1 or values["amount"] != 0)
        auction_nonflat += row["trade_time"] == auction_stamp and len(ohlc) != 1
        if isinstance(row["trade_time"], str) and counts[row["trade_time"]] == 1:
            numeric[row["trade_time"]] = values
    changed_zero_volume, previous = 0, None
    for stamp in sorted(expected):
        value = numeric.get(stamp)
        if value is None:
            previous = None  # Do not bridge a missing or invalid minute.
            continue
        changed_zero_volume += value["vol"] == 0 and previous is not None and value["close"] != previous
        previous = value["close"]
    for count, category in ((invalid_numeric, "INVALID_PRICE_OR_VOLUME"), (inconsistent_ohlc, "INCONSISTENT_OHLC"),
                            (zero_volume_conflict, "ZERO_VOLUME_PRICE_AMOUNT_CONFLICT"),
                            (auction_nonflat, "AMBIGUOUS_0930_POINT"), (changed_zero_volume, "ZERO_VOLUME_PRICE_CHANGED")):
        if count:
            issues.add(category)
    result.update(continuous_unique_minutes=len(set(timestamps) & expected),
                  missing_bar_ends=missing, unexpected_timestamps=unexpected,
                  duplicate_timestamps=duplicate, lunch_timestamps=lunch,
                  auction_0930_rows=counts[auction_stamp], wrong_code_rows=wrong_code_count,
                  wrong_date_timestamps=wrong_date_count, invalid_numeric_rows=invalid_numeric,
                  inconsistent_ohlc_rows=inconsistent_ohlc, zero_volume_conflict_rows=zero_volume_conflict,
                  ambiguous_0930_rows=auction_nonflat, zero_volume_price_changed_rows=changed_zero_volume)
    try:
        minute.normalize_source_rows(rows, day, code)
        result["strict_adapter_accepts"] = True
    except ValueError as exc:
        message = str(exc)
        result["strict_adapter_error_category"] = ADAPTER_ERRORS.get(message, "UNCLASSIFIED_ADAPTER_REJECTION")
        result["strict_adapter_error"] = message if message in ADAPTER_ERRORS else None
    result["source_contract_accepts"] = result["strict_adapter_accepts"] and result["table_contract_valid"] and result["query_window_valid"]
    result["issues"] = sorted(issues)
    return result


def probe(output, *, token, runner_temp=None, call=safe.official_call, clock=time.monotonic, sleep=time.sleep):
    plan = contract()
    out = safe._fresh_output(output, runner_temp or os.environ.get("RUNNER_TEMP"))
    files = [safe._write(out, "request_contract.json", safe._json(plan), token)]
    report = {"schema_version": SCHEMA, "run_commit": os.environ.get("GITHUB_SHA"),
              "run_id": os.environ.get("GITHUB_RUN_ID"), "contract_sha256": safe._sha(safe._json(plan)),
              "source_sha256": safe._sha(Path(__file__).read_bytes()), "api_calls": 0,
              "research_adapter_sha256": safe._sha(Path(research_adapter.__file__).read_bytes()),
              "max_api_calls": MAX_CALLS, "requests": [], "source_files": files,
              "production_writes": False, "purchase_permission": False, "credential_persisted": False,
              "server_messages_persisted": False, "source_values_modified": False,
              "minute_grid_relaxed": False, "minute_truth_imported": False,
              "time_semantics": TIME_SEMANTICS, "provider_timestamp_semantics_confirmed": False,
              "training_performed": False, "settlement_performed": False, "release_allowed": False}
    started, next_request, stopped = clock(), clock(), None
    for case in plan["cases"]:
        params = request_parameters(case["trade_date"], case["ts_code"])
        receipt = {**case, "query_number": len(report["requests"]) + 1, "endpoint": "stk_mins",
                   "params": params, "fields": list(minute.FIELDS), "status": "PENDING_UNKNOWN",
                   "network_request_performed": False, "source_files": []}
        if not token.strip():
            receipt["status"] = "CREDENTIAL_ABSENT"
        elif stopped:
            receipt["status"] = "SKIPPED_AFTER_" + stopped
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
                    raw = call("stk_mins", params, minute.FIELDS, token, TIMEOUT_SECONDS)
                    if not isinstance(raw, bytes) or len(raw) > safe.MAX_RESPONSE_BYTES:
                        raise ValueError("invalid response bytes")
                    receipt.update(http_response_sha256=safe._sha(raw), http_response_bytes=len(raw))
                    payload = research_adapter._parse(raw)
                    receipt["status"] = classify(payload)
                    if receipt["status"] in {"ENTITLEMENT_DENIED", "CREDENTIAL_REJECTED", "RATE_LIMITED"}:
                        stopped = receipt["status"]
                    elif receipt["status"] == "SUCCESS":
                        data = _safe_data(payload)
                        body = safe._json(data)
                        # Guard decoded, canonical values too, including escaped credential echoes.
                        if token.encode() in body:
                            receipt["status"] = "CREDENTIAL_LIKE_DATA_NOT_PERSISTED"
                        else:
                            receipt["diagnosis"] = diagnose(data, case)
                            receipt["diagnosis"]["research_adapter_sha256"] = report["research_adapter_sha256"]
                            try:
                                encoded_data, encoded_meta = research_adapter.source_bytes(
                                    raw, case["trade_date"], case["ts_code"], request_params=params,
                                    fetched_at_utc=receipt["fetched_at_utc"], token=token)
                                receipt["diagnosis"].update(
                                    research_adapter_accepts=True,
                                    research_adapter_error_category=None,
                                    research_data_encoding_sha256=safe._sha(encoded_data),
                                    research_meta_encoding_sha256=safe._sha(encoded_meta))
                            except (ValueError, TypeError, OSError, OverflowError):
                                receipt["diagnosis"].update(
                                    research_adapter_accepts=False,
                                    research_adapter_error_category="RESEARCH_ADAPTER_REJECTED",
                                    research_data_encoding_sha256=None,
                                    research_meta_encoding_sha256=None)
                            stem = "responses/" + str(receipt["query_number"]).zfill(2) + "_stk_mins"
                            binding = safe._write(out, stem + ".data.json", body, token)
                            meta = {"schema_version": "dc20_minute_diagnostic_table_v2", "source": "tushare:stk_mins",
                                    "params": params, "data_sha256": binding["sha256"],
                                    "http_response_sha256": receipt["http_response_sha256"],
                                    "fetched_at_utc": receipt["fetched_at_utc"],
                                    "encoding": "ORIGINAL_DATA_TABLE_CANONICAL_JSON_NO_ENVELOPE_MESSAGES",
                                    "diagnostic_only": True, "immutable": True,
                                    "time_semantics": TIME_SEMANTICS,
                                    "source_values_modified": False, "credential_persisted": False}
                            pair = [binding, safe._write(out, stem + ".meta.json", safe._json(meta), token)]
                            receipt["source_files"] = pair
                            files.extend(pair)
                            if "QUERY_WINDOW_MISMATCH" in receipt["diagnosis"]["issues"]:
                                receipt["status"] = "DIAGNOSED_QUERY_WINDOW_MISMATCH_NOT_IMPORTED"
                            elif receipt["diagnosis"]["source_contract_accepts"] and not receipt["diagnosis"]["research_adapter_accepts"]:
                                receipt["status"] = "DIAGNOSED_RESEARCH_ADAPTER_REJECTED_NOT_IMPORTED"
                            else:
                                receipt["status"] = "DIAGNOSED_STRICT_ACCEPTED_NOT_IMPORTED" if receipt["diagnosis"]["source_contract_accepts"] else "DIAGNOSED_STRICT_REJECTED_NOT_IMPUTED"
                except error.HTTPError as exc:
                    receipt.update(status="HTTP_ERROR", http_status=exc.code)
                except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                    receipt["status"] = "INVALID_RESPONSE_NOT_INTERPRETED"
                except Exception:
                    receipt["status"] = "NETWORK_OR_RESPONSE_ERROR"
        report["requests"].append(receipt)
        files.append(safe._write(out, "receipts/" + str(receipt["query_number"]).zfill(2) + ".json", safe._json(receipt), token))
    report["elapsed_seconds"] = round(clock() - started, 3)
    report["status_counts"] = dict(Counter(r["status"] for r in report["requests"]))
    report["status"] = "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT" if all(r["status"].startswith("DIAGNOSED_") for r in report["requests"]) else "DIAGNOSTIC_PARTIAL_NOT_SETTLEMENT"
    safe._write(out, "receipt.json", safe._json(report), token)
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
