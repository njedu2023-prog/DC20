#!/usr/bin/env python3
"""Bounded, isolated Tushare source collection; stdlib only, never labels/models."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ENDPOINT = "https://api.tushare.pro/"
ASOF = "20260904"
PAGE_SIZE, MAX_PAGES, MAX_REQUESTS = 5000, 4, 6500
INTERVAL, TAIL_SESSIONS = .5, 20
SOFT_DEADLINE_SECONDS = 90 * 60
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
OUTPUT_SUBDIR = "dc20-profit-history-source-20260906"
FIELDS = {
    "daily": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
    "stk_limit": ["ts_code", "trade_date", "pre_close", "up_limit", "down_limit"],
    "adj_factor": ["ts_code", "trade_date", "adj_factor"],
}
INPUTS = {
    "ledger": {"path": "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz", "sha256": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"},
    "calendar": {"path": "data/market/trade_cal_sse.csv", "sha256": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"},
}
MARKET_CODE = re.compile(r"\d{6}\.(?:SH|SZ|BJ|OF)")
CANDIDATE_CODE = re.compile(r"\d{6}\.(?:SH|SZ)")


class CollectionError(Exception):
    """Only a code selected in this file is ever exposed; never upstream text."""
    def __init__(self, code, *, optional_api_rejection=False):
        self.code = code
        self.optional_api_rejection = optional_api_rejection
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise CollectionError(code)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha(path):
    return sha_bytes(Path(path).read_bytes())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def no_symlinks(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() for part in [path, *path.parents]), "SYMLINK_PATH_FORBIDDEN")


def safe_input(repo, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "UNSAFE_INPUT_PATH")
    path = Path(repo).resolve(strict=True) / relative
    no_symlinks(path)
    require(path.is_file(), "INPUT_FILE_MISSING")
    return path


def read_rows(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_plan(plan):
    expected = {"as_of_date": ASOF, "tail_sessions": TAIL_SESSIONS, "daily_mode": "full_market_paged", "page_size": PAGE_SIZE, "max_pages": MAX_PAGES, "min_request_interval_seconds": INTERVAL, "max_requests": MAX_REQUESTS, "optional_adj_factor": True}
    require(all(plan.get(key) == value for key, value in expected.items()), "PLAN_FETCH_CONTRACT_MISMATCH")
    require(plan.get("source_inputs") == INPUTS, "PLAN_SOURCE_PINS_MISMATCH")
    require(plan.get("source_repository") == "njedu2023-prog/DC20" and plan.get("source_commit") == "3e2299a07f7b4430002da0b870c47ecf57c49bb3", "PLAN_SOURCE_IDENTITY_MISMATCH")
    require("daily_batch_size" not in plan and "candidate_batches" not in canonical(plan).decode(), "BATCH_CODE_MODE_FORBIDDEN")


def build_request(repo, plan):
    """Pure read-only builder used by root to freeze REQUEST.json before dispatch."""
    validate_plan(plan)
    paths = {name: safe_input(repo, spec["path"]) for name, spec in INPUTS.items()}
    require(all(sha(paths[name]) == spec["sha256"] for name, spec in INPUTS.items()), "SOURCE_PIN_MISMATCH")
    calendar = read_rows(paths["calendar"])
    opened = [row["cal_date"] for row in calendar if row["exchange"] == "SSE" and row["is_open"] == "1"]
    require(opened == sorted(set(opened)) and all(re.fullmatch(r"20\d{6}", date) for date in opened), "STRICT_CALENDAR_INVALID")
    positions = {date: i for i, date in enumerate(opened)}
    require(ASOF in positions, "ASOF_NOT_STRICT_SSE_SESSION")
    rows = read_rows(paths["ledger"])
    require(len(rows) == 6753, "FIXED_LEDGER_ROW_COUNT_MISMATCH")
    sessions, seen, canaries = {}, set(), []
    for row in rows:
        d, t, t1, code = (row[key] for key in ["signal_date", "exec_date", "scheduled_exit_date", "ts_code"])
        require(CANDIDATE_CODE.fullmatch(code) is not None, "CANDIDATE_CODE_INVALID")
        require((d, code) not in seen, "DUPLICATE_FROZEN_CANDIDATE")
        seen.add((d, code))
        require(d in positions and positions[d] + 2 < len(opened), "D_OUTSIDE_CALENDAR")
        i = positions[d]
        require(t == opened[i + 1] and t1 == opened[i + 2] and t1 <= ASOF, "FROZEN_D_T_T1_MISMATCH")
        require(row["stage"] in ("2", "3"), "FROZEN_STAGE_SCOPE_INVALID")
        rank = int(row["promotion_rank"])
        require(1 <= rank <= 10, "FROZEN_RANK_INVALID")
        canaries.append((t, rank, code))
        stop = min(positions[t1] + TAIL_SESSIONS, positions[ASOF])
        for session in opened[positions[t]:stop + 1]:
            sessions.setdefault(session, set()).add(code)
    first_t, _, first_code = min(canaries)
    return {"as_of_date": ASOF, "tail_sessions": TAIL_SESSIONS, "canary": {"trade_date": first_t, "ts_code": first_code}, "sessions": [{"trade_date": date, "codes": sorted(codes)} for date, codes in sorted(sessions.items())], "source_inputs": INPUTS}


def request_contract(expected):
    """Small public authorization; full scope has a deterministic content hash."""
    return {**{key: value for key, value in expected.items() if key != "sessions"}, "session_plan_sha256": sha_bytes(canonical(expected["sessions"])), "session_count": len(expected["sessions"]), "candidate_date_keys": sum(len(session["codes"]) for session in expected["sessions"])}


def verify_request(request, expected):
    supplied = {key: value for key, value in request.items() if key != "_activation"}
    require(supplied == request_contract(expected), "REQUEST_DIFFERS_FROM_FIXED_LEDGER_CALENDAR_WINDOW")


def prepare_output(repo, explicit=None, environment=None):
    environment = os.environ if environment is None else environment
    if explicit is None:
        base = environment.get("RUNNER_TEMP", "")
        require(bool(base), "RUNNER_TEMP_REQUIRED_WITHOUT_EXPLICIT_EMPTY_OUTPUT")
        base_path = Path(base).absolute()
        no_symlinks(base_path)
        require(base_path.is_dir(), "RUNNER_TEMP_DIRECTORY_MISSING")
        target = base_path / OUTPUT_SUBDIR
        require(not target.exists(), "EXISTING_RUNNER_OUTPUT_FORBIDDEN")
    else:
        target = Path(explicit)
        require(target.is_absolute(), "EXPLICIT_OUTPUT_MUST_BE_ABSOLUTE")
    no_symlinks(target)
    if environment.get("GITHUB_ACTIONS") == "true":
        runner_temp = environment.get("RUNNER_TEMP", "")
        require(bool(runner_temp), "RUNNER_TEMP_REQUIRED_IN_ACTIONS")
        no_symlinks(Path(runner_temp).absolute())
        require(target.absolute() == Path(runner_temp).absolute() / OUTPUT_SUBDIR, "ACTIONS_OUTPUT_NOT_FIXED_RUNNER_TEMP")
    repo = Path(repo).resolve(strict=True)
    target = target.absolute()
    require(target != Path(target.anchor) and len(target.parts) >= 4, "BROAD_OUTPUT_ROOT_FORBIDDEN")
    require(not target.is_relative_to(repo) and not repo.is_relative_to(target), "OUTPUT_MUST_BE_OUTSIDE_REPOSITORY")
    require(target.parent.is_dir(), "OUTPUT_PARENT_MUST_ALREADY_EXIST")
    if target.exists():
        require(explicit is not None and target.is_dir() and not any(target.iterdir()), "EXISTING_NONEMPTY_OUTPUT_FORBIDDEN")
    else:
        target.mkdir(mode=0o700)
    require(not any(target.iterdir()), "OUTPUT_NOT_EMPTY")
    return target


class Artifacts:
    def __init__(self, root):
        self.root = Path(root).absolute()
        no_symlinks(self.root)
        self.hashes = {}

    def path(self, relative):
        relative = Path(relative)
        require(not relative.is_absolute() and ".." not in relative.parts, "UNSAFE_ARTIFACT_PATH")
        path = self.root / relative
        no_symlinks(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        no_symlinks(path)
        return path

    def write(self, relative, payload, *, replace_status=False):
        path = self.path(relative)
        if replace_status:
            require(str(relative) == "status.json", "ONLY_STATUS_CAN_BE_REPLACED")
            require(not path.exists() or path.stat().st_nlink == 1, "SHARED_STATUS_FILE_FORBIDDEN")
            with path.open("wb") as handle:
                handle.write(payload)
        else:
            with path.open("xb") as handle:
                handle.write(payload)
        self.hashes[str(relative)] = {"sha256": sha_bytes(payload), "bytes": len(payload)}

    def json(self, relative, value, **kwargs):
        self.write(relative, canonical(value) + b"\n", **kwargs)

    def raw(self, relative, payload):
        self.write(relative, gzip.compress(payload, mtime=0))

    def csv(self, relative, fields, rows):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        self.write(relative, stream.getvalue().encode())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("HTTPS_REDIRECT_REFUSED")


def https_post(body):
    """TLS certificate verification, exact official endpoint, no proxy or redirect."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=30) as response:
            require(response.geturl() == ENDPOINT, "HTTPS_ENDPOINT_CHANGED")
            require(response.status == 200, "UPSTREAM_HTTP_FAILURE")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            require(len(payload) <= MAX_RESPONSE_BYTES, "RESPONSE_SIZE_CAP_EXCEEDED")
            return payload
    except CollectionError:
        raise
    except Exception:
        # urllib exceptions may contain URLs, request objects, or upstream text.
        raise CollectionError("HTTPS_TRANSPORT_FAILURE") from None


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def incomplete_candidate_fields(api, rows):
    """Only explicit provider JSON nulls; missing rows are a separate concept."""
    return [{"ts_code": row["ts_code"], "fields": [field for field in FIELDS[api][2:] if row[field] is None]}
            for row in sorted(rows, key=lambda row: row["ts_code"])
            if any(row[field] is None for field in FIELDS[api][2:])]


