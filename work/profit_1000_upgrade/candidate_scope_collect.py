#!/usr/bin/env python3
"""Collect only frozen missing candidate auction pairs into an isolated overlay.

The accepted base ZIP is retained byte-for-byte. Complete empty responses are
source observations, not fabricated no-fill/zero-return labels. No retries,
alternate filters, future holdout, model fitting or production writes here.
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
from work.profit_1000_upgrade import auction_candidate_scope as source
from work.profit_1000_upgrade import stocks_scope_inputs as inputs

SCHEMA = "dc20_candidate_auction_collection_20260913_v1"
CONTRACT_FILE = "CANDIDATE_AUCTION_COLLECTION.json"
RECEIPT_FILE, JOURNAL_FILE = "candidate_scope_receipt.json", "candidate_scope_requests.jsonl"
MAX_CALLS, TIMEOUT, MAX_SECONDS, MAX_WORKERS = 1860, 20, 3000, 4
START_INTERVAL = 0.5
PREFLIGHT_PAIRS = (("20250319", "000612.SZ"), ("20260203", "000995.SZ"))
CODE_FILES = tuple("work/profit_1000_upgrade/" + name for name in (
    "auction_candidate_scope.py", "stocks_scope_inputs.py", "candidate_scope_collect.py", CONTRACT_FILE))
_IMPORTED = {name: inputs._file_sha(CHECKOUT / name) for name in CODE_FILES if not name.endswith(".json")}
FLAGS = {"research_only": True, "source_only": True, "label_rebuild_performed": False,
         "source_import_into_labels_performed": False, "training_performed": False,
         "settlement_performed": False, "production_writes": False, "production_activation_allowed": False,
         "fallback_generated": False, "old_source_overwrite_allowed": False,
         "old_outcomes_used_for_scope": False, "actual_execution_claimed": False,
         "raw_response_envelope_saved": False, "credential_persisted": False,
         "forward_holdout_touched": False}


def expected_contract():
    return {"schema_version": SCHEMA, "request_id": "candidate_1860_gaps_20260913_v1",
            "base_archive_sha256": inputs.BASE_SHA256, "base_archive_bytes": inputs.BASE_BYTES,
            "base_artifact_id": inputs.BASE_ARTIFACT_ID, "source_policy_id": source.SOURCE_POLICY_ID,
            "scope_counts": dict(inputs.COUNTS), "preflight_pairs": [list(p) for p in PREFLIGHT_PAIRS],
            "preflight_rule": "BOTH_COMPLETE_NONEMPTY_SINGLE_CANDIDATE_RESPONSES",
            "api_name": "stk_auction", "params": {"trade_date": "FROZEN_GAP_T_DATE", "ts_code": "FROZEN_CANDIDATE_CODE"},
            "fields": list(source.FIELDS), "max_api_calls": MAX_CALLS, "retries": 0,
            "timeout_seconds": TIMEOUT, "max_http_response_bytes": source.MAX_BYTES,
            "request_start_budget_seconds": MAX_SECONDS, "request_start_headroom_seconds": TIMEOUT,
            "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE",
            "max_workers": MAX_WORKERS, "minimum_request_start_interval_seconds": START_INTERVAL,
            "redirects_allowed": False, "pagination_or_filter_fallback_allowed": False,
            "documentation": "https://tushare.pro/document/2?doc_id=369", **FLAGS}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unaliased(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def binding(root, path):
    require(path.is_file() and unaliased(path), "REGULAR_UNALIASED_OUTPUT_REQUIRED")
    return {"path": path.relative_to(root).as_posix(), "sha256": inputs._file_sha(path), "bytes": path.stat().st_size}


def write(root, relative, raw, token):
    require(type(raw) is bytes and (not token or token.encode() not in raw), "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    text = str(relative)
    require(text and not Path(relative).is_absolute() and "\\" not in text
            and all(p not in ("", ".", "..") for p in text.split("/")), "UNSAFE_RELATIVE_OUTPUT_PATH")
    path = root / relative
    require(root in path.parents and unaliased(path) and not path.exists(), "EXCLUSIVE_UNALIASED_OUTPUT_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
    return binding(root, path)


def pairs(scope):
    result = sorted((day, code) for day in scope["gap_dates"] for code in scope["gap_candidate_codes"][day])
    require(len(result) == len(set(result)) == MAX_CALLS and set(PREFLIGHT_PAIRS) <= set(result), "FROZEN_PAIR_SCOPE_CHANGED")
    return result


def code_snapshot(scope):
    code = {item["path"]: item["sha256"] for item in scope["code_bindings"]}
    code.update({name: inputs._file_sha(CHECKOUT / name) for name in CODE_FILES})
    return dict(sorted(code.items()))


def guard(code):
    require(all(inputs._file_sha(CHECKOUT / p) == s for p, s in _IMPORTED.items()), "COLLECTION_CODE_CHANGED_SINCE_IMPORT")
    require(all(inputs._file_sha(CHECKOUT / p) == s for p, s in code.items()), "COLLECTION_CODE_CHANGED")
    require(inputs._json((HERE / CONTRACT_FILE).read_bytes()) == expected_contract(), "REGISTERED_COLLECTION_CHANGED")
    source._guard()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def official_call(contract, token):
    params = contract.get("params", {}) if type(contract) is dict else {}
    require(contract == source.request_contract(params.get("trade_date"), params.get("ts_code")), "CANDIDATE_REQUEST_CHANGED")
    payload = {**contract, "fields": ",".join(source.FIELDS), "token": token}
    req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.build_opener(NoRedirect()).open(req, timeout=TIMEOUT) as response:
        raw = response.read(source.MAX_BYTES + 1)
    require(type(raw) is bytes and 0 < len(raw) <= source.MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
    return raw


class RequestBudget:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.started = self.next_start = clock()
        self.calls, self.lock = 0, threading.Lock()

    def reserve(self):
        with self.lock:
            if self.calls >= MAX_CALLS or self.clock() - self.started >= MAX_SECONDS - TIMEOUT:
                return None
            if self.next_start > self.clock():
                self.sleep(self.next_start - self.clock())
            if self.clock() - self.started >= MAX_SECONDS - TIMEOUT:
                return None
            self.calls += 1
            self.next_start = self.clock() + START_INTERVAL
            return self.calls


def blank(pair, status="NOT_REQUESTED_PREFLIGHT_BLOCKED"):
    day, code = pair
    return {"trade_date": day, "ts_code": code, "request": source.request_contract(day, code),
            "preflight": pair in PREFLIGHT_PAIRS, "api_calls": 0, "request_sequence": None,
            "status": status, "reason": None, "network_request_performed": False,
            "http_response_sha256": None, "http_response_bytes": None, "source_files": [], "rows": None}


def collect_one(root, pair, token, budget):
    day, code = pair
    item = blank(pair)
    if not token.strip():
        return {**item, "status": "PENDING_CREDENTIAL_ABSENT"}
    sequence = budget.reserve()
    if sequence is None:
        return {**item, "status": "NOT_REQUESTED_BUDGET_EXHAUSTED"}
    item.update(api_calls=1, request_sequence=sequence, network_request_performed=True, status="PENDING_NETWORK_OR_RESPONSE_ERROR")
    try:
        raw = official_call(item["request"], token)
        require(type(raw) is bytes and 0 < len(raw) <= source.MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
        item.update(http_response_sha256=sha(raw), http_response_bytes=len(raw))
        body, meta = source.source_bytes(raw, day, code, request=item["request"],
            fetched_at_utc=datetime.now(timezone.utc).isoformat(), network_request_performed=True, token=token)
    except source.AuctionSourceError as exc:
        reason = str(exc)
        return {**item, "status": "PENDING_INVALID_CANDIDATE_SOURCE",
                "reason": reason if re.fullmatch(r"[A-Z0-9_]{1,120}", reason) else "INVALID_CANDIDATE_SOURCE"}
    except error.HTTPError as exc:
        return {**item, "status": "PENDING_HTTP_ERROR", "reason": "HTTP_" + str(exc.code)
                if type(exc.code) is int and 100 <= exc.code <= 599 else "HTTP_ERROR"}
    except Exception:
        return item
    # A disk/readback failure is fatal, never converted into missing market data.
    files = [write(root, p.relative_to(root), raw, token)
             for p, raw in zip(source.source_paths(root, day, code), (body, meta))]
    loaded = source.load(root, day, code)
    require({b["path"]: b["sha256"] for b in files} ==
            {b["path"]: b["sha256"] for b in loaded.source_files}, "SOURCE_READBACK_MISMATCH")
    return {**item, "status": loaded.status, "rows": len(loaded.rows), "source_files": files}


def preflight_ok(item):
    return item["status"] == "CANDIDATE_TABLE_PRESENT" and type(item["rows"]) is int and item["rows"] == 1


def run_requests(root, scope, token, budget, progress=None):
    expected, results, passed = pairs(scope), {}, True
    for pair in PREFLIGHT_PAIRS:
        item = collect_one(root, pair, token, budget)
        results[pair] = item
        if progress:
            progress({"phase": "preflight", "trade_date": pair[0], "ts_code": pair[1], "status": item["status"]})
        if not preflight_ok(item):
            passed = False
            break
    if passed:
        remaining = [p for p in expected if p not in results]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for item in pool.map(lambda p: collect_one(root, p, token, budget), remaining):
                results[(item["trade_date"], item["ts_code"])] = item
                if progress and len(results) % 100 == 0:
                    progress({"phase": "collection", "completed_pairs": len(results), "api_calls": budget.calls})
    return [results.get(p, blank(p)) for p in expected], passed


def run_collection(base_archive, output, *, token, call=None, progress=None):
    require(type(token) is str and call is None, "REAL_TRANSPORT_REQUIRED_NO_INJECTION")
    require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "COLLECTION_RERUN_NOT_ALLOWED")
    scope = inputs.read_base_archive(base_archive)
    pairs(scope)
    code = code_snapshot(scope)
    guard(code)
    root = Path(output)
    require(not root.exists() and unaliased(root), "FRESH_UNALIASED_OUTPUT_REQUIRED")
    root = root.resolve()
    require(root != CHECKOUT and CHECKOUT not in root.parents and root not in CHECKOUT.parents,
            "OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    require(root not in Path(base_archive).resolve().parents, "BASE_ARCHIVE_INSIDE_OUTPUT")
    root.mkdir(parents=True, exist_ok=False)
    with Path(base_archive).open("rb") as src, (root / "base_v3.zip").open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024**2)
    inputs.verify_base_unchanged(root / "base_v3.zip")
    manifest = {"schema_version": "dc20_candidate_auction_gap_manifest_v1", "base_archive": scope["base_archive"],
                "scope_counts": dict(inputs.COUNTS), "gap_dates": scope["gap_dates"],
                "gap_candidate_codes": scope["gap_candidate_codes"], "reused_dates": scope["reused_dates"],
                "precoverage_dates": scope["precoverage_dates"], "preflight_pairs": [list(p) for p in PREFLIGHT_PAIRS],
                "frozen_manifest_sha256": sha(json_bytes(scope["frozen_manifest"])), "old_outcomes_used_for_scope": False}
    write(root, "gap_manifest.json", json_bytes(manifest), token)
    budget = RequestBudget()
    rows, passed = run_requests(root, scope, token, budget, progress)
    journal = sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"])
    require([r["request_sequence"] for r in journal] == list(range(1, budget.calls + 1)), "REQUEST_SEQUENCE_MISMATCH")
    write(root, JOURNAL_FILE, b"".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n"
                                     for r in journal), token)
    guard(code)
    inputs.verify_base_unchanged(base_archive)
    inputs.verify_base_unchanged(root / "base_v3.zip")
    files = sorted((b for r in rows for b in r["source_files"]), key=lambda b: b["path"])
    require(all(binding(root, root / b["path"]) == b for b in files), "COLLECTED_SOURCE_CHANGED")
    count = sum(r["status"] in source.STATUSES for r in rows)
    status = "PREFLIGHT_BLOCKED" if not passed else "SOURCE_PAIRS_COLLECTED" if count == MAX_CALLS else "SOURCE_PAIRS_PARTIAL"
    bindings = sorted((binding(root, p) for p in root.rglob("*") if p.is_file()), key=lambda b: b["path"])
    require({b["path"] for b in bindings} == {"base_v3.zip", "gap_manifest.json", JOURNAL_FILE} | {b["path"] for b in files},
            "UNREGISTERED_OUTPUT_FILE")
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schema_version": SCHEMA, "status": status, "base_archive": scope["base_archive"],
              "source_policy_id": source.SOURCE_POLICY_ID, "scope_counts": dict(inputs.COUNTS),
              "preflight_pairs": [list(p) for p in PREFLIGHT_PAIRS], "preflight_passed": passed,
              "eligible_as_real_collection": True, "callable_injected_for_test": False,
              "api_calls": budget.calls, "max_api_calls": MAX_CALLS, "retries": 0,
              "elapsed_collection_seconds": round(budget.clock() - budget.started, 3),
              "qualified_new_source_pairs": count, "reused_base_source_dates": len(scope["reused_dates"]),
              "new_source_status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
              "requests": rows, "source_files": files, "output_file_bindings": bindings,
              "execution_file_bindings": code, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
              "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None, **FLAGS}
    write(root, RECEIPT_FILE, json_bytes(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_collection(args.base_archive, args.output, token=os.environ.get("TUSHARE_TOKEN", ""),
                                progress=lambda item: print(json.dumps(item), flush=True))
        print(json.dumps({k: report[k] for k in ("status", "api_calls", "qualified_new_source_pairs")}))
        return 0 if report["status"] == "SOURCE_PAIRS_COLLECTED" else 2
    except Exception:
        print("CANDIDATE_AUCTION_COLLECTION_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
