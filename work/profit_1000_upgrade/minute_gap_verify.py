"""Independent read-only admission for registered source-only minute artifacts.

The external run identity and both expected hashes are mandatory. The upstream
label report is not included in this artifact: the later typed overlay must
bind its actual bytes and independently derive the identical missing-pair set.
No collection, label rebuilding, training, activation or output writes occur.
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
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import minute_truth as source

SCHEMA = "dc20_research_minute_gap_collection_20260913_v1"
PLAN, RECEIPT, JOURNAL = "registered_minute_gap_plan.json", "minute_gap_receipt.json", "minute_gap_requests.jsonl"
CONTRACT = "MINUTE_GAP_COLLECTION.json"
AS_OF, MIN_DATE, MAX_CALLS = "20260911", "20221101", 5000
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 10003, 32_000_000, 5_200_000_000
DEPENDENCIES = {
    "work/profit_1000_upgrade/minute_truth.py": "69f2eb9fe2ceb1579a59d5247a3f1ddc72b80d251abbed9fe69e06e0d83df268",
    "src/top10decision/decision/shadow_exit_minute_truth.py": "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65"}
EXECUTION_PATHS = set(DEPENDENCIES) | {"work/profit_1000_upgrade/minute_gap_collect.py"}
FLAGS = {"research_only": True, "source_only": True, "label_source_eligible": False,
    "label_rebuild_performed": False, "training_performed": False, "settlement_performed": False,
    "production_writes": False, "production_activation_allowed": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "provider_timestamp_semantics_confirmed": False, "fallback_generated": False,
    "missing_values_imputed": False, "source_rows_deleted": False, "source_values_modified": False,
    "old_sources_overwritten": False, "raw_response_envelope_saved": False,
    "credential_persisted": False, "forward_holdout_touched": False,
    "upstream_label_report_verified_by_collector": False}
REQUEST_KEYS = set("trade_date ts_code request preflight status api_calls request_sequence request_start_elapsed_seconds "
    "network_request_performed http_response_sha256 http_response_bytes source_files source_rows reason".split())
RECEIPT_KEYS = set("schema_version status plan_sha256 label_report_sha256 as_of_date planned_pair_count preflight_pairs "
    "preflight_passed api_calls max_api_calls retries qualified_source_pairs request_status_counts requests source_files "
    "output_file_bindings execution_file_bindings time_semantics callable_injected_for_test eligible_as_real_collection "
    "elapsed_collection_seconds run_id run_commit run_identity_basis observed_at_utc".split()) | set(FLAGS)
SUCCESS = "RESEARCH_MINUTE_SOURCE_WRITTEN"
NO_CALL = {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "NOT_REQUESTED_BUDGET_EXHAUSTED", "PENDING_CREDENTIAL_ABSENT"}
FAILED = {"PENDING_NETWORK_OR_RESPONSE_ERROR", "PENDING_HTTP_ERROR", "PENDING_INVALID_MINUTE_SOURCE"}
_KEY = object()


def require(condition, reason):
    if not condition: raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def equal(actual, expected, reason):
    require(canonical(actual) == canonical(expected), reason)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_SHA256")
    return value


def integer(value, low, high, reason):
    require(type(value) is int and low <= value <= high, reason)
    return value


def number(value, reason):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, reason)
    return value


def path_name(value):
    require(type(value) is str and value and not value.startswith("/") and "\\" not in value
            and all(p not in ("", ".", "..") for p in value.split("/")), "UNSAFE_RELATIVE_PATH")
    return value


def file_sha(path):
    path = Path(path)
    require(".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), "ALIASED_FILE")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGULAR_NONHARDLINK_FILE_REQUIRED")
    with path.open("rb") as handle: return hashlib.file_digest(handle, "sha256").hexdigest()


_LOADED_CODE = {Path(__file__).resolve(): file_sha(__file__)}


def guard():
    for path, digest in {**_LOADED_CODE, **{CHECKOUT / p: d for p, d in DEPENDENCIES.items()}}.items():
        require(file_sha(path) == digest, "MINUTE_VERIFIER_CODE_CHANGED")
    source._verify_normalizer()


def identity(day, code):
    require(type(day) is str and re.fullmatch(r"[0-9]{8}", day) and MIN_DATE <= day <= AS_OF,
            "MINUTE_PAIR_OUTSIDE_FROZEN_WINDOW")
    require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code), "EXACT_MINUTE_STOCK_REQUIRED")
    source._identity(day, code)
    return day, code


def expected_plan(pairs, report_sha):
    """Independently encode the registered contract, not the collector function."""
    sha(report_sha)
    require(type(pairs) is list and 1 <= len(pairs) <= MAX_CALLS
            and all(type(p) is list and len(p) == 2 for p in pairs), "INVALID_MINUTE_PLAN_PAIRS")
    normalized = [identity(*p) for p in pairs]
    require(normalized == sorted(set(normalized)), "MINUTE_PAIRS_NOT_SORTED_UNIQUE")
    preflight = list(dict.fromkeys(normalized[i] for i in (0, len(normalized) // 2, len(normalized) - 1)))
    return {"schema_version": SCHEMA, "as_of_date": AS_OF, "minimum_trade_date": MIN_DATE,
        "label_report_sha256": report_sha, "missing_evidence_kind": "research_exit_1000_1m_0931",
        "scope_basis": "INDEPENDENTLY_REPLAYED_LABEL_MISSING_MINUTE_PAIRS_ONLY",
        "pairs": pairs, "preflight_pairs": [list(p) for p in preflight],
        "preflight_rule": "ALL_UNIQUE_FIRST_MIDDLE_LAST_EXACT_240_BARS_BEFORE_BULK",
        "planned_pair_count": len(normalized), "max_api_calls": MAX_CALLS, "calls_per_pair": 1, "retries": 0,
        "api_name": "stk_mins", "fields": list(source.FIELDS), "query_start": "09:31:00", "query_end": "15:00:00",
        "continuous_rows": 240, "source_schema": source.SOURCE_SCHEMA, "source_adapter": source.ADAPTER,
        "time_semantics": source.TIME_SEMANTICS, "max_http_response_bytes": 1_000_000,
        "socket_timeout_seconds": 20, "max_workers": 4, "minimum_request_start_interval_seconds": .5,
        "request_start_budget_seconds": 4200, "request_start_headroom_seconds": 20,
        "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE",
        "redirects_allowed": False, "pagination_or_request_fallback_allowed": False,
        "frozen_dependencies": dict(DEPENDENCIES), **FLAGS}


def _scan(output):
    root = Path(output)
    require(root.is_dir() and ".." not in root.parts and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_COLLECTION_ROOT")
    root = root.resolve(strict=True)
    require(root != CHECKOUT and root not in CHECKOUT.parents and CHECKOUT not in root.parents, "OUTPUT_INSIDE_CHECKOUT")
    files, names, directories, inodes, pending, total = {}, set(), set(), set(), [root], 0
    while pending:
        with os.scandir(pending.pop()) as entries: children = list(entries)
        for entry in children:
            path = Path(entry.path)
            name = path_name(path.relative_to(root).as_posix())
            require(name.casefold() not in names, "ALIASED_OUTPUT_PATH")
            names.add(name.casefold())
            require(len(names) <= 21000, "OUTPUT_ENTRY_LIMIT")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(name); pending.append(path); continue
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


def _read(root, name, files, limit=32_000_000):
    require(name in files and files[name]["bytes"] <= limit, "MISSING_OR_OVERSIZED_DOCUMENT")
    with (root / name).open("rb") as handle: raw = handle.read(limit + 1)
    require(len(raw) == files[name]["bytes"] and hashlib.sha256(raw).hexdigest() == files[name]["sha256"], "OUTPUT_CHANGED_DURING_READ")
    return raw


def _binding_names(items, files):
    require(type(items) is list, "BINDING_LIST_REQUIRED")
    names = set()
    for item in items:
        require(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "BINDING_FIELDS_CHANGED")
        name = path_name(item["path"])
        require(name not in names and name in files, "DUPLICATE_OR_MISSING_BINDING")
        sha(item["sha256"]); integer(item["bytes"], 0, MAX_FILE_BYTES, "INVALID_BINDING_BYTES")
        equal(item, files[name], "BINDING_CONTENT_CHANGED")
        names.add(name)
    return names


def _utc(value):
    require(type(value) is str, "UTC_TIMESTAMP_REQUIRED")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, "UTC_TIMESTAMP_REQUIRED")
    return stamp


def _request(item, root, files, preflight, observed, planned):
    require(type(item) is dict and set(item) == REQUEST_KEYS, "REQUEST_FIELDS_CHANGED")
    pair = identity(item["trade_date"], item["ts_code"])
    expected = {"api_name": "stk_mins", "params": source.request_parameters(*pair), "fields": list(source.FIELDS)}
    equal(item["request"], expected, "MINUTE_REQUEST_CHANGED")
    equal(item["preflight"], pair in preflight, "PREFLIGHT_FLAG_CHANGED")
    calls = integer(item["api_calls"], 0, 1, "INVALID_REQUEST_CALLS")
    equal(item["network_request_performed"], bool(calls), "NETWORK_ACCOUNTING_CHANGED")
    require(type(item["status"]) is str and item["status"] in NO_CALL | FAILED | {SUCCESS}, "UNKNOWN_REQUEST_STATUS")
    if not calls:
        require(item["status"] in NO_CALL, "UNREQUESTED_RESPONSE")
        for key in ("request_sequence", "request_start_elapsed_seconds", "http_response_sha256", "http_response_bytes", "source_rows", "reason"):
            equal(item[key], None, "UNREQUESTED_RESPONSE_CLAIM")
        equal(item["source_files"], [], "UNREQUESTED_SOURCE_FILES")
        return set()
    integer(item["request_sequence"], 1, planned, "INVALID_REQUEST_SEQUENCE")
    require(number(item["request_start_elapsed_seconds"], "INVALID_REQUEST_TIME") < 4180, "REQUEST_AFTER_ADMISSION_DEADLINE")
    require(item["status"] not in NO_CALL, "REQUESTED_UNREQUESTED_STATUS")
    response = item["http_response_sha256"] is not None
    if response:
        sha(item["http_response_sha256"]); integer(item["http_response_bytes"], 1, 1_000_000, "INVALID_HTTP_BYTES")
    else: equal(item["http_response_bytes"], None, "UNPAIRED_HTTP_BINDING")
    if item["status"] in FAILED:
        equal(item["source_files"], [], "FAILED_REQUEST_SOURCE_FILES")
        equal(item["source_rows"], None, "FAILED_RESPONSE_ROW_CLAIM")
        if item["status"] == "PENDING_INVALID_MINUTE_SOURCE":
            require(type(item["reason"]) is str and item["reason"] in {"HTTP_TRANSPORT_FAILED", "STRICT_RESPONSE_AND_CREDENTIAL_GUARD_FAILED",
                    "FROZEN_EXACT_240_BAR_VALIDATION_FAILED"}, "UNKNOWN_INVALID_SOURCE_REASON")
            require(response or item["reason"] == "HTTP_TRANSPORT_FAILED", "INVALID_SOURCE_WITHOUT_HTTP")
        elif item["status"] == "PENDING_HTTP_ERROR":
            require(not response and type(item["reason"]) is str and re.fullmatch(r"HTTP_(?:ERROR|[1-5][0-9]{2})", item["reason"]), "INVALID_HTTP_FAILURE")
        else: equal(item["reason"], None, "UNSAFE_FAILURE_REASON")
        return set()
    require(response, "SOURCE_WITHOUT_HTTP_BINDING")
    equal(item["reason"], None, "SUCCESS_WITH_FAILURE_REASON")
    equal(item["source_rows"], 240, "SOURCE_ROW_COUNT_CHANGED")
    paths = [p.relative_to(root).as_posix() for p in source.paths(root, *pair)]
    names = _binding_names(item["source_files"], files)
    equal([b["path"] for b in item["source_files"]], paths, "SOURCE_PAIR_PATHS_CHANGED")
    loaded = source.load(root, *pair)
    require(loaded is not None, "SUCCESS_SOURCE_PAIR_ABSENT")
    equal(loaded["source_files"], [{k: b[k] for k in ("path", "sha256")} for b in item["source_files"]], "SOURCE_REPLAY_BINDING_CHANGED")
    require(len(loaded["rows"]) == 240 and loaded["time_semantics"] == source.TIME_SEMANTICS
        and loaded["provider_timestamp_semantics_confirmed"] is False and loaded["research_only"] is True
        and loaded["production_activation_allowed"] is False, "FROZEN_MINUTE_QUALIFICATION_CHANGED")
    meta = source._parse(_read(root, paths[1], files, 16384))
    equal(meta["response_body_sha256"], item["http_response_sha256"], "HTTP_SOURCE_RECEIPT_SHA_CHANGED")
    equal(meta["request"], item["request"]["params"], "SOURCE_REQUEST_RECEIPT_CHANGED")
    require(_utc(meta["fetched_at_utc"]) <= observed, "SOURCE_FETCH_AFTER_RECEIPT")
    return names


def _preflight(rows, journal, preflight):
    by_pair = {(r["trade_date"], r["ts_code"]): r for r in rows}
    failure = next((i for i, pair in enumerate(preflight) if by_pair[pair]["status"] != SUCCESS), None)
    if failure is None:
        equal([(r["trade_date"], r["ts_code"]) for r in journal[:len(preflight)]], preflight, "PREFLIGHT_SEQUENCE_CHANGED")
        require(all(r["status"] not in {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "PENDING_CREDENTIAL_ABSENT"} for r in rows), "PASSED_PREFLIGHT_UNREACHED_REQUESTS")
        return True
    reached = preflight[:failure + 1]
    failed = by_pair[preflight[failure]]
    require(failed["status"] != "NOT_REQUESTED_PREFLIGHT_BLOCKED", "PREFLIGHT_NOT_ATTEMPTED")
    require(all(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" for p, r in by_pair.items() if p not in reached), "REQUEST_AFTER_PREFLIGHT_FAILURE")
    equal([(r["trade_date"], r["ts_code"]) for r in journal], [p for p in reached if by_pair[p]["api_calls"]], "FAILED_PREFLIGHT_JOURNAL_CHANGED")
    if failed["status"] == "PENDING_CREDENTIAL_ABSENT":
        require(failure == 0 and not journal, "CREDENTIAL_STATE_CHANGED_DURING_COLLECTION")
    return False


def _freeze(value):
    if type(value) is dict: return MappingProxyType({key: _freeze(v) for key, v in value.items()})
    if type(value) in (list, tuple): return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True, init=False, slots=True)
class VerifiedMinuteCollection:
    root: Path
    as_of_date: str
    gap_pairs: tuple
    successful_pairs: tuple
    source_bindings: tuple
    receipt_sha256: str
    plan_sha256: str
    label_report_sha256: str
    registered_plan_path: Path
    run_id: str
    run_commit: str
    status: str
    source_only: bool
    label_source_eligible: bool
    plan: MappingProxyType
    _output_bindings: tuple
    _code_bindings: tuple

    def __init__(self, *, _key=None, **values):
        require(_key is _KEY and set(values) == set(self.__dataclass_fields__), "VERIFIED_MINUTE_PRIVATE_CONSTRUCTION")
        for key, value in values.items(): object.__setattr__(self, key, _freeze(value))

    def assert_unchanged(self):
        require(type(self) is VerifiedMinuteCollection, "EXACT_VERIFIED_MINUTE_COLLECTION_REQUIRED")
        guard()
        for path, digest in self._code_bindings:
            require(file_sha(path) == digest, "VERIFIED_MINUTE_CODE_CHANGED")
        require(file_sha(self.registered_plan_path) == self.plan_sha256, "REGISTERED_PLAN_CHANGED")
        _, files = _scan(self.root)
        equal(list(files.values()), [dict(b) for b in self._output_bindings], "VERIFIED_MINUTE_OUTPUT_CHANGED")


def verify(output, *, expected_run_id, expected_run_commit, expected_plan_sha256,
           expected_label_report_sha256, registered_plan_path=HERE / CONTRACT):
    guard(); sha(expected_plan_sha256); sha(expected_label_report_sha256)
    for value, pattern in ((expected_run_id, r"[0-9]{1,20}"), (expected_run_commit, r"[0-9a-f]{40}")):
        require(type(value) is str and re.fullmatch(pattern, value), "EXTERNAL_RUN_IDENTITY_REQUIRED")
    registered = Path(registered_plan_path)
    require(file_sha(registered) == expected_plan_sha256 and registered.stat().st_size <= 2_000_000, "REGISTERED_PLAN_SHA_MISMATCH")
    registered = registered.resolve(strict=True)
    root, files = _scan(output)
    require({PLAN, RECEIPT, JOURNAL} <= set(files), "MISSING_COLLECTION_DOCUMENT")
    raw_plan = _read(root, PLAN, files, 2_000_000)
    require(hashlib.sha256(raw_plan).hexdigest() == expected_plan_sha256 and raw_plan == registered.read_bytes(), "ARTIFACT_PLAN_SHA_MISMATCH")
    plan = source._parse(raw_plan)
    require(type(plan) is dict, "REGISTERED_PLAN_OBJECT_REQUIRED")
    require(raw_plan == canonical(expected_plan(plan.get("pairs"), expected_label_report_sha256)), "REGISTERED_PLAN_CONTRACT_CHANGED")
    pairs, preflight = [tuple(p) for p in plan["pairs"]], [tuple(p) for p in plan["preflight_pairs"]]
    raw_report = _read(root, RECEIPT, files)
    report = source._parse(raw_report)
    require(type(report) is dict and set(report) == RECEIPT_KEYS and raw_report == canonical(report), "RECEIPT_FIELDS_OR_ENCODING_CHANGED")
    fixed = {"schema_version": SCHEMA, "plan_sha256": expected_plan_sha256,
        "label_report_sha256": expected_label_report_sha256, "as_of_date": AS_OF, "planned_pair_count": len(pairs),
        "preflight_pairs": plan["preflight_pairs"], "max_api_calls": MAX_CALLS, "retries": 0,
        "time_semantics": source.TIME_SEMANTICS, "callable_injected_for_test": False, "eligible_as_real_collection": True,
        "run_id": expected_run_id, "run_commit": expected_run_commit,
        "run_identity_basis": "ENVIRONMENT_CLAIMS_REQUIRE_EXTERNAL_VERIFICATION", **FLAGS}
    for key, expected in fixed.items(): equal(report[key], expected, "RECEIPT_POLICY_OR_IDENTITY_CHANGED")
    code = report["execution_file_bindings"]
    require(type(code) is dict and set(code) == EXECUTION_PATHS, "EXECUTION_CODE_SET_CHANGED")
    for path, digest in code.items():
        require(file_sha(CHECKOUT / path) == sha(digest), "EXECUTION_CODE_SHA_CHANGED")
        if path in DEPENDENCIES: equal(digest, DEPENDENCIES[path], "FROZEN_DEPENDENCY_SHA_CHANGED")
    calls = integer(report["api_calls"], 0, len(pairs), "INVALID_TOTAL_CALLS")
    elapsed = number(report["elapsed_collection_seconds"], "INVALID_ELAPSED_TIME")
    observed = _utc(report["observed_at_utc"])
    require(observed >= datetime(2026, 9, 11, 7, tzinfo=timezone.utc), "RECEIPT_BEFORE_FROZEN_AS_OF_CLOSE")
    rows = report["requests"]
    require(type(rows) is list and len(rows) == len(pairs) and all(type(r) is dict for r in rows), "REQUEST_UNIVERSE_CHANGED")
    equal([(r.get("trade_date"), r.get("ts_code")) for r in rows], pairs, "REQUEST_PAIR_ORDER_OR_SCOPE_CHANGED")
    sources = set()
    for item in rows:
        bound = _request(item, root, files, preflight, observed, len(pairs))
        require(not sources.intersection(bound), "SOURCE_REUSED_ACROSS_PAIRS")
        sources.update(bound)
    journal = sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"])
    raw_journal = b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n" for r in journal)
    require(_read(root, JOURNAL, files) == raw_journal, "REQUEST_JOURNAL_CHANGED")
    equal([r["request_sequence"] for r in journal], list(range(1, calls + 1)), "REQUEST_SEQUENCE_OR_TOTAL_CHANGED")
    starts = [r["request_start_elapsed_seconds"] for r in journal]
    require(all(b - a >= .5 - .000001 for a, b in zip(starts, starts[1:]))
            and (not starts or elapsed >= starts[-1]), "GLOBAL_REQUEST_TIMING_CHANGED")
    if any(r["status"] == "NOT_REQUESTED_BUDGET_EXHAUSTED" for r in rows):
        require(elapsed >= 4180 - .000001, "EARLY_BUDGET_EXHAUSTION")
    passed = _preflight(rows, journal, preflight)
    successful = [(r["trade_date"], r["ts_code"]) for r in rows if r["status"] == SUCCESS]
    status = "PREFLIGHT_BLOCKED" if not passed else "MINUTE_GAPS_COLLECTED" if len(successful) == len(pairs) else "MINUTE_GAPS_PARTIAL"
    for key, expected in {"status": status, "preflight_passed": passed, "qualified_source_pairs": len(successful),
                          "request_status_counts": dict(Counter(r["status"] for r in rows))}.items():
        equal(report[key], expected, "COLLECTION_RESULT_CHANGED")
    require(_binding_names(report["source_files"], files) == sources, "AGGREGATE_SOURCE_BINDINGS_CHANGED")
    equal(report["source_files"], [files[name] for name in sorted(sources)], "SOURCE_BINDING_ORDER_CHANGED")
    expected_files = sources | {PLAN, JOURNAL}
    require(set(files) == expected_files | {RECEIPT} and _binding_names(report["output_file_bindings"], files) == expected_files,
            "UNREGISTERED_OR_MISSING_OUTPUT_FILE")
    equal(report["output_file_bindings"], [files[name] for name in sorted(expected_files)], "OUTPUT_BINDING_ORDER_CHANGED")
    result = VerifiedMinuteCollection(_key=_KEY, root=root, as_of_date=AS_OF, gap_pairs=tuple(pairs),
        successful_pairs=tuple(successful), source_bindings=[{k: files[n][k] for k in ("path", "sha256")} for n in sorted(sources)],
        receipt_sha256=files[RECEIPT]["sha256"], plan_sha256=expected_plan_sha256,
        label_report_sha256=expected_label_report_sha256, registered_plan_path=registered,
        run_id=expected_run_id, run_commit=expected_run_commit, status=status, source_only=True,
        label_source_eligible=False, plan=plan, _output_bindings=list(files.values()),
        _code_bindings=tuple({**_LOADED_CODE, **{CHECKOUT / p: d for p, d in code.items()}}.items()))
    result.assert_unchanged()
    return result


def reload_verified(value):
    require(type(value) is VerifiedMinuteCollection, "EXACT_VERIFIED_MINUTE_COLLECTION_REQUIRED")
    value.assert_unchanged()
    return verify(value.root, expected_run_id=value.run_id, expected_run_commit=value.run_commit,
        expected_plan_sha256=value.plan_sha256, expected_label_report_sha256=value.label_report_sha256,
        registered_plan_path=value.registered_plan_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registered-plan", type=Path, default=HERE / CONTRACT)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-label-report-sha256", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-commit", required=True)
    args = parser.parse_args()
    try:
        result = verify(args.output, registered_plan_path=args.registered_plan,
            expected_plan_sha256=args.expected_plan_sha256, expected_label_report_sha256=args.expected_label_report_sha256,
            expected_run_id=args.expected_run_id, expected_run_commit=args.expected_run_commit)
        print(json.dumps({"status": result.status, "successful_pairs": len(result.successful_pairs),
            "gap_pairs": len(result.gap_pairs), "receipt_sha256": result.receipt_sha256, "source_only": True}))
        return 0
    except Exception:
        print("MINUTE_GAP_ACCEPTANCE_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
