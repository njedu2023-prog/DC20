"""Isolated suspension EVENT evidence. Never prices, marks, labels or settlement.

Official suspend_d documents S as suspended and nonblank suspend_timing as
intraday only. A precise S/null row proves that session's suspension event;
it does not prove a missing daily partition was complete or authorize skipping
an exit day. No caller-provided boolean can turn event evidence into that proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import zipfile

SOURCE_POLICY_ID = "dc20_research_suspend_d_event_only_20260912_v1"
SOURCE_SCHEMA = "dc20_research_suspension_event_source_v1"
ROOT_PATH = "research_inputs/suspension_events_v1"
FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
MAX_BYTES = 1_000_000
MAX_ROWS = 500
DIAGNOSTIC_ZIP_SHA = "760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9"
DIAGNOSTIC_RUN = "34672430723"
DIAGNOSTIC_HEAD = "6ea9616014da3850ae4064c89e56e832608180d3"
ARCHIVE_PATH = ROOT_PATH + "/diagnostic_imports/" + DIAGNOSTIC_RUN + "/original.zip"
_LOAD_KEY = object()


class SuspensionSourceError(ValueError):
    pass


def _expect(condition, message):
    if not condition:
        raise SuspensionSourceError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def _parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            _expect(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise SuspensionSourceError("NONFINITE_JSON_CONSTANT")
    def finite(value):
        number = float(value)
        _expect(math.isfinite(number), "NONFINITE_JSON_NUMBER")
        return number
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise SuspensionSourceError("INVALID_SOURCE_JSON") from None


def _date(value):
    _expect(isinstance(value, str) and re.fullmatch(r"20\d{6}", value), "INVALID_DATE")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise SuspensionSourceError("INVALID_DATE") from None
    return value


def _code(value):
    _expect(isinstance(value, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", value), "INVALID_CODE")
    return value


def _sha_value(value):
    _expect(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_SHA")


def request_parameters(trade_date, code):
    return {"ts_code": _code(code), "trade_date": _date(trade_date)}


def _request(params, day, code):
    _expect(type(params) is dict, "INVALID_REQUEST")
    if set(params) == {"ts_code", "trade_date"}:
        _expect(params == request_parameters(day, code), "REQUEST_IDENTITY_CONFLICT")
        return day, day
    _expect(set(params) == {"ts_code", "start_date", "end_date"}, "INVALID_REQUEST_FIELDS")
    start, end = _date(params["start_date"]), _date(params["end_date"])
    _expect(params["ts_code"] == code and start <= day <= end, "REQUEST_IDENTITY_CONFLICT")
    span = datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")
    _expect(span.days <= 366, "REQUEST_RANGE_TOO_LARGE")
    return start, end


def _fetched(value, end):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _expect(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0,
                "FETCH_TIMESTAMP_NOT_UTC")
        cutoff = datetime.strptime(end + " 08:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
        _expect(stamp >= cutoff, "FETCH_BEFORE_COMPLETED_SESSION")
    except (AttributeError, TypeError, ValueError):
        raise SuspensionSourceError("INVALID_FETCH_TIMESTAMP") from None


def _timing(value):
    if value in (None, ""):
        return
    _expect(isinstance(value, str) and len(value) <= 64, "INVALID_SUSPENSION_TIMING")
    segments = value.split(",")
    for segment in segments:
        _expect(re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", segment), "INVALID_SUSPENSION_TIMING")
        start, end = segment.split("-")
        try:
            datetime.strptime(start, "%H:%M")
            datetime.strptime(end, "%H:%M")
        except ValueError:
            raise SuspensionSourceError("INVALID_SUSPENSION_TIMING") from None
        _expect("09:15" <= start < end <= "15:00", "INVALID_SUSPENSION_TIMING")


def _table(data, day, code, params):
    start, end = _request(params, day, code)
    _expect(type(data) is dict and {"fields", "items"} <= set(data)
            and not set(data) - {"fields", "items", "count", "has_more"}, "INVALID_TABLE")
    fields, items = data["fields"], data["items"]
    _expect(type(fields) is list and len(fields) == len(FIELDS)
            and all(type(k) is str for k in fields) and set(fields) == set(FIELDS), "INVALID_FIELDS")
    _expect(type(items) is list and len(items) <= MAX_ROWS, "INVALID_ROW_COUNT")
    if "count" in data:
        _expect(type(data["count"]) is int and data["count"] in (0, len(items)), "COUNT_CONFLICT")
    if "has_more" in data:
        _expect(data["has_more"] is False, "PAGINATED_TABLE")
    rows = {}
    for item in items:
        _expect(type(item) is list and len(item) == len(fields), "INVALID_ROW")
        row = dict(zip(fields, item))
        date = _date(row["trade_date"])
        _expect(row["ts_code"] == code and start <= date <= end, "SOURCE_IDENTITY_CONFLICT")
        _expect(row["suspend_type"] in ("S", "R"), "INVALID_SUSPEND_TYPE")
        _timing(row["suspend_timing"])
        # Multiple rows, even identical S rows, cannot be silently deduplicated;
        # S and R on one day and multiple intraday intervals need explicit review.
        _expect(date not in rows, "DUPLICATE_OR_CONFLICTING_SESSION")
        rows[date] = MappingProxyType(row)
    return MappingProxyType(rows)


def _metadata(raw, day, code, params, fetched, rows, http_sha, http_bytes, kind, origin):
    return {"schema_version": SOURCE_SCHEMA, "source_policy_id": SOURCE_POLICY_ID,
            "source": "tushare:suspend_d", "trade_date": day, "ts_code": code,
            "request": {"api_name": "suspend_d", "params": params, "fields": list(FIELDS)},
            "fetched_at_utc": fetched, "network_request_performed": True,
            "data_sha256": _sha(raw), "rows": len(rows),
            "http_response_sha256": http_sha, "http_response_bytes": http_bytes,
            "source_evidence_kind": kind, "diagnostic_import": origin,
            "raw_http_supplied_to_codec": kind == "RAW_HTTP_TABLE",
            "raw_http_retained": False, "source_values_modified": False,
            "market_absence_verified": False, "can_advance_holding_day": False,
            "settlement_allowed": False, "production_integrated": False,
            "production_activation_allowed": False, "research_only": True,
            "actual_execution_claimed": False, "credential_persisted": False,
            "server_messages_persisted": False, "immutable": True}


def source_bytes(raw_response, trade_date, code, *, request_params, fetched_at_utc,
                 network_request_performed, token=""):
    """Validate actual HTTP input, return sanitized original table/meta bytes.

    This API does no network or filesystem writes. A caller must bind a real
    request/response in its collection receipt. Error envelopes are never proof
    of no event, even when they report permission denial or no data.
    """
    day, code = _date(trade_date), _code(code)
    _, end = _request(request_params, day, code)
    _fetched(fetched_at_utc, end)
    _expect(network_request_performed is True, "REAL_REQUEST_REQUIRED")
    _expect(type(token) is str, "INVALID_TOKEN_ARGUMENT")
    _expect(type(raw_response) is bytes and 0 < len(raw_response) <= MAX_BYTES, "INVALID_HTTP_BYTES")
    payload = _parse(raw_response)
    _expect(type(payload) is dict and type(payload.get("code")) is int and payload["code"] == 0,
            "API_FAILURE_NOT_EVENT_EVIDENCE")
    data = payload.get("data")
    rows = _table(data, day, code, request_params)
    raw = _json(data)
    meta = _metadata(raw, day, code, request_params, fetched_at_utc, rows,
                     _sha(raw_response), len(raw_response), "RAW_HTTP_TABLE", None)
    meta_raw = _json(meta)
    _expect(not token or token.encode() not in raw + meta_raw, "CREDENTIAL_LIKE_SOURCE_FORBIDDEN")
    return raw, meta_raw


def source_paths(root, trade_date, code):
    day, code = _date(trade_date), _code(code)
    base = Path(root) / ROOT_PATH / day[:4] / day
    return base / (code + ".data.json"), base / (code + ".meta.json")


def _archive(zip_bytes):
    _expect(type(zip_bytes) is bytes and len(zip_bytes) <= MAX_BYTES
            and _sha(zip_bytes) == DIAGNOSTIC_ZIP_SHA, "DIAGNOSTIC_ZIP_NOT_PINNED")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            infos = archive.infolist()
            _expect(len(infos) == 32 and len({i.filename for i in infos}) == 32,
                    "DIAGNOSTIC_MEMBER_SET_INVALID")
            _expect(all(not i.is_dir() and not i.flag_bits & 1 and i.file_size <= MAX_BYTES
                        and not i.filename.startswith("/") and "\\" not in i.filename
                        and all(p not in ("", ".", "..") for p in i.filename.split("/")) for i in infos),
                    "UNSAFE_DIAGNOSTIC_MEMBER")
            files = {i.filename: archive.read(i) for i in infos}
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise SuspensionSourceError("INVALID_DIAGNOSTIC_ARCHIVE") from None
    report = _parse(files["receipt.json"])
    _expect(report.get("run_id") == DIAGNOSTIC_RUN and report.get("run_commit") == DIAGNOSTIC_HEAD
            and report.get("status") == "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT", "DIAGNOSTIC_ORIGIN_CONFLICT")
    bindings = report.get("source_files")
    _expect(type(bindings) is list and len(bindings) == 31, "DIAGNOSTIC_BINDINGS_INVALID")
    bound = set()
    for item in bindings:
        _expect(type(item) is dict and set(item) == {"path", "bytes", "sha256"}, "DIAGNOSTIC_BINDING_INVALID")
        path = item["path"]
        _expect(path in files and path not in bound and type(item["bytes"]) is int
                and len(files[path]) == item["bytes"] and _sha(files[path]) == item["sha256"],
                "DIAGNOSTIC_BINDING_MISMATCH")
        bound.add(path)
    _expect(bound == set(files) - {"receipt.json"}, "DIAGNOSTIC_UNBOUND_MEMBERS")
    _expect(all(report.get(k) is False for k in ("production_writes", "training_performed", "settlement_performed", "release_allowed",
                                                "purchase_permission", "synthetic_ohlc_created")), "DIAGNOSTIC_FLAGS_CHANGED")
    _expect(len(report["cases"]) == 5 and all(c.get("settlement_allowed") is False
            and c.get("full_day_suspension_automatically_confirmed") is False for c in report["cases"]),
            "DIAGNOSTIC_CASE_FLAGS_CHANGED")
    return files, report


def _diagnostic_pair(files, receipt, day, code):
    _expect(receipt.get("endpoint") == "suspend_d" and receipt.get("status") == "SUSPENSION_EVENTS_PRESENT"
            and receipt.get("network_request_performed") is True and receipt.get("fields") == list(FIELDS),
            "DIAGNOSTIC_REQUEST_INVALID")
    params = receipt["params"]
    _, end = _request(params, day, code)
    _fetched(receipt["fetched_at_utc"], end)
    pairs = receipt["source_files"]
    _expect(type(pairs) is list and len(pairs) == 2, "DIAGNOSTIC_SOURCE_PAIR_INVALID")
    data_member, meta_member = (p["path"] for p in pairs)
    raw, old_meta = files[data_member], _parse(files[meta_member])
    rows = _table(_parse(raw), day, code, params)
    _expect(old_meta.get("diagnostic_only") is True and old_meta.get("data_values_modified") is False
            and old_meta.get("immutable") is True and old_meta.get("source") == "tushare:suspend_d"
            and old_meta.get("data_sha256") == _sha(raw) and old_meta.get("params") == params
            and old_meta.get("fields") == list(FIELDS) and old_meta.get("row_count") == len(rows)
            and receipt.get("row_count") == len(rows)
            and old_meta.get("http_response_sha256") == receipt["http_response_sha256"]
            and old_meta.get("fetched_at_utc") == receipt["fetched_at_utc"], "DIAGNOSTIC_METADATA_CONFLICT")
    origin = {"archive_path": ARCHIVE_PATH, "archive_sha256": DIAGNOSTIC_ZIP_SHA,
              "run_id": DIAGNOSTIC_RUN, "run_commit": DIAGNOSTIC_HEAD,
              "data_member": data_member, "metadata_member": meta_member,
              "request_member": "receipts/" + str(receipt["query_number"]).zfill(2) + ".json",
              "original_diagnostic_flags_unchanged": True}
    meta = _metadata(raw, day, code, params, receipt["fetched_at_utc"], rows,
                     receipt["http_response_sha256"], receipt["http_response_bytes"],
                     "DIAGNOSTIC_TABLE_IMPORT", origin)
    return raw, _json(meta)


def import_diagnostic_bytes(zip_bytes):
    """Return immutable-ready file bytes; do not extract, write or re-fetch.

    Includes the exact original ZIP, preserving all old diagnostic flags. The
    22 per-session pairs retain original full-range table bytes; projecting a
    requested day happens only in session_evidence, never by rewriting rows.
    No absent session between S/R endpoints is inferred or synthesized.
    """
    files, report = _archive(zip_bytes)
    result = {ARCHIVE_PATH: zip_bytes}
    for receipt in report["requests"]:
        if receipt["endpoint"] != "suspend_d":
            continue
        code, params = receipt["params"]["ts_code"], receipt["params"]
        raw = files[receipt["source_files"][0]["path"]]
        rows = _table(_parse(raw), params["start_date"], code, params)
        for day in rows:
            pair = _diagnostic_pair(files, receipt, day, code)
            for path, body in zip(source_paths(Path("."), day, code), pair):
                relative = path.as_posix()
                _expect(relative not in result, "DUPLICATE_IMPORTED_PAIR")
                result[relative] = body
    _expect(len(result) == 45, "DIAGNOSTIC_IMPORT_CASE_SET_CHANGED")
    return result


def _read(root, path, maximum):
    _expect(root in path.resolve().parents and not any(p.is_symlink() for p in (path, *path.parents)),
            "UNSAFE_SOURCE_PATH")
    _expect(path.is_file() and path.stat().st_size <= maximum, "MISSING_OR_OVERSIZED_SOURCE")
    return path.read_bytes()


@dataclass(frozen=True, init=False)
class LoadedSource:
    trade_date: str
    code: str
    rows: Mapping
    evidence_kind: str
    source_files: tuple

    def __init__(self, day, code, rows, kind, bindings, *, _key=None):
        _expect(_key is _LOAD_KEY, "PRELOAD_MUST_BE_CREATED_BY_LOAD")
        values = {"trade_date": day, "code": code, "rows": rows, "evidence_kind": kind,
                  "source_files": tuple(MappingProxyType(dict(b)) for b in bindings)}
        for key, value in values.items():
            object.__setattr__(self, key, value)


def load(root, trade_date, code):
    """Absent pair returns None; any present but bad or mixed source raises."""
    day, code, root = _date(trade_date), _code(code), Path(root)
    _expect(not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_SOURCE_ROOT")
    root = root.resolve(strict=True)
    paths = source_paths(root, day, code)
    # Check even broken aliases before treating a nonexistent pair as absent.
    for path in paths:
        _expect(not any(p.is_symlink() for p in (path, *path.parents)), "UNSAFE_SOURCE_PATH")
    if not any(p.exists() for p in paths):
        return None
    raw, meta_raw = (_read(root, p, MAX_BYTES if i == 0 else 30_000) for i, p in enumerate(paths))
    meta = _parse(meta_raw)
    _expect(type(meta) is dict and _json(meta) == meta_raw, "NONCANONICAL_METADATA")
    request = meta.get("request")
    _expect(type(request) is dict and set(request) == {"api_name", "params", "fields"}
            and request.get("api_name") == "suspend_d" and request.get("fields") == list(FIELDS), "INVALID_REQUEST")
    params = request["params"]
    _, end = _request(params, day, code)
    _fetched(meta.get("fetched_at_utc"), end)
    rows = _table(_parse(raw), day, code, params)
    _sha_value(meta.get("http_response_sha256"))
    _expect(type(meta.get("http_response_bytes")) is int and 0 < meta["http_response_bytes"] <= MAX_BYTES,
            "INVALID_HTTP_BYTE_COUNT")
    kind, origin = meta.get("source_evidence_kind"), meta.get("diagnostic_import")
    _expect(kind in ("RAW_HTTP_TABLE", "DIAGNOSTIC_TABLE_IMPORT"), "INVALID_EVIDENCE_KIND")
    expected = _metadata(raw, day, code, params, meta["fetched_at_utc"], rows,
                         meta["http_response_sha256"], meta["http_response_bytes"], kind, origin)
    _expect(_json(expected) == meta_raw, "SOURCE_METADATA_OR_SHA_MISMATCH")
    bindings = [{"path": p.relative_to(root).as_posix(), "sha256": _sha(b)} for p, b in zip(paths, (raw, meta_raw))]
    if kind == "RAW_HTTP_TABLE":
        _expect(origin is None and _json(_parse(raw)) == raw, "RAW_HTTP_DIAGNOSTIC_OR_CANONICAL_CONFLICT")
    else:
        _expect(type(origin) is dict and origin.get("archive_path") == ARCHIVE_PATH, "INVALID_DIAGNOSTIC_ORIGIN")
        archive_bytes = _read(root, root / ARCHIVE_PATH, MAX_BYTES)
        files, report = _archive(archive_bytes)
        request_member = origin.get("request_member")
        _expect(request_member in files, "DIAGNOSTIC_REQUEST_MEMBER_MISSING")
        receipt = _parse(files[request_member])
        _expect(receipt in report["requests"], "DIAGNOSTIC_REQUEST_NOT_IN_RUN")
        _expect(_diagnostic_pair(files, receipt, day, code) == (raw, meta_raw), "IMPORTED_BYTES_DIFFER_FROM_ORIGIN")
        bindings.append({"path": ARCHIVE_PATH, "sha256": _sha(archive_bytes)})
    return LoadedSource(day, code, rows, kind, bindings, _key=_LOAD_KEY)


def session_evidence(source, trade_date, code):
    """Only event proof. No bool/price/daily callback can authorize advancement."""
    day, code = _date(trade_date), _code(code)
    result = {"source_policy_id": SOURCE_POLICY_ID, "trade_date": day, "ts_code": code,
              "status": "EVENT_NOT_PROVEN", "market_absence_verified": False,
              "can_advance_holding_day": False, "settlement_allowed": False,
              "research_only": True, "production_activation_allowed": False,
              "source_evidence_kind": None, "source_files": []}
    if source is None:
        return result
    _expect(type(source) is LoadedSource and source.trade_date == day and source.code == code,
            "PRELOADED_SOURCE_IDENTITY_CONFLICT")
    result.update(source_evidence_kind=source.evidence_kind,
                  source_files=[dict(b) for b in source.source_files])
    row = source.rows.get(day)
    if row is not None:
        if row["suspend_type"] == "R":
            result["status"] = "RESUMPTION_EVENT_NOT_SUSPENSION"
        elif row["suspend_timing"] in (None, ""):
            result["status"] = "FULL_SESSION_SUSPENSION_EVENT"
        else:
            result["status"] = "INTRADAY_SUSPENSION_EVENT_NOT_FULL_SESSION"
    return result
