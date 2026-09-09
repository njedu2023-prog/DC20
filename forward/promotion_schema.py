"""Pure promotion/runtime identity validation with no P1 or ML imports.

Extracted without changing the retained staging contract. File reads, inference,
model dependencies, trading, and publication do not occur in this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Mapping

SHA_RE = re.compile(r"[0-9a-f]{64}")
CODE_RE = re.compile(r"(?:(?:600|601|603|605)\d{3}\.SH|(?:000|001|002|003)\d{3}\.SZ)")


class PromotionSchemaError(ValueError):
    """The independent promotion runtime contract is invalid."""


def _require(condition, message):
    if not condition:
        raise PromotionSchemaError(message)


def _digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as exc:
        raise PromotionSchemaError("input must be finite canonical JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def _number(value):
    _require(not isinstance(value, bool) and isinstance(value, (int, float, str)), "invalid numeric input")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PromotionSchemaError("invalid numeric input") from exc
    _require(math.isfinite(number), "nonfinite numeric input")
    return number


def _date(value):
    _require(isinstance(value, str) and re.fullmatch(r"20\d{6}", value), "exact YYYYMMDD required")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise PromotionSchemaError("invalid calendar date") from exc
    return value


def _timestamp(value):
    _require(isinstance(value, str), "timezone-aware timestamp required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionSchemaError("invalid timestamp") from exc
    _require(stamp.utcoffset() is not None, "timestamp must be timezone aware")
    return stamp


def validate_bundle(bundle):
    _require(isinstance(bundle, Mapping), "promotion runtime bundle required")
    required = {"day", "runtime_rows", "runtime_columns", "runtime_sha256", "feature_snapshot_sha256", "receipt"}
    _require(required.issubset(bundle), "promotion runtime bundle is incomplete")
    day = bundle["day"]
    _require(isinstance(day, dict) and day.get("generation_mode") == "REPLAY",
             "profit inference currently permits REPLAY only; production not activated")
    d, t, t1 = [_date(day.get(key)) for key in ("signal_date", "exec_date", "exit_date")]
    _require(d >= "20260824" and d < t < t1, "profit model date scope or D/T/T1 invalid")
    _timestamp(day.get("generated_at_utc"))
    _require(isinstance(day.get("source"), dict) and bool(day["source"]), "promotion immutable sources required")
    rows = day.get("rows")
    _require(isinstance(rows, list) and len(rows) <= 10, "promotion members must be real 0..10")
    codes = []
    for rank, row in enumerate(rows, 1):
        _require(isinstance(row, dict), "invalid promotion member")
        code = row.get("ts_code")
        _require(isinstance(code, str) and CODE_RE.fullmatch(code), "non-main-board or invalid member")
        _require(type(row.get("promotion_rank")) is int and row["promotion_rank"] == rank,
                 "promotion order must remain contiguous and frozen")
        _require(row.get("stage_transition") in {"2→3", "3→4"}, "promotion stage invalid")
        _require(isinstance(row.get("name"), str) and row["name"].strip()
                 and isinstance(row.get("industry"), str), "company identity required")
        _require(0 <= _number(row.get("promotion_probability")) <= 1, "promotion probability out of range")
        codes.append(code)
    _require(len(codes) == len(set(codes)), "duplicate promotion members")
    columns, runtime = bundle["runtime_columns"], bundle["runtime_rows"]
    _require(isinstance(columns, list) and all(isinstance(c, str) and c for c in columns)
             and len(columns) == len(set(columns)), "runtime columns invalid")
    _require(isinstance(runtime, list), "runtime rows must be a JSON list")
    _require(len(rows) == min(10, len(runtime)), "real hard-pool/TopN count drifted")
    _require(all(isinstance(row, dict) and set(row) == set(columns) for row in runtime), "runtime row schema drifted")
    runtime_sha = _digest({"schema_version": "dc20_forward_promotion_runtime_v1",
                           "signal_date": d, "columns": columns, "rows": runtime})
    _require(bundle["runtime_sha256"] == runtime_sha, "runtime canonical source SHA mismatch")
    feature_sha = bundle["feature_snapshot_sha256"]
    _require(isinstance(feature_sha, str) and SHA_RE.fullmatch(feature_sha), "feature snapshot SHA required")
    source, receipt = day["source"], bundle["receipt"]
    _require(source.get("schema_version") == "dc20_forward_promotion_source_v1"
             and source.get("computation_performed") is True
             and source.get("inference_performed") is bool(runtime) and source.get("training_performed") is False
             and source.get("legacy_ranking_read") is False and source.get("production_enabled") is False,
             "requires independently recomputed staging P0 sources")
    _require(source.get("model_sha256") == "b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c",
             "promotion source model identity changed")
    _require(source.get("runtime_sha256") == runtime_sha and source.get("feature_snapshot_sha256") == feature_sha
             and source.get("members_sha256") == _digest({"schema": "dc20_three_rank_member_set_v1",
                                                         "signal_date": d, "members": sorted(codes)}),
             "promotion source member/runtime/feature binding mismatch")
    _require(isinstance(receipt, dict) and receipt.get("day_sha256") == _digest(day)
             and receipt.get("runtime_sha256") == runtime_sha and receipt.get("signal_date") == d
             and receipt.get("selected_count") == len(rows) and receipt.get("promotion_pool_size") == len(runtime)
             and receipt.get("computation_performed") is True
             and receipt.get("inference_performed") is bool(runtime) and receipt.get("training_performed") is False,
             "recomputed promotion receipt mismatch")
    by_code, selected = {}, {}
    for row in runtime:
        code = row.get("ts_code")
        _require(isinstance(code, str) and CODE_RE.fullmatch(code) and code not in by_code,
                 "duplicate or non-main-board runtime code")
        _require(row.get("signal_date") == d and row.get("generated_at_utc") == day["generated_at_utc"]
                 and row.get("feature_snapshot_sha256") == feature_sha, "runtime date/timestamp/freeze mismatch")
        transition = row.get("stage_transition")
        _require(transition in {"2→3", "3→4"} and row.get("identity") == f"{d}|{code}|{transition}",
                 "runtime member identity mismatch")
        flag = _number(row.get("top10_selected"))
        _require(flag in (0, 1), "runtime selection flag invalid")
        by_code[code] = row
        if flag:
            selected[code] = row
    _require(set(selected) == set(codes), "runtime selected membership differs from promotion")
    for row in rows:
        runtime_row = selected[row["ts_code"]]
        _require(all(runtime_row.get(key) == row[key] for key in ("name", "industry", "stage_transition")),
                 "runtime company/stage identity differs from promotion")
        _require(_number(runtime_row.get("promotion_rank")) == row["promotion_rank"]
                 and math.isclose(_number(runtime_row.get("predicted_promotion_probability")),
                                  _number(row["promotion_probability"]), rel_tol=0, abs_tol=1e-15),
                 "runtime promotion rank or probability changed")
    return day, codes, runtime_sha

