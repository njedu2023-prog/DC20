#!/usr/bin/env python3
"""Exactly two single-stock/day original-HTTP diagnostics, never source data.

No prices, table values, messages, request-id or raw response are persisted.
Pure table validation is not price/capacity qualification. No source codec
writer/loader, labels, fallback, pagination, batch collection or training runs.
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
from work.profit_1000_upgrade import auction_pagination_probe as pagination
from work.profit_1000_upgrade import auction_truth_v3 as codec

PAIRS = (("20250319", "000612.SZ"), ("20260203", "000995.SZ"))
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
ENDPOINT, MAX_CALLS, TIMEOUT_SECONDS, MAX_BYTES = "stk_auction", 2, 20, 4_000_000
CONTRACT_FILE, OUTPUT_FILE = "AUCTION_SINGLE_STOCK_PROBE.json", "auction_single_stock_probe.json"
SCHEMA = "dc20_auction_single_stock_shape_probe_20260912_v1"
PINNED_DEPENDENCIES = {
    "auction_pagination_probe.py": "a04bbf123339a3fffa51120566565bc1582ba36cb9974b27b0ff631ce5f25f04",
    "auction_envelope_probe.py": "a61919ad87a098bb60b3d494eda699c2d075f1ca1ad9ab9080c9b000e8a3a929",
    "auction_truth.py": "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af",
    "auction_truth_v3.py": "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665",
    "auction_http_v3.py": "289fbcca8eb1642af7ea4a4c51bd6a136faf3ce96c5fc4c4e3a9091a369fa89c"}
BASE_ARCHIVE_SHA256 = "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
VERIFIED_STK_FAILURE = {"run_id": "34686515444", "run_commit": "25fc20006763338e445933357edc435a6522e22a",
    "artifact_id": "10295762271", "archive_sha256": "cb9c59a99bed0fb19f83bac21a6ec7bbd8eeb0f5e4f8fac9953bbb5c8d17a746",
    "trade_date": "20250319", "api_calls": 1, "reason": "STOCKS_TABLE_REQUIRES_EXPLICIT_COMPLETE_PAGINATION",
    "http_response_bytes": 384539, "http_response_sha256": "6fc0d8a9761fe51e6504cdee76ae0cf5abbd44a11c8c4672c226610e68814e78",
    "individual_pagination_predicate_failure_known": False}
FLAGS = {"diagnostic_only": True, "research_only": True, "source_import_allowed": False,
         "source_files_written": False, "entry_source_eligible": False, "label_source_eligible": False,
         "training_performed": False, "settlement_performed": False, "fallback_generated": False,
         "actual_execution_claimed": False, "actual_capacity_verified": False,
         "capacity_qualification_performed": False, "entry_price_qualification_performed": False,
         "production_writes": False, "production_activation_allowed": False,
         "raw_response_saved": False, "market_table_saved": False, "response_values_saved": False,
         "detail_value_saved": False, "server_message_saved": False, "request_id_value_saved": False,
         "credential_persisted": False, "codec_allowlist_modified": False,
         "table_validation_is_source_qualification": False}
TABLE_REASONS = frozenset({"INVALID_DATA_TABLE", "INVALID_FIELDS", "INVALID_ROW_COUNT_OR_POSSIBLE_TRUNCATION",
    "PAGINATED_SOURCE_FORBIDDEN", "POSITIVE_COUNT_MISMATCH", "INVALID_ROW_SHAPE", "UNSAFE_NESTED_TABLE_VALUE",
    "OVERSIZED_TABLE_VALUE", "NONFINITE_TABLE_VALUE", "SOURCE_WRONG_TRADE_DATE", "SOURCE_WRONG_REQUESTED_CODE",
    "DUPLICATE_STOCK_ROW", "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED", "INVALID_STOCK_CODE"})


class SingleStockProbeError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise SingleStockProbeError(reason)


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "REGULAR_UNALIASED_CODE_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# Do not read the not-yet-created registration JSON during import.
_IMPORTED_CODE = {name: _file_sha(HERE / name) for name in (Path(__file__).name, *PINNED_DEPENDENCIES)}


def request_contract(trade_date, ts_code):
    _require(type(trade_date) is str and type(ts_code) is str and (trade_date, ts_code) in PAIRS,
             "SINGLE_STOCK_PROBE_PAIR_OUT_OF_SCOPE")
    return {"api_name": ENDPOINT, "params": {"trade_date": trade_date, "ts_code": ts_code}, "fields": list(FIELDS)}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "canonical_two_single_stock_days_20260912_v1",
            "requests": [request_contract(day, code) for day, code in PAIRS], "max_api_calls": MAX_CALLS,
            "calls_per_pair": 1, "retries": 0, "socket_timeout_seconds": TIMEOUT_SECONDS,
            "max_http_response_bytes": MAX_BYTES, "redirects_allowed": False,
            "pagination_or_request_fallback_allowed": False, "branch": "main", "run_attempt": 1,
            "base_archive_sha256": BASE_ARCHIVE_SHA256,
            "verified_stocks_scope_failure": dict(VERIFIED_STK_FAILURE),
            "candidate_identity_basis": "PINNED_BASE_FROZEN_GAP_CANDIDATE_CODES_NOT_OLD_OUTCOMES",
            "frozen_dependencies": dict(PINNED_DEPENDENCIES),
            "response_persistence": "STRUCTURE_BOOLEAN_COUNTS_SAFE_REASONS_AND_ORIGINAL_HTTP_SHA_ONLY",
            "output_file": OUTPUT_FILE, "documentation": "https://tushare.pro/document/2?doc_id=369", **FLAGS}


def _guard():
    for name, expected in PINNED_DEPENDENCIES.items():
        _require(_file_sha(HERE / name) == expected, "PINNED_SINGLE_STOCK_DEPENDENCY_CHANGED")
    for name, expected in _IMPORTED_CODE.items():
        _require(_file_sha(HERE / name) == expected, "SINGLE_STOCK_PROBE_CODE_CHANGED_SINCE_IMPORT")
    path = HERE / CONTRACT_FILE
    digest = _file_sha(path)
    _require(pagination.shape._parse(path.read_bytes()) == expected_contract(), "SINGLE_STOCK_PROBE_CONTRACT_CHANGED")
    return {**_IMPORTED_CODE, CONTRACT_FILE: digest}


def diagnose_response(raw, trade_date, ts_code, *, token=""):
    """Pure original-byte observation; returned rows never carry source authority."""
    request_contract(trade_date, ts_code)
    _require(type(token) is str, "INVALID_CREDENTIAL_ARGUMENT")
    shape = pagination.diagnose_response(raw, trade_date, token=token)
    result = {"trade_date": trade_date, "ts_code": ts_code, "status": "SINGLE_STOCK_RESPONSE_NOT_PARSED",
              "reason": "BOUNDED_STRICT_JSON_REQUIRED", "envelope_shape": shape["envelope_shape"],
              "pagination": shape["pagination"], "server_error_category": shape["server_error_category"],
              "table_structure_validation_performed": False, "same_requested_identity_observed": False,
              "complete_nonempty_single_row_observed": False, "row_count": None, **FLAGS}
    if shape["status"] != "PAGINATION_DIAGNOSTIC_CAPTURED":
        return result
    payload = pagination.shape._parse(raw)
    try:
        _require(type(payload) is dict and not set(payload) - {"code", "msg", "data", "request_id", "detail"},
                 "INVALID_SINGLE_STOCK_API_ENVELOPE")
        if token:
            _require(token.encode() not in raw, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
            codec._credential_guard(payload, token, persisted_values=False)
        _require(type(payload.get("code")) is int and payload["code"] == 0, "SUCCESS_CODE_EXACT_ZERO_REQUIRED")
        _require("detail" not in payload or type(payload["detail"]) is str and payload["detail"] in ("", "..."),
                 "NONEMPTY_OR_INVALID_API_DETAIL")
        data = payload.get("data")
        _require(type(data) is dict and type(data.get("items")) is list and data.get("has_more") is False
                 and type(data.get("count")) is int and data["count"] in (0, len(data["items"])),
                 "EXPLICIT_COMPLETE_TABLE_REQUIRED")
        result["table_structure_validation_performed"] = True
        rows = codec.table_rows(data, trade_date, requested_code=ts_code)
    except SingleStockProbeError as exc:
        return {**result, "status": "SINGLE_STOCK_RESPONSE_BLOCKED", "reason": str(exc)}
    except codec.AuctionSourceError as exc:
        reason = str(exc)
        return {**result, "status": "SINGLE_STOCK_RESPONSE_BLOCKED",
                "reason": reason if reason in TABLE_REASONS else "INVALID_SINGLE_STOCK_TABLE"}
    if not rows:
        return {**result, "status": "EMPTY_NOT_PRICE_EVIDENCE", "reason": None, "row_count": 0}
    return {**result, "status": "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED", "reason": None, "row_count": 1,
            "same_requested_identity_observed": True, "complete_nonempty_single_row_observed": True}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def official_call(endpoint, params, fields, token, timeout):
    _require(type(params) is dict and set(params) == {"trade_date", "ts_code"}, "SINGLE_STOCK_TRANSPORT_SCOPE_CHANGED")
    contract = request_contract(params["trade_date"], params["ts_code"])
    _require(endpoint == ENDPOINT and type(fields) in (list, tuple) and tuple(fields) == FIELDS
             and type(timeout) is int and timeout == TIMEOUT_SECONDS and type(token) is str,
             "SINGLE_STOCK_TRANSPORT_SCOPE_CHANGED")
    payload = {**contract, "fields": ",".join(FIELDS), "token": token}
    req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.build_opener(_NoRedirect()).open(req, timeout=TIMEOUT_SECONDS) as reply:
        raw = reply.read(MAX_BYTES + 1)
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "ORIGINAL_HTTP_BYTES_REQUIRED_WITHIN_4MB")
    return raw


def run_probe(output, *, token, call=None):
    _require(type(token) is str and (call is None or callable(call)), "INVALID_TRANSPORT_ARGUMENT")
    code_bindings = _guard()
    _require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "DIAGNOSTIC_RERUN_NOT_ALLOWED")
    _require(os.environ.get("GITHUB_REF", "refs/heads/main") == "refs/heads/main", "MAIN_BRANCH_REQUIRED")
    output = Path(output)
    _require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)), "FRESH_UNALIASED_OUTPUT_REQUIRED")
    output = output.resolve()
    _require(output != CHECKOUT and CHECKOUT not in output.parents and output not in CHECKOUT.parents,
             "DIAGNOSTIC_OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    output.mkdir(parents=True, exist_ok=False)
    transport, injected = official_call if call is None else call, call is not None
    results = []
    for day, stock in PAIRS:
        _require(_guard() == code_bindings, "SINGLE_STOCK_EXECUTION_BINDINGS_CHANGED")
        item = {"trade_date": day, "ts_code": stock, "request": request_contract(day, stock),
                "status": "CREDENTIAL_ABSENT", "api_calls": 0, "network_request_performed": False,
                "callable_injected_for_test": injected, "diagnostic": None, **FLAGS}
        if token.strip():
            item.update(status="REQUEST_STARTED", api_calls=1, network_request_performed=not injected)
            try:
                raw = transport(ENDPOINT, {"trade_date": day, "ts_code": stock}, FIELDS, token, TIMEOUT_SECONDS)
                item["diagnostic"] = diagnose_response(raw, day, stock, token=token)
                item["status"] = "DIAGNOSTIC_COMPLETE" if item["diagnostic"]["status"] != "SINGLE_STOCK_RESPONSE_NOT_PARSED" else "INVALID_RESPONSE_DIAGNOSTIC"
            except error.HTTPError as exc:
                item.update(status="HTTP_ERROR", reason="HTTP_" + str(exc.code)
                            if type(exc.code) is int and 100 <= exc.code <= 599 else "HTTP_ERROR")
            except Exception:
                item.update(status="NETWORK_OR_RESPONSE_ERROR", reason="NETWORK_OR_RESPONSE_ERROR")
        results.append(item)
        _require(_guard() == code_bindings, "SINGLE_STOCK_EXECUTION_BINDINGS_CHANGED")
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schema_version": SCHEMA, "status": "DIAGNOSTIC_COMPLETE" if all(r["status"] == "DIAGNOSTIC_COMPLETE" for r in results) else "DIAGNOSTIC_INCOMPLETE",
              "api_calls": sum(r["api_calls"] for r in results), "max_api_calls": MAX_CALLS, "retries": 0,
              "requests": results, "callable_injected_for_test": injected,
              "base_archive_sha256": BASE_ARCHIVE_SHA256, "execution_file_bindings": code_bindings,
              "verified_stocks_scope_failure": dict(VERIFIED_STK_FAILURE),
              "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
              "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None,
              "run_metadata_basis": "ENVIRONMENT_CLAIMS_REQUIRE_EXTERNAL_RUN_VERIFICATION",
              "independent_run_identity_verified": False, **FLAGS}
    raw_report = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    _require(not token or token.encode() not in raw_report, "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    _require(_guard() == code_bindings, "SINGLE_STOCK_EXECUTION_BINDINGS_CHANGED")
    target = output / OUTPUT_FILE
    _require(not target.exists() and not any(p.is_symlink() for p in (target, *target.parents)), "EXCLUSIVE_UNALIASED_OUTPUT_REQUIRED")
    with target.open("xb") as handle:
        handle.write(raw_report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
        print(json.dumps({key: report[key] for key in ("status", "api_calls", "max_api_calls")}))
        return 0 if report["status"] == "DIAGNOSTIC_COMPLETE" else 2
    except Exception:
        print("SINGLE_STOCK_PROBE_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
