"""Isolated STK-scoped auction SOURCE ONLY, with original request provenance.

Never strips ts_type to call an old HTTP adapter, constructs an old LoadedSource,
merges HTTP responses, prices an entry, writes files, or generates labels. A
caller must independently bind its actual network attempt; a boolean alone is
not evidence of a real request. Frozen v3 supplies only its existing validators.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from work.profit_1000_upgrade import auction_truth_v3 as codec

ROOT_PATH = "data/research/stocks_scoped_auction_v1"
SOURCE_POLICY_ID = "dc20_research_stocks_scoped_auction_source_20260912_v1"
SOURCE_SCHEMA = "dc20_research_stocks_scoped_auction_source_v1"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
MAX_BYTES, MAX_ROWS = 4_000_000, 8000
CODEC_SHA256 = "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665"
HTTP_ADAPTER_SHA256 = "289fbcca8eb1642af7ea4a4c51bd6a136faf3ce96c5fc4c4e3a9091a369fa89c"
HERE = Path(__file__).parent
STATUSES = frozenset({"STOCKS_SCOPED_TABLE_PRESENT", "STOCKS_SCOPED_TABLE_EMPTY"})
DETAIL_KINDS = frozenset({"ABSENT", "EMPTY_STRING", "ASCII_PLACEHOLDER"})
FLAGS = {"research_only": True, "source_only": True, "production_integrated": False,
         "production_activation_allowed": False, "production_writes": False,
         "label_source_eligible": False, "entry_source_eligible": False,
         "entry_price_qualification_performed": False, "capacity_qualification_performed": False,
         "training_performed": False, "settlement_performed": False, "fallback_generated": False,
         "actual_execution_claimed": False, "actual_capacity_verified": False,
         "known_before_0925": False, "price_reporting_precision_confirmed": False,
         "reported_price_preserved": True}
AuctionSourceError = codec.AuctionSourceError
_LOAD_KEY = object()


class StockSourceMissing(AuctionSourceError):
    """No source pair exists; this is not an observed empty table or fallback."""


def _guard():
    for name, expected in (("auction_truth_v3.py", CODEC_SHA256), ("auction_http_v3.py", HTTP_ADAPTER_SHA256)):
        path = HERE / name
        codec._fail(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                    and hashlib.sha256(path.read_bytes()).hexdigest() == expected, "PINNED_STOCKS_SOURCE_DEPENDENCY_CHANGED")
    codec._fail(codec.FIELDS == FIELDS and codec.MAX_BYTES == MAX_BYTES and codec.MAX_ROWS == MAX_ROWS,
                "FROZEN_STOCKS_VALIDATOR_CONSTANTS_CHANGED")


def request_contract(trade_date):
    return {"api_name": "stk_auction", "params": {"trade_date": codec._date(trade_date), "ts_type": "STK"},
            "fields": list(FIELDS)}


def _request(value, day):
    codec._fail(type(value) is dict and set(value) == {"api_name", "params", "fields"}, "INVALID_STOCKS_REQUEST_CONTRACT")
    params, fields = value.get("params"), value.get("fields")
    codec._fail(type(value["api_name"]) is str and value["api_name"] == "stk_auction"
                and type(params) is dict and set(params) == {"trade_date", "ts_type"}
                and type(params["trade_date"]) is str and params["trade_date"] == day
                and type(params["ts_type"]) is str and params["ts_type"] == "STK"
                and type(fields) is list and all(type(field) is str for field in fields)
                and fields == list(FIELDS), "STOCKS_REQUEST_SCOPE_OR_FIELDS_CHANGED")


def _table(data, day):
    codec._fail(type(data) is dict and data.get("has_more") is False and type(data.get("items")) is list
                and type(data.get("count")) is int and data["count"] in (0, len(data["items"])),
                "STOCKS_TABLE_REQUIRES_EXPLICIT_COMPLETE_PAGINATION")
    return codec.table_rows(data, day)


def _metadata_expected(day, body):
    return {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
            "source": "tushare:stk_auction", "trade_date": day, "security_type": "STK",
            "scope_basis": "EXPLICIT_ORIGINAL_REQUEST_TS_TYPE_STK", "request": request_contract(day),
            "network_request_performed": True, "immutable": True, "data_sha256": codec._sha(body),
            "price_unit": "CNY_PER_SHARE", "volume_unit": "SHARES", "amount_unit": "CNY",
            "event_semantics": "CANONICAL_OPENING_AUCTION_AVAILABLE_0926_0929",
            "credential_persisted": False, "server_messages_persisted": False,
            "full_response_envelope_retained": False, "diagnostic_table_imported": False,
            "ingestion": "ORIGINAL_STK_SCOPED_HTTP_RESPONSE_ONLY", "numeric_qualification_scope": "NOT_PERFORMED_SOURCE_ONLY",
            "frozen_v3_codec_sha256": CODEC_SHA256, "frozen_http_adapter_sha256": HTTP_ADAPTER_SHA256, **FLAGS}


def source_bytes(raw_response, trade_date, *, request, fetched_at_utc, network_request_performed, token=""):
    """Validate original HTTP bytes and return a new data/meta pair, no writes."""
    _guard()
    day = codec._date(trade_date)
    codec._fail(day >= codec.COVERAGE_START, "STOCKS_SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
    _request(request, day)
    old = codec._old()
    codec._legacy(lambda: old._fetched(fetched_at_utc, day))
    codec._fail(network_request_performed is True, "REAL_NETWORK_ATTEMPT_REQUIRED")
    codec._fail(type(token) is str, "INVALID_CREDENTIAL_ARGUMENT")
    codec._fail(type(raw_response) is bytes and 0 < len(raw_response) <= MAX_BYTES, "INVALID_STOCKS_HTTP_RESPONSE_BYTES")
    payload = codec._strict_json(raw_response)
    codec._fail(type(payload) is dict and type(payload.get("code")) is int
                and not set(payload) - {"code", "msg", "data", "request_id", "detail"}, "INVALID_STOCKS_API_ENVELOPE")
    if token:
        codec._fail(token.encode() not in raw_response, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
        codec._credential_guard(payload, token, persisted_values=False)
    codec._fail(payload["code"] == 0, "STOCKS_SOURCE_REQUIRES_SUCCESS_CODE_ZERO")
    codec._fail("detail" not in payload or (type(payload["detail"]) is str and payload["detail"] in ("", "...")),
                "INVALID_STOCKS_API_DETAIL")
    detail_kind = "ABSENT" if "detail" not in payload else "EMPTY_STRING" if payload["detail"] == "" else "ASCII_PLACEHOLDER"
    data = payload.get("data")
    rows = _table(data, day)
    body = codec._json(data)
    codec._fail(len(body) <= MAX_BYTES, "OVERSIZED_STOCKS_SOURCE")
    metadata = {**_metadata_expected(day, body), "fetched_at_utc": fetched_at_utc,
                "status": "STOCKS_SCOPED_TABLE_PRESENT" if rows else "STOCKS_SCOPED_TABLE_EMPTY",
                "api_code": 0, "rows": len(rows), "http_detail_kind": detail_kind,
                "http_response_sha256": codec._sha(raw_response), "http_response_bytes": len(raw_response)}
    meta_body = codec._json(metadata)
    codec._fail(not token or token.encode() not in body + meta_body, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
    _guard()
    return body, meta_body


def source_paths(root, trade_date):
    day = codec._date(trade_date)
    path = Path(root) / ROOT_PATH / day[:4] / day / "data.json"
    return path, path.with_name("meta.json")


@dataclass(frozen=True, init=False)
class LoadedStockSource:
    """Immutable source snapshot; only load() may create one (no label authority)."""
    trade_date: str
    status: str
    rows: Mapping
    source_files: tuple
    request: Mapping

    def __init__(self, trade_date, status, rows, source_files, request, *, _key=None):
        codec._fail(_key is _LOAD_KEY, "STOCKS_PRELOAD_MUST_BE_CREATED_BY_LOAD")
        immutable_request = MappingProxyType({"api_name": request["api_name"],
            "params": MappingProxyType(dict(request["params"])), "fields": tuple(request["fields"])})
        for key, value in (("trade_date", trade_date), ("status", status),
                           ("rows", MappingProxyType({code: MappingProxyType(dict(row)) for code, row in rows.items()})),
                           ("source_files", tuple(MappingProxyType(dict(item)) for item in source_files)),
                           ("request", immutable_request)):
            object.__setattr__(self, key, value)


def load(root, trade_date):
    """Read/validate one new namespace pair; missing never means no-trade."""
    _guard()
    day, old = codec._date(trade_date), codec._old()
    codec._fail(day >= codec.COVERAGE_START, "STOCKS_SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
    root = Path(root)
    codec._fail(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_STOCKS_SOURCE_ROOT")
    root = root.resolve(strict=True)
    paths = source_paths(root, day)
    for path in paths:
        codec._legacy(lambda path=path: old._safe_path(root, path))
    if not any(path.exists() for path in paths):
        raise StockSourceMissing("STOCKS_SCOPED_SOURCE_NOT_ATTEMPTED")
    codec._fail(all(path.is_file() for path in paths), "INCOMPLETE_STOCKS_SOURCE_PAIR")
    body, meta_body = (path.read_bytes() for path in paths)
    codec._fail(0 < len(body) <= MAX_BYTES and 0 < len(meta_body) <= 20_000, "OVERSIZED_STOCKS_SOURCE")
    data, meta = codec._strict_json(body), codec._strict_json(meta_body)
    codec._fail(type(meta) is dict, "INVALID_STOCKS_SOURCE_METADATA")
    codec._fail(codec._json(data) == body and codec._json(meta) == meta_body, "NONCANONICAL_STOCKS_SOURCE_REPRESENTATION")
    expected = _metadata_expected(day, body)
    codec._fail(set(meta) == set(expected) | {"fetched_at_utc", "status", "api_code", "rows", "http_detail_kind",
                                            "http_response_sha256", "http_response_bytes"}, "STOCKS_METADATA_FIELDS_DRIFTED")
    codec._fail(all(type(meta[key]) is type(value) and meta[key] == value for key, value in expected.items()),
                "STOCKS_SOURCE_METADATA_OR_SHA_MISMATCH")
    _request(meta["request"], day)
    codec._legacy(lambda: old._fetched(meta["fetched_at_utc"], day))
    codec._legacy(lambda: old._sha_value(meta["http_response_sha256"]))
    codec._fail(type(meta["http_response_bytes"]) is int and 0 < meta["http_response_bytes"] <= MAX_BYTES,
                "INVALID_STOCKS_RESPONSE_BYTE_COUNT")
    codec._fail(type(meta["status"]) is str and meta["status"] in STATUSES and type(meta["api_code"]) is int
                and meta["api_code"] == 0 and type(meta["rows"]) is int
                and type(meta["http_detail_kind"]) is str and meta["http_detail_kind"] in DETAIL_KINDS,
                "INVALID_STOCKS_SOURCE_STATUS")
    rows = _table(data, day)
    codec._fail(meta["rows"] == len(rows) and meta["status"] ==
                ("STOCKS_SCOPED_TABLE_PRESENT" if rows else "STOCKS_SCOPED_TABLE_EMPTY"), "STOCKS_SOURCE_STATUS_OR_ROW_COUNT_CONFLICT")
    bindings = [{"path": path.relative_to(root).as_posix(), "sha256": codec._sha(raw)}
                for path, raw in zip(paths, (body, meta_body))]
    # The returned snapshot is stable even if its original files later change;
    # the collector must recheck source_files before relying on that snapshot.
    _guard()
    return LoadedStockSource(day, meta["status"], rows, bindings, meta["request"], _key=_LOAD_KEY)


__all__ = ["request_contract", "source_paths", "source_bytes", "load", "LoadedStockSource",
           "AuctionSourceError", "StockSourceMissing", "ROOT_PATH", "SOURCE_POLICY_ID", "SOURCE_SCHEMA", "FIELDS"]
