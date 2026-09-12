"""Isolated v3: structural auction truth, candidate price, separate capacity.

Nothing imports diagnostic tables or replaces v2. A real HTTP caller must bind
its request separately; this codec cannot prove a network call from a boolean.
Daily-open comparison is a POSTHOC price check, never a pre-09:25 feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

OLD_CODEC_SHA256 = "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af"
OLD_CODEC_PATH = Path(__file__).with_name("auction_truth.py")
SOURCE_POLICY_ID = "dc20_research_canonical_price_capacity_split_20260912_v3"
SOURCE_SCHEMA = "dc20_research_canonical_auction_source_v3"
ROOT_PATH = "data/research/canonical_auction_price_v3"
COVERAGE_START = "20250101"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
MAX_BYTES, MAX_ROWS = 4_000_000, 8000
STATUSES = frozenset({"CANONICAL_TABLE_PRESENT", "CANONICAL_TABLE_EMPTY", "ENTITLEMENT_DENIED"})
BASIS = "POSTHOC_ENTRY_PRICE_CHECK_NOT_PRE0925_FEATURE"
FLAGS = {"basis": BASIS, "research_only": True, "production_integrated": False,
         "production_activation_allowed": False, "actual_execution_claimed": False,
         "actual_capacity_verified": False, "known_before_0925": False,
         "price_reporting_precision_confirmed": False, "reported_price_preserved": True}
_LOAD_KEY = object()


class AuctionSourceError(ValueError):
    """Unsafe sources and invalid entries cannot silently trigger fallback."""


class AuctionSourceMissing(AuctionSourceError):
    """Only exact absent pairs qualify for precoverage absence handling."""


def _fail(condition, reason):
    if not condition:
        raise AuctionSourceError(reason)


def _old():
    _fail(not any(p.is_symlink() for p in (OLD_CODEC_PATH, *OLD_CODEC_PATH.parents)) and
          _sha(OLD_CODEC_PATH.read_bytes()) == OLD_CODEC_SHA256, "PINNED_V2_CODEC_CHANGED")
    from work.profit_1000_upgrade import auction_truth
    return auction_truth


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            _fail(key not in out, "DUPLICATE_JSON_KEY")
            out[key] = value
        return out
    def number(value):
        out = float(value)
        _fail(math.isfinite(out), "NONFINITE_JSON_NUMBER")
        return out
    def constant(value):
        raise AuctionSourceError("NONFINITE_JSON_NUMBER")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
    except AuctionSourceError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise AuctionSourceError("INVALID_SOURCE_JSON") from None


def _legacy(call):
    try:
        return call()
    except _old().AuctionSourceError as exc:
        raise AuctionSourceError(str(exc)) from None


def _date(value):
    old = _old()
    return _legacy(lambda: old._date(value))


def _code(value, market_row=False):
    old = _old()
    return _legacy(lambda: old._code(value, market_row=market_row))


def request_parameters(trade_date, code=None):
    result = {"trade_date": _date(trade_date)}
    if code is not None:
        result["ts_code"] = _code(code)
    return result


def request_contract(trade_date, code=None):
    return {"api_name": "stk_auction", "params": request_parameters(trade_date, code), "fields": list(FIELDS)}


def _credential_guard(value, token="", *, persisted_values=True):
    todo = [value]
    while todo:
        current = todo.pop()
        if isinstance(current, Mapping):
            todo.extend(current.keys())
            todo.extend(current.values())
        elif isinstance(current, list):
            todo.extend(current)
        elif isinstance(current, str):
            _fail(not token or token not in current, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
            if not persisted_values:
                # request_id and server messages are not persisted. Their
                # UUID/opaque text must not poison otherwise safe data tables.
                continue
            _fail(not re.search(r"token|secret|password|credential|authorization|bearer|api[_-]?key|密钥|凭证", current, re.I),
                  "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
            numeric_literal = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", current)
            _fail(not (len(current) >= 32 and numeric_literal is None and
                       re.fullmatch(r"[A-Za-z0-9_./+=\-]+", current)), "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")


def table_rows(data, trade_date, requested_code=None):
    """Validate full-table structure/identity ONLY; numeric eligibility is local.

    The returned immutable mappings carry no source provenance qualification.
    Reaching 8,000 rows is rejected as potentially truncated, even count=0.
    """
    day = _date(trade_date)
    if requested_code is not None:
        _code(requested_code)
    _fail(isinstance(data, dict) and {"fields", "items"} <= set(data) and
          not set(data) - {"fields", "items", "count", "has_more"}, "INVALID_DATA_TABLE")
    fields, items = data["fields"], data["items"]
    _fail(isinstance(fields, list) and len(fields) == len(FIELDS) and
          all(isinstance(field, str) for field in fields) and set(fields) == set(FIELDS), "INVALID_FIELDS")
    _fail(isinstance(items, list) and len(items) < MAX_ROWS and
          (requested_code is None or len(items) <= 1), "INVALID_ROW_COUNT_OR_POSSIBLE_TRUNCATION")
    if "has_more" in data:
        _fail(type(data["has_more"]) is bool and data["has_more"] is False, "PAGINATED_SOURCE_FORBIDDEN")
    if "count" in data:
        _fail(type(data["count"]) is int and data["count"] >= 0 and
              data["count"] in (0, len(items)), "POSITIVE_COUNT_MISMATCH")
    rows = {}
    for item in items:
        _fail(isinstance(item, list) and len(item) == len(fields), "INVALID_ROW_SHAPE")
        for value in item:
            _fail(value is None or type(value) in (str, bool, int, float), "UNSAFE_NESTED_TABLE_VALUE")
            _fail(not isinstance(value, str) or len(value) <= 128, "OVERSIZED_TABLE_VALUE")
            _fail(not isinstance(value, float) or math.isfinite(value), "NONFINITE_TABLE_VALUE")
        row = dict(zip(fields, item))
        code = _code(row["ts_code"], market_row=True)
        _fail(row["trade_date"] == day, "SOURCE_WRONG_TRADE_DATE")
        _fail(requested_code is None or requested_code == code, "SOURCE_WRONG_REQUESTED_CODE")
        _fail(code not in rows, "DUPLICATE_STOCK_ROW")
        _credential_guard(row)
        rows[code] = MappingProxyType(row)
    return MappingProxyType(rows)


def _number(value):
    old = _old()
    try:
        return old._number(value)
    except (ValueError, TypeError, ArithmeticError):
        return None


def qualify_row(row, trade_date, code, daily_open):
    """Pure per-row POSTHOC diagnostic, explicitly not loaded-source authority.

    Price is returned exactly as supplied (including numeric strings), never
    cent-rounded. Amount disagreement only makes capacity arithmetic UNKNOWN:
    amount/capacity_amount are None unless qualified; reported_auction_amount
    and raw_values retain the provider's original value independently.
    No-trade and pending have price=None and no zero-return/fallback semantics.
    """
    day, code = _date(trade_date), _code(code, market_row=True)
    opening = _number(daily_open)
    _fail(opening is not None and opening > 0, "INVALID_DAILY_OPEN")
    if row is None:
        return {"status": "PENDING_CANONICAL_ROW_ABSENT", "reason": "ROW_ABSENT_WITHOUT_LOADED_SOURCE_AUTHORITY",
                "price": None, "price_qualified": False, "trade_date": day, "ts_code": code,
                "raw_values": None, "reported_auction_price": None, "reported_auction_amount": None,
                "volume": None, "amount": None, "capacity_amount": None, "amount_identity_delta": None,
                "daily_open_cent_match": None, "capacity_proxy_verified": False, "capacity_evidence": "UNKNOWN",
                "capacity_reason": "ROW_ABSENT", "auction_trade_observed": None, "fallback_reason": None,
                "source_import_allowed": False, "loaded_source_qualification_claimed": False, **FLAGS}
    _fail(isinstance(row, Mapping) and set(row) == set(FIELDS), "INVALID_CANDIDATE_ROW")
    _fail(row["ts_code"] == code and row["trade_date"] == day, "CANDIDATE_IDENTITY_CONFLICT")
    price, vol, amount, pre = (_number(row[key]) for key in ("price", "vol", "amount", "pre_close"))
    result = {"status": "PENDING_CANONICAL_ROW", "price": None, "price_qualified": False,
              "trade_date": day, "ts_code": code, "raw_values": dict(row),
              "reported_auction_price": row["price"], "volume": row["vol"], "amount": None,
              "capacity_amount": None, "reported_auction_amount": row["amount"],
              "amount_identity_delta": None, "daily_open_cent_match": None,
              "capacity_proxy_verified": False, "capacity_evidence": "UNKNOWN",
              "capacity_reason": "PRICE_NOT_QUALIFIED", "auction_trade_observed": None,
              "fallback_reason": None, "source_import_allowed": False,
              "loaded_source_qualification_claimed": False, **FLAGS}
    def pending(reason):
        return {**result, "status": "PENDING_CANONICAL_" + reason, "reason": reason}
    if vol is None or vol != vol.to_integral_value():
        return pending("INVALID_SHARE_VOLUME")
    if pre is None or pre <= 0:
        return pending("INVALID_PRE_CLOSE")
    if vol == 0:
        if amount == 0 and (row["price"] is None or price == 0):
            return {**result, "status": "OBSERVED_NO_AUCTION_TRADE", "reason": "OBSERVED_ZERO_VOLUME_AND_AMOUNT",
                    "auction_trade_observed": False, "capacity_reason": "NO_AUCTION_TRADE_NOT_CAPACITY"}
        return pending("ZERO_VOLUME_DATA_CONFLICT")
    if price is None or price <= 0:
        return pending("INVALID_PRICE")
    old = _old()
    matches = old._cent(price) == old._cent(opening)
    result["daily_open_cent_match"] = matches
    if not matches:
        return pending("PRICE_DAILY_OPEN_CONFLICT")
    if amount is None:
        capacity_reason = "AMOUNT_INVALID_OR_MISSING"
    elif amount <= 0:
        capacity_reason = "POSITIVE_VOLUME_WITH_NONPOSITIVE_AMOUNT"
    else:
        with localcontext() as context:
            context.prec = 60
            delta = price * vol - amount
        result["amount_identity_delta"] = str(delta)
        capacity_reason = "PRICE_VOLUME_AMOUNT_ARITHMETIC_CONSISTENT" if abs(delta) <= Decimal("0.02") else "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT"
    consistent = capacity_reason == "PRICE_VOLUME_AMOUNT_ARITHMETIC_CONSISTENT"
    return {**result, "status": "CANONICAL_PRICE_OBSERVED", "reason": "POSTHOC_PRICE_CENT_MATCH",
            "price": row["price"], "price_qualified": True, "auction_trade_observed": True,
            "amount": float(amount) if consistent else None,
            "capacity_amount": float(amount) if consistent else None,
            "capacity_proxy_verified": consistent,
            "capacity_evidence": "CANONICAL_AMOUNT_ARITHMETIC_ONLY_NOT_ORDER_CAPACITY" if consistent else "UNKNOWN",
            "capacity_reason": capacity_reason, "capacity_arithmetic_tolerance_cny": "0.02"}


def _metadata_expected(day, data_raw):
    return {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
            "source": "tushare:stk_auction", "trade_date": day, "network_request_performed": True,
            "immutable": True, "price_unit": "CNY_PER_SHARE", "volume_unit": "SHARES", "amount_unit": "CNY",
            "event_semantics": "CANONICAL_OPENING_AUCTION_AVAILABLE_0926_0929",
            "credential_persisted": False, "server_messages_persisted": False,
            "full_response_envelope_retained": False, "diagnostic_table_imported": False,
            "ingestion": "ORIGINAL_HTTP_RESPONSE_ONLY", "numeric_qualification_scope": "REQUESTED_CANDIDATE_ONLY",
            "old_codec_sha256": OLD_CODEC_SHA256, "data_sha256": _sha(data_raw), **FLAGS}


def source_bytes(raw_response, trade_date, *, request, fetched_at_utc, network_request_performed, token=""):
    old = _old()
    day = _date(trade_date)
    requested_code = _legacy(lambda: old._request(request, day))
    _legacy(lambda: old._fetched(fetched_at_utc, day))
    _fail(network_request_performed is True, "REAL_NETWORK_ATTEMPT_REQUIRED")
    _fail(isinstance(token, str), "INVALID_CREDENTIAL_ARGUMENT")
    _fail(isinstance(raw_response, bytes) and 0 < len(raw_response) <= MAX_BYTES, "INVALID_HTTP_RESPONSE_BYTES")
    payload = _strict_json(raw_response)
    _fail(isinstance(payload, dict) and type(payload.get("code")) is int and
          not set(payload) - {"code", "msg", "data", "request_id"}, "INVALID_API_ENVELOPE_OR_DIAGNOSTIC_INPUT")
    # No response messages are persisted. A token embedded anywhere in a real
    # response (including escaped JSON strings) prevents source persistence.
    if token:
        _fail(token.encode() not in raw_response, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
        _credential_guard(payload, token, persisted_values=False)
    if payload["code"] == 0:
        data = payload.get("data")
        rows = table_rows(data, day, requested_code)
        _fail(not rows or day >= COVERAGE_START, "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
        status = "CANONICAL_TABLE_PRESENT" if rows else "CANONICAL_TABLE_EMPTY"
    else:
        message = str(payload.get("msg", "")).lower()
        _fail(not any(word in message for word in ("token", "凭证", "credential", "频率", "每分钟", "rate limit")),
              "OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE")
        _fail(any(word in message for word in ("权限", "permission", "授权", "积分", "entitlement")),
              "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE")
        _fail(payload.get("data") is None, "API_FAILURE_WITH_DATA_CONFLICT")
        data, rows, status = None, {}, "ENTITLEMENT_DENIED"
    body = _json(data)
    _fail(len(body) <= MAX_BYTES, "OVERSIZED_CANONICAL_SOURCE")
    metadata = {**_metadata_expected(day, body), "request": request, "fetched_at_utc": fetched_at_utc,
                "status": status, "api_code": payload["code"], "rows": len(rows),
                "http_response_sha256": _sha(raw_response), "http_response_bytes": len(raw_response)}
    meta_body = _json(metadata)
    _fail(not token or token.encode() not in body + meta_body, "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
    return body, meta_body


def source_paths(root, trade_date):
    day = _date(trade_date)
    path = Path(root) / ROOT_PATH / day[:4] / day / "stk_auction.data.json"
    return path, path.with_name("stk_auction.meta.json")


@dataclass(frozen=True, init=False)
class LoadedSource:
    trade_date: str
    requested_code: str | None
    status: str
    rows: Mapping
    source_files: tuple

    def __init__(self, trade_date, requested_code, status, rows, source_files, *, _key=None):
        _fail(_key is _LOAD_KEY, "PRELOAD_MUST_BE_CREATED_BY_V3_LOAD")
        for key, value in (("trade_date", trade_date), ("requested_code", requested_code), ("status", status),
                           ("rows", MappingProxyType({code: MappingProxyType(dict(row)) for code, row in rows.items()})),
                           ("source_files", tuple(MappingProxyType(dict(binding)) for binding in source_files))):
            object.__setattr__(self, key, value)


def load(root, trade_date):
    day, old = _date(trade_date), _old()
    root = Path(root)
    _fail(not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_SOURCE_ROOT")
    root = root.resolve(strict=True)
    paths = source_paths(root, day)
    for path in paths:
        _legacy(lambda path=path: old._safe_path(root, path))
    if not any(path.exists() for path in paths):
        raise AuctionSourceMissing("V3_CANONICAL_SOURCE_NOT_ATTEMPTED")
    _fail(all(path.is_file() for path in paths), "INCOMPLETE_SOURCE_PAIR")
    body, meta_body = (path.read_bytes() for path in paths)
    _fail(len(body) <= MAX_BYTES and len(meta_body) <= 20_000, "OVERSIZED_SOURCE")
    data, meta = _strict_json(body), _strict_json(meta_body)
    _fail(isinstance(meta, dict), "INVALID_SOURCE_METADATA")
    _fail(_json(data) == body and _json(meta) == meta_body, "NONCANONICAL_SOURCE_REPRESENTATION")
    expected = _metadata_expected(day, body)
    _fail(set(meta) == set(expected) | {"request", "fetched_at_utc", "status", "api_code", "rows", "http_response_sha256", "http_response_bytes"},
          "METADATA_FIELDS_DRIFTED")
    _fail(all(type(meta[key]) is type(value) and meta[key] == value for key, value in expected.items()),
          "SOURCE_METADATA_OR_SHA_MISMATCH")
    requested_code = _legacy(lambda: old._request(meta["request"], day))
    _legacy(lambda: old._fetched(meta["fetched_at_utc"], day))
    _legacy(lambda: old._sha_value(meta["http_response_sha256"]))
    _fail(type(meta["http_response_bytes"]) is int and 0 < meta["http_response_bytes"] <= MAX_BYTES,
          "INVALID_RESPONSE_BYTE_COUNT")
    _fail(isinstance(meta["status"], str) and meta["status"] in STATUSES and
          type(meta["api_code"]) is int and type(meta["rows"]) is int, "INVALID_SOURCE_STATUS")
    if meta["status"] == "ENTITLEMENT_DENIED":
        _fail(data is None and meta["rows"] == 0 and meta["api_code"] != 0, "UNAVAILABLE_RECEIPT_CONFLICT")
        rows = {}
    else:
        rows = table_rows(data, day, requested_code)
        _fail(meta["api_code"] == 0 and meta["rows"] == len(rows) and
              meta["status"] == ("CANONICAL_TABLE_PRESENT" if rows else "CANONICAL_TABLE_EMPTY"),
              "SOURCE_STATUS_OR_ROW_COUNT_CONFLICT")
        _fail(not rows or day >= COVERAGE_START, "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
    bindings = ({"path": path.relative_to(root).as_posix(), "sha256": _sha(raw)}
                for path, raw in zip(paths, (body, meta_body)))
    return LoadedSource(day, requested_code, meta["status"], rows, bindings, _key=_LOAD_KEY)


def entry_price(source, trade_date, code, daily_open, *, daily_source_binding):
    day, code, old = _date(trade_date), _code(code), _old()
    opening = _number(daily_open)
    _fail(opening is not None and opening > 0, "INVALID_DAILY_OPEN")
    _fail(isinstance(daily_source_binding, Mapping) and set(daily_source_binding) == {"path", "sha256"},
          "DAILY_SOURCE_BINDING_REQUIRED")
    path = daily_source_binding["path"]
    _fail(isinstance(path, str) and path and not path.startswith("/") and ":" not in path and "\\" not in path and
          all(part not in ("", ".", "..") for part in path.split("/")), "UNSAFE_DAILY_SOURCE_PATH")
    _legacy(lambda: old._sha_value(daily_source_binding["sha256"]))
    result = {"source_policy_id": SOURCE_POLICY_ID, "trade_date": day, "ts_code": code,
              "daily_source_binding": dict(daily_source_binding), "source_files": [], **FLAGS}
    if source is None:
        _fail(day < COVERAGE_START, "CANONICAL_ATTEMPT_REQUIRED_BEFORE_FALLBACK")
        reason = "HISTORY_BEFORE_CANONICAL_COVERAGE"
    else:
        _fail(type(source) is LoadedSource and source.trade_date == day, "V3_PRELOADED_SOURCE_REQUIRED_FOR_EXACT_DAY")
        _fail(source.requested_code is None or source.requested_code == code, "CODE_OUTSIDE_RECORDED_REQUEST")
        result["source_files"] = [dict(binding) for binding in source.source_files]
        if code in source.rows:
            qualified = qualify_row(source.rows[code], day, code, daily_open)
            return {**qualified, **result, "entry_price_source": "TUSHARE_STK_AUCTION",
                    "loaded_source_qualification_claimed": True}
        _fail(source.status in STATUSES, "SOURCE_STATUS_INELIGIBLE_FOR_FALLBACK")
        reason = "CANONICAL_ENTITLEMENT_DENIED" if source.status == "ENTITLEMENT_DENIED" else "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST"
    return {**result, "price": daily_open, "price_qualified": False, "reported_auction_price": None,
            "volume": None, "amount": None, "capacity_amount": None, "reported_auction_amount": None,
            "raw_values": None, "amount_identity_delta": None,
            "daily_open_cent_match": None, "entry_price_source": "DAILY_OPEN_PROXY", "fallback_reason": reason,
            "status": "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN", "auction_trade_observed": None,
            "capacity_proxy_verified": False, "capacity_evidence": "UNKNOWN", "capacity_reason": reason,
            "source_import_allowed": False, "loaded_source_qualification_claimed": source is not None}


__all__ = ["source_bytes", "source_paths", "load", "entry_price", "table_rows", "qualify_row",
           "LoadedSource", "AuctionSourceError", "AuctionSourceMissing", "request_contract"]
