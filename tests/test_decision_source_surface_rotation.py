from __future__ import annotations

import copy
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
    source = (ROOT / path).read_bytes()
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
    inventory = json.loads((ROOT / dep["path"]).read_text())
    assert inventory["status"] == "INACTIVE_MIGRATION_REPLAY_ONLY"
    assert len(inventory["assets"]) == len({asset["path"] for asset in inventory["assets"]}) == 42
    assert inventory["dependency_successor_review"] == {
        "path": LOADING_REVIEW.relative_to(ROOT).as_posix(), "sha256": LOADING_REVIEW_SHA,
        "approved_base_commit": review["approved_base_commit"], "scope": dep["current_scope"],
    }
    for asset in inventory["assets"]:
        assert not (ROOT / asset["path"]).is_symlink()
        raw = (ROOT / asset["path"]).read_bytes()
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
    review = _loading_review()
    assert len(manifest["pinned_files"]) == review["pin_count"] == 224
    assert _canonical_sha256(manifest) == review["current_manifest_canonical_sha256"]
    for path, expected in manifest["pinned_files"].items():
        assert not (ROOT / path).is_symlink() and (ROOT / path).is_file()
        assert _sha256(ROOT / path) == expected
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
    assert _sha256(ROOT / review["tests"]["path"]) == review["tests"]["sha256"]
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
    source = (ROOT / "decision.html").read_text()
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
    assert len(manifest["pinned_files"]) == (
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
