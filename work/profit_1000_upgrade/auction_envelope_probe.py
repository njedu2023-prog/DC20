#!/usr/bin/env python3
"""One-date, one-request HTTP envelope SHAPE diagnostic; never source truth.

Only safe key/field names, JSON types, derived row counts, and complete-response
hash/length are retained. No message, request-id value, market row, or other
response value is saved. The pinned v3 envelope allowlist is not relaxed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
DAY = "20250116"
ENDPOINT = "stk_auction"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
MAX_BYTES = 4_000_000
TIMEOUT_SECONDS = 20
V3_CODEC_SHA256 = "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665"
V3_ALLOWED_KEYS = frozenset({"code", "msg", "data", "request_id"})
SCHEMA = "dc20_auction_envelope_shape_probe_20260912_v1"
FLAGS = {"diagnostic_only": True, "research_only": True,
         "source_import_allowed": False, "source_files_written": False,
         "label_source_eligible": False, "entry_source_eligible": False,
         "training_performed": False, "settlement_performed": False,
         "fallback_generated": False, "production_writes": False,
         "codec_allowlist_modified": False, "source_values_modified": False,
         "raw_response_saved": False, "market_table_saved": False,
         "response_values_saved": False, "server_message_saved": False,
         "request_id_value_saved": False, "credential_persisted": False,
         "production_activation_allowed": False}
_IMPLEMENTATIONS = (Path(__file__), HERE / "auction_truth_v3.py", HERE / "AUCTION_ENVELOPE_PROBE.json")
_SENSITIVE = re.compile(r"token|secret|password|passwd|credential|auth|bearer|cookie|session|jwt|signature|private_?key|api_?key|access_?key", re.I)


class ProbeError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise ProbeError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "REGULAR_UNALIASED_CODE_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_HASHES = {path: _file_sha(path) for path in _IMPLEMENTATIONS}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "canonical_one_date_envelope_shape_20260912_v1",
            "endpoint": ENDPOINT, "params": {"trade_date": DAY}, "fields": list(FIELDS),
            "max_api_calls": 1, "retries": 0, "timeout_seconds": TIMEOUT_SECONDS,
            "max_http_response_bytes": MAX_BYTES, "v3_codec_sha256": V3_CODEC_SHA256,
            "v3_allowed_top_level_keys": sorted(V3_ALLOWED_KEYS),
            "safe_name_format": "ASCII_IDENTIFIER_MAX32_REDACT_CREDENTIAL_LIKE",
            "response_persistence": "STRUCTURE_ONLY_NO_VALUES", **FLAGS}


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def finite(value):
        result = float(value)
        _require(math.isfinite(result), "NONFINITE_JSON_NUMBER")
        return result
    def invalid(_):
        raise ProbeError("NONFINITE_JSON_NUMBER")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=finite, parse_constant=invalid)
    except ProbeError:
        raise
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise ProbeError("INVALID_RESPONSE_JSON") from None


def _guard():
    _require(_file_sha(HERE / "auction_truth_v3.py") == V3_CODEC_SHA256, "PINNED_V3_CODEC_CHANGED")
    for path, digest in _IMPORTED_HASHES.items():
        _require(_file_sha(path) == digest, "ENVELOPE_PROBE_CODE_CHANGED_SINCE_IMPORT")
    value = _parse((HERE / "AUCTION_ENVELOPE_PROBE.json").read_bytes())
    _require(value == expected_contract(), "ENVELOPE_PROBE_REQUEST_CHANGED")
    return value


def _safe_name(value, token):
    if (type(value) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", value)
            or _SENSITIVE.search(value) or token and token in value
            or re.fullmatch(r"[A-Fa-f0-9]{16,}", value)
            or re.fullmatch(r"[A-Za-z0-9_-]{24,}", value)):
        return "REDACTED"
    return value


def _type(value):
    return {dict: "object", list: "array", str: "string", int: "integer",
            float: "number", bool: "boolean", type(None): "null"}.get(type(value), "unknown")


def diagnose_response(raw, *, token=""):
    """Pure shape-only diagnostic on original bytes; does not claim a request."""
    result = {"schema_version": SCHEMA, "trade_date": DAY, "endpoint": ENDPOINT,
              "status": "UNPARSED", "http_response_sha256": None, "http_response_bytes": None,
              "response_body_complete": False, "diagnostic_reason": None,
              "envelope_only_not_full_codec_acceptance": True, **FLAGS}
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        return {**result, "status": "INVALID_BOUNDED_RESPONSE", "diagnostic_reason": "ORIGINAL_HTTP_BYTES_REQUIRED_WITHIN_4MB"}
    result.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw), response_body_complete=True)
    try:
        payload = _parse(raw)
    except ProbeError as exc:
        return {**result, "status": "INVALID_RESPONSE_JSON", "diagnostic_reason": str(exc)}
    is_object = type(payload) is dict
    fields = [{"name": _safe_name(key, token), "type": _type(value)}
              for key, value in sorted(payload.items())] if is_object else []
    code_present = is_object and "code" in payload
    code_int = code_present and type(payload["code"]) is int
    key_gate = is_object and not set(payload) - V3_ALLOWED_KEYS
    code_zero = code_int and payload["code"] == 0
    data = payload.get("data") if is_object else None
    table = {"data_type": _type(data) if is_object and "data" in payload else "missing",
             "fields_type": _type(data.get("fields")) if type(data) is dict and "fields" in data else "missing",
             "items_type": _type(data.get("items")) if type(data) is dict and "items" in data else "missing",
             "field_names": [], "field_count": None, "row_count": None}
    if type(data) is dict:
        if type(data.get("fields")) is list:
            table["field_names"] = [_safe_name(field, token) for field in data["fields"]]
            table["field_count"] = len(data["fields"])
        if type(data.get("items")) is list:
            table["row_count"] = len(data["items"])
    # Derived comparisons help identify a provider envelope contract without
    # persisting unknown values or treating an extra key as pagination truth.
    extra = [{"name": _safe_name(key, token), "type": _type(value),
              "is_null": value is None,
              "is_boolean_false": value is False if type(value) is bool else None,
              "is_integer_zero": value == 0 if type(value) is int else None,
              "integer_equals_data_row_count": value == table["row_count"]
                  if type(value) is int and type(table["row_count"]) is int else None}
             for key, value in sorted(payload.items()) if key not in V3_ALLOWED_KEYS] if is_object else []
    result.update(status="DIAGNOSTIC_RESPONSE_SHAPE_CAPTURED", top_level_type=_type(payload),
                  top_level_field_count=len(fields), top_level_fields=fields,
                  extra_top_level_fields=extra, extra_top_level_field_count=len(extra),
                  redacted_top_level_names=sum(item["name"] == "REDACTED" for item in fields),
                  code_present=code_present, code_type=_type(payload["code"]) if code_present else "missing",
                  code_is_exact_integer=code_int, code_is_integer_zero=code_zero,
                  extra_field_flags_are_diagnostics_not_pagination_authority=True,
                  v3_envelope_checks={"json_object": is_object, "code_exact_int": code_int,
                                      "top_level_key_allowlist_pass": key_gate,
                                      "combined_gate_pass": is_object and code_int and key_gate},
                  table_shape=table)
    return result


def official_call(endpoint, params, fields, token, timeout):
    _require(endpoint == ENDPOINT and params == {"trade_date": DAY} and tuple(fields) == FIELDS
             and timeout == TIMEOUT_SECONDS, "PROBE_REQUEST_SCOPE_CHANGED")
    body = json.dumps({"api_name": endpoint, "params": params, "fields": ",".join(fields), "token": token}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, "HTTP_RESPONSE_EXCEEDS_4MB")
    return raw


def run_probe(output, *, token, call=None):
    _require(type(token) is str, "CREDENTIAL_ARGUMENT_TYPE_INVALID")
    contract = _guard()
    output = Path(output)
    _require(not any(path.is_symlink() for path in (output, *output.parents)) and not output.exists(),
             "FRESH_UNALIASED_DIAGNOSTIC_OUTPUT_REQUIRED")
    output = output.resolve()
    _require(output != CHECKOUT and CHECKOUT not in output.parents and output not in CHECKOUT.parents,
             "DIAGNOSTIC_OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    output.mkdir(parents=True, exist_ok=False)
    injected = call is not None
    transport = official_call if call is None else call
    _require(callable(transport), "CALLABLE_TRANSPORT_REQUIRED")
    envelope = {"status": "CREDENTIAL_ABSENT", "api_calls": 0, "retries": 0,
                "network_request_performed": False, "callable_injected_for_test": injected,
                "request": {"api_name": ENDPOINT, "params": {"trade_date": DAY}, "fields": list(FIELDS)},
                "max_http_response_bytes": MAX_BYTES, "timeout_seconds": TIMEOUT_SECONDS,
                "diagnostic": None, **FLAGS}
    if token.strip():
        envelope.update(status="REQUEST_STARTED", api_calls=1, network_request_performed=not injected)
        try:
            raw = transport(ENDPOINT, {"trade_date": DAY}, FIELDS, token, TIMEOUT_SECONDS)
            envelope["diagnostic"] = diagnose_response(raw, token=token)
            envelope["status"] = "DIAGNOSTIC_COMPLETE"
        except error.HTTPError as exc:
            envelope.update(status="HTTP_ERROR", http_status=exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None)
        except ProbeError as exc:
            envelope.update(status="REQUEST_REJECTED", reason=str(exc) if str(exc) in {"HTTP_RESPONSE_EXCEEDS_4MB", "PROBE_REQUEST_SCOPE_CHANGED"} else "SAFE_PROBE_REJECTION")
        except Exception:
            envelope.update(status="NETWORK_OR_RESPONSE_ERROR", reason="NETWORK_OR_RESPONSE_ERROR")
    _guard()
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    envelope.update(schema_version=SCHEMA, trade_date=DAY, observed_at_utc=datetime.now(timezone.utc).isoformat(),
                    run_id=run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
                    run_commit=commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None,
                    request_contract_sha256=_IMPORTED_HASHES[HERE / "AUCTION_ENVELOPE_PROBE.json"],
                    v3_codec_sha256=V3_CODEC_SHA256,
                    execution_file_bindings={p.relative_to(CHECKOUT).as_posix(): sha for p, sha in _IMPORTED_HASHES.items()})
    raw_report = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    _require(not token or token.encode() not in raw_report, "CREDENTIAL_LIKE_REPORT_VALUE_FORBIDDEN")
    target = output / "auction_envelope_probe.json"
    _require(not any(p.is_symlink() for p in (target, *target.parents)) and not target.exists(), "DIAGNOSTIC_TARGET_CHANGED")
    with target.open("xb") as handle:
        handle.write(raw_report)
    return envelope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps({"status": result["status"], "api_calls": result["api_calls"],
                      "response_values_saved": False, "source_import_allowed": False}, sort_keys=True))
    return 0 if result["status"] == "DIAGNOSTIC_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
