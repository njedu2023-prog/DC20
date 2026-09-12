"""Read-only acceptance of a real, isolated STK source-only collection.

This verifies files, frozen scope, request accounting and source replay. It
neither rebuilds labels nor promotes sources to entry/training authority. A
complete source collection can still omit individual frozen candidate rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import auction_stocks_scope as source
from work.profit_1000_upgrade import stocks_scope_inputs as inputs

_LOADED_FILES = {Path(p): inputs._file_sha(Path(p)) for p in (__file__, source.__file__, inputs.__file__)}

SCHEMA = "dc20_research_stocks_scope_collection_20260912_v1"
RECEIPT = "stocks_scope_receipt.json"
JOURNAL = "stocks_scope_requests.jsonl"
MANIFEST = "gap_manifest.json"
BASE = "base_v3.zip"
NEW_CODE = {"work/profit_1000_upgrade/" + n for n in (
    "auction_stocks_scope.py", "stocks_scope_inputs.py", "stocks_scope_collect.py", "STOCKS_SCOPE_COLLECTION.json")}
FLAGS = {"research_only": True, "source_only": True, "source_import_into_labels_performed": False,
         "label_rebuild_performed": False, "training_performed": False, "settlement_performed": False,
         "production_writes": False, "production_activation_allowed": False, "fallback_generated": False,
         "old_source_overwrite_allowed": False, "old_labels_used_as_new_outcomes": False,
         "actual_execution_claimed": False, "raw_response_envelope_saved": False,
         "credential_persisted": False, "forward_holdout_touched": False}
REQUEST_KEYS = set("trade_date request api_calls request_sequence preflight status reason network_request_performed "
                   "callable_injected_for_test http_response_sha256 http_response_bytes source_files rows "
                   "present_candidate_count missing_candidate_codes".split())
RECEIPT_KEYS = set("schema_version status base_archive source_policy_id scope_counts preflight_T_dates preflight_passed "
    "callable_injected_for_test eligible_as_real_collection api_calls max_api_calls retries elapsed_collection_seconds "
    "qualified_new_source_dates qualified_source_dates_including_base new_source_status_counts remaining_gap_dates "
    "requests source_files output_file_bindings execution_file_bindings observed_at_utc run_id run_commit".split()) | set(FLAGS)
NO_CALL = {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "PENDING_CREDENTIAL_ABSENT", "NOT_REQUESTED_BUDGET_EXHAUSTED"}
FAILED_CALL = {"PENDING_NETWORK_OR_RESPONSE_ERROR", "PENDING_INVALID_STOCKS_SOURCE", "PENDING_HTTP_ERROR"}
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 428, 128 * 1024**2, 1024**3


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _equal(actual, expected, reason):
    """JSON equality that does not conflate bool/int or int/float."""
    _require(type(actual) is type(expected), reason)
    if type(expected) is dict:
        _require(set(actual) == set(expected), reason)
        for key in expected:
            _equal(actual[key], expected[key], reason)
    elif type(expected) is list:
        _require(len(actual) == len(expected), reason)
        for left, right in zip(actual, expected):
            _equal(left, right, reason)
    else:
        _require(actual == expected, reason)


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_SHA256")
    return value


def _integer(value, low, high, reason):
    _require(type(value) is int and low <= value <= high, reason)
    return value


def _scan(output):
    root = Path(output)
    _require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_OUTPUT_ROOT")
    root = root.resolve(strict=True)
    files, names, inodes, directories, total = {}, set(), set(), set(), 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda e: e.name)
        for entry in children:
            path = Path(entry.path)
            name = inputs._path(path.relative_to(root).as_posix())
            _require(name.casefold() not in names, "ALIASED_OUTPUT_PATH")
            names.add(name.casefold())
            _require(len(names) <= 3000, "OUTPUT_ENTRY_LIMIT")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(name); pending.append(path)
                continue
            _require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "NONREGULAR_OUTPUT_FILE")
            _require((info.st_dev, info.st_ino) not in inodes, "ALIASED_OUTPUT_FILE")
            inodes.add((info.st_dev, info.st_ino))
            _require(0 <= info.st_size <= MAX_FILE_BYTES, "OUTPUT_FILE_SIZE_LIMIT")
            total += info.st_size
            _require(total <= MAX_TOTAL_BYTES and len(files) < MAX_FILES, "OUTPUT_SIZE_LIMIT")
            files[name] = {"path": name, "sha256": inputs._file_sha(path), "bytes": info.st_size}
    expected_dirs = {p.as_posix() for n in files for p in Path(n).parents if p.as_posix() != "."}
    _require(directories == expected_dirs, "UNREGISTERED_EMPTY_OUTPUT_DIRECTORY")
    return root, {n: files[n] for n in sorted(files)}


def _read(root, name, files, limit=4_000_000):
    _require(name in files and files[name]["bytes"] <= limit, "REQUIRED_DOCUMENT_MISSING_OR_OVERSIZED")
    raw = (root / name).read_bytes()
    _require(len(raw) == files[name]["bytes"] and hashlib.sha256(raw).hexdigest() == files[name]["sha256"],
             "OUTPUT_CHANGED_DURING_READ")
    return raw


def _bindings(items, files):
    _require(type(items) is list, "INVALID_OUTPUT_BINDING_LIST")
    seen = set()
    for item in items:
        _require(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "INVALID_OUTPUT_BINDING")
        name = inputs._path(item["path"])
        _require(name not in seen and name in files, "DUPLICATE_OR_MISSING_OUTPUT_BINDING")
        _sha(item["sha256"])
        _integer(item["bytes"], 0, MAX_FILE_BYTES, "INVALID_OUTPUT_BYTE_COUNT")
        _equal(item, files[name], "OUTPUT_BINDING_MISMATCH")
        seen.add(name)
    return seen


def _manifest(scope):
    frozen = (json.dumps(scope["frozen_manifest"], ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    return {"schema_version": "dc20_stocks_scope_gap_manifest_v1", "base_archive": scope["base_archive"],
            "scope_counts": dict(inputs.COUNTS), "gap_dates": scope["gap_dates"],
            "gap_candidate_codes": scope["gap_candidate_codes"], "reused_dates": scope["reused_dates"],
            "precoverage_dates": scope["precoverage_dates"], "preflight_T_dates": scope["preflight_T_dates"],
            "frozen_manifest_sha256": hashlib.sha256(frozen).hexdigest(), "old_outcomes_used_for_scope": False}


def _contract():
    expected = {"schema_version": SCHEMA, "request_id": "stocks_only_frozen_212_gaps_20260912_v1",
        "base_archive_sha256": inputs.BASE_SHA256, "base_archive_bytes": inputs.BASE_BYTES,
        "base_artifact_id": inputs.BASE_ARTIFACT_ID, "source_policy_id": source.SOURCE_POLICY_ID,
        "scope_counts": dict(inputs.COUNTS), "preflight_T_dates": list(inputs.PREFLIGHT_T_DATES),
        "preflight_rule": "COMPLETE_NONEMPTY_TABLE_WITH_AT_LEAST_ONE_FROZEN_CANDIDATE",
        "api_name": "stk_auction", "params": {"trade_date": "FROZEN_GAP_T_DATE", "ts_type": "STK"},
        "fields": list(source.FIELDS), "max_api_calls": 212, "retries": 0, "timeout_seconds": 20,
        "max_http_response_bytes": 4_000_000, "request_start_budget_seconds": 1200,
        "request_start_headroom_seconds": 20,
        "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE", "max_workers": 2,
        "minimum_request_start_interval_seconds": 1.0, "redirects_allowed": False,
        "pagination_or_filter_fallback_allowed": False,
        "documentation": "https://tushare.pro/document/2?doc_id=369", **FLAGS}
    _equal(inputs._json((HERE / "STOCKS_SCOPE_COLLECTION.json").read_bytes()), expected, "REGISTERED_SOURCE_CONTRACT_CHANGED")


def _request(item, scope, root, files):
    _require(type(item) is dict and set(item) == REQUEST_KEYS, "REQUEST_FIELDS_CHANGED")
    day = item["trade_date"]
    _require(type(day) is str and day in scope["gap_candidate_codes"], "REQUEST_OUTSIDE_FROZEN_GAPS")
    _equal(item["request"], source.request_contract(day), "STK_REQUEST_CHANGED")
    _equal(item["preflight"], day in scope["preflight_T_dates"], "PREFLIGHT_FLAG_CHANGED")
    _equal(item["callable_injected_for_test"], False, "INJECTED_REQUEST_NOT_REAL_EVIDENCE")
    calls = _integer(item["api_calls"], 0, 1, "INVALID_REQUEST_CALL_COUNT")
    _equal(item["network_request_performed"], bool(calls), "NETWORK_CALL_ACCOUNTING_CHANGED")
    status = item["status"]
    _require(type(status) is str and status in NO_CALL | FAILED_CALL | source.STATUSES, "UNKNOWN_REQUEST_STATUS")
    if not calls:
        _require(status in NO_CALL, "UNREQUESTED_SUCCESS_OR_RESPONSE")
        for key in ("request_sequence", "reason", "http_response_sha256", "http_response_bytes", "rows",
                    "present_candidate_count", "missing_candidate_codes"):
            _equal(item[key], None, "UNREQUESTED_RESPONSE_OR_CANDIDATES")
        _equal(item["source_files"], [], "UNREQUESTED_SOURCE_FILES")
        return set()
    _integer(item["request_sequence"], 1, inputs.COUNTS["gap_dates"], "INVALID_REQUEST_SEQUENCE")
    _require(status not in NO_CALL, "NETWORK_CALL_WITH_UNREQUESTED_STATUS")
    response = item["http_response_sha256"] is not None
    if response:
        _sha(item["http_response_sha256"])
        _integer(item["http_response_bytes"], 1, source.MAX_BYTES, "INVALID_HTTP_BYTE_COUNT")
    else:
        _equal(item["http_response_bytes"], None, "UNPAIRED_HTTP_BINDING")
    if status in FAILED_CALL:
        _equal(item["source_files"], [], "FAILED_REQUEST_SOURCE_FILES")
        for key in ("rows", "present_candidate_count", "missing_candidate_codes"):
            _equal(item[key], None, "FAILED_RESPONSE_CANDIDATE_CLAIM")
        if status == "PENDING_INVALID_STOCKS_SOURCE":
            _require(response and type(item["reason"]) is str and re.fullmatch(r"[A-Z0-9_]{1,120}", item["reason"]),
                     "INVALID_SOURCE_FAILURE_RECEIPT")
        elif status == "PENDING_HTTP_ERROR":
            _require(not response and type(item["reason"]) is str and
                     re.fullmatch(r"HTTP_(?:ERROR|[1-5][0-9]{2})", item["reason"]), "INVALID_HTTP_FAILURE_RECEIPT")
        else:
            _equal(item["reason"], None, "UNREGISTERED_FAILURE_REASON")
        return set()
    _require(response, "SOURCE_WITHOUT_HTTP_BINDING")
    _equal(item["reason"], None, "SUCCESS_WITH_FAILURE_REASON")
    expected_paths = [p.relative_to(root).as_posix() for p in source.source_paths(root, day)]
    bound = _bindings(item["source_files"], files)
    _require(bound == set(expected_paths) and [b["path"] for b in item["source_files"]] == expected_paths,
             "SOURCE_PAIR_PATHS_CHANGED")
    loaded = source.load(root, day)
    _equal(item["status"], loaded.status, "SOURCE_STATUS_MISMATCH")
    _equal([{k: b[k] for k in ("path", "sha256")} for b in item["source_files"]],
           [dict(b) for b in loaded.source_files], "REPLAY_SOURCE_BINDINGS_CHANGED")
    _equal(item["rows"], len(loaded.rows), "SOURCE_ROW_COUNT_CHANGED")
    expected = set(scope["gap_candidate_codes"][day])
    present = expected.intersection(loaded.rows)
    _equal(item["present_candidate_count"], len(present), "CANDIDATE_COVERAGE_CHANGED")
    _equal(item["missing_candidate_codes"], sorted(expected - present), "CANDIDATE_MISSINGNESS_CHANGED")
    metadata = inputs._json(_read(root, expected_paths[1], files, 20_000))
    for key in ("http_response_sha256", "http_response_bytes", "rows", "status", "request"):
        _equal(metadata[key], item[key], "SOURCE_HTTP_RECEIPT_MISMATCH")
    return bound


def _preflight(rows, scope, journal):
    by_day = {r["trade_date"]: r for r in rows}
    pre = scope["preflight_T_dates"]
    def passed(r):
        return r["status"] == "STOCKS_SCOPED_TABLE_PRESENT" and r["rows"] > 0 and r["present_candidate_count"] > 0
    failure = next((i for i, day in enumerate(pre) if not passed(by_day[day])), None)
    if failure is None:
        _require([r["trade_date"] for r in journal[:3]] == pre, "PREFLIGHT_START_ORDER_CHANGED")
        _require(not any(r["status"] in {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "PENDING_CREDENTIAL_ABSENT"} for r in rows),
                 "PASSED_PREFLIGHT_HAS_UNATTEMPTED_ROWS")
        return True
    reached = set(pre[:failure + 1])
    _require(by_day[pre[failure]]["status"] != "NOT_REQUESTED_PREFLIGHT_BLOCKED", "PREFLIGHT_NOT_ATTEMPTED")
    _require(all(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" for r in rows if r["trade_date"] not in reached),
             "NETWORK_OR_SOURCE_AFTER_PREFLIGHT_FAILURE")
    expected = [d for d in pre[:failure + 1] if by_day[d]["api_calls"]]
    _equal([r["trade_date"] for r in journal], expected, "PREFLIGHT_JOURNAL_ORDER_CHANGED")
    return False


def verify(output):
    """Return source-only acceptance; all malformed/injected artifacts raise."""
    for path, digest in _LOADED_FILES.items():
        _require(inputs._file_sha(path) == digest, "LOADED_VERIFICATION_CODE_CHANGED")
    _contract()
    root, files = _scan(output)
    _require({BASE, MANIFEST, JOURNAL, RECEIPT} <= files.keys(), "INCOMPLETE_COLLECTION_OUTPUT")
    initial_self = inputs._file_sha(Path(__file__))
    report = inputs._json(_read(root, RECEIPT, files))
    _require(type(report) is dict and set(report) == RECEIPT_KEYS, "RECEIPT_FIELDS_CHANGED")
    _equal(report["schema_version"], SCHEMA, "WRONG_RECEIPT_SCHEMA")
    for key, value in FLAGS.items():
        _equal(report[key], value, "SOURCE_ONLY_AUTHORITY_CHANGED")
    _equal(report["callable_injected_for_test"], False, "INJECTED_COLLECTION_NOT_REAL_EVIDENCE")
    _equal(report["eligible_as_real_collection"], True, "INELIGIBLE_REAL_COLLECTION")
    _equal(report["scope_counts"], inputs.COUNTS, "FROZEN_SCOPE_COUNTS_CHANGED")
    _equal(report["max_api_calls"], 212, "CALL_BUDGET_CHANGED")
    _equal(report["retries"], 0, "RETRY_POLICY_CHANGED")
    _equal(report["source_policy_id"], source.SOURCE_POLICY_ID, "SOURCE_POLICY_CHANGED")
    calls = _integer(report["api_calls"], 0, 212, "INVALID_TOTAL_CALL_COUNT")
    elapsed = report["elapsed_collection_seconds"]
    _require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, "INVALID_ELAPSED_SECONDS")
    try:
        observed = datetime.fromisoformat(report["observed_at_utc"])
        _require(observed.utcoffset() is not None and observed.utcoffset().total_seconds() == 0, "INVALID_OBSERVED_TIME")
    except (TypeError, ValueError):
        raise ValueError("INVALID_OBSERVED_TIME") from None
    for key, pattern in (("run_id", r"[0-9]{1,20}"), ("run_commit", r"[0-9a-f]{40}")):
        value = report[key]
        _require(value is None and calls == 0 or type(value) is str and re.fullmatch(pattern, value), "INVALID_REAL_RUN_IDENTITY")
    code = report["execution_file_bindings"]
    _require(type(code) is dict and set(code) == inputs.CODE_PATHS | NEW_CODE, "EXECUTION_CODE_SET_CHANGED")
    for path, digest in code.items():
        _require(inputs._file_sha(CHECKOUT / path) == _sha(digest), "EXECUTION_CODE_SHA_MISMATCH")
    scope = inputs.read_base_archive(root / BASE)
    _equal(report["base_archive"], scope["base_archive"], "BASE_ARCHIVE_PROVENANCE_CHANGED")
    _equal(report["preflight_T_dates"], scope["preflight_T_dates"], "PREFLIGHT_DATES_CHANGED")
    _equal(inputs._json(_read(root, MANIFEST, files)), _manifest(scope), "GAP_MANIFEST_CHANGED")
    for binding in scope["code_bindings"]:
        _equal(code[binding["path"]], binding["sha256"], "OLD_EXECUTION_CODE_CHANGED")
    rows = report["requests"]
    _require(type(rows) is list and len(rows) == 212 and all(type(r) is dict for r in rows), "REQUEST_UNIVERSE_CHANGED")
    _equal([r.get("trade_date") for r in rows], scope["gap_dates"], "REQUEST_DATE_ORDER_OR_SCOPE_CHANGED")
    sources = set()
    for item in rows:
        bound = _request(item, scope, root, files)
        _require(not sources.intersection(bound), "SOURCE_PAIR_REUSED_ACROSS_DATES")
        sources.update(bound)
    journal = [inputs._json(line) for line in _read(root, JOURNAL, files).splitlines()]
    expected_journal = sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"])
    _equal(journal, expected_journal, "REQUEST_JOURNAL_CHANGED")
    _equal([r["request_sequence"] for r in journal], list(range(1, calls + 1)), "REQUEST_SEQUENCE_OR_TOTAL_CHANGED")
    passed = _preflight(rows, scope, journal)
    _equal(report["preflight_passed"], passed, "PREFLIGHT_RESULT_CHANGED")
    qualified = sum(r["status"] in source.STATUSES for r in rows)
    expected_status = "PREFLIGHT_BLOCKED" if not passed else "SOURCE_GAPS_COLLECTED" if qualified == 212 else "SOURCE_GAPS_PARTIAL"
    _equal(report["status"], expected_status, "COLLECTION_STATUS_CHANGED")
    _equal(report["qualified_new_source_dates"], qualified, "SOURCE_DATE_COUNT_CHANGED")
    _equal(report["qualified_source_dates_including_base"], qualified + 181, "BASE_SOURCE_COUNT_CHANGED")
    _equal(report["new_source_status_counts"], dict(Counter(r["status"] for r in rows)), "STATUS_COUNTS_CHANGED")
    _equal(report["remaining_gap_dates"], [r["trade_date"] for r in rows if r["status"] not in source.STATUSES], "REMAINING_GAPS_CHANGED")
    _require(_bindings(report["source_files"], files) == sources, "AGGREGATE_SOURCE_BINDINGS_CHANGED")
    _equal(report["source_files"], [files[n] for n in sorted(sources)], "AGGREGATE_SOURCE_ORDER_CHANGED")
    expected_files = sources | {BASE, MANIFEST, JOURNAL}
    _require(_bindings(report["output_file_bindings"], files) == expected_files and set(files) == expected_files | {RECEIPT},
             "UNREGISTERED_OR_MISSING_OUTPUT_FILE")
    _equal(report["output_file_bindings"], [files[n] for n in sorted(expected_files)], "OUTPUT_BINDING_ORDER_CHANGED")
    for path, digest in code.items():
        _require(inputs._file_sha(CHECKOUT / path) == digest, "CODE_CHANGED_DURING_ACCEPTANCE")
    _require(inputs._file_sha(Path(__file__)) == initial_self, "VERIFIER_CHANGED_DURING_ACCEPTANCE")
    _equal(_scan(root)[1], files, "OUTPUT_CHANGED_DURING_ACCEPTANCE")
    status = {"SOURCE_GAPS_COLLECTED": "ACCEPTED_SOURCE_ONLY_COMPLETE", "SOURCE_GAPS_PARTIAL": "ACCEPTED_SOURCE_ONLY_PARTIAL",
              "PREFLIGHT_BLOCKED": "ACCEPTED_PREFLIGHT_BLOCKED"}[expected_status]
    return {"schema_version": "dc20_stocks_scope_readonly_acceptance_v1", "status": status,
            "verifier_sha256": initial_self,
            "receipt_sha256": files[RECEIPT]["sha256"], "base_archive_sha256": inputs.BASE_SHA256,
            "api_calls": calls, "preflight_passed": passed, "qualified_new_source_dates": qualified,
            "remaining_gap_dates": report["remaining_gap_dates"], "precoverage_dates": 517, "reused_source_dates": 181,
            "present_candidate_pairs_in_new_sources": sum(r["present_candidate_count"] or 0 for r in rows),
            "missing_candidate_pairs_in_accepted_sources": sum(len(r["missing_candidate_codes"] or []) for r in rows),
            "source_loads_replayed": qualified, "verified_output_files": len(files), "files_written": 0,
            "network_requests": 0, "label_ready": False, "training_ready": False,
            "entry_price_qualification_performed": False, "capacity_qualification_performed": False,
            "actual_execution_claimed": False, "old_labels_recomputed": False, **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.output), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print("STOCKS_SCOPE_ACCEPTANCE_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
