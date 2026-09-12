"""Narrow original-HTTP envelope adapter for the frozen v3 auction codec.

Only an optional ``detail`` whose exact type is str and value is "" is newly
accepted. Nonempty detail is unknown evidence, not a message to discard. The
ORIGINAL response bytes are parsed directly and bound by SHA/length; no keys
are deleted and no rewritten envelope is passed to the frozen codec. The
existing data/metadata schema, candidate qualification and source policy are
unchanged. This module performs no network calls or source-file writes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from work.profit_1000_upgrade import auction_truth_v3 as codec

ADAPTER_ID = "dc20_canonical_http_empty_detail_v1"
CODEC_SHA256 = "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665"
CODEC_PATH = Path(__file__).with_name("auction_truth_v3.py")


def _verify_codec():
    codec._fail(not any(path.is_symlink() for path in (CODEC_PATH, *CODEC_PATH.parents))
                and CODEC_PATH.is_file()
                and hashlib.sha256(CODEC_PATH.read_bytes()).hexdigest() == CODEC_SHA256,
                "PINNED_V3_CODEC_CHANGED")


def source_bytes(raw_response, trade_date, *, request, fetched_at_utc,
                 network_request_performed, token=""):
    """Return the frozen v3 source pair, bound to the unmodified HTTP bytes.

    A caller-supplied network flag is not proof of a real request. The research
    collector must continue to bind the actual request/response independently.
    No adapter field is injected into metadata: v3 load checks its exact schema.
    """
    _verify_codec()
    old = codec._old()
    day = codec._date(trade_date)
    requested_code = codec._legacy(lambda: old._request(request, day))
    codec._legacy(lambda: old._fetched(fetched_at_utc, day))
    codec._fail(network_request_performed is True, "REAL_NETWORK_ATTEMPT_REQUIRED")
    codec._fail(isinstance(token, str), "INVALID_CREDENTIAL_ARGUMENT")
    codec._fail(isinstance(raw_response, bytes) and 0 < len(raw_response) <= codec.MAX_BYTES,
                "INVALID_HTTP_RESPONSE_BYTES")
    payload = codec._strict_json(raw_response)
    codec._fail(isinstance(payload, dict) and type(payload.get("code")) is int
                and not set(payload) - {"code", "msg", "data", "request_id", "detail"},
                "INVALID_API_ENVELOPE_OR_DIAGNOSTIC_INPUT")
    codec._fail("detail" not in payload or (type(payload["detail"]) is str and payload["detail"] == ""),
                "NONEMPTY_OR_INVALID_API_DETAIL")
    # Keep exact-token detection over all original bytes and parsed envelope,
    # including escaped strings; persisted-table guards remain in table_rows.
    if token:
        codec._fail(token.encode() not in raw_response, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
        codec._credential_guard(payload, token, persisted_values=False)
    if payload["code"] == 0:
        data = payload.get("data")
        rows = codec.table_rows(data, day, requested_code)
        codec._fail(not rows or day >= codec.COVERAGE_START, "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
        status = "CANONICAL_TABLE_PRESENT" if rows else "CANONICAL_TABLE_EMPTY"
    else:
        message = str(payload.get("msg", "")).lower()
        codec._fail(not any(word in message for word in ("token", "凭证", "credential", "频率", "每分钟", "rate limit")),
                    "OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE")
        codec._fail(any(word in message for word in ("权限", "permission", "授权", "积分", "entitlement")),
                    "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE")
        codec._fail(payload.get("data") is None, "API_FAILURE_WITH_DATA_CONFLICT")
        data, rows, status = None, {}, "ENTITLEMENT_DENIED"
    body = codec._json(data)
    codec._fail(len(body) <= codec.MAX_BYTES, "OVERSIZED_CANONICAL_SOURCE")
    metadata = {**codec._metadata_expected(day, body), "request": request, "fetched_at_utc": fetched_at_utc,
                "status": status, "api_code": payload["code"], "rows": len(rows),
                "http_response_sha256": codec._sha(raw_response), "http_response_bytes": len(raw_response)}
    meta_body = codec._json(metadata)
    codec._fail(not token or token.encode() not in body + meta_body, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
    _verify_codec()
    return body, meta_body


__all__ = ["source_bytes", "ADAPTER_ID", "CODEC_SHA256"]
