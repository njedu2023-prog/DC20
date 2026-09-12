"""Offline artifact protocol tests; fake HTTP/run identities are NOT cloud proof."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from work.profit_1000_upgrade import minute_gap_collect as collector, minute_gap_verify as verifier
from work.profit_1000_upgrade.test_minute_gap_collect import FakeClock, FixedDatetime, response

RUN, COMMIT, LABEL_SHA = "123456789", "a" * 40, "b" * 64
DAY = "20260106"
REAL_BUDGET = collector.RequestBudget


def pairs(n=5):
    return [(DAY, f"{i:06d}.SZ") for i in range(1, n + 1)]


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN)
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    monkeypatch.setattr(collector, "datetime", FixedDatetime)
    counter = 0
    def make(n=5, *, fail_at=None, token="synthetic-never-real", budget_expire_at=None):
        nonlocal counter
        counter += 1
        parent = tmp_path / f"fixture-{counter}"
        parent.mkdir()
        plan = collector.expected_plan(pairs(n), LABEL_SHA)
        plan_path = parent / "registered.json"
        plan_path.write_bytes(collector.json_bytes(plan))
        digest = verifier.file_sha(plan_path)
        clock, seen = FakeClock(), []
        monkeypatch.setattr(collector, "RequestBudget", lambda total: REAL_BUDGET(total, clock=clock, sleep=clock.sleep))
        def fake_http(contract, supplied_token):
            assert supplied_token == token
            pair = (contract["params"]["start_date"][:10].replace("-", ""), contract["params"]["ts_code"])
            seen.append(pair)
            if budget_expire_at == pair: clock.value = 4180
            if fail_at == pair: return b'{"code":-1,"msg":"synthetic failure"}'
            return response(pair)
        monkeypatch.setattr(collector, "official_call", fake_http)
        root = parent / "output"
        report = collector.run_collection(root, plan_path=plan_path, expected_plan_sha256=digest,
            expected_label_report_sha256=LABEL_SHA, token=token)
        kwargs = dict(expected_plan_sha256=digest, expected_label_report_sha256=LABEL_SHA,
                      expected_run_id=RUN, expected_run_commit=COMMIT, registered_plan_path=plan_path)
        return root, report, kwargs, seen
    return make


def rewrite(root, report, *, journal=True):
    """Reseal mutable test documents so deep checks, not stale outer SHA, fail."""
    if journal:
        ordered = sorted((r for r in report["requests"] if r["api_calls"]), key=lambda r: r["request_sequence"])
        (root / verifier.JOURNAL).write_bytes(b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False,
            allow_nan=False).encode() + b"\n" for r in ordered))
    files = {}
    for path in root.rglob("*"):
        if path.is_file() and path.name != verifier.RECEIPT:
            name = path.relative_to(root).as_posix()
            files[name] = {"path": name, "sha256": verifier.file_sha(path), "bytes": path.stat().st_size}
    for row in report["requests"]:
        row["source_files"] = [files.get(b["path"], b) for b in row["source_files"]]
    report["source_files"] = sorted((b for row in report["requests"] for b in row["source_files"]), key=lambda b: b["path"])
    report["output_file_bindings"] = [files[p] for p in sorted(files)]
    if journal:
        ordered = sorted((r for r in report["requests"] if r["api_calls"]), key=lambda r: r["request_sequence"])
        (root / verifier.JOURNAL).write_bytes(b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False,
            allow_nan=False).encode() + b"\n" for r in ordered))
        for item in report["output_file_bindings"]:
            if item["path"] == verifier.JOURNAL:
                item.update(sha256=verifier.file_sha(root / verifier.JOURNAL), bytes=(root / verifier.JOURNAL).stat().st_size)
    (root / verifier.RECEIPT).write_bytes(verifier.canonical(report))


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_all_exact_minute_sources_validate_and_typed_result_is_source_only(artifact, n):
    root, report, kwargs, seen = artifact(n)
    authority = verifier.verify(root, **kwargs)
    assert type(authority) is verifier.VerifiedMinuteCollection
    assert authority.root == root and authority.gap_pairs == authority.successful_pairs == tuple(pairs(n))
    assert len(authority.source_bindings) == n * 2 and len(seen) == n
    assert authority.receipt_sha256 == verifier.file_sha(root / verifier.RECEIPT)
    assert authority.plan_sha256 == kwargs["expected_plan_sha256"] and authority.label_report_sha256 == LABEL_SHA
    assert authority.run_id == RUN and authority.run_commit == COMMIT and authority.as_of_date == "20260911"
    assert authority.source_only is True and authority.label_source_eligible is False
    assert authority.status == "MINUTE_GAPS_COLLECTED" and report["qualified_source_pairs"] == n
    authority.assert_unchanged()
    again = verifier.reload_verified(authority)
    assert again == authority and again is not authority


def test_registered_scope_independent_contract_matches_collector_without_calling_it(monkeypatch):
    expected = collector.expected_plan(pairs(), LABEL_SHA)
    monkeypatch.setattr(collector, "expected_plan", lambda *a: pytest.fail("collector plan trusted"))
    assert verifier.expected_plan([list(p) for p in pairs()], LABEL_SHA) == expected


@pytest.mark.parametrize("index,expected_calls", [(0, 1), (2, 2), (4, 3)])
def test_failed_first_middle_last_preflight_stops_all_later_pairs(artifact, index, expected_calls):
    root, report, kwargs, seen = artifact(fail_at=pairs()[index])
    authority = verifier.verify(root, **kwargs)
    assert authority.status == "PREFLIGHT_BLOCKED" and len(seen) == expected_calls
    assert len(authority.successful_pairs) == expected_calls - 1
    assert report["api_calls"] == expected_calls
    assert len(authority.gap_pairs) == 5


def test_zero_call_credential_absence_is_not_success_and_still_needs_external_identity(artifact):
    root, report, kwargs, seen = artifact(token="")
    authority = verifier.verify(root, **kwargs)
    assert authority.status == "PREFLIGHT_BLOCKED" and authority.successful_pairs == ()
    assert report["api_calls"] == 0 and not seen and authority.source_bindings == ()
    kwargs["expected_run_id"] = None
    with pytest.raises(ValueError, match="EXTERNAL_RUN_IDENTITY_REQUIRED"): verifier.verify(root, **kwargs)


def test_partial_bulk_failure_keeps_only_qualified_successes(artifact):
    root, report, kwargs, seen = artifact(fail_at=pairs()[1])
    authority = verifier.verify(root, **kwargs)
    assert authority.status == "MINUTE_GAPS_PARTIAL" and len(seen) == 5
    assert authority.successful_pairs == tuple(p for p in pairs() if p != pairs()[1])
    assert report["requests"][1]["source_files"] == [] and report["requests"][1]["source_rows"] is None


def test_actual_admission_budget_exhaustion_accepts_partial_without_inventing_sources(artifact):
    root, report, kwargs, seen = artifact(budget_expire_at=pairs()[2])
    authority = verifier.verify(root, **kwargs)
    assert authority.status == "PREFLIGHT_BLOCKED" and len(seen) == 2
    assert authority.successful_pairs == (pairs()[0], pairs()[2])
    assert report["elapsed_collection_seconds"] >= 4180


@pytest.mark.parametrize("name,value", [("expected_run_id", "9"), ("expected_run_commit", "c" * 40),
    ("expected_plan_sha256", "c" * 64), ("expected_label_report_sha256", "c" * 64),
    ("expected_run_id", 123), ("expected_run_commit", None), ("expected_plan_sha256", "A" * 64)])
def test_external_identity_and_both_expected_hashes_cannot_be_self_declared(artifact, name, value):
    root, _, kwargs, _ = artifact(1)
    kwargs[name] = value
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("field,value", [("max_api_calls", 5001), ("retries", 1), ("query_start", "09:30:00"),
    ("continuous_rows", 239), ("planned_pair_count", True), ("preflight_pairs", []),
    ("provider_timestamp_semantics_confirmed", True), ("unregistered", "extra"),
    ("source_only", False), ("transport_timeout_semantics", "HARD_DEADLINE"), ("max_workers", 5)])
def test_even_rehashed_registered_plan_cannot_change_fixed_contract(artifact, field, value):
    root, report, kwargs, _ = artifact(1)
    plan = json.loads((root / verifier.PLAN).read_bytes())
    plan[field] = value
    raw = verifier.canonical(plan)
    (root / verifier.PLAN).write_bytes(raw)
    kwargs["registered_plan_path"].write_bytes(raw)
    kwargs["expected_plan_sha256"] = hashlib.sha256(raw).hexdigest()
    report["plan_sha256"] = kwargs["expected_plan_sha256"]
    rewrite(root, report)
    with pytest.raises(ValueError, match="REGISTERED_PLAN_CONTRACT_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("planned", [[], pairs(5001), [pairs()[0], pairs()[0]], list(reversed(pairs())),
    [["20260914", "600000.SH"]], [["20221031", "600000.SH"]], [["20260230", "600000.SH"]],
    [[DAY, "600000.BJ"]], [[DAY, "../600000.SH"]], [[DAY, "600000.SH", "extra"]]])
def test_independently_derived_plan_rejects_scope_drift(planned):
    with pytest.raises(ValueError): verifier.expected_plan([list(p) for p in planned], LABEL_SHA)


@pytest.mark.parametrize("field", list(verifier.FLAGS))
def test_source_only_and_no_activation_flags_are_exact_not_truthy(artifact, field):
    root, report, kwargs, _ = artifact(1)
    report[field] = 1 if report[field] else 0
    rewrite(root, report)
    with pytest.raises(ValueError, match="RECEIPT_POLICY_OR_IDENTITY_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("field,value", [("schema_version", "old"), ("status", "MINUTE_GAPS_PARTIAL"),
    ("api_calls", True), ("qualified_source_pairs", True), ("preflight_passed", 1), ("retries", True),
    ("planned_pair_count", 2), ("run_id", None), ("run_commit", "b" * 40),
    ("eligible_as_real_collection", False), ("callable_injected_for_test", True),
    ("elapsed_collection_seconds", -1), ("elapsed_collection_seconds", True),
    ("observed_at_utc", "2026-09-13T12:00:00"), ("observed_at_utc", "2026-09-10T12:00:00Z"),
    ("request_status_counts", {}), ("execution_file_bindings", {})])
def test_receipt_count_time_code_identity_and_status_must_reconcile(artifact, field, value):
    root, report, kwargs, _ = artifact(1)
    report[field] = value
    rewrite(root, report)
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("field,value", [("api_calls", True), ("network_request_performed", 1),
    ("request_sequence", 0), ("request_sequence", True), ("request_sequence", 2),
    ("request_start_elapsed_seconds", 4180), ("request_start_elapsed_seconds", -1),
    ("request_start_elapsed_seconds", True), ("preflight", 1), ("source_rows", 240.0),
    ("http_response_bytes", 1_000_001), ("http_response_sha256", "bad"),
    ("reason", "server response text"), ("status", "MISSING_AS_ZERO"), ("source_files", [])])
def test_per_request_types_budget_and_evidence_are_not_coerced(artifact, field, value):
    root, report, kwargs, _ = artifact(1)
    report["requests"][0][field] = value
    rewrite(root, report)
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


def test_global_start_spacing_and_preflight_order_not_just_final_call_count(artifact):
    root, report, kwargs, _ = artifact()
    by_seq = sorted(report["requests"], key=lambda r: r["request_sequence"])
    by_seq[1]["request_start_elapsed_seconds"] = .49
    rewrite(root, report)
    with pytest.raises(ValueError, match="GLOBAL_REQUEST_TIMING_CHANGED"): verifier.verify(root, **kwargs)
    by_seq[1]["request_start_elapsed_seconds"] = .5
    by_seq[1]["request_sequence"], by_seq[2]["request_sequence"] = 3, 2
    by_seq[1]["request_start_elapsed_seconds"], by_seq[2]["request_start_elapsed_seconds"] = 1., .5
    rewrite(root, report)
    with pytest.raises(ValueError, match="PREFLIGHT_SEQUENCE_CHANGED"): verifier.verify(root, **kwargs)


def test_unreached_preflight_pair_cannot_claim_an_extra_request(artifact):
    root, report, kwargs, _ = artifact(fail_at=pairs()[0])
    row = report["requests"][1]
    row.update(status="PENDING_NETWORK_OR_RESPONSE_ERROR", api_calls=1, request_sequence=2,
               request_start_elapsed_seconds=.5, network_request_performed=True)
    report.update(api_calls=2, elapsed_collection_seconds=.5)
    rewrite(root, report)
    with pytest.raises(ValueError, match="REQUEST_AFTER_PREFLIGHT_FAILURE"): verifier.verify(root, **kwargs)


def test_no_call_slots_have_no_http_hash_time_source_or_zero_row_claim(artifact):
    root, report, kwargs, _ = artifact(token="")
    for field, value in [("http_response_sha256", "a" * 64), ("source_rows", 0), ("request_sequence", 1),
                         ("request_start_elapsed_seconds", 0), ("reason", "empty")]:
        changed = deepcopy(report)
        changed["requests"][0][field] = value
        rewrite(root, changed)
        with pytest.raises(ValueError, match="UNREQUESTED_RESPONSE_CLAIM"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("mutation", ["remove_data", "remove_meta", "wrong_timestamp", "239_rows", "foreign_code",
    "alter_meta_sha", "different_http_sha", "fetch_after_receipt", "source_claims_confirmed", "extra_metadata"])
def test_frozen_source_loader_revalidates_each_real_pair_not_just_receipt_hash(artifact, mutation):
    root, report, kwargs, _ = artifact(1)
    paths = collector.source.paths(root, *pairs(1)[0])
    if mutation.startswith("remove"):
        paths[0 if mutation == "remove_data" else 1].unlink()
    elif mutation in {"wrong_timestamp", "239_rows", "foreign_code"}:
        data = json.loads(paths[0].read_bytes())
        if mutation == "239_rows": data["items"].pop()
        if mutation == "wrong_timestamp": data["items"][0][data["fields"].index("trade_time")] = "2026-01-06 09:30:00"
        if mutation == "foreign_code": data["items"][0][data["fields"].index("ts_code")] = "600999.SH"
        paths[0].write_bytes(verifier.canonical(data))
        meta = json.loads(paths[1].read_bytes()); meta["data_sha256"] = verifier.file_sha(paths[0])
        paths[1].write_bytes(verifier.canonical(meta))
    else:
        meta = json.loads(paths[1].read_bytes())
        if mutation == "alter_meta_sha": meta["data_sha256"] = "c" * 64
        if mutation == "different_http_sha": meta["response_body_sha256"] = "c" * 64
        if mutation == "fetch_after_receipt": meta["fetched_at_utc"] = "2026-09-14T12:00:00Z"
        if mutation == "source_claims_confirmed": meta["provider_timestamp_semantics_confirmed"] = True
        if mutation == "extra_metadata": meta["extra"] = "wrong"
        paths[1].write_bytes(verifier.canonical(meta))
    rewrite(root, report)
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("extra", ["file", "directory", "symlink", "hardlink", "fifo", "case_alias"])
def test_pristine_namespace_forbids_aliases_extra_or_special_nodes(artifact, extra):
    root, report, kwargs, _ = artifact(1)
    target = root / "extra"
    if extra == "file": target.write_bytes(b"unregistered")
    if extra == "directory": target.mkdir()
    if extra == "symlink": target.symlink_to(root / verifier.PLAN)
    if extra == "hardlink": os.link(root / verifier.PLAN, target)
    if extra == "fifo": os.mkfifo(target)
    if extra == "case_alias":
        # Two-step rename also changes the actual directory entry on case-folding macOS.
        (root / verifier.PLAN).rename(target)
        target.rename(root / verifier.PLAN.upper())
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


def test_wrong_root_symlink_and_registered_plan_hardlink_rejected(artifact, tmp_path):
    root, _, kwargs, _ = artifact(1)
    alias = tmp_path / "alias"; alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="UNSAFE_COLLECTION_ROOT"): verifier.verify(alias, **kwargs)
    os.link(kwargs["registered_plan_path"], tmp_path / "linked-plan")
    with pytest.raises(ValueError, match="NONHARDLINK"): verifier.verify(root, **kwargs)


def test_authority_is_private_deeply_immutable_and_revalidated_after_any_change(artifact):
    root, report, kwargs, _ = artifact(1)
    with pytest.raises(ValueError, match="PRIVATE_CONSTRUCTION"): verifier.VerifiedMinuteCollection()
    value = verifier.verify(root, **kwargs)
    with pytest.raises(FrozenInstanceError): value.status = "forged"
    with pytest.raises(TypeError): value.plan["max_api_calls"] = 999
    with pytest.raises(TypeError): value.source_bindings[0]["sha256"] = "c" * 64
    assert type(value.plan["pairs"]) is tuple and type(value.plan["pairs"][0]) is tuple
    (root / verifier.JOURNAL).write_bytes(b"")
    with pytest.raises(ValueError, match="OUTPUT_CHANGED"): value.assert_unchanged()
    with pytest.raises(ValueError): verifier.reload_verified(value)
    with pytest.raises(ValueError, match="EXACT_VERIFIED"): verifier.reload_verified({"accepted": True})


def test_execution_code_hashes_and_post_verification_code_mutation_are_guarded(artifact, monkeypatch):
    root, report, kwargs, _ = artifact(1)
    actual = verifier.file_sha
    report["execution_file_bindings"]["work/profit_1000_upgrade/minute_gap_collect.py"] = "c" * 64
    rewrite(root, report)
    with pytest.raises(ValueError, match="EXECUTION_CODE_SHA_CHANGED"): verifier.verify(root, **kwargs)
    report["execution_file_bindings"]["work/profit_1000_upgrade/minute_gap_collect.py"] = actual(Path(collector.__file__))
    rewrite(root, report)
    value = verifier.verify(root, **kwargs)
    monkeypatch.setattr(verifier, "file_sha", lambda p: "d" * 64 if Path(p).name == "minute_gap_collect.py" else actual(p))
    with pytest.raises(ValueError, match="VERIFIED_MINUTE_CODE_CHANGED"): value.assert_unchanged()


def test_registered_plan_mutation_after_acceptance_invalidates_authority(artifact):
    root, _, kwargs, _ = artifact(1)
    value = verifier.verify(root, **kwargs)
    kwargs["registered_plan_path"].write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="REGISTERED_PLAN_CHANGED"): value.assert_unchanged()


def test_verifier_never_collects_trains_rebuilds_writes_or_uses_network(artifact, monkeypatch):
    root, _, kwargs, _ = artifact(1)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs): raise AssertionError("network/write/collection forbidden")
    monkeypatch.setattr(collector, "run_collection", forbidden)
    monkeypatch.setattr(collector, "official_call", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    value = verifier.verify(root, **kwargs)
    assert value.source_only and not value.label_source_eligible
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_cli_requires_external_identity_and_only_prints_read_only_acceptance(artifact):
    root, _, kwargs, _ = artifact(1)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    command = [sys.executable, "-B", str(Path(verifier.__file__)), "--output", str(root),
        "--registered-plan", str(kwargs["registered_plan_path"]), "--expected-plan-sha256", kwargs["expected_plan_sha256"],
        "--expected-label-report-sha256", LABEL_SHA, "--expected-run-id", RUN, "--expected-run-commit", COMMIT]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["successful_pairs"] == 1
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    result = subprocess.run(command[:-2], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "--expected-run-commit" in result.stderr
