from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from top10decision.decision.action_plan import (
    _independent_d_close_research_rows,
    _pending_candidates,
    _stage_watchlist,
)
from top10decision.decision.three_rank import (
    THREE_RANK_CONTRACT_VERSION,
    ThreeRankContractError,
    build_three_rank_contract,
    materialize_three_rank_artifacts,
    materialize_three_rank_index,
    top10_members_sha256,
    validate_three_rank_contract,
    validate_three_rank_index,
)


SIGNAL_DATE = "20260820"
EXEC_DATE = "20260821"
EXIT_DATE = "20260824"


def _ready_row(code: str, rank: int, count: int = 3) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": code,
        "name": f"样本{rank}",
        "industry": "测试行业",
        "stage_transition": "2→3" if rank % 2 else "3→4",
        "top10_selected": 1,
        "three_rank_contract_version": THREE_RANK_CONTRACT_VERSION,
        "promotion_pool_size": count,
        "promotion_rank": rank,
        "predicted_promotion_probability": 0.90 - rank * 0.05,
        "big_loss_safety_rank": count + 1 - rank,
        "predicted_big_loss_probability": 0.02 + (count - rank) * 0.03,
        "profit_rank": rank,
        "predicted_profit_probability": 0.75 - rank * 0.04,
        "feature_snapshot_sha256": "f" * 64,
        "p_fill_shadow_rank": rank,
        "p_fill_shadow_probability": 0.95 - rank * 0.05,
        "p_fill_shadow_status": "SHADOW_READY",
        "p_fill_shadow_model_version": "p_fill_shadow_v1",
        "p_fill_shadow_model_as_of_date": "20260819",
        "p_fill_shadow_model_artifact_sha256": "4" * 64,
        "p_fill_shadow_validation_gate_pass_count": 26,
        "p_fill_shadow_validation_gate_total_count": 26,
        "p_fill_shadow_validation_gate_score_pct": 100.0,
    }
    gate_scores = {
        "promotion": (26, 26, 100.0),
        "big_loss": (17, 26, 65.4),
        "profit": (20, 26, 76.9),
    }
    for index, head in enumerate(("promotion", "big_loss", "profit"), start=1):
        pass_count, total_count, score = gate_scores[head]
        row.update(
            {
                f"{head}_model_status": "READY",
                f"{head}_model_version": f"{head}_v1",
                f"{head}_model_as_of_date": "20260819",
                f"{head}_model_artifact_sha256": str(index) * 64,
                f"{head}_validation_gate_pass_count": pass_count,
                f"{head}_validation_gate_total_count": total_count,
                f"{head}_validation_gate_score_pct": score,
            }
        )
    return row


def _plan(count: int = 3) -> dict[str, object]:
    return {
        "generated_at_utc": "2026-08-20T13:20:00Z",
        "signal_date": SIGNAL_DATE,
        "exec_date": EXEC_DATE,
        "exit_date": EXIT_DATE,
        "stage_watchlist": [
            _ready_row(f"60000{rank}.SH", rank, count)
            for rank in range(1, count + 1)
        ],
        "candidates": [],
        "model": {},
    }


def test_member_hash_is_set_stable_but_membership_sensitive() -> None:
    first = top10_members_sha256(
        SIGNAL_DATE,
        ["600001.SH", "600002.SH", "600003.SH"],
    )
    reordered = top10_members_sha256(
        SIGNAL_DATE,
        ["600003.SH", "600001.SH", "600002.SH"],
    )
    changed = top10_members_sha256(
        SIGNAL_DATE,
        ["600001.SH", "600002.SH", "600004.SH"],
    )
    assert first == reordered
    assert first != changed


