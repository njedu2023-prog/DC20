#!/usr/bin/env python3
"""Registered, isolated exit-minute gap collection; no label/model execution.

Only the exact planned pairs are requested. The caller independently verifies
the upstream label report and missing-pair set; its SHA cannot prove that by
itself. BAR_END is still an unconfirmed research interpretation. Production
entry forbids injected transports; original HTTP and request receipts remain
external evidence to be independently audited before any later source use.
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
import stat
import sys
import threading
import time
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]
from work.profit_1000_upgrade import minute_truth as source

CONTRACT_FILE = "MINUTE_GAP_COLLECTION.json"
PLAN_COPY, RECEIPT_FILE, JOURNAL_FILE = "registered_minute_gap_plan.json", "minute_gap_receipt.json", "minute_gap_requests.jsonl"
SCHEMA = "dc20_research_minute_gap_collection_20260913_v1"
AS_OF_DATE, MIN_DATE = "20260911", "20221101"
MAX_CALLS, TIMEOUT_SECONDS, MAX_BYTES, MAX_WORKERS, MAX_SECONDS = 5000, 20, 1_000_000, 4, 4200
START_INTERVAL = 0.5
PINNED_DEPENDENCIES = {
    "work/profit_1000_upgrade/minute_truth.py": "69f2eb9fe2ceb1579a59d5247a3f1ddc72b80d251abbed9fe69e06e0d83df268",
    "src/top10decision/decision/shadow_exit_minute_truth.py": "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65"}
FLAGS = {"research_only": True, "source_only": True, "label_source_eligible": False,
         "label_rebuild_performed": False, "training_performed": False, "settlement_performed": False,
         "production_writes": False, "production_activation_allowed": False,
         "actual_execution_claimed": False, "actual_capacity_verified": False,
         "provider_timestamp_semantics_confirmed": False, "fallback_generated": False,
         "missing_values_imputed": False, "source_rows_deleted": False, "source_values_modified": False,
         "old_sources_overwritten": False, "raw_response_envelope_saved": False,
         "credential_persisted": False, "forward_holdout_touched": False,
         "upstream_label_report_verified_by_collector": False}


def require(condition, reason):
    if not condition: raise ValueError(reason)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_value(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "INVALID_REQUIRED_SHA256")
    return value


def unaliased(path):
    return ".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents))


def file_sha(path):
    path = Path(path)
    require(path.is_file() and unaliased(path) and stat.S_ISREG(path.stat().st_mode)
            and path.stat().st_nlink == 1, "REGULAR_UNALIASED_FILE_REQUIRED")
    with path.open("rb") as handle: return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED = {Path(__file__).resolve(): file_sha(Path(__file__))}


def guard():
    bindings = {**_IMPORTED, **{CHECKOUT / name: value for name, value in PINNED_DEPENDENCIES.items()}}
    require(all(file_sha(path) == digest for path, digest in bindings.items()), "MINUTE_COLLECTION_CODE_CHANGED")
    require(source.MAX_RESPONSE_BYTES == MAX_BYTES and source.MAX_DATA_BYTES == MAX_BYTES,
            "FROZEN_MINUTE_BYTE_LIMIT_CHANGED")
    source._verify_normalizer()
    return {path.relative_to(CHECKOUT).as_posix(): digest for path, digest in sorted(bindings.items())}


def identity(day, code):
    require(type(day) is str and re.fullmatch(r"[0-9]{8}", day) and MIN_DATE <= day <= AS_OF_DATE,
            "MINUTE_DATE_OUTSIDE_FROZEN_WINDOW")
    require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code), "EXACT_MINUTE_STOCK_REQUIRED")
    source._identity(day, code)
    return day, code


def request_contract(day, code):
    identity(day, code)
    return {"api_name": "stk_mins", "params": source.request_parameters(day, code), "fields": list(source.FIELDS)}


def expected_plan(pairs, label_report_sha256):
    """Registration builder only; the caller proves these are replayed gaps."""
    sha_value(label_report_sha256)
    require(type(pairs) in (list, tuple) and 1 <= len(pairs) <= MAX_CALLS, "INVALID_PLANNED_PAIR_COUNT")
    require(all(type(p) in (list, tuple) and len(p) == 2 for p in pairs), "INVALID_PLANNED_PAIR_SHAPE")
    normalized = [identity(*pair) for pair in pairs]
    require(normalized == sorted(set(normalized)), "PLANNED_PAIRS_MUST_BE_SORTED_UNIQUE")
    preflight = list(dict.fromkeys(normalized[i] for i in (0, len(normalized) // 2, len(normalized) - 1)))
    return {"schema_version": SCHEMA, "as_of_date": AS_OF_DATE, "minimum_trade_date": MIN_DATE,
        "label_report_sha256": label_report_sha256, "missing_evidence_kind": "research_exit_1000_1m_0931",
        "scope_basis": "INDEPENDENTLY_REPLAYED_LABEL_MISSING_MINUTE_PAIRS_ONLY",
        "pairs": [list(p) for p in normalized], "preflight_pairs": [list(p) for p in preflight],
        "preflight_rule": "ALL_UNIQUE_FIRST_MIDDLE_LAST_EXACT_240_BARS_BEFORE_BULK",
        "planned_pair_count": len(normalized), "max_api_calls": MAX_CALLS, "calls_per_pair": 1, "retries": 0,
        "api_name": "stk_mins", "fields": list(source.FIELDS), "query_start": "09:31:00", "query_end": "15:00:00",
        "continuous_rows": 240, "source_schema": source.SOURCE_SCHEMA, "source_adapter": source.ADAPTER,
        "time_semantics": source.TIME_SEMANTICS, "max_http_response_bytes": MAX_BYTES,
        "socket_timeout_seconds": TIMEOUT_SECONDS, "max_workers": MAX_WORKERS,
        "minimum_request_start_interval_seconds": START_INTERVAL, "request_start_budget_seconds": MAX_SECONDS,
        "request_start_headroom_seconds": TIMEOUT_SECONDS,
        "transport_timeout_semantics": "SOCKET_TIMEOUT_NOT_ABSOLUTE_RESPONSE_DEADLINE",
        "redirects_allowed": False, "pagination_or_request_fallback_allowed": False,
        "frozen_dependencies": dict(PINNED_DEPENDENCIES), **FLAGS}


def read_plan(path, expected_plan_sha256, expected_label_report_sha256):
    path = Path(path)
    sha_value(expected_plan_sha256); sha_value(expected_label_report_sha256)
    require(path.is_file() and unaliased(path) and path.stat().st_nlink == 1
            and 0 < path.stat().st_size <= 2_000_000, "INVALID_REGISTERED_PLAN_FILE")
    with path.open("rb") as handle: raw = handle.read(2_000_001)
    require(sha(raw) == expected_plan_sha256, "REGISTERED_PLAN_SHA_MISMATCH")
    plan = source._parse(raw)
    require(type(plan) is dict and plan.get("label_report_sha256") == expected_label_report_sha256,
            "EXPECTED_LABEL_REPORT_SHA_MISMATCH")
    require(raw == json_bytes(plan) == json_bytes(expected_plan(plan.get("pairs"), expected_label_report_sha256)),
            "REGISTERED_PLAN_CONTRACT_CHANGED")
    return plan, raw


def binding(root, path):
    require(path.is_file() and unaliased(path), "REGULAR_UNALIASED_OUTPUT_REQUIRED")
    return {"path": path.relative_to(root).as_posix(), "sha256": file_sha(path), "bytes": path.stat().st_size}


def inventory(root, expected):
    """No aliases, extra files, nonregular nodes, or unrelated directories."""
    allowed_dirs = {p for name in expected for p in (root / name).parents if root in p.parents}
    found = []
    for path in root.rglob("*"):
        require(unaliased(path), "ALIASED_OUTPUT_FORBIDDEN")
        if path.is_dir(): require(path in allowed_dirs, "UNREGISTERED_OUTPUT_DIRECTORY")
        else: found.append(binding(root, path))
    require({b["path"] for b in found} == expected, "UNREGISTERED_OUTPUT_FILE")
    return sorted(found, key=lambda b: b["path"])


def write(root, relative, raw, token):
    text = str(relative)
    require(text and not Path(relative).is_absolute() and "\\" not in text
            and all(p not in ("", ".", "..") for p in text.split("/")), "UNSAFE_OUTPUT_RELATIVE_PATH")
    require(type(raw) is bytes and (not token or token.encode() not in raw), "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    path = root / relative
    require(root in path.parents and unaliased(path) and not path.exists(), "EXCLUSIVE_UNALIASED_OUTPUT_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle: handle.write(raw)
    return binding(root, path)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def official_call(contract, token):
    require(type(contract) is dict and type(contract.get("params")) is dict, "EXACT_MINUTE_REQUEST_REQUIRED")
    params = contract["params"]
    start, code = params.get("start_date"), params.get("ts_code")
    require(type(start) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} 09:31:00", start), "EXACT_0931_REQUEST_REQUIRED")
    day = start[:10].replace("-", "")
    require(json_bytes(contract) == json_bytes(request_contract(day, code)), "EXACT_MINUTE_REQUEST_REQUIRED")
    require(type(token) is str, "INVALID_CREDENTIAL_ARGUMENT")
    payload = {**contract, "fields": ",".join(source.FIELDS), "token": token}
    req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    with request.build_opener(NoRedirect()).open(req, timeout=TIMEOUT_SECONDS) as reply: raw = reply.read(MAX_BYTES + 1)
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
    return raw


class RequestBudget:
    def __init__(self, total, *, clock=time.monotonic, sleep=time.sleep):
        require(type(total) is int and 1 <= total <= MAX_CALLS, "INVALID_CALL_BUDGET")
        self.total, self.clock, self.sleep = total, clock, sleep
        self.started = self.next_start = clock()
        self.calls, self.lock = 0, threading.Lock()

    def reserve(self):
        with self.lock:
            if self.calls >= self.total or self.clock() - self.started >= MAX_SECONDS - TIMEOUT_SECONDS: return None
            if self.next_start > self.clock(): self.sleep(self.next_start - self.clock())
            now = self.clock()
            if now - self.started >= MAX_SECONDS - TIMEOUT_SECONDS: return None
            self.calls += 1
            self.next_start = now + START_INTERVAL
            return self.calls, round(now - self.started, 6)


def blank(pair, preflight):
    day, code = pair
    return {"trade_date": day, "ts_code": code, "request": request_contract(day, code),
        "preflight": pair in preflight, "status": "NOT_REQUESTED_PREFLIGHT_BLOCKED", "api_calls": 0,
        "request_sequence": None, "request_start_elapsed_seconds": None, "network_request_performed": False,
        "http_response_sha256": None, "http_response_bytes": None, "source_files": [], "source_rows": None, "reason": None}


def _credential_check(raw, token):
    require(not token or token.encode() not in raw, "CREDENTIAL_IN_HTTP_RESPONSE")
    parsed = source._parse(raw)
    json_bytes(parsed)  # Reject nonfinite JSON anywhere, including ignored envelope fields.
    todo = [parsed]
    while todo:
        item = todo.pop()
        if type(item) is dict: todo.extend(item.keys()); todo.extend(item.values())
        elif type(item) is list: todo.extend(item)
        elif type(item) is str: require(not token or token not in item, "CREDENTIAL_IN_HTTP_RESPONSE")


def collect_one(root, pair, preflight, token, budget):
    item = blank(pair, preflight)
    if not token.strip(): return {**item, "status": "PENDING_CREDENTIAL_ABSENT"}
    reserved = budget.reserve()
    if reserved is None: return {**item, "status": "NOT_REQUESTED_BUDGET_EXHAUSTED"}
    item.update(api_calls=1, request_sequence=reserved[0], request_start_elapsed_seconds=reserved[1],
                network_request_performed=True, status="PENDING_NETWORK_OR_RESPONSE_ERROR")
    stage = "HTTP_TRANSPORT"
    try:
        raw = official_call(item["request"], token)
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
        item.update(http_response_sha256=sha(raw), http_response_bytes=len(raw))
        stage = "STRICT_RESPONSE_AND_CREDENTIAL_GUARD"
        _credential_check(raw, token)
        stage = "FROZEN_EXACT_240_BAR_VALIDATION"
        bodies = source.source_bytes(raw, *pair, request_params=item["request"]["params"],
            fetched_at_utc=datetime.now(timezone.utc).isoformat(), token=token)
    except error.HTTPError as exc:
        return {**item, "status": "PENDING_HTTP_ERROR", "reason": "HTTP_" + str(exc.code) if type(exc.code) is int and 100 <= exc.code <= 599 else "HTTP_ERROR"}
    except (ValueError, TypeError):
        return {**item, "status": "PENDING_INVALID_MINUTE_SOURCE", "reason": stage + "_FAILED"}
    except Exception:
        return item
    files = [write(root, path.relative_to(root), body, token) for path, body in zip(source.paths(root, *pair), bodies)]
    loaded = source.load(root, *pair)
    require(loaded is not None and len(loaded["rows"]) == 240 and loaded["time_semantics"] == source.TIME_SEMANTICS
            and loaded["provider_timestamp_semantics_confirmed"] is False and loaded["production_activation_allowed"] is False,
            "FROZEN_MINUTE_READBACK_CONTRACT_CHANGED")
    require([{k: b[k] for k in ("path", "sha256")} for b in files] == loaded["source_files"], "SOURCE_READBACK_BINDING_MISMATCH")
    return {**item, "status": "RESEARCH_MINUTE_SOURCE_WRITTEN", "source_rows": 240, "source_files": files}


def run_requests(root, plan, token, budget, progress=None):
    pairs, preflight = [tuple(p) for p in plan["pairs"]], [tuple(p) for p in plan["preflight_pairs"]]
    results, passed = {}, True
    for pair in preflight:
        item = collect_one(root, pair, preflight, token, budget)
        results[pair] = item
        if progress: progress({"phase": "preflight", "trade_date": pair[0], "ts_code": pair[1], "status": item["status"]})
        if item["status"] != "RESEARCH_MINUTE_SOURCE_WRITTEN": passed = False; break
    if passed:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for item in pool.map(lambda p: collect_one(root, p, preflight, token, budget), [p for p in pairs if p not in results]):
                results[(item["trade_date"], item["ts_code"])] = item
                if progress and len(results) % 100 == 0: progress({"phase": "collection", "completed_pairs": len(results), "api_calls": budget.calls})
    return [results.get(pair, blank(pair, preflight)) for pair in pairs], passed


def run_collection(output, *, expected_plan_sha256, expected_label_report_sha256, token,
                   plan_path=HERE / CONTRACT_FILE, call=None, progress=None):
    require(type(token) is str and call is None, "REAL_TRANSPORT_REQUIRED_NO_INJECTION")
    require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "COLLECTION_RERUN_NOT_ALLOWED")
    code = guard()
    plan, raw_plan = read_plan(plan_path, expected_plan_sha256, expected_label_report_sha256)
    root = Path(output)
    require(not root.exists() and unaliased(root), "FRESH_UNALIASED_OUTPUT_REQUIRED")
    root = root.resolve()
    require(root != CHECKOUT and CHECKOUT not in root.parents and root not in CHECKOUT.parents, "OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    root.mkdir(parents=True, exist_ok=False)
    write(root, PLAN_COPY, raw_plan, token)
    budget = RequestBudget(len(plan["pairs"]))
    rows, passed = run_requests(root, plan, token, budget, progress)
    journal = sorted((row for row in rows if row["api_calls"]), key=lambda row: row["request_sequence"])
    require([row["request_sequence"] for row in journal] == list(range(1, budget.calls + 1)), "REQUEST_SEQUENCE_CHANGED")
    write(root, JOURNAL_FILE, b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n" for r in journal), token)
    require(guard() == code and read_plan(plan_path, expected_plan_sha256, expected_label_report_sha256)[1] == raw_plan,
            "CODE_OR_PLAN_CHANGED_DURING_COLLECTION")
    sources = sorted((b for row in rows for b in row["source_files"]), key=lambda b: b["path"])
    require(all(binding(root, root / b["path"]) == b for b in sources), "COLLECTED_SOURCE_CHANGED")
    files = inventory(root, {PLAN_COPY, JOURNAL_FILE} | {b["path"] for b in sources})
    count = sum(row["status"] == "RESEARCH_MINUTE_SOURCE_WRITTEN" for row in rows)
    status = "PREFLIGHT_BLOCKED" if not passed else "MINUTE_GAPS_COLLECTED" if count == len(rows) else "MINUTE_GAPS_PARTIAL"
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schema_version": SCHEMA, "status": status, "plan_sha256": expected_plan_sha256,
        "label_report_sha256": expected_label_report_sha256, "as_of_date": AS_OF_DATE,
        "planned_pair_count": len(rows), "preflight_pairs": plan["preflight_pairs"], "preflight_passed": passed,
        "api_calls": budget.calls, "max_api_calls": MAX_CALLS, "retries": 0, "qualified_source_pairs": count,
        "request_status_counts": dict(Counter(row["status"] for row in rows)),
        "requests": rows, "source_files": sources, "output_file_bindings": files,
        "execution_file_bindings": code, "time_semantics": source.TIME_SEMANTICS,
        "callable_injected_for_test": False, "eligible_as_real_collection": True,
        "elapsed_collection_seconds": round(budget.clock() - budget.started, 6),
        "run_id": run_id if re.fullmatch(r"[0-9]{1,20}", run_id) else None,
        "run_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None,
        "run_identity_basis": "ENVIRONMENT_CLAIMS_REQUIRE_EXTERNAL_VERIFICATION",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(), **FLAGS}
    write(root, RECEIPT_FILE, json_bytes(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path, default=HERE / CONTRACT_FILE)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-label-report-sha256", required=True)
    args = parser.parse_args()
    try:
        report = run_collection(args.output, plan_path=args.plan, expected_plan_sha256=args.expected_plan_sha256,
            expected_label_report_sha256=args.expected_label_report_sha256, token=os.environ.get("TUSHARE_TOKEN", ""),
            progress=lambda value: print(json.dumps(value), flush=True))
        print(json.dumps({key: report[key] for key in ("status", "api_calls", "planned_pair_count", "qualified_source_pairs")}))
        return 0 if report["status"] == "MINUTE_GAPS_COLLECTED" else 2
    except Exception:
        print("MINUTE_GAP_COLLECTION_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
