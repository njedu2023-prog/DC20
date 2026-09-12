#!/usr/bin/env python3
"""Two bounded official-minute reads. Safe receipts; no repository writes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract():
    plan = json.loads((HERE / "REQUEST.json").read_text())
    if (plan.get("schema_version") != "dc20_profit_1000_probe_request_v1"
            or plan.get("max_api_calls") != 2 or plan.get("timeout_seconds") != 20
            or plan.get("production_writes") is not False
            or plan.get("purchase_permission") is not False
            or plan.get("probes") != [{"ts_code": "600000.SH", "trade_date": "20260911"},
                                      {"ts_code": "600000.SH", "trade_date": "20221114"}]):
        raise ValueError("probe request scope changed")
    adapter = ROOT / "src/top10decision/decision/shadow_exit_minute_truth.py"
    calendar = ROOT / "data/market/trade_cal_sse.csv"
    if sha(adapter) != plan["minute_adapter_sha256"] or sha(calendar) != plan["calendar_sha256"]:
        raise ValueError("probe input source SHA changed")
    with calendar.open(newline="", encoding="utf-8-sig") as handle:
        days = {r["cal_date"] for r in csv.DictReader(handle)
                if r["exchange"] == "SSE" and r["is_open"] == "1"}
    if any(p["trade_date"] not in days for p in plan["probes"]):
        raise ValueError("probe date is not an exchange session")
    spec = importlib.util.spec_from_file_location("profit_probe_minute_adapter", adapter)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return plan, module


def official_call(params, fields, token, timeout):
    body = json.dumps({"api_name": "stk_mins", "token": token,
                       "params": params, "fields": ",".join(fields)}).encode()
    req = request.Request("https://api.tushare.pro", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(2_000_000))


def classify(payload):
    """Never retain arbitrary server messages: even errors may echo credentials."""
    if not isinstance(payload, dict) or type(payload.get("code")) is not int:
        return "INVALID_RESPONSE"
    if payload["code"] == 0:
        return "SUCCESS"
    msg = str(payload.get("msg", "")).lower()
    if any(word in msg for word in ("权限", "permission", "授权", "积分")):
        return "ENTITLEMENT_DENIED"
    if any(word in msg for word in ("token", "凭证")):
        return "CREDENTIAL_REJECTED"
    if any(word in msg for word in ("频率", "每分钟", "rate limit")):
        return "RATE_LIMITED"
    return "API_REJECTED"


def probe(output, *, token, call=official_call):
    plan, adapter = load_contract()
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("symlink output forbidden")
    resolved = output.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("probe evidence must be outside checkout")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "dc20_profit_1000_probe_receipt_v1",
              "request_id": plan["request_id"], "request_sha256": sha(HERE / "REQUEST.json"),
              "run_commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
              "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
              "api_calls": 0, "probes": [], "source_files": [],
              "production_writes": False, "credential_persisted": False,
              "trained": False, "release_allowed": False}
    stopped = not bool(token.strip())
    for item in plan["probes"]:
        row = dict(item, endpoint="stk_mins")
        report["probes"].append(row)
        if stopped:
            row["status"] = "CREDENTIAL_ABSENT" if not token.strip() else "SKIPPED_AFTER_ACCESS_FAILURE"
            continue
        report["api_calls"] += 1
        try:
            payload = call(adapter.request_parameters(item["trade_date"], item["ts_code"]),
                           adapter.FIELDS, token, plan["timeout_seconds"])
        except error.HTTPError as exc:
            row.update(status="HTTP_ERROR", http_status=exc.code)
            continue
        except Exception:
            row["status"] = "NETWORK_OR_RESPONSE_ERROR"
            continue
        status = classify(payload)
        if status != "SUCCESS":
            row["status"] = status
            row["api_code"] = payload.get("code") if isinstance(payload, dict) and type(payload.get("code")) is int else None
            stopped = status in {"ENTITLEMENT_DENIED", "CREDENTIAL_REJECTED"}
            continue
        try:
            data = payload["data"]
            fields, items = data["fields"], data["items"]
            if (len(fields) != len(adapter.FIELDS) or set(fields) != set(adapter.FIELDS)
                    or not isinstance(items, list) or len(items) not in {240, 241}
                    or any(not isinstance(r, list) or len(r) != len(fields) for r in items)):
                raise ValueError("invalid minute table")
            records = [dict(zip(fields, r)) for r in items]
            raw, meta = adapter.source_bytes(records, item["trade_date"], item["ts_code"],
                                              fetched_at_utc=report["fetched_at_utc"])
            # Validated numbers and identities only, not arbitrary API objects.
            if token.encode() in raw or token.encode() in meta:
                raise ValueError("credential-like content forbidden")
            paths = adapter.minute_paths(output, item["trade_date"], item["ts_code"])
            paths[0].parent.mkdir(parents=True, exist_ok=True)
            for path, body in zip(paths, (raw, meta)):
                with path.open("xb") as handle:
                    handle.write(body)
            validated = adapter.load_exit_minutes(output, item["trade_date"], item["ts_code"])
            report["source_files"].extend(validated["source_files"])
            row.update(status="COMPLETE_240_BAR_END", continuous_rows=240,
                       source_rows=len(items))
        except (KeyError, TypeError, ValueError):
            row["status"] = "INCOMPLETE_OR_INVALID_MINUTE_TRUTH"
    report["status"] = ("MINUTE_ACCESS_VERIFIED_NOT_HISTORY_COMPLETE"
                        if all(r["status"] == "COMPLETE_240_BAR_END" for r in report["probes"])
                        else "BLOCKED_MINUTE_ACCESS_OR_SCHEMA")
    (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = probe(args.output, token=os.environ.get("TUSHARE_TOKEN", ""))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] == "MINUTE_ACCESS_VERIFIED_NOT_HISTORY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