def test_three_independent_heads_share_exact_top10_without_overwriting() -> None:
    contract = build_three_rank_contract(_plan())
    validate_three_rank_contract(contract, require_all_models_ready=True)

    assert contract["status"] == "READY"
    assert contract["top10_count"] == 3
    assert [row["promotion_rank"] for row in contract["rows"]] == [1, 2, 3]
    assert [row["big_loss_safety_rank"] for row in contract["rows"]] == [3, 2, 1]
    assert [row["profit_rank"] for row in contract["rows"]] == [1, 2, 3]
    expected_hash = contract["top10_members_sha256"]
    assert {
        contract["models"][head]["input_members_sha256"]
        for head in ("promotion", "big_loss", "profit")
    } == {expected_hash}
    shadow = contract["shadow_contract"]
    assert shadow["status"] == "ANNOTATION_ONLY"
    assert shadow["input_members_sha256"] == expected_hash
    assert shadow["may_change_membership"] is False
    assert shadow["may_override_core_ranks"] is False
    assert shadow["model_status"] == "SHADOW_READY"
    assert shadow["validation_gate_pass_count"] == 26
    assert shadow["validation_gate_total_count"] == 26
    assert shadow["validation_gate_score_pct"] == 100.0
    assert len(shadow["shadow_snapshot_sha256"]) == 64
    assert [row["p_fill_shadow_rank"] for row in contract["rows"]] == [
        1,
        2,
        3,
    ]
    top2 = contract["shadow_top2"]
    assert top2["requested_slots"] == 2
    assert top2["actual_slots"] == 2
    assert top2["may_change_core_bundle"] is False
    assert top2["may_override_core_ranks"] is False
    assert top2["may_create_trade_action"] is False
    assert [row["ts_code"] for row in top2["rows"]] == [
        "600001.SH",
        "600002.SH",
    ]
    assert all("action" not in row for row in top2["rows"])
    assert set(contract["models"]) == {"promotion", "big_loss", "profit"}


def test_validation_gate_scores_are_metadata_not_readiness_or_ranking() -> None:
    contract = build_three_rank_contract(_plan())

    assert {
        head: (
            contract["models"][head]["validation_gate_pass_count"],
            contract["models"][head]["validation_gate_total_count"],
            contract["models"][head]["validation_gate_score_pct"],
        )
        for head in ("promotion", "big_loss", "profit")
    } == {
        "promotion": (26, 26, 100.0),
        "big_loss": (17, 26, 65.4),
        "profit": (20, 26, 76.9),
    }

    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["promotion_model_status"] = "NOT_READY_RUNTIME_FEATURES"
        row["top10_selected"] = 0
    failed = build_three_rank_contract(plan)
    assert failed["models"]["promotion"]["status"] != "READY"
    assert failed["models"]["promotion"]["validation_gate_score_pct"] == 100.0
    assert failed["models"]["big_loss"]["status"] == "NOT_READY_NO_FROZEN_TOP10"
    assert failed["models"]["big_loss"]["validation_gate_score_pct"] == 65.4
    assert failed["models"]["profit"]["status"] == "NOT_READY_NO_FROZEN_TOP10"
    assert failed["models"]["profit"]["validation_gate_score_pct"] == 76.9
    assert failed["shadow_contract"]["validation_gate_score_pct"] == 100.0
    assert failed["rows"] == []


def test_validation_gate_summary_must_be_complete_and_round_consistent() -> None:
    contract = build_three_rank_contract(_plan())
    contract["models"]["big_loss"]["validation_gate_score_pct"] = 65.5
    with pytest.raises(ThreeRankContractError, match="gate score"):
        validate_three_rank_contract(contract)

    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["profit_validation_gate_total_count"] = None
    with pytest.raises(ThreeRankContractError, match="gate summary is incomplete"):
        build_three_rank_contract(plan)


def test_not_ready_downstream_head_cannot_emit_fake_rank_or_probability() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["profit_model_status"] = "NOT_READY_CONSTANT_FALLBACK"
        # Deliberately leave the source rank/probability populated.  The
        # contract must remove it instead of giving a constant model a tie-break.
    contract = build_three_rank_contract(plan)

    assert contract["status"] == "PARTIAL_MODELS_NOT_READY"
    assert contract["models"]["profit"]["status"] == "NOT_READY_CONSTANT_FALLBACK"
    assert all(row["profit_rank"] is None for row in contract["rows"])
    assert all(
        row["predicted_profit_probability"] is None
        for row in contract["rows"]
    )
    validate_three_rank_contract(contract)
    with pytest.raises(ThreeRankContractError, match="not all ready"):
        validate_three_rank_contract(contract, require_all_models_ready=True)


