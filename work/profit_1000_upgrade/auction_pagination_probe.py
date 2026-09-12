#!/usr/bin/env python3
"""Two original-HTTP pagination SHAPE diagnostics; never market source truth.

No detail, server message, request-id value, table or raw response is saved.
Exact-type predicates explain the frozen placeholder precondition failure;
they do not qualify a source, imply pagination completeness or produce labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import auction_envelope_probe as shape

DATES = ("20250319", "20260203")
ENDPOINT = "stk_auction"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
MAX_BYTES, TIMEOUT_SECONDS, MAX_DIAGNOSTIC_COUNT = 4_000_000, 20, 10_000_000
SHAPE_HELPER_SHA256 = "a61919ad87a098bb60b3d494eda699c2d075f1ca1ad9ab9080c9b000e8a3a929"
HTTP_ADAPTER_SHA256 = "289fbcca8eb1642af7ea4a4c51bd6a136faf3ce96c5fc4c4e3a9091a369fa89c"
V3_CODEC_SHA256 = "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665"
BASE_FAILED_RUN = {"run_id": "34684000971", "artifact_id": "10295337206",
                   "archive_sha256": "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"}
SCHEMA = "dc20_auction_pagination_shape_probe_20260912_v1"
SCOPE = "ONLY_PAGINATION_SHAPE_DIAGNOSTIC_NOT_SOURCE"
FLAGS = {"diagnostic_only": True, "research_only": True, "diagnostic_scope": SCOPE,
         "source_import_allowed": False, "source_files_written": False,
         "entry_source_eligible": False, "label_source_eligible": False,
         "training_performed": False, "settlement_performed": False,
         "fallback_generated": False, "production_writes": False,
         "production_activation_allowed": False, "codec_allowlist_modified": False,
         "full_codec_validation_performed": False, "source_values_modified": False,
         "raw_response_saved": False, "market_table_saved": False,
         "detail_value_saved": False, "server_message_saved": False,
         "request_id_value_saved": False, "credential_persisted": False,
         "predicates_are_source_qualification": False, "failed_preconditions_may_be_ignored": False}
_IMPLEMENTATIONS = tuple(HERE / name for name in (
    "auction_pagination_probe.py", "AUCTION_PAGINATION_PROBE.json", "auction_envelope_probe.py",
    "AUCTION_ENVELOPE_PROBE.json", "auction_http_v3.py", "auction_truth_v3.py"))


class PaginationProbeError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise PaginationProbeError(reason)


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
             "REGULAR_UNALIASED_CODE_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_HASHES = {path: _file_sha(path) for path in _IMPLEMENTATIONS}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "canonical_two_date_pagination_shape_20260912_v1",
            "endpoint": ENDPOINT, "trade_dates": list(DATES), "fields": list(FIELDS),
            "max_api_calls": 2, "calls_per_date": 1, "retries": 0,
            "timeout_seconds": TIMEOUT_SECONDS, "max_http_response_bytes": MAX_BYTES,
            "redirects_allowed": False, "shape_helper_sha256": SHAPE_HELPER_SHA256,
            "http_adapter_sha256": HTTP_ADAPTER_SHA256, "v3_codec_sha256": V3_CODEC_SHA256,
            "base_failed_run": dict(BASE_FAILED_RUN), "diagnostic_count_maximum": MAX_DIAGNOSTIC_COUNT,
            "response_persistence": "SHAPE_EXACT_TYPE_PREDICATES_AND_BOUNDED_INTEGER_COUNT_ONLY",
            "nonzero_code_message_categories": ["AUTH", "RATE_LIMIT", "ENTITLEMENT", "OTHER"], **FLAGS}


def _guard():
    for name, expected in (("auction_envelope_probe.py", SHAPE_HELPER_SHA256),
                           ("auction_http_v3.py", HTTP_ADAPTER_SHA256), ("auction_truth_v3.py", V3_CODEC_SHA256)):
        _require(_file_sha(HERE / name) == expected, "PINNED_DIAGNOSTIC_DEPENDENCY_CHANGED")
    for path, expected in _IMPORTED_HASHES.items():
        _require(_file_sha(path) == expected, "PAGINATION_PROBE_CODE_CHANGED_SINCE_IMPORT")
    contract = shape._parse((HERE / "AUCTION_PAGINATION_PROBE.json").read_bytes())
    _require(contract == expected_contract(), "PAGINATION_PROBE_REQUEST_CHANGED")
    return contract


def _type(value, present=True):
    return {dict: "object", list: "array", str: "string", int: "integer", float: "number",
            bool: "boolean", type(None): "null"}.get(type(value), "unknown") if present else "missing"


def _server_error_category(payload):
    if type(payload) is not dict or type(payload.get("code")) is not int or payload["code"] == 0:
        return None
    message = payload.get("msg")
    if type(message) is not str:
        return "OTHER"
    # No text (including unknown or credential-bearing messages) is returned.
    message = message.casefold()
    if any(word in message for word in ("token", "credential", "authentication", "密码", "凭证", "密钥", "认证")):
        return "AUTH"
    if any(word in message for word in ("rate limit", "too many requests", "频率", "每分钟", "限流")):
        return "RATE_LIMIT"
    if any(word in message for word in ("permission", "entitlement", "权限", "授权", "积分")):
        return "ENTITLEMENT"
    return "OTHER"


def diagnose_response(raw, trade_date, *, token=""):
    """Pure diagnostic on original bytes; never invoke codec/load/entry logic."""
    _require(type(trade_date) is str and trade_date in DATES, "PAGINATION_PROBE_DATE_OUT_OF_SCOPE")
    _require(type(token) is str, "CREDENTIAL_ARGUMENT_TYPE_INVALID")
    observed = {**shape.diagnose_response(raw, token=token), "trade_date": trade_date}
    result = {"schema_version": SCHEMA, "trade_date": trade_date, "status": "PAGINATION_NOT_PARSED",
              "envelope_shape": observed, "pagination": None, "server_error_category": None, **FLAGS}
    if observed["status"] != "DIAGNOSTIC_RESPONSE_SHAPE_CAPTURED":
        return result
    payload = shape._parse(raw)
    object_ok = type(payload) is dict
    data = payload.get("data") if object_ok else None
    data_ok = type(data) is dict
    detail = payload.get("detail") if object_ok else None
    items_present = data_ok and "items" in data
    items = data.get("items") if data_ok else None
    items_ok = type(items) is list
    rows = len(items) if items_ok else None
    count_present, has_more_present = data_ok and "count" in data, data_ok and "has_more" in data
    count, has_more = (data.get("count"), data.get("has_more")) if data_ok else (None, None)
    count_int = count_present and type(count) is int
    count_zero = count_int and count == 0
    count_equals_rows = count_int and items_ok and count == rows
    predicates = {
        "DETAIL_EXACT_ASCII_PLACEHOLDER": type(detail) is str and detail == "...",
        "CODE_EXACT_INTEGER_ZERO": object_ok and type(payload.get("code")) is int and payload["code"] == 0,
        "DATA_EXACT_OBJECT": data_ok, "HAS_MORE_PRESENT": has_more_present,
        "HAS_MORE_EXACT_FALSE": has_more_present and has_more is False,
        "ITEMS_EXACT_ARRAY": items_ok, "COUNT_PRESENT": count_present,
        "COUNT_EXACT_INTEGER": count_int, "COUNT_ZERO_OR_EQUALS_ITEMS_LENGTH": count_zero or count_equals_rows}
    pagination = {"detail_is_ascii_placeholder": predicates["DETAIL_EXACT_ASCII_PLACEHOLDER"],
                  "code_is_exact_integer_zero": predicates["CODE_EXACT_INTEGER_ZERO"], "data_is_exact_dict": data_ok,
                  "has_more_present": has_more_present, "has_more_type": _type(has_more, has_more_present),
                  "has_more_is_exact_false": has_more_present and has_more is False,
                  "has_more_is_exact_true": has_more_present and has_more is True,
                  "items_present": items_present, "items_type": _type(items, items_present),
                  "items_is_exact_list": items_ok, "items_row_count": rows,
                  "count_present": count_present, "count_type": _type(count, count_present),
                  "count_is_exact_integer": count_int, "count_is_zero": count_zero,
                  "count_equals_items_row_count": count_equals_rows,
                  "count_value_if_bounded_integer": count if count_int and 0 <= count <= MAX_DIAGNOSTIC_COUNT else None,
                  "placeholder_preconditions_pass": all(predicates.values()),
                  "placeholder_preconditions": predicates,
                  "failed_placeholder_preconditions": [key for key, passed in predicates.items() if not passed]}
    return {**result, "status": "PAGINATION_DIAGNOSTIC_CAPTURED", "pagination": pagination,
            "server_error_category": _server_error_category(payload)}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def official_call(endpoint, params, fields, token, timeout):
    _require(endpoint == ENDPOINT and type(params) is dict and set(params) == {"trade_date"}
             and type(params["trade_date"]) is str and params["trade_date"] in DATES and tuple(fields) == FIELDS
             and type(timeout) is int and timeout == TIMEOUT_SECONDS and type(token) is str,
             "PAGINATION_PROBE_REQUEST_SCOPE_CHANGED")
    body = json.dumps({"api_name": ENDPOINT, "params": params, "fields": ",".join(FIELDS), "token": token}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.build_opener(_NoRedirect()).open(req, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_BYTES + 1)
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "ORIGINAL_HTTP_BYTES_REQUIRED_WITHIN_4MB")
    return raw


def run_probe(output, *, token, call=None):
    _require(type(token) is str, "CREDENTIAL_ARGUMENT_TYPE_INVALID")
    _guard()
    _require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "DIAGNOSTIC_RERUN_NOT_ALLOWED")
    output = Path(output)
    _require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)),
             "FRESH_UNALIASED_DIAGNOSTIC_OUTPUT_REQUIRED")
    output = output.resolve()
    _require(output != CHECKOUT and CHECKOUT not in output.parents and output not in CHECKOUT.parents,
             "DIAGNOSTIC_OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    transport, injected = official_call if call is None else call, call is not None
    _require(callable(transport), "CALLABLE_TRANSPORT_REQUIRED")
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for day in DATES:
        _guard()
        item = {"trade_date": day, "status": "CREDENTIAL_ABSENT", "api_calls": 0,
                "network_request_performed": False, "callable_injected_for_test": injected,
                "request": {"api_name": ENDPOINT, "params": {"trade_date": day}, "fields": list(FIELDS)},
                "timeout_seconds": TIMEOUT_SECONDS, "max_http_response_bytes": MAX_BYTES, "diagnostic": None, **FLAGS}
        if token.strip():
            item.update(status="REQUEST_STARTED", api_calls=1, network_request_performed=not injected)
            try:
                raw = transport(ENDPOINT, {"trade_date": day}, FIELDS, token, TIMEOUT_SECONDS)
                item["diagnostic"] = diagnose_response(raw, day, token=token)
                item["status"] = ("DIAGNOSTIC_COMPLETE" if item["diagnostic"]["status"] == "PAGINATION_DIAGNOSTIC_CAPTURED"
                                  else "INVALID_RESPONSE_DIAGNOSTIC")
            except error.HTTPError as exc:
                item.update(status="HTTP_ERROR", http_status=exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None)
            except Exception:
                item.update(status="NETWORK_OR_RESPONSE_ERROR", reason="NETWORK_OR_RESPONSE_ERROR")
        _guard()
        results.append(item)
    _guard()
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schema_version": SCHEMA,
              "status": "DIAGNOSTIC_COMPLETE" if all(r["status"] == "DIAGNOSTIC_COMPLETE" for r in results) else "DIAGNOSTIC_INCOMPLETE",
              "api_calls": sum(r["api_calls"] for r in results), "max_api_calls": 2, "retries": 0,
              "trade_dates": list(DATES), "base_failed_run": dict(BASE_FAILED_RUN), "requests": results,
              "callable_injected_for_test": injected, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
              "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None,
              "request_contract_sha256": _IMPORTED_HASHES[HERE / "AUCTION_PAGINATION_PROBE.json"],
              "execution_file_bindings": {p.relative_to(CHECKOUT).as_posix(): digest for p, digest in _IMPORTED_HASHES.items()}, **FLAGS}
    serialized = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    _require(not token or token.encode() not in serialized, "CREDENTIAL_LIKE_REPORT_VALUE_FORBIDDEN")
    target = output / "auction_pagination_probe.json"
    _require(not target.exists() and not any(p.is_symlink() for p in (target, *target.parents)), "DIAGNOSTIC_TARGET_CHANGED")
    with target.open("xb") as handle:
        handle.write(serialized)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps({"status": report["status"], "api_calls": report["api_calls"],
                      "diagnostic_scope": SCOPE, "source_import_allowed": False}, sort_keys=True))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
