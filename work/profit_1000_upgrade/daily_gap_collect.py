#!/usr/bin/env python3
"""Fixed fourteen-request source-only daily/suspension evidence collection.

Empty daily tables are retained as exact query evidence, never as automatic
suspension, prices, labels or permission to advance an exit. Each transport
has a parent-process deadline in addition to its socket timeout. No response
text, credential, original source or historical outcome is rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib import error, request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SCHEMA = "dc20_research_daily_gap_collection_20260913_v1"
SOURCE_SCHEMA = "dc20_research_daily_gap_source_20260913_v1"
CONTRACT_FILE = "DAILY_GAP_COLLECTION.json"
PLAN_COPY = "registered_daily_gap_plan.json"
RECEIPT_FILE = "daily_gap_receipt.json"
JOURNAL_FILE = "daily_gap_requests.jsonl"
SOURCE_ROOT = "research_inputs/daily_gap_sources_v1"
LABEL_SHA = "2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609"
# Exact registered diagnostic identity; no diagnostic bytes are imported here.
DIAGNOSTIC_SHA = "760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9"
DIAGNOSTIC_RUN = "34672430723"
DIAGNOSTIC_COMMIT = "6ea9616014da3850ae4064c89e56e832608180d3"
AS_OF_DATE, MAX_SIGNAL_DATE = "20260911", "20260814"
MAX_CALLS, MAX_BYTES, SOCKET_TIMEOUT, TRANSPORT_TIMEOUT, WALL_BUDGET = 14, 1_000_000, 20, 20, 600
START_INTERVAL = 0.5
FIELDS = {"daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg"),
          "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type")}
SCOPE = (("daily", "20240430", "600083.SH"), ("suspend_d", "20240430", "600083.SH"),
         ("daily", "20250611", "603226.SH"), ("daily", "20250612", "603226.SH"),
         ("daily", "20251118", "603122.SH"), ("daily", "20251119", "603122.SH"),
         ("daily", "20260708", "603580.SH"), ("daily", "20260709", "603580.SH"),
         ("daily", "20260710", "603580.SH"), ("daily", "20260713", "603580.SH"),
         ("daily", "20260724", "002036.SZ"), ("daily", "20260727", "002036.SZ"),
         ("daily", "20260728", "002036.SZ"), ("daily", "20260729", "002036.SZ"))
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
         "upstream_label_report_verified_by_collector": False,
         "diagnostic_archive_imported": False}
SUCCESS = {"DAILY_EMPTY_SOURCE_WRITTEN", "DAILY_ROW_SOURCE_WRITTEN",
           "SUSPEND_EMPTY_SOURCE_WRITTEN", "SUSPEND_EVENTS_SOURCE_WRITTEN"}
SOURCE_FLAGS = {"source_only": True, "immutable": True, "source_values_modified": False,
                "label_source_eligible": False, "nontrading_session_qualified": False,
                "synthetic_ohlc_created": False, "credential_persisted": False,
                "raw_response_envelope_saved": False, "server_messages_persisted": False}


def require(condition, reason):
    if not condition: raise ValueError(reason)


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(values):
        out = {}
        for key, value in values:
            require(key not in out, "DUPLICATE_JSON_KEY")
            out[key] = value
        return out
    def constant(value): raise ValueError("NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def unaliased(path):
    return ".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents))


def file_sha(path):
    path = Path(path)
    require(unaliased(path) and path.is_file() and stat.S_ISREG(path.stat().st_mode)
            and path.stat().st_nlink == 1, "REGULAR_UNALIASED_FILE_REQUIRED")
    with path.open("rb") as handle: return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_SHA = file_sha(Path(__file__))


def guard():
    require(file_sha(Path(__file__)) == _IMPORTED_SHA, "DAILY_GAP_COLLECTOR_CHANGED")
    return {Path(__file__).relative_to(CHECKOUT).as_posix(): _IMPORTED_SHA}


def request_contract(api, day, code):
    require((api, day, code) in SCOPE, "REQUEST_OUTSIDE_EXACT_REGISTERED_SCOPE")
    return {"api_name": api, "params": {"ts_code": code, "trade_date": day}, "fields": list(FIELDS[api])}


def expected_plan():
    return {"schema_version": SCHEMA, "source_schema": SOURCE_SCHEMA,
            "label_report_sha256": LABEL_SHA, "as_of_date": AS_OF_DATE,
            "maximum_signal_date": MAX_SIGNAL_DATE,
            "diagnostic_archive": {"sha256": DIAGNOSTIC_SHA, "bytes": 19600,
                "run_id": DIAGNOSTIC_RUN, "run_commit": DIAGNOSTIC_COMMIT},
            "missing_daily_cases": [{"signal_date": d, "exec_date": t, "missing_date": day, "ts_code": code}
                                    for d, t, day, code in CASES],
            "requests": [{"ordinal": i, **request_contract(*scope)} for i, scope in enumerate(SCOPE, 1)],
            "planned_request_count": MAX_CALLS, "endpoint_counts": {"daily": 13, "suspend_d": 1},
            "max_api_calls": MAX_CALLS, "calls_per_request": 1, "retries": 0, "max_workers": 1,
            "minimum_request_start_interval_seconds": START_INTERVAL,
            "socket_timeout_seconds": SOCKET_TIMEOUT, "transport_wall_timeout_seconds": TRANSPORT_TIMEOUT,
            "request_start_budget_seconds": WALL_BUDGET, "request_start_headroom_seconds": TRANSPORT_TIMEOUT + 1,
            "transport_timeout_semantics": "PARENT_PROCESS_RESPONSE_DEADLINE_PLUS_SOCKET_TIMEOUT",
            "max_http_response_bytes": MAX_BYTES, "max_rows_per_exact_query": 1,
            "redirects_allowed": False, "pagination_or_request_fallback_allowed": False,
            "allowed_run_attempt": 1, "source_namespace": SOURCE_ROOT,
            "daily_empty_rule": "RETAIN_EXACT_QUERY_EVIDENCE_NOT_AUTOMATIC_SUSPENSION",
            "suspend_event_rule": "RETAIN_EXACT_DATE_CODE_EVENT_NOT_NONTRADING_ADMISSION",
            "scope_basis": "FROZEN_SIX_MISSING_DAILY_CASES_AND_EXPLICIT_DIAGNOSTIC_S_DATES",
            **FLAGS}


def read_plan(path, expected_plan_sha256, expected_label_report_sha256):
    require(type(expected_plan_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256), "INVALID_EXPECTED_PLAN_SHA")
    require(expected_label_report_sha256 == LABEL_SHA, "EXPECTED_LABEL_REPORT_SHA_MISMATCH")
    path = Path(path)
    require(unaliased(path) and path.is_file() and 0 < path.stat().st_size <= 100_000, "INVALID_REGISTERED_PLAN_FILE")
    require(file_sha(path) == expected_plan_sha256, "REGISTERED_PLAN_SHA_MISMATCH")
    raw = path.read_bytes()
    require(raw == json_bytes(parse(raw)) == json_bytes(expected_plan()), "REGISTERED_PLAN_CONTRACT_CHANGED")
    return parse(raw), raw


def source_paths(api, day, code):
    request_contract(api, day, code)
    stem = f"{SOURCE_ROOT}/{api}/{day[:4]}/{day}/{code}"
    return stem + ".data.json", stem + ".meta.json"


def _numeric(value, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), "INVALID_NUMERIC_SOURCE_VALUE")
    if positive: require(value > 0, "NONPOSITIVE_DAILY_PRICE")
    return value


def _timing(value):
    if value in (None, ""): return
    require(type(value) is str and re.fullmatch(r"[0-9]{2}:[0-9]{2}-[0-9]{2}:[0-9]{2}", value), "INVALID_SUSPEND_TIMING")
    start, end = (datetime.strptime(v, "%H:%M") for v in value.split("-"))
    require(start < end, "INVALID_SUSPEND_TIMING")


def qualified_table(raw, contract, token):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
    require(not token or token.encode() not in raw, "CREDENTIAL_IN_RESPONSE")
    value = parse(raw)
    todo = [value]
    while todo:
        item = todo.pop()
        if type(item) is dict: todo.extend(item.keys()); todo.extend(item.values())
        elif type(item) is list: todo.extend(item)
        elif type(item) is str: require(not token or token not in item, "CREDENTIAL_IN_RESPONSE")
        elif type(item) is float: require(math.isfinite(item), "NONFINITE_RESPONSE")
    require(type(value) is dict and set(value) <= {"code", "data", "msg", "request_id", "detail"}
            and type(value.get("code")) is int and value["code"] == 0, "INVALID_API_ENVELOPE")
    require(all(value.get(k) is None or type(value[k]) is str for k in ("msg", "request_id", "detail")), "INVALID_API_TEXT_FIELD")
    api, params = contract["api_name"], contract["params"]
    require(contract == request_contract(api, params["trade_date"], params["ts_code"]), "EXACT_REQUEST_REQUIRED")
    data = value.get("data")
    require(type(data) is dict and set(data) == {"fields", "items", "count", "has_more"}
            and data["fields"] == list(FIELDS[api]) and type(data["items"]) is list
            and type(data["count"]) is int and data["count"] in (0, len(data["items"]))
            and data["has_more"] is False and len(data["items"]) <= 1, "INCOMPLETE_OR_INVALID_TABLE")
    for values in data["items"]:
        require(type(values) is list and len(values) == len(FIELDS[api]), "INVALID_SOURCE_ROW")
        row = dict(zip(FIELDS[api], values))
        require(row["ts_code"] == params["ts_code"] and row["trade_date"] == params["trade_date"], "SOURCE_ROW_IDENTITY_MISMATCH")
        if api == "daily":
            for name in ("open", "high", "low", "close", "pre_close"): _numeric(row[name], True)
            require(row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"], "INVALID_DAILY_OHLC")
            require(_numeric(row["vol"]) >= 0 and _numeric(row["amount"]) >= 0, "NEGATIVE_DAILY_ACTIVITY")
            _numeric(row["pct_chg"])
        else:
            require(row["suspend_type"] in ("S", "R"), "INVALID_SUSPEND_EVENT")
            _timing(row["suspend_timing"])
    if api == "daily": status = "DAILY_ROW_SOURCE_WRITTEN" if data["items"] else "DAILY_EMPTY_SOURCE_WRITTEN"
    else: status = "SUSPEND_EVENTS_SOURCE_WRITTEN" if data["items"] else "SUSPEND_EMPTY_SOURCE_WRITTEN"
    return data, status


def metadata(data_raw, row):
    fetched = datetime.fromisoformat(row["fetched_at_utc"])
    require(fetched.tzinfo is not None and fetched.utcoffset().total_seconds() == 0
            and fetched.strftime("%Y%m%d") >= row["request"]["params"]["trade_date"], "INVALID_SOURCE_FETCH_TIME")
    return {"schema_version": SOURCE_SCHEMA, "ordinal": row["ordinal"], "request": row["request"],
            "fetched_at_utc": row["fetched_at_utc"], "api_code": 0,
            "http_response_sha256": row["http_response_sha256"], "http_response_bytes": row["http_response_bytes"],
            "data_sha256": sha(data_raw), "data_bytes": len(data_raw), "rows": row["source_rows"],
            "status": row["status"], "network_request_performed": True,
            "complete_table_verified": True, **SOURCE_FLAGS}


def binding(root, path):
    return {"path": path.relative_to(root).as_posix(), "sha256": file_sha(path), "bytes": path.stat().st_size}


def write(root, relative, raw, token):
    require(type(relative) is str and relative and "\\" not in relative
            and not Path(relative).is_absolute() and all(p not in ("", ".", "..") for p in relative.split("/")), "UNSAFE_OUTPUT_PATH")
    require(type(raw) is bytes and (not token or token.encode() not in raw), "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    path = root / relative
    require(unaliased(path) and not path.exists(), "EXCLUSIVE_UNALIASED_OUTPUT_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle: handle.write(raw)
    return binding(root, path)


def append_journal(root, row, token):
    path = root / JOURNAL_FILE
    file_sha(path)
    raw = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    require(not token or token.encode() not in raw, "CREDENTIAL_PERSISTENCE_FORBIDDEN")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REGULAR_UNALIASED_JOURNAL_REQUIRED")
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    finally: os.close(descriptor)


def inventory(root, expected):
    allowed_dirs = {p for name in expected for p in (root / name).parents if root in p.parents}
    found = []
    for path in root.rglob("*"):
        require(unaliased(path), "ALIASED_OUTPUT_FORBIDDEN")
        if path.is_dir(): require(path in allowed_dirs, "UNREGISTERED_OUTPUT_DIRECTORY")
        else: found.append(binding(root, path))
    require({b["path"] for b in found} == expected, "UNREGISTERED_OUTPUT_FILE")
    return sorted(found, key=lambda item: item["path"])


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def _transport_worker(connection, contract, token):
    try:
        payload = {**contract, "fields": ",".join(contract["fields"]), "token": token}
        req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
        with request.build_opener(NoRedirect()).open(req, timeout=SOCKET_TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
        connection.send_bytes(b"S" + raw if 0 < len(raw) <= MAX_BYTES else b"EHTTP_RESPONSE_BYTE_LIMIT")
    except error.HTTPError as exc:
        connection.send_bytes(b"EHTTP_ERROR")
    except Exception:
        connection.send_bytes(b"ETRANSPORT_ERROR")
    finally: connection.close()


class TransportFailure(ValueError):
    pass


def official_call(contract, token):
    params = contract.get("params", {})
    require(contract == request_contract(contract.get("api_name"), params.get("trade_date"), params.get("ts_code")), "EXACT_REQUEST_REQUIRED")
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_transport_worker, args=(send, contract, token), daemon=True)
    try:
        process.start(); send.close()
        if not receive.poll(TRANSPORT_TIMEOUT): raise TransportFailure("TRANSPORT_TIMEOUT")
        try: packet = receive.recv_bytes(MAX_BYTES + 1)
        except (EOFError, OSError): raise TransportFailure("TRANSPORT_ERROR") from None
        if packet.startswith(b"S") and 1 < len(packet) <= MAX_BYTES + 1: return packet[1:]
        reason = packet[1:].decode("ascii", errors="ignore")
        raise TransportFailure(reason if reason in ("HTTP_ERROR", "TRANSPORT_ERROR", "HTTP_RESPONSE_BYTE_LIMIT") else "TRANSPORT_ERROR")
    finally:
        send.close(); receive.close()
        if process.pid is not None:
            if process.is_alive(): process.terminate()
            process.join(timeout=0.5)
            if process.is_alive(): process.kill(); process.join(timeout=0.5)
            require(not process.is_alive(), "TRANSPORT_PROCESS_NOT_STOPPED")
            process.close()


class RequestBudget:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.started = self.next_start = clock()
        self.calls = 0

    def reserve(self):
        if self.calls >= MAX_CALLS or self.clock() - self.started >= WALL_BUDGET - TRANSPORT_TIMEOUT - 1: return None
        if self.next_start > self.clock(): self.sleep(self.next_start - self.clock())
        now = self.clock()
        if now - self.started >= WALL_BUDGET - TRANSPORT_TIMEOUT - 1: return None
        self.calls += 1; self.next_start = now + START_INTERVAL
        return self.calls, round(now - self.started, 6)


def blank(planned):
    return {"ordinal": planned["ordinal"], "request": {k: v for k, v in planned.items() if k != "ordinal"},
            "status": "NOT_REQUESTED_WALL_BUDGET", "reason": None, "api_calls": 0,
            "request_sequence": None, "request_start_elapsed_seconds": None, "request_finished_elapsed_seconds": None,
            "network_request_performed": False, "fetched_at_utc": None,
            "http_response_sha256": None, "http_response_bytes": None, "source_rows": None, "source_files": []}


def collect_one(root, planned, token, budget):
    row = blank(planned)
    if not token.strip(): return {**row, "status": "PENDING_CREDENTIAL_ABSENT"}
    reserved = budget.reserve()
    if reserved is None: return row
    row.update(api_calls=1, request_sequence=reserved[0], request_start_elapsed_seconds=reserved[1], network_request_performed=True)
    try:
        raw = official_call(row["request"], token)
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "HTTP_RESPONSE_BYTE_LIMIT")
        row.update(http_response_sha256=sha(raw), http_response_bytes=len(raw), fetched_at_utc=datetime.now(timezone.utc).isoformat())
        data, status = qualified_table(raw, row["request"], token)
    except TransportFailure as exc:
        reason = str(exc)
        reason = reason if reason in ("TRANSPORT_TIMEOUT", "HTTP_ERROR", "TRANSPORT_ERROR", "HTTP_RESPONSE_BYTE_LIMIT") else "TRANSPORT_ERROR"
        row.update(status="PENDING_" + reason, reason=reason)
    except (ValueError, TypeError, KeyError, OverflowError):
        row.update(status="PENDING_INVALID_SOURCE", reason="STRICT_SOURCE_VALIDATION_FAILED")
    except Exception:
        row.update(status="PENDING_TRANSPORT_ERROR", reason="TRANSPORT_ERROR")
    else:
        row.update(status=status, source_rows=len(data["items"]))
        data_raw = json_bytes(data)
        paths = source_paths(row["request"]["api_name"], row["request"]["params"]["trade_date"], row["request"]["params"]["ts_code"])
        row["source_files"] = [write(root, relative, body, token) for relative, body in zip(paths, (data_raw, json_bytes(metadata(data_raw, row))))]
    row["request_finished_elapsed_seconds"] = round(budget.clock() - budget.started, 6)
    append_journal(root, row, token)
    return row


def run_collection(output, *, expected_plan_sha256, expected_label_report_sha256,
                   expected_run_id, expected_run_commit, token, plan_path=HERE / CONTRACT_FILE, call=None, progress=None):
    require(type(token) is str and call is None, "REAL_TRANSPORT_REQUIRED_NO_INJECTION")
    require(type(expected_run_id) is str and re.fullmatch(r"[0-9]{1,20}", expected_run_id)
            and type(expected_run_commit) is str and re.fullmatch(r"[0-9a-f]{40}", expected_run_commit), "EXACT_RUN_IDENTITY_REQUIRED")
    require(os.environ.get("GITHUB_RUN_ID") == expected_run_id and os.environ.get("GITHUB_SHA") == expected_run_commit
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "RUN_IDENTITY_OR_ATTEMPT_MISMATCH")
    code = guard()
    plan, raw_plan = read_plan(plan_path, expected_plan_sha256, expected_label_report_sha256)
    root = Path(output)
    require(not root.exists() and unaliased(root), "FRESH_UNALIASED_OUTPUT_REQUIRED")
    root = root.resolve()
    require(root != CHECKOUT and CHECKOUT not in root.parents and root not in CHECKOUT.parents, "OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    root.mkdir(parents=True, exist_ok=False)
    write(root, PLAN_COPY, raw_plan, token); write(root, JOURNAL_FILE, b"", token)
    budget = RequestBudget()
    rows = []
    for planned in plan["requests"]:
        row = collect_one(root, planned, token, budget); rows.append(row)
        if progress: progress({"ordinal": row["ordinal"], "status": row["status"], "api_calls": budget.calls})
    require(guard() == code and read_plan(plan_path, expected_plan_sha256, expected_label_report_sha256)[1] == raw_plan, "CODE_OR_PLAN_CHANGED_DURING_COLLECTION")
    journal = [parse(line) for line in (root / JOURNAL_FILE).read_bytes().splitlines()]
    require(journal == [row for row in rows if row["api_calls"]]
            and [r["request_sequence"] for r in journal] == list(range(1, budget.calls + 1)), "REQUEST_JOURNAL_MISMATCH")
    sources = sorted((b for row in rows for b in row["source_files"]), key=lambda b: b["path"])
    require(all(binding(root, root / item["path"]) == item for item in sources), "SOURCE_CHANGED_DURING_COLLECTION")
    files = inventory(root, {PLAN_COPY, JOURNAL_FILE} | {b["path"] for b in sources})
    count = sum(row["status"] in SUCCESS for row in rows)
    report = {"schema_version": SCHEMA, "status": "DAILY_GAP_SOURCES_COLLECTED" if count == MAX_CALLS else "DAILY_GAP_SOURCES_PARTIAL" if count else "DAILY_GAP_SOURCES_BLOCKED",
              "plan_sha256": expected_plan_sha256, "label_report_sha256": LABEL_SHA, "as_of_date": AS_OF_DATE,
              "diagnostic_archive": plan["diagnostic_archive"], "planned_request_count": MAX_CALLS,
              "api_calls": budget.calls, "max_api_calls": MAX_CALLS, "retries": 0,
              "qualified_source_pairs": count, "request_status_counts": dict(Counter(r["status"] for r in rows)),
              "requests": rows, "source_files": sources, "output_file_bindings": files,
              "execution_file_bindings": code, "elapsed_collection_seconds": round(budget.clock() - budget.started, 6),
              "run_id": expected_run_id, "run_commit": expected_run_commit, "run_attempt": 1,
              "run_identity_basis": "ENVIRONMENT_CLAIMS_REQUIRE_EXTERNAL_VERIFICATION",
              "callable_injected_for_test": False, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **FLAGS}
    write(root, RECEIPT_FILE, json_bytes(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registered-plan", type=Path, default=HERE / CONTRACT_FILE)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-label-report-sha256", required=True)
    args = parser.parse_args()
    try:
        report = run_collection(args.output, plan_path=args.registered_plan, expected_plan_sha256=args.expected_plan_sha256,
            expected_label_report_sha256=args.expected_label_report_sha256, expected_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            expected_run_commit=os.environ.get("GITHUB_SHA", ""), token=os.environ.get("TUSHARE_TOKEN", ""),
            progress=lambda item: print(json.dumps(item, sort_keys=True), flush=True))
        print(json.dumps({k: report[k] for k in ("status", "api_calls", "qualified_source_pairs")}, sort_keys=True))
        return 0 if report["status"] == "DAILY_GAP_SOURCES_COLLECTED" else 2
    except Exception:
        print("DAILY_GAP_COLLECTION_FAILED_CLOSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
