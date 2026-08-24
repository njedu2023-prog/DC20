from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from top10decision.decision import executable_profit_research_projection as public
from top10decision.decision import executable_profit_shadow as shadow
from top10decision.decision import executable_profit_shadow_settlement as truth
from top10decision.decision.three_rank import (
    THREE_RANK_CONTRACT_VERSION,
    build_three_rank_contract,
    materialize_three_rank_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_VALIDATE_STATISTICS = truth.validate_statistics


@pytest.fixture(autouse=True)
def _isolate_selection_scoring_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # Scorer validation is exhaustive in its own suite. These tests prove that
    # the public layer only projects immutable bytes and exact source identity.
    monkeypatch.setattr(
        shadow,
        "validate_internal_forward_shadow_payload",
        lambda payload, require_downloads=False: None,
    )
    monkeypatch.setattr(truth, "validate_t_verification", lambda payload: None)
    monkeypatch.setattr(truth, "validate_t1_settlement", lambda payload: None)
    monkeypatch.setattr(truth, "validate_statistics", lambda payload: None)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _ready_row(
    code: str,
    rank: int,
    count: int,
    *,
    model_as_of: str,
) -> dict[str, object]:
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
        "big_loss_safety_rank": None,
        "predicted_big_loss_probability": None,
        "profit_rank": None,
        "predicted_profit_probability": None,
        "feature_snapshot_sha256": "f" * 64,
        "p_fill_shadow_probability": 0.5,
        "p_fill_shadow_status": "SHADOW_READY",
        "p_fill_shadow_model_version": "old_annotation_is_not_projection_input",
        "p_fill_shadow_model_as_of_date": model_as_of,
        "p_fill_shadow_model_artifact_sha256": "4" * 64,
        "p_fill_shadow_validation_gate_pass_count": 26,
        "p_fill_shadow_validation_gate_total_count": 26,
        "p_fill_shadow_validation_gate_score_pct": 100.0,
    }
    head_status = {
        "promotion": "READY",
        "big_loss": "NOT_READY_VALIDATION_GATE",
        "profit": "NOT_READY_VALIDATION_GATE",
    }
    gates = {
        "promotion": (26, 26, 100.0),
        "big_loss": (17, 26, 65.4),
        "profit": (20, 26, 76.9),
    }
    for index, head in enumerate(("promotion", "big_loss", "profit"), start=1):
        passed, total, score = gates[head]
        row.update(
            {
                f"{head}_model_status": head_status[head],
                f"{head}_model_version": f"{head}_v1",
                f"{head}_model_as_of_date": model_as_of,
                f"{head}_model_artifact_sha256": str(index) * 64,
                f"{head}_validation_gate_pass_count": passed,
                f"{head}_validation_gate_total_count": total,
                f"{head}_validation_gate_score_pct": score,
            }
        )
    return row


def _dates(signal_date: str) -> tuple[str, str, str]:
    cases = {
        "20260824": ("20260825", "20260826", "20260823"),
        "20260825": ("20260826", "20260827", "20260824"),
    }
    return cases[signal_date]


