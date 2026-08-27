from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONTRACT_PATH = Path(
    "models/decision_executable_profit_shadow_contract.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# The v1 shadow-design contract is an immutable historical record.  Its
# promotion source pin therefore remains the exact pre-P0 byte identity, while
# the active freeze records one reviewed source-only rotation that adds the
# promotion-only P0 loader.  Accepting this one exact pair keeps the historical
# contract honest without letting it veto the current primary D path.  Any
# later source change still fails closed until it receives a new review.
APPROVED_HISTORICAL_CODE_PIN_ROTATIONS = {
    "src/top10decision/decision/three_engine_models.py": {
        "prior_sha256": (
            "f7358d952fef888d1614672128c1ab524add02d4863bac7e45217550b842fb34"
        ),
        "current_sha256": (
            "9a4a2405e3b95af9f1c05100aa8b97dc8b3ee62d63b4dda12e13f7f0fcd1de4c"
        ),
        "rotation_id": "dc20_restore_canonical_source_external_runtime_20260826",
        "evidence_path": "models/decision_source_surface_rotation_20260824.json",
        "classification": "promotion_only_primary_d_loader",
    }
}


class ExecutableProfitShadowContractError(ValueError):
    """Raised when the frozen shadow design is inconsistent with DC20 truth."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutableProfitShadowContractError(
            f"cannot load contract dependency {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ExecutableProfitShadowContractError(f"{path} must contain an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitShadowContractError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    _expect(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _historical_code_pin_is_current_or_reviewed(
    *,
    freeze: Mapping[str, Any],
    path: str,
    historical_sha256: Any,
    current_sha256: Any,
) -> bool:
    if current_sha256 == historical_sha256:
        return True
    approved = APPROVED_HISTORICAL_CODE_PIN_ROTATIONS.get(path)
    if not isinstance(approved, Mapping):
        return False
    rotation = freeze.get("source_surface_rotation")
    if not isinstance(rotation, Mapping):
        return False
    return bool(
        historical_sha256 == approved.get("prior_sha256")
        and current_sha256 == approved.get("current_sha256")
        and rotation.get("schema_version")
        == "decision_source_surface_rotation_v1"
        and rotation.get("rotation_id") == approved.get("rotation_id")
        and rotation.get("evidence_path") == approved.get("evidence_path")
        and SHA256_RE.fullmatch(str(rotation.get("evidence_sha256", "")))
        is not None
    )


def _validate_reviewed_rotation_evidence(
    *,
    root: Path,
    freeze: Mapping[str, Any],
) -> None:
    rotation = _mapping(
        freeze.get("source_surface_rotation"),
        "freeze source_surface_rotation",
    )
    evidence_relative = Path(str(rotation.get("evidence_path") or ""))
    _expect(
        not evidence_relative.is_absolute(),
        "source rotation evidence path must be repository-relative",
    )
    evidence_path = (root / evidence_relative).resolve()
    try:
        evidence_path.relative_to(root)
    except ValueError as exc:
        raise ExecutableProfitShadowContractError(
            "source rotation evidence escaped the repository root"
        ) from exc
    _expect(evidence_path.is_file(), "source rotation evidence is missing")
    _expect(
        _file_sha256(evidence_path) == rotation.get("evidence_sha256"),
        "source rotation evidence hash drifted",
    )
    evidence = _load_json(evidence_path)
    _expect(
        evidence.get("schema_version") == "decision_source_surface_rotation_v1"
        and evidence.get("rotation_id") == rotation.get("rotation_id"),
        "source rotation evidence identity drifted",
    )
    protected = _mapping(
        evidence.get("protected_model_identity"),
        "source rotation protected_model_identity",
    )
    _expect(
        protected.get("model_identity_changed") is False
        and protected.get("training_ledger_changed") is False
        and protected.get("action_plan_changed") is False,
        "source rotation changed protected model, ledger, or Action identity",
    )
    changes = evidence.get("pin_changes")
    _expect(isinstance(changes, list), "source rotation pin changes are missing")
    freeze_pins = _mapping(freeze.get("pinned_files"), "freeze pinned_files")
    for path, approved in APPROVED_HISTORICAL_CODE_PIN_ROTATIONS.items():
        matches = [
            item
            for item in changes
            if isinstance(item, Mapping) and item.get("path") == path
        ]
        _expect(len(matches) == 1, "reviewed source rotation is not unique")
        change = matches[0]
        _expect(
            change.get("prior_sha256") == approved.get("prior_sha256")
            and change.get("current_sha256") == approved.get("current_sha256")
            and change.get("classification") == approved.get("classification")
            and freeze_pins.get(path) == approved.get("current_sha256"),
            "reviewed source rotation details drifted",
        )


def validate_payloads(
    *,
    contract: Mapping[str, Any],
    freeze: Mapping[str, Any],
    validation: Mapping[str, Any],
    ledger_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    _expect(
        contract.get("schema_version")
        == "dc20_executable_profit_shadow_contract_v1",
        "unexpected executable-profit shadow schema",
    )
    _expect(
        contract.get("status") == "FROZEN_DESIGN_IMPLEMENTATION_PENDING",
        "the first-step contract must not claim implementation or release",
    )

    authority = _mapping(contract.get("authority"), "authority")
    _expect(authority.get("repository") == "njedu2023-prog/DC20", "wrong owner")
    _expect(authority.get("branch") == "main", "wrong branch")
    _expect(
        GIT_SHA_RE.fullmatch(str(authority.get("reviewed_base_commit_sha", "")))
        is not None,
        "reviewed base commit must be a full Git SHA",
    )
    _expect(
        authority.get("runtime_dependency_on_top10_decision") is False,
        "DC20 must remain runtime-independent from top10-decision",
    )

    current = _mapping(contract.get("current_state"), "current_state")
    _expect(
        current.get("promotion_engine") == "READY_FROZEN_UNCHANGED",
        "promotion state must remain frozen",
    )
    _expect(
        current.get("executable_profit_engine") == "NOT_IMPLEMENTED",
        "the contract cannot imply a model already exists",
    )
    _expect(
        current.get("executable_profit_forward_ledger") == "NOT_STARTED",
        "the contract cannot imply a forward ledger already exists",
    )
    _expect(
        current.get("official_trade_action_allowed") is False,
        "shadow design must not authorize a trade",
    )

    promotion = _mapping(contract.get("promotion_freeze"), "promotion_freeze")
    _expect(promotion.get("immutable") is True, "promotion must be immutable")
    _expect(promotion.get("top_n") == 10, "promotion Top10 scope drifted")
    _expect(
        promotion.get("membership_authority")
        == "promotion_probability_engine_only",
        "promotion must remain the sole membership authority",
    )
    feature_policy = _mapping(
        promotion.get("downstream_model_feature_policy"),
        "promotion downstream feature policy",
    )
    _expect(
        feature_policy.get("promotion_rank_allowed_as_model_feature") is False
        and feature_policy.get("promotion_probability_allowed_as_model_feature")
        is False
        and feature_policy.get("promotion_membership_allowed_as_scope_filter") is True,
        "the downstream engine must only consume frozen membership from promotion",
    )
    identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
    identity_bytes = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    identity_sha256 = hashlib.sha256(identity_bytes).hexdigest()
    _expect(
        contract.get("promotion_contract_sha256") == identity_sha256,
        "promotion_contract_sha256 does not bind the canonical identity",
    )
    _expect(
        identity.get("freeze_id") == promotion.get("freeze_id"),
        "promotion identity freeze_id drifted",
    )

    frozen_three_rank = _mapping(
        _mapping(freeze.get("production"), "freeze.production").get("three_rank"),
        "freeze.production.three_rank",
    )
    _expect(
        freeze.get("freeze_id") == promotion.get("freeze_id"),
        "promotion freeze_id no longer matches",
    )
    _expect(
        freeze.get("training_cutoff_signal_date")
        == promotion.get("training_cutoff_signal_date"),
        "promotion training cutoff no longer matches",
    )
    _expect(
        frozen_three_rank.get("membership_authority")
        == promotion.get("membership_authority"),
        "membership authority no longer matches the production freeze",
    )
    _expect(
        frozen_three_rank.get("top_n") == promotion.get("top_n"),
        "Top10 size no longer matches the production freeze",
    )
    identity_features = _mapping(identity.get("features"), "promotion identity features")
    _expect(
        identity_features.get("feature_columns_sha256")
        == frozen_three_rank.get("feature_columns_sha256")
        and identity_features.get("runtime_feature_columns_sha256")
        == frozen_three_rank.get("runtime_feature_columns_sha256")
        and identity_features.get("runtime_feature_contract_version")
        == frozen_three_rank.get("runtime_feature_contract_version"),
        "promotion feature identity drifted",
    )
    identity_ledger = _mapping(
        identity.get("source_ledger"), "promotion identity source ledger"
    )
    frozen_ledger = _mapping(
        frozen_three_rank.get("source_ledger"), "frozen source ledger"
    )
    _expect(
        identity_ledger.get("path") == frozen_ledger.get("ledger_path")
        and identity_ledger.get("sha256") == frozen_ledger.get("ledger_sha256")
        and identity_ledger.get("manifest_path")
        == frozen_ledger.get("ledger_manifest_path")
        and identity_ledger.get("manifest_sha256")
        == frozen_ledger.get("ledger_manifest_sha256")
        and identity_ledger.get("data_validation_path")
        == frozen_ledger.get("data_validation_path")
        and identity_ledger.get("data_validation_sha256")
        == frozen_ledger.get("data_validation_sha256"),
        "promotion source-ledger identity drifted",
    )

    contract_model = _mapping(promotion.get("model"), "promotion model")
    frozen_model = _mapping(
        _mapping(frozen_three_rank.get("heads"), "frozen heads").get("promotion"),
        "frozen promotion head",
    )
    validation_model = _mapping(
        _mapping(validation.get("heads"), "validation heads").get("promotion"),
        "validated promotion head",
    )
    for field in (
        "status",
        "model_version",
        "model_as_of_date",
        "artifact_path",
        "artifact_sha256",
    ):
        _expect(
            frozen_model.get(field) == contract_model.get(field),
            f"frozen promotion {field} drifted",
        )
    _expect(validation_model.get("status") == "READY", "promotion is no longer READY")
    artifact = _mapping(
        _mapping(validation.get("artifacts"), "validation artifacts").get(
            "promotion"
        ),
        "validation promotion artifact",
    )
    _expect(
        artifact.get("path") == contract_model.get("artifact_path")
        and artifact.get("sha256") == contract_model.get("artifact_sha256")
        and SHA256_RE.fullmatch(str(artifact.get("sha256", ""))) is not None,
        "promotion artifact no longer matches the frozen contract",
    )
    identity_validation = _mapping(
        identity.get("validation"), "promotion identity validation"
    )
    frozen_validation = _mapping(
        frozen_three_rank.get("validation"), "frozen validation"
    )
    _expect(
        identity_validation.get("path") == frozen_validation.get("path")
        and identity_validation.get("sha256") == frozen_validation.get("sha256")
        and identity_validation.get("schema_version")
        == frozen_validation.get("schema_version")
        and identity_validation.get("promotion_status") == "READY",
        "promotion validation identity drifted",
    )
    identity_oof = _mapping(identity.get("oof_top10"), "promotion identity OOF")
    frozen_oof = _mapping(frozen_three_rank.get("oof_top10"), "frozen OOF")
    _expect(
        all(identity_oof.get(field) == frozen_oof.get(field) for field in (
            "path", "sha256", "rows", "dates"
        )),
        "promotion OOF identity drifted",
    )
    code_pins = _mapping(
        identity.get("code_and_runtime_pins"), "promotion code pins"
    )
    freeze_pins = _mapping(freeze.get("pinned_files"), "freeze pinned_files")
    _expect(
        all(
            _historical_code_pin_is_current_or_reviewed(
                freeze=freeze,
                path=str(path),
                historical_sha256=sha,
                current_sha256=freeze_pins.get(path),
            )
            for path, sha in code_pins.items()
        ),
        "promotion code or runtime pin drifted",
    )
    candidate_contract = _mapping(
        identity.get("candidate_and_sort_contract"),
        "promotion candidate and sort contract",
    )
    _expect(
        candidate_contract.get("active_golden_vector_status")
        == "ACTIVE_VERIFIED",
        "active promotion golden vector is not verified",
    )
    golden = _mapping(identity.get("golden_vector"), "promotion golden vector")
    _expect(
        golden.get("schema_version")
        == "dc20_promotion_active_golden_vector_binding_v1"
        and golden.get("status") == "ACTIVE_VERIFIED",
        "promotion golden-vector binding is not active",
    )
    _expect(
        golden.get("fixture_path")
        == "tests/fixtures/decision_promotion_active_golden_vector_v1.json"
        and golden.get("signal_date") == "20260812",
        "promotion golden-vector fixture identity drifted",
    )
    for field in (
        "fixture_sha256",
        "candidate_source_sha256",
        "feature_generation_source_sha256",
        "model_artifact_sha256",
        "authoritative_hard_pool_sha256",
        "feature_snapshot_sha256",
        "golden_output_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(golden.get(field, ""))) is not None,
            f"promotion golden-vector {field} is not a SHA-256",
        )
    _expect(
        golden.get("candidate_source_path")
        == "outputs/auction_v3/predictions/pred_20260812.csv"
        and golden.get("feature_generation_source_path")
        == identity_ledger.get("path")
        and golden.get("feature_generation_source_sha256")
        == identity_ledger.get("sha256"),
        "promotion golden-vector source identity drifted",
    )
    _expect(
        golden.get("strict_prior_rule")
        == "eight promotion priors recomputed from atomic promotion_hit truth with signal_date strictly before 20260812",
        "promotion golden-vector strict prior rule drifted",
    )
    _expect(
        golden.get("model_artifact_path") == contract_model.get("artifact_path")
        and golden.get("model_artifact_sha256")
        == contract_model.get("artifact_sha256")
        and golden.get("model_version") == contract_model.get("model_version"),
        "promotion golden-vector model identity drifted",
    )
    _expect(
        golden.get("pool_size") == 13
        and golden.get("stage_counts") == {"2→3": 7, "3→4": 6}
        and golden.get("top_n") == promotion.get("top_n"),
        "promotion golden-vector hard-pool counts drifted",
    )
    _expect(
        golden.get("contains_outcomes_or_future_labels") is False
        and golden.get("contains_cross_head_outputs") is False
        and golden.get("runtime_dependency_on_top10_decision") is False
        and golden.get("runtime_dependency_on_recovery_snapshot") is False,
        "promotion golden vector crossed its information or independence boundary",
    )

    timing = _mapping(contract.get("information_timing"), "information_timing")
    calendar = _mapping(timing.get("calendar"), "contract calendar")
    source = _mapping(ledger_manifest.get("source"), "ledger source")
    ledger_calendar = _mapping(source.get("calendar"), "ledger calendar")
    _expect(
        source.get("date_binding_rule") == calendar.get("date_binding_rule"),
        "strict D/T/T+1 binding drifted",
    )
    _expect(
        ledger_calendar.get("strict") is True
        and ledger_calendar.get("exchange") == calendar.get("exchange")
        and ledger_calendar.get("path") == calendar.get("path")
        and ledger_calendar.get("sha256") == calendar.get("sha256")
        and ledger_calendar.get("source") == calendar.get("source"),
        "strict SSE calendar no longer matches",
    )
    t_timing = _mapping(timing.get("T"), "T timing")
    _expect(
        t_timing.get("decision_deadline") == "T 09:24:50 Asia/Shanghai"
        and t_timing.get("post_092450_data_role")
        == "outcome truth only; never a ranking feature"
        and t_timing.get("actual_order_fill_observed") is False
        and t_timing.get("actual_execution_claimed") is False,
        "T shadow order timing or truth boundary drifted",
    )

    outcome = _mapping(
        contract.get("outcome_and_cost_contract"), "outcome_and_cost_contract"
    )
    target = _mapping(ledger_manifest.get("target_contract"), "ledger target")
    _expect(
        target.get("return_window") == "T open proxy to T+1 open",
        "training proxy window drifted",
    )
    _expect(
        outcome.get("cost_contract_version") == "dc20_shadow_cost_v1_45bp",
        "shadow cost version drifted",
    )
    _expect(
        target.get("round_trip_cost_rate") == outcome.get("round_trip_cost_rate")
        and outcome.get("round_trip_cost_bps")
        == outcome.get("round_trip_cost_rate") * 10000,
        "round-trip cost contract drifted",
    )
    _expect(
        target.get("big_loss_threshold") == outcome.get("big_loss_threshold"),
        "big-loss threshold drifted",
    )
    _expect(
        target.get("nonfill_return_targets") == "null"
        and outcome.get("nonfill_conditional_return") is None
        and outcome.get("nonfill_strategy_slot_return") == 0.0,
        "nonfill must be null conditionally and zero only in strategy accounting",
    )

    ranking = _mapping(contract.get("ranking_contract"), "ranking_contract")
    _expect(ranking.get("shadow_slots") == 2, "shadow selection must be Top2")
    _expect(
        ranking.get("candidate_scope") == "exact frozen promotion Top10 only",
        "executable-profit ranking escaped the frozen Top10",
    )
    _expect(
        ranking.get("always_record_top2_when_two_valid_scores_exist") is True
        and ranking.get("eligibility_is_separate_from_rank") is True
        and ranking.get("outcome_known_replacement_allowed") is False
        and ranking.get("may_create_formal_buy_action") is False,
        "shadow Top2 selection or eligibility contract drifted",
    )

    separation = _mapping(contract.get("ledger_separation"), "ledger_separation")
    _expect(
        separation.get("existing_p_fill_shadow_top2_ledger")
        == "SEPARATE_DIAGNOSTIC_NOT_EXECUTABLE_PROFIT_LEDGER",
        "P_fill and executable-profit ledgers must remain separate",
    )
    for field in (
        "historical_and_forward_statistics_may_be_merged",
        "p_fill_and_executable_profit_statistics_may_be_merged",
        "final_model_historical_rescoring_allowed",
        "post_outcome_reranking_allowed",
        "filter_to_filled_before_rank_evaluation_allowed",
    ):
        _expect(separation.get(field) is False, f"unsafe ledger rule: {field}")

    statistics = _mapping(
        contract.get("cumulative_statistics"), "cumulative_statistics"
    )
    _expect(
        statistics.get("confidence_resampling_unit") == "signal_date_not_stock_row",
        "Top2 rows from the same D date cannot be treated as independent samples",
    )
    _expect(
        statistics.get("required_same_date_baselines")
        == [
            "frozen_promotion_rank_top2",
            "frozen_promotion_top10_equal_weight",
        ],
        "same-date value baselines drifted",
    )

    return {
        "valid": True,
        "contract_id": contract.get("contract_id"),
        "status": contract.get("status"),
        "promotion_freeze_id": promotion.get("freeze_id"),
        "promotion_artifact_sha256": contract_model.get("artifact_sha256"),
        "promotion_contract_sha256": identity_sha256,
        "promotion_golden_fixture_sha256": golden.get("fixture_sha256"),
        "promotion_golden_hard_pool_sha256": golden.get(
            "authoritative_hard_pool_sha256"
        ),
        "promotion_golden_output_sha256": golden.get("golden_output_sha256"),
        "shadow_slots": ranking.get("shadow_slots"),
        "official_trade_action_allowed": False,
    }


def validate_contract(repo_root: Path, contract_path: Path = DEFAULT_CONTRACT_PATH) -> dict[str, Any]:
    root = repo_root.resolve()
    contract = _load_json(root / contract_path)
    freeze = _load_json(root / "models/decision_model_freeze.json")
    _validate_reviewed_rotation_evidence(root=root, freeze=freeze)
    result = validate_payloads(
        contract=contract,
        freeze=freeze,
        validation=_load_json(
            root / "models/decision_three_engines/validation_latest.json"
        ),
        ledger_manifest=_load_json(
            root / "data/decision_three_engines/five_year_ledger_manifest.json"
        ),
    )
    try:
        try:
            from scripts.validate_frozen_promotion_golden_vector import (
                FrozenPromotionGoldenVectorError,
                validate_frozen_promotion_golden_vector,
            )
        except ModuleNotFoundError:
            from validate_frozen_promotion_golden_vector import (  # type: ignore
                FrozenPromotionGoldenVectorError,
                validate_frozen_promotion_golden_vector,
            )
        identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
        golden = _mapping(identity.get("golden_vector"), "promotion golden vector")
        golden_result = validate_frozen_promotion_golden_vector(
            root,
            fixture_path=Path(str(golden.get("fixture_path") or "")),
            binding=golden,
        )
    except FrozenPromotionGoldenVectorError as exc:
        raise ExecutableProfitShadowContractError(
            f"active promotion golden-vector validation failed: {exc}"
        ) from exc
    result["promotion_golden_vector"] = golden_result
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the frozen DC20 executable-profit Shadow Top2 design."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    args = parser.parse_args()
    try:
        result = validate_contract(args.repo_root, args.contract)
    except ExecutableProfitShadowContractError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
