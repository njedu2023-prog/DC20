#!/usr/bin/env python3
"""Offline audit of the adjacent collector's v1 artifact. Never labels or trains.

Use physical absolute paths (no symlinks). External expected run/SHA/manifest
digest must come from the separately inspected GitHub run and artifact download;
an artifact cannot authenticate its own origin. Only HERE/outputs/audit.json is
written. This is retrospective source evidence, not historical availability.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SOURCE_PACKAGE = "work/executable-profit-history-source-20260906"
ASOF, TAIL, PAGE, MAX_PAGES, MAX_REQUESTS = "20260904", 20, 5000, 4, 6500
INPUTS = {
    "ledger": {"path": "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz", "sha256": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"},
    "calendar": {"path": "data/market/trade_cal_sse.csv", "sha256": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"},
}
FIELDS = {
    "daily": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
    "stk_limit": ["ts_code", "trade_date", "pre_close", "up_limit", "down_limit"],
    "adj_factor": ["ts_code", "trade_date", "adj_factor"],
}
COMPARE = {"daily": ("open", "high", "low", "close", "pre_close", "vol"), "stk_limit": ("up_limit", "down_limit")}
HEX = re.compile(r"[0-9a-f]{64}")
CODE = re.compile(r"\d{6}\.(?:SH|SZ|BJ|OF)")
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RAW_BYTES = 16 * 1024 * 1024
# Exact independently inspected failed run only. It may be diagnosed after the
# collector changes, but may never supply a successful source-admission result.
LEGACY_FAILED = {
    "6b6dc4651a73eaeb44e76953266b11a8d2fc71a7a6d38c16eee43cc7b2b523a0": {
        "collector_sha256": "1be2a6c9bf1dfa8848e590ce16bef338a8a91c4d8a1db62c5ac3cf8c6e6c91b8",
        "plan_sha256": "e150bdae3d664cdfaea89ef3ada6603db4d1fe1e1c0e70b57e29287592872cdd",
        "request_sha256": "8267f229fdf7c62278526170fcb277e55ccc7467d84a097e867a5b4a82318609",
        "github_run_id": "34022598288", "github_sha": "8c12086f24c92463d569861d5637d5de1f55805b", "github_run_attempt": "1",
    }
}


class AuditError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise AuditError(code)


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def no_symlinks(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_PATH_FORBIDDEN")
    return path


def safe_file(root, relative):
    require(isinstance(relative, str) and "\\" not in relative, "UNSAFE_RELATIVE_PATH")
    p = Path(relative)
    require(relative == p.as_posix() and not p.is_absolute() and not {"", ".", ".."}.intersection(p.parts), "UNSAFE_RELATIVE_PATH")
    root = no_symlinks(root)
    target = no_symlinks(root / p)
    require(target.is_file() and stat.S_ISREG(target.stat().st_mode), "INPUT_FILE_MISSING_OR_NONREGULAR")
    require(target.stat().st_size <= MAX_FILE_BYTES, "INPUT_FILE_TOO_LARGE")
    return target


def read(root, relative):
    return safe_file(root, relative).read_bytes()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(payload, *, decimal=False):
    try:
        return json.loads(payload, object_pairs_hook=pairs,
                          parse_float=Decimal if decimal else float,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError("NONFINITE_JSON")))
    except (ValueError, UnicodeError) as error:
        raise AuditError("INVALID_JSON") from error


def csv_rows(payload):
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
        fields = reader.fieldnames
        require(fields and len(fields) == len(set(fields)), "INVALID_CSV_HEADER")
        rows = list(reader)
        require(all(None not in row and all(value is not None for value in row.values()) for row in rows), "INVALID_CSV_WIDTH")
        return fields, rows
    except UnicodeError as error:
        raise AuditError("INVALID_CSV_ENCODING") from error


def number(value):
    require(not isinstance(value, bool) and value is not None and value != "", "MISSING_NUMERIC_VALUE")
    try:
        result = Decimal(str(value))
        require(result.is_finite(), "NONFINITE_NUMERIC_VALUE")
        return result
    except InvalidOperation as error:
        raise AuditError("INVALID_NUMERIC_VALUE") from error


def normalized(field, value):
    value = number(value)
    try:
        return value if field == "vol" else value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise AuditError("PRICE_NOT_NORMALIZABLE_TO_CENT_TICK") from error


def timestamp(value):
    require(isinstance(value, str), "TIMESTAMP_MISSING")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "TIMESTAMP_NOT_TIMEZONE_AWARE")
        return parsed
    except ValueError as error:
        raise AuditError("TIMESTAMP_INVALID") from error


def load_contract(repo):
    """Independently reconstruct the frozen window; never trust artifact dates."""
    repo = no_symlinks(repo)
    plan_bytes = read(repo, SOURCE_PACKAGE + "/PLAN.json")
    request_bytes = read(repo, SOURCE_PACKAGE + "/REQUEST.json")
    collector = read(repo, SOURCE_PACKAGE + "/collect.py")
    plan, request = decode(plan_bytes), decode(request_bytes)
    require(plan.get("schema_version") == "dc20_profit_history_source_v1", "PLAN_SCHEMA_MISMATCH")
    require(plan.get("source_inputs") == INPUTS, "PLAN_INPUT_PINS_MISMATCH")
    for key, value in {"as_of_date": ASOF, "tail_sessions": TAIL, "page_size": PAGE,
                       "max_pages": MAX_PAGES, "max_requests": MAX_REQUESTS,
                       "daily_mode": "full_market_paged", "optional_adj_factor": True}.items():
        require(plan.get(key) == value, "PLAN_CONTRACT_MISMATCH")
    sources = {key: read(repo, spec["path"]) for key, spec in INPUTS.items()}
    require(all(sha(sources[key]) == spec["sha256"] for key, spec in INPUTS.items()), "SOURCE_PIN_MISMATCH")
    _, calendar = csv_rows(sources["calendar"])
    sessions = [row["cal_date"] for row in calendar if row["exchange"] == "SSE" and row["is_open"] == "1"]
    require(sessions == sorted(set(sessions)) and ASOF in sessions, "STRICT_SSE_CALENDAR_INVALID")
    positions = {date: i for i, date in enumerate(sessions)}
    _, ledger = csv_rows(gzip.decompress(sources["ledger"]))
    requested, seen, canaries = {}, set(), []
    for row in ledger:
        d, t, t1, code = (row[key] for key in ("signal_date", "exec_date", "scheduled_exit_date", "ts_code"))
        require(re.fullmatch(r"\d{6}\.(?:SH|SZ)", code) is not None, "CANDIDATE_CODE_INVALID")
        require((d, code) not in seen, "FROZEN_CANDIDATE_DUPLICATED")
        seen.add((d, code))
        require(d in positions and positions[d] + 2 < len(sessions), "D_OUTSIDE_CALENDAR")
        i = positions[d]
        require((t, t1) == tuple(sessions[i + 1:i + 3]) and t1 <= ASOF, "D_T_T1_NOT_ADJACENT_SSE")
        require(row["stage"] in ("2", "3") and 1 <= int(row["promotion_rank"]) <= 10, "FROZEN_STAGE_RANK_INVALID")
        canaries.append((t, int(row["promotion_rank"]), code))
        for date in sessions[i + 1:min(i + 2 + TAIL, positions[ASOF]) + 1]:
            requested.setdefault(date, set()).add(code)
    require((len(ledger), len({r["signal_date"] for r in ledger}), len({r["ts_code"] for r in ledger})) == (6753, 910, 1959), "FIXED_LEDGER_COUNTS_MISMATCH")
    require(len(requested) == 926 and sum(map(len, requested.values())) == 102935, "FIXED_WINDOW_COUNTS_MISMATCH")
    first_t, _, first_code = min(canaries)
    expected = {"as_of_date": ASOF, "tail_sessions": TAIL,
                "canary": {"trade_date": first_t, "ts_code": first_code},
                "sessions": [{"trade_date": date, "codes": sorted(codes)} for date, codes in sorted(requested.items())],
                "source_inputs": INPUTS}
    compact = {k: v for k, v in expected.items() if k != "sessions"}
    compact.update(session_plan_sha256=sha(canonical(expected["sessions"])), session_count=len(requested),
                   candidate_date_keys=sum(map(len, requested.values())))
    require(compact["session_plan_sha256"] == "526c0d07f3a31cb9e8eee864abe6a47321e641c80d716a8e0c90e84201ef0b6e", "FIXED_SESSION_PLAN_HASH_MISMATCH")
    require({k: v for k, v in request.items() if k != "_activation"} == compact, "FIXED_REQUEST_MISMATCH")
    return {"request": expected, "plan_sha256": sha(plan_bytes), "request_sha256": sha(request_bytes),
            "collector_sha256": sha(collector), "source_inputs": INPUTS,
            "source_repository": plan["source_repository"], "source_commit": plan["source_commit"],
            "expected_overlap_days": 29}


def audit_bundle(root, repo, contract, *, expected_run_id, expected_run_sha, expected_manifest_sha256, expected_run_attempt="1"):
    """Read-only core; callers construct contract from trusted local source pins."""
    issues, missing_keys, incomplete_fields, complete, tables, full_tables = [], [], [], {}, {}, {}
    report = {"schema_version": "dc20_profit_history_artifact_audit_v1", "source_ready_for_label_rebuild": False,
              "training_authorized": False, "production_release_authorized": False,
              "actual_fill_observed": False, "historically_available_at_D": False,
              "evidence_role": "retrieved_now_retrospective_market_evidence_not_historical_availability_or_forward_results",
              "missingness_policy": "unknown_not_zero_not_presumed_suspension; later strict complete-D label mask applies",
              "as_of_date": ASOF, "tail_sessions": TAIL, "tail_window_is_forced_exit": False,
              "source_repository": contract["source_repository"], "source_commit": contract["source_commit"],
              "expected_run_id": expected_run_id, "expected_run_sha": expected_run_sha,
              "expected_run_attempt": expected_run_attempt,
              "expected_manifest_sha256": expected_manifest_sha256,
              "audit_code_sha256": sha(Path(__file__).read_bytes()),
              "source_contract": {k: contract[k] for k in ("plan_sha256", "request_sha256", "collector_sha256", "source_inputs")},
              "session_plan_sha256": sha(canonical(contract["request"]["sessions"])),
              "issues": issues, "missing_candidate_keys": missing_keys, "incomplete_candidate_fields": incomplete_fields}

    def issue(code, **context):
        issues.append({"code": code, **context})

    try:
        require(re.fullmatch(r"\d{1,24}", expected_run_id or "") is not None, "EXTERNAL_RUN_ID_REQUIRED")
        require(re.fullmatch(r"[0-9a-f]{40}", expected_run_sha or "") is not None, "EXTERNAL_RUN_SHA_REQUIRED")
        require(re.fullmatch(r"\d{1,8}", expected_run_attempt or "") is not None, "EXTERNAL_RUN_ATTEMPT_REQUIRED")
        require(HEX.fullmatch(expected_manifest_sha256 or "") is not None, "EXTERNAL_MANIFEST_DIGEST_REQUIRED")
        root, repo = no_symlinks(root), no_symlinks(repo)
        manifest_bytes = read(root, "artifact_manifest.json")
        require(sha(manifest_bytes) == expected_manifest_sha256, "EXTERNAL_MANIFEST_HASH_MISMATCH")
        report["artifact_manifest_sha256"] = sha(manifest_bytes)
        manifest = decode(manifest_bytes)
        require(manifest.get("schema_version") == "dc20_isolated_source_artifact_manifest_v1", "MANIFEST_SCHEMA_MISMATCH")
        require(manifest.get("production_writes") is False and manifest.get("source_data_only") is True, "MANIFEST_ISOLATION_MISMATCH")
        identity = {"github_run_id": expected_run_id, "github_sha": expected_run_sha, "github_run_attempt": expected_run_attempt}
        require(all(manifest.get(k) == v for k, v in identity.items()), "MANIFEST_EXTERNAL_IDENTITY_MISMATCH")
        files = manifest.get("files")
        require(isinstance(files, dict) and "artifact_manifest.json" not in files, "MANIFEST_FILE_MAP_INVALID")
        payloads = set()
        for relative, metadata in files.items():
            try:
                payload = read(root, relative)
                require(isinstance(metadata, dict) and metadata.get("sha256") == sha(payload) and metadata.get("bytes") == len(payload), "MANIFEST_FILE_HASH_OR_SIZE_MISMATCH")
                payloads.add(relative)
            except (AuditError, OSError) as error:
                issue(str(error) if isinstance(error, AuditError) else "ARTIFACT_READ_FAILED", path=relative)
        actual_files = set()
        for path in root.rglob("*"):
            no_symlinks(path)
            if path.is_file():
                actual_files.add(path.relative_to(root).as_posix())
        require(actual_files == set(files) | {"artifact_manifest.json"}, "UNMANIFESTED_OR_MISSING_ARTIFACT_FILES")
        report["manifest_file_count"], report["verified_manifest_file_count"] = len(files), len(payloads)
        def payload_for(relative):
            require(relative in payloads, "VERIFIED_ARTIFACT_FILE_REQUIRED")
            value = read(root, relative)
            require(sha(value) == files[relative]["sha256"] and len(value) == files[relative]["bytes"], "ARTIFACT_CHANGED_DURING_AUDIT")
            return value

        require("status.json" in payloads, "VALID_STATUS_MISSING")
        status = decode(payload_for("status.json"))
        require(status.get("schema_version") == "dc20_profit_history_collection_v1", "STATUS_SCHEMA_MISMATCH")
        require(status.get("status") == manifest.get("status"), "MANIFEST_STATUS_MISMATCH")
        require(status.get("as_of_date") == ASOF and status.get("tail_sessions") == TAIL, "STATUS_WINDOW_MISMATCH")
        for key, value in {"actual_fill_claim": False, "production_data_writes": False, "models_trained": 0,
                           "source_files_are_new_labels": False, "corporate_actions_resolved": False,
                           "tail_window_is_forced_exit": False}.items():
            require(status.get(key) == value, "STATUS_RESEARCH_BOUNDARY_MISMATCH")
        provenance = status.get("provenance", {})
        require(provenance == manifest.get("provenance"), "MANIFEST_STATUS_PROVENANCE_MISMATCH")
        artifact_contract = contract
        legacy = LEGACY_FAILED.get(expected_manifest_sha256)
        if legacy:
            require(status.get("status") == "BLOCKED_COLLECTION" and all(provenance.get(k) == v for k, v in legacy.items()), "LEGACY_FAILED_IDENTITY_MISMATCH")
            artifact_contract = {**contract, **{key: legacy[key] for key in ("collector_sha256", "plan_sha256", "request_sha256")}}
            report["audit_reference_contract"] = report["source_contract"]
            report["source_contract"] = {k: artifact_contract[k] for k in ("plan_sha256", "request_sha256", "collector_sha256", "source_inputs")}
            report["legacy_failed_artifact_diagnostic_only"] = True
            issue("LEGACY_FAILED_ARTIFACT_DIAGNOSTIC_ONLY_NO_TRAINING_SOURCE")
        for key in ("plan_sha256", "request_sha256", "collector_sha256", "source_inputs"):
            require(provenance.get(key) == artifact_contract[key], "SOURCE_PROVENANCE_MISMATCH")
        require(all(provenance.get(k) == v for k, v in identity.items()), "PROVENANCE_EXTERNAL_IDENTITY_MISMATCH")
        require(provenance.get("source_repository") == contract["source_repository"] and provenance.get("source_commit") == contract["source_commit"], "SOURCE_REPOSITORY_COMMIT_MISMATCH")
        require(provenance.get("ledger_candidate_universe_unchanged") is True and provenance.get("market_endpoint") == "https://api.tushare.pro/", "SOURCE_IDENTITY_MISMATCH")
        started, completed = timestamp(status.get("started_utc")), timestamp(status.get("completed_utc"))
        require(started <= completed, "COLLECTION_TIMESTAMP_ORDER_INVALID")
        report["retrieved_started_utc"], report["retrieved_completed_utc"] = status["started_utc"], status["completed_utc"]
        request = contract["request"]
        require("PLAN.json" in payloads and sha(payload_for("PLAN.json")) == artifact_contract["plan_sha256"], "ARTIFACT_PLAN_MISMATCH")
        require("REQUEST.json" in payloads and sha(payload_for("REQUEST.json")) == artifact_contract["request_sha256"], "ARTIFACT_REQUEST_MISMATCH")
        require("session_plan.json" in payloads and decode(payload_for("session_plan.json")) == request["sessions"], "ARTIFACT_SESSION_PLAN_MISMATCH")
        require(decode(payload_for("REQUEST.json")).get("session_plan_sha256") == sha(canonical(request["sessions"])), "SESSION_PLAN_CANONICAL_HASH_MISMATCH")
        expected = {row["trade_date"]: set(row["codes"]) for row in request["sessions"]}
        report["requested_sessions"] = len(expected)
        report["requested_code_date_keys"] = sum(map(len, expected.values()))
        report["requested_first_date"], report["requested_last_date"] = min(expected), max(expected)
        count = manifest.get("requests_attempted")
        require(type(count) is int and 1 <= count <= MAX_REQUESTS and status.get("request_count") == count, "REQUEST_COUNT_INVALID")
        receipt_paths = sorted(p for p in files if p.startswith("receipts/"))
        require(receipt_paths == [f"receipts/{i:06d}.json" for i in range(1, count + 1)], "RECEIPT_SEQUENCE_INCOMPLETE")
        groups, canaries, linked_raw, used_csv = {}, {}, set(), set()
        overlap_dates = {p.parent.name for p in (repo / "data/market/raw").glob("20*/20*/daily.csv") if p.parent.name in expected}
        active_group = None
        prior_started = started
        for relative in receipt_paths:
            if relative not in payloads:
                continue
            try:
                receipt = decode(payload_for(relative))
                n = int(Path(relative).stem)
                require(receipt.get("query_number") == n, "QUERY_NUMBER_MISMATCH")
                require(all(receipt.get(k) == v for k, v in identity.items()), "EXTERNAL_RUN_IDENTITY_MISMATCH")
                require(receipt.get("credential_persisted") is False and receipt.get("upstream_message_persisted") is False, "RECEIPT_PRIVACY_CONTRACT_MISMATCH")
                begin, end = timestamp(receipt.get("started_utc")), timestamp(receipt.get("completed_utc"))
                require(prior_started <= begin <= end <= completed, "RECEIPT_TIMESTAMP_ORDER_INVALID")
                prior_started = begin
                api, params = receipt.get("api_name"), receipt.get("params")
                require(api in FIELDS and receipt.get("fields") == FIELDS[api] and isinstance(params, dict), "RECEIPT_API_OR_FIELDS_INVALID")
                date = params.get("trade_date")
                is_canary = "ts_code" in params
                require(date in expected and date <= ASOF, "RECEIPT_DATE_OUT_OF_SCOPE")
                require(set(params) == ({"trade_date", "limit", "offset", "ts_code"} if is_canary else {"trade_date", "limit", "offset"}), "RECEIPT_PARAMS_INVALID")
                require(params["limit"] == PAGE and type(params["offset"]) is int and 0 <= params["offset"] < PAGE * MAX_PAGES, "RECEIPT_PAGE_CONTRACT_INVALID")
                credential_free_request = {"api_name": api, "params": params, "fields": ",".join(FIELDS[api])}
                require(receipt.get("request_without_credential_sha256") == sha(canonical(credential_free_request)), "CREDENTIAL_FREE_REQUEST_HASH_MISMATCH")
                if is_canary:
                    require({k: params[k] for k in ("trade_date", "ts_code")} == request["canary"] and params["offset"] == 0, "CANARY_SCOPE_MISMATCH")
                    wanted = {request["canary"]["ts_code"]}
                else:
                    wanted = expected[date]
                if receipt.get("status") == "FAILED":
                    require(receipt.get("raw_response_artifact") is None and isinstance(receipt.get("failure_code"), str), "FAILED_RECEIPT_INVALID")
                    if api != "adj_factor":
                        issue("REQUIRED_QUERY_FAILED", path=relative, api=api, trade_date=date, failure_code=receipt["failure_code"])
                    continue
                require(receipt.get("status") == "SUCCESS", "RECEIPT_STATUS_INVALID")
                raw_path = f"responses/{n:06d}.json.gz"
                require(receipt.get("raw_response_artifact") == raw_path and raw_path in payloads, "RAW_RESPONSE_BINDING_MISSING")
                with gzip.GzipFile(fileobj=io.BytesIO(payload_for(raw_path))) as handle:
                    raw = handle.read(MAX_RAW_BYTES + 1)
                require(len(raw) <= MAX_RAW_BYTES and receipt.get("response_sha256") == sha(raw) and receipt.get("response_bytes") == len(raw), "RAW_RESPONSE_HASH_OR_SIZE_MISMATCH")
                linked_raw.add(raw_path)
                obj = decode(raw, decimal=True)
                require(type(obj.get("code")) is int and obj["code"] == 0 and obj.get("msg") in (None, ""), "RAW_SUCCESS_RESPONSE_INVALID")
                data = obj.get("data", {})
                fields, items = data.get("fields"), data.get("items")
                require(isinstance(fields, list) and len(fields) == len(set(fields)) and set(fields) == set(FIELDS[api]), "RAW_FIELDS_MISMATCH")
                require(isinstance(items, list) and len(items) <= PAGE, "RAW_PAGE_SIZE_INVALID")
                rows = []
                for values in items:
                    require(isinstance(values, list) and len(values) == len(fields), "RAW_ROW_WIDTH_MISMATCH")
                    row = dict(zip(fields, values))
                    require(isinstance(row["ts_code"], str) and CODE.fullmatch(row["ts_code"]) is not None and row["trade_date"] == date, "RAW_ROW_DATE_OR_CODE_INVALID")
                    if row["ts_code"] in wanted:
                        for field in FIELDS[api][2:]:
                            if row[field] is None and not is_canary and not legacy:
                                continue
                            require(isinstance(row[field], (int, Decimal)) and not isinstance(row[field], bool), "RAW_CANDIDATE_NOT_JSON_NUMERIC")
                            number(row[field])
                        if api == "daily":
                            require(all(row[k] is None or number(row[k]) > 0 for k in ("open", "high", "low", "close", "pre_close")), "RAW_CANDIDATE_PRICE_NONPOSITIVE")
                            require(all(row[k] is None or number(row[k]) >= 0 for k in ("vol", "amount")), "RAW_CANDIDATE_VOLUME_NEGATIVE")
                            for upper, lower in (("high", "open"), ("high", "close"), ("high", "low"), ("open", "low"), ("close", "low")):
                                if row[upper] is not None and row[lower] is not None:
                                    require(row[upper] + Decimal("0.011") >= row[lower], "RAW_CANDIDATE_OHLC_INCONSISTENT")
                        elif api == "stk_limit":
                            require(all(row[k] is None or row[k] > 0 for k in ("pre_close", "up_limit", "down_limit")), "RAW_CANDIDATE_LIMIT_INVALID")
                            if row["up_limit"] is not None and row["down_limit"] is not None:
                                require(row["up_limit"] >= row["down_limit"], "RAW_CANDIDATE_LIMIT_INVALID")
                        else:
                            require(row["adj_factor"] is None or row["adj_factor"] > 0, "RAW_CANDIDATE_ADJ_FACTOR_INVALID")
                    rows.append(row)
                require(len({r["ts_code"] for r in rows}) == len(rows), "RAW_DUPLICATE_CODE")
                require(receipt.get("row_count") == len(rows) and receipt.get("selected_row_count") == sum(r["ts_code"] in wanted for r in rows), "RECEIPT_ROW_COUNTS_MISMATCH")
                if is_canary:
                    require(api not in canaries and len(rows) == 1 and rows[0]["ts_code"] in wanted, "CANARY_RESULT_INVALID")
                    canaries[api] = rows
                else:
                    key = (date, api)
                    if active_group != key:
                        if active_group in groups:
                            groups[active_group].pop("seen", None)
                        require(key not in groups, "NONCONTIGUOUS_PAGINATION_GROUP")
                        groups[key] = {"pages": [], "selected": [], "seen": set(), "terminated": False, "next_offset": 0}
                        active_group = key
                    group = groups[key]
                    require(not group["terminated"], "PAGINATION_CONTINUED_AFTER_TERMINATION")
                    require(params["offset"] == group["next_offset"], "PAGINATION_OFFSETS_INCOMPLETE")
                    codes = {r["ts_code"] for r in rows}
                    require(not group["seen"].intersection(codes), "PAGINATION_DUPLICATES_OR_IGNORED_OFFSET")
                    group["seen"].update(codes)
                    group["selected"].extend(r for r in rows if r["ts_code"] in wanted)
                    group["pages"].append({"offset": params["offset"], "rows": len(rows), "query_number": n})
                    group["next_offset"] += len(rows)
                    group["terminated"] = not rows or wanted.issubset(group["seen"])
                    if date in overlap_dates and api in COMPARE:
                        full_tables.setdefault(key, {}).update((r["ts_code"], r) for r in rows)
            except (AuditError, OSError, EOFError, TypeError, KeyError, AttributeError) as error:
                issue(str(error) if isinstance(error, AuditError) else "RECEIPT_OR_RAW_PARSE_FAILED", path=relative)
        for path in sorted({p for p in files if p.startswith("responses/")} - linked_raw):
            issue("UNLINKED_OR_INVALID_RAW_RESPONSE", path=path)
        if active_group in groups:
            groups[active_group].pop("seen", None)

        def verify_csv(path, api, selected):
            require(path in payloads, "CANDIDATE_CSV_MISSING_OR_INVALID")
            header, rows = csv_rows(payload_for(path))
            require(header == FIELDS[api] and len(rows) == len(selected), "CSV_SCHEMA_OR_ROW_COUNT_MISMATCH")
            for saved, source in zip(rows, selected):
                require(all(saved[k] == source[k] for k in ("ts_code", "trade_date")), "CSV_KEYS_OR_ORDER_MISMATCH")
                for field in FIELDS[api][2:]:
                    if source[field] is None:
                        require(saved[field] == "", "CSV_NULL_NOT_PRESERVED_AS_EMPTY_FIELD")
                    else:
                        require(number(saved[field]) == number(source[field]), "CSV_DIFFERS_FROM_RAW_RESPONSE")
            used_csv.add(path)

        for api in FIELDS:
            if api in canaries:
                try:
                    verify_csv("canary/" + api + ".csv", api, canaries[api])
                except AuditError as error:
                    issue(str(error), api=api, phase="canary_csv")
            elif api != "adj_factor":
                issue("REQUIRED_CANARY_NOT_VERIFIED", api=api)
        optional_partial = []
        for (date, api), group in groups.items():
            try:
                pages = group["pages"]
                require(0 < len(pages) <= MAX_PAGES, "PAGINATION_PAGE_CAP_EXCEEDED")
                selected = sorted(group["selected"], key=lambda row: row["ts_code"])
                exhausted = pages[-1]["rows"] == 0
                candidate_complete = expected[date] == {r["ts_code"] for r in selected}
                if not (exhausted or candidate_complete) and api == "adj_factor" and status.get("optional_adj_factor") in ("OPTIONAL_PARTIAL_BUDGET", "OPTIONAL_PARTIAL_THEN_UNAVAILABLE"):
                    optional_partial.append({"trade_date": date, "api": api, "reason": "OPTIONAL_PAGINATION_UNFINISHED", "query_numbers": [p["query_number"] for p in pages]})
                    continue
                require(exhausted or candidate_complete, "PAGINATION_COMPLETENESS_UNPROVEN")
                verify_csv(f"candidate_sources/{date}/{api}.csv", api, selected)
                absent = sorted(expected[date] - {r["ts_code"] for r in selected})
                complete[(date, api)] = {"pages": len(pages), "queried_market_rows": sum(p["rows"] for p in pages), "selected_rows": len(selected),
                                        "missing_candidate_codes": absent, "candidate_scope_complete": candidate_complete,
                                        "whole_market_exhaustion_observed": exhausted,
                                        "pagination_termination": "ALL_REQUESTED_CANDIDATES_FOUND" if candidate_complete else "EMPTY_PAGE_EXHAUSTED_WITH_MISSING_CANDIDATES",
                                        "query_numbers": [p["query_number"] for p in pages]}
                null_fields = [{"ts_code": row["ts_code"], "fields": [k for k in FIELDS[api][2:] if row[k] is None]}
                               for row in selected if any(row[k] is None for k in FIELDS[api][2:])]
                if not legacy:
                    complete[(date, api)]["incomplete_candidate_fields"] = null_fields
                incomplete_fields.extend({"trade_date": date, "api": api, **item, "status": "EXPLICIT_JSON_NULL_UNKNOWN_NOT_ZERO"} for item in null_fields)
                tables[(date, api)] = {r["ts_code"]: r for r in selected}
                missing_keys.extend({"trade_date": date, "ts_code": code, "api": api, "status": "UNKNOWN_NOT_ZERO"} for code in absent)
            except AuditError as error:
                issue(str(error), api=api, trade_date=date)
        for path in files:
            if path.endswith(".csv") and path not in used_csv:
                issue("UNVERIFIED_OR_UNLINKED_CSV", path=path)
            elif not (path in ("status.json", "PLAN.json", "REQUEST.json", "session_plan.json") or path.startswith("receipts/") or path.startswith("responses/") or path.endswith(".csv")):
                issue("UNEXPECTED_MANIFEST_FILE", path=path)
        coverage = status.get("coverage")
        require(isinstance(coverage, list) and status.get("sessions_completed") == len(coverage), "STATUS_COVERAGE_COUNT_MISMATCH")
        require([r.get("trade_date") for r in coverage] == list(expected)[:len(coverage)], "STATUS_COVERAGE_DATE_SEQUENCE_INVALID")
        for row in coverage:
            date = row["trade_date"]
            try:
                require(row.get("requested_candidate_count") == len(expected[date]) and isinstance(row.get("apis"), dict), "STATUS_COVERAGE_TARGET_MISMATCH")
                verified_apis = {api: complete[(date, api)] for api in FIELDS if (date, api) in complete}
                require(row["apis"] == verified_apis, "STATUS_COVERAGE_RAW_MISMATCH")
                require(all(api in row["apis"] for api in COMPARE), "STATUS_REQUIRED_API_COVERAGE_MISSING")
            except AuditError as error:
                issue(str(error), trade_date=date)
        missing_partitions = [{"trade_date": date, "api": api} for date in expected for api in COMPARE if (date, api) not in complete]
        report["missing_required_partitions"] = missing_partitions
        report["verified_required_partitions"] = sum(api in COMPARE for _, api in complete)
        report["expected_required_partitions"] = len(expected) * len(COMPARE)
        report["missing_required_candidate_key_count"] = sum(key["api"] in COMPARE for key in missing_keys)
        report["incomplete_required_candidate_row_count"] = sum(item["api"] in COMPARE for item in incomplete_fields)
        report["incomplete_required_field_count"] = sum(len(item["fields"]) for item in incomplete_fields if item["api"] in COMPARE)
        report["optional_adj_factor"] = status.get("optional_adj_factor")
        report["incomplete_optional_partitions"] = optional_partial
        report["verified_optional_partitions"] = sum(api == "adj_factor" for _, api in complete)
        require(status.get("optional_sessions_completed") == report["verified_optional_partitions"], "OPTIONAL_PARTITION_COUNT_MISMATCH")
        if missing_partitions or status.get("required_collection_complete") is not True:
            issue("REQUIRED_SOURCE_COLLECTION_INCOMPLETE")
        if status.get("status") == "BLOCKED_COLLECTION":
            issue("COLLECTOR_REPORTED_BLOCK", failure_code=status.get("failure_code"))
        elif status.get("required_collection_complete") is True:
            require(len(coverage) == len(expected) and not missing_partitions, "FALSE_COLLECTION_COMPLETENESS_CLAIM")
            no_missing = not (report["missing_required_candidate_key_count"] or report["incomplete_required_field_count"])
            require(status.get("required_candidate_coverage_complete") is no_missing, "FALSE_CANDIDATE_COVERAGE_CLAIM")
            require(status["status"] == ("COLLECTED_REQUIRED_SOURCES" if no_missing else "COLLECTED_REQUIRED_SOURCES_WITH_GAPS"), "COMPLETION_STATUS_INCONSISTENT")
        report["overlap"] = compare_overlap(repo, expected, tables, full_tables, contract.get("expected_overlap_days", 29))
        if report["overlap"]["conflicts"] or report["overlap"]["unverified"]:
            issue("OVERLAP_REQUIRES_REVIEW_NO_CHERRY_PICKING")
        report["source_ready_for_label_rebuild"] = not issues
        report["collection_status"] = status["status"]
        report["requests_attempted"] = count
    except (AuditError, OSError, TypeError, KeyError, AttributeError, ValueError) as error:
        issue(str(error) if isinstance(error, AuditError) else "AUDIT_STRUCTURE_OR_IO_FAILURE")
    report["status"] = "SOURCE_VERIFIED_FOR_SEPARATE_LABEL_REBUILD" if report["source_ready_for_label_rebuild"] else "BLOCKED_SOURCE_ADMISSION"
    return report


def compare_overlap(repo, expected, tables, full_tables, expected_days):
    """Compare full-market shared SH/SZ rows, explicitly including requested rows.

    Old partitions are read-only. Numeric differences and availability changes
    are retained; nothing selects which revision is correct. Missing old fields
    cannot be certified equivalent. Candidate absence in both sources is unknown.
    """
    result = {"scope": "shared_queried_SH_SZ_market_rows_plus_requested_code_presence; not_whole_market_completeness",
              "price_normalization": "Decimal cent tick ROUND_HALF_UP", "volume_normalization": "exact Decimal",
              "reference_files": [], "days": [], "compared_field_values": 0,
              "candidate_compared_field_values": 0, "conflicts": [], "unverified": [], "incomplete_fields": []}
    raw_root = no_symlinks(repo / "data/market/raw")
    days = sorted({p.parent.name for p in raw_root.glob("20*/20*/daily.csv") if p.parent.name in expected})
    result["days"] = days
    if len(days) != expected_days:
        result["unverified"].append({"reason": "REFERENCE_OVERLAP_DAY_COUNT_MISMATCH", "expected": expected_days, "actual": len(days)})
    for date in days:
        for api, fields in COMPARE.items():
            relative = f"data/market/raw/{date[:4]}/{date}/{api}.csv"
            try:
                payload = read(repo, relative)
                header, rows = csv_rows(payload)
                result["reference_files"].append({"path": relative, "sha256": sha(payload), "bytes": len(payload)})
                require(all(field in header for field in ("trade_date", "ts_code", *fields)), "REFERENCE_FIELDS_MISSING")
                old = {}
                for row in rows:
                    require(row["trade_date"] == date and row["ts_code"] not in old, "REFERENCE_DATE_OR_DUPLICATE_INVALID")
                    old[row["ts_code"]] = row
                if (date, api) not in tables or (date, api) not in full_tables:
                    result["unverified"].append({"trade_date": date, "api": api, "reason": "NEW_COMPLETE_PARTITION_UNAVAILABLE"})
                    continue
                new = full_tables[(date, api)]
                shared = {code for code in old.keys() & new.keys() if re.fullmatch(r"\d{6}\.(?:SH|SZ)", code)}
                for code in sorted(expected[date]):
                    if (code in old) != (code in new):
                        result["conflicts"].append({"trade_date": date, "api": api, "ts_code": code, "field": "row_presence", "old": code in old, "new": code in new})
                for code in sorted(shared):
                    for field in fields:
                        if new[code].get(field) is None:
                            result["incomplete_fields"].append({"trade_date": date, "api": api, "ts_code": code, "field": field,
                                                                "reason": "NEW_EXPLICIT_JSON_NULL_UNKNOWN; old_value_not_substituted",
                                                                "old_field_present": old[code].get(field) not in (None, "")})
                            continue
                        try:
                            before, after = normalized(field, old[code].get(field)), normalized(field, new[code].get(field))
                        except AuditError as error:
                            result["unverified"].append({"trade_date": date, "api": api, "ts_code": code, "field": field, "reason": str(error)})
                            continue
                        result["compared_field_values"] += 1
                        result["candidate_compared_field_values"] += code in expected[date]
                        if before != after:
                            result["conflicts"].append({"trade_date": date, "api": api, "ts_code": code, "field": field, "old": str(before), "new": str(after), "requested_candidate": code in expected[date]})
            except (AuditError, OSError) as error:
                result["unverified"].append({"trade_date": date, "api": api, "reason": str(error) if isinstance(error, AuditError) else "REFERENCE_FILE_READ_FAILED"})
    return result


def write_report(report):
    """Exclusive fixed output; never follows or truncates existing links/files."""
    directory = no_symlinks(HERE / "outputs")
    directory.mkdir(mode=0o700, exist_ok=True)
    require(directory.is_dir(), "OUTPUT_DIRECTORY_INVALID")
    path = directory / "audit.json"
    no_symlinks(path)
    payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        require(os.fstat(handle.fileno()).st_nlink == 1, "OUTPUT_HARDLINK_FORBIDDEN")
        handle.write(payload)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, help="physical absolute extracted artifact directory")
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-sha", required=True)
    parser.add_argument("--expected-run-attempt", default="1")
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        repo = HERE.parents[1]
        contract = load_contract(repo)
        report = audit_bundle(args.artifact_root, repo, contract, expected_run_id=args.expected_run_id,
                              expected_run_sha=args.expected_run_sha, expected_run_attempt=args.expected_run_attempt,
                              expected_manifest_sha256=args.expected_manifest_sha256)
        report["audit_code_sha256"] = sha(Path(__file__).read_bytes())
        report["audited_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        path = write_report(report)
        print(json.dumps({"status": report["status"], "source_ready_for_label_rebuild": report["source_ready_for_label_rebuild"],
                          "training_authorized": False, "issues": len(report["issues"]), "output": str(path)}))
        return 0 if report["source_ready_for_label_rebuild"] else 2
    except (AuditError, OSError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED_AUDIT_STARTUP_OR_OUTPUT", "training_authorized": False,
                          "reason": str(error) if isinstance(error, AuditError) else "INPUT_OR_FIXED_OUTPUT_UNAVAILABLE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
