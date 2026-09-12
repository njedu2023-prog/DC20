"""Isolated research minute source; never a production or execution approval.

Actual requests are restricted to 09:31--15:00. Every returned row is kept and
must satisfy the existing exact 240 BAR_END-grid validator. Interpretation as
BAR_END is a research assumption, not independently confirmed provider timing.
This module performs no network calls, source writes, or label activation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
NORMALIZER_PATH = HERE.parents[1] / "src/top10decision/decision/shadow_exit_minute_truth.py"
NORMALIZER_SHA = "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65"
SPEC = importlib.util.spec_from_file_location("research_minute_0931_normalizer", NORMALIZER_PATH)
_minute = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(_minute)

SOURCE_SCHEMA = "dc20_research_minute_0931_source_v1"
ADAPTER = "dc20_research_stk_mins_query_0931_v1"
TIME_SEMANTICS = "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED"
SOURCE_ROOT = "research_inputs/minute_truth_0931"
FIELDS = _minute.FIELDS
MAX_RESPONSE_BYTES = 1_000_000
MAX_DATA_BYTES = 1_000_000


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite JSON numeric value")


def _parse(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("malformed research source JSON") from exc


def _identity(trade_date, code):
    if not isinstance(trade_date, str) or re.fullmatch(r"20\d{6}", trade_date) is None:
        raise ValueError("research minute date must be YYYYMMDD")
    datetime.strptime(trade_date, "%Y%m%d")
    if not isinstance(code, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", code) is None:
        raise ValueError("research minute exact stock identity required")


def _verify_normalizer():
    if any(p.is_symlink() for p in (NORMALIZER_PATH, *NORMALIZER_PATH.parents)):
        raise ValueError("aliased strict normalizer")
    if _sha(NORMALIZER_PATH.read_bytes()) != NORMALIZER_SHA:
        raise ValueError("strict normalizer source SHA changed")


def _fetched(value, trade_date):
    if not isinstance(value, str):
        raise ValueError("fetched timestamp must be UTC text")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid fetched timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("fetched timestamp must have an explicit UTC offset")
    close = datetime.strptime(trade_date, "%Y%m%d").replace(hour=7, tzinfo=timezone.utc)
    if stamp < close:
        raise ValueError("complete research minutes cannot be fetched before session close")
    return value


def request_parameters(trade_date, code):
    _identity(trade_date, code)
    day = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    return {"ts_code": code, "freq": "1min", "start_date": day + " 09:31:00", "end_date": day + " 15:00:00"}


def paths(root, trade_date, code):
    _identity(trade_date, code)
    base = Path(root) / SOURCE_ROOT / trade_date[:4] / trade_date / code.replace(".", "_")
    return base.with_suffix(".data.json"), base.with_suffix(".meta.json")


def _data_rows(data, trade_date, code):
    if (not isinstance(data, dict) or not {"fields", "items"} <= set(data)
            or set(data) - {"fields", "items", "has_more", "count"}):
        raise ValueError("research minute source table fields drifted")
    columns, items = data["fields"], data["items"]
    if (not isinstance(columns, list) or len(columns) != len(FIELDS)
            or any(not isinstance(c, str) for c in columns) or set(columns) != set(FIELDS)):
        raise ValueError("research minute source columns drifted")
    if not isinstance(items, list) or len(items) != 240:
        raise ValueError("research query must return exactly 240 rows without filtering")
    if "has_more" in data and (type(data["has_more"]) is not bool or data["has_more"]):
        raise ValueError("research minute pagination is incomplete or invalid")
    if "count" in data:
        count = data["count"]
        # Observed official responses use count=0 as a non-cardinality sentinel.
        if type(count) is not int or count < 0 or (count > 0 and count != len(items)):
            raise ValueError("research minute positive count contradicts returned rows")
    if any(not isinstance(row, list) or len(row) != len(columns) for row in items):
        raise ValueError("research minute source row shape drifted")
    rows = [dict(zip(columns, values)) for values in items]
    # The production validator optionally accepts a flat 09:30 point. This
    # narrower research request never does: no unsolicited row may be removed.
    expected = set(_minute.expected_bar_ends(trade_date))
    if any(not isinstance(row["trade_time"], str) or row["trade_time"] not in expected for row in rows):
        raise ValueError("research minute response is outside exact 09:31 query grid")
    normalized, has_auction = _minute.normalize_source_rows(rows, trade_date, code)
    if has_auction or len(normalized) != 240:
        raise ValueError("research minute grid cannot contain auction or missing rows")
    return normalized


def _metadata(data_raw, data, trade_date, code, body_sha, fetched_at_utc):
    return {"schema_version": SOURCE_SCHEMA, "adapter": ADAPTER,
            "source": "tushare:stk_mins", "endpoint": "stk_mins",
            "trade_date": trade_date, "ts_code": code,
            "request": request_parameters(trade_date, code), "fields": data["fields"],
            "timezone": "Asia/Shanghai", "interval_seconds": 60,
            "time_semantics": TIME_SEMANTICS,
            "provider_timestamp_semantics_confirmed": False,
            "complete_continuous_grid": True, "continuous_rows": 240, "source_rows": 240,
            "response_body_sha256": body_sha, "data_sha256": _sha(data_raw),
            "normalizer_sha256": NORMALIZER_SHA, "fetched_at_utc": fetched_at_utc,
            "encoding": "ORIGINAL_DATA_TABLE_CANONICAL_JSON_NO_ENVELOPE_MESSAGES",
            "source_values_modified": False, "immutable": True, "credential_persisted": False,
            "research_only": True, "production_activation_allowed": False,
            "actual_execution_claimed": False}


def source_bytes(payload, trade_date, code, *, request_params, fetched_at_utc, token=""):
    """Return data/meta bytes without writing them; payload is original HTTP bytes.

    Caller supplies and independently binds the actual HTTP request and network
    receipt, including the credential guard if any. This encoder does not prove
    a network request occurred and does not manufacture a network-success flag.
    Full response bytes are SHA-bound only, because envelope messages can echo
    credentials. The validated data table keeps original field order and values.
    """
    _identity(trade_date, code)
    _verify_normalizer()
    _fetched(fetched_at_utc, trade_date)
    if type(request_params) is not dict or request_params != request_parameters(trade_date, code):
        raise ValueError("actual research minute request must be exact 09:31 to 15:00")
    if not isinstance(token, str):
        raise ValueError("credential guard must be text")
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RESPONSE_BYTES:
        raise ValueError("original bounded HTTP response bytes required")
    response = _parse(payload)
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0:
        raise ValueError("research minute API response was not successful")
    data = response.get("data")
    _data_rows(data, trade_date, code)
    data_raw = _json(data)
    if len(data_raw) > MAX_DATA_BYTES:
        raise ValueError("research minute data exceeds bounded size")
    meta_raw = _json(_metadata(data_raw, data, trade_date, code, _sha(payload), fetched_at_utc))
    if token and any(token.encode() in raw for raw in (data_raw, meta_raw)):
        raise ValueError("credential-like research source forbidden")
    return data_raw, meta_raw


def _safe_root(root):
    root = Path(root)
    if ".." in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("aliased research minute root forbidden")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("research minute root must be a directory")
    return root


def _safe_path(root, path):
    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("aliased or escaping research minute source forbidden")
    if path.exists() and not path.is_file():
        raise ValueError("research minute source must be a regular file")


def load(root, trade_date, code):
    """None only for an absent pair; any present corruption is a hard failure.

    The compatibility payload permits isolated exit-engine experiments. Its
    BAR_END interpretation is explicitly conditional and is not activation.
    """
    _identity(trade_date, code)
    _verify_normalizer()
    root = _safe_root(root)
    data_path, meta_path = paths(root, trade_date, code)
    for path in (data_path, meta_path):
        _safe_path(root, path)
    if not data_path.exists() and not meta_path.exists():
        return None
    if not data_path.is_file() or not meta_path.is_file():
        raise ValueError("research minute data/metadata pair is incomplete")
    if data_path.stat().st_size > MAX_DATA_BYTES or meta_path.stat().st_size > 16_384:
        raise ValueError("research minute source exceeds bounded size")
    data_raw, meta_raw = data_path.read_bytes(), meta_path.read_bytes()
    data, meta = _parse(data_raw), _parse(meta_raw)
    normalized = _data_rows(data, trade_date, code)
    if not isinstance(meta, dict):
        raise ValueError("research minute metadata must be an object")
    body_sha = meta.get("response_body_sha256")
    if not isinstance(body_sha, str) or re.fullmatch(r"[0-9a-f]{64}", body_sha) is None:
        raise ValueError("research minute original body SHA invalid")
    _fetched(meta.get("fetched_at_utc"), trade_date)
    expected = _metadata(data_raw, data, trade_date, code, body_sha, meta["fetched_at_utc"])
    if set(meta) != set(expected) or any(type(meta[key]) is not type(value) or meta[key] != value for key, value in expected.items()):
        raise ValueError("research minute metadata/source/SHA contract mismatch")
    # Enforce the immutable canonical representation written by source_bytes;
    # this rejects duplicate/mutated encodings even if metadata was edited too.
    if data_raw != _json(data) or meta_raw != _json(meta):
        raise ValueError("research minute canonical immutable encoding changed")
    return {"schema_version": _minute.SCHEMA, "ts_code": code, "trade_date": trade_date,
            "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END", "interval_seconds": 60,
            "complete_session": True, "rows": normalized,
            "time_semantics": TIME_SEMANTICS, "provider_timestamp_semantics_confirmed": False,
            "research_only": True, "production_activation_allowed": False,
            "actual_execution_claimed": False,
            "source_files": [{"path": path.relative_to(root).as_posix(), "sha256": _sha(raw)}
                             for path, raw in ((data_path, data_raw), (meta_path, meta_raw))]}