def _prepare_repo(
    tmp_path: Path,
    *,
    signal_date: str = "20260824",
    count: int = 3,
) -> tuple[Path, dict, dict]:
    repo = tmp_path
    contract_target = repo / public.CONTRACT_PATH
    contract_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / public.CONTRACT_PATH, contract_target)
    calendar_target = repo / truth.CALENDAR_PATH
    calendar_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / truth.CALENDAR_PATH, calendar_target)
    exec_date, exit_date, model_as_of = _dates(signal_date)
    codes = [f"60000{index}.SH" for index in range(1, count + 1)]
    plan = {
        "generated_at_utc": f"{signal_date[:4]}-{signal_date[4:6]}-{signal_date[6:]}T08:00:00Z",
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "stage_watchlist": [
            _ready_row(code, rank, count, model_as_of=model_as_of)
            for rank, code in enumerate(codes, start=1)
        ],
        "candidates": [],
        "model": (
            {
                "three_rank_models": {
                    "promotion": {
                        "status": "READY",
                        "version": "promotion_v1",
                        "as_of_date": model_as_of,
                        "artifact_sha256": "1" * 64,
                        "validation_gate_pass_count": 26,
                        "validation_gate_total_count": 26,
                        "validation_gate_score_pct": 100.0,
                    },
                    "big_loss": {"status": "NOT_READY_VALIDATION_GATE"},
                    "profit": {"status": "NOT_READY_VALIDATION_GATE"},
                }
            }
            if count == 0
            else {}
        ),
    }
    three_rank = build_three_rank_contract(plan)
    _, _, three_rank = materialize_three_rank_artifacts(repo, three_rank)
    three_rows = list(three_rank["rows"])
    research_order = list(reversed(three_rows))
    selection_rows = []
    for research_rank, source in enumerate(research_order, start=1):
        fill = 0.60 + research_rank * 0.05
        conditional = 0.80 - research_rank * 0.05
        selection_rows.append(
            {
                "ts_code": source["ts_code"],
                "name": source["name"],
                "industry": source["industry"],
                "stage_transition": source["stage_transition"],
                "promotion_rank": source["promotion_rank"],
                "predicted_promotion_probability": source[
                    "predicted_promotion_probability"
                ],
                "research_fill_proxy_score": fill,
                "research_conditional_profit_score": conditional,
                "research_joint_proxy_score": fill * conditional,
                "internal_shadow_order": research_rank,
                "internal_shadow_selected": int(research_rank <= min(2, count)),
                "shadow_slot": research_rank if research_rank <= min(2, count) else None,
                "shadow_max_price": 10.0 + research_rank / 10.0,
                "shadow_price_basis": "D_FROZEN_RECOMMENDED_MAX_PRICE",
                "shadow_price_source_sha256": "c" * 64,
            }
        )
    top2 = [
        {"shadow_slot": row["shadow_slot"], "ts_code": row["ts_code"]}
        for row in selection_rows[: min(2, count)]
    ]
    selection: dict = {
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "top10_count": count,
        "top10_members_sha256": three_rank["top10_members_sha256"],
        "source_promotion": {
            "source_bundle_sha256": three_rank["bundle_sha256"],
            "source_feature_snapshot_sha256": three_rank[
                "feature_snapshot_sha256"
            ],
            "source_top10_members_sha256": three_rank["top10_members_sha256"],
        },
        "source_d_feature": {"file_sha256": "c" * 64},
        "ranking_contract": {
            "shadow_slot_rule": "min(2, N); no padding",
        },
        "rows": selection_rows,
        "shadow_top2": {
            "requested_slots": 2,
            "actual_slots": min(2, count),
            "rows": top2,
        },
    }
    selection["snapshot_sha256"] = public._payload_snapshot(selection)
    csv_relative = public.SELECTION_ROOT / f"shadow_{signal_date}.csv"
    csv_path = repo / csv_relative
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "ts_code,internal_shadow_order\n"
        + "".join(
            f"{row['ts_code']},{row['internal_shadow_order']}\n"
            for row in selection_rows
        ),
        encoding="utf-8",
    )
    json_relative = public.SELECTION_ROOT / f"shadow_{signal_date}.json"
    selection["downloads"] = {
        "json_url": json_relative.as_posix(),
        "csv_url": csv_relative.as_posix(),
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "row_count": count,
    }
    _write_json(repo / json_relative, selection)
    return repo, three_rank, selection


