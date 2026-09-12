#!/usr/bin/env python3
"""Bounded, source-only STK collection for 212 frozen v3 gaps; no label rebuild.

Original accepted ZIP is copied unchanged. Three sequential preflight requests
must pass before the remaining dates. No retries, unregistered pagination,
old-source overwrite, model, training, shadow-ledger or production writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import auction_stocks_scope as source
from work.profit_1000_upgrade import stocks_scope_inputs as inputs

SCHEMA = "dc20_research_stocks_scope_collection_20260912_v1"
CONTRACT_FILE = "STOCKS_SCOPE_COLLECTION.json"
RECEIPT_FILE = "stocks_scope_receipt.json"
JOURNAL_FILE = "stocks_scope_requests.jsonl"
MAX_CALLS, TIMEOUT, MAX_SECONDS, MAX_WORKERS = 212, 20, 1200, 2
START_INTERVAL = 1.0
NEW_CODE = tuple("work/profit_1000_upgrade/" + n for n in (
    "auction_stocks_scope.py", "stocks_scope_inputs.py", "stocks_scope_collect.py", CONTRACT_FILE))
_IMPORTED_CODE = {name: inputs._file_sha(CHECKOUT / name) for name in NEW_CODE if not name.endswith(".json")}
FLAGS = {"research_only": True, "source_only": True, "source_import_into_labels_performed": False,
         "label_rebuild_performed": False, "training_performed": False, "settlement_performed": False,
         "production_writes": False, "production_activation_allowed": False,
         "fallback_generated": False, "old_source_overwrite_allowed": False,
         "old_labels_used_as_new_outcomes": False, "actual_execution_claimed": False,
         "raw_response_envelope_saved": False, "credential_persisted": False,
         "forward_holdout_touched": False}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "stocks_only_frozen_212_gaps_20260912_v1",
            "base_archive_sha256": inputs.BASE_SHA256, "base_archive_bytes": inputs.BASE_BYTES,
            "base_artifact_id": inputs.BASE_ARTIFACT_ID, "source_policy_id": source.SOURCE_POLICY_ID,
            "scope_counts": dict(inputs.COUNTS), "preflight_T_dates": list(inputs.PREFLIGHT_T_DATES),
            "preflight_rule": "COMPLETE_NONEMPTY_TABLE_WITH_AT_LEAST_ONE_FROZEN_CANDIDATE",
            "api_name": "stk_auction", "params": {"trade_date": "FROZEN_GAP_T_DATE", "ts_type": "STK"},
            "fields": list(source.FIELDS), "max_api_calls": MAX_CALLS, "retries": 0,
            "timeout_seconds": TIMEOUT, "max_http_response_bytes": source.MAX_BYTES,
            "request_start_budget_seconds": MAX_SECONDS, "request_start_headroom_seconds": TIMEOUT,
            "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE", "max_workers": MAX_WORKERS,
            "minimum_request_start_interval_seconds": START_INTERVAL, "redirects_allowed": False,
            "pagination_or_filter_fallback_allowed": False,
            "documentation": "https://tushare.pro/document/2?doc_id=369", **FLAGS}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unaliased(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def _binding(root, path):
    _require(path.is_file() and _unaliased(path), "REGULAR_UNALIASED_OUTPUT_REQUIRED")
    return {"path": path.relative_to(root).as_posix(), "sha256": inputs._file_sha(path),
            "bytes": path.stat().st_size}


def _write(root, relative, raw, token):
    _require(type(raw) is bytes and (not token or token.encode() not in raw), "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    text = str(relative)
    _require(not Path(relative).is_absolute() and text and "\\" not in text
             and all(p not in ("", ".", "..") for p in text.split("/")), "UNSAFE_RELATIVE_OUTPUT_PATH")
    path = root / relative
    _require(_unaliased(path) and not path.exists() and root in path.parents, "EXCLUSIVE_UNALIASED_OUTPUT_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
    return _binding(root, path)


def _code_snapshot(scope):
    code = {item["path"]: item["sha256"] for item in scope["code_bindings"]}
    code.update({name: inputs._file_sha(CHECKOUT / name) for name in NEW_CODE})
    return dict(sorted(code.items()))


def _guard(code):
    _require(all(inputs._file_sha(CHECKOUT / p) == sha for p, sha in _IMPORTED_CODE.items()),
             "COLLECTION_CODE_CHANGED_SINCE_IMPORT")
    _require(all(inputs._file_sha(CHECKOUT / p) == sha for p, sha in code.items()), "COLLECTION_CODE_CHANGED")
    _require(inputs._json((HERE / CONTRACT_FILE).read_bytes()) == expected_contract(), "REGISTERED_COLLECTION_CHANGED")
    source._guard()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def official_call(contract, token):
    day = contract.get("params", {}).get("trade_date") if type(contract) is dict else None
    _require(contract == source.request_contract(day), "STOCKS_TRANSPORT_REQUEST_CHANGED")
    payload = {**contract, "fields": ",".join(source.FIELDS), "token": token}
    req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.build_opener(_NoRedirect()).open(req, timeout=TIMEOUT) as response:
        raw = response.read(source.MAX_BYTES + 1)
    _require(type(raw) is bytes and 0 < len(raw) <= source.MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
    return raw


class RequestBudget:
    """Serialize starts, not response handling; reserve each attempt once."""
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.started, self.next_start = clock(), clock()
        self.calls, self.lock = 0, threading.Lock()

    def reserve(self):
        with self.lock:
            if self.calls >= MAX_CALLS or self.clock() - self.started >= MAX_SECONDS - TIMEOUT:
                return None
            delay = self.next_start - self.clock()
            if delay > 0:
                self.sleep(delay)
            if self.clock() - self.started >= MAX_SECONDS - TIMEOUT:
                return None
            self.calls += 1
            self.next_start = self.clock() + START_INTERVAL
            return self.calls


def blank_result(day, scope, injected, status="NOT_REQUESTED_PREFLIGHT_BLOCKED"):
    return {"trade_date": day, "request": source.request_contract(day), "api_calls": 0,
            "request_sequence": None, "preflight": day in scope["preflight_T_dates"],
            "status": status, "reason": None, "network_request_performed": False,
            "callable_injected_for_test": injected, "http_response_sha256": None,
            "http_response_bytes": None, "source_files": [], "rows": None,
            "present_candidate_count": None, "missing_candidate_codes": None}


def _collect_one(root, scope, day, token, transport, injected, budget):
    _require(injected is False, "SYNTHETIC_TRANSPORT_CANNOT_CREATE_SOURCE")
    _require(day in scope["gap_dates"], "UNREGISTERED_GAP_DATE")
    item = blank_result(day, scope, injected)
    if not token.strip():
        return {**item, "status": "PENDING_CREDENTIAL_ABSENT"}
    sequence = budget.reserve()
    if sequence is None:
        return {**item, "status": "NOT_REQUESTED_BUDGET_EXHAUSTED"}
    item.update(api_calls=1, request_sequence=sequence, network_request_performed=not injected,
                status="PENDING_NETWORK_OR_RESPONSE_ERROR")
    try:
        raw = transport(source.request_contract(day), token)
        _require(type(raw) is bytes and 0 < len(raw) <= source.MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
        item.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw))
        body, meta = source.source_bytes(raw, day, request=source.request_contract(day),
            fetched_at_utc=datetime.now(timezone.utc).isoformat(), network_request_performed=True, token=token)
    except source.AuctionSourceError as exc:
        # Validator reasons are fixed enum-like strings; never persist HTTP/server text.
        reason = str(exc)
        item.update(status="PENDING_INVALID_STOCKS_SOURCE",
                    reason=reason if re.fullmatch(r"[A-Z0-9_]{1,120}", reason) else "INVALID_STOCKS_SOURCE")
        return item
    except error.HTTPError as exc:
        item.update(status="PENDING_HTTP_ERROR", reason="HTTP_" + str(exc.code)
                    if type(exc.code) is int and 100 <= exc.code <= 599 else "HTTP_ERROR")
        return item
    except Exception:
        return item
    # File-system/readback failures are fatal, not misreported as API missingness.
    pairs = source.source_paths(root, day)
    bindings = [_write(root, p.relative_to(root), data, token) for p, data in zip(pairs, (body, meta))]
    loaded = source.load(root, day)
    _require({b["path"]: b["sha256"] for b in bindings} ==
             {b["path"]: b["sha256"] for b in loaded.source_files}, "SOURCE_READBACK_MISMATCH")
    expected_codes = set(scope["gap_candidate_codes"][day])
    present = expected_codes.intersection(loaded.rows)
    return {**item, "status": loaded.status, "reason": None, "source_files": bindings,
            "rows": len(loaded.rows), "present_candidate_count": len(present),
            "missing_candidate_codes": sorted(expected_codes - present)}


def preflight_ok(item):
    return (item["status"] == "STOCKS_SCOPED_TABLE_PRESENT" and item["rows"] > 0
            and item["present_candidate_count"] > 0)


def _run_requests(root, scope, token, transport, injected, budget, progress=None):
    results, passed = {}, True
    for day in scope["preflight_T_dates"]:
        item = _collect_one(root, scope, day, token, transport, injected, budget)
        results[day] = item
        if progress:
            progress({"phase": "preflight", "trade_date": day, "status": item["status"], "rows": item["rows"]})
        if not preflight_ok(item):
            passed = False
            break
    if passed:
        remaining = [d for d in scope["gap_dates"] if d not in results]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for item in pool.map(lambda d: _collect_one(root, scope, d, token, transport, injected, budget), remaining):
                results[item["trade_date"]] = item
                if progress and len(results) % 25 == 0:
                    progress({"phase": "collection", "completed_dates": len(results), "budget_calls": budget.calls})
    for day in scope["gap_dates"]:
        if day not in results:
            results[day] = blank_result(day, scope, injected)
    _require(len(results) == MAX_CALLS and set(results) == set(scope["gap_dates"]), "FROZEN_GAP_SCOPE_CHANGED")
    return [results[d] for d in sorted(results)], passed


def run_collection(base_archive, output, *, token, call=None, progress=None):
    _require(type(token) is str, "INVALID_TRANSPORT_ARGUMENT")
    _require(call is None, "SYNTHETIC_TRANSPORT_CANNOT_CREATE_SOURCE")
    _require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "COLLECTION_RERUN_NOT_ALLOWED")
    scope = inputs.read_base_archive(base_archive)
    code = _code_snapshot(scope)
    _guard(code)
    root = Path(output)
    _require(not root.exists() and _unaliased(root), "FRESH_UNALIASED_OUTPUT_REQUIRED")
    root = root.resolve()
    _require(root != CHECKOUT and CHECKOUT not in root.parents and root not in CHECKOUT.parents,
             "OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    _require(root not in Path(base_archive).resolve().parents, "BASE_ARCHIVE_INSIDE_OUTPUT")
    root.mkdir(parents=True, exist_ok=False)
    with Path(base_archive).open("rb") as handle, (root / "base_v3.zip").open("xb") as dest:
        shutil.copyfileobj(handle, dest, length=1024**2)
    inputs.verify_base_unchanged(root / "base_v3.zip")
    gap_manifest = {"schema_version": "dc20_stocks_scope_gap_manifest_v1",
                    "base_archive": scope["base_archive"], "scope_counts": dict(inputs.COUNTS),
                    "gap_dates": scope["gap_dates"], "gap_candidate_codes": scope["gap_candidate_codes"],
                    "reused_dates": scope["reused_dates"], "precoverage_dates": scope["precoverage_dates"],
                    "preflight_T_dates": scope["preflight_T_dates"],
                    "frozen_manifest_sha256": _sha(_json(scope["frozen_manifest"])),
                    "old_outcomes_used_for_scope": False}
    _write(root, "gap_manifest.json", _json(gap_manifest), token)
    injected = call is not None
    budget = RequestBudget()
    rows, passed = _run_requests(root, scope, token, official_call if call is None else call, injected, budget, progress)
    journal = sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"])
    _require([r["request_sequence"] for r in journal] == list(range(1, budget.calls + 1)), "REQUEST_SEQUENCE_MISMATCH")
    _write(root, JOURNAL_FILE, b"".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n"
                                      for r in journal), token)
    _guard(code)
    inputs.verify_base_unchanged(base_archive)
    inputs.verify_base_unchanged(root / "base_v3.zip")
    source_files = sorted((b for r in rows for b in r["source_files"]), key=lambda b: b["path"])
    _require(all(_binding(root, root / b["path"]) == b for b in source_files), "COLLECTED_SOURCE_CHANGED")
    count = sum(r["status"] in source.STATUSES for r in rows)
    status = "PREFLIGHT_BLOCKED" if not passed else "SOURCE_GAPS_COLLECTED" if count == MAX_CALLS else "SOURCE_GAPS_PARTIAL"
    bindings = sorted((_binding(root, p) for p in root.rglob("*") if p.is_file()), key=lambda b: b["path"])
    _require({b["path"] for b in bindings} == {"base_v3.zip", "gap_manifest.json", JOURNAL_FILE} |
             {b["path"] for b in source_files}, "UNREGISTERED_OUTPUT_FILE")
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schema_version": SCHEMA, "status": status, "base_archive": scope["base_archive"],
              "source_policy_id": source.SOURCE_POLICY_ID, "scope_counts": dict(inputs.COUNTS),
              "preflight_T_dates": scope["preflight_T_dates"], "preflight_passed": passed,
              "callable_injected_for_test": injected, "eligible_as_real_collection": not injected,
              "api_calls": budget.calls, "max_api_calls": MAX_CALLS, "retries": 0,
              "elapsed_collection_seconds": round(budget.clock() - budget.started, 3),
              "qualified_new_source_dates": count, "qualified_source_dates_including_base": count + 181,
              "new_source_status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
              "remaining_gap_dates": [r["trade_date"] for r in rows if r["status"] not in source.STATUSES],
              "requests": rows, "source_files": source_files, "output_file_bindings": bindings,
              "execution_file_bindings": code, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
              "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None, **FLAGS}
    _write(root, RECEIPT_FILE, _json(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_collection(args.base_archive, args.output, token=os.environ.get("TUSHARE_TOKEN", ""),
                                progress=lambda item: print(json.dumps(item), flush=True))
        print(json.dumps({k: report[k] for k in ("status", "api_calls", "qualified_new_source_dates",
                                                "qualified_source_dates_including_base")}))
        return 0 if report["status"] == "SOURCE_GAPS_COLLECTED" else 2
    except Exception:
        print("STOCKS_SCOPE_COLLECTION_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