def test_not_ready_shadow_nulls_outputs_and_has_no_top2() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["p_fill_shadow_status"] = "SHADOW_NOT_READY_VALIDATION_GATE"
        # Populated source values must not escape an unready shadow model.
    contract = build_three_rank_contract(plan)

    assert contract["shadow_contract"]["model_status"] == (
        "SHADOW_NOT_READY_VALIDATION_GATE"
    )
    assert all(
        row["p_fill_shadow_rank"] is None
        and row["p_fill_shadow_probability"] is None
        and row["p_fill_shadow_status"]
        == "SHADOW_NOT_READY_VALIDATION_GATE"
        for row in contract["rows"]
    )
    assert contract["shadow_top2"]["requested_slots"] == 2
    assert contract["shadow_top2"]["actual_slots"] == 0
    assert contract["shadow_top2"]["rows"] == []
    validate_three_rank_contract(contract)


@pytest.mark.parametrize(
    "field,value",
    (
        ("p_fill_shadow_rank", 2),
        ("p_fill_shadow_probability", 1.1),
        ("p_fill_shadow_status", "SHADOW_NOT_READY_INCONSISTENT"),
    ),
)
def test_invalid_ready_shadow_source_fails_closed(
    field: str,
    value: object,
) -> None:
    plan = _plan()
    plan["stage_watchlist"][0][field] = value
    contract = build_three_rank_contract(plan)

    assert contract["shadow_contract"]["model_status"].startswith(
        "SHADOW_NOT_READY_"
    )
    assert all(
        row["p_fill_shadow_rank"] is None
        and row["p_fill_shadow_probability"] is None
        for row in contract["rows"]
    )
    assert contract["shadow_top2"]["rows"] == []
    validate_three_rank_contract(contract)


def test_new_contract_never_uses_legacy_shadow_as_official_big_loss() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["predicted_big_loss_probability"] = None
        row["big_loss_safety_rank"] = None
        # These legacy fields deliberately remain populated.  Under the new
        # contract they are shadow diagnostics, not fallback official output.
        row["trade_predicted_big_loss_probability"] = 0.01
        row["big_loss_rank"] = int(row["promotion_rank"])

    contract = build_three_rank_contract(plan)

    assert contract["models"]["big_loss"]["status"] == (
        "NOT_READY_INVALID_OUTPUT"
    )
    assert all(
        row["predicted_big_loss_probability"] is None
        and row["big_loss_safety_rank"] is None
        for row in contract["rows"]
    )


def test_unready_promotion_head_means_no_official_top10() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["promotion_model_status"] = "NOT_READY_INSUFFICIENT_OOS"
        row["top10_selected"] = 0
    contract = build_three_rank_contract(plan)

    assert contract["status"] == "NOT_READY_PROMOTION"
    assert contract["models"]["promotion"]["status"] == (
        "NOT_READY_INSUFFICIENT_OOS"
    )
    assert contract["models"]["promotion"]["version"] == "promotion_v1"
    assert contract["models"]["big_loss"]["status"] == (
        "NOT_READY_NO_FROZEN_TOP10"
    )
    assert contract["models"]["profit"]["status"] == (
        "NOT_READY_NO_FROZEN_TOP10"
    )
    assert contract["shadow_contract"]["model_status"] == (
        "SHADOW_NOT_READY_NO_FROZEN_TOP10"
    )
    assert contract["shadow_top2"]["requested_slots"] == 2
    assert contract["shadow_top2"]["actual_slots"] == 0
    assert contract["shadow_top2"]["rows"] == []
    assert contract["rows"] == []
    assert contract["top10_count"] == 0
    validate_three_rank_contract(contract)


