"""Independent runner boundary and real pinned historical-panel checks."""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import zipfile

import pytest

from work.profit_1000_upgrade import run
from work.profit_1000_upgrade.labels import _load_candidates
from top10decision.decision.executable_profit_shadow_settlement import _strict_open_dates


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def research_root(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".dc20-profit-1000-research-root.json").write_text(json.dumps({
        "schema_version": "dc20_profit_1000_research_mirror_v1", "production_writes": False,
        "plan_sha256": run.sha(run.HERE / "PLAN.json")}))
    return path


def archive_fixture(tmp_path, *, extra=None, member_path="candidate_sources/20221114/daily.csv"):
    files = {member_path: b"ts_code,trade_date,open,close\n600000.SH,20221114,10,10\n",
             "legacy_open_exit_labels.csv": b"signal_date,net_return\n20221111,0.99\n"}
    manifest = {"files": {name: {"bytes": len(raw), "sha256": digest(raw)} for name, raw in files.items()}}
    manifest_raw = json.dumps(manifest).encode()
    path = tmp_path / "retained.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        for name, raw in files.items():
            bundle.writestr("artifact/" + name, raw)
        bundle.writestr("artifact/artifact_manifest.json", manifest_raw)
        if extra:
            bundle.writestr(extra, b"unbound")
    spec = {"zip_sha256": digest(path.read_bytes()), "manifest_sha256": digest(manifest_raw),
            "run_id": 1, "original_status": "BLOCKED_COLLECTION"}
    return path, spec


def test_real_pinned_history_has_exact_6753_rows_and_910_complete_d_dates():
    manifest, features, evidence = run.prepare_history(run.ROOT)
    assert len(manifest["rows"]) == len(features) == 6753
    assert len(manifest["expected_candidate_codes"]) == 910
    assert min(manifest["expected_candidate_codes"]) == "20221111"
    assert max(manifest["expected_candidate_codes"]) == "20260814"
    assert sum(map(len, manifest["expected_candidate_codes"].values())) == 6753
    assert evidence["day_candidate_counts"] == {d: len(c) for d, c in manifest["expected_candidate_codes"].items()}
    assert all(len(c) == len(set(c)) for c in manifest["expected_candidate_codes"].values())
    assert {r["stage_transition"] for r in manifest["rows"]} == {"2_to_3", "3_to_4"}
    assert all(row["promotion_oof_train_end"] < row["signal_date"] for row in features)
    assert sum(count == 1 for count in evidence["day_candidate_counts"].values()) == 9
    assert all(count >= 2 for day, count in evidence["day_candidate_counts"].items() if day >= "20251111")


def test_real_panel_adapter_matches_labels_contract_and_preserves_missing_features():
    manifest, features, _ = run.prepare_history(run.ROOT)
    loaded, bindings = _load_candidates(run.ROOT.resolve(), manifest, _strict_open_dates(run.ROOT))
    assert len(loaded) == 6753 and len(bindings) == 3
    assert {r["stage_transition"] for r in loaded} == {"2→3", "3→4"}
    assert any(v is None for row in loaded for v in row["features"].values())
    assert loaded[0]["features"] == manifest["rows"][0]["features"]
    assert "conditional_net_return" not in loaded[0]["features"]
    assert "slot_net_return" not in features[0]
    assert "profit_probability" not in features[0]
    assert "promotion_probability" not in features[0]
    assert all(row["shadow_max_price"] is None for row in loaded)


def test_historical_availability_is_explicitly_not_natural_forward_evidence():
    manifest, _, evidence = run.prepare_history(run.ROOT)
    assert manifest["evidence_kind"] == "RETROSPECTIVE_D_ONLY_RECONSTRUCTION"
    assert manifest["feature_availability_is_natural_freeze_evidence"] is False
    assert evidence["natural_freeze_verified"] is False
    assert "not new independent proof" in evidence["causal_feature_audit_scope"]
    assert evidence["missing_historical_signals"] == ["promotion_probability", "named_limit_path", "existing_profit_rank"]


def test_historical_entry_is_not_misrepresented_as_current_production_policy():
    _, _, evidence = run.prepare_history(run.ROOT)
    assert evidence["entry_policy_id"] == "research_auction_or_open_no_cap_v1"
    assert evidence["cost_rate"] == .0045
    assert evidence["entry_policy_matches_production_shadow"] is False
    assert evidence["feature_timestamp_semantics"] == "RETROSPECTIVE_D_ONLY_BOUND"


