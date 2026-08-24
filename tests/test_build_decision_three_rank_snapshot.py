from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from scripts.build_decision_three_rank_snapshot import (
    EVIDENCE_PATH,
    EXPECTED_BINDINGS,
    EXPECTED_HARD_POOL,
    EXPECTED_SEALED_RUNTIME_BINDINGS,
    EXPECTED_TENCENT_SHA256,
    EXIT_DATE,
    RECOVERY_MANIFEST_PATH,
    RECOVERY_SOURCE_META_PATH,
    RECOVERY_SOURCE_PATH,
    RUNTIME_BINDING_PATHS,
    SIGNAL_DATE,
    ThreeRankSnapshotError,
    build_decision_three_rank_snapshot,
    load_bound_candidate_pool,
    load_recovery_inputs,
    normalize_tencent_payload,
    validate_static_bindings,
)
from top10decision.decision.d_close_features import compute_d_close_features
from top10decision.decision.three_engine_models import (
    ThreeEngineArtifactError,
    load_research_only_legacy_three_engine_snapshot,
    load_three_engine_artifacts,
)
from top10decision.decision.three_rank import validate_three_rank_contract


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OUTPUT_SHA256 = {
    "outputs/decision/three_rank_top10_20260821.json": (
        "a43285654a32206d3a9deeaac1b8c373cafe71bf39610af779b66df4bb458e34"
    ),
    "outputs/decision/three_rank_top10_20260821.csv": (
        "dbe41e6f269f186a7bf46d00350844e62720b8d896966dc9a02990c52608896c"
    ),
    EVIDENCE_PATH: (
        "108df999852f6920f97023264b5df8dd8b42349307e0f110a0bccc8f682d0333"
    ),
    RECOVERY_MANIFEST_PATH: (
        "14ecaad8e8b393e98a80c0f08d10b9e6e17e51213586cb88973d102dff66d1c8"
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_file(source_root: Path, target_root: Path, relative: str) -> None:
    destination = target_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / relative, destination)


def _copy_clean_runtime_root(target: Path) -> None:
    shutil.copytree(
        ROOT / "data/decision_three_engines/recovery/20260821",
        target / "data/decision_three_engines/recovery/20260821",
    )


def test_snapshot_is_exact_hash_bound_partial_and_buy_zero() -> None:
    bindings = validate_static_bindings(ROOT)
    assert bindings == {
        relative: EXPECTED_SEALED_RUNTIME_BINDINGS[relative]
        for relative in RUNTIME_BINDING_PATHS
    }

    result = build_decision_three_rank_snapshot(ROOT)
    contract = result["contract"]
    validate_three_rank_contract(contract)
    assert contract["signal_date"] == SIGNAL_DATE
    assert contract["exec_date"] == "20260824"
    assert contract["exit_date"] == EXIT_DATE
    assert contract["status"] == "PARTIAL_MODELS_NOT_READY"
    assert contract["promotion_pool_size"] == len(EXPECTED_HARD_POOL) == 9
    assert contract["top10_count"] == 9
    assert contract["models"]["promotion"]["status"] == "READY"
    assert contract["models"]["big_loss"]["status"] == (
        "NOT_READY_VALIDATION_GATE"
    )
    assert contract["models"]["profit"]["status"] == (
        "NOT_READY_VALIDATION_GATE"
    )
    assert "p_fill_shadow" not in contract["models"]
    assert (
        contract["models"]["promotion"]["validation_gate_pass_count"],
        contract["models"]["promotion"]["validation_gate_total_count"],
        contract["models"]["promotion"]["validation_gate_score_pct"],
    ) == (26, 26, 100.0)
    assert (
        contract["models"]["big_loss"]["validation_gate_pass_count"],
        contract["models"]["big_loss"]["validation_gate_total_count"],
        contract["models"]["big_loss"]["validation_gate_score_pct"],
    ) == (17, 26, 65.4)
    assert (
        contract["models"]["profit"]["validation_gate_pass_count"],
        contract["models"]["profit"]["validation_gate_total_count"],
        contract["models"]["profit"]["validation_gate_score_pct"],
    ) == (20, 26, 76.9)
    assert [row["promotion_rank"] for row in contract["rows"]] == list(
        range(1, 10)
    )
    assert all(
        row["big_loss_safety_rank"] is None
        and row["predicted_big_loss_probability"] is None
        and row["profit_rank"] is None
        and row["predicted_profit_probability"] is None
        for row in contract["rows"]
    )
    assert contract["execution_summary"] == {
        "actual_execution_claimed": False,
        "buy_count": 0,
        "decision": "NO_TRADE",
        "reason": "dated research ranking recovery; B/C validation gates are not READY",
    }

    evidence = result["evidence"]
    assert evidence["execution"]["buy_count"] == 0
    assert evidence["ranking_proof"]["unready_head_fields_are_null"] is True
    assert evidence["model_bindings"]["ledger"]["sha256"] == (
        EXPECTED_SEALED_RUNTIME_BINDINGS[
            "data/decision_three_engines/recovery/20260821/model_snapshot/"
            "five_year_supervised_ledger.csv.gz"
        ]
    )
    evidence_text = json.dumps(evidence, ensure_ascii=False)
    assert "data/pred/" not in evidence_text
    assert "/private/" not in evidence_text
    assert evidence["source_bindings"]["candidate"]["path"] == (
        RECOVERY_SOURCE_PATH
    )
    assert evidence["source_bindings"]["candidate_meta"]["path"] == (
        RECOVERY_SOURCE_META_PATH
    )
    for relative, expected in EXPECTED_OUTPUT_SHA256.items():
        assert _sha256(ROOT / relative) == expected


def test_snapshot_rerun_is_byte_idempotent() -> None:
    paths = tuple(ROOT / relative for relative in EXPECTED_OUTPUT_SHA256)
    before = {path: _sha256(path) for path in paths}
    first = build_decision_three_rank_snapshot(ROOT)
    middle = {path: _sha256(path) for path in paths}
    second = build_decision_three_rank_snapshot(ROOT)
    after = {path: _sha256(path) for path in paths}
    assert before == middle == after
    assert first["contract"]["bundle_sha256"] == second["contract"][
        "bundle_sha256"
    ]


def test_sealed_legacy_ready_artifact_is_research_only_and_exact_hash_bound(
    tmp_path: Path,
) -> None:
    validation_path = ROOT / (
        "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json"
    )
    with pytest.raises(
        ThreeEngineArtifactError,
        match="production bundle presence disagrees",
    ):
        load_three_engine_artifacts(validation_path, root=ROOT)
    loaded = load_research_only_legacy_three_engine_snapshot(
        validation_path,
        root=ROOT,
    )
    assert loaded.metadata["promotion"][
        "research_only_legacy_calibration_evidence_missing"
    ] is True

    _copy_clean_runtime_root(tmp_path)
    copied_validation = tmp_path / (
        "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json"
    )
    copied_validation.write_bytes(copied_validation.read_bytes() + b"\n")
    with pytest.raises(
        ThreeEngineArtifactError,
        match="research-only legacy validation SHA-256 drifted",
    ):
        load_research_only_legacy_three_engine_snapshot(
            copied_validation,
            root=tmp_path,
        )


@pytest.mark.parametrize(
    "drifted_relative", (RECOVERY_SOURCE_PATH, RECOVERY_SOURCE_META_PATH)
)
def test_immutable_recovery_source_or_meta_drift_fails_closed(
    tmp_path: Path,
    drifted_relative: str,
) -> None:
    for relative in (RECOVERY_SOURCE_PATH, RECOVERY_SOURCE_META_PATH):
        _copy_file(ROOT, tmp_path, relative)
    target = tmp_path / drifted_relative
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ThreeRankSnapshotError, match="candidate source drifted"):
        load_bound_candidate_pool(tmp_path, recovery_only=True)


