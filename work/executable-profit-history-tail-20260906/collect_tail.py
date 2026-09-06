#!/usr/bin/env python3
"""One new source run for nineteen audited missing partitions, never a rerun.

The old source run remains failed. This module imports byte-pinned query/row
validators, not the old all-session entrypoint. No labels, models or production
files are written. Local entrypoint use is refused; tests inject a fake transport.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE_PACKAGE = "work/executable-profit-history-source-20260906"
SOURCE_COLLECTOR_SHA = "721f3a52e7ff8070c2951528f9d88ae6715d16f61b880a9f47e06ceced40088b"
SOURCE_PLAN_SHA = "e150bdae3d664cdfaea89ef3ada6603db4d1fe1e1c0e70b57e29287592872cdd"
FULL_SESSION_SHA = "526c0d07f3a31cb9e8eee864abe6a47321e641c80d716a8e0c90e84201ef0b6e"
MAX_REQUESTS, MAX_BULK_REQUESTS, MAX_CANARY_REQUESTS = 78, 76, 2
SOFT_DEADLINE_SECONDS = 600
OUTPUT_SUBDIR = "dc20-profit-history-tail-20260906"
LATER_DATES = ("20260825", "20260826", "20260827", "20260828", "20260831", "20260901", "20260902", "20260903", "20260904")
MISSING_PARTITIONS = [{"trade_date": "20260824", "api": "stk_limit"}] + [
    {"trade_date": date, "api": api} for date in LATER_DATES for api in ("daily", "stk_limit")]
PREVIOUS_SOURCE = {
    "run_id": "34023469106", "run_sha": "d5f3df57c78b0458d1329034c94ec324827aa390", "run_attempt": "1",
    "artifact_id": "9987577487",
    "zip_sha256": "e56fcaf1ee54bd8ff562d1eae832a2d46397de256588c4aa097556533e0410c7",
    "manifest_sha256": "1be7507a8fdea6764c2ce5e9e0e617f38792a5eb41971b5d3d10e1e94c7e6953",
    "audit_sha256": "0685b96c0657c3c1c528743d0b80dc1adee64673ea89643e71732d0103a04190",
    "audit_script_sha256": "105b32e9af800c9d556263757300aecb43d56b1b5d4d9d0c077157b5556b56a3",
    "collection_status": "BLOCKED_COLLECTION", "failure_code": "COLLECTION_SOFT_DEADLINE_EXCEEDED",
    "requests_attempted": 3354, "completed_sessions": 916, "verified_required_partitions": 1833,
    "expected_required_partitions": 1852, "overlap_compared_fields": 897756, "overlap_conflicts": 0,
    "old_failure_status_preserved": True,
}


def load_source(repo):
    import hashlib
    path = Path(repo) / SOURCE_PACKAGE / "collect.py"
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("UNSAFE_PINNED_COLLECTOR")
    if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_COLLECTOR_SHA:
        raise ValueError("PINNED_COLLECTOR_SHA_MISMATCH")
    spec = importlib.util.spec_from_file_location("dc20_pinned_history_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = load_source(REPO)


def expected_plan():
    return {
        "schema_version": "dc20_profit_history_tail_plan_v1", "as_of_date": "20260904", "tail_sessions": 20,
        "source_repository": "njedu2023-prog/DC20", "source_commit": "3e2299a07f7b4430002da0b870c47ecf57c49bb3",
        "source_package": SOURCE_PACKAGE, "source_collector_sha256": SOURCE_COLLECTOR_SHA,
        "source_plan_sha256": SOURCE_PLAN_SHA, "source_inputs": SOURCE.INPUTS,
        "full_session_plan_sha256": FULL_SESSION_SHA, "full_session_count": 926, "full_candidate_date_keys": 102935,
        "previous_source": PREVIOUS_SOURCE, "missing_partitions": MISSING_PARTITIONS,
        "partition_count": 19, "candidate_date_keys": 821, "candidate_partition_keys": 1533,
        "daily_mode": "full_market_paged", "page_size": 5000, "max_pages": 4,
        "max_requests": MAX_REQUESTS, "max_bulk_requests": MAX_BULK_REQUESTS, "max_canary_requests": MAX_CANARY_REQUESTS,
        "min_request_interval_seconds": .5, "soft_deadline_seconds": SOFT_DEADLINE_SECONDS,
        "required_apis": ["daily", "stk_limit"], "optional_adj_factor": False,
        "retry_policy": "NONE", "cross_run_pagination": False, "copy_previous_partitions": False,
        "training_authorized": False, "production_writes": False,
    }


def build_request(repo, plan):
    SOURCE.require(plan == expected_plan(), "TAIL_PLAN_CONTRACT_MISMATCH")
    source_plan_path = SOURCE.safe_input(repo, SOURCE_PACKAGE + "/PLAN.json")
    SOURCE.require(SOURCE.sha(source_plan_path) == SOURCE_PLAN_SHA, "SOURCE_PLAN_SHA_MISMATCH")
    original = SOURCE.build_request(repo, json.loads(source_plan_path.read_text()))
    SOURCE.require(SOURCE.sha_bytes(SOURCE.canonical(original["sessions"])) == FULL_SESSION_SHA, "FULL_SESSION_SCOPE_CHANGED")
    SOURCE.require(len(original["sessions"]) == 926 and sum(len(s["codes"]) for s in original["sessions"]) == 102935, "FULL_SCOPE_COUNT_CHANGED")
    codes = {s["trade_date"]: s["codes"] for s in original["sessions"]}
    partitions = [{**p, "codes": codes[p["trade_date"]]} for p in MISSING_PARTITIONS]
    SOURCE.require(sum(len(p["codes"]) for p in partitions) == 1533, "TAIL_CODE_SCOPE_CHANGED")
    canaries = []
    for api in ("daily", "stk_limit"):
        first = next(p for p in partitions if p["api"] == api)
        canaries.append({"api": api, "trade_date": first["trade_date"], "ts_code": first["codes"][0]})
    return {"schema_version": "dc20_profit_history_tail_request_v1", "as_of_date": "20260904",
            "previous_source": PREVIOUS_SOURCE, "full_session_plan_sha256": FULL_SESSION_SHA,
            "source_inputs": SOURCE.INPUTS, "partitions": partitions, "canaries": canaries,
            "max_requests": MAX_REQUESTS, "max_bulk_requests": MAX_BULK_REQUESTS,
            "soft_deadline_seconds": SOFT_DEADLINE_SECONDS}


def request_contract(expected):
    return {**{key: value for key, value in expected.items() if key != "partitions"},
            "partition_plan_sha256": SOURCE.sha_bytes(SOURCE.canonical(expected["partitions"])),
            "partition_count": len(expected["partitions"]), "candidate_partition_keys": sum(len(p["codes"]) for p in expected["partitions"])}


def verify_request(request, expected):
    SOURCE.require({k: v for k, v in request.items() if k != "_activation"} == request_contract(expected), "TAIL_REQUEST_SCOPE_CHANGED")


class TailClient(SOURCE.Client):
    def __init__(self, token, artifacts, request, *, transport=SOURCE.https_post, clock=time.monotonic, sleep=time.sleep, environment=None):
        self.allowed = {(p["trade_date"], p["api"]): set(p["codes"]) for p in request["partitions"]}
        self.canaries = {(c["trade_date"], c["api"]): c["ts_code"] for c in request["canaries"]}
        self.used_canaries = set()
        self.bulk_count, self.canary_count = 0, 0

        def bounded_transport(body):
            # Recheck after the inherited pacing sleep, immediately before HTTPS.
            SOURCE.require(self.clock() - self.started_monotonic < SOFT_DEADLINE_SECONDS, "TAIL_SOFT_DEADLINE_EXCEEDED")
            SOURCE.require(self.count <= MAX_REQUESTS, "TAIL_REQUEST_BUDGET_EXHAUSTED")
            return transport(body)

        super().__init__(token, artifacts, transport=bounded_transport, clock=clock, sleep=sleep, environment=environment)

    def query(self, api, params, wanted, *, canary=False):
        key = (params.get("trade_date"), api)
        SOURCE.require(key in self.allowed, "QUERY_OUTSIDE_MISSING_PARTITIONS")
        SOURCE.require(self.count < MAX_REQUESTS, "TAIL_REQUEST_BUDGET_EXHAUSTED")
        SOURCE.require(self.clock() - self.started_monotonic < SOFT_DEADLINE_SECONDS, "TAIL_SOFT_DEADLINE_EXCEEDED")
        if canary:
            SOURCE.require(key in self.canaries and key not in self.used_canaries and self.canary_count < MAX_CANARY_REQUESTS, "TAIL_CANARY_SCOPE_OR_COUNT_CHANGED")
            SOURCE.require(params.get("ts_code") == self.canaries[key] and set(wanted) == {self.canaries[key]}, "TAIL_CANARY_CODE_CHANGED")
            self.used_canaries.add(key)
            self.canary_count += 1
        else:
            SOURCE.require(set(wanted) == self.allowed[key], "TAIL_CANDIDATE_SET_CHANGED")
            SOURCE.require(self.bulk_count < MAX_BULK_REQUESTS, "TAIL_BULK_BUDGET_EXHAUSTED")
            self.bulk_count += 1
        return super().query(api, params, wanted, canary=canary)


def perform_tail(client, request, artifacts, provenance):
    status = {"schema_version": "dc20_profit_history_tail_collection_v1", "status": "RUNNING_TAIL_CANARY",
              "started_utc": SOURCE.utc(), "as_of_date": "20260904", "provenance": provenance,
              "expected_partitions": 19, "completed_partitions": 0, "request_count": 0, "coverage": [],
              "required_collection_complete": False, "required_candidate_coverage_complete": False,
              "optional_adj_factor": "NOT_REQUESTED", "source_data_only": True, "production_writes": False,
              "models_trained": 0, "training_authorized": False, "previous_status_rewritten": False,
              "actual_fill_claim": False, "corporate_actions_resolved": False, "historically_available_at_D": False,
              "tail_window_is_forced_exit": False}
    artifacts.json("status.json", status, replace_status=True)
    try:
        for canary in request["canaries"]:
            api = canary["api"]
            params = {"trade_date": canary["trade_date"], "ts_code": canary["ts_code"], "offset": 0, "limit": 5000}
            rows = client.query(api, params, {canary["ts_code"]}, canary=True)
            artifacts.csv("canary/" + api + ".csv", SOURCE.FIELDS[api], rows)
        status["status"] = "COLLECTING_TAIL_REQUIRED_PARTITIONS"
        gaps = False
        for partition in request["partitions"]:
            date, api, codes = partition["trade_date"], partition["api"], set(partition["codes"])
            selected, info = SOURCE.collect_pages(client, api, date, codes)
            artifacts.csv(f"candidate_sources/{date}/{api}.csv", SOURCE.FIELDS[api], selected)
            status["coverage"].append({"trade_date": date, "api": api, "requested_candidate_count": len(codes), "info": info})
            status["completed_partitions"] += 1
            status["request_count"] = client.count
            gaps |= bool(info["missing_candidate_codes"] or info["incomplete_candidate_fields"])
            artifacts.json("status.json", status, replace_status=True)
            print(json.dumps({"completed_tail_partitions": status["completed_partitions"], "last_date": date, "last_api": api, "requests": client.count}), flush=True)
        SOURCE.require(status["completed_partitions"] == 19, "TAIL_PARTITION_COMPLETENESS_CHANGED")
        status.update(status="COLLECTED_TAIL_REQUIRED_PARTITIONS_WITH_GAPS" if gaps else "COLLECTED_TAIL_REQUIRED_PARTITIONS",
                      required_collection_complete=True, required_candidate_coverage_complete=not gaps)
        result = 0
    except SOURCE.CollectionError as exc:
        status.update(status="BLOCKED_TAIL_COLLECTION", failure_code=exc.code)
        result = 2
    except Exception:
        status.update(status="BLOCKED_TAIL_COLLECTION", failure_code="UNCLASSIFIED_TAIL_COLLECTION_FAILURE")
        result = 2
    status.update(completed_utc=SOURCE.utc(), request_count=client.count, bulk_requests=client.bulk_count, canary_requests=client.canary_count)
    artifacts.json("status.json", status, replace_status=True)
    artifacts.json("artifact_manifest.json", {"schema_version": "dc20_isolated_source_artifact_manifest_v1",
                   "artifact_role": "required_partition_tail", "status": status["status"], "provenance": provenance,
                   **client.identity, "files": {p: item for p, item in artifacts.hashes.items() if p != "artifact_manifest.json"},
                   "requests_attempted": client.count, "production_writes": False, "source_data_only": True})
    print(json.dumps({"status": status["status"], "completed_partitions": status["completed_partitions"], "requests_attempted": client.count,
                      "artifact_manifest_sha256": artifacts.hashes["artifact_manifest.json"]["sha256"]}), flush=True)
    return result, status


def prepare_output(explicit, environment):
    base = Path(environment.get("RUNNER_TEMP", ""))
    SOURCE.require(base.is_absolute() and base.is_dir(), "RUNNER_TEMP_REQUIRED")
    target = base / OUTPUT_SUBDIR
    SOURCE.require(Path(explicit).is_absolute() and Path(explicit) == target, "OUTPUT_NOT_FIXED_RUNNER_TEMP")
    SOURCE.no_symlinks(target)
    SOURCE.require(not target.exists(), "TAIL_OUTPUT_MUST_BE_NEW")
    SOURCE.require(not target.is_relative_to(REPO) and not REPO.is_relative_to(target), "TAIL_OUTPUT_OVERLAPS_REPOSITORY")
    target.mkdir(mode=0o700)
    return target


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        SOURCE.require(os.environ.get("GITHUB_ACTIONS") == "true", "LOCAL_NETWORK_COLLECTION_FORBIDDEN")
        # Direct execution cannot bypass the workflow's twice-run guard.
        spec = importlib.util.spec_from_file_location("tail_guard", HERE / "guard.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        guard.validate(REPO, os.environ, json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), guard.datetime.now(guard.timezone.utc))
        plan_path, request_path = (SOURCE.safe_input(HERE, name) for name in ("PLAN.json", "REQUEST.json"))
        plan, request = json.loads(plan_path.read_text()), json.loads(request_path.read_text())
        expected = build_request(REPO, plan)
        verify_request(request, expected)
        SOURCE.require(request.get("_activation", {}).get("state") == "ACTIVATED", "TAIL_REQUEST_NOT_ACTIVATED")
        artifacts = SOURCE.Artifacts(prepare_output(args.output, os.environ))
        token = os.environ.get("TUSHARE_TOKEN", "")
        client = TailClient(token, artifacts, expected)
        for name, path in (("PLAN.json", plan_path), ("REQUEST.json", request_path)):
            payload = path.read_bytes()
            SOURCE.require(token.encode() not in payload and token.encode() not in SOURCE.canonical(json.loads(payload)), "SECRET_IN_CONTROL_FILE_REFUSED")
            artifacts.write(name, payload)
        artifacts.json("partition_plan.json", expected["partitions"])
        provenance = {"source_repository": plan["source_repository"], "source_commit": plan["source_commit"],
                      "previous_source": PREVIOUS_SOURCE, "source_collector_sha256": SOURCE_COLLECTOR_SHA,
                      "source_plan_sha256": SOURCE_PLAN_SHA, "source_inputs": SOURCE.INPUTS,
                      "full_session_plan_sha256": FULL_SESSION_SHA,
                      "partition_plan_sha256": request_contract(expected)["partition_plan_sha256"],
                      "tail_plan_sha256": SOURCE.sha(plan_path), "tail_request_sha256": SOURCE.sha(request_path),
                      "tail_collector_sha256": SOURCE.sha(HERE / "collect_tail.py"),
                      "soft_deadline_seconds": 600, "max_requests": 78,
                      "market_endpoint": SOURCE.ENDPOINT, "global_other_IP_activity_audited": False,
                      "old_failure_status_preserved": True, "cross_run_pagination": False, **client.identity}
        code, _ = perform_tail(client, expected, artifacts, provenance)
        return code
    except SOURCE.CollectionError as exc:
        code = exc.code
    except Exception:
        code = "TAIL_STARTUP_VALIDATION_FAILURE"
    print(json.dumps({"status": "BLOCKED_TAIL_STARTUP", "failure_code": code, "production_writes": False, "models_trained": 0}), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
