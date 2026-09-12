from __future__ import annotations

import copy
from functools import lru_cache
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
COMPACT_REVIEW = ROOT / "models/decision_source_surface_review_20260909.json"
COMPACT_REVIEW_SHA = "c9ef3cad7ebdb3d0583fe4cc473c8f7178cd39f25a13c96a1265b6d2f383fe28"
DENSITY_REVIEW = ROOT / "models/decision_source_surface_review_20260909_density.json"
DENSITY_REVIEW_SHA = "8dbed9ad83a8d8db78ccf598192e8589a6516b8af5771452f63da5d1b247cfee"
STATISTICS_REVIEW = ROOT / "models/decision_source_surface_review_20260909_statistics.json"
STATISTICS_REVIEW_SHA = "20524bc2dcdfac594e73e9e552bb88af2136ccce83026ddb193f5140204fad97"
SUCCESS_RATE_REVIEW = ROOT / "models/decision_source_surface_review_20260909_success_rate.json"
SUCCESS_RATE_REVIEW_SHA = "313373fb0f20948c9a9e095c8ddd50d8ee2aeb6c6bf3acc4cff005ed8b30884b"
PROFIT_DETAILS_REVIEW = ROOT / "models/decision_source_surface_review_20260909_hide_profit_details.json"
PROFIT_DETAILS_REVIEW_SHA = "1857dc0401902bc53c379de0534de1009908d32bf7f3b3e4298d5b7c8a57f436"
PROFIT_SUMMARY_REVIEW = ROOT / "models/decision_source_surface_review_20260909_profit_summary.json"
PROFIT_SUMMARY_REVIEW_SHA = "d9cbc524f3eccee46b79635a8317089a64bcf9ffae31ae4992020ebe6109973f"
NAVIGATION_REVIEW = ROOT / "models/decision_source_surface_review_20260910_navigation.json"
NAVIGATION_REVIEW_SHA = "e559f856e5ec4cfe96ba9d8bb7d32d9f220fb2be2254eb421da6626ece116293"
DAILY_DISPATCH_REVIEW = ROOT / "models/decision_source_surface_review_20260911_daily_dispatch.json"
DAILY_DISPATCH_REVIEW_SHA = "e2993edc8afcae453e9e0742dcdbb58c9f68b9cdd50c83e08a8a71059262c28a"
LOADING_REVIEW = ROOT / "models/decision_source_surface_review_20260911_loading.json"
LOADING_REVIEW_SHA = "b153c5acce99f8154b33eaf6c4c1710cb2e86a4c211839f7603fdc3b2242e6d7"
REFERENCE_DENSITY_REVIEW = ROOT / "models/decision_source_surface_review_20260911_density.json"
REFERENCE_DENSITY_REVIEW_SHA = "3f482dc61d184c6f0ff68ff656364d04aaa1f71b3a6abcac14cc89f9ecb27f2c"
EXIT_LABEL_REVIEW = ROOT / "models/decision_source_surface_review_20260911_exit_label.json"
EXIT_LABEL_REVIEW_SHA = "cca69f5452fc52237948cb6f9433c3882de014b24f94d21add4954ba8eb259e1"
EXIT_LABEL_PATHS = {"decision.html", "tests/test_three_rank_truth_frontend.py"}
SETTLE_CLI_REVIEW = ROOT / "models/decision_source_surface_review_20260911_settle_cli.json"
SETTLE_CLI_REVIEW_SHA = "9c7b13f47008704358c7cdf8aeb5f9af11480fe8412886a85337d1d5580f3f95"
SETTLE_CLI_PATHS = {"scripts/settle_decision_executable_profit_forward_shadow.py", "tests/test_sync_frozen_shadow_truth.py"}
VERIFY_CLOSE_REVIEW = ROOT / "models/decision_source_surface_review_20260911_verify_close.json"
VERIFY_CLOSE_REVIEW_SHA = "a70bc4119535c92c540f2157ba71e1a7fbd8bebe89f579d48637232b88dd56e3"
VERIFY_CLOSE_PATHS = {"decision.html", ".github/workflows/run_primary_profit_rankings.yml",
    "scripts/sync_frozen_shadow_truth.py", "tests/test_sync_frozen_shadow_truth.py",
    "tests/test_primary_profit_rankings_p1.py", "tests/test_three_rank_truth_frontend.py",
    "tests/test_decision_two_rank_frontend.py"}
COLUMNS_REVIEW = ROOT / "models/decision_source_surface_review_20260911_columns.json"
COLUMNS_REVIEW_SHA = "7f089ed18d51e8375cfb731607fdc946989c1363df19aa44c8efe65c22f3326d"
COLUMNS_PIN_PATHS = {"decision.html", ".github/workflows/run_primary_profit_rankings.yml",
                     "tests/test_decision_three_rank_frontend.py", "tests/test_primary_profit_rankings_p1.py"}
DAILY_DISPATCH_PATHS = {
    ".github/workflows/run_primary_d_daily.yml",
    ".github/workflows/run_primary_profit_rankings.yml",
    "tests/test_decision_three_rank_history_projection.py",
    "tests/test_primary_profit_rankings_p1.py",
    "tests/test_primary_three_rank_p0.py",
}
DAILY_DISPATCH_UNPINNED_TEST_PATHS = {"tests/test_compact_rank_statistics.py"}
COMPACT_REVIEW_PATHS = {
    ".github/workflows/run_primary_profit_rankings.yml", "decision.html",
    "tests/test_dashboard_research_projection.py",
    "tests/test_decision_executable_profit_frontend.py",
    "tests/test_decision_three_rank_frontend.py", "tests/test_primary_profit_rankings_p1.py",
}
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


SHADOW_PRICE_REVIEW = ROOT / "models/decision_source_surface_review_20260911_shadow_price_v2.json"
SHADOW_PRICE_REVIEW_SHA = "b8753fa8f65b3d8533f15ac93f723dc8f5c1a7f52a51fc82f1a13eb9987920c5"
SHADOW_PRICE_EXISTING_PATHS = {
    ".github/workflows/deploy_dc20_pages.yml",
    ".github/workflows/run_primary_profit_forward_shadow.yml",
    ".github/workflows/verify_decision_observations.yml",
    "decision.html",
    "src/top10decision/decision/executable_profit_shadow_settlement.py",
    "src/top10decision/decision/primary_profit_forward_shadow_bridge.py",
    "tests/test_decision_executable_profit_shadow_settlement.py",
    "tests/test_pages_truthfulness_workflow.py",
    "tests/test_primary_profit_forward_shadow_bridge.py",
    "tests/test_primary_profit_forward_shadow_workflow.py",
    "tests/test_verify_forecast_inputs.py",
    "tests/test_compact_rank_statistics.py",
}
SHADOW_PRICE_ADDED_PATHS = {
    "models/decision_primary_profit_shadow_entry_price_policy_v2.json",
    "tests/test_profit_shadow_versioned_frontend.py",
}
SHADOW_PRICE_ADDED_PIN = "models/decision_primary_profit_shadow_entry_price_policy_v2.json"
SHADOW_PRICE_BOUNDARIES = {
    "model_weights_changed": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "truth_policy_changed": True,
    "versioned_truth_publication_changed": True,
    "historical_ledger_rewritten": False,
    "workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "validation_gates_bypassed": False,
    "actual_trading_enabled": False,
}


COPY_DELETE_REVIEW = ROOT / "models/decision_source_surface_review_20260911_copy_delete.json"
COPY_DELETE_REVIEW_SHA = "226113a5c1a28bcff836a16bd3f2ec1e6b0ca1b3bb59027ec894d3b46130108d"
COPY_DELETE_PATHS = {"decision.html", "tests/test_compact_rank_statistics.py", "tests/test_profit_shadow_versioned_frontend.py"}
COPY_DELETE_BOUNDARIES = {
    "model_weights_changed": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "truth_policy_changed": False,
    "statistics_calculation_changed": False,
    "versioned_truth_publication_changed": False,
    "historical_ledger_rewritten": False,
    "workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "validation_gates_bypassed": False,
    "actual_trading_enabled": False,
}


COMPACT_WINDOW_REVIEW = ROOT / "models/decision_source_surface_review_20260911_window_0910.json"
COMPACT_WINDOW_REVIEW_SHA = "b2f2bc9beb343c3d50a5384a6bf04ee3bdcf72b4e23583a0f3b8d6dedf9df5db"
COMPACT_WINDOW_EXISTING_PATHS = {'.github/workflows/deploy_dc20_pages.yml', 'decision.html', 'tests/test_compact_rank_statistics.py', 'tests/test_primary_d_navigation.py', 'tests/test_primary_observation_frontend.py', 'tests/test_profit_shadow_versioned_frontend.py'}
COMPACT_WINDOW_ADDED_PATHS = {'models/decision_compact_statistics_window_v1.json', 'scripts/build_compact_statistics_window.py', 'tests/test_compact_statistics_window.py', 'tests/test_compact_statistics_window_frontend.py', 'tests/test_compact_statistics_window_pages.py'}
COMPACT_WINDOW_ADDED_PINS = {
    "models/decision_compact_statistics_window_v1.json",
    "scripts/build_compact_statistics_window.py",
}
COMPACT_WINDOW_BOUNDARIES = {
    "model_weights_changed": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "truth_policy_changed": False,
    "public_statistics_window_changed": True,
    "separate_statistics_projection_added": True,
    "original_statistics_calculation_changed": False,
    "historical_navigation_preserves_latest_window": True,
    "versioned_truth_publication_changed": False,
    "historical_ledger_rewritten": False,
    "workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "validation_gates_bypassed": False,
    "actual_trading_enabled": False,
}


EXIT1000_REVIEW = ROOT / "models/decision_source_surface_review_20260912_exit1000.json"
EXIT1000_REVIEW_SHA = "c0449de0047e5b83353650f7d24655dbb8635ad538e20364213b032b28c963a9"
EXIT1000_EXISTING_PATHS = {
    ".github/workflows/deploy_dc20_pages.yml", ".github/workflows/verify_decision_observations.yml",
    "decision.html", "scripts/build_compact_statistics_window.py", "scripts/settle_primary_observations.py",
    "scripts/sync_frozen_shadow_truth.py", "src/top10decision/decision/executable_profit_shadow_settlement.py",
    "tests/test_primary_observation_summary.py", "tests/test_compact_statistics_window.py",
}
EXIT1000_ADDED_PINS = {
    "models/decision_shadow_exit_policy_1000_v1.json", "scripts/sync_exit_1000_minute_truth.py",
    "src/top10decision/decision/shadow_exit_1000.py", "src/top10decision/decision/shadow_exit_minute_truth.py",
}
EXIT1000_ADDED_PATHS = EXIT1000_ADDED_PINS | {
    "tests/test_exit_1000_minute_truth.py", "tests/test_shadow_exit_1000.py",
    "tests/test_shadow_exit_1000_settlement.py", "tests/test_exit_1000_frontend_window.py",
    "docs/shadow_exit_1000_migration.md",
}
EXIT1000_BOUNDARIES = {
    "model_weights_changed": False,
    "new_profit_model_trained": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "promotion_model_changed": False,
    "entry_policy_changed": False,
    "truth_policy_changed": True,
    "scheduled_exit_1000_limit_hold_policy_added": True,
    "minute_truth_collection_and_publication_added": True,
    "return_statistics_separated_by_exit_policy": True,
    "historical_terminal_ledger_rewritten": False,
    "workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "validation_gates_bypassed": False,
    "actual_trading_enabled": False,
}


DEV_YAML_REVIEW = ROOT / "models/decision_source_surface_review_20260912_dev_yaml.json"
DEV_YAML_REVIEW_SHA = "79df484cad5e7ca7fb23201b99da1376e00e5bf44d5c9830af66c2819f92cea1"
DEV_YAML_SCOPE = "DEV_ONLY_HASH_LOCKED_PYYAML_COLLECTION_DEPENDENCY_NO_RUNTIME_MODEL_OR_TRUTH_CHANGE"
DEV_YAML_SOURCE_PATHS = {"requirements-dev.in", "requirements-dev.lock"}
DEV_YAML_BOUNDARIES = {
    "dev_test_dependency_added": True,
    "production_requirements_changed": False,
    "other_dependencies_changed": False,
    "model_weights_changed": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "promotion_model_changed": False,
    "entry_policy_changed": False,
    "exit_policy_changed": False,
    "historical_ledger_rewritten": False,
    "workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "validation_gates_bypassed": False,
    "actual_trading_enabled": False,
}


CI_PARTITION_REVIEW = ROOT / "models/decision_source_surface_review_20260912_ci_partition.json"
CI_PARTITION_REVIEW_SHA = "a43e8c11e0e6e1efb1474034761197c7e53eb8fd87880ab2c06fb00b16787395"
CI_PARTITION_SCOPE = "CI_ONLY_COMPLETE_TEST_PARTITION_AND_CLEAN_CHECKOUT_HISTORY_FIXTURE_NO_RUNTIME_OR_MODEL_CHANGE"
CI_PARTITION_EXISTING_PATHS = {
    ".github/workflows/test_decision_core.yml", "tests/test_compact_statistics_window_frontend.py",
}
CI_PARTITION_ADDED_PATHS = {"tests/test_decision_core_ci_partition.py"}
CI_PARTITION_BOUNDARIES = {
    "ci_partition_changed": True,
    "test_fixture_generation_changed": True,
    "test_coverage_reduced": False,
    "validation_gates_bypassed": False,
    "production_requirements_changed": False,
    "dev_dependencies_changed": False,
    "model_weights_changed": False,
    "ranking_algorithm_changed": False,
    "frozen_members_changed": False,
    "promotion_model_changed": False,
    "entry_policy_changed": False,
    "exit_policy_changed": False,
    "historical_ledger_rewritten": False,
    "production_workflow_scheduling_changed": False,
    "forward_epoch_activated": False,
    "actual_trading_enabled": False,
}


REPLAY_PIN_REVIEW = ROOT / "models/decision_source_surface_review_20260912_replay_pin_inventory.json"
REPLAY_PIN_REVIEW_SHA = "23d01351fa63600a5a29841cb23640ec7f0beed84914c3f6ad868c8a3c2b3d14"
REPLAY_PIN_SCOPE = "TEST_ONLY_EXACT_REVIEWED_SEVEN_PIN_SUCCESSOR_INVENTORY_NO_LOADER_MODEL_OR_TRUTH_CHANGE"
REPLAY_PIN_SOURCE = "tests/test_frozen_canonical_v2_replay.py"
REPLAY_PIN_BOUNDARIES = {
    "test_oracle_changed": True, "exact_pin_set_assertion_preserved": True,
    "unknown_or_missing_pins_accepted": False, "production_loader_changed": False,
    "production_required_pin_constant_changed": False, "dependency_files_changed": False,
    "model_weights_changed": False, "ranking_algorithm_changed": False,
    "promotion_model_changed": False, "entry_policy_changed": False, "exit_policy_changed": False,
    "frozen_members_changed": False, "historical_ledger_rewritten": False,
    "workflow_scheduling_changed": False, "forward_epoch_activated": False,
    "validation_gates_bypassed": False, "actual_trading_enabled": False,
}


def _replay_pin_live_source(path: str) -> bytes:
    assert isinstance(path, str) and path and not path.startswith("/") and "\\" not in path
    assert all(part not in ("", ".", "..") for part in path.split("/"))
    target = ROOT / path
    assert ROOT in target.resolve().parents
    assert not any(part.is_symlink() for part in (target, *target.parents) if part != ROOT)
    assert target.is_file()
    return target.read_bytes()


@lru_cache(maxsize=1)
def _parse_replay_pin_review(raw: bytes) -> dict:
    return json.loads(raw)


def _replay_pin_review(review: dict | None = None) -> dict:
    raw = _replay_pin_live_source(REPLAY_PIN_REVIEW.relative_to(ROOT).as_posix())
    assert hashlib.sha256(raw).hexdigest() == REPLAY_PIN_REVIEW_SHA
    approved = _parse_replay_pin_review(raw)
    review = approved if review is None else review
    assert review == approved
    assert review["schema_version"] == "decision_replay_pin_inventory_test_source_review_v1"
    assert review["approved_base_commit"] == "763ac86e8db225d363612f7a0a4792b29bd5237f"
    assert review["scope"] == REPLAY_PIN_SCOPE and review["boundaries"] == REPLAY_PIN_BOUNDARIES
    assert review["predecessor_evidence_path"] == CI_PARTITION_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(CI_PARTITION_REVIEW) == CI_PARTITION_REVIEW_SHA
    predecessor = json.loads(CI_PARTITION_REVIEW.read_bytes())
    assert len(review["preserved_evidence"]) == 22
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": CI_PARTITION_REVIEW.relative_to(ROOT).as_posix(), "sha256": CI_PARTITION_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert hashlib.sha256(_replay_pin_live_source(item["path"])).hexdigest() == item["sha256"]
    approvals = [(SHADOW_PRICE_REVIEW, SHADOW_PRICE_REVIEW_SHA),
                 (COMPACT_WINDOW_REVIEW, COMPACT_WINDOW_REVIEW_SHA), (EXIT1000_REVIEW, EXIT1000_REVIEW_SHA)]
    expected_approvals = [{"path": path.relative_to(ROOT).as_posix(), "sha256": digest,
                           "added_runtime_pins": json.loads(path.read_bytes())["added_runtime_pins"]}
                          for path, digest in approvals]
    assert review["approved_extension_reviews"] == expected_approvals
    approved_paths = [path for item in expected_approvals for path in item["added_runtime_pins"]]
    assert len(approved_paths) == len(set(approved_paths)) == 7
    assert set(approved_paths) == ({"models/decision_primary_profit_shadow_entry_price_policy_v2.json"}
                                   | COMPACT_WINDOW_ADDED_PINS | EXIT1000_ADDED_PINS)
    assert REQUIRED_ACTIVE_PIN_PATHS.isdisjoint(approved_paths)
    assert [item["path"] for item in review["source_changes"]] == [REPLAY_PIN_SOURCE]
    assert review["pin_count"] == 231
    assert review["pin_changes"] == [{
        "path": REPLAY_PIN_SOURCE,
        "baseline_sha256": "a3786639ca8b6310a6f67980ce28aa464b0b7cdff23cc86d8aa2daf70effb25e",
        "current_sha256": "718a24d19f6758a634c1d28280d352f6e05113410db89af53957ab48140b3f89",
    }]
    return review


