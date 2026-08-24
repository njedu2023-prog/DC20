from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models/decision_model_freeze.json"
EVIDENCE = ROOT / "models/decision_source_surface_rotation_20260824.json"
EXPECTED_ADDED_RUNTIME_PINS = {
    "models/decision_executable_profit_forward_settlement_contract.json",
    "models/decision_executable_profit_internal_forward_challenger.json",
    "models/decision_executable_profit_research_projection_contract.json",
    "models/decision_executable_profit_shadow_contract.json",
    "scripts/project_decision_executable_profit_research.py",
    "scripts/run_decision_executable_profit_forward_shadow.py",
    "scripts/settle_decision_executable_profit_forward_shadow.py",
    "scripts/validate_decision_executable_profit_shadow_contract.py",
    "src/top10decision/decision/executable_profit_research_projection.py",
    "src/top10decision/decision/executable_profit_shadow.py",
    "src/top10decision/decision/executable_profit_shadow_settlement.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_reviewed_source_surface_rotation_is_hash_bound_and_model_preserving() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    rotation = manifest["source_surface_rotation"]

    assert evidence["schema_version"] == "decision_source_surface_rotation_v1"
    assert rotation == {
        "schema_version": "decision_source_surface_rotation_v1",
        "rotation_id": evidence["rotation_id"],
        "approved_base_commit": evidence["approved_base_commit"],
        "prior_manifest_sha256": evidence["prior_manifest_sha256"],
        "evidence_path": EVIDENCE.relative_to(ROOT).as_posix(),
        "evidence_sha256": _sha256(EVIDENCE),
    }
    assert evidence["approved_base_commit"] == (
        "fd0934af6c84e762252e52c3e5f494728e0ae8be"
    )
    assert evidence["prior_manifest_sha256"] == (
        "8271fa1a7038fa3614bb033c8f3e56b94a452eff68ff80ebd0d73b9be7432f77"
    )
    assert evidence["prior_manifest_sha256"] != _sha256(MANIFEST)
    assert manifest["pinned_files"][rotation["evidence_path"]] == (
        rotation["evidence_sha256"]
    )

    identity = evidence["protected_model_identity"]
    assert identity == {
        "freeze_id": manifest["freeze_id"],
        "training_cutoff_signal_date": manifest["training_cutoff_signal_date"],
        "history_snapshot_sha256": manifest["history_snapshot"]["sha256"],
        "three_rank_contract_sha256": _canonical_sha256(
            manifest["production"]["three_rank"]
        ),
        "model_identity_changed": False,
        "training_ledger_changed": False,
        "action_plan_changed": False,
    }

    changes = evidence["pin_changes"]
    paths = [item["path"] for item in changes]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths)) == 7
    assert set(paths) == {
        ".github/workflows/deploy_dc20_pages.yml",
        ".github/workflows/run_decision_daily.yml",
        ".github/workflows/train_decision_three_engines.yml",
        ".github/workflows/verify_decision_observations.yml",
        "decision.html",
        "src/top10decision/auction_v3/engine.py",
        "tests/test_auction_v3_three_engine_runtime.py",
    }
    current_surface: dict[str, str] = {}
    for item in changes:
        assert item["prior_sha256"] != item["current_sha256"]
        assert item["classification"] in {
            "prior_executable_profit_source_pin_reconciliation",
            "executable_profit_public_surface_release",
        }
        target = ROOT / item["path"]
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == item["current_sha256"]
        assert manifest["pinned_files"][item["path"]] == item["current_sha256"]
        current_surface[item["path"]] = item["current_sha256"]
    assert _canonical_sha256(current_surface) == evidence["changed_surface_sha256"]

    added_runtime_pins = evidence["added_runtime_pins"]
    assert set(added_runtime_pins) == EXPECTED_ADDED_RUNTIME_PINS
    assert set(added_runtime_pins).isdisjoint(paths)
    for path, expected_sha256 in added_runtime_pins.items():
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == expected_sha256
        assert manifest["pinned_files"][path] == expected_sha256

    reconstructed_prior_pins = dict(manifest["pinned_files"])
    assert reconstructed_prior_pins.pop(rotation["evidence_path"]) == (
        rotation["evidence_sha256"]
    )
    for path, expected_sha256 in added_runtime_pins.items():
        assert reconstructed_prior_pins.pop(path) == expected_sha256
    for item in changes:
        assert reconstructed_prior_pins[item["path"]] == item["current_sha256"]
        reconstructed_prior_pins[item["path"]] = item["prior_sha256"]
    assert _canonical_sha256(reconstructed_prior_pins) == (
        evidence["prior_pinned_files_sha256"]
    )
    assert len(manifest["pinned_files"]) == (
        len(reconstructed_prior_pins) + len(added_runtime_pins) + 1
    )

    assert evidence["release_contract"] == {
        "candidate_count": "ACTUAL_N_0_TO_10_NO_PADDING",
        "shadow_slots": "min(2,N)",
        "public_label": "UNCALIBRATED_RESEARCH_PROXY_NOT_FORMAL_PROBABILITY",
        "codex_runtime_dependency": False,
        "external_top10_decision_runtime_dependency": False,
        "writer_dispatch_performed": False,
    }
