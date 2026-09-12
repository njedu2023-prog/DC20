"""Offline latency-report contracts, not a cloud-archive or profitability proof.

The orchestration fixture deliberately mocks original archive acceptance and
historical reconstruction. Real v2 label/minute fixtures separately exercise
source verification and the pinned exit pair without any model fitting.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import zipfile

import pytest

from work.profit_1000_upgrade import candidate, labels, latency_report as report, minute_truth, policy_v2, run
from work.profit_1000_upgrade.test_labels import CODE, D, NEXT, T, T1, binding, case, daily
from work.profit_1000_upgrade.test_labels_v2 import v2_case


def _pair_row(day=D, rank=1, status="BOTH_SETTLED", baseline=-.02, delayed=-.03):
    return {"signal_date": day, "ts_code": CODE, "promotion_rank": rank,
            "pair_status": status, "baseline_net_45bp": baseline,
            "delayed_net_45bp": delayed,
            "baseline_net_90bp": None if baseline is None else baseline - .0045,
            "delayed_net_90bp": None if delayed is None else delayed - .0045}


def _label(status="PENDING_T", *, rank=1):
    return {"signal_date": D, "ts_code": CODE, "promotion_rank": rank,
            "exec_date": T, "scheduled_exit_date": T1, "label_status": status,
            "slot_net_return": None, "proxy_fill": None}


def _forbidden(*args, **kwargs):
    raise AssertionError("read-only sensitivity must not write, train or network")


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_summary_uses_only_paired_settled_denominator_and_keeps_negative_results():
    rows = [_pair_row(baseline=.01, delayed=-.01),
            _pair_row(rank=2, baseline=-.03, delayed=.02),
            _pair_row(rank=3, status="KNOWN_NO_FILL", baseline=0, delayed=0),
            _pair_row(day=T, status="DELAYED_UNRESOLVED", baseline=.8, delayed=None),
            _pair_row(day=T, status="BASELINE_UNRESOLVED", baseline=None, delayed=None)]
    frozen = copy.deepcopy(rows)
    out = report._summary(rows)
    assert rows == frozen
    assert out["rows"] == 5 and out["paired_settled_rows"] == 2
    assert out["known_no_fill_rows"] == 1 and out["unresolved_rows"] == 2
    assert not out["sample_complete"] and out["complete_D_dates"] == 1
    assert out["total_D_dates"] == 2 and out["account_NAV_computed"] is False
    stats = out["cost_sensitivity"]["45bp"]
    assert stats["baseline_mean_net"] == pytest.approx(-.01)
    assert stats["delayed_mean_net"] == pytest.approx(.005)
    assert stats["mean_change"] == pytest.approx(.015)
    assert stats["positive_to_nonpositive"] == stats["nonpositive_to_positive"] == 1
    assert out["cost_sensitivity"]["90bp"]["baseline_mean_net"] == pytest.approx(-.0145)
    assert out["mean_denominator"] == "PAIRED_SETTLED_SUBSET_ONLY_EXCLUDES_NO_FILL_AND_UNRESOLVED"


@pytest.mark.parametrize("rows,complete,days,nofill", [
    ([], False, 0, 0),
    ([_pair_row(status="KNOWN_NO_FILL", baseline=0, delayed=0)], True, 1, 1),
    ([_pair_row(status="BASELINE_UNRESOLVED", baseline=None, delayed=None)], False, 0, 0),
])
def test_summary_empty_or_no_settled_rows_do_not_invent_means(rows, complete, days, nofill):
    result = report._summary(rows)
    assert result["sample_complete"] is complete
    assert result["complete_D_dates"] == days and result["known_no_fill_rows"] == nofill
    for stats in result["cost_sensitivity"].values():
        assert stats["paired_settled_rows"] == 0
        assert stats["baseline_mean_net"] is stats["delayed_mean_net"] is stats["mean_change"] is None
        assert stats["positive_to_nonpositive"] == stats["nonpositive_to_positive"] == 0


@pytest.mark.parametrize("baseline,delayed,down,up", [
    (.01, 0, 1, 0), (0, .01, 0, 1), (0, -.01, 0, 0),
    (-.01, 0, 0, 0), (0, 0, 0, 0), (-.01, .01, 0, 1),
])
def test_summary_zero_boundary_is_explicit(baseline, delayed, down, up):
    result = report._summary([_pair_row(baseline=baseline, delayed=delayed)])
    stats = result["cost_sensitivity"]["45bp"]
    assert stats["positive_to_nonpositive"] == down
    assert stats["nonpositive_to_positive"] == up
    assert result["sample_complete"] and result["complete_D_dates"] == 1


def test_source_callbacks_read_exact_bound_prices_and_minutes(v2_case):
    root, manifest = v2_case
    rebuilt = labels.build_labels(root, manifest, as_of_date=T1)
    allowed = {b["path"]: b for b in rebuilt["source_files"]}
    used = {}
    callbacks = report._source_callbacks(root, allowed, used, [D, T, T1], T1, CODE)
    assert callbacks[0](T)["close"] == "10"
    assert callbacks[1](T1)["up_limit"] == "11"
    payload = callbacks[2](T1)
    assert payload["time_semantics"] == minute_truth.TIME_SEMANTICS
    assert payload["provider_timestamp_semantics_confirmed"] is False
    assert {b["path"] for b in payload["source_files"]} <= set(used)
    assert set(used) <= set(allowed)


@pytest.mark.parametrize("day", ["2026-09-14", "20260912", "bogus", NEXT])
@pytest.mark.parametrize("callback_index", [0, 1, 2])
def test_source_callbacks_reject_noncalendar_or_future_date_before_read(v2_case, monkeypatch, day, callback_index):
    root, _ = v2_case
    monkeypatch.setattr(report.settlement, "_find_market_file", _forbidden)
    monkeypatch.setattr(report.minute_truth, "load", _forbidden)
    callbacks = report._source_callbacks(root, {}, {}, [D, T, T1, NEXT], T1, CODE)
    with pytest.raises(ValueError, match="OUT_OF_SCOPE_LATENCY_DATE"):
        callbacks[callback_index](day)


@pytest.mark.parametrize("callback_index", [0, 1, 2])
def test_source_callbacks_do_not_accept_unbound_prices(v2_case, callback_index):
    root, _ = v2_case
    callbacks = report._source_callbacks(root, {}, {}, [T, T1], T1, CODE)
    with pytest.raises(ValueError, match="LATENCY_SOURCE_NOT_IN_VERIFIED_COLLECTION"):
        callbacks[callback_index](T1)


@pytest.mark.parametrize("kind", ["wrong_sha", "mutated_cached_daily", "mutated_minutes", "symlink_daily"])
def test_source_callbacks_reverify_hashes_and_safe_paths(v2_case, kind):
    root, manifest = v2_case
    rebuilt = labels.build_labels(root, manifest, as_of_date=T1)
    allowed = {b["path"]: b for b in rebuilt["source_files"]}
    callbacks = report._source_callbacks(root, allowed, {}, [T, T1], T1, CODE)
    daily_path = root / f"data/market/raw/2026/{T1}/daily.csv"
    if kind == "wrong_sha":
        key = daily_path.relative_to(root).as_posix()
        allowed[key] = dict(allowed[key], sha256="0" * 64)
    elif kind == "mutated_cached_daily":
        assert callbacks[0](T1)
        daily_path.write_bytes(daily_path.read_bytes() + b"\n")
    elif kind == "mutated_minutes":
        assert callbacks[2](T1)
        path = minute_truth.paths(root, T1, CODE)[0]
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        renamed = daily_path.with_name("other.csv")
        daily_path.rename(renamed)
        daily_path.symlink_to(renamed)
    with pytest.raises((ValueError, report.settlement.ExecutableProfitSettlementError)):
        callbacks[2 if kind == "mutated_minutes" else 0](T1)


@pytest.mark.parametrize("semantics", ["BAR_START", "PROVIDER_CONFIRMED", None])
def test_source_callbacks_reject_changed_minute_semantics(v2_case, monkeypatch, semantics):
    root, _ = v2_case
    payload = {"time_semantics": semantics, "provider_timestamp_semantics_confirmed": False,
               "source_files": []}
    monkeypatch.setattr(report.minute_truth, "load", lambda *args: payload)
    with pytest.raises(ValueError, match="LATENCY_MINUTE_SEMANTICS_CHANGED"):
        report._source_callbacks(root, {}, {}, [T1], T1, CODE)[2](T1)


def test_source_callbacks_reject_new_confirmation_flag(v2_case, monkeypatch):
    root, _ = v2_case
    monkeypatch.setattr(report.minute_truth, "load", lambda *args: {
        "time_semantics": minute_truth.TIME_SEMANTICS,
        "provider_timestamp_semantics_confirmed": True, "source_files": []})
    with pytest.raises(ValueError, match="LATENCY_MINUTE_SEMANTICS_CHANGED"):
        report._source_callbacks(root, {}, {}, [T1], T1, CODE)[2](T1)


def test_source_callbacks_reject_changed_used_binding(v2_case):
    root, manifest = v2_case
    allowed = {b["path"]: b for b in labels.build_labels(root, manifest, as_of_date=T1)["source_files"]}
    path = f"data/market/raw/2026/{T}/daily.csv"
    used = {path: {"path": path, "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="LATENCY_SOURCE_CHANGED"):
        report._source_callbacks(root, allowed, used, [T], T, CODE)[0](T)


def test_source_callbacks_absent_evidence_stays_none(tmp_path):
    callbacks = report._source_callbacks(tmp_path, {}, {}, [T], T, CODE)
    assert [callback(T) for callback in callbacks] == [None, None, None]


def _small_archive(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    marker = {"schema_version": "dc20_profit_1000_research_mirror_v2", "plan_version": "v2",
              "production_writes": False, "plan_sha256": run.sha(run.plan_path("v2")),
              **dict(policy_v2.CONTRACT)}
    files = {".dc20-profit-1000-research-root.json": json.dumps(marker).encode(),
             "research_inputs/evidence.json": b'{"fixture":true}'}
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("research_inputs/", b"")
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            handle.writestr(relative, body)
    return root, archive, files


def test_verified_mirror_matches_every_archived_byte_without_extracting(tmp_path):
    root, archive, files = _small_archive(tmp_path)
    extra = root / "unbound.json"
    extra.write_text('{"not_a_price_binding":true}')
    before = _snapshot(tmp_path)
    resolved, bindings = report._verified_mirror(root, archive)
    assert resolved == root.resolve() and set(bindings) == set(files)
    assert all(b == {"path": name, "sha256": hashlib.sha256(files[name]).hexdigest()}
               for name, b in bindings.items())
    assert extra.name not in bindings and _snapshot(tmp_path) == before


@pytest.mark.parametrize("kind", ["different_bytes", "missing_file", "file_symlink", "directory_symlink", "invalid_marker"])
def test_verified_mirror_rejects_missing_mutated_or_aliased_source(tmp_path, kind):
    root, archive, _ = _small_archive(tmp_path)
    path = root / "research_inputs/evidence.json"
    if kind == "different_bytes":
        path.write_text('{"fixture":false}')
    elif kind == "missing_file":
        path.unlink()
    elif kind == "file_symlink":
        other = root / "duplicate.json"
        path.rename(other)
        path.symlink_to(other)
    elif kind == "directory_symlink":
        original = path.parent
        renamed = root / "other"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
    else:
        marker = root / ".dc20-profit-1000-research-root.json"
        value = json.loads(marker.read_text())
        value["production_writes"] = True
        marker.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        report._verified_mirror(root, archive)


def test_read_json_requires_audited_binding_and_json_object(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="JSON_NOT_IN_AUDITED_ARCHIVE"):
        report._read_json(tmp_path, {}, path.name)
    bindings = {path.name: binding(tmp_path, path)}
    with pytest.raises(ValueError, match="JSON_OBJECT_REQUIRED"):
        report._read_json(tmp_path, bindings, path.name)


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}'])
def test_read_json_rejects_ambiguous_or_nonfinite_evidence(tmp_path, body):
    path = tmp_path / "evidence.json"
    path.write_bytes(body)
    with pytest.raises(ValueError):
        report._read_json(tmp_path, {path.name: binding(tmp_path, path)}, path.name)


def test_pair_rows_preserves_every_candidate_and_only_known_nofill_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_source_callbacks", _forbidden)
    rows = []
    for rank, status in enumerate(sorted(policy_v2.NO_FILL_STATUSES), start=1):
        rows.append(dict(_label(status, rank=rank), slot_net_return=0, proxy_fill=0))
    rows += [_label("PENDING_T", rank=5), _label("PENDING_EXIT_MISSING_MINUTES", rank=6)]
    original = copy.deepcopy(rows)
    paired, used = report._pair_rows(tmp_path, {"rows": rows}, {}, [D, T, T1], T1)
    assert rows == original and used == {}
    assert len(paired) == len(rows)
    assert [row["promotion_rank"] for row in paired] == list(range(1, 7))
    for row in paired[:4]:
        assert row["pair_status"] == "KNOWN_NO_FILL" and row["comparison"] is None
        assert all(row[key] == 0 for key in ("baseline_net_45bp", "delayed_net_45bp", "baseline_net_90bp", "delayed_net_90bp"))
    for row in paired[4:]:
        assert row["pair_status"] == "BASELINE_UNRESOLVED"
        assert all(row[key] is None for key in ("baseline_net_45bp", "delayed_net_45bp", "baseline_net_90bp", "delayed_net_90bp"))


@pytest.mark.parametrize("status,slot,fill", [
    ("NO_FILL_UNKNOWN_SOURCE", 0, 0), ("NO_FILL_PRICE_UNCERTAIN", None, None),
    ("PENDING_T", 0, None), ("PENDING_EXIT_MISSING_MINUTES", -.03, 1),
    ("invented", None, None), ("NO_FILL_CAPACITY", None, 0), ("NO_FILL_CAPACITY", 0, 1),
])
def test_pair_rows_rejects_unknown_nofill_or_false_zero(tmp_path, status, slot, fill):
    row = dict(_label(status), slot_net_return=slot, proxy_fill=fill)
    with pytest.raises(ValueError):
        report._pair_rows(tmp_path, {"rows": [row]}, {}, [D, T, T1], T1)


def test_real_v2_label_and_latency_pair_retain_negative_price_proxy(v2_case):
    root, manifest = v2_case
    rebuilt = labels.build_labels(root, manifest, as_of_date=T1)
    allowed = {b["path"]: b for b in rebuilt["source_files"]}
    before = _snapshot(root)
    paired, used = report._pair_rows(root, rebuilt, allowed, [D, T, T1, NEXT], T1)
    assert _snapshot(root) == before
    assert len(paired) == 1 and paired[0]["pair_status"] == "BOTH_SETTLED"
    assert paired[0]["baseline_net_45bp"] == pytest.approx(-.0245)
    assert paired[0]["delayed_net_45bp"] == pytest.approx(-.0245)
    assert paired[0]["delayed_net_90bp"] == pytest.approx(-.029)
    pair = paired[0]["comparison"]
    assert pair["baseline"]["result"] == rebuilt["rows"][0]["exit_evidence"]
    assert pair["production_activation_allowed"] is pair["actual_execution_claimed"] is False
    assert pair["source_snapshots_verified_unchanged"]
    assert used and set(used) <= set(allowed)


@pytest.fixture
def mocked_pair(v2_case, monkeypatch):
    root, manifest = v2_case
    rebuilt = labels.build_labels(root, manifest, as_of_date=T1)
    row = rebuilt["rows"][0]
    pair = {"baseline": {"status": "SETTLED", "result": copy.deepcopy(row["exit_evidence"]),
                         "net_return_45bp": row["slot_net_return"], "net_return_90bp": row["slot_net_return"] - .0045},
            "delayed": {"status": "PENDING_EXIT_MISSING_MINUTES", "result": None,
                        "net_return_45bp": None, "net_return_90bp": None}}
    monkeypatch.setattr(report, "compare_exit_latency", lambda *args: pair)
    allowed = {b["path"]: b for b in rebuilt["source_files"]}
    return root, rebuilt, allowed, pair


def test_pair_rows_keeps_stress_unresolved_not_zero(mocked_pair):
    root, rebuilt, allowed, _ = mocked_pair
    result, _ = report._pair_rows(root, rebuilt, allowed, [D, T, T1], T1)
    row = result[0]
    assert row["pair_status"] == "DELAYED_UNRESOLVED"
    assert row["baseline_net_45bp"] < 0
    assert row["delayed_net_45bp"] is row["delayed_net_90bp"] is None
    assert report._summary(result)["sample_complete"] is False


@pytest.mark.parametrize("kind", ["evidence", "net"])
def test_pair_rows_rejects_baseline_replay_drift(mocked_pair, kind):
    root, rebuilt, allowed, pair = mocked_pair
    if kind == "evidence":
        pair["baseline"]["result"]["exit_price"] = 999
    else:
        pair["baseline"]["net_return_45bp"] += .01
    with pytest.raises(ValueError, match="BASELINE_.*_REPLAY_CHANGED"):
        report._pair_rows(root, rebuilt, allowed, [D, T, T1], T1)


def test_pair_rows_cannot_lose_rebuilt_entry_daily(mocked_pair, monkeypatch):
    root, rebuilt, allowed, _ = mocked_pair
    monkeypatch.setattr(report, "_source_callbacks", lambda *args: (lambda day: None, lambda day: None, lambda day: None))
    with pytest.raises(ValueError, match="REBUILT_ENTRY_DAILY_DISAPPEARED"):
        report._pair_rows(root, rebuilt, allowed, [D, T, T1], T1)


@pytest.fixture
def orchestration_fixture(tmp_path, monkeypatch):
    """Explicit mocked upstream audit/rebuild; NOT a real archive acceptance."""
    root, archive, _ = _small_archive(tmp_path)
    manifest = {"fixture": "frozen D-only manifest"}
    source = binding(root, root / "research_inputs/evidence.json")
    rebuilt = {"fixture": "source-rebuilt label payload", "source_files": [source], "rows": []}
    for name, payload in [("research_inputs/manifest.json", manifest), ("research_results/labels.json", rebuilt)]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, sort_keys=True).encode()
        path.write_bytes(body)
        with zipfile.ZipFile(archive, "a") as handle:
            handle.writestr(name, body)
    plan = {"as_of_date": T1, "historical_evidence_role": "UNIT_FIXTURE_NOT_AUDITED_CLOUD_PROOF"}
    allowed = {source["path"]: source}
    rows = [_pair_row(rank=1), _pair_row(rank=2, baseline=.01, delayed=-.005)]
    calls = []
    def audit(*args, **kwargs):
        calls.append(("audit", args, kwargs))
        return {"unit_test_mock": True, "integrity_verified": True}
    monkeypatch.setattr(report.acceptance, "audit_archive", audit)
    # _verified_mirror remains real; only bypass its full plan loader because
    # generate_report itself receives the intentionally tiny mocked plan.
    monkeypatch.setattr(report.run, "require_research_mirror", lambda path, **kwargs: Path(path).resolve())
    monkeypatch.setattr(report.run, "load_plan", lambda version: plan)
    monkeypatch.setattr(report.run, "prepare_history", lambda *args, **kwargs: (copy.deepcopy(manifest), None, None))
    monkeypatch.setattr(report.run, "validate_collection_evidence", lambda *args, **kwargs: (allowed, {"unit_test_mock": True}))
    monkeypatch.setattr(report.labels, "build_labels", lambda *args, **kwargs: copy.deepcopy(rebuilt))
    monkeypatch.setattr(report.settlement, "_strict_open_dates", lambda root: [D, T, T1])
    monkeypatch.setattr(report, "_pair_rows", lambda *args: (copy.deepcopy(rows), allowed))
    expected = {"expected_zip_sha256": run.sha(archive), "expected_run_id": "unit-fixture-not-a-real-run",
                "expected_commit": "0" * 40}
    return {"root": root, "archive": archive, "manifest": manifest, "rebuilt": rebuilt,
            "allowed": allowed, "rows": rows, "expected": expected, "calls": calls, "source": source}


def _generate(fixture):
    return report.generate_report(fixture["root"], fixture["archive"], **fixture["expected"])


def test_generate_report_is_read_only_nontraining_and_qualified(orchestration_fixture, monkeypatch):
    fixture = orchestration_fixture
    before = _snapshot(fixture["root"].parent)
    monkeypatch.setattr(run, "write_json", _forbidden)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    monkeypatch.setattr(candidate, "run_candidate", _forbidden)
    monkeypatch.setattr(candidate, "_fit_ridge", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    result = _generate(fixture)
    assert _snapshot(fixture["root"].parent) == before
    assert result["status"] == "LATENCY_REPLAY_COMPLETE"
    assert result["source_archive_acceptance"]["unit_test_mock"]
    assert result["summary"]["paired_settled_rows"] == 2
    assert result["promotion_rank_summaries"]["1"]["rows"] == 1
    assert result["promotion_rank_summaries"]["3"]["rows"] == 0
    for key in ("production_activation_allowed", "actual_execution_claimed", "profitability_improvement_proven",
                "forward_holdout_evaluated", "candidate_fitted_or_reranked", "decision_rule_changed",
                "provider_timestamp_semantics_confirmed", "exit_capacity_verified", "BAR_START_interpretation_tested"):
        assert result[key] is False
    assert result["not_bar_start_validation"] is True and result["execution_delay_seconds"] == 60
    assert result["historical_role"] == "UNIT_FIXTURE_NOT_AUDITED_CLOUD_PROOF"
    assert not any("html" in key.lower() or "frontend" in key.lower() for key in result)
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    _, args, kwargs = fixture["calls"][0]
    assert args == (fixture["archive"],) and kwargs == fixture["expected"]


def test_generate_report_unresolved_status_preserved(orchestration_fixture):
    fixture = orchestration_fixture
    fixture["rows"].append(_pair_row(status="BASELINE_UNRESOLVED", baseline=None, delayed=None))
    result = _generate(fixture)
    assert result["status"] == "LATENCY_REPLAY_WITH_UNRESOLVED_ROWS"
    assert result["summary"]["unresolved_rows"] == 1
    assert result["summary"]["sample_complete"] is False


def test_generate_report_original_audit_must_succeed_first(orchestration_fixture, monkeypatch):
    def rejected(*args, **kwargs):
        raise ValueError("ORIGINAL_ARCHIVE_REJECTED")
    monkeypatch.setattr(report.acceptance, "audit_archive", rejected)
    monkeypatch.setattr(report, "_verified_mirror", _forbidden)
    with pytest.raises(ValueError, match="ORIGINAL_ARCHIVE_REJECTED"):
        _generate(orchestration_fixture)


def test_generate_report_reconstruction_manifest_must_match(orchestration_fixture, monkeypatch):
    monkeypatch.setattr(run, "prepare_history", lambda *args, **kwargs: ({"changed": True}, None, None))
    with pytest.raises(ValueError, match="FROZEN_D_FEATURE_RECONSTRUCTION_CHANGED"):
        _generate(orchestration_fixture)


def test_generate_report_collection_bindings_must_exist_in_archive(orchestration_fixture):
    fixture = orchestration_fixture
    fixture["allowed"]["unarchived.csv"] = {"path": "unarchived.csv", "sha256": "0" * 64}
    with pytest.raises(ValueError, match="COLLECTION_BINDING_NOT_IN_AUDITED_ARCHIVE"):
        _generate(fixture)


def test_generate_report_saved_labels_must_equal_source_rebuild(orchestration_fixture):
    fixture = orchestration_fixture
    fixture["rebuilt"]["drift"] = True
    with pytest.raises(ValueError, match="SOURCE_REBUILT_LABELS_DIFFER_FROM_ARCHIVE"):
        _generate(fixture)


def test_generate_report_rebuilt_baseline_sources_cannot_be_outside_archive(orchestration_fixture, monkeypatch):
    fixture = orchestration_fixture
    revised = dict(fixture["rebuilt"], source_files=[{"path": "unarchived.csv", "sha256": "0" * 64}])
    monkeypatch.setattr(report.labels, "build_labels", lambda *args, **kwargs: revised)
    original = report._read_json
    monkeypatch.setattr(report, "_read_json", lambda root, bindings, path:
                        copy.deepcopy(revised) if path == "research_results/labels.json" else original(root, bindings, path))
    with pytest.raises(ValueError, match="BASELINE_PRICE_OUTSIDE_AUDITED_ARCHIVE"):
        _generate(fixture)


@pytest.mark.parametrize("kind", ["source", "archive"])
def test_generate_report_end_of_replay_rechecks_all_source_and_zip_bytes(orchestration_fixture, monkeypatch, kind):
    fixture = orchestration_fixture
    def changed_during_pair(*args):
        if kind == "source":
            path = fixture["root"] / fixture["source"]["path"]
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            path = fixture["archive"]
            path.write_bytes(path.read_bytes() + b"changed_after_verification")
        return fixture["rows"], fixture["allowed"]
    monkeypatch.setattr(report, "_pair_rows", changed_during_pair)
    with pytest.raises(ValueError, match="source SHA mismatch|ARCHIVE_CHANGED_DURING_REPLAY"):
        _generate(fixture)


@pytest.mark.parametrize("when", ["before", "during"])
def test_generate_report_refuses_implementation_drift_without_editing_repository(orchestration_fixture, monkeypatch, when):
    fixture = orchestration_fixture
    original_sha = run.sha
    target = report._IMPLEMENTATIONS[1]
    changed = [when == "before"]
    def synthetic_changed_sha(path):
        if Path(path) == target and changed[0]:
            return "0" * 64
        return original_sha(path)
    monkeypatch.setattr(run, "sha", synthetic_changed_sha)
    if when == "before":
        monkeypatch.setattr(report.acceptance, "audit_archive", _forbidden)
    else:
        def pair_then_change(*args):
            changed[0] = True
            return fixture["rows"], fixture["allowed"]
        monkeypatch.setattr(report, "_pair_rows", pair_then_change)
    message = "LATENCY_IMPLEMENTATION_CHANGED_SINCE_IMPORT" if when == "before" else "LATENCY_IMPLEMENTATION_CHANGED_DURING_REPLAY"
    with pytest.raises(ValueError, match=message):
        _generate(fixture)


def test_cli_disables_project_bytecode_before_local_imports(tmp_path):
    """The help-only subprocess must not create project-source pycache files."""
    cache = tmp_path / "external-bytecode-cache"
    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(cache))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-c",
                             "import runpy,sys; sys.dont_write_bytecode=False; "
                             "sys.argv=[sys.argv[1],'--help']; runpy.run_path(sys.argv[0],run_name='__main__')",
                             str(Path(report.__file__).resolve())],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--expected-zip-sha256" in result.stdout
    # Startup stdlib caches in the explicitly isolated prefix are allowed;
    # the implementation must stop caches before importing any project code.
    paths = [p.as_posix() for p in cache.rglob("*.pyc")]
    assert not any("/work/profit_1000_upgrade/" in p or "/src/top10decision/" in p for p in paths)
