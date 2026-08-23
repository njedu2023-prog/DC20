from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import top10decision.auction_v3.engine as auction_engine_module

from scripts.refreeze_decision_three_rank import (
    ThreeRankRefreezeError,
    _atomic_write_json,
    build_refrozen_manifest,
    build_three_rank_contract,
)
from scripts.replay_frozen_canonical_v2 import DiagnosticFrozenEngine
from top10decision.decision.model_freeze import (
    DecisionModelFreezeError,
    LEGACY_PRE_THREE_RANK_FREEZE_ID,
    REQUIRED_ACTIVE_PIN_PATHS,
    THREE_RANK_ALL_HEADS,
    THREE_RANK_BEHAVIOR_PIN_PATHS,
    THREE_RANK_DYNAMIC_ASSET_PATHS,
    THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS,
    validate_production_three_rank_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_for(contract: dict) -> dict:
    pins = {
        contract["source_ledger"]["ledger_path"]: contract["source_ledger"][
            "ledger_sha256"
        ],
        contract["source_ledger"]["ledger_manifest_path"]: contract[
            "source_ledger"
        ]["ledger_manifest_sha256"],
        contract["source_ledger"]["data_validation_path"]: contract[
            "source_ledger"
        ]["data_validation_sha256"],
        contract["validation"]["path"]: contract["validation"]["sha256"],
        contract["oof_top10"]["path"]: contract["oof_top10"]["sha256"],
        **{
            contract["heads"][head]["artifact_path"]: contract["heads"][head][
                "artifact_sha256"
            ]
            for head in THREE_RANK_ALL_HEADS
        },
    }
    return {
        "freeze_id": "dc20_decision_three_rank_v2_test",
        "production": {"three_rank": contract},
        "pinned_files": pins,
    }


def test_only_reviewed_canonical_v2_baseline_may_omit_three_rank_overlay() -> None:
    legacy = {
        "freeze_id": LEGACY_PRE_THREE_RANK_FREEZE_ID,
        "production": {},
        "pinned_files": {},
    }
    assert (
        validate_production_three_rank_contract(
            ROOT,
            legacy,
            require_complete=True,
        )
        is None
    )
    new = copy.deepcopy(legacy)
    new["freeze_id"] = "dc20_decision_three_rank_v2_missing_contract"
    with pytest.raises(DecisionModelFreezeError, match="requires production.three_rank"):
        validate_production_three_rank_contract(
            ROOT,
            new,
            require_complete=True,
        )
def test_current_evidence_builds_honest_hash_bound_release_state() -> None:
    contract = build_three_rank_contract(ROOT)
    assert contract["heads"]["promotion"]["status"] == "READY"
    assert contract["heads"]["promotion"]["promoted"] is True
    core_promoted = [
        contract["heads"][head]["promoted"]
        for head in ("promotion", "big_loss", "profit")
    ]
    assert contract["all_core_heads_promoted"] is all(core_promoted)
    assert contract["release_mode"] == (
        "ALL_CORE_READY"
        if contract["all_core_heads_promoted"]
        else "PROMOTION_READY_PARTIAL"
    )
    for head in ("big_loss", "profit"):
        item = contract["heads"][head]
        if item["promoted"]:
            assert item["status"] == "READY"
        else:
            assert item["status"].startswith("NOT_READY_")
    assert contract["heads"]["p_fill_shadow"]["status"] == "SHADOW_READY"
    assert contract["heads"]["p_fill_shadow"]["promoted"] is False
    assert contract["fail_closed"]["unready_secondary_head"] == "NULL_HEAD_FIELDS"
    assert set(_manifest_for(contract)["pinned_files"]) == set(
        THREE_RANK_DYNAMIC_ASSET_PATHS
    )
    assert {
        "src/top10decision/decision/d_close_features.py",
        "tests/test_d_close_features.py",
    } <= REQUIRED_ACTIVE_PIN_PATHS
    validate_production_three_rank_contract(
        ROOT,
        _manifest_for(contract),
        require_complete=True,
    )


def test_recovery_snapshot_and_first_dated_artifacts_are_non_dynamic_pins() -> None:
    expected_recovery = {
        "data/decision_three_engines/recovery/20260821/candidate_pool.csv",
        "data/decision_three_engines/recovery/20260821/daily_bars/000017_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/000710_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/000931_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002038_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002412_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002491_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/002903_SZ.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/603626_SH.csv.gz",
        "data/decision_three_engines/recovery/20260821/daily_bars/603958_SH.csv.gz",
        "data/decision_three_engines/recovery/20260821/manifest.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/big_loss.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/data_validation.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/five_year_ledger_manifest.json",
        "data/decision_three_engines/recovery/20260821/model_snapshot/five_year_supervised_ledger.csv.gz",
        "data/decision_three_engines/recovery/20260821/model_snapshot/p_fill_shadow.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/profit.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/promotion.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/three_engine_oof_top10.csv.gz",
        "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json",
        "data/decision_three_engines/recovery/20260821/source_candidates.csv",
        "data/decision_three_engines/recovery/20260821/source_meta.json",
        "data/decision_three_engines/recovery/20260821/stock_priors.csv",
        "outputs/decision/three_rank_top10_20260821.csv",
        "outputs/decision/three_rank_top10_20260821.evidence.json",
        "outputs/decision/three_rank_top10_20260821.json",
    }
    assert set(THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS) == expected_recovery
    assert THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS <= REQUIRED_ACTIVE_PIN_PATHS
    assert THREE_RANK_RECOVERY_EVIDENCE_PIN_PATHS.isdisjoint(
        THREE_RANK_DYNAMIC_ASSET_PATHS
    )
    assert {
        "scripts/build_decision_three_rank_snapshot.py",
        "tests/test_build_decision_three_rank_snapshot.py",
    } <= THREE_RANK_BEHAVIOR_PIN_PATHS
    assert "outputs/decision/three_rank_index.json" not in REQUIRED_ACTIVE_PIN_PATHS
    assert all((ROOT / relative).is_file() for relative in expected_recovery)
    recovery_root = ROOT / "data/decision_three_engines/recovery/20260821"
    actual_recovery = {
        path.relative_to(ROOT).as_posix()
        for path in recovery_root.rglob("*")
        if path.is_file()
    }
    assert actual_recovery == {
        relative
        for relative in expected_recovery
        if relative.startswith("data/decision_three_engines/recovery/20260821/")
    }
    assert not any(path.is_symlink() for path in recovery_root.rglob("*"))


def test_optional_strict_refreeze_mode_requires_all_core_heads_ready() -> None:
    current = build_three_rank_contract(ROOT)
    if current["all_core_heads_promoted"]:
        assert build_three_rank_contract(
            ROOT, require_all_core_ready=True
        )["all_core_heads_promoted"] is True
    else:
        with pytest.raises(
            ThreeRankRefreezeError,
            match="requires all three core heads READY",
        ):
            build_three_rank_contract(ROOT, require_all_core_ready=True)


def test_publisher_authorization_is_bound_to_the_exact_release_mode() -> None:
    current = build_three_rank_contract(ROOT)
    assert build_three_rank_contract(
        ROOT,
        expected_release_mode=current["release_mode"],
    )["release_mode"] == current["release_mode"]
    opposite = (
        "PROMOTION_READY_PARTIAL"
        if current["release_mode"] == "ALL_CORE_READY"
        else "ALL_CORE_READY"
    )
    with pytest.raises(ThreeRankRefreezeError, match="differs from publisher"):
        build_three_rank_contract(ROOT, expected_release_mode=opposite)


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        ("unexpected_key", "keys drift"),
        ("membership", "membership_authority"),
        ("feature_columns", "runtime_feature_columns"),
        ("secondary_promoted", "READY and promoted must agree"),
        ("shadow_override", "must remain false"),
        ("asset_hash", "differs from pinned_files"),
        ("release_mode", "release_mode is inconsistent"),
    ),
)
def test_three_rank_exact_key_and_fail_closed_contract_rejects_drift(
    mutation: str,
    match: str,
) -> None:
    contract = build_three_rank_contract(ROOT)
    manifest = _manifest_for(contract)
    if mutation == "unexpected_key":
        contract["unexpected"] = True
    elif mutation == "membership":
        contract["membership_authority"] = "shadow_selector"
    elif mutation == "feature_columns":
        contract["runtime_feature_columns"] = contract[
            "runtime_feature_columns"
        ][:-1]
    elif mutation == "secondary_promoted":
        contract["heads"]["big_loss"]["promoted"] = True
    elif mutation == "shadow_override":
        contract["fail_closed"]["shadow_may_override_core_ranks"] = True
    elif mutation == "asset_hash":
        contract["heads"]["promotion"]["artifact_sha256"] = "0" * 64
    elif mutation == "release_mode":
        contract["release_mode"] = (
            "PROMOTION_READY_PARTIAL"
            if contract["release_mode"] == "ALL_CORE_READY"
            else "ALL_CORE_READY"
        )
    with pytest.raises(DecisionModelFreezeError, match=match):
        validate_production_three_rank_contract(
            ROOT,
            manifest,
            require_complete=True,
        )