def _write_minimal_truth_artifacts(
    repo: Path,
    projection: dict,
    *,
    actual_exit_date: str,
) -> tuple[dict, dict]:
    selected_members = [
        {"shadow_slot": row["shadow_slot"], "ts_code": row["ts_code"]}
        for row in projection["rows"]
        if row["shadow_selected"]
    ]
    selection_source = projection["source_bindings"]["selection"]
    selection_binding = {
        "path": selection_source["json_path"],
        "file_sha256": selection_source["json_sha256"],
        "snapshot_sha256": selection_source["snapshot_sha256"],
        "top10_members_sha256": projection["top10_members_sha256"],
        "selected_slots": len(selected_members),
        "selected_members": selected_members,
    }
    verification: dict = {
        "signal_date": projection["signal_date"],
        "exec_date": projection["exec_date"],
        "exit_date": projection["exit_date"],
        "selection": selection_binding,
        "rows": [
            {
                "shadow_slot": row["shadow_slot"],
                "ts_code": row["ts_code"],
                "validation_status": "T_VERIFIED_PROXY_FILLED",
                "proxy_fill": 1,
            }
            for row in projection["rows"]
            if row["shadow_selected"]
        ],
        "boundaries": {
            "official_trade_action_allowed": False,
            "selection_changed": False,
        },
    }
    verification["snapshot_sha256"] = public._payload_snapshot(verification)
    verification_path = (
        repo
        / public.VERIFICATION_ROOT
        / f"t_verification_{projection['signal_date']}.json"
    )
    _write_json(verification_path, verification)

    settlement: dict = {
        "signal_date": projection["signal_date"],
        "exec_date": projection["exec_date"],
        "exit_date": projection["exit_date"],
        "selection": selection_binding,
        "t_verification": {
            "path": verification_path.relative_to(repo).as_posix(),
            "file_sha256": hashlib.sha256(verification_path.read_bytes()).hexdigest(),
            "snapshot_sha256": verification["snapshot_sha256"],
        },
        "rows": [
            {
                "shadow_slot": row["shadow_slot"],
                "ts_code": row["ts_code"],
                "settlement_status": "FINAL_FIRST_TRADABLE_OPEN_PUBLIC_MARKET_PROXY",
                "actual_exit_date": actual_exit_date,
                "net_return_after_cost": 0.02 * int(row["shadow_slot"]),
                "strategy_slot_return": 0.02 * int(row["shadow_slot"]),
            }
            for row in projection["rows"]
            if row["shadow_selected"]
        ],
        "boundaries": {
            "official_trade_action_allowed": False,
            "selection_changed": False,
        },
    }
    settlement["snapshot_sha256"] = public._payload_snapshot(settlement)
    settlement_path = (
        repo
        / public.SETTLEMENT_ROOT
        / f"settlement_{projection['signal_date']}.json"
    )
    _write_json(settlement_path, settlement)
    return verification, settlement


def _write_minimal_statistics_summary(repo: Path) -> dict:
    summary = {
        "status": "INTERNAL_RESEARCH_SHADOW_ONLY",
        "as_of_date": "20260824",
        "scope": {"selection_dates": 1},
        "forward_signal_date_progress_180": {"observed_signal_dates": 1},
        "cohorts": {"shadow_slot_1": {"win_rate": None}},
        "probability_diagnostics": {"status": "UNCALIBRATED"},
        "excluded_ledgers": [],
        "pending_definitions": {},
        "input_files": [],
        "input_files_sha256": public._source_statistics_input_files_sha256([]),
        "boundaries": {
            "official_trade_action_allowed": False,
            "actual_execution_claimed": False,
        },
        "snapshot_sha256": "8" * 64,
    }
    _write_json(repo / public.SOURCE_STATISTICS_PATH, summary)
    return summary