def test_real_feature_adapter_reaches_data_gate_without_fake_or_legacy_labels():
    from work.profit_1000_upgrade.candidate import run_candidate
    _, features, evidence = run.prepare_history(run.ROOT)
    result = run_candidate(features, [], as_of_date="20260911", training_cutoff_date="20251111",
                           validation_end_date="20260814", frozen_manifest=evidence)
    assert result["status"] == "BLOCKED_DATA_QUALITY", result["report"].get("input_error")
    assert result["candidate_model"] is None and not result["report"]["training_performed"]
    assert result["report"]["production_activation_allowed"] is False


def test_plan_keeps_research_gate_and_future_holdout_separate_from_development():
    plan = run.load_plan()
    assert plan["historical_evidence_role"] == "RETROSPECTIVE_DEVELOPMENT_ALREADY_INSPECTED_NOT_UNTOUCHED_TEST"
    assert plan["production_release_allowed"] is False and plan["promotion_model_changed"] is False
    assert plan["always_record_top1_top2"] is True and plan["negative_score_skip_allowed"] is False
    assert plan["missing_truth_is_zero"] is False and plan["actual_execution_claimed"] is False
    assert plan["validation_end_date"] < plan["future_holdout_start_date"]
    assert plan["future_holdout_start_date"] == "20260914"
    assert "untouched prospective validation" in plan["release_requires"]
    assert "overlapping position capital accounting" in plan["release_requires"]
    assert plan["capital_validation"]["compound_slot_returns_are_account_NAV"] is False


@pytest.mark.parametrize("key,value", [("production_release_allowed", True),
    ("negative_score_skip_allowed", True), ("future_holdout_start_date", "20260915"),
    ("label_policy_id", "legacy_open_exit"), ("cost_rate", 0)])
def test_plan_critical_policy_drift_rejected(tmp_path, monkeypatch, key, value):
    plan = run.load_plan()
    plan[key] = value
    (tmp_path / "PLAN.json").write_text(json.dumps(plan))
    monkeypatch.setattr(run, "HERE", tmp_path)
    with pytest.raises(ValueError, match="policy changed"):
        run.load_plan()


def test_prepare_scope_drift_and_wrong_source_sha_rejected(monkeypatch):
    plan = run.load_plan()
    plan["historical_rows"] = 6752
    monkeypatch.setattr(run, "load_plan", lambda: plan)
    with pytest.raises(ValueError, match="scope changed"):
        run.prepare_history(run.ROOT)
    plan["historical_rows"] = 6753
    plan["source_inputs"]["ledger"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="binding changed"):
        run.prepare_history(run.ROOT)


def test_immutable_write_rejects_overwrite(tmp_path):
    path = tmp_path / "report.json"
    run.write_json(path, {"original": True})
    raw = path.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        run.write_json(path, {"original": False})
    assert path.read_bytes() == raw


def test_immutable_write_rejects_symlink_parent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "aliased_results"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        run.write_json(alias / "result.json", {"outside_write": True})
    assert not (outside / "result.json").exists()


def test_initialize_mirror_is_outside_checkout_and_does_not_reuse_directory(tmp_path):
    with pytest.raises(ValueError, match="outside checkout"):
        run.initialize_mirror(run.ROOT / "research_mirror_forbidden")
    with pytest.raises(FileExistsError):
        run.initialize_mirror(tmp_path)


def test_archive_imports_bound_raw_only_never_old_outcome_labels(tmp_path):
    path, spec = archive_fixture(tmp_path)
    mirror = research_root(tmp_path / "mirror")
    out = run.import_archive(mirror, path, spec)
    assert out["imported_partitions"] == 1
    assert (mirror / "data/market/raw/2022/20221114/daily.csv").is_file()
    assert not list(mirror.rglob("*labels*"))
    assert run.import_archive(mirror, path, spec) == out


def test_archive_sha_unbound_and_path_traversal_rejected(tmp_path):
    path, spec = archive_fixture(tmp_path)
    mirror = research_root(tmp_path / "mirror")
    bad = dict(spec, zip_sha256="0" * 64)
    with pytest.raises(ValueError, match="ZIP SHA mismatch"):
        run.import_archive(mirror, path, bad)
    path, spec = archive_fixture(tmp_path, extra="artifact/unbound.csv")
    with pytest.raises(ValueError, match="unbound archive"):
        run.import_archive(mirror, path, spec)
    path, spec = archive_fixture(tmp_path, member_path="../../escape.csv")
    with pytest.raises(ValueError, match="unsafe archive"):
        run.import_archive(mirror, path, spec)


