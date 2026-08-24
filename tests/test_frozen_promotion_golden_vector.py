from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_frozen_promotion_golden_vector import (  # noqa: E402
    DEFAULT_FIXTURE_PATH,
    FrozenPromotionGoldenVectorError,
    validate_frozen_promotion_golden_vector,
)


def _fixture() -> dict:
    return json.loads((ROOT / DEFAULT_FIXTURE_PATH).read_text(encoding="utf-8"))


def _contract_binding() -> dict:
    contract = json.loads(
        (
            ROOT / "models/decision_executable_profit_shadow_contract.json"
        ).read_text(encoding="utf-8")
    )
    return contract["promotion_identity"]["golden_vector"]


def _isolated_root(tmp_path: Path, fixture: dict) -> Path:
    root = tmp_path / "repo"
    (root / DEFAULT_FIXTURE_PATH.parent).mkdir(parents=True)
    (root / "models/decision_three_engines").mkdir(parents=True)
    shutil.copytree(ROOT / "src", root / "src")
    shutil.copy2(
        ROOT / "models/decision_three_engines/promotion.joblib",
        root / "models/decision_three_engines/promotion.joblib",
    )
    provenance = _fixture()["generation_provenance"]
    for source_key in ("candidate_source_path", "feature_source_path"):
        source = ROOT / provenance[source_key]
        target = root / provenance[source_key]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (root / DEFAULT_FIXTURE_PATH).write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _validate_mutation(tmp_path: Path, fixture: dict) -> dict:
    return validate_frozen_promotion_golden_vector(
        _isolated_root(tmp_path, fixture)
    )


def test_repository_active_promotion_golden_vector_is_exact() -> None:
    result = validate_frozen_promotion_golden_vector(
        ROOT,
        binding=_contract_binding(),
    )
    assert result == {
        "valid": True,
        "fixture_id": "dc20_promotion_b7837d_d20260812_v1",
        "fixture_sha256": (
            "ced6754bb2b64cc8c8603a64c9208a2ff2bc4db531d14c330f5c11f2401fecdd"
        ),
        "signal_date": "20260812",
        "candidate_source_sha256": (
            "cbcd0f6b21c7ebdf0410ade39e4c053c8bab8c7efa416a89f2752aa74b912527"
        ),
        "feature_generation_source_sha256": (
            "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
        ),
        "promotion_artifact_sha256": (
            "b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c"
        ),
        "authoritative_hard_pool_sha256": (
            "b767ed82ec6e28ae6b75273a0297f09a93d56fbb56365f354a0cfacc65d5281c"
        ),
        "feature_snapshot_sha256": (
            "f71b1975a3cf8e145e405cc313f0c95efd2acfa1f0569a85ecc8383dd1ee48dc"
        ),
        "golden_output_sha256": (
            "567acbb9ef5a8b64e7e67f22b2ccc02a894cd492c2be65c2289d520e77d9201d"
        ),
        "pool_size": 13,
        "stage_counts": {"2→3": 7, "3→4": 6},
        "top_n": 10,
    }


def test_semantic_vector_is_independent_of_fixture_row_order(tmp_path: Path) -> None:
    changed = _fixture()
    changed["rows"] = list(reversed(changed["rows"]))
    result = _validate_mutation(tmp_path, changed)
    assert result["authoritative_hard_pool_sha256"] == (
        "b767ed82ec6e28ae6b75273a0297f09a93d56fbb56365f354a0cfacc65d5281c"
    )
    assert result["golden_output_sha256"] == (
        "567acbb9ef5a8b64e7e67f22b2ccc02a894cd492c2be65c2289d520e77d9201d"
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_missing_or_duplicate_hard_pool_identity_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    changed = _fixture()
    if mutation == "missing":
        changed["rows"].pop()
    else:
        changed["rows"][-1] = copy.deepcopy(changed["rows"][0])
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="pool size|duplicate identities",
    ):
        _validate_mutation(tmp_path, changed)


def test_stage_transition_drift_fails_closed(tmp_path: Path) -> None:
    changed = _fixture()
    changed["rows"][0]["stage_transition"] = (
        "2→3"
        if changed["rows"][0]["stage_transition"] == "3→4"
        else "3→4"
    )
    with pytest.raises(FrozenPromotionGoldenVectorError, match="stage transition"):
        _validate_mutation(tmp_path, changed)