def validate_rows(api, fields, items, date, wanted, *, canary=False):
    require(isinstance(fields, list) and all(isinstance(field, str) for field in fields) and len(fields) == len(set(fields)) and set(fields) == set(FIELDS[api]), "RESPONSE_FIELDS_MISMATCH")
    require(isinstance(items, list) and len(items) <= PAGE_SIZE, "RESPONSE_PAGE_BOUND_EXCEEDED")
    rows, keys = [], set()
    for values in items:
        require(isinstance(values, list) and len(values) == len(fields), "RESPONSE_ROW_WIDTH_MISMATCH")
        row = dict(zip(fields, values))
        code = row.get("ts_code")
        require(isinstance(code, str) and MARKET_CODE.fullmatch(code) is not None, "RESPONSE_CODE_INVALID")
        require(row.get("trade_date") == date, "RESPONSE_DATE_OUT_OF_SCOPE")
        require(code not in keys, "RESPONSE_DUPLICATE_CODE")
        keys.add(code)
        require(not canary or code in wanted, "CANARY_CODE_OUT_OF_SCOPE")
        if code in wanted:
            for field in FIELDS[api][2:]:
                # Bulk null is observed missingness, not malformed evidence and
                # never zero. The permission/schema canary remains non-null.
                require((row[field] is None and not canary) or numeric(row[field]), "CANDIDATE_NUMERIC_EVIDENCE_MISSING")
            if api == "daily":
                require(all(row[k] is None or row[k] > 0 for k in ["open", "high", "low", "close", "pre_close"]), "CANDIDATE_PRICE_NONPOSITIVE")
                for upper, lower in [("high", "open"), ("high", "close"), ("high", "low"), ("open", "low"), ("close", "low")]:
                    if row[upper] is not None and row[lower] is not None:
                        require(row[upper] + .011 >= row[lower], "CANDIDATE_OHLC_INCONSISTENT")
                require(all(row[k] is None or row[k] >= 0 for k in ["vol", "amount"]), "CANDIDATE_VOLUME_NEGATIVE")
            elif api == "stk_limit":
                require(all(row[k] is None or row[k] > 0 for k in ["pre_close", "up_limit", "down_limit"]), "CANDIDATE_LIMIT_INVALID")
                if row["up_limit"] is not None and row["down_limit"] is not None:
                    require(row["up_limit"] >= row["down_limit"], "CANDIDATE_LIMIT_INVALID")
            else:
                require(row["adj_factor"] is None or row["adj_factor"] > 0, "CANDIDATE_ADJ_FACTOR_INVALID")
        rows.append(row)
    require(not canary or len(rows) == 1, "CANARY_EMPTY_OR_MULTIPLE_ROWS")
    return rows


