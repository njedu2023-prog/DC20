from __future__ import annotations

import hashlib
import json
from pathlib import Path

from top10decision.decision.model_freeze import REQUIRED_ACTIVE_PIN_PATHS


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models/decision_model_freeze.json"
EVIDENCE = ROOT / "models/decision_source_surface_rotation_20260824.json"
EXPECTED_ADDED_RUNTIME_PINS = {
    "scripts/project_decision_legacy_profit_relative_research.py",
    "src/top10decision/decision/legacy_profit_relative_research.py",
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
        "1c06ca4658049e76b225617f3a8413c302258938"
    )
    assert evidence["prior_manifest_sha256"] == (
        "a9b1db64363994a9c9ebbb437d2b3e1ff5d4ed3f0174f27e01ffab5682a15730"
    )
    assert evidence["prior_evidence_sha256"] == (
        "eaff60c840e0856ecfe067858c3ef0e68588acd164a8bf76d6abfa15da456abd"
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
    assert len(paths) == len(set(paths)) == 9
    assert set(paths) == {
        ".github/workflows/deploy_dc20_pages.yml",
        ".github/workflows/run_decision_daily.yml",
        "decision.html",
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/model_freeze.py",
        "tests/test_dashboard_research_projection.py",
        "tests/test_decision_three_rank_frontend.py",
        "tests/test_decision_three_rank_history_projection.py",
        "tests/test_pages_truthfulness_workflow.py",
    }
    current_surface: dict[str, str] = {}
    for item in changes:
        assert item["prior_sha256"] != item["current_sha256"]
        assert item["classification"] in {
            "decision_ui_research_ranking_and_cleanup",
            "legacy_profit_independent_runtime_path",
            "legacy_profit_pages_fail_closed",
            "legacy_profit_required_runtime_pin_closure",
        }
        target = ROOT / item["path"]
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == item["current_sha256"]
        assert manifest["pinned_files"][item["path"]] == item["current_sha256"]
        current_surface[item["path"]] = item["current_sha256"]
    assert _canonical_sha256(current_surface) == evidence["changed_surface_sha256"]

    added_runtime_pins = evidence["added_runtime_pins"]
    assert set(added_runtime_pins) == EXPECTED_ADDED_RUNTIME_PINS
    assert EXPECTED_ADDED_RUNTIME_PINS.issubset(REQUIRED_ACTIVE_PIN_PATHS)
    for path, expected_sha256 in added_runtime_pins.items():
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == expected_sha256
        assert manifest["pinned_files"][path] == expected_sha256

    reconstructed_prior_pins = dict(manifest["pinned_files"])
    assert reconstructed_prior_pins[rotation["evidence_path"]] == (
        rotation["evidence_sha256"]
    )
    reconstructed_prior_pins[rotation["evidence_path"]] = evidence[
        "prior_evidence_sha256"
    ]
    for path, expected_sha256 in added_runtime_pins.items():
        assert reconstructed_prior_pins.pop(path) == expected_sha256
    for item in changes:
        assert reconstructed_prior_pins[item["path"]] == item["current_sha256"]
        reconstructed_prior_pins[item["path"]] = item["prior_sha256"]
    assert _canonical_sha256(reconstructed_prior_pins) == (
        evidence["prior_pinned_files_sha256"]
    )
    assert len(manifest["pinned_files"]) == (
        len(reconstructed_prior_pins) + len(added_runtime_pins)
    )

    assert evidence["release_contract"] == {
        "candidate_count": "ACTUAL_N_0_TO_10_NO_PADDING",
        "public_label": (
            "LEGACY_PROFIT_RAW_RELATIVE_SCORE_NOT_PROBABILITY_NOT_FORMAL"
        ),
        "visible_full_n_relative_ranking": True,
        "legacy_profit_official_status": "NOT_READY_VALIDATION_GATE",
        "promotion_rank_frozen_and_independent": True,
        "official_action_count": 0,
        "removed_ui_blocks": [
            "p_fill_shadow_top2",
            "trade_selector_legacy_commentary",
            "three_rank_history_archive",
        ],
        "underlying_ledgers_preserved": True,
        "codex_runtime_dependency": False,
        "external_top10_decision_runtime_dependency": False,
        "writer_dispatch_performed": False,
    }
