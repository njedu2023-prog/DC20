"""Independent source-only admission for registered historical daily gaps.

This module never imports the collector, requests prices, rebuilds labels,
or certifies a nontrading day. A complete empty response remains only an
auditable response; advancement/marking/settlement require separate authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from types import MappingProxyType

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 40, 2_000_000, 24_000_000
SCHEMA = "dc20_research_daily_gap_collection_20260913_v1"
SOURCE_SCHEMA = "dc20_research_daily_gap_source_20260913_v1"
SOURCE_ROOT = "research_inputs/daily_gap_sources_v1"
AS_OF = "20260911"
PRIOR_LABEL_SHA = "2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609"
DIAGNOSTIC_ARCHIVE_SHA = "760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9"
DAILY_FIELDS = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg")
SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
PAIRS = (
    ("daily", "20240430", "600083.SH"), ("suspend_d", "20240430", "600083.SH"),
    ("daily", "20250611", "603226.SH"), ("daily", "20250612", "603226.SH"),
    ("daily", "20251118", "603122.SH"), ("daily", "20251119", "603122.SH"),
    ("daily", "20260708", "603580.SH"), ("daily", "20260709", "603580.SH"),
    ("daily", "20260710", "603580.SH"), ("daily", "20260713", "603580.SH"),
    ("daily", "20260724", "002036.SZ"), ("daily", "20260727", "002036.SZ"),
    ("daily", "20260728", "002036.SZ"), ("daily", "20260729", "002036.SZ"),
)
_KEY = object()
CONTRACT = "DAILY_GAP_COLLECTION.json"
PLAN, RECEIPT, JOURNAL = "registered_daily_gap_plan.json", "daily_gap_receipt.json", "daily_gap_requests.jsonl"
EXECUTION_PATHS = {"work/profit_1000_upgrade/daily_gap_collect.py"}
CASES = (("20240425", "20240426", "20240430", "600083.SH"),
         ("20240425", "20240426", "20240429", "600234.SH"),
         ("20250606", "20250609", "20250610", "603226.SH"),
         ("20251113", "20251114", "20251117", "603122.SH"),
         ("20260703", "20260706", "20260707", "603580.SH"),
         ("20260721", "20260722", "20260723", "002036.SZ"))
FLAGS = {"research_only": True, "source_only": True, "label_source_eligible": False,
         "label_rebuild_performed": False, "training_performed": False, "settlement_performed": False,
         "production_writes": False, "production_activation_allowed": False,
         "actual_execution_claimed": False, "actual_capacity_verified": False,
         "nontrading_session_qualified": False, "synthetic_ohlc_created": False,
         "fallback_generated": False, "missing_values_imputed": False,
         "source_values_modified": False, "old_sources_overwritten": False,
         "raw_response_envelope_saved": False, "server_messages_persisted": False,
         "credential_persisted": False, "forward_holdout_touched": False,
         "upstream_label_report_verified_by_collector": False, "diagnostic_archive_imported": False}
SOURCE_FLAGS = {"source_only": True, "immutable": True, "source_values_modified": False,
               "label_source_eligible": False, "nontrading_session_qualified": False,
               "synthetic_ohlc_created": False, "credential_persisted": False,
               "raw_response_envelope_saved": False, "server_messages_persisted": False}
SUCCESS = {"DAILY_EMPTY_SOURCE_WRITTEN", "DAILY_ROW_SOURCE_WRITTEN",
           "SUSPEND_EMPTY_SOURCE_WRITTEN", "SUSPEND_EVENTS_SOURCE_WRITTEN"}
NO_CALL = {"PENDING_CREDENTIAL_ABSENT", "NOT_REQUESTED_WALL_BUDGET"}
FAILURE_REASONS = {"PENDING_TRANSPORT_TIMEOUT": "TRANSPORT_TIMEOUT", "PENDING_HTTP_ERROR": "HTTP_ERROR",
                   "PENDING_TRANSPORT_ERROR": "TRANSPORT_ERROR", "PENDING_HTTP_RESPONSE_BYTE_LIMIT": "HTTP_RESPONSE_BYTE_LIMIT",
                   "PENDING_INVALID_SOURCE": "STRICT_SOURCE_VALIDATION_FAILED"}
REQUEST_KEYS = set("ordinal request status reason api_calls request_sequence request_start_elapsed_seconds "
    "request_finished_elapsed_seconds network_request_performed fetched_at_utc http_response_sha256 "
    "http_response_bytes source_rows source_files".split())
RECEIPT_KEYS = set("schema_version status plan_sha256 label_report_sha256 as_of_date diagnostic_archive planned_request_count "
    "api_calls max_api_calls retries qualified_source_pairs request_status_counts requests source_files output_file_bindings "
    "execution_file_bindings elapsed_collection_seconds run_id run_commit run_attempt run_identity_basis "
    "callable_injected_for_test observed_at_utc".split()) | set(FLAGS)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "DUPLICATE_JSON_MEMBER")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("NONFINITE_JSON_NUMBER")
    def number(value):
        parsed = float(value)
        require(math.isfinite(parsed), "NONFINITE_JSON_NUMBER")
        return parsed
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=number)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("MALFORMED_JSON") from None


def equal(actual, expected, reason):
    require(canonical(actual) == canonical(expected), reason)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_SHA256")
    return value


def integer(value, minimum, maximum, reason):
    require(type(value) is int and minimum <= value <= maximum, reason)
    return value


def number(value, reason):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, reason)
    return value


def relative(value):
    require(type(value) is str and value and not value.startswith("/") and "\\" not in value
            and ":" not in value and all(p not in ("", ".", "..") for p in value.split("/")), "UNSAFE_RELATIVE_PATH")
    return value


def file_sha(path):
    path = Path(path)
    require(".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), "ALIASED_FILE")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGULAR_NONHARDLINK_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _scan(output):
    root = Path(output)
    require(root.is_dir() and ".." not in root.parts and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_COLLECTION_ROOT")
    root = root.resolve(strict=True)
    require(root != CHECKOUT and root not in CHECKOUT.parents and CHECKOUT not in root.parents, "OUTPUT_INSIDE_CHECKOUT")
    files, names, directories, inodes, pending, total = {}, set(), set(), set(), [root], 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            children = list(entries)
        for entry in children:
            path = Path(entry.path)
            name = relative(path.relative_to(root).as_posix())
            require(name.casefold() not in names, "ALIASED_OUTPUT_PATH")
            names.add(name.casefold())
            require(len(names) <= 120, "OUTPUT_ENTRY_LIMIT")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(name)
                pending.append(path)
                continue
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(), "NONREGULAR_OR_HARDLINK_OUTPUT")
            require((info.st_dev, info.st_ino) not in inodes, "REUSED_OUTPUT_INODE")
            inodes.add((info.st_dev, info.st_ino))
            require(0 <= info.st_size <= MAX_FILE_BYTES, "OUTPUT_FILE_SIZE_LIMIT")
            total += info.st_size
            require(len(files) < MAX_FILES and total <= MAX_TOTAL_BYTES, "OUTPUT_TOTAL_SIZE_LIMIT")
            files[name] = {"path": name, "sha256": file_sha(path), "bytes": info.st_size}
    expected_dirs = {p.as_posix() for name in files for p in Path(name).parents if p.as_posix() != "."}
    require(directories == expected_dirs, "EXTRA_EMPTY_DIRECTORY")
    return root, {name: files[name] for name in sorted(files)}


def _read(root, name, files, limit=MAX_FILE_BYTES):
    require(name in files and files[name]["bytes"] <= limit, "MISSING_OR_OVERSIZED_DOCUMENT")
    with (root / name).open("rb") as handle:
        raw = handle.read(limit + 1)
    require(len(raw) == files[name]["bytes"] and hashlib.sha256(raw).hexdigest() == files[name]["sha256"], "OUTPUT_CHANGED_DURING_READ")
    return raw


def _bindings(items, files):
    require(type(items) is list, "BINDING_LIST_REQUIRED")
    names = set()
    for item in items:
        require(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "BINDING_FIELDS_CHANGED")
        name = relative(item["path"])
        require(name not in names and name in files, "DUPLICATE_OR_MISSING_BINDING")
        sha(item["sha256"])
        integer(item["bytes"], 0, MAX_FILE_BYTES, "INVALID_BINDING_BYTES")
        equal(item, files[name], "BINDING_CONTENT_CHANGED")
        names.add(name)
    return names


def _utc(value):
    require(type(value) is str, "UTC_TIMESTAMP_REQUIRED")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, "UTC_TIMESTAMP_REQUIRED")
    return stamp


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(v) for key, v in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(v) for v in value)
    return value


_LOADED_CODE = {Path(__file__).resolve(): file_sha(__file__)}


def guard():
    for path, digest in _LOADED_CODE.items():
        require(file_sha(path) == digest, "DAILY_VERIFIER_CODE_CHANGED")


def request_contract(api, day, code):
    require((api, day, code) in PAIRS, "REQUEST_OUTSIDE_REGISTERED_SCOPE")
    return {"api_name": api, "params": {"ts_code": code, "trade_date": day},
            "fields": list(DAILY_FIELDS if api == "daily" else SUSPEND_FIELDS)}


def expected_plan():
    """Encode the independently fixed fourteen requests, not caller metadata."""
    return {"schema_version": SCHEMA, "source_schema": SOURCE_SCHEMA,
        "label_report_sha256": PRIOR_LABEL_SHA, "as_of_date": AS_OF, "maximum_signal_date": "20260814",
        "diagnostic_archive": {"sha256": DIAGNOSTIC_ARCHIVE_SHA, "bytes": 19600,
            "run_id": "34672430723", "run_commit": "6ea9616014da3850ae4064c89e56e832608180d3"},
        "missing_daily_cases": [{"signal_date": d, "exec_date": t, "missing_date": day, "ts_code": code}
                                for d, t, day, code in CASES],
        "requests": [{"ordinal": i, **request_contract(*pair)} for i, pair in enumerate(PAIRS, 1)],
        "planned_request_count": 14, "endpoint_counts": {"daily": 13, "suspend_d": 1},
        "max_api_calls": 14, "calls_per_request": 1, "retries": 0, "max_workers": 1,
        "minimum_request_start_interval_seconds": .5, "socket_timeout_seconds": 20,
        "transport_wall_timeout_seconds": 20, "request_start_budget_seconds": 600,
        "request_start_headroom_seconds": 21,
        "transport_timeout_semantics": "PARENT_PROCESS_RESPONSE_DEADLINE_PLUS_SOCKET_TIMEOUT",
        "max_http_response_bytes": 1_000_000, "max_rows_per_exact_query": 1,
        "redirects_allowed": False, "pagination_or_request_fallback_allowed": False,
        "allowed_run_attempt": 1, "source_namespace": SOURCE_ROOT,
        "daily_empty_rule": "RETAIN_EXACT_QUERY_EVIDENCE_NOT_AUTOMATIC_SUSPENSION",
        "suspend_event_rule": "RETAIN_EXACT_DATE_CODE_EVENT_NOT_NONTRADING_ADMISSION",
        "scope_basis": "FROZEN_SIX_MISSING_DAILY_CASES_AND_EXPLICIT_DIAGNOSTIC_S_DATES", **FLAGS}


def source_paths(pair):
    api, day, code = pair
    request_contract(*pair)
    stem = f"{SOURCE_ROOT}/{api}/{day[:4]}/{day}/{code}"
    return stem + ".data.json", stem + ".meta.json"


def _numeric(value, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), "INVALID_SOURCE_NUMBER")
    require(not positive or value > 0, "NONPOSITIVE_DAILY_PRICE")
    return value


def _table(data, pair):
    api, day, code = pair
    fields = list(DAILY_FIELDS if api == "daily" else SUSPEND_FIELDS)
    require(type(data) is dict and set(data) == {"fields", "items", "count", "has_more"}
            and type(data["fields"]) is list and data["fields"] == fields
            and type(data["items"]) is list and len(data["items"]) <= 1, "INVALID_EXACT_SOURCE_TABLE")
    require(type(data["count"]) is int and data["count"] in (0, len(data["items"]))
            and data["has_more"] is False, "INCOMPLETE_OR_PAGINATED_SOURCE_TABLE")
    rows = []
    for item in data["items"]:
        require(type(item) is list and len(item) == len(fields), "INVALID_SOURCE_ROW")
        row = dict(zip(fields, item))
        require(row["trade_date"] == day and row["ts_code"] == code, "SOURCE_DATE_CODE_MISMATCH")
        if api == "daily":
            for key in ("open", "high", "low", "close", "pre_close"):
                _numeric(row[key], positive=True)
            require(row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"],
                    "INVALID_DAILY_OHLC")
            require(_numeric(row["vol"]) >= 0 and _numeric(row["amount"]) >= 0, "NEGATIVE_DAILY_ACTIVITY")
            _numeric(row["pct_chg"])
        else:
            require(row["suspend_type"] in ("S", "R"), "INVALID_SUSPEND_EVENT")
            timing = row["suspend_timing"]
            if timing not in (None, ""):
                require(type(timing) is str and re.fullmatch(r"[0-9]{2}:[0-9]{2}-[0-9]{2}:[0-9]{2}", timing),
                        "INVALID_SUSPEND_TIMING")
                start, end = (datetime.strptime(v, "%H:%M") for v in timing.split("-"))
                require(start < end, "INVALID_SUSPEND_TIMING")
        rows.append(row)
    status = ("DAILY_ROW_SOURCE_WRITTEN" if rows else "DAILY_EMPTY_SOURCE_WRITTEN") if api == "daily" else (
        "SUSPEND_EVENTS_SOURCE_WRITTEN" if rows else "SUSPEND_EMPTY_SOURCE_WRITTEN")
    return rows, status


def _source_record(item, pair, root, files, observed):
    paths = source_paths(pair)
    require(_bindings(item["source_files"], files) == set(paths), "SUCCESS_SOURCE_PAIR_CHANGED")
    equal([b["path"] for b in item["source_files"]], list(paths), "SOURCE_PAIR_ORDER_CHANGED")
    data_raw, meta_raw = _read(root, paths[0], files, 1_000_000), _read(root, paths[1], files, 30_000)
    data, meta = parse(data_raw), parse(meta_raw)
    require(data_raw == canonical(data), "SOURCE_TABLE_ENCODING_CHANGED")
    rows, status = _table(data, pair)
    equal(item["status"], status, "SOURCE_STATUS_DIFFERS_FROM_TABLE")
    equal(item["source_rows"], len(rows), "SOURCE_ROW_COUNT_CHANGED")
    fetched = _utc(item["fetched_at_utc"])
    cutoff = datetime.strptime(pair[1], "%Y%m%d").replace(hour=7, tzinfo=timezone.utc)
    require(cutoff <= fetched <= observed, "SOURCE_FETCH_OUTSIDE_COMPLETED_EVIDENCE_WINDOW")
    expected = {"schema_version": SOURCE_SCHEMA, "ordinal": item["ordinal"], "request": item["request"],
        "fetched_at_utc": item["fetched_at_utc"], "api_code": 0,
        "http_response_sha256": item["http_response_sha256"], "http_response_bytes": item["http_response_bytes"],
        "data_sha256": hashlib.sha256(data_raw).hexdigest(), "data_bytes": len(data_raw), "rows": len(rows),
        "status": status, "network_request_performed": True, "complete_table_verified": True, **SOURCE_FLAGS}
    require(type(meta) is dict and meta_raw == canonical(expected), "SOURCE_METADATA_HTTP_REQUEST_OR_SHA_CHANGED")
    return {"ordinal": item["ordinal"], "api_name": pair[0], "trade_date": pair[1], "ts_code": pair[2],
        "request": item["request"], "status": status, "complete_empty": not rows, "table": data, "rows": rows,
        "source_files": [{k: b[k] for k in ("path", "sha256")} for b in item["source_files"]],
        "http_response_sha256": item["http_response_sha256"], "http_response_bytes": item["http_response_bytes"],
        "fetched_at_utc": item["fetched_at_utc"], "source_only": True, "label_source_eligible": False,
        "nontrading_session_qualified": False, "market_absence_verified": False,
        "can_advance_holding_day": False, "settlement_allowed": False}


def _request(item, ordinal, root, files, observed, elapsed):
    require(type(item) is dict and set(item) == REQUEST_KEYS, "REQUEST_FIELDS_CHANGED")
    pair = PAIRS[ordinal - 1]
    equal(item["ordinal"], ordinal, "REQUEST_ORDINAL_CHANGED")
    equal(item["request"], request_contract(*pair), "EXACT_REGISTERED_REQUEST_CHANGED")
    calls = integer(item["api_calls"], 0, 1, "INVALID_REQUEST_CALLS")
    equal(item["network_request_performed"], bool(calls), "NETWORK_ACCOUNTING_CHANGED")
    status = item["status"]
    require(type(status) is str and status in SUCCESS | NO_CALL | set(FAILURE_REASONS), "UNKNOWN_REQUEST_STATUS")
    if not calls:
        require(status in NO_CALL, "UNREQUESTED_RESPONSE")
        for key in ("reason", "request_sequence", "request_start_elapsed_seconds", "request_finished_elapsed_seconds",
                    "fetched_at_utc", "http_response_sha256", "http_response_bytes", "source_rows"):
            equal(item[key], None, "UNREQUESTED_RESPONSE_CLAIM")
        equal(item["source_files"], [], "UNREQUESTED_SOURCE_FILES")
        return None
    require(status not in NO_CALL, "REQUESTED_UNREQUESTED_STATUS")
    integer(item["request_sequence"], 1, 14, "INVALID_REQUEST_SEQUENCE")
    started = number(item["request_start_elapsed_seconds"], "INVALID_REQUEST_START_TIME")
    finished = number(item["request_finished_elapsed_seconds"], "INVALID_REQUEST_FINISH_TIME")
    require(started < 579 and started <= finished <= elapsed, "REQUEST_OUTSIDE_WALL_BUDGET")
    response = item["http_response_sha256"] is not None
    if response:
        sha(item["http_response_sha256"])
        integer(item["http_response_bytes"], 1, 1_000_000, "INVALID_HTTP_BYTES")
        require(_utc(item["fetched_at_utc"]) <= observed, "HTTP_FETCH_AFTER_RECEIPT")
    else:
        equal(item["http_response_bytes"], None, "UNPAIRED_HTTP_BINDING")
        equal(item["fetched_at_utc"], None, "FETCH_WITHOUT_HTTP_RESPONSE")
    if status in FAILURE_REASONS:
        equal(item["reason"], FAILURE_REASONS[status], "UNSAFE_FAILURE_REASON")
        equal(item["source_rows"], None, "FAILED_RESPONSE_ROW_CLAIM")
        equal(item["source_files"], [], "FAILED_REQUEST_SOURCE_FILES")
        if status not in {"PENDING_INVALID_SOURCE", "PENDING_TRANSPORT_ERROR"}:
            require(not response, "TRANSPORT_FAILURE_WITH_RESPONSE")
        return None
    require(response, "SUCCESS_WITHOUT_HTTP_RESPONSE")
    equal(item["reason"], None, "SUCCESS_WITH_FAILURE_REASON")
    return _source_record(item, pair, root, files, observed)


@dataclass(frozen=True, init=False, slots=True)
class VerifiedDailyGapCollection:
    root: Path
    as_of_date: str
    gap_pairs: tuple
    successful_pairs: tuple
    failed_pairs: tuple
    unattempted_pairs: tuple
    source_bindings: tuple
    records: MappingProxyType
    receipt_sha256: str
    plan_sha256: str
    prior_label_report_sha256: str
    diagnostic_archive_sha256: str
    registered_plan_path: Path
    run_id: str
    run_commit: str
    status: str
    source_only: bool
    label_source_eligible: bool
    nontrading_session_qualified: bool
    plan: MappingProxyType
    _output_bindings: tuple
    _code_bindings: tuple

    def __init__(self, *, _key=None, **values):
        require(_key is _KEY and set(values) == set(self.__dataclass_fields__), "VERIFIED_DAILY_PRIVATE_CONSTRUCTION")
        for key, value in values.items():
            object.__setattr__(self, key, _freeze(value))

    def assert_unchanged(self):
        require(type(self) is VerifiedDailyGapCollection, "EXACT_VERIFIED_DAILY_COLLECTION_REQUIRED")
        guard()
        for path, digest in self._code_bindings:
            require(file_sha(path) == digest, "VERIFIED_DAILY_CODE_CHANGED")
        require(file_sha(self.registered_plan_path) == self.plan_sha256, "REGISTERED_PLAN_CHANGED")
        _, files = _scan(self.root)
        equal(list(files.values()), [dict(b) for b in self._output_bindings], "VERIFIED_DAILY_OUTPUT_CHANGED")


def verify(output, *, expected_run_id, expected_run_commit, expected_plan_sha256,
           expected_label_report_sha256, registered_plan_path=HERE / CONTRACT):
    guard()
    sha(expected_plan_sha256)
    require(expected_label_report_sha256 == PRIOR_LABEL_SHA, "FROZEN_PRIOR_LABEL_SHA_CHANGED")
    for value, pattern in ((expected_run_id, r"[0-9]{1,20}"), (expected_run_commit, r"[0-9a-f]{40}")):
        require(type(value) is str and re.fullmatch(pattern, value), "EXTERNAL_RUN_IDENTITY_REQUIRED")
    registered = Path(registered_plan_path)
    require(file_sha(registered) == expected_plan_sha256 and registered.stat().st_size <= 100_000, "REGISTERED_PLAN_SHA_MISMATCH")
    registered = registered.resolve(strict=True)
    root, files = _scan(output)
    require({PLAN, RECEIPT, JOURNAL} <= set(files), "MISSING_COLLECTION_DOCUMENT")
    raw_plan = _read(root, PLAN, files, 100_000)
    require(hashlib.sha256(raw_plan).hexdigest() == expected_plan_sha256 and raw_plan == registered.read_bytes(), "ARTIFACT_PLAN_SHA_MISMATCH")
    plan = parse(raw_plan)
    require(type(plan) is dict and raw_plan == canonical(expected_plan()), "REGISTERED_PLAN_CONTRACT_CHANGED")
    raw_report = _read(root, RECEIPT, files)
    report = parse(raw_report)
    require(type(report) is dict and set(report) == RECEIPT_KEYS and raw_report == canonical(report), "RECEIPT_FIELDS_OR_ENCODING_CHANGED")
    fixed = {"schema_version": SCHEMA, "plan_sha256": expected_plan_sha256, "label_report_sha256": PRIOR_LABEL_SHA,
        "as_of_date": AS_OF, "diagnostic_archive": plan["diagnostic_archive"], "planned_request_count": 14,
        "max_api_calls": 14, "retries": 0, "run_id": expected_run_id, "run_commit": expected_run_commit,
        "run_attempt": 1, "run_identity_basis": "ENVIRONMENT_CLAIMS_REQUIRE_EXTERNAL_VERIFICATION",
        "callable_injected_for_test": False, **FLAGS}
    for key, expected in fixed.items():
        equal(report[key], expected, "RECEIPT_POLICY_OR_EXTERNAL_IDENTITY_CHANGED")
    code = report["execution_file_bindings"]
    require(type(code) is dict and set(code) == EXECUTION_PATHS, "EXECUTION_CODE_SET_CHANGED")
    for name, digest in code.items():
        require(file_sha(CHECKOUT / name) == sha(digest), "EXECUTION_CODE_SHA_CHANGED")
    calls = integer(report["api_calls"], 0, 14, "INVALID_TOTAL_CALLS")
    elapsed = number(report["elapsed_collection_seconds"], "INVALID_ELAPSED_TIME")
    observed = _utc(report["observed_at_utc"])
    require(observed >= datetime(2026, 9, 11, 7, tzinfo=timezone.utc), "RECEIPT_BEFORE_AS_OF_CLOSE")
    rows = report["requests"]
    require(type(rows) is list and len(rows) == 14, "FULL_REQUEST_UNIVERSE_REQUIRED")
    records, source_names = {}, set()
    for ordinal, item in enumerate(rows, 1):
        record = _request(item, ordinal, root, files, observed, elapsed)
        if record is not None:
            records[PAIRS[ordinal - 1]] = record
            bound = {b["path"] for b in record["source_files"]}
            require(not source_names.intersection(bound), "SOURCE_REUSED_ACROSS_REQUESTS")
            source_names.update(bound)
    called = [r for r in rows if r["api_calls"]]
    require([r["ordinal"] for r in called] == [r["request_sequence"] for r in called] == list(range(1, calls + 1)),
            "SEQUENTIAL_REQUEST_PREFIX_OR_TOTAL_CHANGED")
    raw_journal = b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n" for r in called)
    require(_read(root, JOURNAL, files) == raw_journal, "REQUEST_JOURNAL_CHANGED")
    require(all(right["request_start_elapsed_seconds"] - left["request_start_elapsed_seconds"] >= .5 - .000001
                and right["request_start_elapsed_seconds"] + .000001 >= left["request_finished_elapsed_seconds"]
                for left, right in zip(called, called[1:])), "GLOBAL_SEQUENTIAL_TIMING_CHANGED")
    if any(r["status"] == "PENDING_CREDENTIAL_ABSENT" for r in rows):
        require(calls == 0 and all(r["status"] == "PENDING_CREDENTIAL_ABSENT" for r in rows), "CREDENTIAL_STATE_CHANGED")
    elif calls < 14:
        require(elapsed >= 579 - .000001 and all(r["status"] == "NOT_REQUESTED_WALL_BUDGET" for r in rows[calls:]),
                "EARLY_OR_INCONSISTENT_WALL_BUDGET_EXHAUSTION")
    success = tuple(pair for pair in PAIRS if pair in records)
    status = "DAILY_GAP_SOURCES_COLLECTED" if len(success) == 14 else "DAILY_GAP_SOURCES_PARTIAL" if success else "DAILY_GAP_SOURCES_BLOCKED"
    for key, expected in {"status": status, "qualified_source_pairs": len(success),
                          "request_status_counts": dict(Counter(r["status"] for r in rows))}.items():
        equal(report[key], expected, "COLLECTION_RESULT_CHANGED")
    require(_bindings(report["source_files"], files) == source_names, "AGGREGATE_SOURCE_BINDINGS_CHANGED")
    equal(report["source_files"], [files[p] for p in sorted(source_names)], "SOURCE_BINDING_ORDER_CHANGED")
    expected_files = source_names | {PLAN, JOURNAL}
    require(set(files) == expected_files | {RECEIPT} and _bindings(report["output_file_bindings"], files) == expected_files,
            "UNREGISTERED_OR_MISSING_OUTPUT_FILE")
    equal(report["output_file_bindings"], [files[p] for p in sorted(expected_files)], "OUTPUT_BINDING_ORDER_CHANGED")
    result = VerifiedDailyGapCollection(_key=_KEY, root=root, as_of_date=AS_OF, gap_pairs=PAIRS,
        successful_pairs=success, failed_pairs=tuple(p for p in PAIRS if p not in records),
        unattempted_pairs=tuple(PAIRS[i] for i, r in enumerate(rows) if not r["api_calls"]),
        source_bindings=[{k: files[p][k] for k in ("path", "sha256")} for p in sorted(source_names)], records=records,
        receipt_sha256=files[RECEIPT]["sha256"], plan_sha256=expected_plan_sha256,
        prior_label_report_sha256=PRIOR_LABEL_SHA, diagnostic_archive_sha256=DIAGNOSTIC_ARCHIVE_SHA,
        registered_plan_path=registered, run_id=expected_run_id, run_commit=expected_run_commit, status=status,
        source_only=True, label_source_eligible=False, nontrading_session_qualified=False, plan=plan,
        _output_bindings=list(files.values()),
        _code_bindings=tuple({**_LOADED_CODE, **{CHECKOUT / name: digest for name, digest in code.items()}}.items()))
    result.assert_unchanged()
    return result


def reload_verified(value):
    require(type(value) is VerifiedDailyGapCollection, "EXACT_VERIFIED_DAILY_COLLECTION_REQUIRED")
    value.assert_unchanged()
    return verify(value.root, expected_run_id=value.run_id, expected_run_commit=value.run_commit,
        expected_plan_sha256=value.plan_sha256, expected_label_report_sha256=value.prior_label_report_sha256,
        registered_plan_path=value.registered_plan_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registered-plan", type=Path, default=HERE / CONTRACT)
    for name in ("expected-run-id", "expected-run-commit", "expected-plan-sha256", "expected-label-report-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        value = verify(args.output, registered_plan_path=args.registered_plan,
            expected_run_id=args.expected_run_id, expected_run_commit=args.expected_run_commit,
            expected_plan_sha256=args.expected_plan_sha256, expected_label_report_sha256=args.expected_label_report_sha256)
        print(json.dumps({"status": value.status, "successful_pairs": len(value.successful_pairs),
            "failed_pairs": len(value.failed_pairs), "receipt_sha256": value.receipt_sha256,
            "source_only": True, "nontrading_session_qualified": False}))
        return 0
    except Exception:
        print("DAILY_GAP_ACCEPTANCE_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