def test_visible_projection_preserves_independent_orders_and_never_pads(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")

    assert projection["display_name"] == "可实现盈利研究排序（未校准代理分）"
    assert [row["executable_profit_research_rank"] for row in projection["rows"]] == [1, 2, 3]
    assert [row["promotion_rank"] for row in projection["rows"]] == [3, 2, 1]
    assert [row["shadow_slot"] for row in projection["rows"]] == [1, 2, None]
    assert projection["rows"][0]["shadow_max_price"] == 10.1
    assert projection["rows"][0]["shadow_price_basis"] == (
        "D_FROZEN_RECOMMENDED_MAX_PRICE"
    )
    assert projection["rows"][0]["shadow_price_source_sha256"] == "c" * 64
    assert projection["ranking_contract"]["shadow_actual_slots"] == 2
    assert "not a buy instruction" in projection["ranking_contract"]["shadow_price_use"]
    assert projection["boundaries"] == public.PUBLIC_BOUNDARIES
    assert projection["boundaries"]["formal_probability_allowed"] is False
    assert projection["boundaries"]["formal_rank_allowed"] is False


@pytest.mark.parametrize("count,slots", [(0, 0), (1, 1), (2, 2)])
def test_candidate_count_zero_to_two_is_exact_without_padding(
    tmp_path: Path,
    count: int,
    slots: int,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path, count=count)
    projection = public.build_research_projection(repo, "20260824")
    assert projection["candidate_count"] == count
    assert projection["ranking_contract"]["shadow_actual_slots"] == slots
    assert sum(row["shadow_selected"] for row in projection["rows"]) == slots


def test_projection_does_not_load_or_require_model_artifacts(tmp_path: Path) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    work = repo / "work/deleted-model"
    work.mkdir(parents=True)
    deleted = work / "challenger.pkl"
    deleted.write_bytes(b"must not be read")
    deleted.unlink()

    projection = public.build_research_projection(repo, "20260824")
    assert projection["candidate_count"] == 3
    source = Path(public.__file__).read_text(encoding="utf-8")
    assert "import pickle" not in source
    assert "import joblib" not in source


def test_legacy_p_fill_ledger_injection_is_rejected(tmp_path: Path) -> None:
    repo, _, selection = _prepare_repo(tmp_path)
    selection["rows"][0]["p_fill_shadow_probability"] = 0.999
    _write_json(
        repo / public.SELECTION_ROOT / "shadow_20260824.json",
        selection,
    )
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="legacy/model ledger fields",
    ):
        public.build_research_projection(repo, "20260824")


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda selection: selection["source_promotion"].__setitem__(
                "source_bundle_sha256", "0" * 64
            ),
            "bundle",
        ),
        (
            lambda selection: selection["rows"][0].__setitem__(
                "promotion_rank", 99
            ),
            "identity or rank",
        ),
        (
            lambda selection: selection.__setitem__("exec_date", "20260827"),
            "exec_date",
        ),
    ],
)
def test_bundle_membership_rank_and_date_drift_fail_closed(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    repo, _, selection = _prepare_repo(tmp_path)
    mutation(selection)
    _write_json(
        repo / public.SELECTION_ROOT / "shadow_20260824.json",
        selection,
    )
    with pytest.raises(public.ExecutableProfitResearchProjectionError, match=match):
        public.build_research_projection(repo, "20260824")


def test_source_csv_sha_drift_fails_closed(tmp_path: Path) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    path = repo / public.SELECTION_ROOT / "shadow_20260824.csv"
    path.write_text(path.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="selection CSV SHA",
    ):
        public.build_research_projection(repo, "20260824")


def test_public_projection_rejects_changed_or_unquantized_shadow_price_cap(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    projection["rows"][0]["shadow_max_price"] = 10.123
    projection["snapshot_sha256"] = public._payload_snapshot(projection)
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="D-frozen price cap",
    ):
        public.validate_research_projection(projection)


def test_missing_statistics_is_explicit_null_and_d_projection_stays_immutable(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    statistics = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260824",
    )
    assert statistics["statistics"] is None
    assert statistics["source_bindings"] == {
        "t_verification": None,
        "t1_settlement": None,
        "statistics": None,
    }
    assert all(
        row["t_validation_status"] is None
        and row["t1_settlement_status"] is None
        for row in statistics["latest_selected_rows"]
    )

    result = public.materialize_research_projection(repo, projection, statistics)
    projection_bytes = result[0].read_bytes()
    # A later statistics as-of artifact is separate and cannot mutate D rank.
    later = public.build_shadow_statistics_projection(repo, projection, "20260825")
    public.materialize_research_projection(repo, projection, later)
    assert result[0].read_bytes() == projection_bytes


def test_delayed_actual_exit_after_asof_is_never_publicly_exposed(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    _write_minimal_truth_artifacts(
        repo,
        projection,
        actual_exit_date="20260827",
    )

    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="actual exit is after public as-of date",
    ):
        public.build_shadow_statistics_projection(
            repo,
            projection,
            "20260826",
        )


def test_public_asof_must_be_a_pinned_sse_open_session_even_at_materialize(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="pinned SSE open session",
    ):
        public.build_shadow_statistics_projection(
            repo,
            projection,
            "20260830",
        )

    forged = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260824",
    )
    forged["as_of_date"] = "20260830"
    forged["snapshot_sha256"] = public._payload_snapshot(forged)
    public.validate_shadow_statistics_projection(forged)
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="pinned SSE open session",
    ):
        public.materialize_research_projection(repo, projection, forged)


