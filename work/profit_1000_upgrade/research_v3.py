"""Isolated, append-only v3 research collection and full-cohort label rebuild.

No model fitting, historical performance selection, production integration or
diagnostic-envelope reconstruction is available from this entry point.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from work.profit_1000_upgrade import acceptance, auction_truth_v3 as auction, minute_truth
from work.profit_1000_upgrade.policy_v3 import CONTRACT, validate_contract

V2_PLAN_SHA = "5c666ece8f7f153004c485959449828b45b249668783f047ff24d7906faea75f"
BASE_SHA = "58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64"
BASE_RUN = "34676871475"
BASE_COMMIT = "df6c38806da20066f673875a4a83c37fa895d2e4"
IMPORTED_BINDINGS_SHA = "7765f9cef50730eef38af9eefdb3a89ca7dbb8a969fff1036f75f61a48b12372"
IMPORTED_MINUTE_REQUESTS_SHA = "8aef0ca5324ab09ea00843fd469978c5e4313853fef0540297f3b8ebd4f7d2e4"
MARKER = ".dc20-profit-1000-research-root.json"
INITIAL_SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(raw):
    return acceptance._json(raw, "v3 research JSON")


def write_json(path, value):
    path = Path(path)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    _write(path, raw)


def _write(path, raw):
    if any(p.is_symlink() for p in (path, *path.parents)) or path.exists():
        raise ValueError("immutable research output already exists or is aliased")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def safe_input(root, binding):
    relative = PurePosixPath(binding["path"])
    if (relative.is_absolute() or ".." in relative.parts or "\\" in str(relative)
            or relative.as_posix() != binding["path"]):
        raise ValueError("unsafe source binding path")
    path = Path(root) / relative
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or sha(path) != binding["sha256"]):
        raise ValueError("immutable source SHA mismatch: " + str(relative))
    return path


def plan_path():
    return HERE / "PLAN_V3.json"


def load_plan():
    plan = _json(plan_path().read_bytes())
    validate_contract(plan)
    if sha(HERE / "PLAN_V2.json") != V2_PLAN_SHA:
        raise ValueError("prior plan bytes changed")
    old = _json((HERE / "PLAN_V2.json").read_bytes())
    changed = {"schema_version", "source_commit", "supersedes_plan_sha256", "entry_policy_id",
               "research_entry_policy_id", "auction_source_policy_id", "adapter_sources", "base_archive", "buy_rule"}
    if any(plan.get(key) != value for key, value in old.items() if key not in changed):
        raise ValueError("v3 cannot change frozen cohort, features, exit, costs or model gates")
    if (plan.get("schema_version") != "dc20_profit_1000_upgrade_plan_v3"
            or plan.get("plan_version") != "v3" or plan.get("phase") != "CANONICAL_AUCTION_ONLY"
            or plan.get("supersedes_plan_sha256") != V2_PLAN_SHA
            or plan.get("training_performed") is not False
            or plan.get("research_entry_policy_id") != CONTRACT["entry_policy_id"]
            or plan.get("source_commit") != "6bbf56c2e6f11d3ea880e7dcff011ef7c3fb501c"
            or plan.get("revises_v3_plan_sha256") != "344b6e87c95665bb08d41c6f328488ab49c5edeb4723347f0706704289c6b8cf"
            or plan.get("http_envelope_adapter_id") != "dc20_canonical_http_empty_detail_v1"
            or plan.get("collection_contract_sha256") != sha(HERE / "COLLECTION_V3.json")):
        raise ValueError("v3 registered stage identity changed")
    if plan.get("base_archive") != {"zip_sha256": BASE_SHA, "run_id": BASE_RUN,
            "run_commit": BASE_COMMIT, "artifact_id": 10292829634,
            "daily_partitions": 926, "limit_partitions": 926, "minute_pairs": 2726}:
        raise ValueError("wrong registered v2 source archive")
    expected = {b["path"] for b in old["adapter_sources"]} | {"src/top10decision/decision/executable_profit_shadow_settlement.py"} | {
        "work/profit_1000_upgrade/" + name for name in ("auction_truth_v3.py", "auction_http_v3.py", "collect.py", "acceptance.py")}
    if len(plan["adapter_sources"]) != len(expected) or {b["path"] for b in plan["adapter_sources"]} != expected:
        raise ValueError("incomplete pinned adapter sources")
    for binding in plan["adapter_sources"]:
        safe_input(ROOT, binding)
    return plan


def require_research_mirror(root):
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("aliased research root")
    root = root.resolve(strict=True)
    if root == ROOT or ROOT in root.parents or root in ROOT.parents:
        raise ValueError("research root must be separate from checkout")
    load_plan()
    if (root / MARKER).is_symlink() or not (root / MARKER).is_file():
        raise ValueError("research marker must be an unaliased regular file")
    marker = _json((root / MARKER).read_bytes())
    expected = {"schema_version": "dc20_profit_1000_research_mirror_v3", "plan_version": "v3",
                "plan_sha256": sha(plan_path()), "production_writes": False, **dict(CONTRACT)}
    if marker != expected:
        raise ValueError("v3 research marker contract changed")
    return root


def prepare_history(root):
    root = require_research_mirror(root)
    plan = load_plan()
    inputs = {key: safe_input(root, value) for key, value in plan["source_inputs"].items()}
    fm = _json(inputs["manifest"].read_bytes())
    columns = fm["feature_contract"]["columns"]
    if fm["feature_contract"]["known_at"] != "D close":
        raise ValueError("feature temporal contract changed")
    with gzip.open(inputs["ledger"], "rt", encoding="utf-8-sig", newline="") as stream:
        original = list(csv.DictReader(stream))
    dates = sorted({r["signal_date"] for r in original})
    if (len(original) != 6753 or len(dates) != 910 or dates[0] != "20221111" or dates[-1] != "20260814"):
        raise ValueError("frozen historical candidate scope changed")
    rows, codes = [], {}
    for row in original:
        d = row["signal_date"]
        if not row["promotion_oof_train_end"] < d:
            raise ValueError("promotion OOF model trained after D")
        features = {key: None if row[key] == "" else float(row[key]) for key in columns}
        if any(v is not None and not math.isfinite(v) for v in features.values()):
            raise ValueError("nonfinite D-only feature")
        normalized = {key: row[key] for key in ("signal_date", "ts_code", "stage_transition", "exec_date", "scheduled_exit_date")}
        normalized.update(promotion_rank=int(row["promotion_rank"]), features=features, feature_as_of_date=d,
                          feature_available_at=f"{d[:4]}-{d[4:6]}-{d[6:]}T23:59:00+08:00")
        rows.append(normalized)
        codes.setdefault(d, []).append(row["ts_code"])
    return {"schema_version": "dc20_profit_1000_research_candidates_v1",
            "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
            "feature_availability_is_natural_freeze_evidence": False,
            "feature_columns": columns, "rows": rows, "expected_candidate_codes": codes,
            "source_bindings": list(plan["source_inputs"].values()),
            **dict(CONTRACT), "plan_version": "v3", "plan_sha256": sha(plan_path())}


def initialize(output, archive):
    """Full old integrity audit first, narrow byte-preserving source import only."""
    output, archive = Path(output), Path(archive)
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("fresh unaliased research output required")
    resolved = output.resolve()
    if resolved == ROOT or ROOT in resolved.parents or resolved in ROOT.parents:
        raise ValueError("research output must be outside checkout")
    plan = load_plan()
    audit = acceptance.audit_archive(archive, expected_zip_sha256=BASE_SHA,
                                    expected_run_id=BASE_RUN, expected_commit=BASE_COMMIT)
    selected, requests = {}, []
    with zipfile.ZipFile(archive) as bundle:
        collection = _json(bundle.read("collection_receipt.json"))
        base = _json(bundle.read("research_inputs/base_archive_import.json"))
        original = _json(bundle.read("research_inputs/manifest.json"))
        def add(binding):
            path = binding["path"]
            raw = bundle.read(path)
            if hashlib.sha256(raw).hexdigest() != binding["sha256"] or path in selected:
                raise ValueError("duplicate or unbound archived source")
            selected[path] = (dict(binding), raw)
        for spec in plan["source_inputs"].values():
            add(spec)
        if len(base["source_files"]) != 1852:
            raise ValueError("base daily/limit partition count changed")
        for binding in base["source_files"]:
            if not re.fullmatch(r"data/market/raw/(20\d{2})/(20\d{6})/(daily|stk_limit)\.csv", binding["path"]):
                raise ValueError("archived daily/limit outside whitelist")
            add(binding)
        qualified_paths = set()
        for item in collection["request_receipts"]:
            if item.get("endpoint") != "stk_mins" or item.get("status") != "EXACT_TRUTH_WRITTEN":
                continue
            day, code = item["trade_date"], item["ts_code"]
            expected_request = {"api_name": "stk_mins", "params": minute_truth.request_parameters(day, code),
                                "fields": list(minute_truth.FIELDS)}
            paths = [p.relative_to(output).as_posix() for p in minute_truth.paths(output, day, code)]
            bindings = item["new_source_files"]
            if (item["request"] != expected_request or item.get("network_request_performed") is not True
                    or item.get("existing_source_files") != [] or len(bindings) != 2
                    or {b["path"] for b in bindings} != set(paths) or qualified_paths.intersection(paths)):
                raise ValueError("archived minute request identity changed")
            meta = _json(bundle.read(paths[1]))
            if item["http_response_sha256"] != meta["response_body_sha256"]:
                raise ValueError("archived minute HTTP digest changed")
            for binding in bindings:
                add(binding)
            qualified_paths.update(paths)
            requests.append({"provenance": "REUSED_FROM_PINNED_V2_NOT_CURRENT_NETWORK_REQUEST", **item})
        declared = {b["path"] for b in collection["new_source_files"] + collection["existing_source_files"]
                    if b["path"].startswith(minute_truth.SOURCE_ROOT + "/")}
        if qualified_paths != declared or len(requests) != 2726:
            raise ValueError("archived qualified minute coverage changed")
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / MARKER, {"schema_version": "dc20_profit_1000_research_mirror_v3",
                   "plan_version": "v3", "plan_sha256": sha(plan_path()), "production_writes": False, **dict(CONTRACT)})
        for path, (_, raw) in sorted(selected.items()):
            _write(output / path, raw)
        for item in requests:
            payload = minute_truth.load(output, item["trade_date"], item["ts_code"])
            if (payload is None or payload["provider_timestamp_semantics_confirmed"] is not False
                    or payload["production_activation_allowed"] is not False):
                raise ValueError("reused minute source qualification changed")
        manifest = prepare_history(output)
        ignored = {"plan_version", "plan_sha256", *CONTRACT.keys()}
        if {k: v for k, v in original.items() if k not in ignored} != {k: v for k, v in manifest.items() if k not in ignored}:
            raise ValueError("D-only candidate reconstruction differs from frozen v2 cohort")
        if sha(archive) != BASE_SHA:
            raise ValueError("archive changed during import")
        receipt = {"schema_version": "dc20_profit_1000_source_import_v3", "base_archive": plan["base_archive"],
                   "plan_sha256": sha(plan_path()), "source_files": [selected[p][0] for p in sorted(selected)],
                   "reused_minute_request_receipts": requests, "current_network_requests": 0,
                   "old_source_bytes_modified": False, "old_auction_sources_imported": False,
                   "old_outcome_labels_imported": False, "production_writes": False,
                   "original_candidate_manifest_sha256": hashlib.sha256(bundle.read("research_inputs/manifest.json")).hexdigest(),
                   "archive_integrity_audit": audit}
        write_json(output / "research_inputs/base_archive_import_v3.json", receipt)
        write_json(output / "research_inputs/manifest.json", manifest)
    return {"root": str(output), "candidate_rows": 6753, "D_dates": 910,
            "reused_minute_pairs": 2726, "imported_files": len(selected), "production_writes": False}


def verify_import(root):
    """Pinned source-list and request-list digests prevent re-baselining changes."""
    root = require_research_mirror(root)
    path = root / "research_inputs/base_archive_import_v3.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("unaliased import receipt required")
    original_sha = sha(path)
    value = _json(path.read_bytes())
    for key, expected in (("source_files", IMPORTED_BINDINGS_SHA),
                          ("reused_minute_request_receipts", IMPORTED_MINUTE_REQUESTS_SHA)):
        digest = hashlib.sha256(json.dumps(value[key], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if digest != expected:
            raise ValueError("original archived source/request binding list changed")
    for binding in value["source_files"]:
        safe_input(root, binding)
    if sha(path) != original_sha:
        raise ValueError("import receipt changed during verification")
    return value


_RESPONSE_FIELDS = ("request", "http_response_sha256", "http_response_bytes", "source_status",
                    "source_rows", "fetched_at_utc", "api_code", "http_status", "candidate_rows_present")


def _validate_source_response_receipt(item, meta):
    mapping = {"http_response_sha256": "http_response_sha256", "http_response_bytes": "http_response_bytes",
               "api_code": "api_code", "source_rows": "rows", "fetched_at_utc": "fetched_at_utc"}
    if any(type(item.get(key)) is not type(meta[source]) or item[key] != meta[source]
           for key, source in mapping.items()):
        raise ValueError("auction receipt and source response metadata disagree")


def _validate_preflight(receipt, journal, contract):
    """Verify the sequential probe barrier from the bound original journal.

    Attempted here means the collector evaluated that date; HTTP attempts are
    separately counted. Missing credentials/budget must still stop the barrier.
    """
    dates = contract["preflight_T_dates"]
    covered = [item for item in journal if item["trade_date"] >= auction.COVERAGE_START]
    if not covered:
        raise ValueError("missing sequential canonical preflight")
    attempted, qualified, failure = [], [], None
    for day, item in zip(dates, covered):
        if item["trade_date"] != day:
            raise ValueError("canonical preflight order changed")
        if item["status"] == "PENDING_PREFLIGHT_ABORTED":
            raise ValueError("preflight attempt cannot itself be skipped")
        attempted.append(day)
        if item["status"] != "EXACT_TRUTH_WRITTEN":
            failure = day
            break
        qualified.append(day)
    passed = len(qualified) == len(dates)
    if not passed and failure is None:
        raise ValueError("incomplete canonical preflight journal")
    expected = {"trade_dates": dates, "attempted_T_dates": attempted,
                "qualified_T_dates": qualified, "status": "PASS" if passed else "BLOCKED",
                "aborted_at_T_date": failure}
    if receipt.get("preflight") != expected:
        raise ValueError("canonical preflight receipt does not match journal")
    for item in covered[len(attempted):]:
        aborted = item["status"] == "PENDING_PREFLIGHT_ABORTED"
        if (passed and aborted) or (not passed and not aborted) or not passed and (
                item.get("network_request_performed") is not False or item.get("new_source_files") != []
                or any(key in item for key in _RESPONSE_FIELDS)):
            raise ValueError("request crossed failed preflight barrier")
    return expected


def validate_collection(root, manifest):
    """Source integrity and completeness are separate; pending is not zero."""
    from work.profit_1000_upgrade import collect_v3, auction_http_v3
    root = require_research_mirror(root)
    verify_import(root)
    plan = load_plan()
    contract = collect_v3.collection_contract()
    receipt_binding = {"path": "collection_receipt.json", "sha256": sha(root / "collection_receipt.json")}
    receipt = _json(safe_input(root, receipt_binding).read_bytes())
    validate_contract(receipt)
    if (receipt.get("schema_version") != collect_v3.SCHEMA or receipt.get("phase") != collect_v3.PHASE
            or receipt.get("plan_sha256") != sha(plan_path())
            or receipt.get("collection_contract_sha256") != plan["collection_contract_sha256"]
            or receipt.get("as_of_date") != plan["as_of_date"]
            or receipt.get("http_envelope_adapter_id") != auction_http_v3.ADAPTER_ID
            or receipt.get("http_envelope_adapter_sha256") != sha(HERE / "auction_http_v3.py")
            or any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k, v in collect_v3.FLAGS.items())
            or receipt.get("retries") != 0 or receipt.get("minute_api_calls") != 0 or receipt.get("daily_api_calls") != 0
            or receipt.get("source_files") != receipt.get("new_source_files")):
        raise ValueError("v3 collection provenance/phase changed")
    collect_v3._budget(receipt["budget"])
    if set(receipt["execution_file_bindings"]) != {p.relative_to(ROOT).as_posix() for p in collect_v3._IMPLEMENTATIONS}:
        raise ValueError("collector code bindings incomplete")
    for path, digest in receipt["execution_file_bindings"].items():
        safe_input(ROOT, {"path": path, "sha256": digest})
    bindings = {}
    for binding in receipt["imported_source_files"] + receipt["new_source_files"] + [receipt["journal_binding"]]:
        safe_input(root, binding)
        if binding["path"] in bindings:
            raise ValueError("duplicate collection source binding")
        bindings[binding["path"]] = binding
    base_path = "research_inputs/base_archive_import_v3.json"
    if base_path not in bindings:
        raise ValueError("base import receipt unbound")
    base = _json(safe_input(root, bindings[base_path]).read_bytes())
    if (base.get("base_archive") != plan["base_archive"] or base.get("plan_sha256") != sha(plan_path())
            or base.get("current_network_requests") != 0
            or any(base.get(k) is not False for k in ("old_source_bytes_modified", "old_auction_sources_imported",
                                                     "old_outcome_labels_imported", "production_writes"))):
        raise ValueError("reused source provenance changed")
    reused = {}
    for binding in base["source_files"]:
        if bindings.get(binding["path"]) != binding or binding["path"] in reused:
            raise ValueError("reused source outside original import bindings")
        reused[binding["path"]] = binding
    if len(reused) != 3 + 1852 + 2726 * 2:
        raise ValueError("reused source count changed")
    observed_minutes = set()
    for item in base["reused_minute_request_receipts"]:
        day, code = item["trade_date"], item["ts_code"]
        if ((day, code) in observed_minutes or item.get("provenance") != "REUSED_FROM_PINNED_V2_NOT_CURRENT_NETWORK_REQUEST"
                or item.get("network_request_performed") is not True or item.get("status") != "EXACT_TRUTH_WRITTEN"
                or item.get("request") != {"api_name": "stk_mins", "params": minute_truth.request_parameters(day, code),
                                           "fields": list(minute_truth.FIELDS)}):
            raise ValueError("reused minute request provenance changed")
        observed_minutes.add((day, code))
        payload = minute_truth.load(root, day, code)
        if payload is None or any(reused.get(b["path"]) != b for b in payload["source_files"]):
            raise ValueError("reused minute missing exact source binding")
        meta = _json(minute_truth.paths(root, day, code)[1].read_bytes())
        if item["http_response_sha256"] != meta["response_body_sha256"] or item["new_source_files"] != payload["source_files"]:
            raise ValueError("reused minute body binding changed")
    if len(observed_minutes) != 2726:
        raise ValueError("reused minute receipt count changed")
    if receipt["journal_binding"]["path"] != "collection_requests.jsonl":
        raise ValueError("incorrect journal path")
    journal = [_json(line) for line in safe_input(root, receipt["journal_binding"]).read_bytes().splitlines()]
    requests = receipt["request_receipts"]
    dates, by_date = collect_v3._scope(manifest, contract)
    if (len(requests) != 910 or [r["trade_date"] for r in requests] != dates
            or sorted(journal, key=lambda r: r["trade_date"]) != requests):
        raise ValueError("candidate/request/journal date coverage changed")
    preflight = _validate_preflight(receipt, journal, contract)
    qualified, pre, seen_paths = 0, 0, set()
    pending_statuses = {"PENDING_CREDENTIAL_ABSENT", "PENDING_BUDGET_EXHAUSTED", "PENDING_HTTP_ERROR",
                        "PENDING_NETWORK_OR_RESPONSE_ERROR", "PENDING_INVALID_HTTP_BYTES", "PENDING_INVALID_SOURCE_NOT_IMPUTED",
                        "PENDING_PREFLIGHT_ABORTED"}
    for item in requests:
        day = item["trade_date"]
        if (item["endpoint"] != "stk_auction" or item["ts_code"] is not None
                or item.get("candidate_codes") != sorted(by_date[day])
                or item.get("source_policy_id") != auction.SOURCE_POLICY_ID
                or item.get("http_envelope_adapter_id") != auction_http_v3.ADAPTER_ID
                or item.get("http_envelope_adapter_sha256") != sha(HERE / "auction_http_v3.py")
                or item.get("plan_sha256") != sha(plan_path())
                or item.get("collection_contract_sha256") != plan["collection_contract_sha256"]
                or type(item.get("network_request_performed")) is not bool):
            raise ValueError("auction request identity changed")
        non_network = item["status"] in {"HISTORY_BEFORE_CANONICAL_COVERAGE", "PENDING_CREDENTIAL_ABSENT",
                                         "PENDING_BUDGET_EXHAUSTED", "PENDING_PREFLIGHT_ABORTED"}
        if (item["network_request_performed"] is non_network
                or non_network and any(key in item for key in _RESPONSE_FIELDS)
                or not non_network and item.get("request") != auction.request_contract(day)):
            raise ValueError("request attempt status or provenance changed")
        if day < auction.COVERAGE_START:
            if (item["status"] != "HISTORY_BEFORE_CANONICAL_COVERAGE" or item["network_request_performed"] is not False
                    or item["new_source_files"] != [] or any(p.exists() for p in auction.source_paths(root, day))):
                raise ValueError("invalid precoverage declaration")
            pre += 1
        elif item["status"] == "EXACT_TRUTH_WRITTEN":
            source = auction.load(root, day)
            meta = _json(auction.source_paths(root, day)[1].read_bytes())
            _validate_source_response_receipt(item, meta)
            source_bindings = [dict(b) for b in source.source_files]
            if (source.requested_code is not None or item["network_request_performed"] is not True
                    or item.get("request") != auction.request_contract(day) or item["request"] != meta["request"]
                    or item.get("source_status") != source.status
                    or item.get("http_response_sha256") != meta["http_response_sha256"]
                    or item["new_source_files"] != source_bindings
                    or any(bindings.get(b["path"]) != b for b in source_bindings)):
                raise ValueError("auction source not bound to actual request")
            seen_paths.update(b["path"] for b in source_bindings)
            qualified += 1
        elif (item["status"] not in pending_statuses or item["new_source_files"] != []
              or any(p.exists() for p in auction.source_paths(root, day))):
            raise ValueError("failed request silently became source truth")
    complete = qualified == 393 and pre == 517
    calls = sum(r["network_request_performed"] for r in requests)
    attempted_all = all(r["network_request_performed"] or r["status"] == "HISTORY_BEFORE_CANONICAL_COVERAGE" for r in requests)
    if (type(receipt.get("api_calls")) is not int or receipt["api_calls"] != calls or calls > receipt["budget"]["max_api_calls"]
            or receipt.get("auction_attempts_complete") is not attempted_all
            or receipt.get("auction_evidence_complete") is not complete
            or receipt.get("status") != ("COMPLETE" if complete else "BLOCKED")
            or receipt.get("request_status_counts") != dict(Counter(r["status"] for r in requests))
            or {b["path"] for b in receipt["new_source_files"]} != seen_paths):
        raise ValueError("collection accounting/qualification changed")
    expected_files = set(bindings) | {"collection_receipt.json"}
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual_files != expected_files:
        raise ValueError("unexpected unbound research input or preexisting output")
    safe_input(root, receipt_binding)
    bindings[receipt_binding["path"]] = receipt_binding
    return bindings, {"integrity": "PASS", "canonical_dates_qualified": qualified,
                      "precoverage_declarations": pre, "reused_minute_pairs": len(observed_minutes),
                      "auction_evidence_complete": complete, "api_calls": calls, "preflight": preflight}


def evaluate(root):
    if sha(Path(__file__)) != INITIAL_SELF_SHA:
        raise ValueError("research implementation changed since import")
    root = require_research_mirror(root)
    code_paths = sorted({"work/profit_1000_upgrade/" + n for n in (
        "research_v3.py", "labels_v3.py", "policy_v3.py", "collect_v3.py", "auction_truth_v3.py",
        "PLAN_V3.json", "COLLECTION_V3.json", "minute_truth.py", "acceptance.py")}
        | {b["path"] for b in load_plan()["adapter_sources"]})
    code = [{"path": p, "sha256": sha(ROOT / p)} for p in code_paths]
    from work.profit_1000_upgrade import labels_v3, policy_v3
    manifest = prepare_history(root)
    if _json((root / "research_inputs/manifest.json").read_bytes()) != manifest:
        raise ValueError("published candidate manifest differs from frozen reconstruction")
    bindings, source_summary = validate_collection(root, manifest)
    for binding in code:
        safe_input(ROOT, binding)
    labels = labels_v3.build_labels(root, manifest, as_of_date=load_plan()["as_of_date"])
    if len(labels["rows"]) != 6753 or len(labels["cohorts_by_date"]) != 910:
        raise ValueError("label rebuild silently dropped frozen candidates")
    for binding in labels["source_files"]:
        if bindings.get(binding["path"]) != binding:
            raise ValueError("label used a source outside qualified collection/import")
    statuses = dict(Counter(r["label_status"] for r in labels["rows"]))
    entry_statuses = dict(Counter(r.get("entry_qualification_status") or "NOT_REACHED" for r in labels["rows"]))
    pending = sorted({(r["missing_evidence_kind"], r["missing_evidence_date"], r["missing_evidence_code"])
                      for r in labels["rows"] if r.get("missing_evidence_kind")
                      and r["label_status"] not in policy_v3.NO_FILL_STATUSES and r["label_status"] != policy_v3.SETTLED})
    for row in labels["rows"]:
        if row["label_status"] != policy_v3.SETTLED and row["label_status"] not in policy_v3.NO_FILL_STATUSES:
            if any(row.get(k) is not None for k in ("net_return", "slot_net_return", "conditional_net_return")):
                raise ValueError("pending truth became a numerical outcome")
    # No mutation between source acceptance and label handoff.
    for binding in bindings.values():
        safe_input(root, binding)
    for binding in code:
        safe_input(ROOT, binding)
    complete_days = sum(c["complete"] for c in labels["cohorts_by_date"].values())
    status = ("BLOCKED_CANONICAL_SOURCE" if not source_summary["auction_evidence_complete"] else
              "BLOCKED_EXIT_OR_ENTRY_EVIDENCE" if complete_days != 910 else "LABEL_EVIDENCE_READY_FOR_SEPARATE_REVIEW")
    summary = {"schema_version": "dc20_profit_1000_rebuild_summary_v3", "status": status,
               "phase": "CANONICAL_AUCTION_ONLY", "source_acceptance": source_summary,
               "candidate_rows": len(labels["rows"]), "D_dates": 910, "complete_D_dates": complete_days,
               "label_status_counts": statuses, "entry_qualification_counts": entry_statuses,
               "pending_evidence_requests": [{"kind": k, "trade_date": d, "ts_code": c} for k, d, c in pending],
               "pending_requests_by_kind": dict(Counter(k for k, _, _ in pending)),
               "plan_sha256": sha(plan_path()), "code_bindings": code,
               "collection_receipt_binding": {"path": "collection_receipt.json", "sha256": sha(root / "collection_receipt.json")},
               "training_performed": False, "production_writes": False, "old_source_bytes_modified": False,
               "actual_execution_claimed": False, "actual_capacity_verified": False,
               "profitability_improvement_proven": False, "forward_holdout_evaluated": False,
               "production_activation_allowed": False, "historical_evidence_is_untouched_test": False}
    write_json(root / "research_results/labels.json", labels)
    summary["labels_binding"] = {"path": "research_results/labels.json", "sha256": sha(root / "research_results/labels.json")}
    write_json(root / "research_results/summary.json", summary)
    return {k: v for k, v in summary.items() if k not in {"pending_evidence_requests", "code_bindings"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("initialize")
    init.add_argument("--root", required=True)
    init.add_argument("--v2-archive", required=True)
    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("--root", required=True)
    args = parser.parse_args()
    value = initialize(args.root, args.v2_archive) if args.command == "initialize" else evaluate(args.root)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