def test_one_feature_drift_fails_closed(tmp_path: Path) -> None:
    changed = _fixture()
    changed["rows"][0]["features"]["atr"] = "999"
    with pytest.raises(FrozenPromotionGoldenVectorError, match="feature snapshot hash"):
        _validate_mutation(tmp_path, changed)


def test_expected_probability_or_rank_drift_fails_closed(tmp_path: Path) -> None:
    changed = _fixture()
    changed["rows"][0]["expected"]["rank"] = 99
    with pytest.raises(FrozenPromotionGoldenVectorError, match="output drifted"):
        _validate_mutation(tmp_path, changed)


def test_artifact_hash_drift_fails_closed(tmp_path: Path) -> None:
    changed = _fixture()
    changed["model"]["artifact_sha256"] = "0" * 64
    with pytest.raises(FrozenPromotionGoldenVectorError, match="artifact hash drifted"):
        _validate_mutation(tmp_path, changed)


@pytest.mark.parametrize("forbidden_key", ["profit_hit", "market_fill", "target_exit_date"])
def test_future_truth_and_cross_head_fields_fail_closed(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    changed = _fixture()
    changed["rows"][0][forbidden_key] = None
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="future truth, a label, or a cross-head output",
    ):
        _validate_mutation(tmp_path, changed)


def test_recovery_dependency_fails_closed(tmp_path: Path) -> None:
    changed = _fixture()
    changed["generation_provenance"]["candidate_source_path"] = (
        "data/decision_three_engines/recovery/20260812/pred.csv"
    )
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="forbidden external or recovery dependency",
    ):
        _validate_mutation(tmp_path, changed)


def test_contract_binding_drift_fails_closed() -> None:
    changed = copy.deepcopy(_contract_binding())
    changed["golden_output_sha256"] = "0" * 64
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="contract golden-vector golden_output_sha256 drifted",
    ):
        validate_frozen_promotion_golden_vector(ROOT, binding=changed)


def test_contract_candidate_source_binding_drift_fails_closed() -> None:
    changed = copy.deepcopy(_contract_binding())
    changed["candidate_source_sha256"] = "0" * 64
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="contract golden-vector candidate_source_sha256 drifted",
    ):
        validate_frozen_promotion_golden_vector(ROOT, binding=changed)


def test_fixture_provenance_must_match_the_pinned_candidate_source(
    tmp_path: Path,
) -> None:
    changed = _fixture()
    changed["generation_provenance"]["candidate_source_sha256"] = "0" * 64
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="candidate source hash drifted",
    ):
        _validate_mutation(tmp_path, changed)


def test_actual_candidate_projection_must_match_fixture_identities(
    tmp_path: Path,
) -> None:
    changed = _fixture()
    root = _isolated_root(tmp_path, changed)
    provenance = changed["generation_provenance"]
    source = root / provenance["candidate_source_path"]
    frame = pd.read_csv(
        source,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    limit_pct = pd.to_numeric(
        frame["mechanism_limit_pct"], errors="coerce"
    )
    row_index = frame.index[
        frame["stage_transition"].isin({"2→3", "3→4"})
        & limit_pct.sub(10.0).abs().le(1e-9)
    ][0]
    frame.loc[row_index, "ts_code"] = "999999.SH"
    frame.to_csv(source, index=False, encoding="utf-8-sig")
    changed["generation_provenance"]["candidate_source_sha256"] = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    (root / DEFAULT_FIXTURE_PATH).write_text(
        json.dumps(changed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="fixture hard pool differs from the pinned candidate source",
    ):
        validate_frozen_promotion_golden_vector(root)


def test_actual_feature_generation_source_hash_must_not_drift(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    root = _isolated_root(tmp_path, fixture)
    source = root / fixture["generation_provenance"]["feature_source_path"]
    source.write_bytes(source.read_bytes() + b"drift")
    with pytest.raises(
        FrozenPromotionGoldenVectorError,
        match="feature-generation source hash drifted",
    ):
        validate_frozen_promotion_golden_vector(root)
