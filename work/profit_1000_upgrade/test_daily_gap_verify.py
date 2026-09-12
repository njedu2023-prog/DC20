"""Synthetic receipt/source attacks; no fixture is actual market authority."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from work.profit_1000_upgrade import daily_gap_collect as collector, daily_gap_verify as verifier

RUN, COMMIT = "123456789", "a" * 40
REAL_BUDGET = collector.RequestBudget


class Clock:
    value = 0.
    def __call__(self): return self.value
    def sleep(self, delay): self.value += delay


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None): return datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def response(contract, *, daily_row=False, event="S", timing=None):
    api, params, fields = contract["api_name"], contract["params"], contract["fields"]
    item = [params["ts_code"], params["trade_date"], 10, 10.2, 9.8, 10, 10, 100, 1000, 0]
    items = [item] if api == "daily" and daily_row else [] if api == "daily" or event is None else [
        [params["ts_code"], params["trade_date"], timing, event]]
    return json.dumps({"code": 0, "detail": "...", "msg": "", "data": {
        "fields": fields, "items": items, "count": 0, "has_more": False}}).encode()


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", RUN); monkeypatch.setenv("GITHUB_SHA", COMMIT)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(collector, "datetime", FixedDatetime)
    count = 0
    def make(*, token="synthetic-not-real", failures=None, daily_row=False, event="S", timing=None,
             expire_after=None):
        nonlocal count
        count += 1
        parent = tmp_path / str(count); parent.mkdir()
        plan_path = parent / "registered.json"
        plan_path.write_bytes(collector.json_bytes(collector.expected_plan()))
        plan_sha = verifier.file_sha(plan_path)
        clock, seen = Clock(), []
        monkeypatch.setattr(collector, "RequestBudget", lambda: REAL_BUDGET(clock=clock, sleep=clock.sleep))
        def transport(contract, supplied_token):
            assert supplied_token == token
            pair = (contract["api_name"], contract["params"]["trade_date"], contract["params"]["ts_code"])
            seen.append(pair)
            ordinal = len(seen)
            if expire_after == ordinal: clock.value = 600.
            failure = (failures or {}).get(ordinal)
            if failure in {"HTTP_ERROR", "TRANSPORT_ERROR", "TRANSPORT_TIMEOUT", "HTTP_RESPONSE_BYTE_LIMIT"}:
                raise collector.TransportFailure(failure)
            if failure == "invalid": return b'{"code":-1,"msg":"synthetic server text"}'
            return response(contract, daily_row=daily_row, event=event, timing=timing)
        monkeypatch.setattr(collector, "official_call", transport)
        root = parent / "output"
        report = collector.run_collection(root, expected_plan_sha256=plan_sha,
            expected_label_report_sha256=verifier.PRIOR_LABEL_SHA, expected_run_id=RUN,
            expected_run_commit=COMMIT, token=token, plan_path=plan_path)
        kwargs = dict(expected_run_id=RUN, expected_run_commit=COMMIT, expected_plan_sha256=plan_sha,
            expected_label_report_sha256=verifier.PRIOR_LABEL_SHA, registered_plan_path=plan_path)
        return root, report, kwargs, seen
    return make


def reseal(root, report, *, journal=True):
    """Update test outer bindings; deeper identity and source checks must fail."""
    files = {}
    for path in root.rglob("*"):
        if path.is_file() and path.name != verifier.RECEIPT:
            name = path.relative_to(root).as_posix()
            files[name] = {"path": name, "sha256": verifier.file_sha(path), "bytes": path.stat().st_size}
    for item in report["requests"]:
        item["source_files"] = [files.get(b["path"], b) for b in item["source_files"]]
    report["source_files"] = sorted((b for r in report["requests"] for b in r["source_files"]), key=lambda b: b["path"])
    if journal:
        raw = b"".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n"
                       for r in report["requests"] if r["api_calls"])
        (root / verifier.JOURNAL).write_bytes(raw)
        files[verifier.JOURNAL] = {"path": verifier.JOURNAL, "sha256": verifier.file_sha(root / verifier.JOURNAL), "bytes": len(raw)}
    report["output_file_bindings"] = [files[p] for p in sorted(files)]
    (root / verifier.RECEIPT).write_bytes(verifier.canonical(report))


def edit_source(root, report, ordinal, mutate_data=None, mutate_meta=None):
    pair = verifier.PAIRS[ordinal - 1]
    data_path, meta_path = (root / p for p in verifier.source_paths(pair))
    if mutate_data:
        data = json.loads(data_path.read_bytes()); mutate_data(data)
        data_path.write_bytes(verifier.canonical(data))
    meta = json.loads(meta_path.read_bytes())
    meta.update(data_sha256=verifier.file_sha(data_path), data_bytes=data_path.stat().st_size)
    if mutate_meta: mutate_meta(meta)
    meta_path.write_bytes(verifier.canonical(meta))
    reseal(root, report)


def test_independent_registered_plan_exact_without_collector_calls(monkeypatch):
    expected = collector.expected_plan()
    monkeypatch.setattr(collector, "expected_plan", lambda: pytest.fail("collector contract was trusted"))
    assert verifier.expected_plan() == expected
    assert len(verifier.PAIRS) == 14 and sum(p[0] == "daily" for p in verifier.PAIRS) == 13
    assert verifier.PRIOR_LABEL_SHA == "2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609"
    assert verifier.DIAGNOSTIC_ARCHIVE_SHA == "760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9"


@pytest.mark.parametrize("daily_row,event,timing", [(False, "S", None), (True, "R", None),
    (False, "S", "09:30-10:00"), (False, "S", ""), (False, None, None)])
def test_all_four_source_statuses_preserve_values_never_grant_nontrading(artifact, daily_row, event, timing):
    root, report, kwargs, seen = artifact(daily_row=daily_row, event=event, timing=timing)
    value = verifier.verify(root, **kwargs)
    assert type(value) is verifier.VerifiedDailyGapCollection
    assert value.gap_pairs == value.successful_pairs == verifier.PAIRS
    assert not value.failed_pairs and not value.unattempted_pairs and len(seen) == 14
    assert len(value.source_bindings) == 28 and len(value.records) == 14
    assert value.receipt_sha256 == verifier.file_sha(root / verifier.RECEIPT)
    assert value.plan_sha256 == kwargs["expected_plan_sha256"]
    assert value.prior_label_report_sha256 == verifier.PRIOR_LABEL_SHA
    assert value.diagnostic_archive_sha256 == verifier.DIAGNOSTIC_ARCHIVE_SHA
    assert value.as_of_date == "20260911" and value.run_id == RUN and value.run_commit == COMMIT
    assert value.source_only and not value.label_source_eligible and not value.nontrading_session_qualified
    assert value.status == "DAILY_GAP_SOURCES_COLLECTED"
    for pair, record in value.records.items():
        assert tuple(record[k] for k in ("api_name", "trade_date", "ts_code")) == pair
        assert record["complete_empty"] is (len(record["rows"]) == 0)
        assert record["table"]["has_more"] is False
        assert record["source_only"] is True
        for key in ("label_source_eligible", "nontrading_session_qualified", "market_absence_verified", "can_advance_holding_day", "settlement_allowed"):
            assert record[key] is False
    if event is not None:
        assert value.records[verifier.PAIRS[1]]["rows"][0]["suspend_timing"] == timing
    value.assert_unchanged()
    assert verifier.reload_verified(value) == value


def test_identical_empty_table_hashes_require_separate_exact_metadata(artifact):
    root, report, kwargs, _ = artifact()
    empty = [r for r in report["requests"] if r["status"] == "DAILY_EMPTY_SOURCE_WRITTEN"]
    assert len({r["source_files"][0]["sha256"] for r in empty}) == 1
    assert len({r["source_files"][1]["sha256"] for r in empty}) == 13
    assert len(verifier.verify(root, **kwargs).successful_pairs) == 14
    # Empty data is indistinguishable; copying another session's metadata is not.
    left, right = empty[1]["source_files"][1]["path"], empty[2]["source_files"][1]["path"]
    (root / right).write_bytes((root / left).read_bytes()); reseal(root, report)
    with pytest.raises(ValueError, match="SOURCE_METADATA_HTTP_REQUEST_OR_SHA_CHANGED"):
        verifier.verify(root, **kwargs)


@pytest.mark.parametrize("failure", ["HTTP_ERROR", "TRANSPORT_ERROR", "TRANSPORT_TIMEOUT", "HTTP_RESPONSE_BYTE_LIMIT", "invalid"])
def test_failed_request_is_explicit_without_source_or_zero_and_no_retry(artifact, failure):
    root, report, kwargs, seen = artifact(failures={3: failure})
    value = verifier.verify(root, **kwargs)
    assert len(seen) == 14 and value.failed_pairs == (verifier.PAIRS[2],)
    assert len(value.successful_pairs) == 13 and value.status == "DAILY_GAP_SOURCES_PARTIAL"
    assert report["requests"][2]["source_files"] == [] and report["requests"][2]["source_rows"] is None
    assert verifier.PAIRS[2] not in value.records


def test_all_failed_is_blocked_source_only(artifact):
    root, _, kwargs, seen = artifact(failures={i: "HTTP_ERROR" for i in range(1, 15)})
    value = verifier.verify(root, **kwargs)
    assert len(seen) == 14 and not value.records and value.failed_pairs == verifier.PAIRS
    assert value.status == "DAILY_GAP_SOURCES_BLOCKED"


def test_no_token_keeps_all_fourteen_unattempted_requires_external_identity(artifact):
    root, report, kwargs, seen = artifact(token="")
    value = verifier.verify(root, **kwargs)
    assert report["api_calls"] == 0 and not seen and not value.successful_pairs
    assert value.unattempted_pairs == value.failed_pairs == verifier.PAIRS
    kwargs["expected_run_id"] = None
    with pytest.raises(ValueError, match="EXTERNAL_RUN_IDENTITY_REQUIRED"): verifier.verify(root, **kwargs)


def test_wall_budget_partial_still_preserves_all_planned_slots(artifact):
    root, report, kwargs, seen = artifact(expire_after=3)
    value = verifier.verify(root, **kwargs)
    assert len(seen) == report["api_calls"] == 3
    assert value.successful_pairs == verifier.PAIRS[:3] and value.unattempted_pairs == verifier.PAIRS[3:]
    assert len(report["requests"]) == 14


@pytest.mark.parametrize("name,value", [("expected_run_id", "999"), ("expected_run_commit", "c" * 40),
    ("expected_run_id", None), ("expected_run_commit", True), ("expected_run_id", "１２３"),
    ("expected_plan_sha256", "c" * 64), ("expected_label_report_sha256", "c" * 64)])
def test_external_identity_and_registration_cannot_be_self_attested(artifact, name, value):
    root, _, kwargs, _ = artifact()
    kwargs[name] = value
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("retries", 1), ("max_api_calls", 15), ("max_workers", 2),
    ("request_start_budget_seconds", 601), ("transport_wall_timeout_seconds", 21),
    ("daily_empty_rule", "SUSPENDED"), ("nontrading_session_qualified", True),
    ("label_report_sha256", "c" * 64), ("diagnostic_archive", {}), ("requests", []),
    ("maximum_signal_date", "20260914"), ("source_namespace", "data/market/raw")])
def test_rehashed_registration_cannot_change_fixed_contract(artifact, key, value):
    root, report, kwargs, _ = artifact()
    plan = json.loads((root / verifier.PLAN).read_bytes()); plan[key] = value
    raw = verifier.canonical(plan)
    (root / verifier.PLAN).write_bytes(raw); kwargs["registered_plan_path"].write_bytes(raw)
    kwargs["expected_plan_sha256"] = hashlib.sha256(raw).hexdigest(); report["plan_sha256"] = kwargs["expected_plan_sha256"]
    reseal(root, report)
    with pytest.raises(ValueError, match="REGISTERED_PLAN_CONTRACT_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key", list(verifier.FLAGS))
def test_source_only_flags_are_strict_booleans_not_truthy(artifact, key):
    root, report, kwargs, _ = artifact()
    report[key] = int(report[key]); reseal(root, report)
    with pytest.raises(ValueError, match="RECEIPT_POLICY_OR_EXTERNAL_IDENTITY_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("run_attempt", 2), ("run_id", "999"), ("as_of_date", "20260914"),
    ("status", "DAILY_GAP_SOURCES_BLOCKED"), ("qualified_source_pairs", True), ("planned_request_count", True),
    ("api_calls", True), ("api_calls", 15), ("request_status_counts", {}), ("source_files", []),
    ("execution_file_bindings", {}), ("callable_injected_for_test", True),
    ("observed_at_utc", "2026-09-10T12:00:00Z"), ("observed_at_utc", "2026-09-13T12:00:00"),
    ("elapsed_collection_seconds", -1)])
def test_receipt_counts_identity_and_source_state_reconcile(artifact, key, value):
    root, report, kwargs, _ = artifact()
    reseal(root, report)
    report[key] = value
    (root / verifier.RECEIPT).write_bytes(verifier.canonical(report))
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("ordinal", True), ("ordinal", 2), ("api_calls", True),
    ("network_request_performed", 1), ("request_sequence", 2), ("request_start_elapsed_seconds", 579),
    ("request_start_elapsed_seconds", -1), ("request_finished_elapsed_seconds", -1),
    ("http_response_bytes", True), ("http_response_bytes", 1_000_001), ("http_response_sha256", None),
    ("source_rows", 0.0), ("reason", "secret server text"), ("fetched_at_utc", None),
    ("status", "NO_FILL_SUSPENDED")])
def test_request_fields_cannot_forge_call_time_price_or_permission(artifact, key, value):
    root, report, kwargs, _ = artifact()
    report["requests"][0][key] = value; reseal(root, report)
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("mutate", [lambda c: c["params"].__setitem__("trade_date", "20240506"),
    lambda c: c["params"].__setitem__("ts_code", "600234.SH"), lambda c: c.__setitem__("api_name", "daily_basic"),
    lambda c: c["fields"].append("unexpected"), lambda c: c["params"].__setitem__("limit", 1)])
def test_every_request_is_exact_single_day_single_stock_no_pagination(artifact, mutate):
    root, report, kwargs, _ = artifact()
    mutate(report["requests"][0]["request"]); reseal(root, report)
    with pytest.raises(ValueError, match="EXACT_REGISTERED_REQUEST_CHANGED"): verifier.verify(root, **kwargs)


def test_missing_extra_or_reordered_request_rejected(artifact):
    root, report, kwargs, _ = artifact()
    for mutate in (lambda r: r.pop(), lambda r: r.append(deepcopy(r[0])), lambda r: r.reverse()):
        changed = deepcopy(report); mutate(changed["requests"]); reseal(root, changed)
        with pytest.raises(ValueError): verifier.verify(root, **kwargs)


def test_no_call_slots_cannot_claim_empty_zero_rows_http_or_reason(artifact):
    root, report, kwargs, _ = artifact(token="")
    for key, value in (("http_response_sha256", "a" * 64), ("source_rows", 0), ("request_sequence", 1),
                       ("fetched_at_utc", "2026-09-13T12:00:00Z"), ("reason", "empty")):
        changed = deepcopy(report); changed["requests"][0][key] = value; reseal(root, changed)
        with pytest.raises(ValueError, match="UNREQUESTED_RESPONSE_CLAIM"): verifier.verify(root, **kwargs)


def test_global_sequential_starts_and_journal_are_independent(artifact):
    root, report, kwargs, _ = artifact()
    report["requests"][1]["request_start_elapsed_seconds"] = .49
    reseal(root, report)
    with pytest.raises(ValueError, match="GLOBAL_SEQUENTIAL_TIMING_CHANGED"): verifier.verify(root, **kwargs)
    report["requests"][1]["request_start_elapsed_seconds"] = .5; reseal(root, report)
    (root / verifier.JOURNAL).write_bytes(b""); reseal(root, report, journal=False)
    with pytest.raises(ValueError, match="REQUEST_JOURNAL_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("has_more", True), ("has_more", 0), ("count", True),
    ("count", 2), ("items", [["unexpected"]]), ("fields", [])])
def test_complete_empty_requires_strict_table_not_just_empty_hash(artifact, key, value):
    root, report, kwargs, _ = artifact()
    edit_source(root, report, 1, mutate_data=lambda d: d.__setitem__(key, value))
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("open", 0), ("open", None), ("high", 1), ("vol", -1),
    ("amount", True), ("close", "10"), ("pct_chg", "NaN"), ("trade_date", "20240429"), ("ts_code", "600234.SH")])
def test_daily_observed_price_row_revalidated_without_imputation(artifact, key, value):
    root, report, kwargs, _ = artifact(daily_row=True)
    edit_source(root, report, 1, mutate_data=lambda d: d["items"][0].__setitem__(d["fields"].index(key), value))
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("suspend_type", "N"), ("suspend_timing", 0),
    ("suspend_timing", "10:00-09:30"), ("suspend_timing", "29:00-30:00"),
    ("suspend_timing", "unknown server text"), ("trade_date", "20240506"), ("ts_code", "600234.SH")])
def test_suspend_event_identity_and_timing_cannot_be_reinterpreted(artifact, key, value):
    root, report, kwargs, _ = artifact()
    edit_source(root, report, 2, mutate_data=lambda d: d["items"][0].__setitem__(d["fields"].index(key), value))
    with pytest.raises(ValueError): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("key,value", [("ordinal", 2), ("api_code", True), ("api_code", -1),
    ("http_response_sha256", "c" * 64), ("http_response_bytes", 1), ("data_sha256", "c" * 64),
    ("data_bytes", 1), ("rows", True), ("complete_table_verified", 1), ("label_source_eligible", True),
    ("nontrading_session_qualified", True), ("status", "DAILY_ROW_SOURCE_WRITTEN"), ("extra", "server-text")])
def test_resealed_metadata_cannot_replace_actual_bound_request_or_table(artifact, key, value):
    root, report, kwargs, _ = artifact()
    edit_source(root, report, 1, mutate_meta=lambda m: m.__setitem__(key, value))
    with pytest.raises(ValueError, match="SOURCE_METADATA_HTTP_REQUEST_OR_SHA_CHANGED"): verifier.verify(root, **kwargs)


@pytest.mark.parametrize("kind", ["missing_data", "missing_meta", "noncanonical", "duplicate_json", "nonfinite_json",
    "extra_file", "extra_directory", "symlink", "hardlink", "fifo", "case_alias"])
def test_all_files_namespace_and_json_are_strict(artifact, kind):
    root, report, kwargs, _ = artifact()
    path = root / report["requests"][0]["source_files"][0]["path"]
    if kind == "missing_data": path.unlink()
    elif kind == "missing_meta": (root / report["requests"][0]["source_files"][1]["path"]).unlink()
    elif kind == "noncanonical": path.write_bytes(path.read_bytes() + b" ")
    elif kind == "duplicate_json": path.write_bytes(b'{"items":[],"items":[]}')
    elif kind == "nonfinite_json": path.write_bytes(b'{"items":[],"unexpected":1e9999}')
    elif kind == "extra_file": (root / "extra").write_bytes(b"extra")
    elif kind == "extra_directory": (root / "extra").mkdir()
    elif kind == "symlink": (root / "alias").symlink_to(path)
    elif kind == "hardlink": os.link(path, root / "alias")
    elif kind == "fifo": os.mkfifo(root / "fifo")
    elif kind == "case_alias":
        tmp = root / "rename"; (root / verifier.PLAN).rename(tmp); tmp.rename(root / verifier.PLAN.upper())
    if kind not in {"symlink", "hardlink", "fifo"}: reseal(root, report)
    with pytest.raises((ValueError, OSError)): verifier.verify(root, **kwargs)


def test_authority_private_deep_immutable_and_rechecked(artifact):
    root, _, kwargs, _ = artifact()
    with pytest.raises(ValueError, match="PRIVATE_CONSTRUCTION"): verifier.VerifiedDailyGapCollection()
    value = verifier.verify(root, **kwargs)
    with pytest.raises(FrozenInstanceError): value.status = "forged"
    with pytest.raises(TypeError): value.records[verifier.PAIRS[0]]["complete_empty"] = False
    with pytest.raises(TypeError): value.records[verifier.PAIRS[0]]["table"]["items"] = [1]
    with pytest.raises(TypeError): value.source_bindings[0]["sha256"] = "c" * 64
    assert type(value.records[verifier.PAIRS[0]]["table"]["fields"]) is tuple
    (root / verifier.JOURNAL).write_bytes(b"")
    with pytest.raises(ValueError, match="OUTPUT_CHANGED"): value.assert_unchanged()
    with pytest.raises(ValueError): verifier.reload_verified(value)
    with pytest.raises(ValueError, match="EXACT_VERIFIED"): verifier.reload_verified({"accepted": True})


def test_changed_registration_and_code_invalidate_prior_authority(artifact, monkeypatch):
    root, _, kwargs, _ = artifact()
    value = verifier.verify(root, **kwargs)
    original = kwargs["registered_plan_path"].read_bytes()
    kwargs["registered_plan_path"].write_bytes(b"{}")
    with pytest.raises(ValueError, match="REGISTERED_PLAN_CHANGED"): value.assert_unchanged()
    kwargs["registered_plan_path"].write_bytes(original)
    real = verifier.file_sha
    monkeypatch.setattr(verifier, "file_sha", lambda p: "c" * 64 if Path(p).name == "daily_gap_collect.py" else real(p))
    with pytest.raises(ValueError, match="VERIFIED_DAILY_CODE_CHANGED"): value.assert_unchanged()


def test_verifier_has_no_collector_import_and_no_network_or_writes(artifact, monkeypatch):
    root, _, kwargs, _ = artifact()
    text = Path(verifier.__file__).read_text()
    assert "import daily_gap_collect" not in text and "from work.profit_1000_upgrade import" not in text
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs): pytest.fail("independent verifier mutated or requested data")
    for name in ("run_collection", "qualified_table", "metadata", "expected_plan", "official_call"):
        monkeypatch.setattr(collector, name, forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden); monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    assert verifier.verify(root, **kwargs).source_only
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_external_identity_and_plan_arguments_match_workflow(artifact):
    root, _, kwargs, _ = artifact()
    command = [sys.executable, "-B", str(Path(verifier.__file__)), "--output", str(root),
        "--registered-plan", str(kwargs["registered_plan_path"]), "--expected-run-id", RUN,
        "--expected-run-commit", COMMIT, "--expected-plan-sha256", kwargs["expected_plan_sha256"],
        "--expected-label-report-sha256", verifier.PRIOR_LABEL_SHA]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["successful_pairs"] == 14
    assert json.loads(result.stdout)["nontrading_session_qualified"] is False
    result = subprocess.run(command[:-2], capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