def test_synthetic_future_public_bar_cannot_change_normalized_input_or_features() -> None:
    code = "002491.SZ"
    _, bars_by_code, _ = load_recovery_inputs(ROOT)
    recovery = bars_by_code[code]
    days = [
        [
            f"{str(row.trade_date)[:4]}-{str(row.trade_date)[4:6]}-{str(row.trade_date)[6:]}",
            str(row.open),
            str(row.close),
            str(row.high),
            str(row.low),
            str(row.volume),
        ]
        for row in recovery.itertuples(index=False)
    ]
    original_payload = {"code": 0, "data": {"sz002491": {"day": days}}}
    future_payload = json.loads(json.dumps(original_payload))
    future_payload["data"]["sz002491"]["day"].append(
        ["2026-08-24", "99.000", "100.000", "101.000", "98.000", "1.000"]
    )

    original, original_audit = normalize_tencent_payload(
        original_payload, code=code
    )
    with_future, future_audit = normalize_tencent_payload(
        future_payload, code=code
    )
    pd.testing.assert_frame_equal(original, with_future)
    original_features = compute_d_close_features(original, cutoff_date=SIGNAL_DATE)
    future_features = compute_d_close_features(with_future, cutoff_date=SIGNAL_DATE)
    pd.testing.assert_frame_equal(original_features, future_features)
    assert original["trade_date"].max() == SIGNAL_DATE
    assert original_audit["discarded_after_cutoff_rows"] == 0
    assert future_audit["discarded_after_cutoff_rows"] == 1


