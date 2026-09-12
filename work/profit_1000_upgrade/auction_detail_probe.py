#!/usr/bin/env python3
"""Two bounded HTTP detail diagnostics; never qualified market source truth.

Only a short, credential-screened plain-text ``detail`` may be saved alongside
the original shape-only diagnostic. No original envelope/table/message/id is
persisted. A retained detail is evidence to interpret, not permission to ignore
it, accept a source, create labels, train a model, or activate production.
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
import unicodedata
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import auction_envelope_probe as shape

DATES = ("20250102", "20250116")
ENDPOINT = "stk_auction"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
MAX_BYTES, TIMEOUT_SECONDS, MAX_DETAIL_CHARS = 4_000_000, 20, 200
SHAPE_HELPER_SHA256 = "a61919ad87a098bb60b3d494eda699c2d075f1ca1ad9ab9080c9b000e8a3a929"
V3_CODEC_SHA256 = "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665"
V2_CODEC_SHA256 = "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af"
SCHEMA = "dc20_auction_sanitized_detail_probe_20260912_v1"
SCOPE = "ONLY_SANITIZED_DETAIL_DIAGNOSTIC_NOT_SOURCE"
FLAGS = {"diagnostic_only": True, "research_only": True,
         "diagnostic_scope": SCOPE, "sanitized_detail_text_may_be_saved": True,
         "source_import_allowed": False, "source_files_written": False,
         "label_source_eligible": False, "entry_source_eligible": False,
         "training_performed": False, "settlement_performed": False,
         "fallback_generated": False, "production_writes": False,
         "codec_allowlist_modified": False, "source_values_modified": False,
         "raw_response_saved": False, "market_table_saved": False,
         "other_response_values_saved": False, "server_message_saved": False,
         "request_id_value_saved": False, "credential_persisted": False,
         "detail_is_source_qualification": False, "detail_may_be_ignored": False,
         "production_activation_allowed": False}
_IMPLEMENTATIONS = tuple(HERE / name for name in (
    "auction_detail_probe.py", "AUCTION_DETAIL_PROBE.json", "auction_envelope_probe.py",
    "AUCTION_ENVELOPE_PROBE.json", "auction_truth_v3.py", "auction_truth.py"))
_SENSITIVE = re.compile(
    r"token|secret|password|passwd|credential|auth|bearer|cookie|session|jwt|signature|"
    r"private[\s_-]*key|api[\s_-]*key|access[\s_-]*key|密钥|凭证|令牌|口令|密码|认证", re.I)
_OPAQUE = re.compile(r"[A-Za-z0-9_./+=-]{24,}|[A-Fa-f0-9]{16,}")
_LONG_NUMBER = re.compile(r"\d{8,}")
_PLAIN = re.compile(r"[\x20-\x7e\u4e00-\u9fff，。；：！？、（）【】《》“”‘’…·]*")


class DetailProbeError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise DetailProbeError(reason)


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
             "REGULAR_UNALIASED_CODE_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_HASHES = {path: _file_sha(path) for path in _IMPLEMENTATIONS}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "canonical_two_date_sanitized_detail_20260912_v1",
            "endpoint": ENDPOINT, "trade_dates": list(DATES), "fields": list(FIELDS),
            "max_api_calls": 2, "calls_per_date": 1, "retries": 0,
            "timeout_seconds": TIMEOUT_SECONDS, "max_http_response_bytes": MAX_BYTES,
            "shape_helper_sha256": SHAPE_HELPER_SHA256, "v3_codec_sha256": V3_CODEC_SHA256,
            "v2_codec_sha256": V2_CODEC_SHA256, "detail_max_characters": MAX_DETAIL_CHARS,
            "detail_policy": "EXACT_STR_PLAIN_TEXT_MAX200_NO_CREDENTIAL_OPAQUE24_HEX16_OR_DIGITS8_ELSE_REDACTED",
            "detail_encoding_policy": "REJECT_BACKSLASH_PERCENT_AMPERSAND_WITHOUT_DECODING",
            "detail_normalization": "NFKC_FOR_SCREENING_ONLY_ORIGINAL_SAFE_TEXT_RETAINED",
            "response_persistence": "SHAPE_PLUS_ONLY_SANITIZED_DETAIL_NO_OTHER_VALUES", **FLAGS}


def _guard():
    for name, expected in (("auction_envelope_probe.py", SHAPE_HELPER_SHA256),
                           ("auction_truth_v3.py", V3_CODEC_SHA256), ("auction_truth.py", V2_CODEC_SHA256)):
        _require(_file_sha(HERE / name) == expected, "PINNED_DIAGNOSTIC_DEPENDENCY_CHANGED")
    for path, expected in _IMPORTED_HASHES.items():
        _require(_file_sha(path) == expected, "DETAIL_PROBE_CODE_CHANGED_SINCE_IMPORT")
    contract = shape._parse((HERE / "AUCTION_DETAIL_PROBE.json").read_bytes())
    _require(contract == expected_contract(), "DETAIL_PROBE_REQUEST_CHANGED")
    return contract


def _safe_detail(value, *, present, token):
    """Retain only explicitly screened exact strings, never other JSON values."""
    kind = shape._type(value) if present else "missing"
    is_empty = present and type(value) in (str, dict, list) and len(value) == 0
    result = {"present": present, "json_type": kind,
              "char_length": len(value) if present and type(value) is str else None,
              "is_null": present and value is None, "is_empty": is_empty,
              "is_empty_string": present and type(value) is str and value == "",
              "is_empty_object": present and type(value) is dict and not value,
              "is_empty_array": present and type(value) is list and not value,
              "text_retained": False, "sanitized_text": "REDACTED",
              "redaction_reason": "ABSENT" if not present else "NON_STRING_VALUE"}
    if not present or type(value) is not str:
        return result
    if len(value) > MAX_DETAIL_CHARS:
        return {**result, "redaction_reason": "TEXT_OVER_200_CHARACTERS"}
    normalized = unicodedata.normalize("NFKC", value)
    normalized_token = unicodedata.normalize("NFKC", token)
    if token and (token in value or normalized_token in normalized):
        return {**result, "redaction_reason": "KNOWN_CREDENTIAL_MATCH"}
    # Diagnostic text has no need for encoded values. Reject their introducers
    # rather than decoding (or persisting) potentially transformed credentials.
    if any(ch in normalized for ch in ("\\", "%", "&")):
        return {**result, "redaction_reason": "ENCODED_OR_ESCAPED_TEXT_FORBIDDEN"}
    if _SENSITIVE.search(normalized):
        return {**result, "redaction_reason": "CREDENTIAL_LIKE_TEXT"}
    if _OPAQUE.search(normalized) or _LONG_NUMBER.search(normalized):
        return {**result, "redaction_reason": "OPAQUE_OR_LONG_NUMERIC_TEXT"}
    if (not _PLAIN.fullmatch(value) or any(unicodedata.category(ch).startswith("C") for ch in value)
            or re.search(r"://|www\.|@", normalized, re.I)):
        return {**result, "redaction_reason": "NOT_SAFE_PLAIN_DIAGNOSTIC_TEXT"}
    return {**result, "text_retained": True, "sanitized_text": value, "redaction_reason": None}


def diagnose_response(raw, trade_date, *, token=""):
    """Pure bounded diagnostic; the helper's hardcoded old day is not reused."""
    _require(type(trade_date) is str and trade_date in DATES, "DETAIL_PROBE_DATE_OUT_OF_SCOPE")
    _require(type(token) is str, "CREDENTIAL_ARGUMENT_TYPE_INVALID")
    # Only this pure helper is reused. Its one-day runner/guard/transport are not.
    observed_shape = shape.diagnose_response(raw, token=token)
    observed_shape = {**observed_shape, "trade_date": trade_date}
    result = {"schema_version": SCHEMA, "trade_date": trade_date,
              "status": "DETAIL_NOT_PARSED", "envelope_shape": observed_shape,
              "detail": None, **FLAGS}
    if observed_shape["status"] != "DIAGNOSTIC_RESPONSE_SHAPE_CAPTURED":
        return result
    parsed = shape._parse(raw)
    present = type(parsed) is dict and "detail" in parsed
    value = parsed["detail"] if present else None
    return {**result, "status": "SANITIZED_DETAIL_DIAGNOSTIC_CAPTURED",
            "detail": _safe_detail(value, present=present, token=token)}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def official_call(endpoint, params, fields, token, timeout):
    _require(endpoint == ENDPOINT and type(params) is dict and set(params) == {"trade_date"}
             and type(params["trade_date"]) is str and params["trade_date"] in DATES
             and tuple(fields) == FIELDS and type(timeout) is int and timeout == TIMEOUT_SECONDS
             and type(token) is str, "DETAIL_PROBE_REQUEST_SCOPE_CHANGED")
    body = json.dumps({"api_name": ENDPOINT, "params": params,
                       "fields": ",".join(FIELDS), "token": token}).encode()
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
                "timeout_seconds": TIMEOUT_SECONDS, "max_http_response_bytes": MAX_BYTES,
                "diagnostic": None, **FLAGS}
        if token.strip():
            item.update(status="REQUEST_STARTED", api_calls=1, network_request_performed=not injected)
            try:
                raw = transport(ENDPOINT, {"trade_date": day}, FIELDS, token, TIMEOUT_SECONDS)
                item["diagnostic"] = diagnose_response(raw, day, token=token)
                item["status"] = ("DIAGNOSTIC_COMPLETE" if item["diagnostic"]["status"] == "SANITIZED_DETAIL_DIAGNOSTIC_CAPTURED"
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
              "trade_dates": list(DATES), "requests": results, "callable_injected_for_test": injected,
              "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
              "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None,
              "request_contract_sha256": _IMPORTED_HASHES[HERE / "AUCTION_DETAIL_PROBE.json"],
              "execution_file_bindings": {p.relative_to(CHECKOUT).as_posix(): digest for p, digest in _IMPORTED_HASHES.items()},
              **FLAGS}
    serialized = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    _require(not token or token.encode() not in serialized, "CREDENTIAL_LIKE_REPORT_VALUE_FORBIDDEN")
    target = output / "auction_detail_probe.json"
    _require(not target.exists() and not any(p.is_symlink() for p in (target, *target.parents)),
             "DIAGNOSTIC_TARGET_CHANGED")
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