def test_refrozen_manifest_preserves_legacy_contract_and_repins_every_surface() -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    candidate = build_refrozen_manifest(ROOT, current)
    state = (
        "ready"
        if candidate["production"]["three_rank"]["all_core_heads_promoted"]
        else "partial"
    )
    assert candidate["freeze_id"].startswith(
        f"dc20_decision_three_rank_v2_{state}_d20260814_"
    )
    assert candidate["training_cutoff_signal_date"] == current[
        "training_cutoff_signal_date"
    ]
    assert candidate["history_snapshot"] == current["history_snapshot"]
    assert candidate["behavior_contract"] == current["behavior_contract"]
    for key in (
        "model_version",
        "promoted",
        "trade_selector_version",
        "trade_selector_promoted",
        "formal_status",
        "formal_buy_count",
        "legacy_v1_audit",
        "canonical_v2",
    ):
        assert candidate["production"][key] == current["production"][key]
    assert REQUIRED_ACTIVE_PIN_PATHS <= set(candidate["pinned_files"])
    for relative in REQUIRED_ACTIVE_PIN_PATHS:
        assert candidate["pinned_files"][relative] == _sha256(ROOT / relative)


def test_refrozen_candidate_authorizes_current_activation_source_six() -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    candidate = build_refrozen_manifest(ROOT, current)
    source_paths = (
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/trade_selector.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/action_plan.py",
        "scripts/publish_decision_action.py",
        "scripts/replay_frozen_canonical_v2.py",
    )
    assert candidate["production"]["three_rank"]
    assert {
        relative: candidate["pinned_files"].get(relative)
        for relative in source_paths
    } == {relative: _sha256(ROOT / relative) for relative in source_paths}


