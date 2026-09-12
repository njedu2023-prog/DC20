#!/usr/bin/env python3
"""Two immutable, non-source auction diagnostics; never repairs or imports data.

The original safe JSON data table is retained, not a response envelope. Each
row is also passed to the unchanged codec in a SYNTHETIC single-row envelope
for diagnosis only. Those envelopes are never saved or eligible price truth.
The 60-second budget is a request-admission deadline; each admitted HTTPS call
has a separate 20-second timeout. The workflow supplies the outer runtime cap.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
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
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

SCHEMA = "dc20_two_fullmarket_auction_diagnostics_20260912_v1"
DATES = ("20250116", "20260817")
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
CODEC_SHA256 = "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af"
COLLECT_SHA256 = "9885bb417779638bb85f5a03893137508a17d21ae3ba93d25732cbb4e83af9fa"
ARCHIVE_SHA256 = "58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64"
MAX_BYTES, MAX_ROWS, MAX_CALLS, TIMEOUT = 4_000_000, 8000, 2, 20
FLAGS = {"research_only": True, "diagnostic_only": True, "source_import_allowed": False,
         "entry_source_eligible": False, "label_source_eligible": False,
         "production_activation_allowed": False, "production_writes": False,
         "training_performed": False, "settlement_performed": False,
         "fallback_generated": False, "source_values_modified": False,
         "credential_persisted": False, "server_messages_persisted": False,
         "full_response_envelope_retained": False}
CODEC_REASONS = frozenset({
    "INVALID_TRADE_DATE", "INVALID_STOCK_CODE", "INVALID_NUMERIC_VALUE",
    "INVALID_REQUEST_CONTRACT", "INVALID_REQUEST_PARAMS", "REQUEST_DATE_CODE_ENDPOINT_OR_FIELDS_MISMATCH",
    "INVALID_FETCH_TIMESTAMP", "REAL_NETWORK_ATTEMPT_REQUIRED", "INVALID_CREDENTIAL_ARGUMENT",
    "INVALID_HTTP_RESPONSE_BYTES", "INVALID_HTTP_RESPONSE_JSON", "INVALID_API_ENVELOPE",
    "INVALID_DATA_TABLE", "INVALID_FIELDS", "INVALID_ROW_COUNT", "PAGINATED_SOURCE_FORBIDDEN",
    "POSITIVE_COUNT_MISMATCH", "INVALID_ROW_SHAPE", "SOURCE_WRONG_TRADE_DATE",
    "SOURCE_WRONG_REQUESTED_CODE", "DUPLICATE_STOCK_ROW", "INVALID_SHARE_VOLUME",
    "ZERO_VOLUME_NONZERO_AMOUNT", "POSITIVE_VOLUME_REQUIRES_PRICE_AND_AMOUNT",
    "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT", "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE",
    "OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE", "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE",
    "API_FAILURE_WITH_DATA_CONFLICT", "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED",
})


class DiagnosticError(ValueError):
    """Only fixed local enum strings are used as diagnostic exceptions."""


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _strict_json(raw):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise DiagnosticError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise DiagnosticError("NONFINITE_JSON_NUMBER")
        return parsed
    def constant(value):
        raise DiagnosticError("NONFINITE_JSON_NUMBER")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
    except DiagnosticError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise DiagnosticError("INVALID_RESPONSE_JSON") from None


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "two_failed_fullmarket_auction_dates_20260912_v1",
            "endpoint": "stk_auction", "fields": list(FIELDS),
            "requests": [{"trade_date": day} for day in DATES],
            "max_api_calls": 2, "retries": 0, "requests_per_second": 1,
            "timeout_seconds": 20, "max_seconds": 60,
            "max_response_bytes": MAX_BYTES, "max_rows": MAX_ROWS,
            "max_representative_rows_per_reason": 5,
            "codec_sha256": CODEC_SHA256, "collector_sha256": COLLECT_SHA256,
            "source_run_id": "34676871475", "source_commit": "df6c38806da20066f673875a4a83c37fa895d2e4",
            "source_archive_sha256": ARCHIVE_SHA256,
            "original_status": "PENDING_INVALID_RESPONSE_NOT_IMPUTED", **FLAGS}


def _dependencies():
    for path, sha in ((HERE / "auction_truth.py", CODEC_SHA256), (HERE / "collect.py", COLLECT_SHA256)):
        if any(p.is_symlink() for p in (path, *path.parents)) or _sha(path.read_bytes()) != sha:
            raise DiagnosticError("PINNED_DEPENDENCY_CHANGED")
    from work.profit_1000_upgrade import auction_truth, collect
    return auction_truth, collect


def contract():
    path = HERE / "AUCTION_DIAGNOSTIC_REQUEST.json"
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise DiagnosticError("REQUEST_SYMLINK_FORBIDDEN")
    value = _strict_json(path.read_bytes())
    if value != expected_contract():
        raise DiagnosticError("FIXED_REQUEST_CONTRACT_CHANGED")
    _dependencies()
    return value


def _credential_guard(payload, raw, token):
    if token and token.encode() in raw:
        raise DiagnosticError("CREDENTIAL_LIKE_RESPONSE_NOT_RETAINED")
    todo = [payload]
    while todo:
        value = todo.pop()
        if isinstance(value, dict):
            todo.extend(value.keys())
            todo.extend(value.values())
        elif isinstance(value, list):
            todo.extend(value)
        elif isinstance(value, str):
            if token and token in value:
                raise DiagnosticError("CREDENTIAL_LIKE_RESPONSE_NOT_RETAINED")
            if re.search(r"token|credential|password|secret|authorization|bearer|api[_-]?key|凭证|密钥", value, re.I):
                raise DiagnosticError("CREDENTIAL_LIKE_RESPONSE_NOT_RETAINED")


def _safe_table(payload):
    data = payload.get("data")
    if not isinstance(data, dict) or not {"fields", "items"} <= set(data) or set(data) - {"fields", "items", "count", "has_more"}:
        raise DiagnosticError("UNSAFE_DATA_TABLE_NOT_RETAINED")
    fields, items = data["fields"], data["items"]
    if (not isinstance(fields, list) or not len(fields) <= 32 or
        any(not isinstance(f, str) or re.fullmatch(r"[A-Za-z0-9_]{1,64}", f) is None for f in fields) or
        not isinstance(items, list) or len(items) > MAX_ROWS):
        raise DiagnosticError("UNSAFE_TABLE_SHAPE_NOT_RETAINED")
    values = list(fields) + [data.get(key) for key in ("count", "has_more")]
    for row in items:
        if not isinstance(row, list) or len(row) > 32:
            raise DiagnosticError("UNSAFE_ROW_SHAPE_NOT_RETAINED")
        values.extend(row)
    for value in values:
        if value is not None and type(value) not in (bool, int, float, str):
            raise DiagnosticError("UNSAFE_NESTED_VALUE_NOT_RETAINED")
        if isinstance(value, str) and (len(value) > 128 or re.search(r"[^A-Za-z0-9_.+\-]", value)):
            raise DiagnosticError("UNSAFE_STRING_NOT_RETAINED")
        if isinstance(value, str) and len(value) >= 32 and re.fullmatch(r"[A-Za-z0-9_-]+", value) and not value.isdecimal():
            raise DiagnosticError("CREDENTIAL_LIKE_TABLE_NOT_RETAINED")
        if isinstance(value, float) and not math.isfinite(value):
            raise DiagnosticError("NONFINITE_TABLE_NOT_RETAINED")
    return data


def _validation(call, codec):
    try:
        call()
        return {"accepted": True, "rejection_class": None, "reason": None}
    except codec.AuctionSourceError as exc:
        reason = str(exc)
        return {"accepted": False, "rejection_class": "AuctionSourceError",
                "reason": reason if reason in CODEC_REASONS else "UNCLASSIFIED_CODEC_REJECTION"}
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        return {"accepted": False, "rejection_class": "UnexpectedCodecValidationError",
                "reason": "UNCLASSIFIED_CODEC_REJECTION"}


def diagnose_response(raw, day, *, token, fetched_at_utc):
    """Return safe original data (or None), plus bounded fixed-enum diagnostics."""
    codec, _ = _dependencies()
    if day not in DATES:
        raise DiagnosticError("DATE_OUTSIDE_FIXED_DIAGNOSTIC")
    report = {"trade_date": day, "request": codec.request_contract(day),
              "fetched_at_utc": fetched_at_utc, "http_response_sha256": None,
              "http_response_bytes": None, "original_table_retained": False, **FLAGS}
    if not isinstance(raw, bytes):
        report.update(status="DIAGNOSTIC_RESPONSE_REJECTED", reason="NONBYTE_RESPONSE")
        return None, report
    report.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw))
    if len(raw) > MAX_BYTES:
        report.update(status="DIAGNOSTIC_RESPONSE_REJECTED", reason="RESPONSE_EXCEEDS_4MB")
        return None, report
    try:
        payload = _strict_json(raw)
        _credential_guard(payload, raw, token)
    except DiagnosticError as exc:
        report.update(status="DIAGNOSTIC_RESPONSE_REJECTED", reason=str(exc))
        return None, report
    report["codec_source_validation"] = _validation(
        lambda: codec.source_bytes(raw, day, request=codec.request_contract(day),
                                  fetched_at_utc=fetched_at_utc, network_request_performed=True, token=token), codec)
    if not isinstance(payload, dict) or type(payload.get("code")) is not int:
        report.update(status="DIAGNOSTIC_RESPONSE_REJECTED", reason="INVALID_API_ENVELOPE")
        return None, report
    report["api_code"] = payload["code"]
    if payload["code"] != 0:
        report.update(status="DIAGNOSTIC_API_ERROR", reason="NONZERO_API_CODE")
        return None, report
    try:
        data = _safe_table(payload)
    except DiagnosticError as exc:
        report.update(status="DIAGNOSTIC_RESPONSE_REJECTED", reason=str(exc))
        return None, report
    # This tests the ORIGINAL full table, including duplicates, field schema,
    # pagination/count, and cross-row date/code constraints, without alteration.
    report["original_table_validation"] = _validation(lambda: codec._table(data, day, None), codec)
    counts, representatives = Counter(), {}
    accepted = 0
    for index, row in enumerate(data["items"]):
        # Deliberately diagnostic-only: no original count/pagination is copied
        # into a synthetic single-row envelope and it is never persisted.
        synthetic = {"fields": data["fields"], "items": [row]}
        check = _validation(lambda: codec._table(synthetic, day, None), codec)
        if check["accepted"]:
            accepted += 1
        else:
            reason = check["reason"]
            counts[reason] += 1
            if len(representatives.setdefault(reason, [])) < 5:
                representatives[reason].append({"original_row_index": index, "original_values": row})
    duplicate_counts = {}
    if data["fields"].count("ts_code") == 1:
        position = data["fields"].index("ts_code")
        code_counts = Counter(row[position] for row in data["items"]
                              if len(row) > position and isinstance(row[position], str))
        duplicate_counts = {code: count for code, count in sorted(code_counts.items()) if count > 1}
    report.update(status="DIAGNOSTIC_COMPLETE", original_table_retained=True,
                  original_table_sha256=_sha(_json(data)), row_count=len(data["items"]),
                  row_diagnostic={"synthetic_single_row_envelopes": True, "eligible_as_source": False,
                                  "row_values_modified": False, "checked_rows": len(data["items"]),
                                  "accepted_rows": accepted, "rejection_reason_counts": dict(sorted(counts.items())),
                                  "representatives_per_reason_limit": 5,
                                  "representative_rows": representatives,
                                  "cross_row_checks_not_replaced": True},
                  duplicate_stock_rows=duplicate_counts)
    return data, report


def _fresh_output(output, runner_temp):
    if not runner_temp:
        raise DiagnosticError("RUNNER_TEMP_REQUIRED")
    temp, out = Path(runner_temp), Path(output)
    if not temp.is_dir() or not out.is_absolute() or any(p.is_symlink() for p in (temp, *temp.parents, out, *out.parents)):
        raise DiagnosticError("OUTPUT_SYMLINK_OR_LOCATION_FORBIDDEN")
    temp, out = temp.resolve(), out.resolve()
    if (temp not in out.parents or ROOT == out or ROOT in out.parents or out in ROOT.parents or
        out.exists() or not out.parent.is_dir()):
        raise DiagnosticError("OUTPUT_MUST_BE_FRESH_ISOLATED_RUNNER_TEMP_CHILD")
    out.mkdir()
    return out


def _write(out, name, value, token):
    path = out / name
    body = _json(value)
    if (path.exists() or path.is_symlink() or any(p.is_symlink() for p in (out, *out.parents)) or
        path.parent != out or (token and token.encode() in body)):
        raise DiagnosticError("IMMUTABLE_SAFE_OUTPUT_REQUIRED")
    with path.open("xb") as handle:
        handle.write(body)
    return {"path": name, "sha256": _sha(body), "bytes": len(body)}


def probe(output, *, token, runner_temp=None, call=None, clock=time.monotonic, sleep=time.sleep):
    plan = contract()
    _, collector = _dependencies()
    pinned_files = (HERE / "auction_truth.py", HERE / "collect.py",
                    HERE / "AUCTION_DIAGNOSTIC_REQUEST.json", Path(__file__))
    execution_bindings = {path.relative_to(ROOT).as_posix(): _sha(path.read_bytes()) for path in pinned_files}
    if not isinstance(token, str) or (token and len(token) < 16):
        raise DiagnosticError("INVALID_CREDENTIAL_ARGUMENT")
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise DiagnosticError("DUPLICATE_RUN_ATTEMPT_FORBIDDEN")
    out = _fresh_output(output, runner_temp or os.environ.get("RUNNER_TEMP"))
    run_id, commit = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_SHA")
    report = {"schema_version": SCHEMA, "request_contract_sha256": _sha(_json(plan)),
              "run_id": run_id if run_id and re.fullmatch(r"\d+", run_id) else None,
              "run_commit": commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None,
              "source_code_sha256": _sha(Path(__file__).read_bytes()),
              "execution_file_bindings": execution_bindings,
              "api_calls": 0, "max_api_calls": MAX_CALLS, "retries": 0,
              "requests": [], "source_files": [], **FLAGS}
    report["source_files"].append(_write(out, "request_contract.json", plan, token))
    if not token:
        report["status"] = "BLOCKED_MISSING_CREDENTIAL"
    else:
        caller = call or collector.official_call_v2
        started = last_started = clock()
        for number, day in enumerate(DATES, start=1):
            if number > MAX_CALLS or clock() - started >= plan["max_seconds"]:
                report["status"] = "STOPPED_HARD_BUDGET"
                break
            if number > 1:
                remaining = 1.0 - (clock() - last_started)
                if remaining > 0:
                    sleep(remaining)
                if clock() - last_started < 1.0:
                    raise DiagnosticError("REQUEST_RATE_LIMIT_CLOCK_CONFLICT")
                if clock() - started >= plan["max_seconds"]:
                    report["status"] = "STOPPED_HARD_BUDGET"
                    break
            receipt = {"trade_date": day, "query_number": number, "endpoint": "stk_auction",
                       "params": {"trade_date": day}, "fields": list(FIELDS), "timeout_seconds": TIMEOUT,
                       "network_request_performed": True, "source_files": [], **FLAGS}
            report["api_calls"] += 1
            last_started = clock()
            try:
                raw = caller("stk_auction", {"trade_date": day}, FIELDS, token, TIMEOUT)
            except (error.HTTPError, error.URLError, TimeoutError, ConnectionError, OSError):
                receipt.update(status="DIAGNOSTIC_NETWORK_FAILURE", reason="NETWORK_FAILURE_NO_RETRY")
            except (ValueError, TypeError):
                receipt.update(status="DIAGNOSTIC_TRANSPORT_RESPONSE_REJECTED", reason="INVALID_BOUNDED_TRANSPORT_RESPONSE")
            else:
                data, diagnosis = diagnose_response(raw, day, token=token,
                    fetched_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                receipt.update(diagnosis)
                if data is not None:
                    binding = _write(out, f"{day}.original_data.json", data, token)
                    receipt["source_files"].append(binding)
                    report["source_files"].append(binding)
            report["requests"].append(receipt)
        report.setdefault("status", "DIAGNOSTIC_REQUESTS_COMPLETE")
    if any(any(p.is_symlink() for p in (path, *path.parents)) or
           _sha(path.read_bytes()) != execution_bindings[path.relative_to(ROOT).as_posix()]
           for path in pinned_files):
        raise DiagnosticError("EXECUTION_FILES_CHANGED_DURING_DIAGNOSTIC")
    _write(out, "auction_diagnostic.json", report, token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps({"status": result["status"], "api_calls": result["api_calls"],
                      "diagnostic_only": True, "production_activation_allowed": False}))


if __name__ == "__main__":
    main()