class Client:
    def __init__(self, token, artifacts, *, transport=https_post, clock=time.monotonic, sleep=time.sleep, environment=None):
        require(isinstance(token, str) and token.strip() == token and len(token) >= 8, "RUNTIME_CREDENTIAL_MISSING_OR_INVALID")
        self._token = token
        self.artifacts, self.transport, self.clock, self.sleep = artifacts, transport, clock, sleep
        self.count, self.last_start = 0, None
        self.started_monotonic = self.clock()
        env = os.environ if environment is None else environment
        runid, commit, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_SHA", ""), env.get("GITHUB_RUN_ATTEMPT", "")
        require(not runid or re.fullmatch(r"\d{1,24}", runid) is not None, "RUN_ID_METADATA_INVALID")
        require(not commit or re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "RUN_SHA_METADATA_INVALID")
        require(not attempt or re.fullmatch(r"\d{1,8}", attempt) is not None, "RUN_ATTEMPT_METADATA_INVALID")
        self.identity = {"github_run_id": runid or None, "github_sha": commit or None, "github_run_attempt": attempt or None}

    def query(self, api, params, wanted, *, canary=False):
        require(api in FIELDS, "API_NOT_ALLOWED")
        require(self.clock() - self.started_monotonic < SOFT_DEADLINE_SECONDS, "COLLECTION_SOFT_DEADLINE_EXCEEDED")
        require(self.count < MAX_REQUESTS, "REQUEST_BUDGET_EXHAUSTED")
        expected_keys = {"trade_date", "limit", "offset"} | ({"ts_code"} if canary else set())
        require(set(params) == expected_keys and params["limit"] == PAGE_SIZE, "REQUEST_PARAMETERS_NOT_FIXED")
        require(re.fullmatch(r"20\d{6}", params["trade_date"]) is not None and params["trade_date"] <= ASOF, "REQUEST_DATE_INVALID")
        require(isinstance(params["offset"], int) and not isinstance(params["offset"], bool) and 0 <= params["offset"] < PAGE_SIZE * MAX_PAGES, "REQUEST_OFFSET_INVALID")
        require(not canary or (params["offset"] == 0 and params["ts_code"] in wanted and CANDIDATE_CODE.fullmatch(params["ts_code"]) is not None), "CANARY_REQUEST_INVALID")
        if self.last_start is not None:
            self.sleep(max(0., self.last_start + INTERVAL - self.clock()))
        require(self.clock() - self.started_monotonic < SOFT_DEADLINE_SECONDS, "COLLECTION_SOFT_DEADLINE_EXCEEDED")
        self.last_start, self.count = self.clock(), self.count + 1
        prefix = f"{self.count:06d}"
        receipt = {"query_number": self.count, "api_name": api, "params": params, "fields": FIELDS[api], "started_utc": utc(), **self.identity, "credential_persisted": False, "upstream_message_persisted": False}
        receipt["request_without_credential_sha256"] = sha_bytes(canonical({"api_name": api, "params": params, "fields": ",".join(FIELDS[api])}))
        payload = None
        try:
            body = canonical({"api_name": api, "token": self._token, "params": params, "fields": ",".join(FIELDS[api])})
            try:
                payload = self.transport(body)
            except CollectionError:
                raise
            except Exception:
                raise CollectionError("HTTPS_TRANSPORT_FAILURE") from None
            require(isinstance(payload, bytes) and len(payload) <= MAX_RESPONSE_BYTES, "RESPONSE_BYTES_INVALID")
            receipt.update(response_sha256=sha_bytes(payload), response_bytes=len(payload))
            require(self._token.encode() not in payload, "SECRET_ECHO_RESPONSE_REFUSED")
            try:
                obj = json.loads(payload)
            except Exception:
                raise CollectionError("RESPONSE_JSON_INVALID") from None
            require(isinstance(obj, dict), "RESPONSE_JSON_STRUCTURE_INVALID")
            try:
                normalized = canonical(obj)
            except (ValueError, TypeError):
                raise CollectionError("RESPONSE_NONFINITE_OR_INVALID_JSON") from None
            require(self._token.encode() not in normalized, "SECRET_ECHO_RESPONSE_REFUSED")
            code = obj.get("code")
            if not isinstance(code, int) or isinstance(code, bool) or code != 0:
                receipt["upstream_code"] = code if isinstance(code, int) and not isinstance(code, bool) else None
                raise CollectionError("UPSTREAM_API_REJECTED", optional_api_rejection=(api == "adj_factor"))
            # Keep exact successful payload only when it contains no upstream
            # prose; error payloads are represented by hashes, never persisted.
            require(obj.get("msg") in (None, ""), "UPSTREAM_MESSAGE_PAYLOAD_REFUSED")
            data = obj.get("data")
            require(isinstance(data, dict), "RESPONSE_DATA_INVALID")
            rows = validate_rows(api, data.get("fields"), data.get("items"), params["trade_date"], set(wanted), canary=canary)
            receipt.update(status="SUCCESS", row_count=len(rows), selected_row_count=sum(row["ts_code"] in wanted for row in rows), completed_utc=utc())
            raw_path = "responses/" + prefix + ".json.gz"
            self.artifacts.raw(raw_path, payload)
            receipt["raw_response_artifact"] = raw_path
            self.artifacts.json("receipts/" + prefix + ".json", receipt)
            if self.count % 25 == 0:
                print(json.dumps({"progress_queries": self.count, "last_api": api, "last_date": params["trade_date"]}), flush=True)
            return rows
        except CollectionError as error:
            receipt.update(status="FAILED", failure_code=error.code, completed_utc=utc(), raw_response_artifact=None)
            self.artifacts.json("receipts/" + prefix + ".json", receipt)
            raise


