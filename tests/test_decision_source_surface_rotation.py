from __future__ import annotations

import hashlib
import json
from pathlib import Path

from top10decision.decision.model_freeze import REQUIRED_ACTIVE_PIN_PATHS


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models/decision_model_freeze.json"
EVIDENCE = ROOT / "models/decision_source_surface_rotation_20260824.json"
EXPECTED_ADDED_RUNTIME_PINS: set[str] = {
    "models/decision_replay_input_snapshots/1bf6eea649d69688f8263fee60c0df0606cb7b4ed86e0d9fd07f2937f999385f.json"
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
    assert evidence["rotation_id"] == (
        "dc20_restore_canonical_source_external_runtime_20260826"
    )
    assert evidence["approved_base_commit"] == (
        "d079e13f4fa90c9079de45578f845b6a5d6a433e"
    )
    assert evidence["prior_manifest_sha256"] == (
        "4f41373f5570a584a4c3c62103061e418a43cc57b4568589a105bd0b5ea429ff"
    )
    assert evidence["prior_evidence_sha256"] == (
        "7d51e950081e3f97aa76f1ff2606db01fa6ffd91903e45c40d400e3e0c0650e3"
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
    assert len(paths) == len(set(paths)) == 35
    assert set(paths) == {
        ".github/workflows/deploy_dc20_pages.yml",
        ".github/workflows/diagnose_decision_fingerprint.yml",
        ".github/workflows/run_auction_v3.yml",
        ".github/workflows/run_decision_daily.yml",
        "decision.html",
        "scripts/build_three_engine_five_year_ledger.py",
        "scripts/migrate_decision_runtime.py",
        "scripts/replay_frozen_canonical_v2.py",
        "scripts/run_auction_v3.py",
        "scripts/sync_market_raw.py",
        "src/top10decision/auction_v3/calibration.py",
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/auction_v3/promotion_model.py",
        "src/top10decision/decision/action_plan.py",
        "src/top10decision/decision/executable_profit_shadow.py",
        "src/top10decision/decision/model_freeze.py",
        "src/top10decision/decision/observation.py",
        "src/top10decision/decision/three_rank.py",
        "tests/test_auction_v3_three_engine_runtime.py",
        "tests/test_d_close_features.py",
        "tests/test_dashboard_research_projection.py",
        "tests/test_decision_research_context.py",
        "tests/test_decision_three_rank_contract.py",
        "tests/test_decision_three_rank_frontend.py",
        "tests/test_decision_three_rank_history_projection.py",
        "tests/test_decision_model_freeze.py",
        "tests/test_decision_v8_calibration.py",
        "tests/test_frozen_canonical_v2_replay.py",
        "tests/test_migrate_decision_runtime.py",
        "tests/test_promotion_model.py",
        "tests/test_pages_truthfulness_workflow.py",
        "tests/test_sync_market_raw.py",
        "tests/test_three_engine_models.py",
        "tests/test_three_rank_freeze.py",
        "tests/test_writer_workflow_hardening.py",
    }
    expected_classification = {
        ".github/workflows/deploy_dc20_pages.yml": (
            "canonical_dc20_research_context_pages_binding"
        ),
        ".github/workflows/diagnose_decision_fingerprint.yml": (
            "frozen_replay_input_snapshot_binding_workflow"
        ),
        ".github/workflows/run_auction_v3.yml": (
            "push_read_only_auction_pipeline_gate"
        ),
        ".github/workflows/run_decision_daily.yml": (
            "strict_dated_daily_context_workflow"
        ),
        "decision.html": "html_only_decision_surface_cleanup",
        "scripts/build_three_engine_five_year_ledger.py": (
            "three_engine_helper_externalization"
        ),
        "scripts/migrate_decision_runtime.py": (
            "immutable_replay_snapshot_migration_binding"
        ),
        "scripts/replay_frozen_canonical_v2.py": (
            "frozen_replay_input_snapshot_binding"
        ),
        "scripts/run_auction_v3.py": "three_engine_runtime_adapter",
        "scripts/sync_market_raw.py": "strict_dated_sse_context_sync",
        "src/top10decision/auction_v3/calibration.py": (
            "canonical_source_preimage_restore"
        ),
        "src/top10decision/auction_v3/engine.py": (
            "canonical_source_preimage_restore"
        ),
        "src/top10decision/auction_v3/promotion_model.py": (
            "canonical_source_preimage_restore"
        ),
        "src/top10decision/decision/action_plan.py": (
            "legacy_action_schema_compatibility"
        ),
        "src/top10decision/decision/executable_profit_shadow.py": (
            "strict_canonical_d_stage_normalization_for_internal_profit_shadow"
        ),
        "src/top10decision/decision/model_freeze.py": (
            "three_rank_canonical_preimage_runtime_validation"
        ),
        "src/top10decision/decision/observation.py": (
            "three_rank_canonical_observation_preimage"
        ),
        "src/top10decision/decision/three_rank.py": (
            "three_engine_runtime_adapter"
        ),
        "tests/test_auction_v3_three_engine_runtime.py": (
            "three_engine_runtime_adapter_test"
        ),
        "tests/test_d_close_features.py": "three_engine_runtime_adapter_test",
        "tests/test_dashboard_research_projection.py": (
            "html_only_decision_surface_cleanup_test"
        ),
        "tests/test_decision_three_rank_contract.py": (
            "legacy_action_schema_compatibility_test"
        ),
        "tests/test_decision_three_rank_frontend.py": (
            "html_only_decision_surface_cleanup_test"
        ),
        "tests/test_decision_three_rank_history_projection.py": (
            "appended_forward_history_contract_test"
        ),
        "tests/test_decision_model_freeze.py": (
            "three_rank_canonical_preimage_runtime_validation_test"
        ),
        "tests/test_decision_research_context.py": (
            "isolated_daily_research_root_test"
        ),
        "tests/test_decision_v8_calibration.py": (
            "independent_monotonic_calibration_test"
        ),
        "tests/test_frozen_canonical_v2_replay.py": (
            "frozen_replay_input_snapshot_binding_test"
        ),
        "tests/test_migrate_decision_runtime.py": (
            "immutable_replay_snapshot_migration_binding_test"
        ),
        "tests/test_promotion_model.py": (
            "three_engine_helper_externalization_test"
        ),
        "tests/test_pages_truthfulness_workflow.py": (
            "canonical_dc20_research_context_pages_binding_test"
        ),
        "tests/test_sync_market_raw.py": (
            "strict_dated_sse_context_sync_test"
        ),
        "tests/test_three_engine_models.py": (
            "independent_monotonic_calibration_test"
        ),
        "tests/test_three_rank_freeze.py": (
            "canonical_diagnostic_overlay_isolation_test"
        ),
        "tests/test_writer_workflow_hardening.py": (
            "strict_dated_daily_context_workflow_test"
        ),
    }
    current_surface: dict[str, str] = {}
    for item in changes:
        assert item["prior_sha256"] != item["current_sha256"]
        assert item["classification"] == expected_classification[item["path"]]
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
        "default_collapsed_ui_blocks": [
            "sentiment_quantification",
        ],
        "removed_ui_blocks": [
            "executable_profit_proof",
            "executable_profit_public_banner",
            "executable_profit_toolbar_caption",
            "legacy_profit_relative_banner",
            "manual_operation_reference_section",
            "p_fill_shadow_top2",
            "research_only_action_notice",
            "trade_selector_legacy_commentary",
            "three_rank_big_loss_profit_columns",
            "three_rank_big_loss_profit_sort_buttons",
            "three_rank_json_download",
            "three_rank_history_archive",
        ],
        "underlying_ledgers_preserved": True,
        "canonical_engine_bytes_restored": True,
        "canonical_source_set_restored": True,
        "independent_three_engine_runtime_adapter": True,
        "research_runtime_export_after_canonical_audit": True,
        "full_frozen_replay_ci_required": True,
        "codex_runtime_dependency": False,
        "external_top10_decision_runtime_dependency": False,
        "writer_dispatch_performed": False,
    }
