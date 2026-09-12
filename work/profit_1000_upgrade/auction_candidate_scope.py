"""Single-day/single-candidate original auction HTTP SOURCE ONLY.

Reuses frozen structural validators, never STK metadata, a prior LoadedSource,
entry qualification, entitlement fallback, network or file writers. A caller
must bind its actual request/response separately; a True argument alone cannot
prove HTTP occurred. Diagnostic/synthetic tables have no import authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from work.profit_1000_upgrade import auction_truth_v3 as codec

ROOT_PATH = "data/research/candidate_auction_v1"
SOURCE_POLICY_ID = "dc20_research_candidate_auction_source_20260913_v1"
SOURCE_SCHEMA = "dc20_research_candidate_auction_source_v1"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
COVERAGE_START, MAX_BYTES, MAX_ROWS, MAX_META_BYTES = "20250101", 4_000_000, 1, 20_000
PINNED_DEPENDENCIES = {
    "auction_truth_v3.py": "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665",
    "auction_truth.py": "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af",
    "auction_http_v3.py": "289fbcca8eb1642af7ea4a4c51bd6a136faf3ce96c5fc4c4e3a9091a369fa89c"}
HERE = Path(__file__).parent
STATUSES = frozenset({"CANDIDATE_TABLE_PRESENT", "CANDIDATE_TABLE_EMPTY"})
DETAIL_KINDS = frozenset({"ABSENT", "EMPTY_STRING", "ASCII_PLACEHOLDER"})
FLAGS = {"research_only": True, "source_only": True, "entry_source_eligible": False,
         "label_source_eligible": False, "entry_price_qualification_performed": False,
         "capacity_qualification_performed": False, "actual_execution_claimed": False,
         "actual_capacity_verified": False, "training_performed": False,
         "settlement_performed": False, "fallback_generated": False, "production_writes": False,
         "production_activation_allowed": False, "known_before_0925": False,
         "price_reporting_precision_confirmed": False, "reported_price_preserved": True,
         "credential_persisted": False, "full_response_envelope_retained": False,
         "server_messages_persisted": False, "diagnostic_table_imported": False}
AuctionSourceError = codec.AuctionSourceError
_LOAD_KEY = object()


class CandidateSourceMissing(AuctionSourceError):
    """No observed pair exists; not an empty response, no-trade or fallback."""


def _guard():
    for name, digest in PINNED_DEPENDENCIES.items():
        path = HERE / name
        codec._fail(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                    and codec._sha(path.read_bytes()) == digest, "PINNED_CANDIDATE_SOURCE_DEPENDENCY_CHANGED")
    codec._fail(codec.FIELDS == FIELDS and codec.MAX_BYTES == MAX_BYTES and codec.COVERAGE_START == COVERAGE_START,
                "FROZEN_CANDIDATE_VALIDATOR_CONSTANTS_CHANGED")


def _identity(trade_date, ts_code):
    codec._fail(type(trade_date) is str and re.fullmatch(r"[0-9]{8}", trade_date) is not None,
                "INVALID_TRADE_DATE")
    codec._fail(type(ts_code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", ts_code) is not None,
                "INVALID_STOCK_CODE")
    day, code = codec._date(trade_date), codec._code(ts_code)
    codec._fail(day >= COVERAGE_START, "CANDIDATE_SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
    return day, code


def request_contract(trade_date, ts_code):
    day, code = _identity(trade_date, ts_code)
    return {"api_name": "stk_auction", "params": {"trade_date": day, "ts_code": code}, "fields": list(FIELDS)}


def _request(value, day, code):
    codec._fail(type(value) is dict and set(value) == {"api_name", "params", "fields"}
                and type(value["api_name"]) is str and type(value["params"]) is dict
                and all(type(item) is str for item in value["params"].values())
                and type(value["fields"]) is list and all(type(item) is str for item in value["fields"])
                and value == request_contract(day, code), "CANDIDATE_REQUEST_CONTRACT_CHANGED")


def source_paths(root, trade_date, ts_code):
    day, code = _identity(trade_date, ts_code)
    directory = Path(root) / ROOT_PATH / day[:4] / day / code
    return directory / "data.json", directory / "meta.json"


def _table(data, day, code):
    codec._fail(type(data) is dict and type(data.get("items")) is list and data.get("has_more") is False
                and type(data.get("count")) is int and data["count"] in (0, len(data["items"])),
                "CANDIDATE_TABLE_REQUIRES_EXPLICIT_COMPLETENESS")
    return codec.table_rows(data, day, requested_code=code)


def _metadata(day, code, body):
    return {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
            "source": "tushare:stk_auction", "trade_date": day, "ts_code": code,
            "request": request_contract(day, code), "network_request_performed": True, "immutable": True,
            "ingestion": "ORIGINAL_SINGLE_STOCK_HTTP_RESPONSE_ONLY", "data_sha256": codec._sha(body),
            "price_unit": "CNY_PER_SHARE", "volume_unit": "SHARES", "amount_unit": "CNY",
            "event_semantics": "CANONICAL_OPENING_AUCTION_AVAILABLE_0926_0929",
            "frozen_dependencies": dict(PINNED_DEPENDENCIES), **FLAGS}


def _fetched(value, day):
    codec._fail(type(value) is str and len(value) <= 64, "INVALID_FETCH_TIMESTAMP")
    old = codec._old()
    codec._legacy(lambda: old._fetched(value, day))


def source_bytes(raw_response, trade_date, ts_code, *, request, fetched_at_utc,
                 network_request_performed, token=""):
    """Return canonical ORIGINAL data object and candidate-specific metadata, no I/O writes."""
    _guard()
    day, code = _identity(trade_date, ts_code)
    _request(request, day, code)
    _fetched(fetched_at_utc, day)
    codec._fail(network_request_performed is True, "REAL_NETWORK_ATTEMPT_REQUIRED")
    codec._fail(type(token) is str, "INVALID_CREDENTIAL_ARGUMENT")
    codec._fail(type(raw_response) is bytes and 0 < len(raw_response) <= MAX_BYTES, "INVALID_CANDIDATE_HTTP_RESPONSE_BYTES")
    payload = codec._strict_json(raw_response)
    codec._fail(type(payload) is dict and not set(payload) - {"code", "msg", "data", "request_id", "detail"},
                "INVALID_CANDIDATE_API_ENVELOPE")
    if token:
        codec._fail(token.encode() not in raw_response, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
        codec._credential_guard(payload, token, persisted_values=False)
    codec._fail(type(payload.get("code")) is int and payload["code"] == 0, "CANDIDATE_SOURCE_REQUIRES_SUCCESS_CODE_ZERO")
    codec._fail("detail" not in payload or type(payload["detail"]) is str and payload["detail"] in ("", "..."),
                "INVALID_CANDIDATE_API_DETAIL")
    data = payload.get("data")
    rows, body = _table(data, day, code), codec._json(data)
    codec._fail(len(body) <= MAX_BYTES, "OVERSIZED_CANDIDATE_SOURCE")
    meta = {**_metadata(day, code, body), "fetched_at_utc": fetched_at_utc,
            "status": "CANDIDATE_TABLE_PRESENT" if rows else "CANDIDATE_TABLE_EMPTY", "rows": len(rows),
            "api_code": 0, "http_response_sha256": codec._sha(raw_response), "http_response_bytes": len(raw_response),
            "http_detail_kind": "ABSENT" if "detail" not in payload else "EMPTY_STRING" if payload["detail"] == "" else "ASCII_PLACEHOLDER"}
    meta_body = codec._json(meta)
    codec._fail(not token or token.encode() not in body + meta_body, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
    _guard()
    return body, meta_body


@dataclass(frozen=True, init=False)
class LoadedCandidateSource:
    """Immutable structural snapshot, no price or label authority; load() only."""
    trade_date: str
    ts_code: str
    status: str
    rows: Mapping
    source_files: tuple
    request: Mapping

    def __init__(self, trade_date, ts_code, status, rows, bindings, request, *, _key=None):
        codec._fail(_key is _LOAD_KEY, "CANDIDATE_PRELOAD_MUST_BE_CREATED_BY_LOAD")
        request = MappingProxyType({"api_name": request["api_name"], "params": MappingProxyType(dict(request["params"])),
                                    "fields": tuple(request["fields"])})
        for name, value in (("trade_date", trade_date), ("ts_code", ts_code), ("status", status), ("request", request),
                            ("rows", MappingProxyType({key: MappingProxyType(dict(row)) for key, row in rows.items()})),
                            ("source_files", tuple(MappingProxyType(dict(item)) for item in bindings))):
            object.__setattr__(self, name, value)


def _read_bounded(path, limit):
    codec._fail(path.is_file() and 0 < path.stat().st_size <= limit, "INVALID_CANDIDATE_SOURCE_FILE_SIZE")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    codec._fail(0 < len(raw) <= limit, "INVALID_CANDIDATE_SOURCE_FILE_SIZE")
    return raw


def load(root, trade_date, ts_code):
    """Read/fully validate one immutable pair; caller rechecks source_files at handoff."""
    _guard()
    day, code = _identity(trade_date, ts_code)
    root = Path(root)
    codec._fail(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_CANDIDATE_SOURCE_ROOT")
    root = root.resolve(strict=True)
    paths, old = source_paths(root, day, code), codec._old()
    for path in paths:
        codec._legacy(lambda path=path: old._safe_path(root, path))
    if not any(path.exists() for path in paths):
        raise CandidateSourceMissing("CANDIDATE_SOURCE_NOT_ATTEMPTED")
    body, meta_body = (_read_bounded(path, cap) for path, cap in zip(paths, (MAX_BYTES, MAX_META_BYTES)))
    data, meta = codec._strict_json(body), codec._strict_json(meta_body)
    codec._fail(type(meta) is dict and codec._json(data) == body and codec._json(meta) == meta_body,
                "NONCANONICAL_CANDIDATE_SOURCE")
    expected = _metadata(day, code, body)
    codec._fail(set(meta) == set(expected) | {"fetched_at_utc", "status", "rows", "api_code", "http_response_sha256",
                                            "http_response_bytes", "http_detail_kind"}, "CANDIDATE_METADATA_FIELDS_CHANGED")
    codec._fail(all(type(meta[key]) is type(value) and meta[key] == value for key, value in expected.items()),
                "CANDIDATE_METADATA_OR_SHA_MISMATCH")
    _request(meta["request"], day, code)
    _fetched(meta["fetched_at_utc"], day)
    codec._legacy(lambda: old._sha_value(meta["http_response_sha256"]))
    codec._fail(type(meta["api_code"]) is int and meta["api_code"] == 0 and type(meta["rows"]) is int
                and type(meta["http_response_bytes"]) is int and 0 < meta["http_response_bytes"] <= MAX_BYTES
                and type(meta["http_detail_kind"]) is str and meta["http_detail_kind"] in DETAIL_KINDS,
                "INVALID_CANDIDATE_RESPONSE_METADATA")
    rows = _table(data, day, code)
    codec._fail(type(meta["status"]) is str and meta["status"] == ("CANDIDATE_TABLE_PRESENT" if rows else "CANDIDATE_TABLE_EMPTY")
                and meta["rows"] == len(rows), "CANDIDATE_STATUS_OR_ROW_COUNT_CONFLICT")
    bindings = [{"path": path.relative_to(root).as_posix(), "sha256": codec._sha(raw)} for path, raw in zip(paths, (body, meta_body))]
    _guard()
    return LoadedCandidateSource(day, code, meta["status"], rows, bindings, meta["request"], _key=_LOAD_KEY)
