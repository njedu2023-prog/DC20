#!/usr/bin/env python3
"""First-stage v3 canonical-auction collection in a new isolated mirror only.

The fixed 910 T dates comprise 517 pre-coverage declarations and at most one
original HTTP request on each of 393 covered dates. No retries, minute/daily
requests, diagnostic imports, label rebuild, training or settlement occur here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib import error

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]

from work.profit_1000_upgrade import auction_truth_v3 as auction, collect as old, policy_v3, research_v3

SCHEMA = "dc20_profit_1000_canonical_collection_v3"
PHASE = "CANONICAL_AUCTION_ONLY"
OLD_COLLECT_SHA = "9885bb417779638bb85f5a03893137508a17d21ae3ba93d25732cbb4e83af9fa"
OLD_AUCTION_SHA = "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af"
V2_SHA = "58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64"
V2_RUN = "34676871475"
V2_COMMIT = "df6c38806da20066f673875a4a83c37fa895d2e4"
DEFAULT_BUDGET = {"max_api_calls": 393, "max_seconds": 1200,
                  "requests_per_second": 2.0, "workers": 4, "timeout_seconds": 20}
FLAGS = {"research_only": True, "production_writes": False,
         "purchase_permission": False, "existing_truth_overwritten": False,
         "diagnostic_sources_imported": False, "old_sources_relabelled": False,
         "training_performed": False, "labels_rebuilt": False,
         "settlement_performed": False, "actual_execution_claimed": False,
         "actual_capacity_verified": False, "production_activation_allowed": False,
         "forward_holdout_evaluated": False, "credential_persisted": False,
         "server_messages_persisted": False, "missing_truth_as_zero": False}
_IMPLEMENTATIONS = tuple(HERE / name for name in (
    "collect_v3.py", "research_v3.py", "policy_v3.py", "auction_truth_v3.py", "collect.py", "auction_truth.py",
    "labels_v3.py", "minute_truth.py", "PLAN_V3.json", "COLLECTION_V3.json")) + tuple(
        CHECKOUT / "src/top10decision/decision" / name for name in (
            "executable_profit_shadow_settlement.py", "shadow_exit_1000.py", "shadow_exit_minute_truth.py"))


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
             "BOUND_REGULAR_UNALIASED_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_HASHES = {path: _file_sha(path) for path in _IMPLEMENTATIONS}


def expected_contract():
    return {"schema_version": "dc20_profit_1000_collection_request_v3", "plan_version": "v3",
            "phase": PHASE, "as_of_date": "20260911", **dict(policy_v3.CONTRACT),
            "base_archive_sha256": V2_SHA, "base_run_id": V2_RUN, "base_run_commit": V2_COMMIT,
            "budget": dict(DEFAULT_BUDGET), "allowed_endpoints": ["stk_auction"], "retries": 0,
            "max_http_response_bytes": 4_000_000, "canonical_coverage_start": "20250101",
            "pre_coverage_T_dates": 517, "canonical_T_dates": 393, "candidate_rows": 6753, "T_dates": 910,
            **FLAGS}


def collection_contract():
    path = HERE / "COLLECTION_V3.json"
    _file_sha(path)
    value = auction._strict_json(path.read_bytes())
    _require(value == expected_contract(), "COLLECTION_V3_REGISTERED_CONTRACT_CHANGED")
    return value


def _budget(value):
    value = dict(DEFAULT_BUDGET) if value is None else value
    _require(isinstance(value, dict) and set(value) == set(DEFAULT_BUDGET), "V3_BUDGET_FIELDS_CHANGED")
    for key, maximum in DEFAULT_BUDGET.items():
        number = value[key]
        _require(type(number) in (int, float) and 0 < number <= maximum, "V3_BUDGET_EXCEEDED:" + key)
        _require(key == "requests_per_second" or type(number) is int, "V3_BUDGET_INTEGER_REQUIRED:" + key)
    return dict(value)


def _limiter(value):
    return old.RequestBudget(value)


def _code_guard():
    _require(_file_sha(HERE / "collect.py") == OLD_COLLECT_SHA, "PINNED_OLD_COLLECTOR_CHANGED")
    _require(_file_sha(HERE / "auction_truth.py") == OLD_AUCTION_SHA, "PINNED_OLD_AUCTION_CODEC_CHANGED")
    for path, expected in _IMPORTED_HASHES.items():
        _require(_file_sha(path) == expected, "V3_COLLECTION_CODE_CHANGED_SINCE_IMPORT")


def _snapshot(root):
    bindings = {}
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink(), "RESEARCH_MIRROR_CONTAINS_SYMLINK")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            bindings[relative] = {"path": relative, "sha256": _file_sha(path)}
        else:
            _require(path.is_dir(), "RESEARCH_MIRROR_CONTAINS_SPECIAL_FILE")
    return bindings


def _scope(manifest, contract):
    _require(isinstance(manifest, dict) and isinstance(manifest.get("rows"), list), "FROZEN_CANDIDATE_ROWS_REQUIRED")
    policy_v3.validate_contract(manifest)
    rows = manifest["rows"]
    _require(len(rows) == contract["candidate_rows"], "V3_FROZEN_CANDIDATE_COUNT_CHANGED")
    by_date, identities, checked_dates, checked_codes = defaultdict(set), set(), set(), set()
    for row in rows:
        signal, day, code = row["signal_date"], row["exec_date"], row["ts_code"]
        for value in (signal, day):
            if value not in checked_dates:
                auction._date(value)
                checked_dates.add(value)
        if code not in checked_codes:
            auction._code(code)
            checked_codes.add(code)
        _require(signal < day <= contract["as_of_date"] and signal < "20260914", "FUTURE_OR_REVERSED_FROZEN_DATE")
        identity = signal, code
        _require(identity not in identities, "DUPLICATE_FROZEN_CANDIDATE")
        identities.add(identity)
        by_date[day].add(code)
    dates = sorted(by_date)
    _require(len(dates) == contract["T_dates"], "V3_FROZEN_T_DATE_COUNT_CHANGED")
    pre = [day for day in dates if day < auction.COVERAGE_START]
    covered = [day for day in dates if day >= auction.COVERAGE_START]
    _require(len(pre) == contract["pre_coverage_T_dates"] and len(covered) == contract["canonical_T_dates"],
             "V3_FROZEN_COVERAGE_SPLIT_CHANGED")
    return dates, by_date


_SAFE_REASONS = frozenset(re.findall(r'"([A-Z][A-Z0-9_]{3,100})"',
    (HERE / "auction_truth_v3.py").read_text() + (HERE / "auction_truth.py").read_text()))


def _reason(exc):
    reason = str(exc)
    return reason if reason in _SAFE_REASONS else "UNCLASSIFIED_CODEC_REJECTION"


def _receipt(day, plan_sha, contract_sha, candidate_codes):
    return {"trade_date": day, "endpoint": "stk_auction", "ts_code": None,
            "candidate_codes": sorted(candidate_codes), "status": "PENDING_UNKNOWN",
            "reason": None, "network_request_performed": False,
            "new_source_files": [], "source_policy_id": auction.SOURCE_POLICY_ID,
            "plan_sha256": plan_sha, "collection_contract_sha256": contract_sha}


def _fetch(root, day, codes, *, token, limiter, call, plan_sha, contract_sha):
    result = _receipt(day, plan_sha, contract_sha, codes)
    paths = auction.source_paths(root, day)
    # This initial stage never imports/reuses any existing v3 source pair.
    for path in paths:
        old._safe_target(root, path)
    if day < auction.COVERAGE_START:
        return {**result, "status": "HISTORY_BEFORE_CANONICAL_COVERAGE", "reason": "BEFORE_DOCUMENTED_CANONICAL_COVERAGE"}
    if not token.strip():
        return {**result, "status": "PENDING_CREDENTIAL_ABSENT", "reason": "CREDENTIAL_ABSENT"}
    if not limiter.take():
        return {**result, "status": "PENDING_BUDGET_EXHAUSTED", "reason": "BUDGET_EXHAUSTED"}
    request = auction.request_contract(day)
    result.update(network_request_performed=True, request=request)
    try:
        raw = call("stk_auction", request["params"], tuple(request["fields"]), token, limiter.budget["timeout_seconds"])
    except error.HTTPError as exc:
        return {**result, "status": "PENDING_HTTP_ERROR", "reason": "HTTP_ERROR",
                "http_status": exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None}
    except Exception:
        return {**result, "status": "PENDING_NETWORK_OR_RESPONSE_ERROR", "reason": "NETWORK_OR_RESPONSE_ERROR"}
    if type(raw) is not bytes or not 0 < len(raw) <= 4_000_000:
        return {**result, "status": "PENDING_INVALID_HTTP_BYTES", "reason": "ORIGINAL_BOUNDED_HTTP_BYTES_REQUIRED"}
    result.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw))
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        bodies = auction.source_bytes(raw, day, request=request, fetched_at_utc=stamp,
                                      network_request_performed=True, token=token)
        metadata = auction._strict_json(bodies[1])
        _require(not token or all(token.encode() not in body for body in bodies), "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED")
        bindings = old._write_pair(root, paths, bodies)
        loaded = auction.load(root, day)
        _require(list(map(dict, loaded.source_files)) == bindings, "V3_WRITTEN_PAIR_BINDING_CHANGED")
        _require(loaded.requested_code is None and loaded.status == metadata["status"], "V3_WRITTEN_PAIR_REQUEST_CHANGED")
        result.update(status="EXACT_TRUTH_WRITTEN", reason=None, new_source_files=bindings,
                      source_status=metadata["status"], source_rows=metadata["rows"],
                      fetched_at_utc=stamp, api_code=metadata["api_code"],
                      candidate_rows_present=sum(code in loaded.rows for code in codes))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result.update(status="PENDING_INVALID_SOURCE_NOT_IMPUTED", reason=_reason(exc))
    return result


def collect_history(root, manifest, token, budget=None, call=None, progress=None):
    """Collect only a new v3 auction source set and return its immutable receipt."""
    _require(isinstance(token, str), "TOKEN_MUST_BE_STRING")
    _code_guard()
    contract = collection_contract()
    plan = research_v3.load_plan()
    _require(plan["as_of_date"] == contract["as_of_date"], "V3_COLLECTION_PLAN_DATE_CHANGED")
    root = research_v3.require_research_mirror(root)
    fresh = research_v3.prepare_history(root)
    _require(manifest == fresh, "FROZEN_MANIFEST_RECONSTRUCTION_CHANGED")
    dates, by_date = _scope(manifest, contract)
    plan_sha = _file_sha(research_v3.plan_path())
    contract_sha = _file_sha(HERE / "COLLECTION_V3.json")
    research_v3.verify_import(root)
    imported = _snapshot(root)
    _require(not any(path == auction.ROOT_PATH or path.startswith(auction.ROOT_PATH + "/") for path in imported),
             "INITIAL_V3_COLLECTION_CANNOT_REUSE_EXISTING_SOURCE")
    journal = root / "collection_requests.jsonl"
    old._safe_target(root, journal)
    old._safe_target(root, root / "collection_receipt.json")
    limiter = _limiter(_budget(budget))
    call = old.official_call_v2 if call is None else call
    _require(callable(call) and (progress is None or callable(progress)), "INVALID_COLLECTION_CALLBACK")
    receipts, written = [], {}
    journal_digest = hashlib.sha256()
    with journal.open("xb") as handle:
        def retain(receipt):
            line = _json(receipt) + b"\n"
            _require(not token or token.encode() not in line, "CREDENTIAL_LIKE_JOURNAL_VALUE_FORBIDDEN")
            _require(not journal.is_symlink() and _file_sha(journal) == journal_digest.hexdigest(), "COLLECTION_JOURNAL_CHANGED")
            handle.write(line)
            handle.flush()
            journal_digest.update(line)
            receipts.append(receipt)
            for binding in receipt["new_source_files"]:
                _require(binding["path"] not in written, "DUPLICATE_NEW_SOURCE_BINDING")
                written[binding["path"]] = binding
            if progress and len(receipts) % 100 == 0:
                progress({"event": "CANONICAL_COLLECTION_PROGRESS", "receipts": len(receipts),
                          "api_calls": limiter.calls, "new_source_files": len(written),
                          "elapsed_seconds": round(limiter.clock() - limiter.started, 2)})
        covered = []
        for day in dates:
            if day < auction.COVERAGE_START:
                retain(_fetch(root, day, by_date[day], token=token, limiter=limiter, call=call,
                              plan_sha=plan_sha, contract_sha=contract_sha))
            else:
                covered.append(day)
        with ThreadPoolExecutor(max_workers=limiter.budget["workers"]) as pool:
            futures = [pool.submit(_fetch, root, day, by_date[day], token=token, limiter=limiter,
                                   call=call, plan_sha=plan_sha, contract_sha=contract_sha) for day in covered]
            for future in as_completed(futures):
                retain(future.result())
    _code_guard()
    _require(research_v3.require_research_mirror(root) == root, "RESEARCH_ROOT_CHANGED_DURING_COLLECTION")
    _require(_file_sha(research_v3.plan_path()) == plan_sha and _file_sha(HERE / "COLLECTION_V3.json") == contract_sha,
             "REGISTERED_PLAN_OR_COLLECTION_CHANGED")
    research_v3.verify_import(root)
    current = _snapshot(root)
    _require(all(current.get(path) == binding for path, binding in imported.items()), "IMPORTED_SOURCE_CHANGED_DURING_COLLECTION")
    journal_binding = {"path": journal.relative_to(root).as_posix(), "sha256": journal_digest.hexdigest()}
    _require(current.get(journal_binding["path"]) == journal_binding, "COLLECTION_JOURNAL_CHANGED")
    _require(all(current.get(path) == binding for path, binding in written.items()), "NEW_V3_SOURCE_CHANGED_DURING_COLLECTION")
    _require(set(current) == set(imported) | set(written) | {journal_binding["path"]}, "UNEXPECTED_COLLECTION_FILE_OR_PARTIAL_PAIR")
    _require(len(receipts) == 910 and len({r["trade_date"] for r in receipts}) == 910, "INCOMPLETE_FROZEN_DATE_JOURNAL")
    _require(sum(r["network_request_performed"] for r in receipts) == limiter.calls <= 393, "CANONICAL_REQUEST_ACCOUNTING_CHANGED")
    complete = all(r["status"] in {"EXACT_TRUTH_WRITTEN", "HISTORY_BEFORE_CANONICAL_COVERAGE"} for r in receipts)
    result = {"schema_version": SCHEMA, "plan_version": "v3", "phase": PHASE,
              "status": "COMPLETE" if complete else "BLOCKED", "as_of_date": contract["as_of_date"],
              **dict(policy_v3.CONTRACT), "source_policy_contract": dict(policy_v3.CONTRACT),
              "plan_sha256": plan_sha, "collection_contract_sha256": contract_sha,
              "collection_request_sha256": contract_sha, "budget": dict(limiter.budget),
              "api_calls": limiter.calls, "retries": 0, "minute_api_calls": 0, "daily_api_calls": 0,
              "candidate_rows": len(manifest["rows"]), "T_dates": len(dates),
              "precoverage_dates": 517, "covered_dates": 393,
              "auction_evidence_complete": complete, "auction_attempts_complete": all(
                  r["network_request_performed"] or r["status"] == "HISTORY_BEFORE_CANONICAL_COVERAGE" for r in receipts),
              "request_receipts": sorted(receipts, key=lambda r: r["trade_date"]),
              "source_files": sorted(written.values(), key=lambda b: b["path"]),
              "new_source_files": sorted(written.values(), key=lambda b: b["path"]),
              "imported_source_files": sorted(imported.values(), key=lambda b: b["path"]),
              "journal_binding": journal_binding,
              "execution_file_bindings": {p.relative_to(CHECKOUT).as_posix(): digest for p, digest in _IMPORTED_HASHES.items()},
              "request_status_counts": dict(Counter(r["status"] for r in receipts)),
              "elapsed_seconds": round(limiter.clock() - limiter.started, 2), **FLAGS}
    _require(not token or token.encode() not in _json(result), "CREDENTIAL_LIKE_RECEIPT_VALUE_FORBIDDEN")
    if progress:
        progress({"event": "CANONICAL_COLLECTION_FINISHED", "status": result["status"],
                  "api_calls": limiter.calls, "request_status_counts": result["request_status_counts"]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = research_v3.require_research_mirror(args.root)
    _require(args.report == root / "collection_receipt.json" and not any(p.is_symlink() for p in (args.report, *args.report.parents)),
             "V3_RECEIPT_MUST_BE_EXACT_MIRROR_PATH")
    old._safe_target(root, args.report)
    manifest = research_v3.prepare_history(root)
    receipt = collect_history(root, manifest, os.environ.get("TUSHARE_TOKEN", ""),
                              progress=lambda value: print(json.dumps(value, sort_keys=True), flush=True))
    research_v3.write_json(args.report, receipt)
    return 0 if receipt["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
