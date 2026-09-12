#!/usr/bin/env python3
"""Bounded research-mirror truth collection, never a production writer.

Official auction truth is attempted before replay. Missing minute evidence is
requested by the exit engine, one exact stock/session at a time; held positions
can expose a later required session on the next replay. No old price is replaced.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib import request, error

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]

from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision import shadow_exit_minute_truth as minute
from work.profit_1000_upgrade.labels import _load_candidates, build_labels
from work.profit_1000_upgrade.probe import classify

SCHEMA = "dc20_profit_1000_collection_receipt_v1"
MARKER = ".dc20-profit-1000-research-root.json"
DEFAULT_BUDGET = {"max_api_calls": 9000, "max_seconds": 5400,
                  "requests_per_second": 2.0, "workers": 4, "timeout_seconds": 20}
MARKET_FIELDS = {
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg"),
    # Optional pre_close is requested explicitly for corporate-action validation.
    "stk_limit": ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
    "stk_auction_o": ("ts_code", "trade_date", "close", "open", "high", "low", "vol", "amount", "vwap"),
}


def _expect(condition, reason):
    if not condition:
        raise ValueError(reason)


def _research_root(value, plan_version="v1"):
    if plan_version == "v2":
        from work.profit_1000_upgrade.run import require_research_mirror
        return require_research_mirror(value, plan_version="v2")
    path = Path(value)
    _expect(not any(component.is_symlink() for component in (path, *path.parents)), "RESEARCH_ROOT_SYMLINK_FORBIDDEN")
    root = path.resolve(strict=True)
    _expect(root != CHECKOUT and root not in CHECKOUT.parents and CHECKOUT not in root.parents,
            "RESEARCH_MIRROR_MUST_BE_OUTSIDE_CODE_CHECKOUT")
    marker = root / MARKER
    _expect(marker.is_file() and not marker.is_symlink(), "RESEARCH_MIRROR_MARKER_REQUIRED")
    payload = json.loads(marker.read_text())
    _expect(isinstance(payload, dict) and payload.get("schema_version") == "dc20_profit_1000_research_mirror_v1"
            and payload.get("production_writes") is False
            and payload.get("plan_sha256") == hashlib.sha256((HERE / "PLAN.json").read_bytes()).hexdigest(),
            "RESEARCH_MIRROR_MARKER_INVALID")
    return root


def _safe_target(root, path):
    _expect(root in path.resolve().parents, "COLLECTION_PATH_ESCAPED_MIRROR")
    for component in (path, *path.parents):
        if component == root:
            break
        _expect(not component.is_symlink(), "COLLECTION_SYMLINK_FORBIDDEN")
    _expect(not path.exists(), "EXISTING_TRUTH_CANNOT_BE_OVERWRITTEN")


def _write_pair(root, paths, bodies):
    for path in paths:
        _safe_target(root, path)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, bodies, strict=True):
        with path.open("xb") as handle:
            handle.write(body)
    return [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(body).hexdigest()}
            for path, body in zip(paths, bodies, strict=True)]


def official_call(endpoint, params, fields, token, timeout):
    body = json.dumps({"api_name": endpoint, "token": token,
                       "params": params, "fields": ",".join(fields)}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read(8_000_001)
        _expect(len(raw) <= 8_000_000, "API_RESPONSE_TOO_LARGE")
        return json.loads(raw)


def official_call_v2(endpoint, params, fields, token, timeout):
    """Exact bounded HTTP bytes; never reconstruct a provider envelope."""
    _expect(endpoint in {"stk_auction", "stk_mins", "daily", "stk_limit"}, "V2_ENDPOINT_FORBIDDEN")
    body = json.dumps({"api_name": endpoint, "token": token,
                       "params": params, "fields": ",".join(fields)}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read(8_000_001)
        _expect(len(raw) <= 8_000_000, "API_RESPONSE_TOO_LARGE")
        return raw


class RequestBudget:
    """A single clock/lock limits aggregate requests across all worker threads."""
    def __init__(self, budget, *, clock=time.monotonic, sleep=time.sleep):
        _expect(isinstance(budget, dict) and set(budget) == set(DEFAULT_BUDGET), "COLLECTION_BUDGET_FIELDS_INVALID")
        for name, maximum in DEFAULT_BUDGET.items():
            value = budget[name]
            _expect(type(value) in (int, float) and 0 < value <= maximum, "COLLECTION_BUDGET_EXCEEDED:" + name)
            if name != "requests_per_second":
                _expect(type(value) is int, "COLLECTION_BUDGET_NOT_INTEGER:" + name)
        self.budget = dict(budget)
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.next_request = self.started
        self.calls = 0
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            now = self.clock()
            ready = max(now, self.next_request)
            if self.calls >= self.budget["max_api_calls"] or ready - self.started >= self.budget["max_seconds"]:
                return False
            if ready > now:
                self.sleep(ready - now)
            if self.clock() - self.started >= self.budget["max_seconds"]:
                return False
            self.calls += 1
            self.next_request = self.clock() + 1.0 / self.budget["requests_per_second"]
            return True

    def expired(self):
        with self.lock:
            return self.calls >= self.budget["max_api_calls"] or self.clock() - self.started >= self.budget["max_seconds"]


def _table(payload, fields):
    data = payload.get("data")
    _expect(isinstance(data, dict), "API_TABLE_MISSING")
    columns, items = data.get("fields"), data.get("items")
    _expect(isinstance(columns, list) and len(columns) == len(fields) and set(columns) == set(fields)
            and isinstance(items, list), "API_TABLE_COLUMNS_INVALID")
    _expect(all(isinstance(row, list) and len(row) == len(columns) for row in items), "API_TABLE_ROW_INVALID")
    return [dict(zip(columns, row)) for row in items]


def _market_bytes(rows, day, endpoint, required_codes, stamp):
    fields = MARKET_FIELDS[endpoint]
    _expect(rows, "EMPTY_MARKET_RESPONSE")
    seen, normalized = set(), []
    for row in rows:
        code = row.get("ts_code")
        _expect(isinstance(code, str) and re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code)
                and code not in seen and row.get("trade_date") == day, "MARKET_IDENTITY_OR_DATE_INVALID")
        seen.add(code)
        clean = {"ts_code": code, "trade_date": day}
        for key in fields:
            if key in clean:
                continue
            value = row[key]
            if value is None or value == "":
                clean[key] = None
                continue
            _expect(not isinstance(value, bool), "MARKET_BOOLEAN_PRICE_INVALID")
            number = float(value)
            _expect(math.isfinite(number) and (key == "pct_chg" or number >= 0), "MARKET_NONFINITE_OR_NEGATIVE_VALUE")
            clean[key] = number
        normalized.append(clean)
    if endpoint != "stk_auction_o":
        _expect(set(required_codes) <= seen, "REQUIRED_STOCK_MISSING_IN_DAILY_PARTITION")
    # Missing auction rows are allowed and explicitly fall back only per stock.
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(normalized, key=lambda row: row["ts_code"]))
    raw = out.getvalue().encode()
    if endpoint == "stk_auction_o":
        meta = {"schema_version": "decision_auction_truth_v1", "source": "tushare:stk_auction_o",
                "trade_date": day, "rows": len(normalized), "requested_code_count": 0,
                "fields": list(fields), "sha256": hashlib.sha256(raw).hexdigest(),
                "immutable": True, "credential_persisted": False}
    else:
        meta = {"schema_version": "dc20_frozen_shadow_truth_source_v1", "source": "tushare:" + endpoint,
                "trade_date": day, "requested_trade_date": day, "rows": len(normalized),
                "sha256": hashlib.sha256(raw).hexdigest(), "fetched_at_utc": stamp,
                "immutable": True, "credential_persisted": False}
    return raw, (json.dumps(meta, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _fetch_one(root, item, *, token, limiter, call):
    day, code, endpoint = item["trade_date"], item.get("ts_code"), item["endpoint"]
    receipt = {"trade_date": day, "endpoint": endpoint, "ts_code": code,
               "status": "PENDING_UNKNOWN", "new_source_files": []}
    try:
        if endpoint == "stk_mins":
            existing = minute.load_exit_minutes(root, day, code)
            paths = minute.minute_paths(root, day, code)
            params, fields = minute.request_parameters(day, code), minute.FIELDS
        else:
            existing_path = settlement._find_market_file(root, day, endpoint)
            if endpoint == "stk_auction_o":
                existing = settlement._verified_auction_sources_v2(root, day)
            else:
                existing = settlement._market_rows(existing_path, day) if existing_path is not None else None
            path = root / f"data/market/raw/{day[:4]}/{day}/{endpoint}.csv"
            paths = (path, path.with_suffix(".meta.json"))
            params, fields = {"trade_date": day}, MARKET_FIELDS[endpoint]
        if existing:
            receipt["status"] = "EXISTING_TRUTH_NOT_OVERWRITTEN"
            return receipt
        for path in paths:
            _safe_target(root, path)
    except (ValueError, OSError, settlement.ExecutableProfitSettlementError):
        receipt["status"] = "PENDING_EXISTING_INVALID_NOT_OVERWRITTEN"
        return receipt
    if not token.strip():
        receipt["status"] = "PENDING_CREDENTIAL_ABSENT"
        return receipt
    if not limiter.take():
        receipt["status"] = "PENDING_BUDGET_EXHAUSTED"
        return receipt
    receipt["network_request_performed"] = True
    try:
        payload = call(endpoint, params, fields, token, limiter.budget["timeout_seconds"])
    except error.HTTPError as exc:
        receipt.update(status="PENDING_HTTP_ERROR", http_status=exc.code)
        return receipt
    except Exception:
        receipt["status"] = "PENDING_NETWORK_OR_RESPONSE_ERROR"
        return receipt
    status = classify(payload)
    if status != "SUCCESS":
        receipt["status"] = "PENDING_" + status
        return receipt
    try:
        rows = _table(payload, fields)
        if not rows:
            receipt["status"] = "AUCTION_ABSENT_FALLBACK_DECLARED" if endpoint == "stk_auction_o" else "PENDING_EMPTY_RESPONSE"
            return receipt
        stamp = datetime.now(timezone.utc).isoformat()
        if endpoint == "stk_mins":
            bodies = minute.source_bytes(rows, day, code, fetched_at_utc=stamp)
        else:
            bodies = _market_bytes(rows, day, endpoint, item.get("required_codes", []), stamp)
        _expect(all(token.encode() not in body for body in bodies), "CREDENTIAL_LIKE_RESPONSE_FORBIDDEN")
        receipt["new_source_files"] = _write_pair(root, paths, bodies)
        if endpoint == "stk_mins":
            _expect(minute.load_exit_minutes(root, day, code) is not None, "WRITTEN_MINUTES_INVALID")
        elif endpoint == "stk_auction_o":
            _expect(settlement._verified_auction_sources_v2(root, day), "WRITTEN_AUCTION_INVALID")
        else:
            settlement._market_rows(paths[0], day)
        receipt.update(status="EXACT_TRUTH_WRITTEN", source_rows=len(rows))
    except (ValueError, TypeError, KeyError, OSError, settlement.ExecutableProfitSettlementError):
        receipt["status"] = "PENDING_INVALID_RESPONSE_NOT_IMPUTED"
    return receipt


def _fetch_one_v2(root, item, *, token, limiter, call):
    from work.profit_1000_upgrade import auction_truth as auction, minute_truth as minutes, run
    day, code, endpoint = item["trade_date"], item.get("ts_code"), item["endpoint"]
    receipt = {"trade_date": day, "endpoint": endpoint, "ts_code": code,
               "status": "PENDING_UNKNOWN", "network_request_performed": False,
               "new_source_files": [], "existing_source_files": []}
    if endpoint == "stk_auction" and day < auction.COVERAGE_START:
        # An actual file, even corrupted, cannot be hidden by a declaration.
        if any(p.exists() or p.is_symlink() for p in auction.source_paths(root, day)):
            receipt["status"] = "PENDING_PRE_COVERAGE_SOURCE_CONFLICT"
        else:
            receipt.update(status="HISTORY_BEFORE_CANONICAL_COVERAGE",
                           source_policy_id=auction.SOURCE_POLICY_ID, plan_sha256=run.sha(run.plan_path("v2")))
        return receipt
    try:
        if endpoint == "stk_auction":
            paths = auction.source_paths(root, day)
            params, fields = auction.request_parameters(day), auction.FIELDS
            try:
                existing = auction.load(root, day)
            except auction.AuctionSourceMissing:
                existing = None
            if existing is not None:
                meta = json.loads(paths[1].read_text())
                receipt.update(status="EXISTING_TRUTH_NOT_OVERWRITTEN", network_request_performed=False,
                               source_network_request_performed=True,
                               request=meta["request"], http_response_sha256=meta["http_response_sha256"],
                               source_status=meta["status"], existing_source_files=[dict(b) for b in existing.source_files])
                return receipt
        elif endpoint == "stk_mins":
            paths = minutes.paths(root, day, code)
            params, fields = minutes.request_parameters(day, code), minutes.FIELDS
            existing = minutes.load(root, day, code)
            if existing is not None:
                meta = json.loads(paths[1].read_text())
                receipt.update(status="EXISTING_TRUTH_NOT_OVERWRITTEN", request={"api_name": endpoint, "params": params, "fields": list(fields)},
                               http_response_sha256=meta["response_body_sha256"], existing_source_files=existing["source_files"])
                return receipt
        elif endpoint in {"daily", "stk_limit"}:
            existing_path = settlement._find_market_file(root, day, endpoint)
            if existing_path is not None:
                settlement._market_rows(existing_path, day)
                receipt.update(status="EXISTING_TRUTH_NOT_OVERWRITTEN",
                               existing_source_files=[settlement._source_binding(root, existing_path)])
                return receipt
            path = root / f"data/market/raw/{day[:4]}/{day}/{endpoint}.csv"
            paths = path, path.with_suffix(".meta.json")
            params, fields = {"trade_date": day}, MARKET_FIELDS[endpoint]
        else:
            raise ValueError("V2_ENDPOINT_FORBIDDEN")
        for path in paths:
            _safe_target(root, path)
    except (ValueError, OSError, settlement.ExecutableProfitSettlementError):
        receipt["status"] = "PENDING_EXISTING_INVALID_NOT_OVERWRITTEN"
        return receipt
    if not token.strip():
        receipt["status"] = "PENDING_CREDENTIAL_ABSENT"
        return receipt
    if not limiter.take():
        receipt["status"] = "PENDING_BUDGET_EXHAUSTED"
        return receipt
    receipt.update(network_request_performed=True,
                   request={"api_name": endpoint, "params": params, "fields": list(fields)})
    try:
        raw = call(endpoint, params, fields, token, limiter.budget["timeout_seconds"])
        _expect(type(raw) is bytes and 0 < len(raw) <= 8_000_000, "ORIGINAL_BOUNDED_HTTP_BYTES_REQUIRED")
    except error.HTTPError as exc:
        receipt.update(status="PENDING_HTTP_ERROR", http_status=exc.code)
        return receipt
    except Exception:
        receipt["status"] = "PENDING_NETWORK_OR_RESPONSE_ERROR"
        return receipt
    receipt.update(http_response_sha256=hashlib.sha256(raw).hexdigest(), http_response_bytes=len(raw))
    try:
        payload = minutes._parse(raw)
        _expect(isinstance(payload, dict), "API_ENVELOPE_INVALID")
        status = classify(payload)
        if status != "SUCCESS" and not (endpoint == "stk_auction" and status == "ENTITLEMENT_DENIED"):
            receipt["status"] = "PENDING_" + status
            return receipt
        stamp = datetime.now(timezone.utc).isoformat()
        receipt["fetched_at_utc"] = stamp
        if endpoint == "stk_auction":
            bodies = auction.source_bytes(raw, day, request=receipt["request"], fetched_at_utc=stamp,
                                          network_request_performed=True, token=token)
            receipt["source_status"] = json.loads(bodies[1])["status"]
        elif endpoint == "stk_mins":
            bodies = minutes.source_bytes(raw, day, code, request_params=params, fetched_at_utc=stamp, token=token)
        else:
            data = payload.get("data", {})
            count, more = data.get("count", 0), data.get("has_more", False)
            _expect(type(count) is int and count in (0, len(data.get("items", [])))
                    and more is False, "DAILY_TABLE_TRUNCATED_OR_PAGINATED")
            rows = _table(payload, fields)
            if not rows:
                receipt["status"] = "PENDING_EMPTY_RESPONSE"
                return receipt
            bodies = _market_bytes(rows, day, endpoint, item.get("required_codes", []), stamp)
        _expect(not token or all(token.encode() not in body for body in bodies), "CREDENTIAL_LIKE_SOURCE_FORBIDDEN")
        receipt["new_source_files"] = _write_pair(root, paths, bodies)
        if endpoint == "stk_auction":
            auction.load(root, day)
        elif endpoint == "stk_mins":
            _expect(minutes.load(root, day, code) is not None, "WRITTEN_MINUTES_INVALID")
        else:
            settlement._market_rows(paths[0], day)
        receipt["status"] = "EXACT_TRUTH_WRITTEN"
    except (ValueError, TypeError, KeyError, OSError, settlement.ExecutableProfitSettlementError):
        receipt["status"] = "PENDING_INVALID_RESPONSE_NOT_IMPUTED"
    return receipt


def collect_history(root, manifest, *, as_of_date, token, budget=None,
                    progress=None, call=official_call, build_labels_fn=build_labels, plan_version="v1"):
    _expect(plan_version in {"v1", "v2"}, "UNKNOWN_COLLECTION_PLAN_VERSION")
    if plan_version == "v1":
        v2_fields = {"entry_policy_id", "auction_source_policy_id", "minute_source_policy_id",
                     "minute_time_semantics", "source_policy_contract"}
        _expect(not v2_fields.intersection(manifest) and manifest.get("plan_version") in (None, "v1"),
                "V2_MANIFEST_REQUIRES_EXPLICIT_V2_COLLECTOR")
    root = _research_root(root, plan_version=plan_version)
    v2 = plan_version == "v2"
    if v2:
        from work.profit_1000_upgrade import run as runner
        from work.profit_1000_upgrade.policy_v2 import CONTRACT, validate_contract
        validate_contract(manifest)
        if call is official_call:
            call = official_call_v2
        base_path = root / "research_inputs/base_archive_import.json"
        _expect(base_path.is_file() and not any(p.is_symlink() for p in (base_path, *base_path.parents)), "V2_BASE_IMPORT_RECEIPT_REQUIRED")
        base_binding = {"path": "research_inputs/base_archive_import.json", "sha256": runner.sha(base_path)}
    fetch = _fetch_one_v2 if v2 else _fetch_one
    limiter = RequestBudget(DEFAULT_BUDGET if budget is None else budget)
    dates = settlement._strict_open_dates(root)
    _expect(as_of_date in dates, "AS_OF_NOT_EXCHANGE_SESSION")
    candidates, bindings = _load_candidates(root, manifest, dates)
    _expect(all(row["signal_date"] <= as_of_date for row in candidates), "FUTURE_CANDIDATE_FORBIDDEN")
    allowed_codes = {row["ts_code"] for row in candidates}
    attempted, receipts, written, existing_files = set(), [], {}, {}
    # Incremental sanitized receipts survive a timeout or later replay failure.
    journal = root / "collection_requests.jsonl"
    _safe_target(root, journal)
    with journal.open("x"):
        pass
    auction_by_day = defaultdict(set)
    for row in candidates:
        if row["exec_date"] <= as_of_date:
            auction_by_day[row["exec_date"]].add(row["ts_code"])

    def key(item):
        return item["endpoint"], item["trade_date"], item.get("ts_code")

    def batch(items):
        items = [item for item in items if key(item) not in attempted]
        attempted.update(key(item) for item in items)
        with ThreadPoolExecutor(max_workers=limiter.budget["workers"]) as executor:
            futures = [executor.submit(fetch, root, item, token=token, limiter=limiter, call=call) for item in items]
            for future in as_completed(futures):
                receipt = future.result()
                receipts.append(receipt)
                with journal.open("a") as handle:
                    handle.write(json.dumps(receipt, sort_keys=True, allow_nan=False) + "\n")
                for binding in receipt["new_source_files"]:
                    written[binding["path"]] = binding
                for binding in receipt.get("existing_source_files", []):
                    existing_files[binding["path"]] = binding
                if progress and len(receipts) % 100 == 0:
                    progress({"event": "COLLECTION_PROGRESS", "receipts": len(receipts),
                              "api_calls": limiter.calls, "new_source_files": len(written),
                              "elapsed_seconds": round(limiter.clock() - limiter.started, 2)})

    # Attempt each distinct T once before any entry labels can become final.
    auction_endpoint = "stk_auction" if v2 else "stk_auction_o"
    batch([{"endpoint": auction_endpoint, "trade_date": date, "required_codes": sorted(codes)}
           for date, codes in sorted(auction_by_day.items())])
    rounds, labels = 0, None
    while True:
        labels = build_labels_fn(root, manifest, as_of_date=as_of_date)
        rounds += 1
        pending = {}
        for row in labels["rows"]:
            day, code, kind = (row.get("missing_evidence_date"), row.get("missing_evidence_code"), row.get("missing_evidence_kind"))
            minute_kind = "research_exit_1000_1m_0931" if v2 else "exit_1000_1m"
            if not day or kind not in {"daily", "stk_limit", minute_kind}:
                continue
            _expect(day in dates and day <= as_of_date and code in allowed_codes, "LABEL_REQUEST_OUTSIDE_RESEARCH_SCOPE")
            endpoint = "stk_mins" if kind == minute_kind else kind
            item = {"endpoint": endpoint, "trade_date": day}
            if endpoint == "stk_mins":
                item["ts_code"] = code
            identity = key(item)
            if identity in attempted:
                continue
            if identity not in pending:
                pending[identity] = item
            if endpoint != "stk_mins":
                pending[identity].setdefault("required_codes", []).append(code)
        if not pending or limiter.expired():
            break
        batch([pending[key] for key in sorted(pending)])
    auction_receipts = [row for row in receipts if row["endpoint"] == auction_endpoint]
    unavailable_auction = [row for row in auction_receipts if row["status"] not in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"}]
    auction_attempts_complete = all(row.get("network_request_performed") is True
                                    or row["status"] == "EXISTING_TRUTH_NOT_OVERWRITTEN"
                                    or v2 and row["status"] == "HISTORY_BEFORE_CANONICAL_COVERAGE"
                                    for row in auction_receipts)
    all_cohorts_complete = all(row["complete"] for row in labels["cohorts_by_date"].values())
    result = {
        "schema_version": SCHEMA, "as_of_date": as_of_date, "budget": dict(limiter.budget),
        "status": ("BLOCKED_AUCTION_ATTEMPTS_INCOMPLETE" if not auction_attempts_complete else
                   "RESEARCH_LABEL_COHORTS_COMPLETE" if all_cohorts_complete else "PENDING_RESEARCH_TRUTH"),
        "api_calls": limiter.calls, "elapsed_seconds": round(limiter.clock() - limiter.started, 2),
        "replay_rounds": rounds, "candidate_rows": len(candidates),
        "candidate_source_bindings": bindings, "cohorts": labels["cohorts_by_date"],
        "label_status_counts": dict(Counter(row["label_status"] for row in labels["rows"])),
        "auction_dates_considered": len(auction_by_day), "auction_unavailable_dates": len(unavailable_auction),
        "auction_attempts_complete": auction_attempts_complete,
        "auction_missing_policy": "FINAL_AUCTION_PREFERRED_OTHERWISE_DISCLOSED_DAILY_OPEN_PROXY",
        "request_receipts": sorted(receipts, key=lambda row: (row["endpoint"], row["trade_date"], row["ts_code"] or "")),
        "new_source_files": sorted(written.values(), key=lambda binding: binding["path"]),
        "credential_persisted": False, "production_writes": False, "purchase_permission": False,
        "existing_truth_overwritten": False, "training_performed": False, "release_allowed": False,
        "actual_execution_claimed": False,
    }
    if v2:
        qualified = all(row["status"] in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN", "HISTORY_BEFORE_CANONICAL_COVERAGE"} for row in auction_receipts)
        result.update(dict(CONTRACT), schema_version="dc20_profit_1000_collection_receipt_v2",
                      plan_version="v2", plan_sha256=runner.sha(runner.plan_path("v2")),
                      collection_request_sha256=runner.sha(HERE / "COLLECTION_V2.json"),
                      base_archive_import_binding=base_binding, auction_evidence_complete=qualified,
                      existing_source_files=sorted(existing_files.values(), key=lambda b: b["path"]),
                      auction_missing_policy="CANONICAL_OR_VALIDATED_UNAVAILABLE_OR_PRE_COVERAGE_ONLY")
        if not qualified:
            result["status"] = "BLOCKED_CANONICAL_EVIDENCE_INCOMPLETE"
    if progress:
        progress({"event": "COLLECTION_FINISHED", "status": result["status"],
                  "api_calls": limiter.calls, "label_status_counts": result["label_status_counts"]})
    return result


def main():
    from work.profit_1000_upgrade.run import prepare_history
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--plan-version", choices=("v1", "v2"), default="v1")
    args = parser.parse_args()
    contract_path = HERE / ("COLLECTION_V2.json" if args.plan_version == "v2" else "COLLECTION.json")
    contract = json.loads(contract_path.read_text())
    _expect(contract.get("schema_version") == "dc20_profit_1000_collection_request_" + args.plan_version
            and contract.get("production_writes") is False and contract.get("purchase_permission") is False
            and contract.get("budget") == DEFAULT_BUDGET, "COLLECTION_REQUEST_CONTRACT_CHANGED")
    if args.plan_version == "v2":
        from work.profit_1000_upgrade.policy_v2 import validate_contract
        from work.profit_1000_upgrade.run import load_plan
        validate_contract(contract)
        plan = load_plan("v2")
        _expect(contract.get("as_of_date") == plan["as_of_date"] and contract.get("base_archive_sha256") == plan["base_archive"]["zip_sha256"]
                and contract.get("max_http_response_bytes") == 8000000, "V2_COLLECTION_PLAN_DRIFT")
    report_path = args.report.resolve()
    if args.plan_version == "v2":
        _expect(report_path == args.root.resolve() / "collection_receipt.json", "V2_RECEIPT_MUST_STAY_IN_NEW_RESEARCH_MIRROR")
    _expect(report_path != CHECKOUT and CHECKOUT not in report_path.parents
            and not args.report.is_symlink(), "REPORT_MUST_BE_OUTSIDE_CHECKOUT")
    for path in (args.report, *args.report.parents):
        _expect(not path.is_symlink(), "REPORT_SYMLINK_FORBIDDEN")
    _expect(not report_path.exists(), "EXISTING_RECEIPT_CANNOT_BE_OVERWRITTEN")
    manifest, _, _ = prepare_history(args.root, plan_version=args.plan_version)
    result = collect_history(args.root, manifest, as_of_date=contract["as_of_date"],
                             token=os.environ.get("TUSHARE_TOKEN", ""), budget=contract["budget"],
                             plan_version=args.plan_version,
                             progress=lambda item: print(json.dumps(item, sort_keys=True), flush=True))
    result["collection_request_sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x") as handle:
        handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return 0 if result["status"] == "RESEARCH_LABEL_COHORTS_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