def test_every_dc20_recovery_bar_is_at_or_before_d_and_hash_bound() -> None:
    pool, bars_by_code, manifest = load_recovery_inputs(ROOT)
    assert len(pool) == len(EXPECTED_HARD_POOL)
    assert set(bars_by_code) == set(EXPECTED_TENCENT_SHA256)
    assert manifest["owner"] == "njedu2023-prog/DC20"
    assert manifest["runtime_dependency_on_external_repository"] is False
    assert (
        manifest["runtime_dependency_on_public_cache_after_materialization"]
        is False
    )
    assert _sha256(ROOT / RECOVERY_SOURCE_PATH) == EXPECTED_BINDINGS[
        "data/pred/archive/pred_source_20260821.csv"
    ]
    assert _sha256(ROOT / RECOVERY_SOURCE_META_PATH) == EXPECTED_BINDINGS[
        "data/pred/_pred_source_meta.json"
    ]
    for record in manifest["market_inputs"]:
        code = record["ts_code"]
        assert record["raw_sha256"] == EXPECTED_TENCENT_SHA256[code]
        assert record["normalized_max_date"] == SIGNAL_DATE
        assert bars_by_code[code]["trade_date"].max() == SIGNAL_DATE
        assert not bars_by_code[code]["trade_date"].gt(SIGNAL_DATE).any()


def test_recovery_file_drift_fails_closed(tmp_path: Path) -> None:
    source = ROOT / "data/decision_three_engines/recovery/20260821"
    target = tmp_path / "data/decision_three_engines/recovery/20260821"
    shutil.copytree(source, target)
    bar_path = target / "daily_bars/002491_SZ.csv.gz"
    bar_path.write_bytes(bar_path.read_bytes() + b"drift")
    with pytest.raises(ThreeRankSnapshotError, match="bar hash drifted"):
        load_recovery_inputs(tmp_path)


@pytest.mark.parametrize(
    "relative",
    (
        "data/decision_three_engines/recovery/20260821/model_snapshot/"
        "promotion.joblib",
        "data/decision_three_engines/recovery/20260821/model_snapshot/"
        "five_year_supervised_ledger.csv.gz",
    ),
)
def test_sealed_model_or_ledger_drift_fails_closed(
    tmp_path: Path,
    relative: str,
) -> None:
    _copy_clean_runtime_root(tmp_path)
    target = tmp_path / relative
    target.write_bytes(target.read_bytes() + b"drift")
    with pytest.raises(ThreeRankSnapshotError, match="sealed historical"):
        validate_static_bindings(tmp_path)


def test_clean_root_rebuild_needs_no_data_pred_cache_network_or_prior_tables(
    tmp_path: Path,
) -> None:
    _copy_clean_runtime_root(tmp_path)
    assert not (tmp_path / "data/pred").exists()
    assert not (tmp_path / "data/auction_v3/promotion_prior").exists()
    assert not (tmp_path / "models").exists()
    assert not (
        tmp_path / "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
    ).exists()

    result = build_decision_three_rank_snapshot(tmp_path)
    assert result["contract"]["bundle_sha256"] == (
        "1508feabb2d684c89d833db71ad97220d9350939d34a7062ec5c6433c0890818"
    )
    for relative, expected in EXPECTED_OUTPUT_SHA256.items():
        assert _sha256(tmp_path / relative) == expected
