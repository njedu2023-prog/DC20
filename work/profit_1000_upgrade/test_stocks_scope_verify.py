"""Independent read-only collection acceptance; all market data is synthetic.

The base ZIP reader is mocked only in unit fixtures. The verifier never has a
test-injection bypass; injected receipts remain rejected. Real ZIP auditing is
exercised by the optional DC20_STOCKS_SCOPE_OUTPUT artifact integration test.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import stocks_scope_verify as verify


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def forbidden(*args, **kwargs):
    raise AssertionError("verifier must not request network, rebuild labels, or write files")


def fixture_scope():
    dates = {(date(2025, 1, 1) + timedelta(days=n)).strftime("%Y%m%d") for n in range(210)}
    dates.update(verify.inputs.PREFLIGHT_T_DATES)
    # 20250319 is already present; the other two add exactly two dates.
    assert len(dates) == 212
    return {"base_archive": {"sha256": verify.inputs.BASE_SHA256, "bytes": verify.inputs.BASE_BYTES,
                "run_id": verify.inputs.BASE_RUN_ID, "run_commit": verify.inputs.BASE_RUN_COMMIT,
                "artifact_id": verify.inputs.BASE_ARTIFACT_ID, "run_metadata_basis": "PINNED_EXTERNAL_RUN_METADATA",
                "prior_full_replay_acceptance_sha256": verify.inputs.REPLAY_ACCEPTANCE_SHA256},
            "frozen_manifest": {"synthetic": True}, "gap_dates": sorted(dates),
            "gap_candidate_codes": {d: ["600001.SH", "600002.SH"] for d in sorted(dates)},
            "reused_dates": ["20241231"], "precoverage_dates": ["20221201"],
            "preflight_T_dates": list(verify.inputs.PREFLIGHT_T_DATES),
            "code_bindings": [{"path": p, "sha256": verify.inputs._file_sha(verify.CHECKOUT / p)}
                              for p in sorted(verify.inputs.CODE_PATHS)]}


def blank(day, scope, status="NOT_REQUESTED_BUDGET_EXHAUSTED"):
    return {"trade_date": day, "request": verify.source.request_contract(day), "api_calls": 0,
            "request_sequence": None, "preflight": day in scope["preflight_T_dates"], "status": status,
            "reason": None, "network_request_performed": False, "callable_injected_for_test": False,
            "http_response_sha256": None, "http_response_bytes": None, "source_files": [],
            "rows": None, "present_candidate_count": None, "missing_candidate_codes": None}


def binding(root, path):
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def success(root, item, sequence, *, empty=False, missing=False):
    day = item["trade_date"]
    records = [] if empty else [["600003.SH" if missing else "600001.SH", day, "10.004", 100, 9999, 9]]
    raw = json.dumps({"code": 0, "detail": "...", "data": {"fields": list(verify.source.FIELDS),
        "items": records, "has_more": False, "count": 0}}).encode()
    bodies = verify.source.source_bytes(raw, day, request=item["request"],
        fetched_at_utc="2026-09-12T10:00:00Z", network_request_performed=True)
    paths = verify.source.source_paths(root, day)
    for path, contents in zip(paths, bodies):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    item.update(api_calls=1, request_sequence=sequence, network_request_performed=True,
        http_response_sha256=hashlib.sha256(raw).hexdigest(), http_response_bytes=len(raw),
        status="STOCKS_SCOPED_TABLE_EMPTY" if empty else "STOCKS_SCOPED_TABLE_PRESENT",
        source_files=[binding(root, p) for p in paths], rows=len(records),
        present_candidate_count=0 if empty or missing else 1,
        missing_candidate_codes=["600001.SH", "600002.SH"] if empty or missing else ["600002.SH"])


def refresh(root, report):
    rows = report["requests"]
    journal = sorted((r for r in rows if r["api_calls"]), key=lambda r: r["request_sequence"])
    (root / verify.JOURNAL).write_bytes(b"".join(encode(r).replace(b"\n", b" ").rstrip() + b"\n" for r in journal))
    report["source_files"] = sorted((b for r in rows for b in r["source_files"]), key=lambda b: b["path"])
    report["output_file_bindings"] = sorted((binding(root, p) for p in root.rglob("*")
        if p.is_file() and p.name != verify.RECEIPT), key=lambda b: b["path"])
    (root / verify.RECEIPT).write_bytes(encode(report))


def counts(report):
    rows = report["requests"]
    count = sum(r["status"] in verify.source.STATUSES for r in rows)
    report.update(api_calls=sum(r["api_calls"] for r in rows), qualified_new_source_dates=count,
        qualified_source_dates_including_base=181 + count,
        new_source_status_counts=dict(Counter(r["status"] for r in rows)),
        remaining_gap_dates=[r["trade_date"] for r in rows if r["status"] not in verify.source.STATUSES])


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    root = tmp_path / "source-only"
    root.mkdir()
    (root / verify.BASE).write_bytes(b"synthetic base ZIP used only with mocked strict reader")
    scope = fixture_scope()
    original_base = (root / verify.BASE).read_bytes()
    def read_base(path):
        assert Path(path) == root / verify.BASE
        if Path(path).read_bytes() != original_base:
            raise ValueError("PINNED_BASE_ARCHIVE_CHANGED")
        return copy.deepcopy(scope)
    monkeypatch.setattr(verify.inputs, "read_base_archive", read_base)
    monkeypatch.setattr(socket, "socket", forbidden)
    (root / verify.MANIFEST).write_bytes(encode(verify._manifest(scope)))
    rows = [blank(day, scope) for day in scope["gap_dates"]]
    by_day = {r["trade_date"]: r for r in rows}
    for sequence, day in enumerate(scope["preflight_T_dates"], 1):
        success(root, by_day[day], sequence)
    report = {"schema_version": verify.SCHEMA, "status": "SOURCE_GAPS_PARTIAL",
        "base_archive": scope["base_archive"], "source_policy_id": verify.source.SOURCE_POLICY_ID,
        "scope_counts": dict(verify.inputs.COUNTS), "preflight_T_dates": scope["preflight_T_dates"],
        "preflight_passed": True, "callable_injected_for_test": False, "eligible_as_real_collection": True,
        "max_api_calls": 212, "retries": 0, "elapsed_collection_seconds": 5.1,
        "requests": rows, "execution_file_bindings": {p: verify.inputs._file_sha(verify.CHECKOUT / p)
            for p in verify.inputs.CODE_PATHS | verify.NEW_CODE},
        "observed_at_utc": "2026-09-12T10:00:00+00:00", "run_id": "123456789", "run_commit": "a" * 40,
        **verify.FLAGS}
    counts(report); refresh(root, report)
    return root, report, scope


def test_partial_real_collection_replays_sources_but_not_labels_or_amount_qualification(artifact, monkeypatch):
    root, report, scope = artifact
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    result = verify.verify(root)
    assert result["status"] == "ACCEPTED_SOURCE_ONLY_PARTIAL"
    assert result["source_loads_replayed"] == 3 and result["api_calls"] == 3
    assert result["present_candidate_pairs_in_new_sources"] == 3
    assert result["missing_candidate_pairs_in_accepted_sources"] == 3
    assert result["precoverage_dates"] == 517 and result["reused_source_dates"] == 181
    for key in ("label_ready", "training_ready", "capacity_qualification_performed", "entry_price_qualification_performed",
                "actual_execution_claimed", "old_labels_recomputed", "training_performed"):
        assert result[key] is False
    assert result["files_written"] == result["network_requests"] == 0
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_complete_source_collection_still_keeps_missing_candidates_and_no_label_authority(artifact):
    root, report, scope = artifact
    seq = 3
    for item in report["requests"]:
        if not item["api_calls"]:
            seq += 1
            success(root, item, seq, empty=seq == 4)
    report["status"] = "SOURCE_GAPS_COLLECTED"
    counts(report); refresh(root, report)
    result = verify.verify(root)
    assert result["status"] == "ACCEPTED_SOURCE_ONLY_COMPLETE" and result["source_loads_replayed"] == 212
    assert result["api_calls"] == 212 and result["remaining_gap_dates"] == []
    assert result["missing_candidate_pairs_in_accepted_sources"] == 213
    assert result["label_ready"] is False and result["training_ready"] is False


def make_blocked(root, report, scope, failure):
    # Remove only fixture-owned pairs, to model a fresh collector output.
    import shutil
    shutil.rmtree(root / "data")
    rows = [blank(day, scope, "NOT_REQUESTED_PREFLIGHT_BLOCKED") for day in scope["gap_dates"]]
    by_day = {r["trade_date"]: r for r in rows}
    if failure == "credentials":
        by_day[scope["preflight_T_dates"][0]]["status"] = "PENDING_CREDENTIAL_ABSENT"
        report.update(run_id=None, run_commit=None)
    else:
        success(root, by_day[scope["preflight_T_dates"][0]], 1)
        item = by_day[scope["preflight_T_dates"][1]]
        if failure in {"empty", "missing"}:
            success(root, item, 2, empty=failure == "empty", missing=failure == "missing")
        else:
            item.update(api_calls=1, request_sequence=2, network_request_performed=True,
                        status="PENDING_NETWORK_OR_RESPONSE_ERROR")
    report.update(requests=rows, status="PREFLIGHT_BLOCKED", preflight_passed=False)
    counts(report); refresh(root, report)


@pytest.mark.parametrize("failure", ["credentials", "network", "empty", "missing"])
def test_blocked_preflight_accepts_honest_source_only_failure(artifact, failure):
    root, report, scope = artifact
    make_blocked(root, report, scope, failure)
    result = verify.verify(root)
    assert result["status"] == "ACCEPTED_PREFLIGHT_BLOCKED" and result["preflight_passed"] is False
    assert result["api_calls"] == (0 if failure == "credentials" else 2)


@pytest.mark.parametrize("key,value", [
    ("callable_injected_for_test", True), ("eligible_as_real_collection", False), ("training_performed", True),
    ("source_only", False), ("fallback_generated", True), ("label_rebuild_performed", True),
    ("source_import_into_labels_performed", True), ("forward_holdout_touched", True),
    ("max_api_calls", 213), ("retries", 1), ("api_calls", 4), ("api_calls", True),
    ("preflight_passed", 1), ("status", "SOURCE_GAPS_COLLECTED"), ("qualified_new_source_dates", 4),
    ("qualified_source_dates_including_base", 185), ("run_id", None), ("run_commit", None),
    ("run_commit", "invalid"), ("observed_at_utc", "2026-09-12T10:00:00"),
    ("elapsed_collection_seconds", -1), ("elapsed_collection_seconds", True),
])
def test_receipt_contract_tampering_rejected(artifact, key, value):
    root, report, scope = artifact
    report[key] = value
    refresh(root, report)
    with pytest.raises(ValueError):
        verify.verify(root)


@pytest.mark.parametrize("mutation", ["extra", "base", "scope", "dates", "counts", "remaining", "drop_request",
                                    "reverse_requests", "code_drop", "code_sha", "old_code_sha"])
def test_frozen_scope_and_binding_mutations_rejected(artifact, mutation):
    root, report, scope = artifact
    if mutation == "extra": report["new_field"] = False
    elif mutation == "base": report["base_archive"]["sha256"] = "0" * 64
    elif mutation == "scope": report["scope_counts"]["gap_candidate_pairs"] = 1859
    elif mutation == "dates": report["preflight_T_dates"] = sorted(report["preflight_T_dates"], reverse=True)
    elif mutation == "counts": report["new_source_status_counts"]["STOCKS_SCOPED_TABLE_PRESENT"] = 4
    elif mutation == "remaining": report["remaining_gap_dates"].pop()
    elif mutation == "drop_request": report["requests"].pop()
    elif mutation == "reverse_requests": report["requests"].reverse()
    elif mutation == "code_drop": report["execution_file_bindings"].pop(next(iter(verify.NEW_CODE)))
    elif mutation == "code_sha": report["execution_file_bindings"][next(iter(verify.NEW_CODE))] = "0" * 64
    else: report["execution_file_bindings"][next(iter(verify.inputs.CODE_PATHS))] = "0" * 64
    refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


@pytest.mark.parametrize("key,value", [
    ("request_sequence", 5), ("api_calls", True), ("network_request_performed", False),
    ("callable_injected_for_test", True), ("preflight", False), ("rows", 2), ("rows", True),
    ("present_candidate_count", 2), ("missing_candidate_codes", []), ("reason", "IGNORED_ERROR"),
    ("http_response_sha256", "0" * 64), ("http_response_bytes", 1), ("http_response_bytes", True),
    ("status", "STOCKS_SCOPED_TABLE_EMPTY"), ("source_files", []),
])
def test_request_and_source_receipt_disagreements_rejected(artifact, key, value):
    root, report, scope = artifact
    item = next(r for r in report["requests"] if r["api_calls"])
    item[key] = value
    refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


@pytest.mark.parametrize("mutation", ["old_scope", "offset", "limit", "different_day", "code_filter", "fields"])
def test_no_unregistered_filter_or_pagination_protocol(artifact, mutation):
    root, report, scope = artifact
    item = report["requests"][0]
    if mutation == "old_scope": del item["request"]["params"]["ts_type"]
    elif mutation in {"offset", "limit"}: item["request"]["params"][mutation] = 1
    elif mutation == "different_day": item["request"]["params"]["trade_date"] = "20250102"
    elif mutation == "code_filter": item["request"]["params"]["ts_code"] = "600001.SH"
    else: item["request"]["fields"].reverse()
    refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


@pytest.mark.parametrize("mutation", ["http", "source", "zero_rows", "zero_missing", "network", "reason"])
def test_unattempted_request_cannot_hide_zero_imputation_or_response(artifact, mutation):
    root, report, scope = artifact
    item = next(r for r in report["requests"] if not r["api_calls"])
    if mutation == "http": item.update(http_response_sha256="0" * 64, http_response_bytes=123)
    elif mutation == "source": item["source_files"] = [report["source_files"][0]]
    elif mutation == "zero_rows": item["rows"] = 0
    elif mutation == "zero_missing": item["missing_candidate_codes"] = []
    elif mutation == "network": item["network_request_performed"] = True
    else: item["reason"] = "NO_FILL"
    refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


@pytest.mark.parametrize("mutation", ["late_call", "late_budget", "skip_failure", "reverse_preflight", "fake_pass"])
def test_preflight_barrier_cannot_be_bypassed(artifact, mutation):
    root, report, scope = artifact
    make_blocked(root, report, scope, "network")
    by_day = {r["trade_date"]: r for r in report["requests"]}
    if mutation == "late_call": success(root, by_day[scope["preflight_T_dates"][2]], 3)
    elif mutation == "late_budget": by_day[scope["preflight_T_dates"][2]]["status"] = "NOT_REQUESTED_BUDGET_EXHAUSTED"
    elif mutation == "skip_failure": by_day[scope["preflight_T_dates"][1]] = blank(scope["preflight_T_dates"][1], scope, "NOT_REQUESTED_PREFLIGHT_BLOCKED")
    elif mutation == "reverse_preflight":
        for r in report["requests"]:
            if r["api_calls"]: r["request_sequence"] = 3 - r["request_sequence"]
    else: report.update(preflight_passed=True, status="SOURCE_GAPS_PARTIAL")
    if mutation == "skip_failure": report["requests"] = [by_day[d] for d in scope["gap_dates"]]
    counts(report); refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


def test_failure_response_retained_as_pending_without_empty_source(artifact):
    root, report, scope = artifact
    item = next(r for r in report["requests"] if not r["api_calls"])
    item.update(api_calls=1, request_sequence=4, network_request_performed=True,
        status="PENDING_INVALID_STOCKS_SOURCE", reason="STOCKS_TABLE_REQUIRES_EXPLICIT_COMPLETE_PAGINATION",
        http_response_sha256="1" * 64, http_response_bytes=384539)
    counts(report); refresh(root, report)
    result = verify.verify(root)
    assert result["status"] == "ACCEPTED_SOURCE_ONLY_PARTIAL" and result["source_loads_replayed"] == 3
    assert item["trade_date"] in result["remaining_gap_dates"]


@pytest.mark.parametrize("mutation", ["base_bytes", "manifest", "unbound_file", "empty_dir", "symlink_file", "hardlink",
    "symlink_dir", "duplicate_json", "journal", "binding_duplicate", "binding_bytes", "metadata", "body", "pair_extra"])
def test_filesystem_and_persisted_document_tampering_rejected(artifact, mutation):
    root, report, scope = artifact
    if mutation == "base_bytes": (root / verify.BASE).write_bytes(b"different base")
    elif mutation == "manifest": (root / verify.MANIFEST).write_bytes(encode({"gap_dates": []}))
    elif mutation == "unbound_file": (root / "labels.json").write_bytes(b"{}")
    elif mutation == "empty_dir": (root / "unused").mkdir()
    elif mutation == "symlink_file": (root / "aliased").symlink_to(root / verify.BASE)
    elif mutation == "hardlink": os.link(root / verify.BASE, root / "aliased")
    elif mutation == "symlink_dir": (root / "aliased").symlink_to(root / "data", target_is_directory=True)
    elif mutation == "duplicate_json":
        (root / verify.RECEIPT).write_bytes(b'{"schema_version": "x", "schema_version": "y"}')
    elif mutation == "journal": (root / verify.JOURNAL).write_bytes(b"{}\n")
    elif mutation == "binding_duplicate":
        report["output_file_bindings"].append(report["output_file_bindings"][0]); (root / verify.RECEIPT).write_bytes(encode(report))
    elif mutation == "binding_bytes":
        report["output_file_bindings"][0]["bytes"] = True; (root / verify.RECEIPT).write_bytes(encode(report))
    elif mutation in {"metadata", "body"}:
        item = next(r for r in report["requests"] if r["api_calls"])
        path = root / item["source_files"][1 if mutation == "metadata" else 0]["path"]
        value = json.loads(path.read_bytes())
        if mutation == "metadata": value["network_request_performed"] = False
        else: value["has_more"] = True
        path.write_bytes(verify.source.codec._json(value))
        item["source_files"] = [binding(root, root / b["path"]) for b in item["source_files"]]
    else:
        extra = root / verify.source.ROOT_PATH / "2025" / "20250731" / "data.json"
        extra.parent.mkdir(parents=True); extra.write_bytes(b"{}")
    if mutation not in {"duplicate_json", "journal", "binding_duplicate", "binding_bytes", "symlink_dir", "symlink_file"}:
        refresh(root, report)
    with pytest.raises(ValueError): verify.verify(root)


def test_output_root_symlink_is_rejected(artifact):
    root, report, scope = artifact
    alias = root.parent / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="UNSAFE_OUTPUT_ROOT"): verify.verify(alias)


@pytest.mark.parametrize("key,value", [("retries", 1), ("max_api_calls", 213), ("max_workers", 4),
    ("redirects_allowed", True), ("pagination_or_filter_fallback_allowed", True),
    ("minimum_request_start_interval_seconds", 1), ("label_rebuild_performed", True),
    ("request_start_budget_seconds", 1300), ("request_start_headroom_seconds", 0),
    ("transport_timeout_semantics", "ABSOLUTE_RESPONSE_DEADLINE"), ("max_elapsed_seconds", 1200)])
def test_registered_contract_is_validated_beyond_its_mutable_binding(artifact, monkeypatch, key, value):
    root, report, scope = artifact
    location = root.parent / "contract-fixture"
    location.mkdir()
    contract = json.loads((verify.HERE / "STOCKS_SCOPE_COLLECTION.json").read_bytes())
    contract[key] = value
    (location / "STOCKS_SCOPE_COLLECTION.json").write_bytes(encode(contract))
    monkeypatch.setattr(verify, "HERE", location)
    with pytest.raises(ValueError, match="REGISTERED_SOURCE_CONTRACT_CHANGED"):
        verify.verify(root)


def test_fifo_rejected_before_any_blocking_read(artifact):
    root, report, scope = artifact
    os.mkfifo(root / "pipe")
    with pytest.raises(ValueError, match="NONREGULAR_OUTPUT_FILE"):
        verify.verify(root)


def test_code_changed_after_module_import_rejected_before_acceptance(artifact, monkeypatch):
    root, report, scope = artifact
    monkeypatch.setattr(verify, "_LOADED_FILES", {Path(verify.__file__): "0" * 64})
    with pytest.raises(ValueError, match="LOADED_VERIFICATION_CODE_CHANGED"):
        verify.verify(root)


@pytest.mark.parametrize("status,reason,response", [
    ("PENDING_HTTP_ERROR", "HTTP_429", False), ("PENDING_HTTP_ERROR", "HTTP_ERROR", False),
    ("PENDING_NETWORK_OR_RESPONSE_ERROR", None, False), ("PENDING_NETWORK_OR_RESPONSE_ERROR", None, True)])
def test_failed_transport_receipts_remain_pending(artifact, status, reason, response):
    root, report, scope = artifact
    item = next(r for r in report["requests"] if not r["api_calls"])
    item.update(api_calls=1, request_sequence=4, network_request_performed=True, status=status, reason=reason,
        http_response_sha256="1" * 64 if response else None, http_response_bytes=321 if response else None)
    counts(report); refresh(root, report)
    assert verify.verify(root)["status"] == "ACCEPTED_SOURCE_ONLY_PARTIAL"


def test_output_mutation_during_source_replay_is_detected(artifact, monkeypatch):
    root, report, scope = artifact
    original_load = verify.source.load
    def changed(*args):
        result = original_load(*args)
        (root / verify.RECEIPT).write_bytes((root / verify.RECEIPT).read_bytes() + b" ")
        return result
    monkeypatch.setattr(verify.source, "load", changed)
    with pytest.raises(ValueError, match="OUTPUT_CHANGED_DURING_ACCEPTANCE"): verify.verify(root)


def test_optional_real_artifact_requires_unmodified_archive_and_live_source_replay():
    location = os.environ.get("DC20_STOCKS_SCOPE_OUTPUT")
    if not location:
        pytest.skip("set DC20_STOCKS_SCOPE_OUTPUT to one immutable real collection directory")
    result = verify.verify(Path(location))
    assert result["status"] in {"ACCEPTED_SOURCE_ONLY_COMPLETE", "ACCEPTED_SOURCE_ONLY_PARTIAL", "ACCEPTED_PREFLIGHT_BLOCKED"}
    assert result["files_written"] == result["network_requests"] == 0
    assert result["label_ready"] is result["training_ready"] is False
