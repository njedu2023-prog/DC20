"""User-authorized, finite, source-only collection for frozen research slots.

Only the fixed official Tushare endpoint is callable. Credentials come only
from TUSHARE_TOKEN. Tests inject a transport and never claim real network use.
One earliest missing session per stock is considered; no historical sweep,
orders, purchased quota, training, source admission or economic rule changes.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib import request

ROOT = Path(__file__).absolute().parents[2]
OUTCOMES_PATH = Path(__file__).with_name("candidate_natural_outcomes.py")
OUTCOMES_SHA = "5949be11309eebba1a3d5f45be9b56d4469b1d6e51f2c416960c9920699d7c18"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(body): return hashlib.sha256(body).hexdigest()


require(not any(p.is_symlink() for p in (OUTCOMES_PATH, *OUTCOMES_PATH.parents))
    and sha(OUTCOMES_PATH.read_bytes()) == OUTCOMES_SHA, "PINNED_OUTCOME_WRAPPER_CHANGED")
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes

natural, labels = outcomes.natural, outcomes.labels
SELF_SHA = sha(natural._read(Path(__file__).absolute())[0])
SCHEMA = "dc20_candidate_natural_outcome_collection_v1"
MAX_CALLS, MAX_SECONDS, MAX_BYTES, MAX_TOTAL_BYTES = 128, 300, 1_000_000, 64_000_000
TIMEOUT, HEADROOM, INTERVAL = 20, 21, .5
FIELDS = {
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg"),
    "stk_limit": ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
}
FLAGS = {"research_only": True, "source_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "natural_forward_admission_issued": False,
    "git_publication_verified": False, "actual_execution_claimed": False,
    "actual_capacity_verified": False, "provider_timestamp_semantics_confirmed": False,
    "nontrading_session_qualified": False, "label_or_settlement_computed": False,
    "training_performed": False, "old_sources_overwritten": False,
    "missing_values_imputed": False, "synthetic_prices_created": False,
    "fallback_generated": False, "credential_persisted": False, "provider_error_text_logged": False}


def code_guard():
    raw, identity = natural._read(OUTCOMES_PATH)
    require(sha(raw) == OUTCOMES_SHA and Path(outcomes.__file__).absolute() == OUTCOMES_PATH,
        "PINNED_OUTCOME_WRAPPER_CHANGED")
    own, own_identity = natural._read(Path(__file__).absolute())
    require(sha(own) == SELF_SHA, "NATURAL_COLLECTOR_CHANGED")
    return identity, own_identity, outcomes._guard()


def request_contract(api, day, code):
    natural.scorer._date(day, "request_date")
    require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code), "EXACT_STOCK_REQUIRED")
    if api == "stk_auction": return labels.auction_truth.request_contract(day, code)
    if api == "stk_mins":
        return {"api_name": api, "params": labels.minute_truth.request_parameters(day, code),
            "fields": list(labels.minute_truth.FIELDS)}
    require(api in FIELDS, "OFFICIAL_ENDPOINT_NOT_ALLOWED")
    return {"api_name": api, "params": {"ts_code": code, "trade_date": day}, "fields": list(FIELDS[api])}


def request_identity(contract):
    require(type(contract) is dict and set(contract) == {"api_name", "params", "fields"}
        and type(contract["params"]) is dict, "EXACT_REQUEST_REQUIRED")
    params = contract["params"]
    if contract["api_name"] == "stk_mins":
        start = params.get("start_date")
        require(type(start) is str and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2} 09:31:00", start), "EXACT_0931_REQUEST_REQUIRED")
        day = start[:10].replace("-", "")
    else:
        day = params.get("trade_date")
    code = params.get("ts_code")
    require(contract == request_contract(contract["api_name"], day, code), "EXACT_REQUEST_REQUIRED")
    return contract["api_name"], day, code


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def _worker(connection, contract):
    packet = b"EREJECTED"
    try:
        payload = {**contract, "fields": ",".join(contract["fields"]), "token": os.environ.get("TUSHARE_TOKEN", "")}
        req = request.Request("https://api.tushare.pro", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        # Do not inherit a proxy or redirect destination from the environment.
        with request.build_opener(request.ProxyHandler({}), NoRedirect()).open(req, timeout=TIMEOUT) as reply:
            body = reply.read(MAX_BYTES + 1)  # One overflow-detection byte; never accepted/persisted.
        if 0 < len(body) <= MAX_BYTES: packet = b"S" + body
    except Exception:
        pass  # No provider exception text, traceback, URL or body is logged.
    try:
        connection.sendall(packet)
    except Exception:
        pass  # A closed parent must not print a chained provider exception.
    finally:
        try: connection.close()
        except Exception: pass


def official_call(contract):
    """Fixed HTTPS only; no redirects/retries and a parent-process deadline."""
    request_identity(contract)
    require(bool(os.environ.get("TUSHARE_TOKEN", "").strip()), "CREDENTIAL_ABSENT")
    context = multiprocessing.get_context("fork")
    receive, send = socket.socketpair()
    process = context.Process(target=_worker, args=(send, contract), daemon=True)
    try:
        process.start(); send.close()
        deadline = time.monotonic() + TIMEOUT
        packet = bytearray()
        while True:
            remaining = deadline-time.monotonic()
            require(remaining > 0, "TRANSPORT_DEADLINE")
            receive.settimeout(remaining)
            chunk = receive.recv(min(65536,MAX_BYTES+2-len(packet)))
            if not chunk: break
            packet.extend(chunk)
            require(len(packet) <= MAX_BYTES+1, "TRANSPORT_REJECTED")
        require(packet[:1] == b"S" and 1 < len(packet) <= MAX_BYTES + 1, "TRANSPORT_REJECTED")
        return bytes(packet[1:])
    except TimeoutError:
        raise ValueError("TRANSPORT_DEADLINE") from None
    except (EOFError, OSError):
        raise ValueError("TRANSPORT_REJECTED") from None
    finally:
        send.close(); receive.close()
        if process.pid is not None:
            if process.is_alive(): process.terminate()
            process.join(timeout=.5)
            if process.is_alive(): process.kill(); process.join(timeout=.5)
            require(not process.is_alive(), "TRANSPORT_PROCESS_NOT_STOPPED")
            process.close()


def safe_response(raw, token):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "RESPONSE_BYTE_LIMIT")
    require(not token or token.encode() not in raw, "SENSITIVE_RESPONSE")
    value = natural._json(raw)
    todo = [value]
    while todo:
        item = todo.pop()
        if type(item) is dict:
            require(not any(re.search(r"token|secret|password|authorization|api[_-]?key", key, re.I) for key in item), "SENSITIVE_RESPONSE")
            todo.extend(item.keys()); todo.extend(item.values())
        elif type(item) is list: todo.extend(item)
        elif type(item) is str: require(not token or token not in item, "SENSITIVE_RESPONSE")
        elif type(item) is float: require(math.isfinite(item), "NONFINITE_RESPONSE")
    require(type(value) is dict and set(value) <= {"code", "data", "msg", "request_id", "detail"}
        and type(value.get("code")) is int, "UNKNOWN_RESPONSE_ENVELOPE")
    return value


def csv_projection(raw, contract):
    """Exact scalar-table serialization, not provider CSV or price eligibility."""
    # The already credential/duplicate/finite-checked original bytes are parsed
    # with Decimal here to avoid reducing numeric precision in the CSV projection.
    value = json.loads(raw, parse_float=Decimal)
    require(value["code"] == 0 and value.get("detail") in (None, "", "..."), "API_RESPONSE_NOT_QUALIFIED")
    data = value.get("data")
    require(type(data) is dict and set(data) == {"fields", "items", "count", "has_more"}, "EXPLICIT_COMPLETE_TABLE_REQUIRED")
    fields, rows = data["fields"], data["items"]
    require(type(fields) is list and len(fields) == len(contract["fields"])
        and all(type(f) is str for f in fields) and set(fields) == set(contract["fields"])
        and type(rows) is list and len(rows) <= 1 and type(data["count"]) is int
        and data["count"] in (0, len(rows)) and data["has_more"] is False, "EXACT_SINGLE_STOCK_COMPLETE_TABLE_REQUIRED")
    if not rows: return None
    require(type(rows[0]) is list and len(rows[0]) == len(fields), "EXACT_SOURCE_ROW_REQUIRED")
    row = dict(zip(fields, rows[0])); api, day, code = request_identity(contract)
    require(row["ts_code"] == code and row["trade_date"] == day, "SOURCE_IDENTITY_MISMATCH")
    for field, item in row.items():
        if field in ("ts_code", "trade_date"): continue
        if api == "stk_limit" and field == "pre_close" and item is None: continue
        require(type(item) in (int, Decimal, str), "FINITE_ORIGINAL_NUMERIC_SCALAR_REQUIRED")
        try: finite = Decimal(item).is_finite()
        except Exception: finite = False
        require(finite, "FINITE_ORIGINAL_NUMERIC_SCALAR_REQUIRED")
    stream = io.StringIO(newline=""); writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields); writer.writerow(rows[0])
    return stream.getvalue().encode()


def source_paths(api, day, code):
    if api in FIELDS: return [f"data/market/raw/{day[:4]}/{day}/{api}.csv"]
    pair = labels.auction_truth.source_paths(Path("/"), day) if api == "stk_auction" else labels.minute_truth.paths(Path("/"), day, code)
    return [p.relative_to("/").as_posix() for p in pair]


def read_bound(path, expected, states):
    path = natural._path(path); natural.scorer._sha(expected)
    body, identity = natural._read(path)
    require(sha(body) == expected, "EXTERNAL_SOURCE_SHA_MISMATCH")
    states.append((path, expected, identity)); return body


def write_new(root, relative, body, token):
    require(type(relative) is str and not Path(relative).is_absolute() and "\\" not in relative
        and all(p not in ("", ".", "..") for p in relative.split("/")), "EXACT_OUTPUT_PATH_REQUIRED")
    path = natural._path(root / relative)
    require(type(body) is bytes and os.environ.get("TUSHARE_TOKEN", "") == token
        and (not token or token.encode() not in body) and not path.exists(), "EXCLUSIVE_SAFE_OUTPUT_REQUIRED")
    if token and relative.endswith(".json"):
        todo = [natural._json(body)]
        while todo:
            value = todo.pop()
            if type(value) is dict: todo.extend(value.keys()); todo.extend(value.values())
            elif type(value) is list: todo.extend(value)
            elif type(value) is str: require(token not in value, "SENSITIVE_OUTPUT")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(body); handle.flush(); os.fsync(handle.fileno())
    return {"path": relative, "sha256": sha(body), "bytes": len(body)}


def previous_state(path, expected, outcome_path, outcome_expected, *, frozen, codes, dates, asof, states, injected):
    require((path is None) == (expected is None) and (outcome_path is None) == (outcome_expected is None), "PAIRED_PRIOR_PATH_AND_SHA_REQUIRED")
    require(outcome_path is None or path is not None, "PRIOR_OUTCOME_REQUIRES_BOUND_COLLECTION")
    if path is None: return {}, [], {}, None
    receipt = natural._json(read_bound(path, expected, states)); outcomes._seal(receipt, "receipt_sha256")
    require(receipt["schema_version"] == SCHEMA and receipt["writer_sha256"] == SELF_SHA
        and receipt["snapshot_file_sha256"] == frozen["file_sha256"] and receipt["signal_date"] == frozen["signal_date"]
        and receipt["exec_date"] == frozen["exec_date"] and receipt["exit_date"] == frozen["exit_date"]
        and receipt["slot_union_codes"] == codes
        and type(receipt["full_frozen_candidate_count"]) is int
        and receipt["full_frozen_candidate_count"] == len(frozen["prediction"]["rows"])
        and receipt["outcomes_wrapper_sha256"] == OUTCOMES_SHA
        and frozen["signal_date"] <= receipt["as_of_date"] <= asof, "PRIOR_COLLECTION_IDENTITY_CHANGED")
    require(type(receipt["callable_injected_for_test"]) is bool
        and receipt["callable_injected_for_test"] is injected
        and receipt["clock_mode"] == ("INJECTED_TEST_CLOCK_RESEARCH_ONLY" if receipt["callable_injected_for_test"] else "HOST_SYSTEM_UTC"),
        "PRIOR_COLLECTION_CLOCK_MODE_CHANGED")
    for key, wanted in FLAGS.items(): require(receipt.get(key) is wanted, "PRIOR_COLLECTION_QUALIFICATION_CHANGED")
    bundle = receipt["source_bundle"]
    planned, _, _ = outcomes._bundle(bundle, codes, t=frozen["exec_date"], t1=frozen["exit_date"],
        asof=receipt["as_of_date"], dates=dates)
    require(bundle["calendar"]["sha256"] == labels.settlement.CALENDAR_SHA256, "PRIOR_CALENDAR_CHANGED")
    history = receipt["request_history"]
    require(type(history) is list and len(history) <= 4096, "BOUNDED_REQUEST_HISTORY_REQUIRED")
    identities = set(); allowed = {"source_bundle.json"}
    for row in history:
        api, request_day, code = request_identity(row["request"])
        require(code in codes and frozen["exec_date"] <= request_day <= receipt["as_of_date"]
            and request_day in dates and (api != "stk_auction" or request_day == frozen["exec_date"])
            and (api != "stk_mins" or request_day >= frozen["exit_date"])
            and (api, request_day, code) not in identities and type(row["api_calls"]) is int and row["api_calls"] == 1
            and row["callable_injected_for_test"] is injected and row["network_request_performed"] is (not injected),
            "PRIOR_REQUEST_HISTORY_CHANGED")
        identities.add((api, request_day, code))
        allowed.update("sources/" + code.replace(".","_") + "/" + p for p in source_paths(api,request_day,code))
        stem = "http/" + code.replace(".","_") + "/" + request_day + "/" + api
        allowed.update((stem+".response.json",stem+".receipt.json"))
    blobs = {}; prior_root = natural._path(receipt["collection_root"])
    files = receipt["output_file_bindings"]
    require(type(files) is list and len(files) <= 20000, "BOUNDED_PRIOR_FILE_LIST_REQUIRED")
    seen = set(); total = 0
    for item in files:
        require(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "EXACT_PRIOR_FILE_BINDING_REQUIRED")
        relative = item["path"]
        require(type(relative) is str and relative not in seen and not Path(relative).is_absolute()
            and all(p not in ("", ".", "..") for p in relative.split("/")) and "\\" not in relative, "PRIOR_FILE_PATH_INVALID")
        seen.add(relative)
        require(relative in allowed, "PRIOR_FILE_SCOPE_INVALID")
        natural.scorer._sha(item["sha256"])
        require(type(item["bytes"]) is int and 0 <= item["bytes"] <= natural.MAX_BYTES, "PRIOR_FILE_LENGTH_INVALID")
    by_path = {item["path"]: item for item in files}
    require(receipt["source_bundle_file"] == by_path.get("source_bundle.json"), "PRIOR_BUNDLE_FILE_BINDING_CHANGED")
    referenced = {"source_bundle.json"}
    for row in history:
        api, request_day, code = request_identity(row["request"])
        prefix = "sources/" + code.replace(".", "_") + "/"
        source_names = [prefix + p for p in source_paths(api,request_day,code)]
        source_items = row["source_files"]
        require(type(source_items) is list and (not source_items or [item["path"] for item in source_items] == source_names),
            "PRIOR_NATIVE_SOURCE_PAIR_CHANGED")
        for item in source_items:
            require(item == by_path.get(item["path"]), "PRIOR_REQUEST_SOURCE_BINDING_CHANGED")
            referenced.add(item["path"])
        raw = row["raw_http_sidecar"]
        require(not source_items or raw is not None, "PRIOR_SOURCE_REQUIRES_ORIGINAL_HTTP")
        if raw is not None:
            stem = "http/" + code.replace(".","_") + "/" + request_day + "/" + api
            require(type(raw) is dict and raw == by_path.get(stem+".response.json")
                and raw["sha256"] == row["http_response_sha256"] and raw["bytes"] == row["http_response_bytes"]
                and stem+".receipt.json" in by_path, "PRIOR_HTTP_RESPONSE_BINDING_CHANGED")
            referenced.update((stem+".response.json",stem+".receipt.json"))
    require(referenced == seen, "PRIOR_ORPHAN_SOURCE_OR_HTTP_FILE")
    # All requested stock/date and file identities passed before any price/raw
    # body from the previous collection is opened.
    for item in files:
        relative = item["path"]
        body = read_bound(prior_root / relative, item["sha256"], states)
        require(len(body) == item["bytes"], "PRIOR_FILE_LENGTH_CHANGED")
        total += len(body); require(total <= MAX_TOTAL_BYTES, "PRIOR_SOURCE_BYTE_BUDGET_EXCEEDED")
        if relative != "source_bundle.json": blobs[relative] = body
        else: require(natural._json(body) == bundle, "PRIOR_BUNDLE_FILE_DOES_NOT_MATCH_RECEIPT")
    for code, relative, origin, expected_sha in planned:
        target = "sources/" + code.replace(".", "_") + "/" + relative
        require(origin == prior_root / target and target in blobs and sha(blobs[target]) == expected_sha,
            "PRIOR_SOURCE_BUNDLE_NOT_BOUND_TO_FILES")
    require({"sources/"+code.replace(".","_")+"/"+relative for code,relative,_,_ in planned}
        == {p for p in blobs if p.startswith("sources/")}, "PRIOR_BUNDLE_OMITS_BOUND_SOURCE")
    for row in history:
        if row["raw_http_sidecar"] is None: continue
        sidecar_path = row["raw_http_sidecar"]["path"].removesuffix(".response.json")+".receipt.json"
        meta = natural._json(blobs[sidecar_path])
        api = row["request"]["api_name"]
        expected_meta = {"schema_version": "dc20_natural_http_projection_receipt_v1", **row,
            "original_http_bytes_preserved": True,
            "csv_origin": "ORIGINAL_JSON_SCALAR_TABLE_PROJECTION_NOT_UPSTREAM_CSV" if api in FIELDS else None, **FLAGS}
        natural.scorer._exact(meta,expected_meta,"PRIOR_HTTP_RECEIPT_CHANGED")
    observed = None
    if outcome_path is not None:
        ledger = natural._json(read_bound(outcome_path, outcome_expected, states)); outcomes._seal(ledger, "ledger_sha256")
        require(ledger["schema_version"] == outcomes.SCHEMA and ledger["signal_date"] == frozen["signal_date"]
            and ledger["snapshot_file_sha256"] == frozen["file_sha256"] and ledger["versions"], "PRIOR_OUTCOME_IDENTITY_CHANGED")
        observed = ledger["versions"][-1]; outcomes._seal(observed, "report_sha256")
        require(observed["as_of_date"] <= asof and observed["snapshot_file_sha256"] == frozen["file_sha256"]
            and observed["writer_sha256"] == OUTCOMES_SHA and set(observed["native_singleton_reports"]) == set(codes)
            and observed["clock_mode"] == ("INJECTED_TEST_CLOCK_RESEARCH_ONLY" if injected else "HOST_SYSTEM_UTC")
            and observed["frozen_clock_mode"] == frozen["clock_mode"], "PRIOR_OUTCOME_SCOPE_CHANGED")
        for key, wanted in outcomes.FLAGS.items():
            natural.scorer._exact(observed.get(key), wanted, "PRIOR_OUTCOME_QUALIFICATION_CHANGED")
        for code, report in observed["native_singleton_reports"].items():
            require(report["historical_counterfactual"] is True and len(report["rows"]) == 1, "NATIVE_SINGLETON_REQUIRED")
            row = report["rows"][0]; labels.policy_v3.validate_label_contract(row)
            original = next(r for r in frozen["prediction"]["rows"] if r["ts_code"] == code)
            require(row["signal_date"] == frozen["signal_date"] and row["ts_code"] == code
                and row["promotion_rank"] == original["promotion_rank"] and row["exec_date"] == frozen["exec_date"]
                and row["scheduled_exit_date"] == frozen["exit_date"], "NATIVE_OUTCOME_IDENTITY_CHANGED")
            available = {b["path"]: b["sha256"] for b in bundle["by_code"][code]["bindings"]}
            available[str(labels.settlement.CALENDAR_PATH)] = labels.settlement.CALENDAR_SHA256
            available["research_inputs/candidate_natural_forward/day_" + frozen["signal_date"] + ".json"] = frozen["file_sha256"]
            require(all(available.get(b["path"]) == b["sha256"] for b in report["source_files"]), "PRIOR_OUTCOME_SOURCE_CHANGED")
    return blobs, history, bundle["by_code"], observed


def earliest_plan(frozen, codes, dates, asof, previous_sources, observed):
    """Scheduling only: no price, entry, return or exit rule is reimplemented."""
    t, t1 = frozen["exec_date"], frozen["exit_date"]
    requests, stocks = [], []
    for code in codes:
        target, reason = None, "NOT_DUE"
        if t <= asof:
            if observed is None:
                present = {b["path"] for b in previous_sources.get(code, {}).get("bindings", [])}
                entry = {p for api in ("daily", "stk_limit", "stk_auction") for p in source_paths(api, t, code)}
                target, reason = (None, "REQUIRES_OUTCOME_VALIDATION") if entry <= present else (t, "FIRST_T_ENTRY_EVIDENCE")
            else:
                row = observed["native_singleton_reports"][code]["rows"][0]
                status = row["label_status"]; missing = row.get("missing_evidence_date")
                if status in outcomes.TERMINAL: reason = "TERMINAL_ORIGINAL_EVIDENCE_REUSED"
                elif missing is not None:
                    natural.scorer._date(missing, "native_missing_date")
                    require(row.get("missing_evidence_code") == code and missing in dates and t <= missing <= asof,
                        "NATIVE_MISSING_EVIDENCE_SCOPE_INVALID")
                    require(row.get("missing_evidence_kind") in {"daily", "stk_limit", "canonical_auction_0925", "research_exit_1000_1m_0931"}, "NATIVE_MISSING_KIND_UNSUPPORTED")
                    target, reason = missing, "NATIVE_EARLIEST_MISSING_EVIDENCE"
                elif status in ("PENDING_T", "PENDING_T1"):
                    due = t if status == "PENDING_T" else t1
                    if due <= asof: target, reason = due, "NATIVE_NEXT_DUE_SESSION"
                elif status in ("PENDING_EXIT_LIMIT_UP_HELD", "PENDING_EXIT_UNSELLABLE", "PENDING_EXIT_SUSPENDED", "PENDING_EXIT_UNRESOLVED"):
                    later = [d for d in dates if observed["as_of_date"] < d <= asof]
                    if later: target, reason = later[0], "NATIVE_UNRESOLVED_NEXT_SESSION_ONLY"
                else: reason = "PENDING_INVALID_OR_UNSUPPORTED_EVIDENCE_NO_AUTOMATIC_REPLACEMENT"
        stocks.append({"ts_code": code, "target_trade_date": target, "reason": reason})
        if target is not None:
            require(target in dates and t <= target <= asof, "NEXT_EVIDENCE_DATE_OUTSIDE_SCOPE")
            for api in ("daily", "stk_limit", "stk_auction" if target == t else "stk_mins"):
                requests.append(request_contract(api, target, code))
    return requests, stocks


def collect_natural_outcome_sources(snapshot_path, output_root, *, expected_snapshot_sha256, calendar_path,
        expected_calendar_sha256, as_of_date, previous_collection_path=None, expected_previous_collection_sha256=None,
        previous_outcomes_path=None, expected_previous_outcomes_sha256=None, transport=None, clock=None,
        monotonic=None, sleep=None):
    injected = any(hook is not None for hook in (transport, clock, monotonic, sleep))
    require(not injected or callable(transport), "TEST_HOOKS_REQUIRE_TEST_TRANSPORT_NO_REAL_NETWORK")
    monotonic_fn, sleep_fn = monotonic or time.monotonic, sleep or time.sleep
    began = last_tick = monotonic_fn()
    require(type(began) in (int,float) and math.isfinite(began), "MONOTONIC_CLOCK_INVALID")
    def tick():
        nonlocal last_tick
        value = monotonic_fn()
        require(type(value) in (int, float) and math.isfinite(value) and value >= last_tick, "MONOTONIC_CLOCK_INVALID")
        last_tick = value; return value
    code_state = code_guard(); states = []
    registration = natural.registration()
    require(registration[1] == outcomes.REGISTRATION_SHA, "FIXED_REGISTRATION_REQUIRED")
    token = os.environ.get("TUSHARE_TOKEN", "")
    now = natural._now(clock); asof = natural.scorer._date(as_of_date, "as_of_date")
    require(now >= labels._timestamp(labels._at(asof, "15:00:00")), "ASOF_MUST_BE_COMPLETED_SESSION")
    raw_snapshot = read_bound(snapshot_path, expected_snapshot_sha256, states)
    frozen = outcomes._snapshot(raw_snapshot, expected_snapshot_sha256)
    require(injected or frozen["clock_mode"] == "HOST_SYSTEM_UTC", "TEST_FROZEN_SELECTION_CANNOT_TRIGGER_REAL_NETWORK")
    require(frozen["signal_date"] <= asof and labels._timestamp(frozen["pre_cas_freeze_at_utc"]) <= now, "ASOF_OR_CLOCK_BEFORE_FROZEN_SELECTION")
    require(expected_calendar_sha256 == labels.settlement.CALENDAR_SHA256, "PINNED_CALENDAR_REQUIRED")
    raw_calendar = read_bound(calendar_path, expected_calendar_sha256, states)
    cal_binding = [b for b in frozen["D_source_evidence"]["source_file_bindings"] if b["receipt_path"] == str(labels.settlement.CALENDAR_PATH)]
    require(len(cal_binding) == 1 and cal_binding[0]["sha256"] == expected_calendar_sha256, "CALENDAR_MUST_MATCH_FROZEN_D")
    with tempfile.TemporaryDirectory(prefix="dc20-natural-calendar-") as temporary:
        cal = Path(temporary) / labels.settlement.CALENDAR_PATH
        cal.parent.mkdir(parents=True); cal.write_bytes(raw_calendar)
        dates = labels.settlement._strict_open_dates(Path(temporary))
    day, t, t1 = (frozen[k] for k in ("signal_date", "exec_date", "exit_date"))
    require(day in dates and asof in dates and dates[dates.index(day)+1:dates.index(day)+3] == [t,t1], "D_T_T1_ASOF_CALENDAR_INVALID")
    codes = sorted({s["ts_code"] for key in ("candidate_slots", "promotion_slots") for s in frozen[key] if s["ts_code"]})
    frozen = {**frozen, "file_sha256": expected_snapshot_sha256}
    blobs, old_history, previous_sources, observed = previous_state(previous_collection_path, expected_previous_collection_sha256,
        previous_outcomes_path, expected_previous_outcomes_sha256, frozen=frozen, codes=codes, dates=dates, asof=asof,
        states=states, injected=injected)
    planned, stock_plan = earliest_plan(frozen, codes, dates, asof, previous_sources, observed)
    require(len(planned) <= 3 * len(codes) <= 12, "ONLY_ONE_EARLIEST_SESSION_PER_FROZEN_SLOT_STOCK")
    root = natural._path(output_root)
    require(root.name == "candidate_natural_outcome_sources" and not root.exists(), "FRESH_ISOLATED_COLLECTION_ROOT_REQUIRED")
    if ROOT in root.parents:
        require(root.relative_to(ROOT).as_posix() == "work/profit_1000_upgrade/candidate_natural_outcome_sources", "OUTPUT_CANNOT_ENTER_PRODUCTION")
    require(all(root != p and root not in p.parents and p not in root.parents for p, _, _ in states), "OUTPUT_OVERLAPS_INPUT")
    root.mkdir(parents=True, exist_ok=False)
    written = [write_new(root, relative, body, token) for relative, body in blobs.items()]
    bundle = {"calendar": {"origin_path": str(natural._path(calendar_path)), "sha256": expected_calendar_sha256}, "by_code": {}}
    for code in codes:
        source_root = root / "sources" / code.replace(".", "_"); source_root.mkdir(parents=True, exist_ok=True)
        bundle["by_code"][code] = {"source_root": str(source_root),
            "bindings": [dict(b) for b in previous_sources.get(code, {}).get("bindings", [])]}
    history = list(old_history); attempted = {request_identity(r["request"]) for r in history}
    calls = response_bytes = 0; next_start = began; rows = []; last_fetched = now
    for ordinal, contract in enumerate(planned, 1):
        api, trade_date, code = request_identity(contract); wanted = source_paths(api, trade_date, code)
        existing = {b["path"] for b in bundle["by_code"][code]["bindings"]}
        row = {"ordinal": ordinal, "request": contract, "api_calls": 0, "network_request_performed": False,
            "status": "NOT_REQUESTED_BUDGET", "http_response_sha256": None, "http_response_bytes": None,
            "fetched_at_utc": None, "raw_http_sidecar": None, "source_files": [], "callable_injected_for_test": injected}
        rows.append(row)
        if (api, trade_date, code) in attempted:
            row["status"] = "EXISTING_BOUND_SOURCE_REUSED" if set(wanted) <= existing else "PRIOR_INVALID_OR_EMPTY_ATTEMPT_PRESERVED_PENDING"
            continue
        require(not set(wanted) & existing, "EXISTING_SOURCE_WITHOUT_BOUND_REQUEST_HISTORY")
        if not token.strip(): row["status"] = "PENDING_CREDENTIAL_ABSENT"; continue
        current = tick(); ready = max(current, next_start)
        if calls >= MAX_CALLS or response_bytes + MAX_BYTES + 1 > MAX_TOTAL_BYTES or ready - began + HEADROOM >= MAX_SECONDS: continue
        if ready > current: sleep_fn(ready-current)
        current = tick()
        if current - began + HEADROOM >= MAX_SECONDS: continue
        require(os.environ.get("TUSHARE_TOKEN", "") == token, "CREDENTIAL_ENVIRONMENT_CHANGED")
        calls += 1; next_start = current + INTERVAL
        row.update(api_calls=1, request_sequence=calls, request_start_elapsed_seconds=current-began,
            network_request_performed=not injected, status="PENDING_TRANSPORT_OR_UNQUALIFIED_RESPONSE")
        original_request = natural.storage.encoded(contract)
        safe = False; bodies = []
        try:
            body = (transport or official_call)(contract)
            require(natural.storage.encoded(contract) == original_request, "REQUEST_CHANGED_DURING_TRANSPORT")
            require(os.environ.get("TUSHARE_TOKEN", "") == token, "CREDENTIAL_ENVIRONMENT_CHANGED")
            require(type(body) is bytes and 0 < len(body) <= MAX_BYTES, "RESPONSE_BYTE_LIMIT")
            response_bytes += len(body)
            row.update(http_response_sha256=sha(body), http_response_bytes=len(body))
            safe_response(body, token); safe = True
            fetched = natural._now(clock)
            require(fetched >= last_fetched and fetched >= labels._timestamp(labels._at(trade_date,"15:00:00")), "FETCH_CLOCK_INVALID")
            last_fetched = fetched
            row["fetched_at_utc"] = natural._stamp(fetched)
            if api in FIELDS:
                projected = csv_projection(body, contract); bodies = [] if projected is None else [projected]
                row["status"] = "COMPLETE_EMPTY_RESPONSE_NO_PRICE_CSV" if projected is None else "CSV_PROJECTED_NOT_ECONOMICALLY_QUALIFIED"
            elif api == "stk_auction":
                bodies = outcomes.auction_http_v3.source_bytes(body, trade_date, request=contract,
                    fetched_at_utc=row["fetched_at_utc"], network_request_performed=True, token=token)
                row["status"] = "NATIVE_AUCTION_PAIR_WRITTEN"
            else:
                bodies = labels.minute_truth.source_bytes(body, trade_date, code, request_params=contract["params"],
                    fetched_at_utc=row["fetched_at_utc"], token=token)
                row["status"] = "NATIVE_MINUTE_PAIR_WRITTEN"
        except Exception:
            bodies = []; row["status"] = "PENDING_TRANSPORT_OR_UNQUALIFIED_RESPONSE"
        require(natural.storage.encoded(contract) == original_request, "REQUEST_CHANGED_DURING_TRANSPORT")
        if safe:
            stem = "http/" + code.replace(".","_") + "/" + trade_date + "/" + api
            row["raw_http_sidecar"] = write_new(root, stem + ".response.json", body, token)
            written.append(row["raw_http_sidecar"])
        for relative, data in zip(wanted, bodies):
            item = write_new(root, "sources/" + code.replace(".","_") + "/" + relative, data, token)
            written.append(item); row["source_files"].append(item)
            bundle["by_code"][code]["bindings"].append({"path": relative, "sha256": item["sha256"]})
        row["request_finished_elapsed_seconds"] = tick()-began
        history.append(row); attempted.add((api,trade_date,code))
        if safe:
            meta = {"schema_version": "dc20_natural_http_projection_receipt_v1", **row,
                "original_http_bytes_preserved": True,
                "csv_origin": "ORIGINAL_JSON_SCALAR_TABLE_PROJECTION_NOT_UPSTREAM_CSV" if api in FIELDS else None, **FLAGS}
            written.append(write_new(root, stem + ".receipt.json", natural.storage.encoded(meta), token))
    for entry in bundle["by_code"].values(): entry["bindings"].sort(key=lambda b:b["path"])
    outcomes._bundle(bundle,codes,t=t,t1=t1,asof=asof,dates=dates)
    def end_guard():
        for path, expected, identity in states:
            require(sha(natural._read(path,identity)[0]) == expected, "BOUND_INPUT_CHANGED")
        natural._read(natural.REGISTRATION_PATH, registration[2])
        require(natural.registration() == registration and code_guard() == code_state
            and os.environ.get("TUSHARE_TOKEN", "") == token, "CODE_REGISTRATION_OR_CREDENTIAL_CHANGED")
        for item in written:
            body, _ = natural._read(root/item["path"])
            require(sha(body) == item["sha256"] and len(body) == item["bytes"], "COLLECTED_FILE_CHANGED")
    end_guard()
    bundle_file = write_new(root,"source_bundle.json",natural.storage.encoded(bundle),token); written.append(bundle_file)
    completed = natural._now(clock); elapsed = tick()-began
    require(completed >= last_fetched and elapsed < MAX_SECONDS, "COLLECTION_CLOCK_OR_TOTAL_BUDGET_EXCEEDED")
    result = {"schema_version":SCHEMA,"status":"SOURCE_COLLECTION_RESEARCH_ONLY", "signal_date":day,
        "exec_date":t,"exit_date":t1,"as_of_date":asof,"snapshot_file_sha256":expected_snapshot_sha256,
        "full_frozen_candidate_count":len(frozen["prediction"]["rows"]),"slot_union_codes":codes,
        "stock_plan":stock_plan,"requests":rows,"request_history":history,"api_calls":calls,
        "response_bytes":response_bytes,"source_bundle":bundle,"source_bundle_file":bundle_file,
        "output_file_bindings":written,"collection_root":str(root),"writer_sha256":SELF_SHA,
        "outcomes_wrapper_sha256":OUTCOMES_SHA,"previous_collection_sha256":expected_previous_collection_sha256,
        "previous_outcomes_sha256":expected_previous_outcomes_sha256,"callable_injected_for_test":injected,
        "clock_mode":"INJECTED_TEST_CLOCK_RESEARCH_ONLY" if injected else "HOST_SYSTEM_UTC",
        "source_processing_completed_at_utc":natural._stamp(completed),"elapsed_source_processing_seconds":elapsed,
        "budget":{"max_calls":MAX_CALLS,"max_seconds":MAX_SECONDS,"max_response_bytes":MAX_BYTES,
            "max_total_response_bytes":MAX_TOTAL_BYTES,"transport_seconds":TIMEOUT,"workers":1,"retries":0,
            "minimum_start_interval_seconds":INTERVAL,"redirects_allowed":False},**FLAGS}
    result["receipt_sha256"] = natural.scorer.canonical_sha(result)
    receipt_file = write_new(root,"receipt.json",natural.storage.encoded(result),token)
    end_guard()
    require(sha(natural._read(root/"receipt.json")[0]) == receipt_file["sha256"], "FINAL_RECEIPT_CHANGED")
    # The persisted timestamp covers source processing, not its own later write.
    # Success additionally requires final byte/identity guards within the budget.
    require(natural._now(clock) >= completed and tick()-began < MAX_SECONDS,
        "FINAL_COLLECTION_CLOCK_OR_TOTAL_BUDGET_EXCEEDED")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ("snapshot","calendar","output-root"):
        parser.add_argument("--"+flag,type=Path,required=True)
    for flag in ("snapshot-sha256","calendar-sha256","as-of-date"):
        parser.add_argument("--"+flag,required=True)
    for flag in ("previous-collection","previous-outcomes"):
        parser.add_argument("--"+flag,type=Path);parser.add_argument("--"+flag+"-sha256")
    args=parser.parse_args(argv)
    try:
        result=collect_natural_outcome_sources(args.snapshot,args.output_root,
            expected_snapshot_sha256=args.snapshot_sha256,calendar_path=args.calendar,
            expected_calendar_sha256=args.calendar_sha256,as_of_date=args.as_of_date,
            previous_collection_path=args.previous_collection,expected_previous_collection_sha256=args.previous_collection_sha256,
            previous_outcomes_path=args.previous_outcomes,expected_previous_outcomes_sha256=args.previous_outcomes_sha256)
        print(json.dumps({k:result[k] for k in ("status","api_calls","receipt_sha256")},sort_keys=True));return 0
    except Exception:
        print("NATURAL_OUTCOME_COLLECTION_FAILED_CLOSED");return 1


if __name__=="__main__":
    raise SystemExit(main())