@pytest.mark.parametrize("mutation", ["score", "rank", "row"])
def test_materialize_rebuild_rejects_projection_semantic_tampering(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    tampered = copy.deepcopy(projection)
    if mutation == "score":
        row = tampered["rows"][0]
        row["research_fill_proxy_score"] -= 0.01
        row["research_joint_proxy_score"] = (
            row["research_fill_proxy_score"]
            * row["research_conditional_profit_score"]
        )
    elif mutation == "rank":
        first = tampered["rows"][0]["promotion_rank"]
        tampered["rows"][0]["promotion_rank"] = tampered["rows"][1][
            "promotion_rank"
        ]
        tampered["rows"][1]["promotion_rank"] = first
    else:
        tampered["rows"][0], tampered["rows"][1] = (
            tampered["rows"][1],
            tampered["rows"][0],
        )
        for index, row in enumerate(tampered["rows"], start=1):
            row["executable_profit_research_rank"] = index
            row["shadow_selected"] = index <= 2
            row["shadow_slot"] = index if index <= 2 else None
    tampered["snapshot_sha256"] = public._payload_snapshot(tampered)
    public.validate_research_projection(tampered)
    statistics = public.build_shadow_statistics_projection(
        repo,
        tampered,
        "20260824",
    )

    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="projection does not exactly reconstruct",
    ):
        public.materialize_research_projection(repo, tampered, statistics)


def test_materialize_rebuild_rejects_settlement_projection_tampering(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    projection = public.build_research_projection(repo, "20260824")
    _write_minimal_truth_artifacts(
        repo,
        projection,
        actual_exit_date="20260826",
    )
    statistics = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260826",
    )
    tampered = copy.deepcopy(statistics)
    tampered["latest_selected_rows"][0]["net_return_after_cost"] = 0.99
    tampered["snapshot_sha256"] = public._payload_snapshot(tampered)
    public.validate_shadow_statistics_projection(tampered)

    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="Shadow statistics does not exactly reconstruct",
    ):
        public.materialize_research_projection(repo, projection, tampered)


def test_materialize_rebuild_rejects_summary_projection_tampering(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    _write_minimal_statistics_summary(repo)
    projection = public.build_research_projection(repo, "20260824")
    statistics = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260824",
    )
    tampered = copy.deepcopy(statistics)
    tampered["statistics"]["scope"] = {"selection_dates": 999}
    tampered["snapshot_sha256"] = public._payload_snapshot(tampered)
    public.validate_shadow_statistics_projection(tampered)

    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="Shadow statistics does not exactly reconstruct",
    ):
        public.materialize_research_projection(repo, projection, tampered)


def test_same_d_different_projection_and_backward_pointer_are_rejected(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path, signal_date="20260825")
    newer = public.build_research_projection(repo, "20260825")
    newer_stats = public.build_shadow_statistics_projection(
        repo,
        newer,
        "20260825",
    )
    public.materialize_research_projection(repo, newer, newer_stats)

    changed = copy.deepcopy(newer)
    changed["rows"][0]["research_fill_proxy_score"] = 0.5
    changed["rows"][0]["research_joint_proxy_score"] = (
        0.5 * changed["rows"][0]["research_conditional_profit_score"]
    )
    changed["snapshot_sha256"] = public._payload_snapshot(changed)
    changed_stats = public.build_shadow_statistics_projection(
        repo,
        changed,
        "20260826",
    )
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="does not exactly reconstruct",
    ):
        public.materialize_research_projection(repo, changed, changed_stats)

    _prepare_repo(repo, signal_date="20260824")
    older = public.build_research_projection(repo, "20260824")
    older_stats = public.build_shadow_statistics_projection(
        repo,
        older,
        "20260824",
    )
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="out-of-order public research",
    ):
        public.materialize_research_projection(repo, older, older_stats)