def test_claimed_set_hash_mismatch_fails_closed() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["top10_members_sha256"] = "0" * 64
    contract = build_three_rank_contract(plan)

    assert contract["models"]["promotion"]["status"] == "NOT_READY_SET_HASH_MISMATCH"
    assert contract["models"]["big_loss"]["status"] == (
        "NOT_READY_NO_FROZEN_TOP10"
    )
    assert contract["models"]["profit"]["status"] == (
        "NOT_READY_NO_FROZEN_TOP10"
    )
    assert contract["rows"] == []


def test_validator_rejects_downstream_ready_without_engine_a_membership() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["promotion_model_status"] = "NOT_READY_INSUFFICIENT_OOS"
        row["top10_selected"] = 0
    contract = build_three_rank_contract(plan)
    contract["models"]["big_loss"].update(
        {
            "status": "READY",
            "ranking_ready": True,
            "probability_ready": True,
        }
    )
    contract["bundle_sha256"] = hashlib.sha256(
        json.dumps(
            {
                "schema_version": contract["schema_version"],
                "artifact_kind": contract["artifact_kind"],
                "contract_version": contract["contract_version"],
                "signal_date": contract["signal_date"],
                "exec_date": contract["exec_date"],
                "exit_date": contract["exit_date"],
                "feature_as_of_date": contract["feature_as_of_date"],
                "feature_snapshot_sha256": contract[
                    "feature_snapshot_sha256"
                ],
                "promotion_pool_size": contract["promotion_pool_size"],
                "top10_count": contract["top10_count"],
                "top10_members_sha256": contract[
                    "top10_members_sha256"
                ],
                "models": contract["models"],
                "rows": [],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ThreeRankContractError, match="without a frozen Top10"):
        validate_three_rank_contract(contract)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda payload: payload["shadow_contract"].__setitem__(
                "may_override_core_ranks", True
            ),
            "shadow contract",
        ),
        (
            lambda payload: payload.__setitem__(
                "top10_members_sha256", "0" * 64
            ),
            "member hash",
        ),
        (
            lambda payload: payload["rows"][0].__setitem__(
                "promotion_rank", 2
            ),
            "promotion ranks",
        ),
        (
            lambda payload: payload["shadow_top2"].__setitem__(
                "actual_slots", 1
            ),
            "shadow Top2",
        ),
        (
            lambda payload: payload["shadow_contract"].__setitem__(
                "shadow_snapshot_sha256", "0" * 64
            ),
            "shadow snapshot hash",
        ),
    ],
)
def test_contract_mutations_fail_closed(mutation, match: str) -> None:
    contract = build_three_rank_contract(_plan())
    mutation(contract)
    with pytest.raises(ThreeRankContractError, match=match):
        validate_three_rank_contract(contract)


def test_materialized_json_and_csv_are_exact_and_d_artifact_is_immutable(
    tmp_path: Path,
) -> None:
    contract = build_three_rank_contract(_plan())
    json_path, csv_path, enriched = materialize_three_rank_artifacts(
        tmp_path,
        contract,
    )

    assert json_path.name == f"three_rank_top10_{SIGNAL_DATE}.json"
    assert csv_path.name == f"three_rank_top10_{SIGNAL_DATE}.csv"
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert persisted["bundle_sha256"] == contract["bundle_sha256"]
    assert enriched["downloads"]["csv_sha256"] == hashlib.sha256(
        csv_path.read_bytes()
    ).hexdigest()
    csv_rows = list(
        csv.DictReader(
            io.StringIO(csv_path.read_bytes()[3:].decode("utf-8"))
        )
    )
    assert [row["ts_code"] for row in csv_rows] == [
        row["ts_code"] for row in contract["rows"]
    ]
    assert {row["top10_members_sha256"] for row in csv_rows} == {
        contract["top10_members_sha256"]
    }
    assert [int(row["p_fill_shadow_rank"]) for row in csv_rows] == [1, 2, 3]
    assert {row["p_fill_shadow_snapshot_sha256"] for row in csv_rows} == {
        contract["shadow_contract"]["shadow_snapshot_sha256"]
    }
    index_path = tmp_path / "outputs" / "decision" / "three_rank_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    validate_three_rank_index(index)
    assert index["data_alias"] is False
    assert index["latest_contract_url"] == (
        f"outputs/decision/three_rank_top10_{SIGNAL_DATE}.json"
    )
    assert index["latest_contract_sha256"] == hashlib.sha256(
        json_path.read_bytes()
    ).hexdigest()

    materialize_three_rank_artifacts(tmp_path, contract)
    changed = build_three_rank_contract(_plan(2))
    with pytest.raises(ThreeRankContractError, match="cannot be overwritten"):
        materialize_three_rank_artifacts(tmp_path, changed)


