from __future__ import annotations

import copy
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


def test_repository_executable_profit_shadow_contract_is_valid() -> None:
    result = validate_contract(ROOT)
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
            "8bd0223cd4a80585c9f3eb63977e33d6d8b9fe0dc14ca95429bf6db8decd7f71"
        ),
        "shadow_slots": 2,
        "official_trade_action_allowed": False,
    }


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