def test_same_asof_new_signal_keeps_two_immutable_statistics_files(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path, signal_date="20260824")
    previous = public.build_research_projection(repo, "20260824")
    previous_stats = public.build_shadow_statistics_projection(
        repo,
        previous,
        "20260825",
    )
    previous_result = public.materialize_research_projection(
        repo,
        previous,
        previous_stats,
    )
    assert previous_result[2].name == (
        "shadow_statistics_20260824_asof_20260825.json"
    )
    rewritten_stats = copy.deepcopy(previous_stats)
    rewritten_stats["latest_selected_rows"][0]["t_validation_status"] = (
        "INJECTED_REWRITE"
    )
    rewritten_stats["snapshot_sha256"] = public._payload_snapshot(rewritten_stats)
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="does not exactly reconstruct",
    ):
        public.materialize_research_projection(
            repo,
            previous,
            rewritten_stats,
        )

    _prepare_repo(repo, signal_date="20260825")
    current = public.build_research_projection(repo, "20260825")
    current_stats = public.build_shadow_statistics_projection(
        repo,
        current,
        "20260825",
    )
    current_result = public.materialize_research_projection(
        repo,
        current,
        current_stats,
    )
    assert current_result[2].name == (
        "shadow_statistics_20260825_asof_20260825.json"
    )
    assert previous_result[2].is_file()
    assert current_result[2].is_file()
    assert current_result[4]["latest_signal_date"] == "20260825"
    assert current_result[4]["latest_statistics_as_of_date"] == "20260825"


def test_statistics_summary_is_copied_with_exact_file_sha_binding(
    tmp_path: Path,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    summary = _write_minimal_statistics_summary(repo)
    summary_path = repo / public.SOURCE_STATISTICS_PATH
    projection = public.build_research_projection(repo, "20260824")
    statistics = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260824",
    )
    assert statistics["statistics"]["scope"] == {"selection_dates": 1}
    assert statistics["source_bindings"]["statistics"]["file_sha256"] == hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()


def test_formal_asof_statistics_summary_projects_without_hash_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    settlement_contract = repo / truth.CONTRACT_PATH
    settlement_contract.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / truth.CONTRACT_PATH, settlement_contract)
    calendar = repo / truth.CALENDAR_PATH
    calendar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / truth.CALENDAR_PATH, calendar)
    monkeypatch.setattr(
        truth,
        "validate_internal_forward_shadow_payload",
        lambda payload, require_downloads=False: None,
    )
    monkeypatch.setattr(truth, "validate_statistics", REAL_VALIDATE_STATISTICS)
    summary = truth.build_statistics(repo, as_of_date="20260824")
    truth.materialize_statistics(repo, summary)

    projection = public.build_research_projection(repo, "20260824")
    statistics = public.build_shadow_statistics_projection(
        repo,
        projection,
        "20260824",
    )
    assert statistics["statistics"] is not None
    assert statistics["statistics"]["source_as_of_date"] == "20260824"
    assert statistics["statistics"]["input_files_sha256"] == summary[
        "input_files_sha256"
    ]


def test_statistics_rejects_injected_legacy_ledger_source(tmp_path: Path) -> None:
    repo, _, _ = _prepare_repo(tmp_path)
    legacy_path = repo / "data/legacy/p_fill_shadow_top2_ledger.json"
    _write_json(legacy_path, {"old": True})
    input_files = [
        {
            "path": legacy_path.relative_to(repo).as_posix(),
            "sha256": hashlib.sha256(legacy_path.read_bytes()).hexdigest(),
        }
    ]
    summary = {
        "status": "INTERNAL_RESEARCH_SHADOW_ONLY",
        "as_of_date": "20260824",
        "scope": {"selection_dates": 1},
        "forward_signal_date_progress_180": {"observed_signal_dates": 1},
        "cohorts": {"shadow_slot_1": {"win_rate": None}},
        "probability_diagnostics": {"status": "UNCALIBRATED"},
        "excluded_ledgers": [],
        "pending_definitions": {},
        "input_files": input_files,
        "input_files_sha256": public._source_statistics_input_files_sha256(
            input_files
        ),
        "boundaries": {
            "official_trade_action_allowed": False,
            "actual_execution_claimed": False,
        },
        "snapshot_sha256": "8" * 64,
    }
    _write_json(repo / public.SOURCE_STATISTICS_PATH, summary)
    projection = public.build_research_projection(repo, "20260824")
    with pytest.raises(
        public.ExecutableProfitResearchProjectionError,
        match="legacy or foreign ledger",
    ):
        public.build_shadow_statistics_projection(
            repo,
            projection,
            "20260824",
        )
