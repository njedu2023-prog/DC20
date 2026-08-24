from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_decision_executable_profit_shadow_contract import (  # noqa: E402
    ExecutableProfitShadowContractError,
    validate_contract,
    validate_payloads,
)


def _payloads() -> tuple[dict, dict, dict, dict]:
    contract = json.loads(
        (ROOT / "models/decision_executable_profit_shadow_contract.json").read_text(
            encoding="utf-8"
        )
    )
    freeze = json.loads(
        (ROOT / "models/decision_model_freeze.json").read_text(encoding="utf-8")
    )
    validation = json.loads(
        (
            ROOT / "models/decision_three_engines/validation_latest.json"
        ).read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (
            ROOT / "data/decision_three_engines/five_year_ledger_manifest.json"
        ).read_text(encoding="utf-8")
    )
    return contract, freeze, validation, manifest


def _rehash_promotion_identity(contract: dict) -> None:
    payload = json.dumps(
        contract["promotion_identity"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    contract["promotion_contract_sha256"] = hashlib.sha256(payload).hexdigest()


def test_repository_executable_profit_shadow_contract_is_valid() -> None:
    result = validate_contract(ROOT)
    golden = result.pop("promotion_golden_vector")
    assert result == {
        "valid": True,
        "contract_id": "dc20_executable_profit_shadow_top2_v1",
        "status": "FROZEN_DESIGN_IMPLEMENTATION_PENDING",
        "promotion_freeze_id": (
            "dc20_decision_three_rank_v2_partial_d20260814_de4cae2427593b05"
        ),
        "promotion_artifact_sha256": (
            "b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c"
        ),
        "promotion_contract_sha256": (
            "841bd93de60ab1c761786ba8cd2bdbb92bad8db6b8cb8a794e42ff7b070a8225"
        ),
        "promotion_golden_fixture_sha256": (
            "ced6754bb2b64cc8c8603a64c9208a2ff2bc4db531d14c330f5c11f2401fecdd"
        ),
        "promotion_golden_hard_pool_sha256": (
            "b767ed82ec6e28ae6b75273a0297f09a93d56fbb56365f354a0cfacc65d5281c"
        ),
        "promotion_golden_output_sha256": (
            "567acbb9ef5a8b64e7e67f22b2ccc02a894cd492c2be65c2289d520e77d9201d"
        ),
        "shadow_slots": 2,
        "official_trade_action_allowed": False,
    }
    assert golden["valid"] is True
    assert golden["fixture_id"] == "dc20_promotion_b7837d_d20260812_v1"
    assert golden["pool_size"] == 13
    assert golden["stage_counts"] == {"2→3": 7, "3→4": 6}


def test_promotion_artifact_drift_fails_closed() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(validation)
    changed["artifacts"]["promotion"]["sha256"] = "0" * 64
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="promotion artifact no longer matches",
    ):
        validate_payloads(
            contract=contract,
            freeze=freeze,
            validation=changed,
            ledger_manifest=manifest,
        )


def test_promotion_identity_hash_tamper_fails_closed() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["promotion_identity"]["candidate_and_sort_contract"]["top_n"] = 9
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="promotion_contract_sha256 does not bind",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_inactive_promotion_golden_binding_fails_closed() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["promotion_identity"]["golden_vector"]["status"] = "PENDING"
    _rehash_promotion_identity(changed)
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="golden-vector binding is not active",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_golden_model_identity_cannot_drift_from_frozen_promotion() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["promotion_identity"]["golden_vector"]["model_artifact_sha256"] = (
        "0" * 64
    )
    _rehash_promotion_identity(changed)
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="golden-vector model identity drifted",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_pfill_ledger_cannot_be_relabelled_as_executable_profit() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["ledger_separation"]["existing_p_fill_shadow_top2_ledger"] = (
        "EXECUTABLE_PROFIT_LEDGER"
    )
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="must remain separate",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_nonfill_cannot_be_dropped_or_counted_as_conditional_return() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["outcome_and_cost_contract"]["nonfill_strategy_slot_return"] = None
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="nonfill must be null conditionally and zero only in strategy",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_shadow_top2_cannot_authorize_a_trade() -> None:
    contract, freeze, validation, manifest = _payloads()
    changed = copy.deepcopy(contract)
    changed["ranking_contract"]["may_create_formal_buy_action"] = True
    with pytest.raises(
        ExecutableProfitShadowContractError,
        match="selection or eligibility contract drifted",
    ):
        validate_payloads(
            contract=changed,
            freeze=freeze,
            validation=validation,
            ledger_manifest=manifest,
        )


def test_shared_three_engine_publisher_is_research_only_and_guarded() -> None:
    workflow = (
        ROOT / ".github/workflows/train_decision_three_engines.yml"
    ).read_text(encoding="utf-8")
    command = "python scripts/validate_decision_executable_profit_shadow_contract.py"
    assert workflow.count(command) == 2

    preflight = workflow.index(
        "Validate frozen promotion identity before research retraining"
    )
    publish_block = workflow.index(
        "Reject publish-capable shared retraining while promotion is frozen"
    )
    training = workflow.index("Train nested chronological three-engine models")
    publisher = workflow.index(
        "Publisher defense-in-depth rejected a candidate that changes the frozen promotion identity"
    )
    commit = workflow.index("git commit -m 'model: retrain independent Decision three engines'")

    assert preflight < publish_block < training
    assert publisher < commit
    assert "if: ${{ steps.mode.outputs.requested_publish == 'true' }}" in workflow[
        publish_block:training
    ]
    assert "a separate fusion-only publisher is required" in workflow[
        publish_block:training
    ]
    assert "exit 1" in workflow[publish_block:training]