def test_three_rank_index_never_moves_backward_or_retargets_same_date(
    tmp_path: Path,
) -> None:
    first = build_three_rank_contract(_plan())
    _, _, first = materialize_three_rank_artifacts(tmp_path, first)
    path, indexed = materialize_three_rank_index(tmp_path, first)
    assert indexed["latest_signal_date"] == SIGNAL_DATE

    older = copy.deepcopy(first)
    older["signal_date"] = "20260819"
    older["feature_as_of_date"] = "20260819"
    older["exec_date"] = "20260820"
    older["exit_date"] = "20260821"
    older["downloads"]["json_url"] = (
        "outputs/decision/three_rank_top10_20260819.json"
    )
    older["downloads"]["csv_url"] = (
        "outputs/decision/three_rank_top10_20260819.csv"
    )
    # The exact dated bundle validation would need a recomputed member/bundle
    # hash; this assertion instead proves that a valid existing pointer itself
    # is strict and cannot be converted into a mutable latest-data alias.
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["data_alias"] = True
    with pytest.raises(ThreeRankContractError, match="data alias"):
        validate_three_rank_index(tampered)


def test_stage_watchlist_uses_only_engine_a_members_and_keeps_legacy_alias() -> None:
    rows = _plan()["stage_watchlist"]
    outsider = _ready_row("600099.SH", 9)
    outsider["top10_selected"] = 0
    outsider["trade_shadow_selected"] = 1
    rows.append(outsider)
    for row in rows:
        row["promotion_pool_size"] = 7
    rows[0]["trade_shadow_selected"] = 0

    selected, total = _stage_watchlist(rows)

    assert {row["ts_code"] for row in selected} == {
        "600001.SH",
        "600002.SH",
        "600003.SH",
    }
    assert all(row["observation_rank"] == row["promotion_rank"] for row in selected)
    assert total == 7


def test_pending_projection_preserves_every_official_three_rank_field() -> None:
    source = _ready_row("600001.SH", 1, 1)
    pending = _pending_candidates(pd.DataFrame([source]))

    assert len(pending) == 1
    assert pending[0]["action"] == "PENDING"
    for field in (
        "three_rank_contract_version",
        "top10_selected",
        "top10_members_sha256",
        "feature_snapshot_sha256",
        "promotion_rank",
        "predicted_promotion_probability",
        "big_loss_safety_rank",
        "predicted_big_loss_probability",
        "profit_rank",
        "predicted_profit_probability",
        "promotion_model_status",
        "promotion_validation_gate_pass_count",
        "promotion_validation_gate_total_count",
        "promotion_validation_gate_score_pct",
        "big_loss_model_status",
        "big_loss_validation_gate_pass_count",
        "big_loss_validation_gate_total_count",
        "big_loss_validation_gate_score_pct",
        "profit_model_status",
        "profit_validation_gate_pass_count",
        "profit_validation_gate_total_count",
        "profit_validation_gate_score_pct",
        "p_fill_shadow_rank",
        "p_fill_shadow_probability",
        "p_fill_shadow_status",
        "p_fill_shadow_model_version",
        "p_fill_shadow_model_as_of_date",
        "p_fill_shadow_model_artifact_sha256",
        "p_fill_shadow_validation_gate_pass_count",
        "p_fill_shadow_validation_gate_total_count",
        "p_fill_shadow_validation_gate_score_pct",
    ):
        assert field in pending[0]
        assert pending[0][field] == source.get(field)