def _source_before_replay_pin(path: str, review: dict | None = None) -> bytes:
    source = _replay_pin_live_source(path)
    if path not in {REPLAY_PIN_SOURCE, "models/decision_model_freeze.json", "forward/model_inventory.json"}:
        return source
    review = _replay_pin_review(review)
    if path == REPLAY_PIN_SOURCE:
        item = review["source_changes"][0]
        assert item["baseline_exists"] is True and item["reason"]
        assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
        lines = source.decode().splitlines(keepends=True)
        changes = item["inverse_changes"]
        assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
        previous_end = baseline_offset = 0
        for part in changes:
            assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
            assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
            assert type(part["current_start"]) is int and part["current_start"] > 0
            start = part["current_start"] - 1
            assert previous_end <= start <= len(lines)
            assert part["baseline_start"] - 1 == start + baseline_offset
            assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
            previous_end = start + len(part["current_lines"])
            baseline_offset += len(part["baseline_lines"]) - len(part["current_lines"])
        for part in reversed(changes):
            start = part["current_start"] - 1
            lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
        restored = "".join(lines).encode()
        assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
        return restored
    if path == "models/decision_model_freeze.json":
        assert hashlib.sha256(source).hexdigest() == review["current_manifest_sha256"]
        manifest = json.loads(source)
        assert len(manifest["pinned_files"]) == review["pin_count"] == 231
        change = review["pin_changes"][0]
        assert manifest["pinned_files"][change["path"]] == change["current_sha256"]
        assert hashlib.sha256(_replay_pin_live_source(change["path"])).hexdigest() == change["current_sha256"]
        manifest["pinned_files"][change["path"]] = change["baseline_sha256"]
        restored = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        assert hashlib.sha256(restored).hexdigest() == review["baseline_manifest_sha256"] == "ad34499be8e4c7a2e87e76736c1cd8a3599eda644e69a0bdbd1a854fe3c3b34c"
        return restored
    inventory = json.loads(source)
    assert source == (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    dep = review["inventory_update"]
    assert dep["path"] == path and dep["current_scope"] == REPLAY_PIN_SCOPE
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": REPLAY_PIN_REVIEW.relative_to(ROOT).as_posix(), "sha256": REPLAY_PIN_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": REPLAY_PIN_SCOPE,
    }
    for asset in inventory["assets"]:
        raw = _replay_pin_live_source(asset["path"])
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    before_manifest = _source_before_replay_pin("models/decision_model_freeze.json", review)
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(item for item in inventory["assets"] if item["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before_manifest).hexdigest(), bytes=len(before_manifest))
    restored = (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(restored).hexdigest() == dep["baseline_sha256"] == "a81aa11a26ec42dfab5121dd14d13c5ccb4eaa8a461ca9358547057776ce6535"
    return restored


def _state_before_replay_pin(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    review = _replay_pin_review(review)
    live = json.loads(_replay_pin_live_source("models/decision_model_freeze.json"))
    manifest = live if manifest is None else manifest
    assert manifest == live
    expected_paths = REQUIRED_ACTIVE_PIN_PATHS | set().union(*(item["added_runtime_pins"] for item in review["approved_extension_reviews"]))
    assert set(manifest["pinned_files"]) == expected_paths and len(expected_paths) == 231
    for path, expected in manifest["pinned_files"].items():
        assert hashlib.sha256(_replay_pin_live_source(path)).hexdigest() == expected
    _source_before_replay_pin(REPLAY_PIN_SOURCE, review)
    return (json.loads(_source_before_replay_pin("models/decision_model_freeze.json", review)),
            json.loads(_source_before_replay_pin("forward/model_inventory.json", review)))


def _ci_partition_live_source(path: str) -> bytes:
    # The already published CI review keeps its exact signed predecessor bytes.
    return _source_before_replay_pin(path)


def test_replay_pin_inventory_review_restores_complete_predecessor_without_model_changes():
    manifest, inventory = _state_before_replay_pin()
    assert len(manifest["pinned_files"]) == 231 and len(inventory["assets"]) == 42
    assert inventory["dependency_successor_review"]["sha256"] == CI_PARTITION_REVIEW_SHA
    restored = _source_before_replay_pin(REPLAY_PIN_SOURCE)
    assert restored.count(b'== REQUIRED_ACTIVE_PIN_PATHS') == 2
    assert b"def test_current_manifest_uses_exact_schema_loader_without_mutating_disk" in restored


@pytest.mark.parametrize("target", ["review", "test", "loader", "model", "manifest", "inventory", "old_review"])
def test_replay_pin_inventory_review_rechecks_live_bytes_after_cache_warmup(monkeypatch, target):
    targets = {"review": REPLAY_PIN_REVIEW, "test": ROOT / REPLAY_PIN_SOURCE,
               "loader": ROOT / "src/top10decision/decision/model_freeze.py",
               "model": ROOT / "models/decision_three_engines/promotion.joblib", "manifest": MANIFEST,
               "inventory": ROOT / "forward/model_inventory.json", "old_review": CI_PARTITION_REVIEW}
    _state_before_replay_pin()
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == targets[target] else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _state_before_replay_pin()


@pytest.mark.parametrize("mutation", ["scope", "base", "subset", "loader", "extra_source", "inverse",
                                     "unapproved_extension", "drop_evidence", "remove_pin", "extra_pin", "model_identity"])
def test_replay_pin_inventory_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_bytes())
    review = json.loads(REPLAY_PIN_REVIEW.read_bytes())
    if mutation == "scope": review["scope"] = "runtime"
    elif mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "subset": review["boundaries"]["exact_pin_set_assertion_preserved"] = False
    elif mutation == "loader": review["boundaries"]["production_loader_changed"] = True
    elif mutation == "extra_source": review["source_changes"].append(dict(review["source_changes"][0], path="requirements.lock"))
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "unapproved_extension": review["approved_extension_reviews"][0]["added_runtime_pins"].append("unreviewed.py")
    elif mutation == "drop_evidence": review["preserved_evidence"].pop()
    elif mutation == "remove_pin": del manifest["pinned_files"]["requirements-dev.lock"]
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _state_before_replay_pin(manifest, review)


@lru_cache(maxsize=1)
def _parse_ci_partition_review(raw: bytes) -> dict:
    # Parsing alone may be cached, never the current file or its digest.
    return json.loads(raw)


def _ci_partition_review(review: dict | None = None) -> dict:
    raw = _ci_partition_live_source(CI_PARTITION_REVIEW.relative_to(ROOT).as_posix())
    assert hashlib.sha256(raw).hexdigest() == CI_PARTITION_REVIEW_SHA
    approved = _parse_ci_partition_review(raw)
    review = approved if review is None else review
    assert review == approved
    assert review["schema_version"] == "decision_ci_partition_source_review_v1"
    assert review["approved_base_commit"] == "6ea9616014da3850ae4064c89e56e832608180d3"
    assert review["scope"] == CI_PARTITION_SCOPE
    assert review["boundaries"] == CI_PARTITION_BOUNDARIES
    assert review["predecessor_evidence_path"] == DEV_YAML_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(DEV_YAML_REVIEW) == DEV_YAML_REVIEW_SHA
    predecessor = json.loads(DEV_YAML_REVIEW.read_bytes())
    assert len(review["preserved_evidence"]) == 21
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": DEV_YAML_REVIEW.relative_to(ROOT).as_posix(), "sha256": DEV_YAML_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert hashlib.sha256(_ci_partition_live_source(item["path"])).hexdigest() == item["sha256"]
    paths = [item["path"] for item in review["source_changes"]]
    assert len(paths) == len(set(paths)) == 3
    assert set(paths) == CI_PARTITION_EXISTING_PATHS | CI_PARTITION_ADDED_PATHS
    assert review["pin_count"] == 231
    assert review["pin_changes"] == [{
        "path": ".github/workflows/test_decision_core.yml",
        "baseline_sha256": "f38b2b0ab78be89dd7992c49582b9991703f7bcb384ff30a90090ab6c9a6fba6",
        "current_sha256": "f4ad709bac2b6ed80370c17ece3b03aa5a7d3dc3211e94f9a46b57b7c0b1f5a1",
    }]
    return review


def _source_before_ci_partition(path: str, review: dict | None = None) -> bytes:
    source = _ci_partition_live_source(path)
    sources = CI_PARTITION_EXISTING_PATHS | CI_PARTITION_ADDED_PATHS
    if path not in sources | {"models/decision_model_freeze.json", "forward/model_inventory.json"}:
        return source
    review = _ci_partition_review(review)
    if path in sources:
        item = next(entry for entry in review["source_changes"] if entry["path"] == path)
        assert item["baseline_exists"] is (path in CI_PARTITION_EXISTING_PATHS)
        assert item["reason"] and len(source) == item["current_bytes"]
        assert hashlib.sha256(source).hexdigest() == item["current_sha256"]
        lines = source.decode().splitlines(keepends=True)
        changes = item["inverse_changes"]
        assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
        previous_end = baseline_offset = 0
        for part in changes:
            assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
            assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
            assert type(part["current_start"]) is int and part["current_start"] > 0
            start = part["current_start"] - 1
            assert previous_end <= start <= len(lines)
            assert part["baseline_start"] - 1 == start + baseline_offset
            assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
            previous_end = start + len(part["current_lines"])
            baseline_offset += len(part["baseline_lines"]) - len(part["current_lines"])
        for part in reversed(changes):
            start = part["current_start"] - 1
            lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
        restored = "".join(lines).encode()
        assert len(restored) == item["baseline_bytes"]
        assert hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
        if path in CI_PARTITION_ADDED_PATHS:
            assert restored == b"" and item["baseline_bytes"] == 0
        return restored
    if path == "models/decision_model_freeze.json":
        assert hashlib.sha256(source).hexdigest() == review["current_manifest_sha256"]
        manifest = json.loads(source)
        assert len(manifest["pinned_files"]) == review["pin_count"] == 231
        change = review["pin_changes"][0]
        assert manifest["pinned_files"][change["path"]] == change["current_sha256"]
        assert hashlib.sha256(_ci_partition_live_source(change["path"])).hexdigest() == change["current_sha256"]
        manifest["pinned_files"][change["path"]] = change["baseline_sha256"]
        restored = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        assert hashlib.sha256(restored).hexdigest() == review["baseline_manifest_sha256"] == "f383aad6ec12d8c54c3c2956ba74e478db9fbc3436c63cf656ac1806c7dc32c8"
        return restored
    inventory = json.loads(source)
    assert source == (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    dep = review["inventory_update"]
    assert dep["path"] == path and dep["current_scope"] == CI_PARTITION_SCOPE
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": CI_PARTITION_REVIEW.relative_to(ROOT).as_posix(), "sha256": CI_PARTITION_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": CI_PARTITION_SCOPE,
    }
    for asset in inventory["assets"]:
        raw = _ci_partition_live_source(asset["path"])
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    before_manifest = _source_before_ci_partition("models/decision_model_freeze.json", review)
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(item for item in inventory["assets"] if item["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before_manifest).hexdigest(), bytes=len(before_manifest))
    restored = (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(restored).hexdigest() == dep["baseline_sha256"] == "3dd57156fd73a5788d79307946b03a87942c1340bd0f4b0010b7bfdb212902cd"
    return restored


def _state_before_ci_partition(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    if manifest is not None:
        manifest = _state_before_replay_pin(manifest)[0]
    review = _ci_partition_review(review)
    live = json.loads(_ci_partition_live_source("models/decision_model_freeze.json"))
    manifest = live if manifest is None else manifest
    assert manifest == live
    assert len(manifest["pinned_files"]) == 231
    for path, expected in manifest["pinned_files"].items():
        assert hashlib.sha256(_ci_partition_live_source(path)).hexdigest() == expected
    for path in CI_PARTITION_EXISTING_PATHS | CI_PARTITION_ADDED_PATHS:
        _source_before_ci_partition(path, review)
    return (json.loads(_source_before_ci_partition("models/decision_model_freeze.json", review)),
            json.loads(_source_before_ci_partition("forward/model_inventory.json", review)))


def _dev_yaml_live_source(path: str) -> bytes:
    # Preserve the signed dev-dependency review; rewind only this CI repair.
    return _source_before_ci_partition(path)


def test_ci_partition_repair_restores_exact_predecessor_models_inventory_and_sources():
    manifest, inventory = _state_before_ci_partition()
    assert len(manifest["pinned_files"]) == 231 and len(inventory["assets"]) == 42
    assert inventory["dependency_successor_review"]["sha256"] == DEV_YAML_REVIEW_SHA
    assert _source_before_ci_partition("tests/test_decision_core_ci_partition.py") == b""
    before = _source_before_ci_partition(".github/workflows/test_decision_core.yml")
    after = _ci_partition_live_source(".github/workflows/test_decision_core.yml")
    assert before.split(b"  frozen-canonical-replay:\n", 1)[1] == after.split(b"  frozen-canonical-replay:\n", 1)[1]
    assert before.split(b"jobs:\n", 1)[0] == after.split(b"jobs:\n", 1)[0]


@pytest.mark.parametrize("target", ["review", "workflow", "frontend", "partition_test", "lock",
                                     "model", "manifest", "inventory", "old_review"])
def test_ci_partition_repair_rechecks_every_live_boundary_after_cache_warmup(monkeypatch, target):
    targets = {"review": CI_PARTITION_REVIEW, "workflow": ROOT / ".github/workflows/test_decision_core.yml",
               "frontend": ROOT / "tests/test_compact_statistics_window_frontend.py",
               "partition_test": ROOT / "tests/test_decision_core_ci_partition.py",
               "lock": ROOT / "requirements-dev.lock", "model": ROOT / "models/decision_three_engines/promotion.joblib",
               "manifest": MANIFEST, "inventory": ROOT / "forward/model_inventory.json", "old_review": DEV_YAML_REVIEW}
    _state_before_ci_partition()
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == targets[target] else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _state_before_ci_partition()


@pytest.mark.parametrize("mutation", ["scope", "base", "coverage", "dependency", "runtime", "extra_source",
                                     "source_sha", "inverse", "added_exists", "drop_evidence", "remove_pin", "extra_pin", "model_identity"])
def test_ci_partition_repair_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_bytes())
    review = json.loads(CI_PARTITION_REVIEW.read_bytes())
    if mutation == "scope": review["scope"] = "runtime"
    elif mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "coverage": review["boundaries"]["test_coverage_reduced"] = True
    elif mutation == "dependency": review["boundaries"]["dev_dependencies_changed"] = True
    elif mutation == "runtime": review["boundaries"]["ranking_algorithm_changed"] = True
    elif mutation == "extra_source": review["source_changes"].append(dict(review["source_changes"][0], path="requirements.lock"))
    elif mutation == "source_sha": review["source_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "added_exists": next(item for item in review["source_changes"] if item["path"] in CI_PARTITION_ADDED_PATHS)["baseline_exists"] = True
    elif mutation == "drop_evidence": review["preserved_evidence"].pop()
    elif mutation == "remove_pin": del manifest["pinned_files"]["requirements-dev.lock"]
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _state_before_ci_partition(manifest, review)


@lru_cache(maxsize=1)
def _parse_dev_yaml_review(raw: bytes) -> dict:
    # Only decoding is cached; live bytes and their hash are always rechecked.
    return json.loads(raw)


def _dev_yaml_review(review: dict | None = None) -> dict:
    raw = _dev_yaml_live_source(DEV_YAML_REVIEW.relative_to(ROOT).as_posix())
    assert hashlib.sha256(raw).hexdigest() == DEV_YAML_REVIEW_SHA
    approved = _parse_dev_yaml_review(raw)
    review = approved if review is None else review
    assert review == approved
    assert review["schema_version"] == "decision_dev_yaml_dependency_source_review_v1"
    assert review["approved_base_commit"] == "b84acfe2d7dbd029a98446e8d19cabfbbea7ad84"
    assert review["scope"] == DEV_YAML_SCOPE
    assert review["boundaries"] == DEV_YAML_BOUNDARIES
    assert review["predecessor_evidence_path"] == EXIT1000_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(EXIT1000_REVIEW) == EXIT1000_REVIEW_SHA
    predecessor = json.loads(EXIT1000_REVIEW.read_bytes())
    assert len(review["preserved_evidence"]) == 20
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": EXIT1000_REVIEW.relative_to(ROOT).as_posix(), "sha256": EXIT1000_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert hashlib.sha256(_dev_yaml_live_source(item["path"])).hexdigest() == item["sha256"]
    assert review["package"] == {
        "name": "PyYAML", "version": "6.0.3", "scope": "TEST_ONLY",
        "metadata_url": "https://pypi.org/pypi/PyYAML/6.0.3/json",
        "wheel_filename": "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "wheel_sha256": "ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc",
        "yanked": False,
    }
    paths = [item["path"] for item in review["source_changes"]]
    assert len(paths) == len(set(paths)) == 2 and set(paths) == DEV_YAML_SOURCE_PATHS
    assert review["pin_count"] == 231
    assert review["pin_changes"] == [{
        "path": "requirements-dev.lock",
        "baseline_sha256": "a63cc07e54091c4c7c35801a02c800d7294f73acbe66f6cddd0351ea74cf19d0",
        "current_sha256": "773ce43677ceff7e5829252816ba017738011ff60a389d60f0435b96264005f2",
    }]
    return review


def _source_before_dev_yaml(path: str, review: dict | None = None) -> bytes:
    source = _dev_yaml_live_source(path)
    if path not in DEV_YAML_SOURCE_PATHS | {"models/decision_model_freeze.json", "forward/model_inventory.json"}:
        return source
    review = _dev_yaml_review(review)
    if path in DEV_YAML_SOURCE_PATHS:
        item = next(entry for entry in review["source_changes"] if entry["path"] == path)
        assert hashlib.sha256(source).hexdigest() == item["current_sha256"]
        assert len(source) == item["current_bytes"]
        added = item["added_text"].encode()
        assert source.count(added) == 1
        restored = source.replace(added, b"", 1)
        assert len(restored) == item["baseline_bytes"]
        assert hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
        return restored
    if path == "models/decision_model_freeze.json":
        assert hashlib.sha256(source).hexdigest() == review["current_manifest_sha256"]
        manifest = json.loads(source)
        assert len(manifest["pinned_files"]) == review["pin_count"] == 231
        change = review["pin_changes"][0]
        assert manifest["pinned_files"][change["path"]] == change["current_sha256"]
        assert hashlib.sha256(_dev_yaml_live_source(change["path"])).hexdigest() == change["current_sha256"]
        manifest["pinned_files"][change["path"]] = change["baseline_sha256"]
        restored = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        assert hashlib.sha256(restored).hexdigest() == review["baseline_manifest_sha256"] == "5382c25246abaa0f0c7a0fef78d0171cac005589465deb2ea8f095031f8cd20f"
        return restored
    inventory = json.loads(source)
    assert source == (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    dep = review["inventory_update"]
    assert dep["path"] == path
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": DEV_YAML_REVIEW.relative_to(ROOT).as_posix(), "sha256": DEV_YAML_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": DEV_YAML_SCOPE,
    }
    for asset in inventory["assets"]:
        raw = _dev_yaml_live_source(asset["path"])
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    before_manifest = _source_before_dev_yaml("models/decision_model_freeze.json", review)
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(item for item in inventory["assets"] if item["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before_manifest).hexdigest(), bytes=len(before_manifest))
    restored = (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(restored).hexdigest() == dep["baseline_sha256"] == "f56bdee5629abf11302eb2a0ba02b18a1ac1bf27ea407f8631f85115816da167"
    return restored


def _state_before_dev_yaml(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    if manifest is not None:
        manifest = _state_before_ci_partition(manifest)[0]
    review = _dev_yaml_review(review)
    live = json.loads(_dev_yaml_live_source("models/decision_model_freeze.json"))
    manifest = live if manifest is None else manifest
    assert manifest == live
    assert len(manifest["pinned_files"]) == 231
    for path, expected in manifest["pinned_files"].items():
        assert hashlib.sha256(_dev_yaml_live_source(path)).hexdigest() == expected
    for path in DEV_YAML_SOURCE_PATHS:
        _source_before_dev_yaml(path, review)
    return (json.loads(_source_before_dev_yaml("models/decision_model_freeze.json", review)),
            json.loads(_source_before_dev_yaml("forward/model_inventory.json", review)))


def _exit1000_live_source(path: str) -> bytes:
    # The old review remains immutable and sees only the precisely restored
    # predecessor bytes. The new dev dependency is verified before this rewind.
    return _source_before_dev_yaml(path)


def test_dev_yaml_repair_is_dev_only_and_restores_all_previous_pins_and_assets():
    manifest, inventory = _state_before_dev_yaml()
    assert len(manifest["pinned_files"]) == 231 and len(inventory["assets"]) == 42
    assert inventory["dependency_successor_review"]["sha256"] == EXIT1000_REVIEW_SHA
    assert _source_before_dev_yaml("requirements-dev.in") == b"-r requirements.txt\n\npytest==9.1.1\n"
    assert b"pyyaml" not in _source_before_dev_yaml("requirements-dev.lock").lower()
    assert b"pyyaml" not in _dev_yaml_live_source("requirements.lock").lower()


@pytest.mark.parametrize("target", ["review", "input", "lock", "production_lock", "model", "manifest", "inventory"])
def test_dev_yaml_repair_rechecks_live_bytes_after_cache_warmup(monkeypatch, target):
    targets = {"review": DEV_YAML_REVIEW, "input": ROOT / "requirements-dev.in",
               "lock": ROOT / "requirements-dev.lock", "production_lock": ROOT / "requirements.lock",
               "model": ROOT / "models/decision_three_engines/promotion.joblib",
               "manifest": MANIFEST, "inventory": ROOT / "forward/model_inventory.json"}
    _state_before_dev_yaml()
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == targets[target] else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _state_before_dev_yaml()


@pytest.mark.parametrize("mutation", ["scope", "version", "wheel_sha", "base", "extra_dependency",
                                    "runtime_change", "remove_pin", "extra_pin", "model_identity", "evidence"])
def test_dev_yaml_repair_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_bytes())
    review = json.loads(DEV_YAML_REVIEW.read_bytes())
    if mutation == "scope": review["scope"] = "runtime"
    elif mutation == "version": review["package"]["version"] = "0"
    elif mutation == "wheel_sha": review["package"]["wheel_sha256"] = "0" * 64
    elif mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "extra_dependency": review["source_changes"].append(dict(review["source_changes"][0], path="requirements.lock"))
    elif mutation == "runtime_change": review["boundaries"]["production_requirements_changed"] = True
    elif mutation == "remove_pin": del manifest["pinned_files"]["requirements-dev.lock"]
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    elif mutation == "model_identity": manifest["training_cutoff_signal_date"] = "20260911"
    else: review["preserved_evidence"].pop()
    with pytest.raises(AssertionError):
        _state_before_dev_yaml(manifest, review)


def _exit1000_review(review: dict | None = None) -> dict:
    raw = _exit1000_live_source(EXIT1000_REVIEW.relative_to(ROOT).as_posix())
    assert hashlib.sha256(raw).hexdigest() == EXIT1000_REVIEW_SHA
    review = json.loads(raw) if review is None else review
    assert review["schema_version"] == "decision_exit_1000_policy_source_review_v1"
    assert review["approved_base_commit"] == "bd6103a674ff24fd8db54dc3832afcbc983f6de5"
    assert review["scope"] == "VERSIONED_1000_LIMIT_HOLD_EXIT_TRUTH_NO_NEW_MODEL_TRAINING_OR_HISTORICAL_REWRITE"
    assert review["boundaries"] == EXIT1000_BOUNDARIES
    assert review["exit_policy_id"] == "dc20_exit_1000_limit_hold_20260912_v1"
    assert review["effective_scheduled_exit_date"] == "20260914"
    assert review["publication_scope"] == "PUBLICATION_EXCLUDES_DATA_AND_OUTPUTS_TREE_MUTATIONS"
    assert review["predecessor_evidence_path"] == COMPACT_WINDOW_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(COMPACT_WINDOW_REVIEW) == COMPACT_WINDOW_REVIEW_SHA
    paths = [item["path"] for item in review["source_changes"]]
    expected = EXIT1000_EXISTING_PATHS | EXIT1000_ADDED_PATHS | {"models/decision_model_freeze.json"}
    assert len(paths) == len(set(paths)) == len(expected) and set(paths) == expected
    assert review["added_runtime_pins"] == sorted(EXIT1000_ADDED_PINS)
    predecessor = json.loads(COMPACT_WINDOW_REVIEW.read_bytes())
    assert len(review["preserved_evidence"]) == 19
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": COMPACT_WINDOW_REVIEW.relative_to(ROOT).as_posix(), "sha256": COMPACT_WINDOW_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert hashlib.sha256(_exit1000_live_source(item["path"])).hexdigest() == item["sha256"]
    tests = review["regression_tests"]
    assert len(tests) == len({item["path"] for item in tests})
    assert {item["path"] for item in tests} == {path for path in expected if path.startswith("tests/")}
    for item in tests:
        assert hashlib.sha256(_exit1000_live_source(item["path"])).hexdigest() == item["sha256"]
    return review


def _source_before_exit1000(path: str, review: dict | None = None) -> bytes:
    raw_review = _exit1000_live_source(EXIT1000_REVIEW.relative_to(ROOT).as_posix())
    assert hashlib.sha256(raw_review).hexdigest() == EXIT1000_REVIEW_SHA
    review = _parse_exit1000_review(raw_review) if review is None else review
    source = _exit1000_live_source(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert item["baseline_exists"] is (path not in EXIT1000_ADDED_PATHS)
    assert item["reason"] and len(source) == item["current_bytes"]
    assert hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
    previous_end = baseline_offset = 0
    for part in changes:
        assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
        assert type(part["current_start"]) is int and part["current_start"] > 0
        start = part["current_start"] - 1
        assert start >= previous_end and start <= len(lines)
        assert part["baseline_start"] - 1 == start + baseline_offset
        assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
        previous_end = start + len(part["current_lines"])
        baseline_offset += len(part["baseline_lines"]) - len(part["current_lines"])
    for part in reversed(changes):
        start = part["current_start"] - 1
        lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    if path in EXIT1000_ADDED_PATHS:
        assert restored == b"" and item["baseline_bytes"] == 0
    return restored


@lru_cache(maxsize=1)
def _parse_exit1000_review(raw: bytes) -> dict:
    # Cache decoding only; every use rechecks live review/source bytes and SHA.
    return json.loads(raw)


def _state_before_exit1000(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    if manifest is not None:
        manifest = _state_before_dev_yaml(manifest)[0]
    review = _exit1000_review(review)
    live_manifest = _exit1000_live_source("models/decision_model_freeze.json")
    manifest = json.loads(live_manifest) if manifest is None else manifest
    assert len(manifest["pinned_files"]) == review["pin_count"] == 231
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected_sha in manifest["pinned_files"].items():
        assert hashlib.sha256(_exit1000_live_source(path)).hexdigest() == expected_sha
    for item in review["source_changes"]:
        _source_before_exit1000(item["path"], review)
    before = _source_before_exit1000("models/decision_model_freeze.json", review)
    restored = json.loads(before)
    assert len(restored["pinned_files"]) == 227
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in EXIT1000_ADDED_PINS:
        assert path not in restored["pinned_files"]
        assert expected["pinned_files"].pop(path) == hashlib.sha256(_exit1000_live_source(path)).hexdigest()
    for path in EXIT1000_EXISTING_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_exit1000(path, review)).hexdigest()
    assert expected == restored  # Complete prior pins AND every model/identity field.
    assert restored["source_surface_rotation"] == manifest["source_surface_rotation"]
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    inventory_raw = _exit1000_live_source(dep["path"])
    inventory = json.loads(inventory_raw)
    assert inventory_raw == (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(
        path=EXIT1000_REVIEW.relative_to(ROOT).as_posix(), sha256=EXIT1000_REVIEW_SHA,
        approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _exit1000_live_source(asset["path"])
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(asset for asset in inventory["assets"] if asset["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_exit1000_review_restores_complete_predecessor_without_model_or_history_changes():
    manifest, inventory = _state_before_exit1000()
    review = _exit1000_review()
    assert _canonical_sha256(manifest) == _compact_window_review()["current_manifest_canonical_sha256"]
    assert inventory["dependency_successor_review"]["sha256"] == COMPACT_WINDOW_REVIEW_SHA
    assert all(_source_before_exit1000(path) == b"" for path in EXIT1000_ADDED_PATHS)
    assert review["boundaries"]["truth_policy_changed"] is True
    assert review["boundaries"]["new_profit_model_trained"] is False
    assert len(inventory["assets"]) == 42 and len(review["preserved_evidence"]) == 19


@pytest.mark.parametrize("target", [
    "review", "html", "engine", "loader", "collector", "config", "model", "inventory", "manifest",
])
def test_exit1000_review_checks_live_bytes_after_decode_cache_warmup(monkeypatch, target):
    targets = {
        "review": EXIT1000_REVIEW, "html": ROOT / "decision.html",
        "engine": ROOT / "src/top10decision/decision/shadow_exit_1000.py",
        "loader": ROOT / "src/top10decision/decision/shadow_exit_minute_truth.py",
        "collector": ROOT / "scripts/sync_exit_1000_minute_truth.py",
        "config": ROOT / "models/decision_shadow_exit_policy_1000_v1.json",
        "model": ROOT / "models/decision_three_engines/promotion.joblib",
        "inventory": ROOT / "forward/model_inventory.json", "manifest": MANIFEST,
    }
    _state_before_exit1000()
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == targets[target] else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _state_before_exit1000()


@pytest.mark.parametrize("mutation", [
    "base", "scope", "policy", "effective_date", "truth_policy", "training", "history", "entry",
    "extra_path", "current_sha", "baseline_sha", "inverse", "extra_pin", "remove_pin",
    "model_identity", "added_exists", "drop_evidence", "inventory_protection", "inventory_baseline",
])
def test_exit1000_review_rejects_unreviewed_policy_source_and_inventory_changes(mutation):
    manifest = json.loads(MANIFEST.read_bytes())
    review = json.loads(EXIT1000_REVIEW.read_bytes())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "scope": review["scope"] = "DISPLAY_ONLY"
    elif mutation == "policy": review["exit_policy_id"] = "old_open"
    elif mutation == "effective_date": review["effective_scheduled_exit_date"] = "20260911"
    elif mutation == "truth_policy": review["boundaries"]["truth_policy_changed"] = False
    elif mutation == "training": review["boundaries"]["new_profit_model_trained"] = True
    elif mutation == "history": review["boundaries"]["historical_terminal_ledger_rewritten"] = True
    elif mutation == "entry": review["boundaries"]["entry_policy_changed"] = True
    elif mutation == "extra_path": review["source_changes"].append(dict(review["source_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "current_sha": review["source_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "baseline_sha": review["source_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    elif mutation == "remove_pin": del manifest["pinned_files"]["scripts/sync_exit_1000_minute_truth.py"]
    elif mutation == "model_identity": manifest["training_cutoff_signal_date"] = "20260911"
    elif mutation == "added_exists": next(item for item in review["source_changes"] if item["path"] in EXIT1000_ADDED_PATHS)["baseline_exists"] = True
    elif mutation == "drop_evidence": review["preserved_evidence"].pop()
    elif mutation == "inventory_protection": review["inventory_update"]["protected_canonical_sha256"] = "0" * 64
    else: review["inventory_update"]["baseline_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _state_before_exit1000(manifest, review)


def _compact_window_review(review: dict | None = None) -> dict:
    assert not COMPACT_WINDOW_REVIEW.is_symlink()
    assert _sha256(COMPACT_WINDOW_REVIEW) == COMPACT_WINDOW_REVIEW_SHA
    review = json.loads(COMPACT_WINDOW_REVIEW.read_text()) if review is None else review
    assert review["schema_version"] == "decision_compact_statistics_window_review_v1"
    assert review["approved_base_commit"] == "1c7b7345800f163e7c0eefc272deba321fc30d9d"
    assert review["scope"] == "INDEPENDENT_D0910_PUBLIC_STATISTICS_WINDOW_OLD_LEDGER_TRUTH_AND_MODELS_PRESERVED"
    assert review["boundaries"] == COMPACT_WINDOW_BOUNDARIES
    assert review["start_signal_date"] == "20260910"
    assert review["predecessor_evidence_path"] == COPY_DELETE_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(COPY_DELETE_REVIEW) == COPY_DELETE_REVIEW_SHA
    paths = [item["path"] for item in review["source_changes"]]
    expected = COMPACT_WINDOW_EXISTING_PATHS | COMPACT_WINDOW_ADDED_PATHS | {"models/decision_model_freeze.json"}
    assert len(paths) == len(set(paths)) == len(expected)
    assert set(paths) == expected
    assert review["added_runtime_pins"] == sorted(COMPACT_WINDOW_ADDED_PINS)
    predecessor = json.loads(COPY_DELETE_REVIEW.read_text())
    assert len(review["preserved_evidence"]) == 18
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": COPY_DELETE_REVIEW.relative_to(ROOT).as_posix(), "sha256": COPY_DELETE_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    regressions = review["regression_tests"]
    assert {item["path"] for item in regressions} == {p for p in expected if p.startswith("tests/")}
    assert len(regressions) == len({item["path"] for item in regressions})
    for item in regressions:
        assert not (ROOT / item["path"]).is_symlink()
        assert hashlib.sha256(_source_before_exit1000(item["path"])).hexdigest() == item["sha256"]
    return review


@lru_cache(maxsize=1)
def _parse_compact_window_review(raw: bytes) -> dict:
    # Only pure parsing is cached; no mutable source, hash or state is cached.
    return json.loads(raw)


def _source_before_compact_window(path: str, review: dict | None = None) -> bytes:
    assert not COMPACT_WINDOW_REVIEW.is_symlink()
    raw_review = COMPACT_WINDOW_REVIEW.read_bytes()
    assert hashlib.sha256(raw_review).hexdigest() == COMPACT_WINDOW_REVIEW_SHA
    review = _parse_compact_window_review(raw_review) if review is None else review
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_exit1000(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert item["baseline_exists"] is (path not in COMPACT_WINDOW_ADDED_PATHS)
    assert item["reason"]
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
    for part in reversed(changes):
        assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
        assert type(part["current_start"]) is int and part["current_start"] > 0
        start = part["current_start"] - 1
        assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
        lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    if path in COMPACT_WINDOW_ADDED_PATHS:
        assert restored == b"" and item["baseline_bytes"] == 0
    return restored


def _state_before_compact_window(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    review = _compact_window_review(review)
    manifest, inventory = _state_before_exit1000(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 227
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_exit1000(path)).hexdigest() == expected
    for item in review["source_changes"]:
        _source_before_compact_window(item["path"], review)
    before = _source_before_compact_window("models/decision_model_freeze.json", review)
    restored = json.loads(before)
    assert len(restored["pinned_files"]) == 225
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in COMPACT_WINDOW_ADDED_PINS:
        assert path not in restored["pinned_files"]
        assert expected["pinned_files"].pop(path) == hashlib.sha256(_source_before_exit1000(path)).hexdigest()
    for path in COMPACT_WINDOW_EXISTING_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_compact_window(path, review)).hexdigest()
    assert expected == restored  # Complete prior map and non-pin model identity.
    assert restored["source_surface_rotation"] == manifest["source_surface_rotation"]
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(
        path=COMPACT_WINDOW_REVIEW.relative_to(ROOT).as_posix(), sha256=COMPACT_WINDOW_REVIEW_SHA,
        approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_exit1000(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(asset for asset in inventory["assets"] if asset["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_compact_window_review_preserves_complete_prior_state_and_models():
    manifest, inventory = _state_before_compact_window()
    predecessor = json.loads(COPY_DELETE_REVIEW.read_text())
    assert _canonical_sha256(manifest) == predecessor["current_manifest_canonical_sha256"]
    assert inventory["dependency_successor_review"]["sha256"] == COPY_DELETE_REVIEW_SHA
    for path in COMPACT_WINDOW_ADDED_PATHS:
        assert _source_before_compact_window(path) == b""
    config = json.loads((ROOT / "models/decision_compact_statistics_window_v1.json").read_text())
    assert config == {
        "schema_version": "dc20_compact_statistics_window_config_v1",
        "window_id": "dc20_compact_statistics_from_d20260910",
        "start_signal_date": "20260910", "date_axis": "signal_date_inclusive",
        "profit_scope": "NATURAL_FROZEN_PRIMARY_MIXED_SHADOW",
        "promotion_scope": "FROZEN_PRIMARY_PROMOTION_TOP3",
        "research_only": True, "source_ledger_mutation_allowed": False,
    }


@pytest.mark.parametrize("target", ["review", "source", "added_script", "config"])
def test_compact_window_review_live_byte_changes_are_not_hidden_by_decode_cache(monkeypatch, target):
    paths = {"review": COMPACT_WINDOW_REVIEW, "source": ROOT / "decision.html",
             "added_script": ROOT / "scripts/build_compact_statistics_window.py",
             "config": ROOT / "models/decision_compact_statistics_window_v1.json"}
    path = "decision.html" if target == "review" else paths[target].relative_to(ROOT).as_posix()
    _source_before_compact_window(path)
    _source_before_compact_window(path)
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == paths[target] else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _source_before_compact_window(path)


@pytest.mark.parametrize("mutation", ["base", "scope", "truth_policy", "window", "start_date", "history", "extra_path", "current_sha", "baseline_sha", "inverse", "extra_pin", "remove_pin", "model_policy", "added_exists", "drop_evidence"])
def test_compact_window_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    review = json.loads(COMPACT_WINDOW_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "scope": review["scope"] = "DISPLAY_ONLY"
    elif mutation == "truth_policy": review["boundaries"]["truth_policy_changed"] = True
    elif mutation == "window": review["boundaries"]["public_statistics_window_changed"] = False
    elif mutation == "start_date": review["start_signal_date"] = "20260828"
    elif mutation == "history": review["boundaries"]["historical_ledger_rewritten"] = True
    elif mutation == "extra_path": review["source_changes"].append(dict(review["source_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "current_sha": review["source_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "baseline_sha": review["source_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    elif mutation == "remove_pin": del manifest["pinned_files"]["scripts/build_compact_statistics_window.py"]
    elif mutation == "added_exists": next(item for item in review["source_changes"] if item["path"] in COMPACT_WINDOW_ADDED_PATHS)["baseline_exists"] = True
    elif mutation == "drop_evidence": review["preserved_evidence"].pop()
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _state_before_compact_window(manifest, review)


def _copy_delete_review(review: dict | None = None) -> dict:
    assert not COPY_DELETE_REVIEW.is_symlink()
    assert _sha256(COPY_DELETE_REVIEW) == COPY_DELETE_REVIEW_SHA
    review = json.loads(COPY_DELETE_REVIEW.read_text()) if review is None else review
    assert review["schema_version"] == "decision_copy_delete_display_review_v1"
    assert review["approved_base_commit"] == "fa9605013f6f370eb14eee6e67838862c9e4db5d"
    assert review["scope"] == "COPY_AND_STATISTICS_DISPLAY_ONLY_CALCULATION_AND_VALIDATION_UNCHANGED"
    assert review["boundaries"] == COPY_DELETE_BOUNDARIES
    assert review["predecessor_evidence_path"] == SHADOW_PRICE_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(SHADOW_PRICE_REVIEW) == SHADOW_PRICE_REVIEW_SHA
    paths = [item["path"] for item in review["source_changes"]]
    assert len(paths) == len(set(paths)) == 4
    assert set(paths) == COPY_DELETE_PATHS | {"models/decision_model_freeze.json"}
    assert review["added_runtime_pins"] == []
    predecessor = json.loads(SHADOW_PRICE_REVIEW.read_text())
    assert len(review["preserved_evidence"]) == 17
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": SHADOW_PRICE_REVIEW.relative_to(ROOT).as_posix(), "sha256": SHADOW_PRICE_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_compact_rank_statistics.py"
    assert hashlib.sha256(_source_before_compact_window(review["regression_test"]["path"])).hexdigest() == review["regression_test"]["sha256"]
    return review


@lru_cache(maxsize=1)
def _parse_copy_delete_review(raw: bytes) -> dict:
    # Pure decoding only. Every use authenticates freshly read live bytes.
    return json.loads(raw)


def _source_before_copy_delete(path: str, review: dict | None = None) -> bytes:
    assert not COPY_DELETE_REVIEW.is_symlink()
    raw_review = COPY_DELETE_REVIEW.read_bytes()
    assert hashlib.sha256(raw_review).hexdigest() == COPY_DELETE_REVIEW_SHA
    review = _parse_copy_delete_review(raw_review) if review is None else review
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_compact_window(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert item["baseline_exists"] is True and item["reason"]
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
    for part in reversed(changes):
        assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
        assert type(part["current_start"]) is int and part["current_start"] > 0
        start = part["current_start"] - 1
        assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
        lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _state_before_copy_delete(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    review = _copy_delete_review(review)
    manifest, inventory = _state_before_compact_window(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 225
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_compact_window(path)).hexdigest() == expected
    for item in review["source_changes"]:
        _source_before_copy_delete(item["path"], review)
    before = _source_before_copy_delete("models/decision_model_freeze.json", review)
    restored = json.loads(before)
    assert len(restored["pinned_files"]) == 225
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    assert COPY_DELETE_PATHS & set(expected["pinned_files"]) == {"decision.html"}
    expected["pinned_files"]["decision.html"] = hashlib.sha256(_source_before_copy_delete("decision.html", review)).hexdigest()
    assert expected == restored  # Exactly one pin changes; all other identity is unchanged.
    assert restored["source_surface_rotation"] == manifest["source_surface_rotation"]
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(
        path=COPY_DELETE_REVIEW.relative_to(ROOT).as_posix(), sha256=COPY_DELETE_REVIEW_SHA,
        approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_compact_window(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(asset for asset in inventory["assets"] if asset["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_copy_delete_review_preserves_complete_prior_state_and_models():
    manifest, inventory = _state_before_copy_delete()
    assert _canonical_sha256(manifest) == _shadow_price_review()["current_manifest_canonical_sha256"]
    assert inventory["dependency_successor_review"]["sha256"] == SHADOW_PRICE_REVIEW_SHA
    assert all(value is False for value in _copy_delete_review()["boundaries"].values())


def test_copy_delete_is_exactly_requested_display_changes():
    previous = _source_before_copy_delete("decision.html").decode()
    changes = [('    .compact-dashboard .compact-disclosure > summary { padding: 6px 8px; font-size: .75rem; }\n', '    .compact-dashboard .compact-disclosure > summary { padding: 6px 8px; font-size: .75rem; }\n    .compact-dashboard .compact-verification-dates { padding: 6px 8px; font-size: .75rem; font-weight: 650; }\n    .compact-dashboard .profit-cumulative-inline { display: inline-flex; flex-wrap: nowrap; align-items: baseline; gap: 6px; white-space: nowrap; }\n    .compact-dashboard .profit-cumulative-inline .score-secondary { color: var(--muted); font-weight: 400; }\n'), ('      if (!source || source.loaded.shadow.selectionOnlyCutover) return \'<p class="stats-note warn">盈利累计统计尚未通过独立账本校验，胜率和收益暂不可用。</p>\';\n', "      if (!source || source.loaded.shadow.selectionOnlyCutover) return '';\n"), ('          return `<tr><th scope="row" class="left"><span class="rank-mark rank-profit" aria-label="盈利排序第${slot}名">盈${slot}</span></th><td>${c.selected_slots}</td><td>${pending}</td><td>${c.wins_after_cost} / ${c.t1_settled_slots}</td><td>${c.t1_settled_slots ? pct(c.win_rate) : noSettled}</td><td>${c.t1_settled_slots ? signedPct(c.mean_net_return_after_cost) : noSettled}</td><td>${cumulative}<div class="score-secondary">${c.effective_dates} 个完整日</div></td><td>${drawdown}</td></tr>`;\n', '          return `<tr><th scope="row" class="left"><span class="rank-mark rank-profit" aria-label="盈利排序第${slot}名">盈${slot}</span></th><td>${c.selected_slots}</td><td>${pending}</td><td>${c.wins_after_cost} / ${c.t1_settled_slots}</td><td>${c.t1_settled_slots ? pct(c.win_rate) : noSettled}</td><td class="${valueTone(c.t1_settled_slots ? c.mean_net_return_after_cost : null)}">${c.t1_settled_slots ? signedPct(c.mean_net_return_after_cost) : noSettled}</td><td><span class="profit-cumulative-inline"><span class="${valueTone(!blocked && c.effective_dates ? c.equal_weight_cumulative_return : null)}">${cumulative}</span><span class="score-secondary">${c.effective_dates} 个完整日</span></span></td><td class="${valueTone(!blocked && c.effective_dates ? c.maximum_drawdown : null)}">${drawdown}</td></tr>`;\n'), ('        return `<div class="table-wrap table-scroll-region" tabindex="0" role="region" aria-label="盈利Top1、Top2独立累计统计"><table class="three-rank-table profit-summary-table"><thead><tr><th scope="col" class="left">盈利名次</th><th scope="col">自然冻结</th><th scope="col">待T / 待结算</th><th scope="col">盈利 / 代理结算</th><th scope="col">胜率</th><th scope="col">平均扣费净收益</th><th scope="col">已结算日合成累计</th><th scope="col">合成回撤</th></tr></thead><tbody>${rows}</tbody></table></div><p class="stats-note">D ${dateText(PUBLIC_STATISTICS_START_SIGNAL_DATE)}起 · 验证快照截至 ${dateText(source.shadow.as_of_date)}。胜率＝盈利笔数÷代理买入且已结算笔数；收益已扣45bp。买价采用竞价优先、缺失时开盘价后备；容量未验证的样本仅为价格代理，不代表实际可成交。未成交、未验证不进入胜率；累计仅含完整结算日，未完成日期不计入；已确认未成交槽位记0，不代表实盘净值。恢复留档不计入。</p>`;\n', '        return `<div class="table-wrap table-scroll-region" tabindex="0" role="region" aria-label="盈利Top1、Top2独立累计统计"><table class="three-rank-table profit-summary-table"><thead><tr><th scope="col" class="left">盈利名次</th><th scope="col">自然冻结</th><th scope="col">待T / 待结算</th><th scope="col">盈利 / 代理结算</th><th scope="col">胜率</th><th scope="col">平均扣费净收益</th><th scope="col">已结算日合成累计</th><th scope="col">合成回撤</th></tr></thead><tbody>${rows}</tbody></table></div>`;\n'), ('        const body = [...ledger.entries].reverse().flatMap(entry => {\n          if (!entry.rows.length) return [`<tr><td>${dateText(entry.signal_date)}</td><td>${dateText(entry.exec_date)}</td><td>${dateText(entry.exit_date)}</td><td colspan="8" class="left">当日无真实候选，已记录0席，不补票</td></tr>`];\n          return entry.rows.map(row => {\n            const bound = entry.generation_mode === "NATURAL" && source && entry.signal_date === source.shadow.signal_date &&\n              entry.exec_date === source.shadow.exec_date && entry.exit_date === source.shadow.exit_date &&\n              entry.projection_json_sha256 === source.loaded.index.latest_projection_json_sha256;\n            const truth = bound && source.shadow.latest_selected_rows.find(item => item.shadow_slot === row.slot && item.ts_code === row.ts_code && item.name === row.name && item.promotion_rank === row.promotion_rank);\n            const recovery = entry.generation_mode !== "NATURAL";\n            const t = truth ? primaryShadowStatus(truth.t_status, "t").label : recovery ? "恢复留档" : "未绑定该日验证";\n            const t1 = truth ? primaryShadowStatus(truth.t1_status, "t1").label : recovery ? "不计前向收益" : "未绑定该日验证";\n            return `<tr><td>${dateText(entry.signal_date)}</td><td>${dateText(entry.exec_date)}</td><td>${dateText(entry.exit_date)}</td><td class="left" data-field="code">${escapeHtml(row.ts_code)}</td><td class="left" data-field="stock"><strong>${escapeHtml(row.name)}</strong></td><td data-field="profit-rank"><span class="rank-mark rank-profit" aria-label="盈利排序第${row.slot}名">盈${row.slot}</span></td><td>${escapeHtml(t)}</td><td>${escapeHtml(t1)}</td><td>${signedPct(truth ? truth.net_return_after_cost : null)}</td><td>${signedPct(truth ? truth.strategy_slot_return : null)}</td><td>${truth ? "自然冻结" : recovery ? "仅留档" : "须独立冻结"}</td></tr>`;\n          });\n        }).join("");\n        els.compactLedgerContent.innerHTML = `${renderCompactProfitStatistics(source)}<details class="compact-disclosure" id="compactProfitDailyDetails"><summary>每日记录与验证 · ${ledger.recorded_days}日 / ${ledger.recorded_slots}席</summary><div class="table-wrap table-scroll-region" tabindex="0" role="region" aria-label="盈利前二每日记录与验证"><table class="three-rank-table"><thead><tr><th scope="col">D</th><th scope="col">T</th><th scope="col">T+1</th><th scope="col" class="left">代码</th><th scope="col" class="left">股票</th><th scope="col">盈利名次</th><th scope="col">T验证</th><th scope="col">T+1验证</th><th scope="col">成交净收益</th><th scope="col">槽位收益</th><th scope="col">统计资格</th></tr></thead><tbody>${body}</tbody></table></div><p class="stats-note">每日入选必留档；只有同D同来源的自然冻结进入前向收益。未绑定验证的历史行不填0，也不拿晋级观察收益代填。</p></details>`;\n', '        els.compactLedgerContent.innerHTML = renderCompactProfitStatistics(source);\n'), ('        ${independentTruth ? `<details class="compact-disclosure"><summary>验证日期与收益口径：T ${dateText(contract.exec_date)} · T+1 ${dateText(contract.exit_date)}</summary><p class="stats-note">D ${dateText(contract.signal_date)}；T收盘核验晋级，T+1收盘后核验退出。上表净收益为独立晋级名单的事后日开盘代理观察、扣除45bp（0.45%）成本，非实际成交或混合Shadow成绩。不能退出时顺延，缺失不按0收益。${state.index !== 0 ? "本历史视图未加载独立逐行验证，不借用当前D或旧Action结果。" : ""}</p></details>` : ""}`;\n', '        ${independentTruth ? `<div class="compact-disclosure compact-verification-dates">验证日期：T ${dateText(contract.exec_date)} · T+1 ${dateText(contract.exit_date)}</div>` : ""}`;\n')]
    expected = previous
    for old, new in changes:
        assert expected.count(old) == 1
        expected = expected.replace(old, new, 1)
    assert _source_before_compact_window("decision.html").decode() == expected
    # No backend, settlement, source acceptance, or production gate can change.
    for path in ("src/top10decision/decision/executable_profit_shadow_settlement.py", "src/top10decision/decision/primary_profit_forward_shadow_bridge.py", "scripts/validate_verify_forecast_inputs.py", ".github/workflows/verify_decision_observations.yml", ".github/workflows/deploy_dc20_pages.yml", "models/decision_primary_profit_shadow_entry_price_policy_v2.json"):
        assert _source_before_copy_delete(path) == _source_before_compact_window(path)


@pytest.mark.parametrize("target", ["review", "source"])
def test_copy_delete_review_rejects_live_byte_changes_after_cache_warmup(monkeypatch, target):
    path = "decision.html"
    assert _source_before_copy_delete(path)
    assert _source_before_copy_delete(path)
    changed = COPY_DELETE_REVIEW if target == "review" else ROOT / path
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == changed else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _source_before_copy_delete(path)


@pytest.mark.parametrize("mutation", ["base", "scope", "truth_policy", "statistics", "extra_path", "current_sha", "baseline_sha", "inverse", "extra_pin", "remove_pin", "model_policy"])
def test_copy_delete_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    review = json.loads(COPY_DELETE_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "scope": review["scope"] = "MODEL_RELEASE"
    elif mutation == "truth_policy": review["boundaries"]["truth_policy_changed"] = True
    elif mutation == "statistics": review["boundaries"]["statistics_calculation_changed"] = True
    elif mutation == "extra_path": review["source_changes"].append(dict(review["source_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "current_sha": review["source_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "baseline_sha": review["source_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    elif mutation == "remove_pin": del manifest["pinned_files"]["decision.html"]
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _state_before_copy_delete(manifest, review)
def _shadow_price_review(review: dict | None = None) -> dict:
    assert not SHADOW_PRICE_REVIEW.is_symlink()
    assert _sha256(SHADOW_PRICE_REVIEW) == SHADOW_PRICE_REVIEW_SHA
    review = json.loads(SHADOW_PRICE_REVIEW.read_text()) if review is None else review
    assert review["schema_version"] == "decision_primary_shadow_price_policy_review_v2"
    assert review["approved_base_commit"] == "a246e2706db3ecbb86ebe8f1ad2f4f3dc3acd9c1"
    assert review["scope"] == "AUTHORIZED_VERSIONED_SHADOW_PRICE_TRUTH_AND_PUBLICATION_NOT_MODEL_OR_RANKING_RELEASE"
    assert review["boundaries"] == SHADOW_PRICE_BOUNDARIES
    assert review["predecessor_evidence_path"] == EXIT_LABEL_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(EXIT_LABEL_REVIEW) == EXIT_LABEL_REVIEW_SHA
    paths = [item["path"] for item in review["source_changes"]]
    assert len(paths) == len(set(paths)) == 15
    assert set(paths) == SHADOW_PRICE_EXISTING_PATHS | SHADOW_PRICE_ADDED_PATHS | {"models/decision_model_freeze.json"}
    assert review["added_runtime_pins"] == [SHADOW_PRICE_ADDED_PIN]
    assert len(review["preserved_evidence"]) == 16
    predecessor = json.loads(EXIT_LABEL_REVIEW.read_text())
    assert review["preserved_evidence"] == predecessor["preserved_evidence"] + [{
        "path": EXIT_LABEL_REVIEW.relative_to(ROOT).as_posix(), "sha256": EXIT_LABEL_REVIEW_SHA,
    }]
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_profit_shadow_versioned_frontend.py"
    assert hashlib.sha256(_source_before_copy_delete(review["regression_test"]["path"])).hexdigest() == review["regression_test"]["sha256"]
    return review


@lru_cache(maxsize=1)
def _parse_shadow_source_review(raw: bytes) -> dict:
    # Cache pure decoding only. Callers always reread and authenticate the
    # complete live bytes before lookup, so changed files cannot hit this key.
    return json.loads(raw)


def _source_before_shadow_price_v2(path: str, review: dict | None = None) -> bytes:
    """Authenticate live bytes, then reconstruct the exact a246 source view."""
    # _state_before_shadow_price_v2 validates all evidence and live pins once.
    # Each individual rewind still authenticates the review's live bytes; do
    # not rescan all 16 predecessors for each of the 225 historical pin reads.
    assert not SHADOW_PRICE_REVIEW.is_symlink()
    raw_review = SHADOW_PRICE_REVIEW.read_bytes()
    assert hashlib.sha256(raw_review).hexdigest() == SHADOW_PRICE_REVIEW_SHA
    review = _parse_shadow_source_review(raw_review) if review is None else review
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_copy_delete(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert item["baseline_exists"] is (path not in SHADOW_PRICE_ADDED_PATHS)
    assert item["reason"]
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [part["current_start"] for part in changes] == sorted(part["current_start"] for part in changes)
    for part in reversed(changes):
        assert set(part) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(part["baseline_start"]) is int and part["baseline_start"] > 0
        assert type(part["current_start"]) is int and part["current_start"] > 0
        start = part["current_start"] - 1
        assert lines[start:start + len(part["current_lines"])] == part["current_lines"]
        lines[start:start + len(part["current_lines"])] = part["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    if path in SHADOW_PRICE_ADDED_PATHS:
        assert restored == b"" and item["baseline_bytes"] == 0
    return restored


def _state_before_shadow_price_v2(manifest: dict | None = None, review: dict | None = None) -> tuple[dict, dict]:
    review = _shadow_price_review(review)
    manifest, inventory = _state_before_copy_delete(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 225
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_copy_delete(path)).hexdigest() == expected
    for item in review["source_changes"]:
        _source_before_shadow_price_v2(item["path"], review)
    before = _source_before_shadow_price_v2("models/decision_model_freeze.json", review)
    restored = json.loads(before)
    assert len(restored["pinned_files"]) == 224
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    assert SHADOW_PRICE_ADDED_PIN not in restored["pinned_files"]
    assert expected["pinned_files"].pop(SHADOW_PRICE_ADDED_PIN) == _sha256(ROOT / SHADOW_PRICE_ADDED_PIN)
    for path in SHADOW_PRICE_EXISTING_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_shadow_price_v2(path, review)).hexdigest()
    assert expected == restored  # Complete non-pin identity and every other pin are protected.
    assert restored["source_surface_rotation"] == manifest["source_surface_rotation"]
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(
        path=SHADOW_PRICE_REVIEW.relative_to(ROOT).as_posix(), sha256=SHADOW_PRICE_REVIEW_SHA,
        approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_copy_delete(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(asset for asset in inventory["assets"] if asset["path"] == "models/decision_model_freeze.json").update(
        sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_shadow_price_v2_review_is_explicit_and_preserves_all_predecessors():
    manifest, inventory = _state_before_shadow_price_v2()
    old = json.loads(EXIT_LABEL_REVIEW.read_text())
    assert _canonical_sha256(manifest) == old["current_manifest_canonical_sha256"]
    assert inventory["dependency_successor_review"]["sha256"] == EXIT_LABEL_REVIEW_SHA
    assert _shadow_price_review()["boundaries"]["truth_policy_changed"] is True


@pytest.mark.parametrize("target", ["review", "source"])
def test_shadow_price_review_live_byte_changes_are_not_hidden_by_decode_cache(monkeypatch, target):
    path = "src/top10decision/decision/executable_profit_shadow_settlement.py"
    assert _source_before_shadow_price_v2(path)
    assert _source_before_shadow_price_v2(path)  # Warm the pure parse cache.
    changed = SHADOW_PRICE_REVIEW if target == "review" else ROOT / path
    read_bytes = Path.read_bytes
    def tampered(file):
        raw = read_bytes(file)
        return raw + b"\n" if file == changed else raw
    monkeypatch.setattr(Path, "read_bytes", tampered)
    with pytest.raises(AssertionError):
        _source_before_shadow_price_v2(path)


@pytest.mark.parametrize("mutation", ["base", "scope", "truth_policy", "history", "extra_path", "current_sha", "baseline_sha", "inverse", "extra_pin", "remove_pin", "model_policy"])
def test_shadow_price_v2_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    review = json.loads(SHADOW_PRICE_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "scope": review["scope"] = "DISPLAY_ONLY"
    elif mutation == "truth_policy": review["boundaries"]["truth_policy_changed"] = False
    elif mutation == "history": review["boundaries"]["historical_ledger_rewritten"] = True
    elif mutation == "extra_path": review["source_changes"].append(dict(review["source_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "current_sha": review["source_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "baseline_sha": review["source_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "inverse": review["source_changes"][0]["inverse_changes"][0]["baseline_lines"].append("unreviewed\n")
    elif mutation == "extra_pin": manifest["pinned_files"]["unreviewed.py"] = "0" * 64
    elif mutation == "remove_pin": del manifest["pinned_files"][SHADOW_PRICE_ADDED_PIN]
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _state_before_shadow_price_v2(manifest, review)


def _exit_label_review() -> dict:
    assert _sha256(EXIT_LABEL_REVIEW) == EXIT_LABEL_REVIEW_SHA
    review = json.loads(EXIT_LABEL_REVIEW.read_text())
    assert review["schema_version"] == "decision_exit_label_display_review_v1"
    assert review["approved_base_commit"] == "f4ba50ea5f2f850ca7c86dd274f2dc9500dbe5cf"
    assert review["scope"] == "EXIT_LABEL_DISPLAY_ONLY_VALIDATION_AND_RETURNS_UNCHANGED"
    assert review["predecessor_evidence_path"] == SETTLE_CLI_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(SETTLE_CLI_REVIEW) == SETTLE_CLI_REVIEW_SHA
    assert len(review["boundaries"]) == 8 and all(value is False for value in review["boundaries"].values())
    assert {item["path"] for item in review["source_changes"]} == EXIT_LABEL_PATHS | {"models/decision_model_freeze.json"}
    assert len(review["source_changes"]) == 3 and len(review["preserved_evidence"]) == 15
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink() and _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_three_rank_truth_frontend.py"
    assert _sha256(ROOT / review["regression_test"]["path"]) == review["regression_test"]["sha256"]
    return review


def _source_before_exit_label(path: str) -> bytes:
    review = _exit_label_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_shadow_price_v2(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    assert item["inverse_changes"]
    for entry in reversed(item["inverse_changes"]):
        assert set(entry) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(entry["current_start"]) is int and entry["current_start"] > 0
        start = entry["current_start"] - 1
        assert lines[start:start + len(entry["current_lines"])] == entry["current_lines"]
        lines[start:start + len(entry["current_lines"])] = entry["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _state_before_exit_label(manifest: dict | None = None) -> tuple[dict, dict]:
    review = _exit_label_review()
    manifest, inventory = _state_before_shadow_price_v2(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_shadow_price_v2(path)).hexdigest() == expected
    before = _source_before_exit_label("models/decision_model_freeze.json")
    restored = json.loads(before)
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in EXIT_LABEL_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_exit_label(path)).hexdigest()
    assert expected == restored
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({a["path"] for a in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(path=EXIT_LABEL_REVIEW.relative_to(ROOT).as_posix(), sha256=EXIT_LABEL_REVIEW_SHA, approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_shadow_price_v2(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(a for a in protected["assets"] if a["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(a for a in inventory["assets"] if a["path"] == "models/decision_model_freeze.json").update(sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_exit_label_is_display_only_and_keeps_validation_and_returns():
    _state_before_exit_label()
    previous = _source_before_exit_label("decision.html").decode()
    current = _source_before_shadow_price_v2("decision.html").decode()
    expected = previous.replace('const exitNote =', 'const exitLabel =', 1).replace('` · 退出 ${dateText(observationRow.actual_exit_date)}`', '`退出 ${dateText(observationRow.actual_exit_date)}`', 1).replace('threeRankTruthStatusLabel(status, contract) + exitNote', 'exitLabel || threeRankTruthStatusLabel(status, contract)', 1)
    assert current == expected
    for path in ("scripts/validate_verify_forecast_inputs.py", ".github/workflows/verify_decision_observations.yml", "scripts/settle_primary_observations.py", "outputs/decision/primary_observation/rows.csv", "outputs/decision/primary_observation/summary.json"):
        assert _source_before_exit_label(path) == _source_before_shadow_price_v2(path)


def _settle_cli_review() -> dict:
    assert _sha256(SETTLE_CLI_REVIEW) == SETTLE_CLI_REVIEW_SHA
    review = json.loads(SETTLE_CLI_REVIEW.read_text())
    assert review["schema_version"] == "decision_settlement_cli_import_review_v1"
    assert review["approved_base_commit"] == "e2752ecdb79a2ba014d74803d5b1f376e212006a"
    assert review["scope"] == "SETTLEMENT_CLI_IMPORT_REPAIR_FULL_VALIDATION_GATES_UNCHANGED"
    assert review["predecessor_evidence_path"] == VERIFY_CLOSE_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(VERIFY_CLOSE_REVIEW) == VERIFY_CLOSE_REVIEW_SHA
    assert len(review["boundaries"]) == 8 and all(value is False for value in review["boundaries"].values())
    assert {item["path"] for item in review["source_changes"]} == SETTLE_CLI_PATHS | {"models/decision_model_freeze.json"}
    assert len(review["source_changes"]) == 3 and len(review["preserved_evidence"]) == 14
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink() and _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_sync_frozen_shadow_truth.py"
    assert _sha256(ROOT / review["regression_test"]["path"]) == review["regression_test"]["sha256"]
    return review


def _source_before_settle_cli(path: str) -> bytes:
    review = _settle_cli_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_exit_label(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    assert item["inverse_changes"]
    for entry in reversed(item["inverse_changes"]):
        assert set(entry) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(entry["current_start"]) is int and entry["current_start"] > 0
        start = entry["current_start"] - 1
        assert lines[start:start + len(entry["current_lines"])] == entry["current_lines"]
        lines[start:start + len(entry["current_lines"])] = entry["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _state_before_settle_cli(manifest: dict | None = None) -> tuple[dict, dict]:
    review = _settle_cli_review()
    manifest, inventory = _state_before_exit_label(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_exit_label(path)).hexdigest() == expected
    before = _source_before_settle_cli("models/decision_model_freeze.json")
    restored = json.loads(before)
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in SETTLE_CLI_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_settle_cli(path)).hexdigest()
    assert expected == restored
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({a["path"] for a in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(path=SETTLE_CLI_REVIEW.relative_to(ROOT).as_posix(), sha256=SETTLE_CLI_REVIEW_SHA, approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_exit_label(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(a for a in protected["assets"] if a["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(a for a in inventory["assets"] if a["path"] == "models/decision_model_freeze.json").update(sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_settlement_cli_bootstrap_keeps_all_models_and_validation_policy():
    _state_before_settle_cli()
    previous = _source_before_settle_cli("scripts/settle_decision_executable_profit_forward_shadow.py").decode()
    current = (ROOT / "scripts/settle_decision_executable_profit_forward_shadow.py").read_text()
    assert current.split("from top10decision", 1)[1] == previous.split("from top10decision", 1)[1]
    assert 'sys.path[:0] = [str(ROOT), str(SRC)]' in current
    for path in ("scripts/validate_verify_forecast_inputs.py", ".github/workflows/verify_decision_observations.yml", "scripts/settle_primary_observations.py"):
        assert _source_before_settle_cli(path) == _source_before_shadow_price_v2(path)


def _verify_close_review() -> dict:
    assert _sha256(VERIFY_CLOSE_REVIEW) == VERIFY_CLOSE_REVIEW_SHA
    review = json.loads(VERIFY_CLOSE_REVIEW.read_text())
    assert review["schema_version"] == "decision_verify_import_and_close_column_review_v1"
    assert review["approved_base_commit"] == "9736747e842f95f0fac0f641ae513a2083f2c1ec"
    assert review["scope"] == "CLI_IMPORT_REPAIR_AND_T_CLOSE_DISPLAY_FULL_VALIDATION_GATES_UNCHANGED"
    assert review["predecessor_evidence_path"] == REFERENCE_DENSITY_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(REFERENCE_DENSITY_REVIEW) == REFERENCE_DENSITY_REVIEW_SHA
    assert len(review["boundaries"]) == 8 and all(value is False for value in review["boundaries"].values())
    assert {item["path"] for item in review["source_changes"]} == VERIFY_CLOSE_PATHS | {"models/decision_model_freeze.json"}
    assert len(review["source_changes"]) == 8 and len(review["preserved_evidence"]) == 13
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink() and _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_decision_two_rank_frontend.py"
    assert _sha256(ROOT / review["regression_test"]["path"]) == review["regression_test"]["sha256"]
    return review


def _source_before_verify_close(path: str) -> bytes:
    review = _verify_close_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_settle_cli(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    assert item["inverse_changes"]
    for entry in reversed(item["inverse_changes"]):
        assert set(entry) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(entry["current_start"]) is int and entry["current_start"] > 0
        start = entry["current_start"] - 1
        assert lines[start:start + len(entry["current_lines"])] == entry["current_lines"]
        lines[start:start + len(entry["current_lines"])] = entry["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _state_before_verify_close(manifest: dict | None = None) -> tuple[dict, dict]:
    review = _verify_close_review()
    manifest, inventory = _state_before_settle_cli(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_settle_cli(path)).hexdigest() == expected
    before = _source_before_verify_close("models/decision_model_freeze.json")
    restored = json.loads(before)
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in VERIFY_CLOSE_PATHS & set(expected["pinned_files"]):
        expected["pinned_files"][path] = hashlib.sha256(_source_before_verify_close(path)).hexdigest()
    assert expected == restored
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({a["path"] for a in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == dict(path=VERIFY_CLOSE_REVIEW.relative_to(ROOT).as_posix(), sha256=VERIFY_CLOSE_REVIEW_SHA, approved_base_commit=review["approved_base_commit"], scope=dep["current_scope"])
    for asset in inventory["assets"]:
        raw = _source_before_settle_cli(asset["path"])
        assert not (ROOT / asset["path"]).is_symlink()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(a for a in protected["assets"] if a["path"] == "models/decision_model_freeze.json")
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    next(a for a in inventory["assets"] if a["path"] == "models/decision_model_freeze.json").update(sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_verify_import_fix_and_close_column_do_not_change_models_or_validation_policy():
    _state_before_verify_close()
    previous = _source_before_verify_close("scripts/sync_frozen_shadow_truth.py").decode()
    current = _source_before_exit1000("scripts/sync_frozen_shadow_truth.py").decode()
    assert current.split("from top10decision", 1)[1] == previous.split("from top10decision", 1)[1]
    assert 'sys.path[:0] = [str(ROOT), str(ROOT / "src")]' in current
    for path in ("scripts/validate_verify_forecast_inputs.py", ".github/workflows/verify_decision_observations.yml", "scripts/settle_primary_observations.py"):
        assert _source_before_verify_close(path) == _source_before_shadow_price_v2(path)


def _density_review() -> dict:
    assert _sha256(REFERENCE_DENSITY_REVIEW) == REFERENCE_DENSITY_REVIEW_SHA
    review = json.loads(REFERENCE_DENSITY_REVIEW.read_text())
    assert review["schema_version"] == "decision_frontend_density_review_v1"
    assert review["approved_base_commit"] == "765228a8b70fd486d432cb83edb0fd9ea2a9fc3d"
    assert review["scope"] == "CSS_ONLY_REFERENCE_TABLE_DENSITY_NOT_MODEL_OR_DATA_RELEASE"
    assert review["predecessor_evidence_path"] == COLUMNS_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(COLUMNS_REVIEW) == COLUMNS_REVIEW_SHA
    assert len(review["boundaries"]) == 8 and all(value is False for value in review["boundaries"].values())
    assert len(review["source_changes"]) == 2
    assert {item["path"] for item in review["source_changes"]} == {"decision.html", "models/decision_model_freeze.json"}
    assert len(review["preserved_evidence"]) == 12
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_decision_two_rank_frontend.py"
    assert hashlib.sha256(_source_before_verify_close(review["regression_test"]["path"])).hexdigest() == review["regression_test"]["sha256"]
    return review


def _source_before_density(path: str) -> bytes:
    review = _density_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_verify_close(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    assert item["inverse_changes"]
    for entry in reversed(item["inverse_changes"]):
        assert set(entry) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(entry["current_start"]) is int and entry["current_start"] > 0
        start = entry["current_start"] - 1
        assert lines[start:start + len(entry["current_lines"])] == entry["current_lines"]
        lines[start:start + len(entry["current_lines"])] = entry["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _state_before_density(manifest: dict | None = None) -> tuple[dict, dict]:
    review = _density_review()
    manifest, inventory = _state_before_verify_close(manifest)
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_verify_close(path)).hexdigest() == expected
    before_manifest = _source_before_density(MANIFEST.relative_to(ROOT).as_posix())
    restored = json.loads(before_manifest)
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    expected["pinned_files"]["decision.html"] = hashlib.sha256(_source_before_density("decision.html")).hexdigest()
    assert expected == restored
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({asset["path"] for asset in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": REFERENCE_DENSITY_REVIEW.relative_to(ROOT).as_posix(), "sha256": REFERENCE_DENSITY_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": dep["current_scope"],
    }
    for asset in inventory["assets"]:
        assert not (ROOT / asset["path"]).is_symlink()
        actual = _source_before_verify_close(asset["path"])
        assert hashlib.sha256(actual).hexdigest() == asset["sha256"] and len(actual) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    freeze = next(asset for asset in inventory["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    freeze.update(sha256=hashlib.sha256(before_manifest).hexdigest(), bytes=len(before_manifest))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return restored, inventory


def test_reference_density_changes_only_css_and_preserves_runtime_and_content():
    _state_before_density()
    current = _source_before_verify_close("decision.html").decode()
    previous = _source_before_density("decision.html").decode()
    assert re.sub(r"<style>.*?</style>", "", current, flags=re.S) == re.sub(r"<style>.*?</style>", "", previous, flags=re.S)
    css = current.split("/* Reference-table density:", 1)[1].split("#compactLedger .stats-note", 1)[0]
    assert "height: 38px; padding: 3px 7px; font-size: .75rem; line-height: 1.2;" in css
    assert "font-size: .6875rem" in css
    assert "max-height" not in css and "overflow: hidden" not in css and "text-overflow" not in css


def _columns_review() -> dict:
    assert _sha256(COLUMNS_REVIEW) == COLUMNS_REVIEW_SHA
    review = json.loads(COLUMNS_REVIEW.read_text())
    assert review["schema_version"] == "decision_frontend_columns_review_v1"
    assert review["approved_base_commit"] == "6711188bb31d93e485b9903871f92932e348ada8"
    assert review["scope"] == "DISPLAY_COLUMNS_AND_MATCHING_PUBLIC_DOM_ACCEPTANCE_NOT_MODEL_OR_DATA_RELEASE"
    assert review["predecessor_evidence_path"] == LOADING_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(LOADING_REVIEW) == LOADING_REVIEW_SHA
    assert review["boundaries"] == {
        **{key: False for key in ("model_weights_changed", "ranking_algorithm_changed", "frozen_members_changed",
                                 "truth_policy_changed", "historical_ledger_rewritten", "workflow_scheduling_changed", "forward_epoch_activated")},
        "publication_dom_acceptance_changed": True,
    }
    assert len(review["source_changes"]) == 6
    assert {item["path"] for item in review["source_changes"]} == COLUMNS_PIN_PATHS | {
        "models/decision_model_freeze.json", "tests/test_decision_two_rank_frontend.py"}
    assert len(review["preserved_evidence"]) == 11
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_decision_two_rank_frontend.py"
    assert hashlib.sha256(_source_before_verify_close(review["regression_test"]["path"])).hexdigest() == review["regression_test"]["sha256"]
    return review


def _source_before_columns(path: str) -> bytes:
    review = _columns_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_density(path)
    item = next((entry for entry in review["source_changes"] if entry["path"] == path), None)
    if item is None:
        return source
    assert len(source) == item["current_bytes"] and hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [entry["current_start"] for entry in changes] == sorted(entry["current_start"] for entry in changes)
    for entry in reversed(changes):
        assert set(entry) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(entry["current_start"]) is int and entry["current_start"] > 0
        assert type(entry["baseline_start"]) is int and entry["baseline_start"] > 0
        start = entry["current_start"] - 1
        assert lines[start:start + len(entry["current_lines"])] == entry["current_lines"]
        lines[start:start + len(entry["current_lines"])] = entry["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"] and hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _inventory_before_columns() -> dict:
    review = _columns_review()
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    inventory = _state_before_density()[1]
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({asset["path"] for asset in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": COLUMNS_REVIEW.relative_to(ROOT).as_posix(), "sha256": COLUMNS_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": dep["current_scope"],
    }
    for asset in inventory["assets"]:
        assert not (ROOT / asset["path"]).is_symlink()
        actual = _source_before_density(asset["path"])
        assert hashlib.sha256(actual).hexdigest() == asset["sha256"] and len(actual) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    inventory["dependency_successor_review"] = dep["baseline_review"]
    freeze = next(asset for asset in inventory["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    before = _source_before_columns(freeze["path"])
    freeze.update(sha256=hashlib.sha256(before).hexdigest(), bytes=len(before))
    assert hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"]
    return inventory


def _manifest_before_columns(manifest: dict) -> dict:
    manifest = _state_before_density(manifest)[0]
    review = _columns_review()
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_density(path)).hexdigest() == expected
    for item in review["source_changes"]:
        _source_before_columns(item["path"])
    restored = json.loads(_source_before_columns(MANIFEST.relative_to(ROOT).as_posix()))
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    for path in COLUMNS_PIN_PATHS:
        expected["pinned_files"][path] = hashlib.sha256(_source_before_columns(path)).hexdigest()
    assert expected == restored
    _inventory_before_columns()
    return restored


def test_columns_change_only_renderer_css_and_public_dom_acceptance():
    _manifest_before_columns(json.loads(MANIFEST.read_text()))
    current = _source_before_verify_close("decision.html").decode()
    previous = _source_before_columns("decision.html").decode()
    def logic_without_renderer(text):
        script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
        return re.sub(r"^    function renderThreeRankWatchlist\(.*?^    }", "", script, flags=re.M | re.S)
    assert logic_without_renderer(current) == logic_without_renderer(previous)
    path = ".github/workflows/run_primary_profit_rankings.yml"
    current, previous = (ROOT / path).read_text(), _source_before_columns(path).decode()
    marker = "- name: Execute public dashboard and verify rendered P1 DOM"
    assert current.split(marker)[0] == previous.split(marker)[0]
    assert current.split(marker)[1].split("\n          PY", 1)[1] == previous.split(marker)[1].split("\n          PY", 1)[1]


def _loading_review() -> dict:
    assert _sha256(LOADING_REVIEW) == LOADING_REVIEW_SHA
    review = json.loads(LOADING_REVIEW.read_text())
    assert review["schema_version"] == "decision_frontend_loading_review_v1"
    assert review["approved_base_commit"] == "3c2dacbf03109801caaadc8f4c37c78b1f2b5c29"
    assert review["scope"] == "READ_ONLY_HOMEPAGE_LOADING_NOT_MODEL_OR_DATA_RELEASE"
    assert review["predecessor_evidence_path"] == DAILY_DISPATCH_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(DAILY_DISPATCH_REVIEW) == DAILY_DISPATCH_REVIEW_SHA
    assert review["boundaries"] == {key: False for key in (
        "model_weights_changed", "ranking_algorithm_changed", "frozen_members_changed",
        "truth_policy_changed", "historical_ledger_rewritten", "workflow_changed", "forward_epoch_activated",
    )}
    assert [item["path"] for item in review["source_changes"]] == ["decision.html", "models/decision_model_freeze.json"]
    assert len(review["preserved_evidence"]) == 10
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    assert review["regression_test"]["path"] == "tests/test_dashboard_loading_performance.py"
    assert _sha256(ROOT / review["regression_test"]["path"]) == review["regression_test"]["sha256"]
    return review


def _source_before_loading(path: str) -> bytes:
    """Validate current bytes, then reverse the read-only frontend successor."""
    review = _loading_review()
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_columns(path)
    matches = [item for item in review["source_changes"] if item["path"] == path]
    if not matches:
        return source
    assert len(matches) == 1
    item = matches[0]
    assert len(source) == item["current_bytes"]
    assert hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode().splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [change["current_start"] for change in changes] == sorted(change["current_start"] for change in changes)
    for change in reversed(changes):
        assert set(change) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(change["current_start"]) is int and change["current_start"] > 0
        assert type(change["baseline_start"]) is int and change["baseline_start"] > 0
        start = change["current_start"] - 1
        end = start + len(change["current_lines"])
        assert lines[start:end] == change["current_lines"]
        lines[start:end] = change["baseline_lines"]
    restored = "".join(lines).encode()
    assert len(restored) == item["baseline_bytes"]
    assert hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    return restored


def _inventory_before_loading() -> dict:
    review = _loading_review()
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    inventory = _inventory_before_columns()
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({asset["path"] for asset in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": LOADING_REVIEW.relative_to(ROOT).as_posix(), "sha256": LOADING_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": dep["current_scope"],
    }
    for asset in inventory["assets"]:
        assert not (ROOT / asset["path"]).is_symlink()
        raw = _source_before_columns(asset["path"])
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"] and len(raw) == asset["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(asset for asset in protected["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    del freeze["sha256"], freeze["bytes"]
    assert _canonical_sha256(protected) == dep["protected_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    restored = copy.deepcopy(inventory)
    restored["dependency_successor_review"] = dep["baseline_review"]
    freeze = next(asset for asset in restored["assets"] if asset["path"] == MANIFEST.relative_to(ROOT).as_posix())
    baseline = _source_before_loading(freeze["path"])
    freeze.update(sha256=hashlib.sha256(baseline).hexdigest(), bytes=len(baseline))
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"] == "de0123ea6bc840bb9698e32767bb1c7862071b79fe351a1afba2dc4803d3f128"
    return restored


def _manifest_before_loading(manifest: dict) -> dict:
    manifest = _manifest_before_columns(manifest)
    review = _loading_review()
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
        assert hashlib.sha256(_source_before_columns(path)).hexdigest() == expected
    before = _source_before_loading(MANIFEST.relative_to(ROOT).as_posix())
    assert hashlib.sha256(before).hexdigest() == "408311f0852892e7b175e589d40d649f56717f2b27de08cd6b5f6f4459b6c2e8"
    restored = json.loads(before)
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    expected = copy.deepcopy(manifest)
    expected["pinned_files"]["decision.html"] = hashlib.sha256(_source_before_loading("decision.html")).hexdigest()
    assert expected == restored  # Only the HTML source pin changed, no model policy.
    _inventory_before_loading()
    return restored


def test_loading_review_preserves_all_model_policies_and_source_history():
    _manifest_before_loading(json.loads(MANIFEST.read_text()))


@pytest.mark.parametrize("mutation", ["extra_pin", "html", "model_policy"])
def test_loading_review_rejects_unreviewed_manifest_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    if mutation == "extra_pin": manifest["pinned_files"]["extra.py"] = "0" * 64
    elif mutation == "html": manifest["pinned_files"]["decision.html"] = "0" * 64
    else: manifest["training_cutoff_signal_date"] = "20260911"
    with pytest.raises(AssertionError):
        _manifest_before_loading(manifest)


def _source_before_daily_dispatch(path: str, review: dict | None = None) -> bytes:
    """Reverse only the reviewed source/test changes, without touching disk."""
    assert _sha256(DAILY_DISPATCH_REVIEW) == DAILY_DISPATCH_REVIEW_SHA
    assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
    source = _source_before_loading(path)
    if path not in DAILY_DISPATCH_PATHS | DAILY_DISPATCH_UNPINNED_TEST_PATHS:
        return source
    review = review if review is not None else json.loads(DAILY_DISPATCH_REVIEW.read_text())
    matches = [item for item in review["pin_changes"] + review["unpinned_test_changes"] if item["path"] == path]
    assert len(matches) == 1
    item = matches[0]
    assert len(source) == item["current_bytes"]
    assert hashlib.sha256(source).hexdigest() == item["current_sha256"]
    lines = source.decode("utf-8").splitlines(keepends=True)
    changes = item["inverse_changes"]
    assert changes and [change["current_start"] for change in changes] == sorted(
        change["current_start"] for change in changes
    )
    for change in reversed(changes):
        assert set(change) == {"baseline_start", "current_start", "baseline_lines", "current_lines"}
        assert type(change["baseline_start"]) is int and change["baseline_start"] > 0
        assert type(change["current_start"]) is int and change["current_start"] > 0
        assert all(isinstance(line, str) for line in change["baseline_lines"] + change["current_lines"])
        start = change["current_start"] - 1
        end = start + len(change["current_lines"])
        assert lines[start:end] == change["current_lines"]
        lines[start:end] = change["baseline_lines"]
    restored = "".join(lines).encode("utf-8")
    assert len(restored) == item["baseline_bytes"]
    assert hashlib.sha256(restored).hexdigest() == item["baseline_sha256"]
    if path.endswith(".yml"):
        assert re.findall(rb"^\s*- cron:.*$", source, re.M) == re.findall(rb"^\s*- cron:.*$", restored, re.M)
    return restored


def _manifest_before_daily_dispatch(manifest: dict, review: dict | None = None) -> dict:
    """Validate actual current pins, then restore the approved 54a62 audit baseline."""
    manifest = _manifest_before_loading(manifest)
    assert _sha256(DAILY_DISPATCH_REVIEW) == DAILY_DISPATCH_REVIEW_SHA
    review = review if review is not None else json.loads(DAILY_DISPATCH_REVIEW.read_text())
    assert review["schema_version"] == "decision_controlled_daily_dispatch_review_v1"
    assert review["approved_base_commit"] == "54a62f2293d26618abeca1942aa733e2448a0b9b"
    assert review["scope"] == "CONTROLLED_SAME_DAY_NATURAL_DISPATCH_AND_P1_HANDOFF_NOT_MODEL_OR_LEDGER_RELEASE"
    assert review["predecessor_evidence_path"] == NAVIGATION_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(NAVIGATION_REVIEW) == NAVIGATION_REVIEW_SHA
    assert review["boundaries"] == {
        "controlled_manual_natural_generation_enabled": True,
        "p1_controlled_source_handoff_enabled": True,
        **{key: False for key in (
            "cron_schedules_changed", "model_inference_changed", "model_weights_changed",
            "frozen_schema_changed", "historical_ledger_or_truth_rewritten",
            "ranking_algorithm_changed", "forward_epoch_activated", "production_run_performed",
        )},
    }
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    current_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(current_bytes).hexdigest() == hashlib.sha256(_source_before_loading(MANIFEST.relative_to(ROOT).as_posix())).hexdigest() == review["current_manifest_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
        assert hashlib.sha256(_source_before_loading(path)).hexdigest() == expected
    assert [item["path"] for item in review["pin_changes"]] == sorted(DAILY_DISPATCH_PATHS)
    assert [item["path"] for item in review["unpinned_test_changes"]] == sorted(DAILY_DISPATCH_UNPINNED_TEST_PATHS)
    assert not DAILY_DISPATCH_UNPINNED_TEST_PATHS & set(manifest["pinned_files"])
    for item in review["unpinned_test_changes"]:
        assert hashlib.sha256(_source_before_daily_dispatch(item["path"], review)).hexdigest() == item["baseline_sha256"]
    restored = copy.deepcopy(manifest)
    for item in review["pin_changes"]:
        assert restored["pinned_files"][item["path"]] == item["current_sha256"]
        old_bytes = _source_before_daily_dispatch(item["path"], review)
        assert hashlib.sha256(old_bytes).hexdigest() == item["baseline_sha256"]
        restored["pinned_files"][item["path"]] = item["baseline_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "d1435ba8944d83127d2ecaba5288bf73289e0c7732bfe88561cdee0d0a8c4177"
    baseline_bytes = (json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(baseline_bytes).hexdigest() == review["baseline_manifest_sha256"] == "40a8037909d6ad01fe59ed160264545989072df0fa2094dcd314f3bea63a782e"
    assert len(review["preserved_evidence"]) == 9
    for item in review["preserved_evidence"]:
        assert (ROOT / item["path"]).parent == ROOT / "models"
        assert not (ROOT / item["path"]).is_symlink()
        assert _sha256(ROOT / item["path"]) == item["sha256"]
    dep = review["inventory_update"]
    assert dep["path"] == "forward/model_inventory.json"
    inventory = _inventory_before_loading()
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({item["path"] for item in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": DAILY_DISPATCH_REVIEW.relative_to(ROOT).as_posix(),
        "sha256": DAILY_DISPATCH_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"],
        "scope": dep["current_scope"],
    }
    assert dep["current_review_path"] == inventory["dependency_successor_review"]["path"]
    assert dep["all_other_41_assets_unchanged"] is True
    for item in inventory["assets"]:
        assert not (ROOT / item["path"]).is_symlink()
        historical = _source_before_loading(item["path"])
        assert hashlib.sha256(historical).hexdigest() == item["sha256"]
        assert len(historical) == item["bytes"]
    protected = copy.deepcopy(inventory)
    del protected["dependency_successor_review"]
    freeze = next(item for item in protected["assets"] if item["path"] == MANIFEST.relative_to(ROOT).as_posix())
    assert freeze.pop("sha256") == dep["current_dependency_sha256"] == review["current_manifest_sha256"]
    assert freeze.pop("bytes") == dep["current_dependency_bytes"] == len(current_bytes)
    assert _canonical_sha256(protected) == review["protected_inventory_canonical_sha256"] == "afc4241cc4655eeca3cfa95bcda9956f04f0489d6f95b2776c40bb876456844c"
    old_inventory = copy.deepcopy(inventory)
    old_inventory["dependency_successor_review"] = dep["baseline_review"]
    old_freeze = next(item for item in old_inventory["assets"] if item["path"] == MANIFEST.relative_to(ROOT).as_posix())
    old_freeze["sha256"] = dep["baseline_dependency_sha256"]
    old_freeze["bytes"] = dep["baseline_dependency_bytes"]
    assert old_freeze["sha256"] == review["baseline_manifest_sha256"] and old_freeze["bytes"] == len(baseline_bytes)
    assert hashlib.sha256((json.dumps(old_inventory, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == dep["baseline_sha256"] == "1eca8fc27e8f4595246a1be4998181138724bf7243ff0ab8e8264264889d0ad8"
    return restored


def test_daily_dispatch_review_preserves_predecessor_sources_and_model_policies():
    manifest = json.loads(MANIFEST.read_text())
    restored = _manifest_before_daily_dispatch(manifest)
    manifest = _manifest_before_loading(manifest)
    assert {path for path in manifest["pinned_files"] if manifest["pinned_files"][path] != restored["pinned_files"][path]} == DAILY_DISPATCH_PATHS
    assert {key: value for key, value in manifest.items() if key != "pinned_files"} == {key: value for key, value in restored.items() if key != "pinned_files"}


@pytest.mark.parametrize("mutation", ["base", "scope", "boundary", "extra_pin", "baseline", "current", "inverse_preimage", "inverse_postimage", "model_policy", "evidence", "inventory"])
def test_daily_dispatch_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    review = json.loads(DAILY_DISPATCH_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "scope": review["scope"] = "MODEL_RELEASE"
    elif mutation == "boundary": review["boundaries"]["model_inference_changed"] = True
    elif mutation == "extra_pin": review["pin_changes"].append(dict(review["pin_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "baseline": review["pin_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "current": review["pin_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "inverse_preimage": review["pin_changes"][0]["inverse_changes"][0]["baseline_lines"][0] = "unreviewed\n"
    elif mutation == "inverse_postimage": review["pin_changes"][0]["inverse_changes"][0]["current_lines"][0] = "unreviewed\n"
    elif mutation == "model_policy": manifest["training_cutoff_signal_date"] = "20260911"
    elif mutation == "evidence": review["preserved_evidence"][0]["sha256"] = "0" * 64
    else: review["inventory_update"]["current_scope"] = "MODEL_RELEASE"
    with pytest.raises(AssertionError):
        _manifest_before_daily_dispatch(manifest, review)


def _source_before_navigation(review: dict | None = None) -> str:
    assert _sha256(NAVIGATION_REVIEW) == NAVIGATION_REVIEW_SHA
    review = review if review is not None else json.loads(NAVIGATION_REVIEW.read_text())
    source = _source_before_loading("decision.html").decode()
    assert hashlib.sha256(source.encode()).hexdigest() == review["current_html_sha256"]
    assert len(review["replacements"]) == 8
    for replacement in review["replacements"]:
        assert source.count(replacement["after"]) == 1
        source = source.replace(replacement["after"], replacement["before"])
    assert hashlib.sha256(source.encode()).hexdigest() == review["baseline_html_sha256"] == "d2718429a77eb6d72d07f5c44c6fbbe1ea56c6f0750d3d26695f798237cdfe41"
    return source


def _manifest_before_navigation(manifest: dict, review: dict | None = None) -> dict:
    manifest = _manifest_before_daily_dispatch(manifest)
    review = review if review is not None else json.loads(NAVIGATION_REVIEW.read_text())
    _source_before_navigation(review)
    assert review["schema_version"] == "decision_daily_navigation_ui_review_v1"
    assert review["scope"] == "READ_ONLY_EXACT_D_NAVIGATION_NOT_MODEL_OR_DATA_RELEASE"
    assert review["approved_base_commit"] == "71e6827f89215fedc919e96742ffae228f599ee4"
    assert review["predecessor_evidence_path"] == PROFIT_SUMMARY_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(PROFIT_SUMMARY_REVIEW) == PROFIT_SUMMARY_REVIEW_SHA
    assert review["boundaries"] == {key: False for key in ("models_changed", "workflows_changed", "frozen_ledger_or_truth_changed", "entry_or_settlement_policy_changed", "ranking_algorithm_changed")}
    assert review["tests"]["path"] == "tests/test_primary_d_navigation.py"
    assert hashlib.sha256(_source_before_compact_window(review["tests"]["path"])).hexdigest() == review["tests"]["sha256"]
    assert hashlib.sha256((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["current_manifest_sha256"]
    assert len(manifest["pinned_files"]) == 224
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and hashlib.sha256(_source_before_daily_dispatch(path)).hexdigest() == expected
    restored = copy.deepcopy(manifest)
    assert restored["pinned_files"]["decision.html"] == review["current_html_sha256"]
    restored["pinned_files"]["decision.html"] = review["baseline_html_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "1ce0940518f602e2f5a70075fc7103b220ecc3345ed82a1a16d3f9b965fb7e80"
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["baseline_manifest_sha256"] == "327ebfcb7b3e7f0784163aeea773a1c761950410ba25f541645172d63aa5d666"
    return restored


@pytest.mark.parametrize("mutation", ["base", "baseline", "policy", "replacement", "model"])
def test_navigation_review_rejects_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(NAVIGATION_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "baseline": review["baseline_html_sha256"] = "0" * 64
    elif mutation == "policy": review["boundaries"]["ranking_algorithm_changed"] = True
    elif mutation == "replacement": review["replacements"][0]["before"] = "anything"
    else: manifest["training_cutoff_signal_date"] = "20260910"
    with pytest.raises(AssertionError):
        _manifest_before_navigation(manifest, review)


def _source_before_profit_summary(review: dict | None = None) -> str:
    assert _sha256(PROFIT_SUMMARY_REVIEW) == PROFIT_SUMMARY_REVIEW_SHA
    review = review if review is not None else json.loads(PROFIT_SUMMARY_REVIEW.read_text())
    source = _source_before_navigation()
    assert hashlib.sha256(source.encode()).hexdigest() == review["current_html_sha256"]
    assert len(review["replacements"]) == 5
    for replacement in review["replacements"]:
        assert source.count(replacement["after"]) == 1
        source = source.replace(replacement["after"], replacement["before"])
    assert hashlib.sha256(source.encode()).hexdigest() == review["baseline_html_sha256"] == "86f2ca2017aea8b66f80fc9ed6005c507b8b433b6572906cb9bb2bc5581cb70c"
    return source


def _manifest_before_profit_summary_review(manifest: dict, review: dict) -> dict:
    manifest = _manifest_before_navigation(manifest)
    _source_before_profit_summary(review)
    assert review["schema_version"] == "decision_profit_summary_ui_review_v1"
    assert review["approved_base_commit"] == "c831522293da5f4a1c259f75279b62287afa1801"
    assert review["scope"] == "READ_ONLY_PROFIT_COHORT_SUMMARY_AND_DAILY_IDENTITY_COLUMNS"
    assert review["predecessor_evidence_path"] == PROFIT_DETAILS_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(PROFIT_DETAILS_REVIEW) == PROFIT_DETAILS_REVIEW_SHA
    assert review["boundaries"] == {key: False for key in ("models_changed", "workflows_changed", "frozen_ledger_or_truth_changed", "entry_or_settlement_policy_changed", "ranking_algorithm_changed")}
    assert hashlib.sha256((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["current_manifest_sha256"]
    assert len(manifest["pinned_files"]) == 224
    for path, expected in manifest["pinned_files"].items():
        actual = hashlib.sha256(_source_before_navigation().encode()).hexdigest() if path == "decision.html" else hashlib.sha256(_source_before_daily_dispatch(path)).hexdigest()
        assert not (ROOT / path).is_symlink() and actual == expected
    restored = copy.deepcopy(manifest)
    assert restored["pinned_files"]["decision.html"] == review["current_html_sha256"]
    restored["pinned_files"]["decision.html"] = review["baseline_html_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "75941016905e7756322610ff63b6c5ebd603fb6e1c911908adbd2181596fb94c"
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["baseline_manifest_sha256"] == "4f07b67fc680470dff2b70ba1cc12e93e3c57682621f428cfc6eac29a5bcdf86"
    return restored


@pytest.mark.parametrize("mutation", ["base", "baseline", "policy", "replacement", "model"])
def test_profit_summary_review_rejects_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(PROFIT_SUMMARY_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "baseline": review["baseline_html_sha256"] = "0" * 64
    elif mutation == "policy": review["boundaries"]["entry_or_settlement_policy_changed"] = True
    elif mutation == "replacement": review["replacements"][0]["before"] = "anything"
    else: manifest["training_cutoff_signal_date"] = "20260909"
    with pytest.raises(AssertionError):
        _manifest_before_profit_summary_review(manifest, review)


def _manifest_before_profit_details_review(manifest: dict, review: dict) -> dict:
    """Only one hidden attribute may differ from the reviewed source."""
    assert _sha256(PROFIT_DETAILS_REVIEW) == PROFIT_DETAILS_REVIEW_SHA
    assert review["schema_version"] == "decision_ui_hidden_disclosure_review_v1"
    assert review["approved_base_commit"] == "8a63cc27e847c951597878c9fc27e788eaaab8aa"
    assert review["scope"] == "HIDE_UNUSED_PROFIT_DISCLOSURE_ONLY_NOT_MODEL_RELEASE"
    assert review["predecessor_evidence_path"] == SUCCESS_RATE_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(SUCCESS_RATE_REVIEW) == SUCCESS_RATE_REVIEW_SHA
    assert review["javascript_changed"] is False and review["model_or_ranking_or_ledger_changed"] is False
    manifest = _manifest_before_profit_summary_review(manifest, json.loads(PROFIT_SUMMARY_REVIEW.read_text()))
    source = _source_before_profit_summary()
    before = '      <details class="compact-disclosure" id="profitDetails">'
    after = '      <details class="compact-disclosure" id="profitDetails" hidden>'
    assert review["replacement"] == {"before": before, "after": after}
    assert source.count(after) == 1 and before not in source
    assert hashlib.sha256(source.replace(after, before).encode()).hexdigest() == review["baseline_html_sha256"]
    assert hashlib.sha256(source.encode()).hexdigest() == review["current_html_sha256"]
    assert hashlib.sha256((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["current_manifest_sha256"]
    assert len(manifest["pinned_files"]) == 224
    restored = copy.deepcopy(manifest)
    assert restored["pinned_files"]["decision.html"] == review["current_html_sha256"]
    restored["pinned_files"]["decision.html"] = review["baseline_html_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "cf08a3fdfc1fe2015d1f0af46cec9a4e568f8934a9b7595fad0d4c72f04ec8f1"
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["baseline_manifest_sha256"] == "8e2b7dac19428b36f3ba14853abe656c40dc8a4a531e4e13e0f3c6b8d958c50e"
    return restored


@pytest.mark.parametrize("mutation", ["base", "baseline", "javascript", "replacement", "policy"])
def test_profit_details_review_rejects_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(PROFIT_DETAILS_REVIEW.read_text())
    if mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "baseline": review["baseline_html_sha256"] = "0" * 64
    elif mutation == "javascript": review["javascript_changed"] = True
    elif mutation == "replacement": review["replacement"]["before"] = "anything"
    else: manifest["training_cutoff_signal_date"] = "20260909"
    with pytest.raises(AssertionError):
        _manifest_before_profit_details_review(manifest, review)


def _manifest_before_success_rate_review(manifest: dict, review: dict) -> dict:
    """Validate current bytes and exactly the reviewed presentation-only changes."""
    assert _sha256(SUCCESS_RATE_REVIEW) == SUCCESS_RATE_REVIEW_SHA
    assert review["schema_version"] == "decision_ui_success_rate_successor_review_v1"
    assert review["approved_base_commit"] == "f45d63307e3dd6c539c0560c7cacb762612c067e"
    assert review["scope"] == "READ_ONLY_FRONTEND_SUCCESS_SUMMARY_AND_IDENTITY_COLUMNS_NOT_MODEL_RELEASE"
    manifest = _manifest_before_profit_details_review(manifest, json.loads(PROFIT_DETAILS_REVIEW.read_text()))
    assert review["predecessor_evidence_path"] == STATISTICS_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(STATISTICS_REVIEW) == STATISTICS_REVIEW_SHA
    prior = json.loads(STATISTICS_REVIEW.read_text())
    assert review["protected_model_identity"] == prior["protected_model_identity"]
    assert review["boundaries"] == {key: False for key in (
        "rankings_or_data_changed", "model_weights_changed", "workflows_changed",
        "historical_evidence_rewritten", "forward_epoch_activated", "ledger_written",
    )}
    restored = copy.deepcopy(manifest)
    assert len(restored["pinned_files"]) == 224
    assert [item["path"] for item in review["pin_changes"]] == ["decision.html", "tests/test_dashboard_research_projection.py"]
    for item in review["pin_changes"]:
        assert restored["pinned_files"][item["path"]] == item["current_sha256"]
        restored["pinned_files"][item["path"]] = item["baseline_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "36dee02abe9a351812a0139689466fe9070ba966756de553a88c2e8c173cc45d"
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["baseline_manifest_sha256"] == "fd939c5f8288bf8821cf2442714e5987a2926b70404923f68a3c806ea272b7c5"
    source = _source_before_profit_summary()
    for name, expected in review["preserved_runtime_functions"].items():
        function = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
        assert function and hashlib.sha256(function.group().encode()).hexdigest() == expected
    assert {"promotionSlotStatistics", "refreshPromotionSlotStatistics", "unifiedProfitView", "threeRankRowTruth"} <= review["preserved_runtime_functions"].keys()
    assert len(review["presentation_function_changes"]) == 1
    change = review["presentation_function_changes"][0]
    assert change["name"] == "renderThreeRankWatchlist" and len(change["replacements"]) == 3
    renderer = re.search(r"^    function renderThreeRankWatchlist\(.*?^    }", source, re.M | re.S).group()
    assert hashlib.sha256(renderer.encode()).hexdigest() == change["current_sha256"]
    for replacement in change["replacements"]:
        assert renderer.count(replacement["after"]) == 1
        renderer = renderer.replace(replacement["after"], replacement["before"])
    assert hashlib.sha256(renderer.encode()).hexdigest() == change["baseline_sha256"] == prior["preserved_runtime_functions"]["renderThreeRankWatchlist"]
    dep = review["forward_inventory_dependency_update"]
    before_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    assert dep["current_sha256"] == hashlib.sha256(before_bytes).hexdigest() and dep["bytes"] == len(before_bytes)
    assert dep["all_other_41_assets_unchanged"] is True
    inventory = _inventory_before_loading()
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert inventory["dependency_successor_review"]["path"] == DAILY_DISPATCH_REVIEW.relative_to(ROOT).as_posix()
    assert inventory["dependency_successor_review"]["sha256"] == DAILY_DISPATCH_REVIEW_SHA
    for item in inventory["assets"]:
        historical = _source_before_loading(item["path"])
        assert hashlib.sha256(historical).hexdigest() == item["sha256"]
        assert len(historical) == item["bytes"]
    return restored


@pytest.mark.parametrize("mutation", ["extra_pin", "base", "baseline", "model_policy", "boundary", "renderer"])
def test_success_rate_successor_rejects_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(SUCCESS_RATE_REVIEW.read_text())
    if mutation == "extra_pin": review["pin_changes"].append(dict(review["pin_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "baseline": review["pin_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "model_policy": manifest["training_cutoff_signal_date"] = "20260909"
    elif mutation == "renderer": review["presentation_function_changes"][0]["replacements"][0]["before"] = "changed"
    else: review["boundaries"]["ledger_written"] = True
    with pytest.raises(AssertionError):
        _manifest_before_success_rate_review(manifest, review)


def _manifest_before_statistics_review(manifest: dict, review: dict) -> dict:
    """Verify all current pins and model assets before rewinding two UI pins."""
    assert _sha256(STATISTICS_REVIEW) == STATISTICS_REVIEW_SHA
    assert review["schema_version"] == "decision_ui_statistics_successor_review_v1"
    assert review["approved_base_commit"] == "a9f5869f1e11794d2840da263d577b1ad74c605c"
    assert review["scope"] == "READ_ONLY_FRONTEND_STATISTICS_PROJECTION_NOT_MODEL_RELEASE"
    assert review["predecessor_evidence_path"] == DENSITY_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(DENSITY_REVIEW) == DENSITY_REVIEW_SHA
    assert review["protected_model_identity"] == json.loads(DENSITY_REVIEW.read_text())["protected_model_identity"]
    assert review["boundaries"] == {key: False for key in (
        "rankings_or_data_changed", "model_weights_changed", "workflows_changed",
        "historical_evidence_rewritten", "forward_epoch_activated", "ledger_written",
    )}
    manifest = _manifest_before_success_rate_review(manifest, json.loads(SUCCESS_RATE_REVIEW.read_text()))
    restored = copy.deepcopy(manifest)
    assert len(restored["pinned_files"]) == 224
    assert [item["path"] for item in review["pin_changes"]] == ["decision.html", "tests/test_dashboard_research_projection.py"]
    for item in review["pin_changes"]:
        assert restored["pinned_files"][item["path"]] == item["current_sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", item["baseline_sha256"])
        restored["pinned_files"][item["path"]] = item["baseline_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"] == "4d91693fcc7776e16ae5d2a20b289ecd601d7f040059631754b23f9cd4a87cb8"
    assert hashlib.sha256((json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest() == review["baseline_manifest_sha256"] == "14767ab5f5de199b5737f4ced58458396ccf3d58b40bdcaed1ec62de8c04ed41"
    source = _source_before_profit_summary()
    assert len(review["preserved_runtime_functions"]) >= 5
    for name, expected in review["preserved_runtime_functions"].items():
        function = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
        if name == "renderThreeRankWatchlist":
            # Exact presentation rewrites were checked and reversed above.
            assert expected == json.loads(SUCCESS_RATE_REVIEW.read_text())["presentation_function_changes"][0]["baseline_sha256"]
        else:
            assert function and hashlib.sha256(function.group().encode()).hexdigest() == expected
    dep = review["forward_inventory_dependency_update"]
    before_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    assert dep["current_sha256"] == hashlib.sha256(before_bytes).hexdigest() and dep["bytes"] == len(before_bytes)
    assert dep["all_other_41_assets_unchanged"] is True
    inventory = _inventory_before_loading()
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert inventory["dependency_successor_review"]["path"] == DAILY_DISPATCH_REVIEW.relative_to(ROOT).as_posix()
    assert inventory["dependency_successor_review"]["sha256"] == DAILY_DISPATCH_REVIEW_SHA
    for item in inventory["assets"]:
        historical = _source_before_loading(item["path"])
        assert hashlib.sha256(historical).hexdigest() == item["sha256"]
        assert len(historical) == item["bytes"]
    return restored


@pytest.mark.parametrize("mutation", ["extra_pin", "base", "baseline", "model_policy", "boundary"])
def test_statistics_successor_rejects_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(STATISTICS_REVIEW.read_text())
    if mutation == "extra_pin": review["pin_changes"].append(dict(review["pin_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "base": review["approved_base_commit"] = "0" * 40
    elif mutation == "baseline": review["pin_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "model_policy": manifest["training_cutoff_signal_date"] = "20260909"
    else: review["boundaries"]["ledger_written"] = True
    with pytest.raises(AssertionError):
        _manifest_before_statistics_review(manifest, review)


def _manifest_before_density_review(manifest: dict, review: dict) -> dict:
    """Check real current bytes, then rewind the CSS-only single-pin successor."""
    assert _sha256(DENSITY_REVIEW) == DENSITY_REVIEW_SHA
    assert review["schema_version"] == "decision_ui_density_successor_review_v1"
    assert review["review_id"] == "dc20_compact_density_20260909"
    assert review["approved_base_commit"] == "42364ebddd89c02ab684fe2586bc04b0cb9a5aec"
    assert review["scope"] == "CSS_ONLY_DENSITY_CHANGE_NOT_MODEL_RELEASE"
    assert review["baseline_manifest_sha256"] == "fbf2f92dec22604e402e73628a70bc287ea3cd16b0c1b6511fc0eb0a14211cae"
    assert review["baseline_manifest_canonical_sha256"] == "3360aee5605b90c76b056659f26dcc79701b95a4aa7d028c711249ebbfee21b9"
    assert review["predecessor_evidence_path"] == COMPACT_REVIEW.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(COMPACT_REVIEW) == COMPACT_REVIEW_SHA
    assert review["protected_model_identity"] == json.loads(COMPACT_REVIEW.read_text())["protected_model_identity"]
    assert review["boundaries"] == {key: False for key in (
        "dom_changed", "javascript_changed", "rankings_or_data_changed", "model_weights_changed",
        "workflows_changed", "historical_evidence_rewritten", "forward_epoch_activated",
    )}
    manifest = _manifest_before_statistics_review(manifest, json.loads(STATISTICS_REVIEW.read_text()))
    restored = copy.deepcopy(manifest)
    pins = restored["pinned_files"]
    assert len(pins) == 224 and DENSITY_REVIEW.relative_to(ROOT).as_posix() not in pins
    for path, expected in pins.items():
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        # Live bytes were checked before the statistics successor rewind.
        assert re.fullmatch(r"[0-9a-f]{64}", expected)
    assert review["pin_changes"] == [{
        "path": "decision.html", "baseline_sha256": "337e892ffa5ef081185a92d2ad3914913306dbcd8d1b9638f7066a40b852a228",
        "current_sha256": pins["decision.html"],
    }]
    # The immutable density evidence binds its complete HTML preimage. The
    # newer DOM/JS successor was checked above, not mislabeled as CSS-only.
    assert review["preserved_non_style_html_sha256"] == (
        "c4ac73502cf2f9f7de34756bcfde4a97632962b83debd2992067a10d45889186"
    )
    pins["decision.html"] = review["pin_changes"][0]["baseline_sha256"]
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    dep = review["forward_inventory_dependency_update"]
    previous_manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    assert dep == {
        "path": "models/decision_model_freeze.json", "baseline_sha256": review["baseline_manifest_sha256"],
        "current_sha256": hashlib.sha256(previous_manifest_bytes).hexdigest(), "bytes": len(previous_manifest_bytes),
        "all_other_41_assets_unchanged": True,
    }
    return restored


@pytest.mark.parametrize("mutation", ["extra_path", "current_sha", "base_commit", "javascript", "model_policy", "non_style"])
def test_density_review_rejects_non_css_and_unreviewed_changes(mutation):
    manifest, review = json.loads(MANIFEST.read_text()), json.loads(DENSITY_REVIEW.read_text())
    if mutation == "extra_path":
        review["pin_changes"].append(dict(review["pin_changes"][0], path="scripts/publish_primary_three_rank.py"))
    elif mutation == "current_sha":
        review["pin_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "base_commit":
        review["approved_base_commit"] = "0" * 40
    elif mutation == "javascript":
        review["boundaries"]["javascript_changed"] = True
    elif mutation == "non_style":
        review["preserved_non_style_html_sha256"] = "0" * 64
    else:
        manifest["training_cutoff_signal_date"] = "20260909"
    with pytest.raises(AssertionError):
        _manifest_before_density_review(manifest, review)


def test_requested_dense_table_typography_and_spacing():
    source = _source_before_shadow_price_v2("decision.html").decode()
    css = re.findall(r"<style(?:\s[^>]*)?>(.*?)</style>", source, re.S)[0]
    assert 'body.compact-dashboard { font-size: 14px; }' in css
    cells = re.search(r'\.compact-dashboard table\.three-rank-table th, \.compact-dashboard table\.three-rank-table td \{([^}]+)}', css).group(1)
    assert 'font-size: 14px' in cells and 'padding: 5px 8px' in cells and 'line-height: 1.25' in cells
    for selector in ('row-meta', 'rank-mark', 'truth-badge'):
        rule = re.search(rf'\.compact-dashboard \.{selector} \{{([^}}]+)}}', css).group(1)
        assert 'font-size: 12px' in rule
    assert '.truth-badge { min-height: 20px;' in css
    assert 'text-overflow: ellipsis' not in css.split('/* Compact density v14:', 1)[1]


def _pins_before_compact_ui_review(manifest: dict, review: dict) -> dict:
    """Verify current disk bytes first, then rewind only six reviewed UI pins."""
    assert _sha256(COMPACT_REVIEW) == COMPACT_REVIEW_SHA
    assert review["schema_version"] == "decision_source_surface_successor_review_v1"
    assert review["review_id"] == "dc20_compact_two_rank_homepage_20260909"
    assert review["reviewed_on"] == "2026-09-09"
    assert review["scope"] == "SOURCE_ONLY_SUCCESSOR_REVIEW_NOT_MODEL_RELEASE"
    assert review["approved_base_commit"] == "85de3d0fe6d576964aac61d95e25d793961b8466"
    assert review["baseline_manifest_sha256"] == "16f80f0b7bc45957dce4239e295abe849ec7fbae519b10b88e759b49e401593a"
    assert review["baseline_manifest_canonical_sha256"] == "b4f1ab8769d9f63144cf332358693f64e55b7869edc2f0347c511fa64a6c5345"
    assert review["predecessor_evidence_path"] == SUCCESSOR.relative_to(ROOT).as_posix()
    assert review["predecessor_evidence_sha256"] == _sha256(SUCCESSOR) == "3380278d97c63cf47538ec5fe46ff8da7bc31389d4fcaff2ce540bc1d899a885"
    predecessor = json.loads(SUCCESSOR.read_text())
    assert review["protected_model_identity"] == predecessor["protected_model_identity"]
    assert review["boundaries"] == {
        **predecessor["boundaries"], "production_schedule_changed": False, "forward_epoch_activated": False,
    }
    manifest = _manifest_before_density_review(manifest, json.loads(DENSITY_REVIEW.read_text()))
    restored = copy.deepcopy(manifest)
    pins = restored["pinned_files"]
    assert len(pins) == 224 and COMPACT_REVIEW.relative_to(ROOT).as_posix() not in pins
    for path, expected in pins.items():
        target = ROOT / path
        assert target.is_file() and not target.is_symlink()
        # Current disk hashes were already verified before the density rewind.
        assert re.fullmatch(r"[0-9a-f]{64}", expected)
    changes = review["pin_changes"]
    assert [item["path"] for item in changes] == sorted(COMPACT_REVIEW_PATHS)
    for item in changes:
        assert set(item) == {"path", "baseline_sha256", "current_sha256", "reason"}
        assert item["current_sha256"] == pins[item["path"]]
        assert re.fullmatch(r"[0-9a-f]{64}", item["baseline_sha256"])
        assert item["baseline_sha256"] != item["current_sha256"] and item["reason"]
        pins[item["path"]] = item["baseline_sha256"]
    # Bind every untouched pin AND the complete non-pin model/contract policy.
    assert _canonical_sha256(restored) == review["baseline_manifest_canonical_sha256"]
    dep = review["forward_inventory_dependency_update"]
    previous_manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    assert dep == {
        "path": "models/decision_model_freeze.json", "baseline_sha256": review["baseline_manifest_sha256"],
        "current_sha256": hashlib.sha256(previous_manifest_bytes).hexdigest(), "bytes": len(previous_manifest_bytes),
        "all_other_41_assets_unchanged": True,
    }
    return pins


@pytest.mark.parametrize("mutation", ["extra_path", "current_sha", "baseline_sha", "model_identity", "base_commit", "ranking_boundary", "model_policy"])
def test_compact_ui_review_rejects_unreviewed_changes(mutation):
    manifest = json.loads(MANIFEST.read_text())
    review = json.loads(COMPACT_REVIEW.read_text())
    if mutation == "extra_path":
        review["pin_changes"].append(dict(review["pin_changes"][0], path="src/top10decision/auction_v3/engine.py"))
    elif mutation == "current_sha":
        review["pin_changes"][0]["current_sha256"] = "0" * 64
    elif mutation == "baseline_sha":
        review["pin_changes"][0]["baseline_sha256"] = "0" * 64
    elif mutation == "model_identity":
        review["protected_model_identity"]["model_identity_changed"] = True
    elif mutation == "base_commit":
        review["approved_base_commit"] = "0" * 40
    elif mutation == "ranking_boundary":
        review["boundaries"]["promotion_members_or_ranks_changed"] = True
    else:
        manifest["training_cutoff_signal_date"] = "20260909"
    with pytest.raises(AssertionError):
        _pins_before_compact_ui_review(manifest, review)


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
    pins = _pins_before_compact_ui_review(manifest, json.loads(COMPACT_REVIEW.read_text()))
    # A source-review record is audit evidence, not a new frozen runtime input.
    # Keep the runtime's exact required pin set and authenticate this record
    # independently rather than extending the protected model source boundary.
    assert SUCCESSOR.relative_to(ROOT).as_posix() not in pins
    assert _sha256(SUCCESSOR) == "3380278d97c63cf47538ec5fe46ff8da7bc31389d4fcaff2ce540bc1d899a885"
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
        # Disk/current hashes were verified before the newer UI review rewind.
        assert pins[path] == item["current_sha256"]
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
    assert len(historical_pins) == (
        len(reconstructed_prior_pins) + len(added_runtime_pins)
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
