from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from top10decision.decision.model_freeze import REQUIRED_ACTIVE_PIN_PATHS


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models/decision_model_freeze.json"
EVIDENCE = ROOT / "models/decision_source_surface_rotation_20260824.json"
SUCCESSOR = ROOT / "models/decision_source_surface_review_20260906.json"
SUCCESSOR_REVIEW_PATHS = {
    ".github/workflows/verify_decision_observations.yml",
    "decision.html",
    "scripts/settle_primary_observations.py",
    "tests/test_dashboard_research_projection.py",
    "tests/test_decision_three_rank_frontend.py",
    "tests/test_executable_profit_workflow_wiring.py",
    "tests/test_primary_observation_summary.py",
    "tests/test_verify_forecast_inputs.py",
}
EXPECTED_ADDED_RUNTIME_PINS: set[str] = {
    ".github/workflows/run_primary_d_daily.yml",
    ".github/workflows/run_primary_profit_forward_shadow.yml",
    ".github/workflows/run_primary_profit_rankings.yml",
    "data/auction_v3/promotion_prior/five_year_daily_stage_board.csv",
    "models/decision_primary_profit_forward_shadow_bridge_contract.json",
    "models/decision_primary_profit_research_contract.json",
    "models/decision_replay_input_snapshots/1bf6eea649d69688f8263fee60c0df0606cb7b4ed86e0d9fd07f2937f999385f.json",
    "scripts/freeze_primary_profit_forward_shadow.py",
    "scripts/publish_primary_profit_rankings.py",
    "scripts/publish_primary_three_rank.py",
    "scripts/settle_primary_observations.py",
    "scripts/sync_frozen_shadow_truth.py",
    "scripts/validate_verify_forecast_inputs.py",
    "src/top10decision/decision/primary_profit_forward_shadow_bridge.py",
    "tests/test_decision_executable_profit_frontend.py",
    "tests/test_decision_executable_profit_shadow_settlement.py",
    "tests/test_executable_profit_workflow_wiring.py",
    "tests/test_primary_profit_forward_shadow_bridge.py",
    "tests/test_primary_profit_forward_shadow_workflow.py",
    "tests/test_primary_profit_rankings_p1.py",
    "tests/test_primary_three_rank_p0.py",
    "tests/test_primary_observation_summary.py",
    "tests/test_primary_observation_frontend.py",
    "tests/test_verify_forecast_inputs.py",
    "tests/test_sync_frozen_shadow_truth.py",
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


def _historical_pins_after_successor_review(manifest: dict, evidence: dict, review: dict) -> dict:
    """Undo only an explicitly reviewed successor, never rewrite old evidence."""
    assert review["schema_version"] == "decision_source_surface_successor_review_v1"
    assert review["review_id"] == "dc20_primary_t_t1_truth_validation_20260906"
    assert review["reviewed_on"] == "2026-09-06"
    assert review["approved_base_commit"] == "54e05018ae05980d016195262a1708f7679fcfef"
    assert review["baseline_manifest_sha256"] == "7eb10190364ab674c91ce6e2f487336df75cb064d29e3fb066512bcd156e566b"
    assert review["historical_evidence_path"] == EVIDENCE.relative_to(ROOT).as_posix()
    assert review["historical_evidence_sha256"] == _sha256(EVIDENCE) == (
        "52a7cea61c8110c9ca84d998a77555db8cbd9fe1bcca07f02548d22823378e5b"
    )
    assert review["scope"] == "SOURCE_ONLY_SUCCESSOR_REVIEW_NOT_MODEL_RELEASE"
    assert review["protected_model_identity"] == evidence["protected_model_identity"]
    assert review["boundaries"] == {
        "historical_evidence_rewritten": False,
        "live_model_weights_changed": False,
        "promotion_members_or_ranks_changed": False,
        "predictions_or_shadow_selections_recreated": False,
        "formal_trade_action_created": False,
        "natural_verify_success_claimed": False,
    }
    historical_surface = {item["path"]: item["current_sha256"] for item in evidence["pin_changes"]}
    historical_surface.update(evidence["added_runtime_pins"])
    pins = dict(manifest["pinned_files"])
    assert pins.pop(SUCCESSOR.relative_to(ROOT).as_posix()) == _sha256(SUCCESSOR)
    changes = review["pin_changes"]
    paths = [item["path"] for item in changes]
    assert paths == sorted(SUCCESSOR_REVIEW_PATHS)
    assert {path for path, old_sha in historical_surface.items() if pins[path] != old_sha} == SUCCESSOR_REVIEW_PATHS
    for item in changes:
        path = item["path"]
        assert set(item) == {"path", "historical_sha256", "baseline_sha256", "current_sha256",
                             "changed_in_this_review", "reason"}
        assert all(re.fullmatch(r"[0-9a-f]{64}", item[key]) for key in (
            "historical_sha256", "baseline_sha256", "current_sha256"))
        assert item["historical_sha256"] == historical_surface[path]
        assert item["historical_sha256"] != item["current_sha256"]
        assert item["changed_in_this_review"] is (item["baseline_sha256"] != item["current_sha256"])
        assert item["reason"]
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == pins[path] == item["current_sha256"]
        pins[path] = item["historical_sha256"]
    return pins


@pytest.mark.parametrize("mutation", ["extra_path", "current_sha", "historical_sha", "model_identity", "base_commit"])
def test_successor_review_rejects_unreviewed_surface_or_identity_drift(mutation: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    review = json.loads(SUCCESSOR.read_text(encoding="utf-8"))
    if mutation == "extra_path":
        review["pin_changes"].append(dict(review["pin_changes"][0], path="src/top10decision/auction_v3/engine.py"))
    elif mutation == "current_sha":
        review["pin_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "historical_sha":
        review["pin_changes"][0]["historical_sha256"] = "0" * 64
    elif mutation == "model_identity":
        review["protected_model_identity"]["model_identity_changed"] = True
    else:
        review["approved_base_commit"] = "0" * 40
    with pytest.raises(AssertionError):
        _historical_pins_after_successor_review(manifest, evidence, review)


def test_reviewed_source_surface_rotation_is_hash_bound_and_model_preserving() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    review = json.loads(SUCCESSOR.read_text(encoding="utf-8"))
    historical_pins = _historical_pins_after_successor_review(manifest, evidence, review)
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
    assert len(paths) == len(set(paths)) == 45
    assert set(paths) == {
        ".github/workflows/deploy_dc20_pages.yml",
        ".github/workflows/diagnose_decision_fingerprint.yml",
        ".github/workflows/run_auction_v3.yml",
        ".github/workflows/run_decision_daily.yml",
        ".github/workflows/test_decision_core.yml",
        ".github/workflows/verify_decision_observations.yml",
        "decision.html",
        "models/decision_executable_profit_forward_settlement_contract.json",
        "scripts/build_decision_three_rank_history.py",
        "scripts/build_three_engine_five_year_ledger.py",
        "scripts/decision_pages_truth.py",
        "scripts/migrate_decision_runtime.py",
        "scripts/replay_frozen_canonical_v2.py",
        "scripts/run_auction_v3.py",
        "scripts/sync_market_raw.py",
        "scripts/validate_decision_executable_profit_shadow_contract.py",
        "scripts/verify_decision_observations.py",
        "src/top10decision/auction_v3/calibration.py",
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/auction_v3/promotion_model.py",
        "src/top10decision/decision/action_plan.py",
        "src/top10decision/decision/executable_profit_shadow.py",
        "src/top10decision/decision/executable_profit_shadow_settlement.py",
        "src/top10decision/decision/model_freeze.py",
        "src/top10decision/decision/observation.py",
        "src/top10decision/decision/three_engine_models.py",
        "src/top10decision/decision/three_rank.py",
        "tests/test_auction_v3.py",
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
            "primary_owned_exact_revision_pages_dispatch_gate"
        ),
        ".github/workflows/diagnose_decision_fingerprint.yml": (
            "frozen_replay_input_snapshot_binding_workflow"
        ),
        ".github/workflows/run_auction_v3.yml": (
            "legacy_auction_manual_only_depower"
        ),
        ".github/workflows/run_decision_daily.yml": (
            "legacy_full_research_manual_only_depower"
        ),
        ".github/workflows/test_decision_core.yml": (
            "frozen_replay_input_snapshot_binding_workflow"
        ),
        ".github/workflows/verify_decision_observations.yml": (
            "primary_mixed_shadow_forward_verification_workflow"
        ),
        "decision.html": "primary_action_independent_three_ranking_frontend",
        "models/decision_executable_profit_forward_settlement_contract.json": (
            "dual_schema_primary_mixed_shadow_settlement_contract"
        ),
        "scripts/build_decision_three_rank_history.py": (
            "primary_only_no_shadow_forward_exclusion"
        ),
        "scripts/build_three_engine_five_year_ledger.py": (
            "three_engine_helper_externalization"
        ),
        "scripts/decision_pages_truth.py": (
            "primary_profit_exact_public_bundle_validation"
        ),
        "scripts/migrate_decision_runtime.py": (
            "immutable_replay_snapshot_migration_binding"
        ),
        "scripts/replay_frozen_canonical_v2.py": (
            "frozen_replay_input_snapshot_binding"
        ),
        "scripts/run_auction_v3.py": "three_engine_runtime_adapter",
        "scripts/sync_market_raw.py": "strict_dated_sse_context_sync",
        "scripts/validate_decision_executable_profit_shadow_contract.py": (
            "historical_contract_reviewed_source_rotation_bridge"
        ),
        "scripts/verify_decision_observations.py": (
            "public_observation_cumulative_cutover_projection"
        ),
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
        "src/top10decision/decision/executable_profit_shadow_settlement.py": (
            "dual_schema_primary_mixed_shadow_settlement_runtime"
        ),
        "src/top10decision/decision/model_freeze.py": (
            "primary_p0_p1_active_pin_validation"
        ),
        "src/top10decision/decision/observation.py": (
            "three_rank_canonical_observation_preimage"
        ),
        "src/top10decision/decision/three_engine_models.py": (
            "promotion_only_primary_d_loader"
        ),
        "src/top10decision/decision/three_rank.py": (
            "three_engine_runtime_adapter"
        ),
        "tests/test_auction_v3.py": (
            "public_observation_cumulative_cutover_projection_test"
        ),
        "tests/test_auction_v3_three_engine_runtime.py": (
            "three_engine_runtime_adapter_test"
        ),
        "tests/test_d_close_features.py": "three_engine_runtime_adapter_test",
        "tests/test_dashboard_research_projection.py": (
            "primary_profit_frontend_contract_test"
        ),
        "tests/test_decision_three_rank_contract.py": (
            "legacy_action_schema_compatibility_test"
        ),
        "tests/test_decision_three_rank_frontend.py": (
            "primary_action_independent_three_ranking_frontend_test"
        ),
        "tests/test_decision_three_rank_history_projection.py": (
            "forward_history_primary_only_exclusion_test"
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
            "primary_owned_pages_public_acceptance_test"
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
            "legacy_writer_depower_primary_schedule_contract_test"
        ),
    }
    current_surface: dict[str, str] = {}
    for item in changes:
        assert item["prior_sha256"] != item["current_sha256"]
        assert item["classification"] == expected_classification[item["path"]]
        target = ROOT / item["path"]
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == manifest["pinned_files"][item["path"]]
        assert historical_pins[item["path"]] == item["current_sha256"]
        current_surface[item["path"]] = item["current_sha256"]
    assert _canonical_sha256(current_surface) == evidence["changed_surface_sha256"]

    added_runtime_pins = evidence["added_runtime_pins"]
    assert set(added_runtime_pins) == EXPECTED_ADDED_RUNTIME_PINS
    assert EXPECTED_ADDED_RUNTIME_PINS.issubset(REQUIRED_ACTIVE_PIN_PATHS)
    for path, expected_sha256 in added_runtime_pins.items():
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == manifest["pinned_files"][path]
        assert historical_pins[path] == expected_sha256

    reconstructed_prior_pins = dict(historical_pins)
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
        len(reconstructed_prior_pins) + len(added_runtime_pins) + 1
    )

    assert evidence["release_contract"] == {
        "candidate_count": "ACTUAL_N_0_TO_10_NO_PADDING",
        "public_label": (
            "LEGACY_PROFIT_RAW_RELATIVE_SCORE_NOT_PROBABILITY_NOT_FORMAL"
        ),
        "primary_d_publication_priority": (
            "SYSTEM_P0_INDEPENDENT_OF_LEGACY_ACTION"
        ),
        "single_profit_public_label": (
            "UNCALIBRATED_RAW_RELATIVE_SCORE_NOT_PROBABILITY_NOT_FORMAL"
        ),
        "mixed_profit_public_label": (
            "UNCALIBRATED_FILL_X_CONDITIONAL_PROFIT_PROXY_NOT_PROBABILITY_NOT_FORMAL"
        ),
        "primary_profit_candidate_scope": "EXACT_SAME_D_P0_FROZEN_TOPN",
        "retrospective_profit_recovery": (
            "NON_FORWARD_NO_SHADOW_STATISTICS_NO_ACTION"
        ),
        "legacy_action_may_block_primary_publication": False,
        "primary_profit_may_change_promotion_membership_or_rank": False,
        "primary_mixed_forward_shadow_scope": (
            "EXACT_SAME_D_P1_TOP1_TOP2_MIN_2_OF_N_NO_PADDING"
        ),
        "primary_mixed_shadow_entry_source": (
            "EXACT_D_PRIMARY_RUNTIME_FEATURES_SHA256"
        ),
        "legacy_shadow_v1_behavior_changed": False,
        "primary_mixed_shadow_statistics": "FORWARD_ONLY_UNCALIBRATED",
        "primary_mixed_shadow_may_create_action_or_order": False,
        "primary_mixed_shadow_may_change_promotion_membership_or_rank": False,
        "visible_full_n_relative_ranking": True,
        "main_visible_rankings": ["promotion_with_path", "profit_research"],
        "legacy_relative_ranking_location": "DEFAULT_COLLAPSED_RESEARCH_BENCHMARK",
        "production_profit_engine_replaced": False,
        "legacy_profit_official_status": "NOT_READY_VALIDATION_GATE",
        "promotion_rank_frozen_and_independent": True,
        "official_action_count": 0,
        "default_collapsed_ui_blocks": [
            "sentiment_quantification",
            "legacy_profit_research_benchmark",
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
