"""Read-only candidate replay; no collector invocation, labels or network.

Any actual requests require externally verified run id/commit supplied by the
caller, not copied from this receipt. The guarded result authorizes only source
provenance; a later explicit overlay must separately qualify its labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import re
import stat
import sys
from types import MappingProxyType
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import auction_candidate_scope as source
from work.profit_1000_upgrade import stocks_scope_inputs as inputs

SCHEMA = "dc20_candidate_auction_collection_20260913_v1"
RECEIPT, JOURNAL, MANIFEST, BASE = "candidate_scope_receipt.json", "candidate_scope_requests.jsonl", "gap_manifest.json", "base_v3.zip"
CONTRACT = "CANDIDATE_AUCTION_COLLECTION.json"
PREFLIGHT = (("20250319", "000612.SZ"), ("20260203", "000995.SZ"))
MAX_CALLS, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 1860, 3724, 128 * 1024**2, 8 * 1024**3
NEW_CODE = {"work/profit_1000_upgrade/" + name for name in
            ("auction_candidate_scope.py", "stocks_scope_inputs.py", "candidate_scope_collect.py", CONTRACT)}
FLAGS = {"research_only": True, "source_only": True, "label_rebuild_performed": False,
         "source_import_into_labels_performed": False, "training_performed": False,
         "settlement_performed": False, "production_writes": False, "production_activation_allowed": False,
         "fallback_generated": False, "old_source_overwrite_allowed": False, "old_outcomes_used_for_scope": False,
         "actual_execution_claimed": False, "raw_response_envelope_saved": False,
         "credential_persisted": False, "forward_holdout_touched": False}
REQUEST_KEYS = set("trade_date ts_code request preflight api_calls request_sequence status reason network_request_performed "
                   "http_response_sha256 http_response_bytes source_files rows".split())
RECEIPT_KEYS = set("schema_version status base_archive source_policy_id scope_counts preflight_pairs preflight_passed "
    "eligible_as_real_collection callable_injected_for_test api_calls max_api_calls retries elapsed_collection_seconds "
    "qualified_new_source_pairs reused_base_source_dates new_source_status_counts requests source_files output_file_bindings "
    "execution_file_bindings observed_at_utc run_id run_commit".split()) | set(FLAGS)
NO_CALL = {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "NOT_REQUESTED_BUDGET_EXHAUSTED", "PENDING_CREDENTIAL_ABSENT"}
FAILED = {"PENDING_INVALID_CANDIDATE_SOURCE", "PENDING_HTTP_ERROR", "PENDING_NETWORK_OR_RESPONSE_ERROR"}
_LOADED_CODE = {Path(p): inputs._file_sha(Path(p)) for p in (__file__, source.__file__, inputs.__file__)}
_KEY = object()


def require(condition, reason):
    if not condition: raise ValueError(reason)


def equal(actual, expected, reason):
    require(source.codec._json(actual) == source.codec._json(expected), reason)


def integer(value, low, high, reason):
    require(type(value) is int and low <= value <= high, reason)
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_SHA256")
    return value


def _freeze(value):
    if type(value) is dict: return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple): return tuple(_freeze(item) for item in value)
    return value


def _scan(output):
    root = Path(output)
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "UNSAFE_COLLECTION_ROOT")
    root = root.resolve(strict=True)
    files, names, inodes, directories, pending, total = {}, set(), set(), set(), [root], 0
    while pending:
        with os.scandir(pending.pop()) as entries: children = list(entries)
        for entry in children:
            path = Path(entry.path)
            name = inputs._path(path.relative_to(root).as_posix())
            require(name.casefold() not in names, "ALIASED_OUTPUT_PATH")
            names.add(name.casefold())
            require(len(names) <= 12_000, "OUTPUT_ENTRY_LIMIT")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(name); pending.append(path); continue
            require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "NONREGULAR_OUTPUT_FILE")
            require((info.st_dev, info.st_ino) not in inodes, "ALIASED_OUTPUT_FILE")
            inodes.add((info.st_dev, info.st_ino))
            require(0 <= info.st_size <= MAX_FILE_BYTES, "OUTPUT_FILE_SIZE_LIMIT")
            total += info.st_size
            require(total <= MAX_TOTAL_BYTES and len(files) < MAX_FILES, "OUTPUT_SIZE_LIMIT")
            files[name] = {"path": name, "sha256": inputs._file_sha(path), "bytes": info.st_size}
    expected = {p.as_posix() for name in files for p in Path(name).parents if p.as_posix() != "."}
    require(directories == expected, "UNREGISTERED_EMPTY_DIRECTORY")
    return root, {name: files[name] for name in sorted(files)}


def _read(root, name, files, limit=16_000_000):
    require(name in files and files[name]["bytes"] <= limit, "MISSING_OR_OVERSIZED_DOCUMENT")
    with (root / name).open("rb") as handle: raw = handle.read(limit + 1)
    require(len(raw) == files[name]["bytes"] and source.codec._sha(raw) == files[name]["sha256"], "OUTPUT_CHANGED_DURING_READ")
    return raw


def _bindings(items, files):
    require(type(items) is list, "INVALID_BINDING_LIST")
    names = set()
    for item in items:
        require(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "INVALID_FILE_BINDING")
        name = inputs._path(item["path"])
        require(name not in names and name in files, "DUPLICATE_OR_MISSING_BINDING")
        sha(item["sha256"]); integer(item["bytes"], 0, MAX_FILE_BYTES, "INVALID_BOUND_BYTES")
        equal(item, files[name], "FILE_BINDING_MISMATCH")
        names.add(name)
    return names


def _pairs(scope):
    pairs = sorted((day, code) for day in scope["gap_dates"] for code in scope["gap_candidate_codes"][day])
    require(len(pairs) == len(set(pairs)) == MAX_CALLS and set(PREFLIGHT) <= set(pairs), "FROZEN_PAIR_SCOPE_CHANGED")
    return pairs


def _manifest(scope):
    return {"schema_version": "dc20_candidate_auction_gap_manifest_v1", "base_archive": scope["base_archive"],
        "scope_counts": dict(inputs.COUNTS), "gap_dates": scope["gap_dates"], "gap_candidate_codes": scope["gap_candidate_codes"],
        "reused_dates": scope["reused_dates"], "precoverage_dates": scope["precoverage_dates"],
        "preflight_pairs": [list(p) for p in PREFLIGHT],
        "frozen_manifest_sha256": source.codec._sha(source.codec._json(scope["frozen_manifest"])), "old_outcomes_used_for_scope": False}


def _contract():
    expected = {"schema_version": SCHEMA, "request_id": "candidate_1860_gaps_20260913_v1",
        "base_archive_sha256": inputs.BASE_SHA256, "base_archive_bytes": inputs.BASE_BYTES, "base_artifact_id": inputs.BASE_ARTIFACT_ID,
        "source_policy_id": source.SOURCE_POLICY_ID, "scope_counts": dict(inputs.COUNTS), "preflight_pairs": [list(p) for p in PREFLIGHT],
        "preflight_rule": "BOTH_COMPLETE_NONEMPTY_SINGLE_CANDIDATE_RESPONSES", "api_name": "stk_auction",
        "params": {"trade_date": "FROZEN_GAP_T_DATE", "ts_code": "FROZEN_CANDIDATE_CODE"}, "fields": list(source.FIELDS),
        "max_api_calls": 1860, "retries": 0, "timeout_seconds": 20, "max_http_response_bytes": 4_000_000,
        "request_start_budget_seconds": 3000, "request_start_headroom_seconds": 20,
        "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE", "max_workers": 4,
        "minimum_request_start_interval_seconds": 0.5, "redirects_allowed": False, "pagination_or_filter_fallback_allowed": False,
        "documentation": "https://tushare.pro/document/2?doc_id=369", **FLAGS}
    equal(inputs._json((HERE / CONTRACT).read_bytes()), expected, "REGISTERED_CONTRACT_CHANGED")


def _request(item, root, files, observed):
    require(type(item) is dict and set(item) == REQUEST_KEYS, "REQUEST_FIELDS_CHANGED")
    day, code = item["trade_date"], item["ts_code"]
    equal(item["request"], source.request_contract(day, code), "SINGLE_CANDIDATE_REQUEST_CHANGED")
    equal(item["preflight"], (day, code) in PREFLIGHT, "PREFLIGHT_FLAG_CHANGED")
    calls = integer(item["api_calls"], 0, 1, "INVALID_REQUEST_CALL_COUNT")
    equal(item["network_request_performed"], bool(calls), "NETWORK_ACCOUNTING_CHANGED")
    status = item["status"]
    require(type(status) is str and status in NO_CALL | FAILED | source.STATUSES, "UNKNOWN_REQUEST_STATUS")
    if not calls:
        require(status in NO_CALL, "UNREQUESTED_RESPONSE")
        for key in ("request_sequence", "reason", "http_response_sha256", "http_response_bytes", "rows"):
            equal(item[key], None, "UNREQUESTED_RESPONSE_CLAIM")
        equal(item["source_files"], [], "UNREQUESTED_SOURCE_FILES")
        return set()
    integer(item["request_sequence"], 1, MAX_CALLS, "INVALID_REQUEST_SEQUENCE")
    require(status not in NO_CALL, "REQUESTED_UNREQUESTED_STATUS")
    response = item["http_response_sha256"] is not None
    if response:
        sha(item["http_response_sha256"]); integer(item["http_response_bytes"], 1, source.MAX_BYTES, "INVALID_HTTP_BYTES")
    else: equal(item["http_response_bytes"], None, "UNPAIRED_HTTP_BINDING")
    if status in FAILED:
        equal(item["source_files"], [], "FAILED_REQUEST_SOURCE_FILES"); equal(item["rows"], None, "FAILED_RESPONSE_ROW_CLAIM")
        if status == "PENDING_INVALID_CANDIDATE_SOURCE":
            require(response and type(item["reason"]) is str and re.fullmatch(r"[A-Z0-9_]{1,120}", item["reason"]), "INVALID_SOURCE_FAILURE")
        elif status == "PENDING_HTTP_ERROR":
            require(not response and type(item["reason"]) is str and re.fullmatch(r"HTTP_(?:ERROR|[1-5][0-9]{2})", item["reason"]), "INVALID_HTTP_FAILURE")
        else: equal(item["reason"], None, "UNKNOWN_FAILURE_REASON")
        return set()
    require(response, "SOURCE_WITHOUT_HTTP_BINDING"); equal(item["reason"], None, "SUCCESS_WITH_FAILURE_REASON")
    paths = [path.relative_to(root).as_posix() for path in source.source_paths(root, day, code)]
    bound = _bindings(item["source_files"], files)
    equal([b["path"] for b in item["source_files"]], paths, "WRONG_CANDIDATE_SOURCE_PATHS")
    loaded = source.load(root, day, code)
    equal(item["status"], loaded.status, "SOURCE_STATUS_CHANGED"); equal(item["rows"], len(loaded.rows), "SOURCE_ROWS_CHANGED")
    equal([{k: b[k] for k in ("path", "sha256")} for b in item["source_files"]], [dict(b) for b in loaded.source_files], "SOURCE_REPLAY_BINDINGS_CHANGED")
    meta = inputs._json(_read(root, paths[1], files, source.MAX_META_BYTES))
    for key in ("trade_date", "ts_code", "request", "status", "rows", "http_response_sha256", "http_response_bytes"):
        equal(meta[key], item[key], "SOURCE_HTTP_RECEIPT_MISMATCH")
    require(datetime.fromisoformat(meta["fetched_at_utc"].replace("Z", "+00:00")) <= observed, "SOURCE_FETCH_AFTER_RECEIPT")
    return bound


def _preflight(rows, journal):
    by_pair = {(r["trade_date"], r["ts_code"]): r for r in rows}
    failure = next((i for i, pair in enumerate(PREFLIGHT) if by_pair[pair]["status"] != "CANDIDATE_TABLE_PRESENT"), None)
    if failure is None:
        equal([(r["trade_date"], r["ts_code"]) for r in journal[:2]], list(PREFLIGHT), "PREFLIGHT_START_ORDER_CHANGED")
        require(not any(r["status"] in {"NOT_REQUESTED_PREFLIGHT_BLOCKED", "PENDING_CREDENTIAL_ABSENT"} for r in rows), "PASSED_PREFLIGHT_UNREACHED_PAIRS")
        return True
    reached = set(PREFLIGHT[:failure + 1])
    require(by_pair[PREFLIGHT[failure]]["status"] != "NOT_REQUESTED_PREFLIGHT_BLOCKED", "PREFLIGHT_NOT_REACHED")
    require(all(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" for p, r in by_pair.items() if p not in reached), "REQUESTS_AFTER_PREFLIGHT_FAILURE")
    equal([(r["trade_date"], r["ts_code"]) for r in journal], [p for p in PREFLIGHT[:failure + 1] if by_pair[p]["api_calls"]], "PREFLIGHT_FAILURE_JOURNAL_CHANGED")
    return False


def _base_asof(root, scope):
    bound = next(item for item in scope["archive_file_bindings"] if item["path"] == inputs.LABELS)
    with zipfile.ZipFile(root / BASE) as archive:
        require(archive.getinfo(inputs.LABELS).file_size == bound["bytes"] <= inputs.MAX_MEMBER_BYTES, "BASE_LABEL_METADATA_SIZE_CHANGED")
        with archive.open(inputs.LABELS) as member: raw = member.read(bound["bytes"] + 1)
    require(len(raw) == bound["bytes"] and source.codec._sha(raw) == bound["sha256"], "BASE_LABEL_METADATA_BINDING_CHANGED")
    asof = inputs._json(raw).get("as_of_date")
    equal(asof, "20260911", "FROZEN_BASE_AS_OF_CHANGED")
    return asof


@dataclass(frozen=True, init=False, slots=True)
class VerifiedCandidateCollection:
    root: Path
    base_archive_sha256: str
    receipt_sha256: str
    frozen_manifest_sha256: str
    gap_pairs: tuple
    successful_pairs: tuple
    source_bindings: tuple
    base_file_bindings: tuple
    scope: MappingProxyType
    as_of_date: str
    run_id: str | None
    run_commit: str | None
    status: str
    source_only: bool
    label_source_eligible: bool
    _output_bindings: tuple
    _code_bindings: tuple

    def __init__(self, *, _key=None, **values):
        require(_key is _KEY and set(values) == set(self.__dataclass_fields__), "VERIFIED_COLLECTION_PRIVATE_CONSTRUCTION")
        for name, value in values.items(): object.__setattr__(self, name, _freeze(value))

    def assert_unchanged(self):
        require(type(self) is VerifiedCandidateCollection, "EXACT_VERIFIED_COLLECTION_REQUIRED")
        source._guard()
        for path, digest in self._code_bindings:
            require(inputs._file_sha(path) == digest, "VERIFIED_CODE_CHANGED")
        _, files = _scan(self.root)
        equal(list(files.values()), [dict(b) for b in self._output_bindings], "VERIFIED_OUTPUT_CHANGED")


def verify(output, *, expected_run_id=None, expected_run_commit=None):
    for path, digest in _LOADED_CODE.items(): require(inputs._file_sha(path) == digest, "LOADED_VERIFIER_CODE_CHANGED")
    source._guard(); _contract()
    root, files = _scan(output)
    require({RECEIPT, JOURNAL, MANIFEST, BASE} <= files.keys(), "INCOMPLETE_COLLECTION_OUTPUT")
    report = inputs._json(_read(root, RECEIPT, files))
    require(type(report) is dict and set(report) == RECEIPT_KEYS, "RECEIPT_FIELDS_CHANGED")
    for key, value in {"schema_version": SCHEMA, "source_policy_id": source.SOURCE_POLICY_ID, "scope_counts": inputs.COUNTS,
                       "eligible_as_real_collection": True, "callable_injected_for_test": False, "max_api_calls": MAX_CALLS, "retries": 0, **FLAGS}.items():
        equal(report[key], value, "RECEIPT_POLICY_OR_AUTHORITY_CHANGED")
    calls = integer(report["api_calls"], 0, MAX_CALLS, "INVALID_TOTAL_CALLS")
    for key, expected, pattern in (("run_id", expected_run_id, r"[0-9]{1,20}"), ("run_commit", expected_run_commit, r"[0-9a-f]{40}")):
        require((report[key] is None and calls == 0) or type(report[key]) is str and re.fullmatch(pattern, report[key]), "INVALID_RUN_IDENTITY")
        if calls or expected is not None:
            require(type(expected) is str and re.fullmatch(pattern, expected), "EXTERNAL_RUN_IDENTITY_REQUIRED")
            equal(report[key], expected, "EXTERNAL_RUN_IDENTITY_MISMATCH")
    elapsed = report["elapsed_collection_seconds"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= max(0, (calls - 1) * 0.5 - 0.001), "INVALID_REQUEST_START_TIMING")
    try:
        observed = datetime.fromisoformat(report["observed_at_utc"])
        require(observed.utcoffset() is not None and observed.utcoffset().total_seconds() == 0, "INVALID_OBSERVED_TIME")
    except (ValueError, TypeError): raise ValueError("INVALID_OBSERVED_TIME") from None
    code = report["execution_file_bindings"]
    require(type(code) is dict and set(code) == inputs.CODE_PATHS | NEW_CODE, "EXECUTION_CODE_SET_CHANGED")
    for path, digest in code.items(): require(inputs._file_sha(CHECKOUT / path) == sha(digest), "EXECUTION_CODE_SHA_MISMATCH")
    scope = inputs.read_base_archive(root / BASE)
    pairs = _pairs(scope)
    equal(report["base_archive"], scope["base_archive"], "BASE_PROVENANCE_CHANGED")
    equal(report["preflight_pairs"], [list(p) for p in PREFLIGHT], "PREFLIGHT_PAIRS_CHANGED")
    equal(inputs._json(_read(root, MANIFEST, files)), _manifest(scope), "FROZEN_GAP_MANIFEST_CHANGED")
    for item in scope["code_bindings"]: equal(code[item["path"]], item["sha256"], "OLD_EXECUTION_CODE_CHANGED")
    rows = report["requests"]
    require(type(rows) is list and len(rows) == MAX_CALLS and all(type(r) is dict for r in rows), "REQUEST_UNIVERSE_CHANGED")
    equal([(r.get("trade_date"), r.get("ts_code")) for r in rows], pairs, "REQUEST_PAIR_ORDER_OR_SCOPE_CHANGED")
    sources = set()
    for item in rows:
        bound = _request(item, root, files, observed)
        require(not sources.intersection(bound), "SOURCE_REUSED_ACROSS_PAIRS")
        sources.update(bound)
    journal = [inputs._json(line) for line in _read(root, JOURNAL, files).splitlines()]
    equal(journal, sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"]), "REQUEST_JOURNAL_CHANGED")
    equal([r["request_sequence"] for r in journal], list(range(1, calls + 1)), "REQUEST_SEQUENCE_OR_TOTAL_CHANGED")
    passed = _preflight(rows, journal)
    if any(r["status"] == "NOT_REQUESTED_BUDGET_EXHAUSTED" for r in rows):
        require(elapsed >= 2980 - 0.001, "BUDGET_EXHAUSTION_BEFORE_TIME_LIMIT")
    successful = [(r["trade_date"], r["ts_code"]) for r in rows if r["status"] in source.STATUSES]
    status = "PREFLIGHT_BLOCKED" if not passed else "SOURCE_PAIRS_COLLECTED" if len(successful) == MAX_CALLS else "SOURCE_PAIRS_PARTIAL"
    for key, expected in {"preflight_passed": passed, "status": status, "qualified_new_source_pairs": len(successful),
                          "reused_base_source_dates": len(scope["reused_dates"]), "new_source_status_counts": dict(Counter(r["status"] for r in rows))}.items():
        equal(report[key], expected, "COLLECTION_RESULT_CHANGED")
    require(_bindings(report["source_files"], files) == sources, "AGGREGATE_SOURCE_BINDINGS_CHANGED")
    equal(report["source_files"], [files[name] for name in sorted(sources)], "SOURCE_BINDING_ORDER_CHANGED")
    expected_files = sources | {BASE, MANIFEST, JOURNAL}
    require(set(files) == expected_files | {RECEIPT} and _bindings(report["output_file_bindings"], files) == expected_files, "UNREGISTERED_OR_MISSING_FILE")
    equal(report["output_file_bindings"], [files[name] for name in sorted(expected_files)], "OUTPUT_BINDING_ORDER_CHANGED")
    code_bindings = {**_LOADED_CODE, **{CHECKOUT / path: digest for path, digest in code.items()}}
    result = VerifiedCandidateCollection(_key=_KEY, root=root, base_archive_sha256=scope["base_archive"]["sha256"],
        receipt_sha256=files[RECEIPT]["sha256"], frozen_manifest_sha256=inputs._manifest_sha(scope["frozen_manifest"]),
        gap_pairs=tuple(pairs), successful_pairs=tuple(successful), as_of_date=_base_asof(root, scope),
        source_bindings=[{k: files[name][k] for k in ("path", "sha256")} for name in sorted(sources)],
        base_file_bindings=[{k: item[k] for k in ("path", "sha256")} for item in scope["archive_file_bindings"]],
        scope=scope, run_id=report["run_id"], run_commit=report["run_commit"], status=status, source_only=True,
        label_source_eligible=False, _output_bindings=list(files.values()), _code_bindings=tuple(code_bindings.items()))
    result.assert_unchanged()
    return result


def reload_verified(value):
    require(type(value) is VerifiedCandidateCollection, "EXACT_VERIFIED_COLLECTION_REQUIRED")
    value.assert_unchanged()
    return verify(value.root, expected_run_id=value.run_id, expected_run_commit=value.run_commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--expected-run-commit", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    try:
        value = verify(args.output, expected_run_id=args.expected_run_id, expected_run_commit=args.expected_run_commit)
        print(source.codec._json({"status": value.status, "successful_pairs": len(value.successful_pairs),
            "gap_pairs": len(value.gap_pairs), "receipt_sha256": value.receipt_sha256, "source_only": True}).decode().strip())
        return 0
    except Exception:
        print("CANDIDATE_COLLECTION_ACCEPTANCE_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