def test_archive_conflicting_partition_is_not_overwritten(tmp_path):
    path, spec = archive_fixture(tmp_path)
    mirror = research_root(tmp_path / "mirror")
    existing = mirror / "data/market/raw/2022/20221114/daily.csv"
    existing.parent.mkdir(parents=True)
    existing.write_text("preserved original")
    with pytest.raises(ValueError, match="conflicting"):
        run.import_archive(mirror, path, spec)
    assert existing.read_text() == "preserved original"


def test_archive_destination_parent_symlink_is_rejected(tmp_path):
    path, spec = archive_fixture(tmp_path)
    mirror = research_root(tmp_path / "mirror")
    parent = mirror / "data/market/raw"
    parent.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        run.import_archive(mirror, path, spec)
    assert not list(outside.rglob("*.csv"))


@pytest.mark.parametrize("receipt_mode", ["valid", "missing", "wrong_sha"])
def test_evaluate_passes_same_new_labels_and_preserves_release_block(tmp_path, monkeypatch, receipt_mode):
    research_root(tmp_path)
    manifest = {"schema_version": "fake-test-only", "rows": [{"exec_date": "20221114"}], "source_bindings": []}
    features = [{"signal_date": "20221111"}]
    evidence = {"test_source": True}
    supplied_labels = [{"label_status": "PENDING_EXIT_MISSING_MINUTES"}]
    monkeypatch.setattr(run, "prepare_history", lambda root: (manifest, features, evidence))
    labels_module = importlib.import_module("labels")
    candidate_module = importlib.import_module("candidate")
    calls = []
    def labels(root, actual_manifest, *, as_of_date):
        assert root == tmp_path and actual_manifest is manifest
        calls.append(("labels", as_of_date))
        return {"rows": supplied_labels}
    def candidate(actual_features, actual_labels, **kwargs):
        assert actual_features is features and actual_labels is supplied_labels
        assert kwargs["frozen_manifest"] is evidence
        calls.append(("candidate", kwargs))
        return {"status": "BLOCKED_DATA_QUALITY", "report": {"training_performed": False}, "candidate_model": None}
    monkeypatch.setattr(labels_module, "build_labels", labels)
    monkeypatch.setattr(candidate_module, "run_candidate", candidate)
    if receipt_mode != "missing":
        receipt = {"schema_version": "dc20_profit_1000_collection_receipt_v1", "as_of_date": "20260911",
                   "auction_attempts_complete": True, "production_writes": False, "existing_truth_overwritten": False,
                   "candidate_source_bindings": [], "new_source_files": [],
                   "request_receipts": [{"trade_date": "20221114", "endpoint": "stk_auction_o",
                                          "network_request_performed": True, "status": "EMPTY_RESPONSE"}]}
        if receipt_mode == "wrong_sha":
            (tmp_path / "raw.csv").write_text("bound source changed")
            receipt["new_source_files"] = [{"path": "raw.csv", "sha256": "0" * 64}]
        (tmp_path / "collection_receipt.json").write_text(json.dumps(receipt))
    result = run.evaluate(tmp_path)
    assert [name for name, _ in calls] == (["labels", "candidate"] if receipt_mode == "valid" else ["labels"])
    assert result["status"] == ("BLOCKED_DATA_QUALITY" if receipt_mode == "valid" else "BLOCKED_COLLECTION_EVIDENCE")
    assert result["production_release_allowed"] is False and result["promotion_model_changed"] is False
    assert result["label_status_counts"] == {"PENDING_EXIT_MISSING_MINUTES": 1}
    assert json.loads((tmp_path / "research_results/labels.json").read_text())["rows"] == supplied_labels
    assert json.loads((tmp_path / "research_results/candidate.json").read_text())["report"]["training_performed"] is False


def test_production_root_and_missing_marker_block_before_any_write(tmp_path):
    path, spec = archive_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside checkout"):
        run.import_archive(run.ROOT, path, spec)
    with pytest.raises(ValueError, match="outside checkout"):
        run.evaluate(run.ROOT)
    with pytest.raises(ValueError, match="marker missing"):
        run.require_research_mirror(tmp_path)
    assert not (tmp_path / "research_results").exists()
