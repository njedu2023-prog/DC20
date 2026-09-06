#!/usr/bin/env python3
"""Read-only two-run source admission. The original failed run stays failed."""
from __future__ import annotations

from decimal import Decimal
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
HEX = re.compile(r"[0-9a-f]{64}")
EXPECTED_ORIGINAL_ISSUES = [{"code": "REQUIRED_SOURCE_COLLECTION_INCOMPLETE"}, {"code": "COLLECTOR_REPORTED_BLOCK", "failure_code": "COLLECTION_SOFT_DEADLINE_EXCEEDED"}, {"code": "OVERLAP_REQUIRES_REVIEW_NO_CHERRY_PICKING"}]


class ClosureError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ClosureError(reason)


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def physical(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_PATH_FORBIDDEN")
    return path


def file(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and not {"", ".", ".."}.intersection(relative.parts) and "\\" not in str(relative), "UNSAFE_RELATIVE_PATH")
    target = physical(physical(root) / relative)
    require(target.is_file(), "MISSING_REGULAR_INPUT")
    return target


def pinned(root, relative, expected):
    require(isinstance(expected, str) and HEX.fullmatch(expected) is not None, "EXTERNAL_SHA_NOT_BOUND")
    path = file(root, relative)
    require(sha(path.read_bytes()) == expected, "PIN_MISMATCH:" + str(relative))
    return path


def load_module(repo, spec, name):
    path = pinned(repo, spec["path"], spec["sha256"])
    loader = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


def external_identity(config, supplied):
    for key in ("run_id", "run_sha", "run_attempt", "manifest_sha256"):
        require(isinstance(config.get(key), str) and bool(config[key]), "SOURCE_EXECUTION_NOT_YET_BOUND:" + key)
        require(config[key] == supplied.get(key), "EXTERNAL_SOURCE_EXECUTION_MISMATCH:" + key)
    require(re.fullmatch(r"\d{1,24}", supplied["run_id"]) and re.fullmatch(r"[0-9a-f]{40}", supplied["run_sha"]) and supplied["run_attempt"] == "1" and HEX.fullmatch(supplied["manifest_sha256"]), "EXTERNAL_EXECUTION_FORMAT_INVALID")
    return {"github_run_id": supplied["run_id"], "github_sha": supplied["run_sha"], "github_run_attempt": supplied["run_attempt"]}


def verify_manifest(A, root, expected_sha, identity):
    payload = pinned(root, "artifact_manifest.json", expected_sha).read_bytes()
    manifest = A.decode(payload)
    require(manifest.get("schema_version") == "dc20_isolated_source_artifact_manifest_v1", "MANIFEST_SCHEMA_INVALID")
    require(all(manifest.get(k) == v for k, v in identity.items()), "MANIFEST_RUN_IDENTITY_MISMATCH")
    require(manifest.get("source_data_only") is True and manifest.get("production_writes") is False, "MANIFEST_ISOLATION_INVALID")
    files = manifest.get("files")
    require(isinstance(files, dict) and "artifact_manifest.json" not in files, "MANIFEST_FILE_MAP_INVALID")
    for relative, metadata in files.items():
        current = pinned(root, relative, metadata["sha256"])
        require(current.stat().st_size == metadata["bytes"], "ARTIFACT_FILE_SIZE_CHANGED")
    actual = set()
    for path in physical(root).rglob("*"):
        physical(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(files) | {"artifact_manifest.json"}, "ARTIFACT_UNMANIFESTED_OR_MISSING_FILE")
    return manifest


def validated_original(A, repo, root, report_path, plan, identity, expected_audit_sha):
    cfg = plan["composite_source"]["original"]
    require(expected_audit_sha == cfg["audit_sha256"], "ORIGINAL_AUDIT_EXTERNAL_PIN_CHANGED")
    stored = A.decode(pinned(Path(report_path).parent, Path(report_path).name, expected_audit_sha).read_bytes())
    contract = A.load_contract(repo)
    recomputed = A.audit_bundle(root, repo, contract, expected_run_id=identity["run_id"], expected_run_sha=identity["run_sha"], expected_run_attempt=identity["run_attempt"], expected_manifest_sha256=identity["manifest_sha256"])
    require({k: v for k, v in stored.items() if k != "audited_at_utc"} == recomputed, "ORIGINAL_READONLY_AUDIT_REPLAY_DIFFERS")
    require(stored["audit_code_sha256"] == cfg["audit_script_sha256"] and stored["issues"] == EXPECTED_ORIGINAL_ISSUES, "ORIGINAL_HAS_UNAUTHORIZED_ISSUE_OR_AUDITOR")
    require(stored["source_ready_for_label_rebuild"] is False and stored["collection_status"] == "BLOCKED_COLLECTION", "ORIGINAL_FAILURE_MUST_REMAIN_FAILED")
    require(stored["verified_required_partitions"] == 1833 and stored["expected_required_partitions"] == 1852, "ORIGINAL_PARTITION_COUNT_CHANGED")
    missing = plan["composite_source"]["missing_partitions"]
    require(stored["missing_required_partitions"] == missing, "ORIGINAL_GAP_SET_CHANGED")
    unverified = [{**p, "reason": "NEW_COMPLETE_PARTITION_UNAVAILABLE"} for p in missing]
    require(stored["overlap"]["unverified"] == unverified and stored["overlap"]["conflicts"] == [], "ORIGINAL_OVERLAP_HAS_NON_GAP_PROBLEM")
    require(stored["overlap"]["compared_field_values"] == 897756 and stored["overlap"]["candidate_compared_field_values"] == 19998, "ORIGINAL_OVERLAP_EVIDENCE_CHANGED")
    return stored, contract


def parse_success_rows(A, root, manifest, receipt, wanted, *, canary):
    n, api, date = receipt["query_number"], receipt["api_name"], receipt["params"]["trade_date"]
    relative = f"responses/{n:06d}.json.gz"
    require(receipt.get("status") == "SUCCESS" and receipt.get("raw_response_artifact") == relative, "TAIL_QUERY_NOT_SUCCESSFUL_OR_UNBOUND")
    compressed = pinned(root, relative, manifest["files"][relative]["sha256"]).read_bytes()
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(A.MAX_RAW_BYTES + 1)
    require(len(raw) <= A.MAX_RAW_BYTES and receipt.get("response_sha256") == sha(raw) and receipt.get("response_bytes") == len(raw), "RAW_RESPONSE_HASH_OR_SIZE_CHANGED")
    obj = A.decode(raw, decimal=True)
    require(type(obj.get("code")) is int and obj["code"] == 0 and obj.get("msg") in (None, ""), "RAW_SUCCESS_PAYLOAD_INVALID")
    data = obj.get("data", {})
    fields, items = data.get("fields"), data.get("items")
    require(isinstance(fields, list) and len(fields) == len(set(fields)) and set(fields) == set(A.FIELDS[api]), "RAW_FIELDS_CHANGED")
    require(isinstance(items, list) and len(items) <= 5000, "RAW_PAGE_BOUND_INVALID")
    rows = []
    for values in items:
        require(isinstance(values, list) and len(values) == len(fields), "RAW_ROW_WIDTH_INVALID")
        row = dict(zip(fields, values))
        require(isinstance(row["ts_code"], str) and A.CODE.fullmatch(row["ts_code"]) and row["trade_date"] == date, "RAW_ROW_DATE_OR_CODE_INVALID")
        if row["ts_code"] in wanted:
            for field in A.FIELDS[api][2:]:
                if row[field] is None and not canary:
                    continue
                require(isinstance(row[field], (int, Decimal)) and not isinstance(row[field], bool), "RAW_CANDIDATE_NOT_NUMERIC_OR_EXPLICIT_NULL")
                A.number(row[field])
            positive = ("open", "high", "low", "close", "pre_close") if api == "daily" else ("pre_close", "up_limit", "down_limit")
            require(all(row[k] is None or row[k] > 0 for k in positive), "RAW_NONPOSITIVE_PRICE")
            if api == "daily":
                require(all(row[k] is None or row[k] >= 0 for k in ("vol", "amount")), "RAW_NEGATIVE_VOLUME")
                for upper, lower in (("high", "open"), ("high", "close"), ("high", "low"), ("open", "low"), ("close", "low")):
                    require(row[upper] is None or row[lower] is None or row[upper] + Decimal("0.011") >= row[lower], "RAW_OHLC_INVALID")
            elif row["up_limit"] is not None and row["down_limit"] is not None:
                require(row["up_limit"] >= row["down_limit"], "RAW_LIMIT_ORDER_INVALID")
        rows.append(row)
    require(len({r["ts_code"] for r in rows}) == len(rows), "RAW_DUPLICATE_CODE")
    require(receipt.get("row_count") == len(rows) and receipt.get("selected_row_count") == sum(r["ts_code"] in wanted for r in rows), "RECEIPT_ROW_COUNT_CHANGED")
    return rows, relative


def verify_csv(A, root, manifest, relative, api, selected):
    payload = pinned(root, relative, manifest["files"][relative]["sha256"]).read_bytes()
    fields, rows = A.csv_rows(payload)
    require(fields == A.FIELDS[api] and len(rows) == len(selected), "TAIL_CSV_SCHEMA_OR_COUNT_CHANGED")
    for saved, raw in zip(rows, selected):
        require(all(saved[k] == raw[k] for k in ("ts_code", "trade_date")), "TAIL_CSV_KEY_OR_ORDER_CHANGED")
        for field in A.FIELDS[api][2:]:
            require(saved[field] == "" if raw[field] is None else A.number(saved[field]) == A.number(raw[field]), "TAIL_CSV_DOES_NOT_REPLAY_RAW")


def verify_tail(A, T, repo, root, plan, identity, full_request):
    cfg = plan["composite_source"]["tail"]
    manifest = verify_manifest(A, root, identity["manifest_sha256"], external_identity(cfg, identity))
    require(manifest.get("artifact_role") == "required_partition_tail", "TAIL_ARTIFACT_ROLE_INVALID")
    tail_plan = A.decode(pinned(root, "PLAN.json", cfg["plan_sha256"]).read_bytes())
    request = A.decode(pinned(root, "REQUEST.json", cfg["request_sha256"]).read_bytes())
    expected = T.build_request(repo, tail_plan)
    T.verify_request(request, expected)
    require(request.get("_activation", {}).get("state") == "ACTIVATED", "TAIL_REQUEST_NOT_ACTIVATED")
    partitions = A.decode(file(root, "partition_plan.json").read_bytes())
    require(partitions == expected["partitions"] and sha(canonical(partitions)) == cfg["partition_plan_sha256"], "TAIL_PARTITION_PLAN_CHANGED")
    missing = plan["composite_source"]["missing_partitions"]
    require([{k: p[k] for k in ("trade_date", "api")} for p in partitions] == missing, "TAIL_MUST_EQUAL_ONLY_ORIGINAL_GAPS")
    all_codes = {s["trade_date"]: s["codes"] for s in full_request["sessions"]}
    require(all(p["codes"] == all_codes[p["trade_date"]] for p in partitions), "TAIL_CANDIDATES_DIFFER_FROM_ORIGINAL_SCOPE")
    require(len(partitions) == 19 and sum(len(p["codes"]) for p in partitions) == 1533, "TAIL_SCOPE_COUNTS_CHANGED")
    provenance = manifest.get("provenance", {})
    require(provenance.get("previous_source") == T.PREVIOUS_SOURCE, "TAIL_ORIGINAL_PROVENANCE_CHANGED")
    for key, expected_value in {"source_collector_sha256": plan["composite_source"]["original"]["source_collector_sha256"], "full_session_plan_sha256": plan["composite_source"]["full_session_plan_sha256"], "partition_plan_sha256": cfg["partition_plan_sha256"], "tail_plan_sha256": cfg["plan_sha256"], "tail_request_sha256": cfg["request_sha256"], "tail_collector_sha256": cfg["collector_sha256"], "source_inputs": plan["source_inputs"], "old_failure_status_preserved": True, "cross_run_pagination": False}.items():
        require(provenance.get(key) == expected_value, "TAIL_PROVENANCE_MISMATCH:" + key)
    status = A.decode(file(root, "status.json").read_bytes())
    require(status.get("schema_version") == "dc20_profit_history_tail_collection_v1" and status.get("status") == manifest.get("status") and status["status"] in ("COLLECTED_TAIL_REQUIRED_PARTITIONS", "COLLECTED_TAIL_REQUIRED_PARTITIONS_WITH_GAPS"), "TAIL_NOT_COMPLETED")
    require(status.get("provenance") == provenance and status.get("as_of_date") == "20260904", "TAIL_STATUS_PROVENANCE_OR_DATE_CHANGED")
    require(status.get("previous_status_rewritten") is False and status.get("models_trained") == 0 and status.get("training_authorized") is False and status.get("production_writes") is False, "TAIL_STATUS_EXCEEDS_SOURCE_SCOPE")
    count = manifest.get("requests_attempted")
    require(type(count) is int and 2 <= count <= 78 and status.get("request_count") == count, "TAIL_REQUEST_COUNT_INVALID")
    expected_receipts = {f"receipts/{n:06d}.json" for n in range(1, count + 1)}
    require({p for p in manifest["files"] if p.startswith("receipts/")} == expected_receipts, "TAIL_RECEIPT_SEQUENCE_INCOMPLETE")
    allowed = {(p["trade_date"], p["api"]): set(p["codes"]) for p in partitions}
    canary_map = {(c["trade_date"], c["api"]): c["ts_code"] for c in expected["canaries"]}
    seen_canaries, groups, full_tables, linked_raw, used_csv = set(), {}, {}, set(), set()
    active = None
    begin, end = A.timestamp(status["started_utc"]), A.timestamp(status["completed_utc"])
    previous = begin
    for n in range(1, count + 1):
        relative = f"receipts/{n:06d}.json"
        receipt = A.decode(file(root, relative).read_bytes())
        require(receipt.get("query_number") == n and all(receipt.get(k) == v for k, v in external_identity(cfg, identity).items()), "TAIL_RECEIPT_IDENTITY_CHANGED")
        require(receipt.get("credential_persisted") is False and receipt.get("upstream_message_persisted") is False, "TAIL_RECEIPT_PRIVACY_CHANGED")
        started, completed = A.timestamp(receipt["started_utc"]), A.timestamp(receipt["completed_utc"])
        require(previous <= started <= completed <= end, "TAIL_RECEIPT_TIME_INVALID")
        previous = started
        api, params = receipt["api_name"], receipt["params"]
        key = params.get("trade_date"), api
        require(key in allowed and api in ("daily", "stk_limit") and receipt["fields"] == A.FIELDS[api], "TAIL_QUERY_OUTSIDE_EXACT_GAPS")
        canary = "ts_code" in params
        require(set(params) == ({"trade_date", "limit", "offset", "ts_code"} if canary else {"trade_date", "limit", "offset"}) and params["limit"] == 5000 and type(params["offset"]) is int and 0 <= params["offset"] < 20000, "TAIL_QUERY_PARAMETERS_CHANGED")
        require(receipt.get("request_without_credential_sha256") == sha(canonical({"api_name": api, "params": params, "fields": ",".join(A.FIELDS[api])})), "TAIL_QUERY_HASH_MISMATCH")
        if canary:
            require(n <= 2 and key in canary_map and key not in seen_canaries and params["ts_code"] == canary_map[key] and params["offset"] == 0, "TAIL_CANARY_SCOPE_CHANGED")
            wanted = {canary_map[key]}
        else:
            require(n > 2 and seen_canaries == set(canary_map), "TAIL_REQUIRED_CANARIES_MISSING")
            wanted = allowed[key]
        rows, raw_path = parse_success_rows(A, root, manifest, receipt, wanted, canary=canary)
        linked_raw.add(raw_path)
        if canary:
            require(len(rows) == 1 and rows[0]["ts_code"] in wanted, "TAIL_CANARY_NOT_EXACT_ONE_ROW")
            seen_canaries.add(key)
            relative = f"canary/{api}.csv"
            verify_csv(A, root, manifest, relative, api, rows)
            used_csv.add(relative)
            continue
        if key != active:
            require(key not in groups, "TAIL_PAGINATION_NONCONTIGUOUS")
            groups[key] = {"rows": [], "seen": set(), "offset": 0, "queries": [], "terminated": False}
            active = key
        group = groups[key]
        require(not group["terminated"] and params["offset"] == group["offset"] and len(group["queries"]) < 4, "TAIL_PARTIAL_OR_CONTINUED_PAGINATION")
        codes = {r["ts_code"] for r in rows}
        require(not codes.intersection(group["seen"]), "TAIL_DUPLICATE_OR_IGNORED_OFFSET")
        group["seen"].update(codes)
        group["rows"].extend(rows)
        group["offset"] += len(rows)
        group["queries"].append(n)
        group["exhausted"] = not rows
        group["terminated"] = not rows or wanted.issubset(group["seen"])
    require(set(groups) == set(allowed) and all(g["terminated"] for g in groups.values()), "TAIL_GAPS_NOT_ALL_CLOSED")
    coverage = {(p["trade_date"], p["api"]): p for p in status["coverage"]}
    require(len(coverage) == len(status["coverage"]) == 19 and set(coverage) == set(allowed), "TAIL_COVERAGE_KEYS_CHANGED")
    tables, missing_rows, null_rows = {}, [], []
    for key, group in groups.items():
        date, api = key
        selected = sorted((r for r in group["rows"] if r["ts_code"] in allowed[key]), key=lambda r: r["ts_code"])
        absent = sorted(allowed[key] - {r["ts_code"] for r in selected})
        nulls = [{"ts_code": r["ts_code"], "fields": [f for f in A.FIELDS[api][2:] if r[f] is None]} for r in selected if any(r[f] is None for f in A.FIELDS[api][2:])]
        info = {"pages": len(group["queries"]), "queried_market_rows": len(group["rows"]), "selected_rows": len(selected), "missing_candidate_codes": absent, "candidate_scope_complete": not absent, "whole_market_exhaustion_observed": group["exhausted"], "pagination_termination": "EMPTY_PAGE_EXHAUSTED_WITH_MISSING_CANDIDATES" if absent else "ALL_REQUESTED_CANDIDATES_FOUND", "query_numbers": group["queries"], "incomplete_candidate_fields": nulls}
        require(coverage[key] == {"trade_date": date, "api": api, "requested_candidate_count": len(allowed[key]), "info": info}, "TAIL_COVERAGE_DOES_NOT_REPLAY_RAW")
        relative = f"candidate_sources/{date}/{api}.csv"
        verify_csv(A, root, manifest, relative, api, selected)
        used_csv.add(relative)
        tables[key], full_tables[key] = {r["ts_code"]: r for r in selected}, {r["ts_code"]: r for r in group["rows"]}
        missing_rows.extend({"trade_date": date, "api": api, "ts_code": code, "status": "UNKNOWN_NOT_ZERO"} for code in absent)
        null_rows.extend({"trade_date": date, "api": api, **r, "status": "EXPLICIT_JSON_NULL_UNKNOWN_NOT_ZERO"} for r in nulls)
    expected_files = {"PLAN.json", "REQUEST.json", "partition_plan.json", "status.json"} | expected_receipts | linked_raw | used_csv
    require(set(manifest["files"]) == expected_files, "TAIL_EXTRA_OR_UNLINKED_ARTIFACT_FILE")
    require(status.get("completed_partitions") == 19 and status.get("required_collection_complete") is True, "TAIL_COMPLETENESS_CLAIM_INVALID")
    require(status.get("required_candidate_coverage_complete") is (not missing_rows and not null_rows), "TAIL_UNKNOWN_COVERAGE_CLAIM_INVALID")
    require(status["status"] == ("COLLECTED_TAIL_REQUIRED_PARTITIONS_WITH_GAPS" if missing_rows or null_rows else "COLLECTED_TAIL_REQUIRED_PARTITIONS"), "TAIL_FINAL_STATUS_NOT_TRUTHFUL")
    require(status.get("bulk_requests") == count - 2 and status.get("canary_requests") == 2, "TAIL_BULK_CANARY_COUNTS_INVALID")
    return manifest, tables, full_tables, missing_rows, null_rows


def compose_partition_sources(expected, original_keys, tail_keys):
    require(not set(original_keys).intersection(tail_keys), "CROSS_RUN_OVERWRITE_OR_DUPLICATE_PARTITION")
    require(set(original_keys) | set(tail_keys) == set(expected), "COMPOSITE_SOURCE_NOT_EXACT_COMPLETE_SCOPE")
    return {key: ("original" if key in original_keys else "tail") for key in sorted(expected)}


def audit_closure(repo, plan, original_root, original_audit, original_identity, original_audit_sha, tail_root, tail_identity):
    spec = plan["composite_source"]
    external_identity(spec["original"], original_identity)
    external_identity(spec["tail"], tail_identity)  # Pending bindings fail before a label or fit can start.
    A = load_module(repo, spec["audit_module"], "dc20_closure_fixed_source_auditor")
    stored, contract = validated_original(A, repo, original_root, original_audit, plan, original_identity, original_audit_sha)
    original_manifest = A.decode(pinned(original_root, "artifact_manifest.json", original_identity["manifest_sha256"]).read_bytes())
    T = load_module(repo, {"path": spec["tail"]["package"] + "/collect_tail.py", "sha256": spec["tail"]["collector_sha256"]}, "dc20_closure_fixed_tail_collector")
    tail_manifest, tail_tables, tail_full, missing, nulls = verify_tail(A, T, repo, tail_root, plan, tail_identity, contract["request"])
    expected_codes = {s["trade_date"]: set(s["codes"]) for s in contract["request"]["sessions"]}
    expected_keys = {(d, api) for d in expected_codes for api in ("daily", "stk_limit")}
    old_keys = set()
    for relative in original_manifest["files"]:
        match = re.fullmatch(r"candidate_sources/(20\d{6})/(daily|stk_limit)\.csv", relative)
        if match:
            old_keys.add(match.groups())
    require(len(old_keys) == 1833 and set(tail_tables) == {(p["trade_date"], p["api"]) for p in spec["missing_partitions"]}, "CLOSURE_PARTITION_COUNTS_CHANGED")
    sources = compose_partition_sources(expected_keys, old_keys, tail_tables)
    # Recheck all original 29-day reference overlap on the combined partition
    # map. The old run's audit itself is unchanged; this is a new comparison.
    overlap_dates = set(stored["overlap"]["days"])
    tables, full = dict(tail_tables), dict(tail_full)
    for relative in sorted(original_manifest["files"]):
        if not relative.startswith("receipts/"):
            continue
        receipt = A.decode(file(original_root, relative).read_bytes())
        params = receipt.get("params", {})
        key = params.get("trade_date"), receipt.get("api_name")
        if key[0] not in overlap_dates or key not in old_keys or "ts_code" in params or receipt.get("status") != "SUCCESS":
            continue
        rows, _ = parse_success_rows(A, original_root, original_manifest, receipt, expected_codes[key[0]], canary=False)
        full.setdefault(key, {}).update((r["ts_code"], r) for r in rows)
    for key in old_keys:
        if key[0] in overlap_dates:
            _, rows = A.csv_rows(file(original_root, f"candidate_sources/{key[0]}/{key[1]}.csv").read_bytes())
            tables[key] = {r["ts_code"]: r for r in rows}
    overlap = A.compare_overlap(repo, expected_codes, tables, full, 29)
    require(not overlap["conflicts"] and not overlap["unverified"], "COMPOSITE_OVERLAP_REQUIRES_REVIEW_NO_CHERRY_PICKING")
    partition_map = []
    for (date, api), origin in sources.items():
        manifest = original_manifest if origin == "original" else tail_manifest
        relative = f"candidate_sources/{date}/{api}.csv"
        partition_map.append({"trade_date": date, "api": api, "source_execution": origin, "artifact_relative_path": relative, **manifest["files"][relative]})
    report = {"schema_version": "dc20_two_run_source_closure_audit_v1", "status": "COMPOSITE_SOURCE_VERIFIED_FOR_SEPARATE_LABEL_REBUILD", "source_ready_for_label_rebuild": True, "single_successful_run_claim": False, "training_authorized": False, "production_release_authorized": False, "actual_fill_observed": False, "new_forward_evidence": False, "old_failure_status_preserved": True, "original_status": stored["collection_status"], "original_audit_issues": stored["issues"], "original_audit_sha256": original_audit_sha, "original_execution": original_identity, "tail_execution": tail_identity, "total_required_partitions": len(partition_map), "original_verified_partitions": 1833, "tail_verified_partitions": 19, "source_partition_map": partition_map, "source_partition_map_sha256": sha(canonical(partition_map)), "full_session_plan_sha256": spec["full_session_plan_sha256"], "missing_candidate_keys": stored["missing_candidate_keys"] + missing, "incomplete_candidate_fields": stored["incomplete_candidate_fields"] + nulls, "overlap": overlap, "as_of_date": "20260904", "tail_window_is_forced_exit": False, "missingness_policy": "unknown_not_zero_not_backfilled_from_older_market_revision", "issues": []}
    return report
