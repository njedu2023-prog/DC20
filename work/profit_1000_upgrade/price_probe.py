#!/usr/bin/env python3
"""Read-only, fixed cross-source auction-price diagnostic; never settles labels.

Official docs 353 and 369 describe different auction endpoints. This probe
preserves source values, including disagreement. It cannot select an entry
price, change source policy, purchase data access, or modify production.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib import error

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from suspension_probe import (_fresh_output, _write, _sha, _json, _date,
                              official_call, classify, MAX_RESPONSE_BYTES)

SCHEMA = "dc20_fixed_auction_price_diagnostic_v1"
MAX_CALLS, MAX_SECONDS, TIMEOUT_SECONDS = 12, 300, 20
INTERVAL_SECONDS = 1.0
CASES = (("002467.SZ", "20221114"), ("600191.SH", "20250103"),
         ("002265.SZ", "20250103"), ("002724.SZ", "20260817"))
FIELDS = {
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"),
    "stk_auction_o": ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount", "vwap"),
    "stk_auction": ("ts_code", "trade_date", "price", "vol", "amount", "pre_close"),
}


def contract():
    trigger = HERE / "PRICE_REQUEST.json"
    if any(p.is_symlink() for p in (trigger, *trigger.parents)):
        raise ValueError("aliased request contract")
    raw = trigger.read_bytes()
    if json.loads(raw) != {"schema_version": SCHEMA,
                           "request_id": "20260912-cross-source-price-v1",
                           "max_api_calls": MAX_CALLS,
                           "production_writes": False, "purchase_permission": False}:
        raise ValueError("fixed request contract changed")
    return {"schema_version": SCHEMA, "max_api_calls": MAX_CALLS,
            "request_sha256": _sha(raw),
            "max_seconds": MAX_SECONDS, "timeout_seconds": TIMEOUT_SECONDS,
            "request_interval_seconds": INTERVAL_SECONDS,
            "cases": [{"ts_code": code, "trade_date": date} for code, date in CASES],
            "endpoints": {k: list(v) for k, v in FIELDS.items()},
            "history_artifact_sha256": "d004f6decba35d6148082333764ba0988bc3fa25062224492d485ac031fe2a29",
            "history_run_id": "34671477608", "source_as_of_date": "20260911",
            "documentation": ["https://tushare.pro/document/2?doc_id=353", "https://tushare.pro/document/2?doc_id=369"],
            "production_writes": False, "settlement_allowed": False,
            "entry_price_selected": False, "purchase_permission": False}


def _rows(payload, endpoint, case):
    data = payload.get("data")
    if not isinstance(data, dict) or not {"fields", "items"} <= set(data) or set(data) - {"fields", "items", "count", "has_more"}:
        raise ValueError("invalid response table")
    fields, items = data["fields"], data["items"]
    if not isinstance(fields, list) or len(fields) != len(FIELDS[endpoint]) or set(fields) != set(FIELDS[endpoint]):
        raise ValueError("invalid fields")
    if not isinstance(items, list) or len(items) > 1:
        raise ValueError("unexpected row count")
    if "has_more" in data and (type(data["has_more"]) is not bool or data["has_more"]):
        raise ValueError("truncated response")
    if "count" in data and (type(data["count"]) is not int or data["count"] != len(items)):
        raise ValueError("unexpected count")
    rows = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ValueError("invalid row")
        row = dict(zip(fields, item))
        if row["ts_code"] != case["ts_code"] or _date(row["trade_date"]) != case["trade_date"]:
            raise ValueError("wrong source identity")
        numbers = {}
        for key in fields:
            if key in {"ts_code", "trade_date"}:
                continue
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError("invalid number")
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError("invalid numeric truth")
            numbers[key] = number
        if endpoint != "stk_auction" and not numbers["low"] <= min(numbers["open"], numbers["close"]) <= max(numbers["open"], numbers["close"]) <= numbers["high"]:
            raise ValueError("invalid OHLC")
        rows.append(row)
    return data, rows


def _equal(a, b):
    try:
        return Decimal(str(a)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(str(b)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # Preserve out-of-range source values without claiming a comparison.
        return None


def _comparison(case, observations):
    # Comparison reports cents only; original values remain bound in artifacts.
    daily = observations.get("daily")
    auction_o = observations.get("stk_auction_o")
    auction = observations.get("stk_auction")
    result = {**case, "entry_price_selected": False, "settlement_allowed": False,
              "daily_open": daily.get("open") if daily else None,
              "auction_o_open": auction_o.get("open") if auction_o else None,
              "auction_o_close": auction_o.get("close") if auction_o else None,
              "auction_price": auction.get("price") if auction else None}
    for name, row, field in (("auction_o_open", auction_o, "open"),
                             ("auction_o_close", auction_o, "close"),
                             ("auction_price", auction, "price")):
        result[name + "_matches_daily_open_at_cent"] = _equal(row[field], daily["open"]) if row and daily else None
    result["status"] = "THREE_SOURCES_OBSERVED_REQUIRES_POLICY_REVIEW" if all((daily, auction_o, auction)) else "PARTIAL_SOURCE_EVIDENCE_NO_PRICE_SELECTED"
    return result


def probe(output, *, token, runner_temp=None, call=official_call, clock=time.monotonic, sleep=time.sleep):
    plan = contract()
    out = _fresh_output(output, runner_temp or os.environ.get("RUNNER_TEMP"))
    files = [_write(out, "request_contract.json", _json(plan), token)]
    report = {"schema_version": SCHEMA, "run_commit": os.environ.get("GITHUB_SHA"),
              "run_id": os.environ.get("GITHUB_RUN_ID"), "contract_sha256": _sha(_json(plan)),
              "source_sha256": _sha(Path(__file__).read_bytes()), "api_calls": 0,
              "requests": [], "cases": [], "source_files": files,
              "production_writes": False, "settlement_performed": False,
              "entry_price_selected": False, "training_performed": False,
              "credential_persisted": False, "server_messages_persisted": False,
              "release_allowed": False, "purchase_permission": False}
    started, next_request = clock(), clock()
    denied, credential_failed = set(), False
    for case in plan["cases"]:
        observations = {}
        for endpoint, fields in FIELDS.items():
            receipt = {"query_number": len(report["requests"]) + 1, "endpoint": endpoint,
                       "params": dict(case), "fields": list(fields), "source_files": [],
                       "network_request_performed": False, "status": "PENDING_UNKNOWN"}
            if not token.strip():
                receipt["status"] = "CREDENTIAL_ABSENT"
            elif credential_failed:
                receipt["status"] = "SKIPPED_AFTER_CREDENTIAL_FAILURE"
            elif endpoint in denied:
                receipt["status"] = "SKIPPED_AFTER_ENDPOINT_ENTITLEMENT_FAILURE"
            elif report["api_calls"] >= MAX_CALLS or max(clock(), next_request) - started + TIMEOUT_SECONDS > MAX_SECONDS:
                receipt["status"] = "SKIPPED_BUDGET_EXHAUSTED"
            else:
                if next_request > clock():
                    sleep(next_request - clock())
                if clock() - started + TIMEOUT_SECONDS > MAX_SECONDS:
                    receipt["status"] = "SKIPPED_BUDGET_EXHAUSTED"
                else:
                    report["api_calls"] += 1
                    next_request = clock() + INTERVAL_SECONDS
                    receipt.update(network_request_performed=True, fetched_at_utc=datetime.now(timezone.utc).isoformat())
                    try:
                        raw = call(endpoint, dict(case), fields, token, TIMEOUT_SECONDS)
                        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError("invalid response bytes")
                        receipt.update(http_response_sha256=_sha(raw), http_response_bytes=len(raw))
                        payload = json.loads(raw)
                        receipt["status"] = status = classify(payload)
                        if status == "ENTITLEMENT_DENIED":
                            denied.add(endpoint)
                        elif status == "CREDENTIAL_REJECTED":
                            credential_failed = True
                        elif status == "SUCCESS":
                            data, rows = _rows(payload, endpoint, case)
                            stem = "responses/" + str(receipt["query_number"]).zfill(2) + "_" + endpoint
                            binding = _write(out, stem + ".data.json", _json(data), token)
                            metadata = {"source": "tushare:" + endpoint, "params": dict(case),
                                        "fields": list(fields), "data_sha256": binding["sha256"],
                                        "http_response_sha256": receipt["http_response_sha256"],
                                        "fetched_at_utc": receipt["fetched_at_utc"], "row_count": len(rows),
                                        "diagnostic_only": True, "data_values_modified": False, "immutable": True}
                            pair = [binding, _write(out, stem + ".meta.json", _json(metadata), token)]
                            files.extend(pair)
                            receipt.update(source_files=pair, row_count=len(rows),
                                           status="SOURCE_ROW_PRESENT" if rows else "SOURCE_ROW_ABSENT")
                            if rows:
                                observations[endpoint] = rows[0]
                    except error.HTTPError as exc:
                        receipt.update(status="HTTP_ERROR", http_status=exc.code)
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        receipt["status"] = "INVALID_RESPONSE_NOT_INTERPRETED"
                    except Exception:
                        receipt["status"] = "NETWORK_OR_RESPONSE_ERROR"
            report["requests"].append(receipt)
            files.append(_write(out, "receipts/" + str(receipt["query_number"]).zfill(2) + ".json", _json(receipt), token))
        report["cases"].append(_comparison(case, observations))
    report["status_counts"] = dict(Counter(r["status"] for r in report["requests"]))
    report["status"] = "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT" if all(r["status"] in {"SOURCE_ROW_PRESENT", "SOURCE_ROW_ABSENT"} for r in report["requests"]) else "DIAGNOSTIC_PARTIAL_NOT_SETTLEMENT"
    report["elapsed_seconds"] = round(clock() - started, 3)
    _write(out, "receipt.json", _json(report), token)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = probe(parser.parse_args().output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps({k: result[k] for k in ("status", "api_calls", "status_counts", "settlement_performed")}, sort_keys=True))
    return 0 if result["status"] == "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