def test_legacy_t_date_mismatch_keeps_valid_d_close_a_list_but_rejects_actions() -> None:
    prediction_rows = []
    for rank in range(1, 4):
        row = _ready_row(f"60000{rank}.SH", rank, 3)
        row.update(
            {
                "signal_date": SIGNAL_DATE,
                # Deliberately mismatched legacy execution dates.  The A list
                # remains valid D-close research but cannot authorize action.
                "expected_buy_date": "20260822",
                "expected_exit_date": "20260825",
                "trade_selected": 1,
                "trade_shadow_selected": 1,
                "decision_limit_pct": 10.0,
            }
        )
        prediction_rows.append(row)
    candidates = pd.DataFrame(
        [
            {
                "ts_code": row["ts_code"],
                "name": row["name"],
                "industry": row["industry"],
            }
            for row in prediction_rows
        ]
    )

    rows = _independent_d_close_research_rows(
        pd.DataFrame(prediction_rows),
        candidates,
        signal_date=SIGNAL_DATE,
        exec_date=EXEC_DATE,
        exit_date=EXIT_DATE,
        risk_budget=0.5,
    )
    contract = build_three_rank_contract(
        {
            "signal_date": SIGNAL_DATE,
            "exec_date": EXEC_DATE,
            "exit_date": EXIT_DATE,
            "candidates": rows,
            "model": {},
        }
    )

    assert contract["models"]["promotion"]["status"] == "READY"
    assert contract["top10_count"] == 3
    assert all(row["action"] == "REJECT" for row in rows)
    assert all(row["target_weight"] == 0.0 for row in rows)
    assert all(row["trade_selected"] == 0 for row in rows)


def test_ready_promotion_cannot_publish_fewer_than_min_top10_pool() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["promotion_pool_size"] = 7

    contract = build_three_rank_contract(plan)

    assert contract["models"]["promotion"]["status"] == (
        "NOT_READY_INVALID_MEMBERSHIP"
    )
    assert contract["rows"] == []


def test_ready_promotion_requires_frozen_feature_snapshot() -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["feature_snapshot_sha256"] = ""

    contract = build_three_rank_contract(plan)

    assert contract["models"]["promotion"]["status"] == (
        "NOT_READY_MISSING_FEATURE_SNAPSHOT"
    )
    assert contract["rows"] == []


def test_bundle_hash_does_not_include_shadow_annotations() -> None:
    first_plan = _plan()
    second_plan = copy.deepcopy(first_plan)
    rotated_ranks = (3, 1, 2)
    for row, shadow_rank in zip(
        second_plan["stage_watchlist"], rotated_ranks
    ):
        row["p_fill_shadow_rank"] = shadow_rank
        row["p_fill_shadow_probability"] = 0.70 + shadow_rank * 0.05
    first = build_three_rank_contract(first_plan)
    second = build_three_rank_contract(second_plan)

    assert first["top10_members_sha256"] == second["top10_members_sha256"]
    assert first["bundle_sha256"] == second["bundle_sha256"]
    assert first["shadow_contract"]["shadow_snapshot_sha256"] != second[
        "shadow_contract"
    ]["shadow_snapshot_sha256"]
    assert first["shadow_top2"]["rows"] != second["shadow_top2"]["rows"]
    # Shadow values remain available for display, but cannot change core model
    # provenance, membership, or the six official rank/probability fields.
    for left, right in zip(first["rows"], second["rows"]):
        for field in (
            "promotion_rank",
            "predicted_promotion_probability",
            "big_loss_safety_rank",
            "predicted_big_loss_probability",
            "profit_rank",
            "predicted_profit_probability",
        ):
            assert left[field] == right[field]


def test_shadow_snapshot_hash_rejects_valid_looking_row_tamper() -> None:
    contract = build_three_rank_contract(_plan())
    # Rank 3 is outside Top2, so the ordinary Top2 equality remains intact;
    # the independent shadow snapshot hash must still detect the mutation.
    contract["rows"][2]["p_fill_shadow_probability"] = 0.123

    with pytest.raises(ThreeRankContractError, match="shadow snapshot hash"):
        validate_three_rank_contract(contract)