def collect_pages(client, api, date, codes):
    all_rows, seen, pages, offset, query_numbers = [], set(), 0, 0, []
    for page in range(MAX_PAGES):
        rows = client.query(api, {"trade_date": date, "limit": PAGE_SIZE, "offset": offset}, codes)
        query_numbers.append(client.count)
        pages += 1
        keys = {row["ts_code"] for row in rows}
        require(not seen.intersection(keys), "PAGINATION_DUPLICATES_OR_IGNORED_OFFSET")
        seen.update(keys)
        all_rows.extend(rows)
        candidate_complete = set(codes).issubset(seen)
        exhausted = len(rows) == 0
        if candidate_complete or exhausted:
            selected = sorted((row for row in all_rows if row["ts_code"] in codes), key=lambda row: row["ts_code"])
            return selected, {"pages": pages, "queried_market_rows": len(all_rows), "selected_rows": len(selected), "missing_candidate_codes": sorted(set(codes) - {row["ts_code"] for row in selected}), "incomplete_candidate_fields": incomplete_candidate_fields(api, selected), "candidate_scope_complete": candidate_complete, "whole_market_exhaustion_observed": exhausted, "pagination_termination": "ALL_REQUESTED_CANDIDATES_FOUND" if candidate_complete else "EMPTY_PAGE_EXHAUSTED_WITH_MISSING_CANDIDATES", "query_numbers": query_numbers}
        # Provider endpoint caps can differ. A nonempty short page does not
        # establish exhaustion, and its actual length defines the next offset.
        offset += len(rows)
    raise CollectionError("PAGINATION_CAP_REACHED_COMPLETENESS_UNPROVEN")


