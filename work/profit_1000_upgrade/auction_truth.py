"""Isolated research-only 09:25 auction truth; not integrated into production.

Only canonical tushare:stk_auction is eligible. The differently scoped
stk_auction_o endpoint is never searched, loaded, or used for price/capacity.
HTTP envelopes are not persisted: sanitized data and response SHA are bound
to the exact request. A receipt proves the recorded request contract, not an
actual order fill. The collector must separately bind these files in its run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

SOURCE_POLICY_ID = "dc20_research_canonical_stk_auction_20260912_v2"
SOURCE_SCHEMA = "dc20_research_canonical_auction_source_v2"
COVERAGE_START = "20250101"
ROOT_PATH = "data/research/canonical_auction_0925"
FIELDS = ("ts_code", "trade_date", "price", "vol", "amount", "pre_close")
STATUSES = {"CANONICAL_TABLE_PRESENT", "CANONICAL_TABLE_EMPTY", "ENTITLEMENT_DENIED"}
MAX_BYTES = 4_000_000
MAX_ROWS = 8000
_LOAD_KEY = object()


class AuctionSourceError(ValueError):
    """Present but ineligible/corrupt truth must never trigger a fallback."""


class AuctionSourceMissing(AuctionSourceError):
    """No canonical source pair or valid unavailable receipt exists."""


def _fail(condition, message):
    if not condition:
        raise AuctionSourceError(message)


def _date(value):
    _fail(isinstance(value, str) and re.fullmatch(r"20\d{6}", value) is not None,
          "INVALID_TRADE_DATE")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise AuctionSourceError("INVALID_TRADE_DATE") from None
    return value


def _code(value, *, market_row=False):
    suffix = "SH|SZ|BJ" if market_row else "SH|SZ"
    _fail(isinstance(value, str) and re.fullmatch(r"\d{6}\.(" + suffix + ")", value) is not None,
          "INVALID_STOCK_CODE")
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _strict_json(raw):
    def pairs(values):
        output = {}
        for key, value in values:
            _fail(key not in output, "DUPLICATE_JSON_KEY")
            output[key] = value
        return output
    def constant(_):
        raise AuctionSourceError("NONFINITE_JSON_CONSTANT")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError):
        raise AuctionSourceError("INVALID_SOURCE_JSON") from None


def _sha_value(value):
    _fail(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
          "INVALID_SHA256")
    return value


def _number(value):
    _fail(not isinstance(value, bool) and isinstance(value, (str, int, float)), "INVALID_NUMERIC_VALUE")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AuctionSourceError("INVALID_NUMERIC_VALUE") from None
    _fail(result.is_finite() and result >= 0 and result.adjusted() < 24, "INVALID_NUMERIC_VALUE")
    return result


def _cent(value):
    with localcontext() as context:
        context.prec = 40
        return _number(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def request_parameters(trade_date, code=None):
    result = {"trade_date": _date(trade_date)}
    if code is not None:
        result["ts_code"] = _code(code)
    return result


def request_contract(trade_date, code=None):
    return {"api_name": "stk_auction", "params": request_parameters(trade_date, code),
            "fields": list(FIELDS)}


def _request(value, trade_date):
    _fail(isinstance(value, dict) and set(value) == {"api_name", "params", "fields"}, "INVALID_REQUEST_CONTRACT")
    params = value.get("params")
    _fail(isinstance(params, dict) and set(params) in ({"trade_date"}, {"trade_date", "ts_code"}), "INVALID_REQUEST_PARAMS")
    expected = request_contract(trade_date, params.get("ts_code"))
    _fail(value == expected, "REQUEST_DATE_CODE_ENDPOINT_OR_FIELDS_MISMATCH")
    return params.get("ts_code")


def _fetched(value, trade_date):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _fail(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "FETCH_TIMESTAMP_NOT_UTC")
        available = datetime.strptime(trade_date + " 01:26:00", "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
        _fail(stamp >= available, "FETCH_BEFORE_CANONICAL_EVENT_AVAILABLE")
    except (AttributeError, TypeError, ValueError):
        raise AuctionSourceError("INVALID_FETCH_TIMESTAMP") from None
    return value


def _table(data, trade_date, requested_code):
    _fail(isinstance(data, dict) and {"fields", "items"} <= set(data)
          and not set(data) - {"fields", "items", "count", "has_more"}, "INVALID_DATA_TABLE")
    fields, items = data["fields"], data["items"]
    _fail(isinstance(fields, list) and len(fields) == len(FIELDS)
          and all(isinstance(field, str) for field in fields) and set(fields) == set(FIELDS), "INVALID_FIELDS")
    _fail(isinstance(items, list) and len(items) <= (1 if requested_code else MAX_ROWS), "INVALID_ROW_COUNT")
    if "has_more" in data:
        _fail(type(data["has_more"]) is bool and data["has_more"] is False, "PAGINATED_SOURCE_FORBIDDEN")
    if "count" in data:
        _fail(type(data["count"]) is int and data["count"] >= 0
              and data["count"] in (0, len(items)), "POSITIVE_COUNT_MISMATCH")
    rows = {}
    for item in items:
        _fail(isinstance(item, list) and len(item) == len(fields), "INVALID_ROW_SHAPE")
        row = dict(zip(fields, item))
        code = _code(row["ts_code"], market_row=True)
        _fail(row["trade_date"] == trade_date, "SOURCE_WRONG_TRADE_DATE")
        _fail(requested_code is None or requested_code == code, "SOURCE_WRONG_REQUESTED_CODE")
        _fail(code not in rows, "DUPLICATE_STOCK_ROW")
        values = {key: _number(row[key]) for key in ("price", "vol", "amount", "pre_close")}
        price, volume, amount = (values[key] for key in ("price", "vol", "amount"))
        # A full-market response can include newly listed, non-candidate rows.
        # Preserve nonnegative pre_close there; require a positive value only
        # when resolving an actual 2->3/3->4 candidate below.
        _fail(volume == volume.to_integral_value(), "INVALID_SHARE_VOLUME")
        if volume == 0:
            _fail(amount == 0, "ZERO_VOLUME_NONZERO_AMOUNT")
        else:
            _fail(price > 0 and amount > 0, "POSITIVE_VOLUME_REQUIRES_PRICE_AND_AMOUNT")
            # Price CNY/share, vol shares, amount CNY. No hand/thousand conversion.
            # Two cents cover currency rounding only, not unit reinterpretation.
            with localcontext() as context:
                context.prec = 60
                _fail(abs(price * volume - amount) <= Decimal("0.02"), "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT")
        rows[code] = MappingProxyType(row)
    return MappingProxyType(rows)


def source_bytes(raw_response, trade_date, *, request, fetched_at_utc,
                 network_request_performed, token=""):
    """Return data/meta bytes after validating one actual recorded HTTP attempt.

    Caller supplies the exact request without a token. Original fields/items
    remain unchanged, including count=0 (a provider sentinel, not row count).
    Permission denial is retained only as a fixed category, never its message.
    """
    day = _date(trade_date)
    code = _request(request, day)
    _fetched(fetched_at_utc, day)
    _fail(network_request_performed is True, "REAL_NETWORK_ATTEMPT_REQUIRED")
    _fail(isinstance(token, str), "INVALID_CREDENTIAL_ARGUMENT")
    _fail(isinstance(raw_response, bytes) and len(raw_response) <= MAX_BYTES, "INVALID_HTTP_RESPONSE_BYTES")
    try:
        payload = _strict_json(raw_response)
    except (ValueError, UnicodeError):
        raise AuctionSourceError("INVALID_HTTP_RESPONSE_JSON") from None
    _fail(isinstance(payload, dict) and type(payload.get("code")) is int, "INVALID_API_ENVELOPE")
    if payload["code"] == 0:
        data = payload.get("data")
        rows = _table(data, day, code)
        _fail(not rows or day >= COVERAGE_START, "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
        status = "CANONICAL_TABLE_PRESENT" if rows else "CANONICAL_TABLE_EMPTY"
    else:
        message = str(payload.get("msg", "")).lower()
        # Authentication/rate problems cannot be turned into data-unavailable truth.
        if any(word in message for word in ("token", "凭证", "credential", "频率", "每分钟", "rate limit")):
            raise AuctionSourceError("OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE")
        _fail(any(word in message for word in ("权限", "permission", "授权", "积分", "entitlement")),
              "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE")
        _fail(payload.get("data") is None, "API_FAILURE_WITH_DATA_CONFLICT")
        data, rows, status = None, {}, "ENTITLEMENT_DENIED"
    raw = _json(data)
    metadata = {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
                "source": "tushare:stk_auction", "trade_date": day,
                "request": request, "network_request_performed": True,
                "fetched_at_utc": fetched_at_utc, "status": status,
                "api_code": payload["code"], "rows": len(rows),
                "data_sha256": _sha(raw), "http_response_sha256": _sha(raw_response),
                "http_response_bytes": len(raw_response), "immutable": True,
                "price_unit": "CNY_PER_SHARE", "volume_unit": "SHARES", "amount_unit": "CNY",
                "event_semantics": "CANONICAL_OPENING_AUCTION_AVAILABLE_0926_0929",
                "credential_persisted": False, "server_messages_persisted": False,
                "actual_execution_claimed": False, "research_only": True,
                "production_integrated": False}
    meta_raw = _json(metadata)
    _fail(not token or token.encode("utf-8") not in raw + meta_raw,
          "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
    return raw, meta_raw


def source_paths(root, trade_date):
    day = _date(trade_date)
    path = Path(root) / ROOT_PATH / day[:4] / day / "stk_auction.data.json"
    return path, path.with_name("stk_auction.meta.json")


def _safe_path(root, path):
    _fail(root in path.resolve().parents and not any(p.is_symlink() for p in (path, *path.parents)), "UNSAFE_SOURCE_PATH")
    _fail(not path.exists() or path.is_file(), "SOURCE_NOT_REGULAR_FILE")


@dataclass(frozen=True, init=False)
class LoadedSource:
    """Internal immutable preload, not a deserialization/public constructor.

    A runner binds/rechecks source_files once per replay; this object does not
    re-read a full-market table for each stock. Only load() constructs it.
    Deliberate Python reflection is outside this in-process trust boundary.
    """
    trade_date: str
    requested_code: str | None
    status: str
    rows: Mapping
    source_files: tuple

    def __init__(self, trade_date, requested_code, status, rows, source_files, *, _key=None):
        _fail(_key is _LOAD_KEY, "PRELOAD_MUST_BE_CREATED_BY_LOAD")
        for key, value in (("trade_date", trade_date), ("requested_code", requested_code),
                           ("status", status),
                           ("rows", MappingProxyType({code: MappingProxyType(dict(row)) for code, row in rows.items()})),
                           ("source_files", tuple(MappingProxyType(dict(item)) for item in source_files))):
            object.__setattr__(self, key, value)


def load(root, trade_date):
    """Load exact research pair; absence raises, never silently returns {}."""
    day = _date(trade_date)
    root = Path(root)
    _fail(not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_SOURCE_ROOT")
    root = root.resolve(strict=True)
    data_path, meta_path = source_paths(root, day)
    for path in (data_path, meta_path):
        _safe_path(root, path)
    if not data_path.exists() and not meta_path.exists():
        raise AuctionSourceMissing("CANONICAL_SOURCE_NOT_ATTEMPTED")
    _fail(data_path.is_file() and meta_path.is_file(), "INCOMPLETE_SOURCE_PAIR")
    raw, meta_raw = data_path.read_bytes(), meta_path.read_bytes()
    _fail(len(raw) <= MAX_BYTES and len(meta_raw) <= 20_000, "OVERSIZED_SOURCE")
    try:
        data, meta = _strict_json(raw), _strict_json(meta_raw)
    except (ValueError, UnicodeError):
        raise AuctionSourceError("INVALID_SOURCE_JSON") from None
    _fail(isinstance(meta, dict), "INVALID_SOURCE_METADATA")
    _fail(_json(data) == raw and _json(meta) == meta_raw, "NONCANONICAL_SOURCE_REPRESENTATION")
    expected = {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
                "source": "tushare:stk_auction", "trade_date": day,
                "network_request_performed": True, "immutable": True,
                "price_unit": "CNY_PER_SHARE", "volume_unit": "SHARES", "amount_unit": "CNY",
                "event_semantics": "CANONICAL_OPENING_AUCTION_AVAILABLE_0926_0929",
                "credential_persisted": False, "server_messages_persisted": False,
                "actual_execution_claimed": False, "research_only": True,
                "production_integrated": False, "data_sha256": _sha(raw)}
    _fail(set(meta) == set(expected) | {"request", "fetched_at_utc", "status", "api_code", "rows", "http_response_sha256", "http_response_bytes"}, "METADATA_FIELDS_DRIFTED")
    _fail(all(type(meta[k]) is type(v) and meta[k] == v for k, v in expected.items()), "SOURCE_METADATA_OR_SHA_MISMATCH")
    code = _request(meta["request"], day)
    _fetched(meta["fetched_at_utc"], day)
    _sha_value(meta["http_response_sha256"])
    _fail(type(meta["http_response_bytes"]) is int and 0 < meta["http_response_bytes"] <= MAX_BYTES,
          "INVALID_RESPONSE_BYTE_COUNT")
    _fail(isinstance(meta["status"], str) and meta["status"] in STATUSES
          and type(meta["api_code"]) is int and type(meta["rows"]) is int, "INVALID_SOURCE_STATUS")
    if meta["status"] == "ENTITLEMENT_DENIED":
        _fail(data is None and meta["rows"] == 0 and meta["api_code"] != 0, "UNAVAILABLE_RECEIPT_CONFLICT")
        rows = MappingProxyType({})
    else:
        rows = _table(data, day, code)
        _fail(meta["api_code"] == 0 and meta["rows"] == len(rows), "SOURCE_STATUS_OR_ROW_COUNT_CONFLICT")
        _fail(meta["status"] == ("CANONICAL_TABLE_PRESENT" if rows else "CANONICAL_TABLE_EMPTY"), "SOURCE_STATUS_OR_ROW_COUNT_CONFLICT")
        _fail(not rows or day >= COVERAGE_START, "SOURCE_OUTSIDE_DOCUMENTED_COVERAGE")
    bindings = tuple(MappingProxyType({"path": path.relative_to(root).as_posix(), "sha256": _sha(body)})
                     for path, body in ((data_path, raw), (meta_path, meta_raw)))
    return LoadedSource(day, code, meta["status"], rows, bindings, _key=_LOAD_KEY)


def entry_price(source, trade_date, code, daily_open, *, daily_source_binding):
    """Resolve a preloaded source or explicit absence; never infer a fill.

    Pass source=None only after catching AuctionSourceMissing, never a general
    source error. None is eligible solely before frozen canonical coverage.
    The runner separately binds/validates the daily row that supplies daily_open.
    """
    day, code = _date(trade_date), _code(code)
    opening = _number(daily_open)
    _fail(opening > 0, "INVALID_DAILY_OPEN")
    _fail(isinstance(daily_source_binding, Mapping) and set(daily_source_binding) == {"path", "sha256"}, "DAILY_SOURCE_BINDING_REQUIRED")
    path = daily_source_binding["path"]
    _fail(isinstance(path, str) and path and not path.startswith("/") and "\\" not in path
          and all(p not in ("", ".", "..") for p in path.split("/")), "UNSAFE_DAILY_SOURCE_PATH")
    _sha_value(daily_source_binding["sha256"])
    result = {"source_policy_id": SOURCE_POLICY_ID, "trade_date": day, "ts_code": code,
              "actual_execution_claimed": False, "actual_capacity_verified": False,
              "research_only": True, "production_integrated": False,
              "daily_source_binding": dict(daily_source_binding), "source_files": []}
    if source is None:
        _fail(day < COVERAGE_START, "CANONICAL_ATTEMPT_REQUIRED_BEFORE_FALLBACK")
        reason = "HISTORY_BEFORE_CANONICAL_COVERAGE"
    else:
        _fail(type(source) is LoadedSource and source.trade_date == day, "PRELOADED_SOURCE_WRONG_TRADE_DATE")
        _fail(source.requested_code is None or source.requested_code == code, "CODE_OUTSIDE_RECORDED_REQUEST")
        result["source_files"] = [dict(binding) for binding in source.source_files]
        row = source.rows.get(code)
        if row is not None:
            _fail(_number(row["pre_close"]) > 0, "CANONICAL_CANDIDATE_PRE_CLOSE_INVALID")
            price, volume, amount = (_number(row[k]) for k in ("price", "vol", "amount"))
            if volume > 0:
                _fail(_cent(price) == _cent(opening), "CANONICAL_PRICE_DAILY_OPEN_CONFLICT")
            return {**result, "price": float(price) if volume > 0 else None,
                    "reported_auction_price": row["price"], "volume": float(volume), "amount": float(amount),
                    "entry_price_source": "TUSHARE_STK_AUCTION", "fallback_reason": None,
                    "status": "CANONICAL_PRICE_OBSERVED" if volume > 0 else "OBSERVED_NO_AUCTION_TRADE",
                    "auction_trade_observed": volume > 0, "capacity_proxy_verified": True,
                    "capacity_evidence": "OBSERVED_CANONICAL_AUCTION_AMOUNT"}
        _fail(source.status in STATUSES, "SOURCE_STATUS_INELIGIBLE_FOR_FALLBACK")
        reason = "CANONICAL_ENTITLEMENT_DENIED" if source.status == "ENTITLEMENT_DENIED" else "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST"
    return {**result, "price": float(opening), "reported_auction_price": None,
            "volume": None, "amount": None, "entry_price_source": "DAILY_OPEN_PROXY",
            "fallback_reason": reason, "status": "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN",
            "auction_trade_observed": None, "capacity_proxy_verified": False, "capacity_evidence": "UNKNOWN"}