def test_atomic_writer_replaces_complete_json_without_temporary_residue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "freeze.json"
    target.write_text('{"old":true}\n', encoding="utf-8")
    _atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob(".freeze.json.*.tmp"))


def test_diagnostic_frozen_engine_bypasses_three_rank_assets_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_overlay_load(*_args, **_kwargs):
        raise AssertionError("diagnostic replay must not inspect overlay assets")

    monkeypatch.setattr(
        auction_engine_module,
        "load_three_engine_artifacts",
        reject_overlay_load,
    )
    engine = object.__new__(DiagnosticFrozenEngine)
    canonical = pd.DataFrame(
        [
            {
                "signal_date": "20260814",
                "ts_code": "000001.SZ",
                "promotion_rank": 7,
                "selected": 0,
            }
        ]
    )
    missing_or_tampered_overlay_pool = pd.DataFrame(
        [{"ts_code": "000001.SZ", "artifact_sha256": "tampered"}]
    )
    actual = engine._apply_three_engine_runtime(
        canonical,
        missing_or_tampered_overlay_pool,
        "20260814",
    )
    pd.testing.assert_frame_equal(actual, canonical, check_exact=True)
    assert actual is not canonical


def test_build_refreeze_does_not_mutate_input_mapping() -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    before = copy.deepcopy(current)
    build_refrozen_manifest(ROOT, current)
    assert current == before


@pytest.mark.parametrize(
    "relative",
    (
        "decision.html",
        "data/decision_three_engines/recovery/20260821/manifest.json",
        "outputs/decision/three_rank_top10_20260821.json",
    ),
)
def test_active_refreeze_cannot_rebless_non_dynamic_pin_drift(
    relative: str,
) -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    active_three_rank = build_refrozen_manifest(ROOT, current)
    active_three_rank["pinned_files"][relative] = "0" * 64
    with pytest.raises(
        ThreeRankRefreezeError,
        match="non-dynamic Decision pin drifted and cannot be reblessed",
    ):
        build_refrozen_manifest(ROOT, active_three_rank)


def test_active_refreeze_only_allows_the_exact_dynamic_asset_set_to_change() -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    active_three_rank = build_refrozen_manifest(ROOT, current)
    for relative in THREE_RANK_DYNAMIC_ASSET_PATHS:
        active_three_rank["pinned_files"][relative] = "0" * 64
    refreshed = build_refrozen_manifest(ROOT, active_three_rank)
    for relative in THREE_RANK_DYNAMIC_ASSET_PATHS:
        assert refreshed["pinned_files"][relative] == _sha256(ROOT / relative)


def test_one_time_migration_exception_requires_exact_legacy_freeze_id() -> None:
    current = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    # Exercise the legacy-to-three-rank branch explicitly.  Once the real
    # repository has migrated, the on-disk manifest already contains the
    # overlay and changing only its freeze_id would test the active-refreeze
    # branch instead of the one-time migration exception.
    current["production"].pop("three_rank", None)
    current["freeze_id"] = "unreviewed-pre-three-rank-freeze"
    with pytest.raises(
        ThreeRankRefreezeError,
        match="exact reviewed pre-three-rank freeze",
    ):
        build_refrozen_manifest(ROOT, current)