def perform_collection(client, request, artifacts, provenance):
    status = {"schema_version": "dc20_profit_history_collection_v1", "status": "RUNNING_CANARY", "started_utc": utc(), "as_of_date": ASOF, "tail_sessions": TAIL_SESSIONS, "provenance": provenance, "request_count": 0, "required_collection_complete": False, "required_candidate_coverage_complete": False, "optional_adj_factor": "NOT_CHECKED", "corporate_actions_resolved": False, "tail_window_is_forced_exit": False, "actual_fill_claim": False, "production_data_writes": False, "models_trained": 0, "source_files_are_new_labels": False, "sessions_completed": 0, "optional_sessions_completed": 0, "coverage": []}
    artifacts.json("status.json", status, replace_status=True)
    try:
        c = request["canary"]
        optional = True
        for api in FIELDS:
            try:
                rows = client.query(api, {**c, "limit": PAGE_SIZE, "offset": 0}, {c["ts_code"]}, canary=True)
                artifacts.csv("canary/" + api + ".csv", FIELDS[api], rows)
            except CollectionError as error:
                if api == "adj_factor" and error.optional_api_rejection:
                    optional = False
                    status["optional_adj_factor"] = "OPTIONAL_UNAVAILABLE"
                    continue
                raise
        if optional:
            status["optional_adj_factor"] = "CANARY_AVAILABLE_NOT_CORPORATE_ACTION_RESOLUTION"
        status["status"] = "COLLECTING_REQUIRED_SOURCES"
        required_missing = False
        for session in request["sessions"]:
            date, codes = session["trade_date"], set(session["codes"])
            coverage = {"trade_date": date, "requested_candidate_count": len(codes), "apis": {}}
            # Required evidence for every session has priority over all bulk
            # optional diagnostics; a small optional canary is the only lead-in.
            for api in ("daily", "stk_limit"):
                selected, info = collect_pages(client, api, date, codes)
                artifacts.csv("candidate_sources/" + date + "/" + api + ".csv", FIELDS[api], selected)
                coverage["apis"][api] = info
                if info["missing_candidate_codes"] or info["incomplete_candidate_fields"]:
                    required_missing = True
            status["coverage"].append(coverage)
            status["sessions_completed"] += 1
            status["request_count"] = client.count
            if status["sessions_completed"] % 25 == 0:
                artifacts.json("status.json", status, replace_status=True)
        status.update(status="COLLECTING_OPTIONAL_DIAGNOSTICS", required_collection_complete=True, required_candidate_coverage_complete=not required_missing)
        artifacts.json("status.json", status, replace_status=True)
        if optional:
            for session, coverage in zip(request["sessions"], status["coverage"]):
                date, codes = session["trade_date"], set(session["codes"])
                try:
                    selected, info = collect_pages(client, "adj_factor", date, codes)
                except CollectionError as error:
                    if error.code in ("REQUEST_BUDGET_EXHAUSTED", "COLLECTION_SOFT_DEADLINE_EXCEEDED"):
                        optional = False
                        status["optional_adj_factor"] = "OPTIONAL_PARTIAL_BUDGET"
                        status["optional_stop_code"] = error.code
                        break
                    if error.optional_api_rejection:
                        optional = False
                        status["optional_adj_factor"] = "OPTIONAL_PARTIAL_THEN_UNAVAILABLE"
                        break
                    raise
                artifacts.csv("candidate_sources/" + date + "/adj_factor.csv", FIELDS["adj_factor"], selected)
                coverage["apis"]["adj_factor"] = info
                status["optional_sessions_completed"] += 1
                status["request_count"] = client.count
                if status["optional_sessions_completed"] % 25 == 0:
                    artifacts.json("status.json", status, replace_status=True)
        status["status"] = "COLLECTED_REQUIRED_SOURCES_WITH_GAPS" if required_missing else "COLLECTED_REQUIRED_SOURCES"
        if optional:
            status["optional_adj_factor"] = "COLLECTED_DIAGNOSTIC_ONLY_NOT_CORPORATE_ACTION_RESOLUTION"
        result = 0
    except CollectionError as error:
        status.update(status="BLOCKED_COLLECTION", failure_code=error.code)
        result = 2
    except Exception:
        # No exception str/traceback: third-party endpoint and IO failures can
        # contain unexpected data. Preserve only the phase and safe code.
        status.update(status="BLOCKED_COLLECTION", failure_code="UNCLASSIFIED_COLLECTION_FAILURE")
        result = 2
    status.update(completed_utc=utc(), request_count=client.count)
    artifacts.json("status.json", status, replace_status=True)
    artifacts.json("artifact_manifest.json", {"schema_version": "dc20_isolated_source_artifact_manifest_v1", "status": status["status"], "provenance": provenance, **client.identity, "files": {path: entry for path, entry in artifacts.hashes.items() if path != "artifact_manifest.json"}, "requests_attempted": client.count, "production_writes": False, "source_data_only": True})
    print(json.dumps({"status": status["status"], "requests_attempted": client.count, "sessions_completed": status["sessions_completed"], "optional_adj_factor": status["optional_adj_factor"], "artifact_manifest_sha256": artifacts.hashes["artifact_manifest.json"]["sha256"]}), flush=True)
    return result, status


def repository():
    for candidate in HERE.parents:
        if (candidate / INPUTS["ledger"]["path"]).is_file():
            return candidate
    raise CollectionError("REPOSITORY_NOT_FOUND")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Explicit absolute external empty temporary directory; no production paths")
    parser.add_argument("--repo", help="Must resolve to this collector's automatically discovered source repository")
    args = parser.parse_args()
    artifacts = None
    try:
        repo = repository()
        if args.repo:
            require(Path(args.repo).resolve(strict=True) == repo.resolve(strict=True), "REPOSITORY_ARGUMENT_MISMATCH")
        target = prepare_output(repo, args.output)
        artifacts = Artifacts(target)
        plan_path, request_path = safe_input(HERE, "PLAN.json"), safe_input(HERE, "REQUEST.json")
        plan, request = json.loads(plan_path.read_text()), json.loads(request_path.read_text())
        expected = build_request(repo, plan)
        verify_request(request, expected)
        # The token is read only now, from the runtime environment, and is never
        # put in parameters, filenames, receipts, status, logs or exception text.
        token = os.environ.get("TUSHARE_TOKEN", "")
        client = Client(token, artifacts)
        # Preserve exact pinned control files, but never permit a credential to
        # be present in any bytes written, even if supplied outside this tool.
        for name, path in [("PLAN.json", plan_path), ("REQUEST.json", request_path)]:
            payload = path.read_bytes()
            require(token.encode() not in payload and token.encode() not in canonical(json.loads(payload)), "SECRET_IN_CONTROL_FILE_REFUSED")
            artifacts.write(name, payload)
        artifacts.json("session_plan.json", expected["sessions"])
        provenance = {"source_repository": plan["source_repository"], "source_commit": plan["source_commit"], "plan_sha256": sha(plan_path), "request_sha256": sha(request_path), "collector_sha256": sha(HERE / "collect.py"), "source_inputs": INPUTS, **client.identity, "soft_deadline_seconds": SOFT_DEADLINE_SECONDS, "ledger_candidate_universe_unchanged": True, "market_endpoint": ENDPOINT, "global_other_IP_activity_audited": False}
        code, _ = perform_collection(client, expected, artifacts, provenance)
        return code
    except CollectionError as error:
        code = error.code
    except Exception:
        code = "STARTUP_VALIDATION_FAILURE"
    if artifacts is not None:
        try:
            artifacts.json("status.json", {"status": "BLOCKED_STARTUP", "failure_code": code, "models_trained": 0, "production_writes": False}, replace_status=True)
        except Exception:
            pass
    print(json.dumps({"status": "BLOCKED_STARTUP", "failure_code": code}), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
