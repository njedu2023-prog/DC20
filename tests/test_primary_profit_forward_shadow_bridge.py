from __future__ import annotations

import copy
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from top10decision.decision import primary_profit_forward_shadow_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
D28_PROJECTION = (
    ROOT
    / "outputs/decision/executable_profit_research/projection_20260828.json"
)
D28_SELECTED_AT = datetime.fromisoformat("2026-08-28T23:00:00+08:00")


def _write(path: Path, value: bytes = b"fixture\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def _load_real_d28_projection() -> dict:
    if D28_PROJECTION.is_file():
        return json.loads(D28_PROJECTION.read_text(encoding="utf-8"))
    raw_rows = [
        ("603269.SH", "海鸥股份", "通用设备", "2→3", 9, 0.3077953767581772, 0.9625775880763978, 0.32807140256321415),
        ("603011.SH", "合锻智能", "专用设备", "2→3", 10, 0.25833832901184434, 0.9839685733652221, 0.3159153921576913),
        ("600162.SH", "香江控股", "房地产开发", "2→3", 8, 0.35934736322878613, 0.9518288283385253, 0.31637317219436745),
        ("002942.SZ", "新农股份", "农化制品", "3→4", 6, 0.40300975638897807, 0.9770784663551753, 0.30375319181368254),
        ("600654.SH", "中安科", "软件开发", "2→3", 5, 0.4149372673347629, 0.8965905160736126, 0.30400754681418696),
        ("600479.SH", "千金药业", "中药Ⅱ", "3→4", 7, 0.37084497443679976, 0.9713611459748406, 0.2771508465894826),
        ("002886.SZ", "沃特股份", "塑料", "2→3", 2, 0.6028465363572788, 0.7621032655675505, 0.3342659213752967),
        ("603900.SH", "莱绅通灵", "饰品", "2→3", 4, 0.4711006519443423, 0.7605257292604003, 0.3220449407894178),
        ("000712.SZ", "锦龙股份", "证券Ⅱ", "3→4", 3, 0.5854469896313731, 0.8771055852699283, 0.2739223888325659),
        ("600540.SH", "新赛股份", "种植业", "2→3", 1, 0.7315741223905382, 0.6800205400897139, 0.3180529307225045),
    ]
    rows = []
    for rank, values in enumerate(raw_rows, start=1):
        code, name, industry, transition, promotion_rank, promotion, fill, conditional = values
        rows.append(
            {
                "ts_code": code,
                "name": name,
                "industry": industry,
                "stage_transition": transition,
                "promotion_rank": promotion_rank,
                "predicted_promotion_probability": promotion,
                "executable_profit_research_rank": rank,
                "research_fill_proxy_score": fill,
                "research_conditional_profit_score": conditional,
                "research_joint_proxy_score": fill * conditional,
            }
        )
    return {
        "signal_date": "20260828",
        "exec_date": "20260831",
        "exit_date": "20260901",
        "candidate_count": 10,
        "generation_mode": "NATURAL",
        "status": "PROSPECTIVE_RESEARCH",
        "prospective": True,
        "retrospective_non_forward": False,
        "research_only": True,
        "top10_members_sha256": bridge._canonical_sha256(
            sorted(row["ts_code"] for row in rows)
        ),
        "snapshot_sha256": "5" * 64,
        "source_bindings": {},
        "boundaries": {
            "proxy_scores_uncalibrated": True,
            "may_create_trade_action": False,
            "action_input_consumed": False,
        },
        "model": {
            "artifact_sha256": bridge.EXPECTED_MODEL_SHA256,
            "feature_columns_sha256": bridge.EXPECTED_FEATURE_COLUMNS_SHA256,
            "feature_count": 156,
            "calibrated_probability_output": False,
            "maximum_used_scheduled_exit_date": "20260818",
            "lagged_prior_max_history_exit_date": "20260818",
        },
        "rows": rows,
    }


def _fixture_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_count: int,
    exact_top2_top3_tie: bool = False,
) -> tuple[Path, dict, bytes]:
    source_projection = _load_real_d28_projection()
    projection = copy.deepcopy(source_projection)
    rows = copy.deepcopy(source_projection["rows"][:candidate_count])
    if candidate_count < 10:
        for rank, row in enumerate(rows, start=1):
            row["promotion_rank"] = rank
    if exact_top2_top3_tie and len(rows) >= 3:
        rows[2]["research_joint_proxy_score"] = rows[1][
            "research_joint_proxy_score"
        ]
        rows[2]["estimated_executable_profit_probability"] = rows[1][
            "research_joint_proxy_score"
        ]
    projection["rows"] = rows
    projection["candidate_count"] = candidate_count
    projection["top10_members_sha256"] = (
        source_projection["top10_members_sha256"]
        if candidate_count == 10
        else bridge._canonical_sha256([row["ts_code"] for row in rows])
    )
    if candidate_count == 0:
        projection["model"] = {
            "status": "INTERNAL_CHALLENGER_NOT_READY",
            "artifact_status": "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY",
            "artifact_sha256": bridge.EXPECTED_MODEL_SHA256,
            "feature_columns_sha256": bridge.EXPECTED_FEATURE_COLUMNS_SHA256,
            "feature_count": 156,
            "model_loaded": False,
            "inference_performed": False,
            "empty_event_reason": "P0_FROZEN_TOPN_EMPTY",
        }

    repo = tmp_path / f"repo-{candidate_count}"
    repo.mkdir()
    contract_path = _write(
        repo / bridge.CONTRACT_PATH,
        (ROOT / bridge.CONTRACT_PATH).read_bytes(),
    )
    calendar_path = _write(repo / bridge.CALENDAR_PATH)
    model_path = _write(repo / bridge.MODEL_PATH, b"sealed-model-fixture\n")
    mixed_json = _write(
        repo
        / f"outputs/decision/executable_profit_research/projection_20260828.json",
        json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    mixed_csv = _write(
        repo
        / f"outputs/decision/executable_profit_research/projection_20260828.csv"
    )
    mixed_index = _write(
        repo / "outputs/decision/executable_profit_research/index.json"
    )
    receipt = _write(repo / "outputs/decision/primary_d_receipt_20260828.json")
    runtime = _write(
        repo / "outputs/decision/primary_d_runtime_features_20260828.csv"
    )
    three_json = _write(repo / "outputs/decision/three_rank_top10_20260828.json")
    three_csv = _write(repo / "outputs/decision/three_rank_top10_20260828.csv")

    original_sha = bridge._sha256

    def fixture_sha(path: Path) -> str:
        if path == calendar_path:
            return bridge.EXPECTED_CALENDAR_SHA256
        if path == model_path:
            return bridge.EXPECTED_MODEL_SHA256
        return original_sha(path)

    runtime_sha = original_sha(runtime)
    projection["source_bindings"] = {
        "contract": {
            "path": "models/decision_primary_profit_research_contract.json",
            "sha256": "1" * 64,
            "contract_id": "fixture",
        },
        "primary_receipt": {
            "path": receipt.relative_to(repo).as_posix(),
            "sha256": original_sha(receipt),
            "generation_mode": "NATURAL",
        },
        "runtime_features": {
            "path": runtime.relative_to(repo).as_posix(),
            "sha256": runtime_sha,
            "row_count": candidate_count,
            "selected_count": candidate_count,
            "identity_sha256": "2" * 64,
            "feature_snapshot_sha256": "3" * 64,
        },
        "three_rank": {
            "json_path": three_json.relative_to(repo).as_posix(),
            "json_sha256": original_sha(three_json),
            "csv_path": three_csv.relative_to(repo).as_posix(),
            "csv_sha256": original_sha(three_csv),
            "bundle_sha256": "4" * 64,
            "feature_snapshot_sha256": "3" * 64,
            "top10_members_sha256": projection["top10_members_sha256"],
        },
    }
    mixed_json.write_bytes(
        json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    mixed_json_original = mixed_json.read_bytes()

    runtime_rows = []
    for index, row in enumerate(rows, start=1):
        d_close = {"603269.SH": 22.18, "603011.SH": 27.65}.get(
            row["ts_code"],
            10.0 + index,
        )
        runtime_rows.append(
            {
                "ts_code": row["ts_code"],
                "name": row["name"],
                "industry": row["industry"],
                "stage_transition": row["stage_transition"],
                "promotion_rank": row["promotion_rank"],
                "top10_selected": 1,
                "d_close": d_close,
                "generated_at_utc": "2026-08-28T14:36:01+00:00",
            }
        )
    full_runtime = pd.DataFrame(
        runtime_rows,
        columns=[
            "ts_code",
            "name",
            "industry",
            "stage_transition",
            "promotion_rank",
            "top10_selected",
            "d_close",
            "generated_at_utc",
        ],
    )
    bundle = {
        "inputs": SimpleNamespace(full_runtime=full_runtime),
        "mixed": {
            "projection": projection,
            "index": {},
            "json_path": mixed_json,
            "csv_path": mixed_csv,
            "index_path": mixed_index,
        },
    }
    monkeypatch.setattr(bridge, "_load_contract", lambda root: (contract_path, {}))
    monkeypatch.setattr(
        bridge,
        "_open_dates",
        lambda root: (calendar_path, ["20260828", "20260831", "20260901"]),
    )
    monkeypatch.setattr(bridge, "_load_primary_bundle", lambda root, date: bundle)
    monkeypatch.setattr(
        bridge,
        "_load_dated_primary_mixed_projection",
        lambda root, date: {
            "projection": projection,
            "json_path": mixed_json,
            "csv_path": mixed_csv,
            "inputs": bundle["inputs"],
        },
    )
    monkeypatch.setattr(bridge, "_sha256", fixture_sha)
    monkeypatch.setattr(
        bridge,
        "_price_cap",
        lambda row, runtime_sha256: (
            round(float(row["d_close"]), 2),
            "D_CLOSE_CONSERVATIVE_CAP",
        ),
    )
    return repo, projection, mixed_json_original


@pytest.mark.parametrize("candidate_count", [0, 1, 2, 10])
def test_build_supports_exact_n_without_padding_or_rescoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_count: int,
) -> None:
    repo, projection, mixed_bytes = _fixture_bundle(
        tmp_path,
        monkeypatch,
        candidate_count=candidate_count,
    )
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    assert payload["top10_count"] == candidate_count
    assert payload["shadow_top2"]["actual_slots"] == min(2, candidate_count)
    assert len(payload["rows"]) == candidate_count
    assert [row["ts_code"] for row in payload["rows"]] == [
        row["ts_code"] for row in projection["rows"]
    ]
    assert [row["promotion_rank"] for row in payload["rows"]] == [
        row["promotion_rank"] for row in projection["rows"]
    ]
    assert payload["boundaries"]["may_create_trade_action"] is False
    assert payload["source_bindings"]["model"]["inference_performed_by_bridge"] is False
    assert (
        repo
        / "outputs/decision/executable_profit_research/projection_20260828.json"
    ).read_bytes() == mixed_bytes


def test_real_d28_top1_top2_are_frozen_with_company_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_bytes_before = (
        D28_PROJECTION.read_bytes() if D28_PROJECTION.is_file() else None
    )
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=10)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    assert [
        (row["shadow_slot"], row["ts_code"], row["name"], row["promotion_rank"])
        for row in payload["shadow_top2"]["rows"]
    ] == [
        (1, "603269.SH", "海鸥股份", 9),
        (2, "603011.SH", "合锻智能", 10),
    ]
    assert [row["shadow_max_price"] for row in payload["shadow_top2"]["rows"]] == [
        22.18,
        27.65,
    ]
    assert payload["exec_date"] == "20260831"
    assert payload["exit_date"] == "20260901"
    if public_bytes_before is not None:
        assert D28_PROJECTION.read_bytes() == public_bytes_before


def test_real_d28_build_calls_unmocked_primary_bundle_validator() -> None:
    required = [
        ROOT / bridge.CONTRACT_PATH,
        ROOT / bridge.CALENDAR_PATH,
        ROOT / bridge.MODEL_PATH,
        ROOT / "outputs/decision/executable_profit_research/index.json",
        ROOT / "outputs/decision/executable_profit_research/projection_20260828.json",
        ROOT / "outputs/decision/executable_profit_research/projection_20260828.csv",
        ROOT / "outputs/decision/legacy_profit_relative_research/index.json",
        ROOT / "outputs/decision/legacy_profit_relative_research/projection_20260828.json",
        ROOT / "outputs/decision/legacy_profit_relative_research/projection_20260828.csv",
        ROOT / "outputs/decision/primary_d_receipt_20260828.json",
        ROOT / "outputs/decision/primary_d_runtime_features_20260828.csv",
        ROOT / "outputs/decision/three_rank_top10_20260828.json",
        ROOT / "outputs/decision/three_rank_top10_20260828.csv",
    ]
    if not all(path.is_file() for path in required):
        pytest.skip("full committed D28 repository surface is absent in sparse checkout")
    mixed_before = required[4].read_bytes()
    single_before = required[7].read_bytes()
    payload = bridge.build_primary_profit_forward_shadow(
        ROOT,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    assert payload["shadow_top2"]["rows"][0]["ts_code"] == "603269.SH"
    assert payload["shadow_top2"]["rows"][1]["ts_code"] == "603011.SH"
    assert required[4].read_bytes() == mixed_before
    assert required[7].read_bytes() == single_before


def test_exact_top2_top3_joint_tie_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _fixture_bundle(
        tmp_path,
        monkeypatch,
        candidate_count=10,
        exact_top2_top3_tie=True,
    )
    with pytest.raises(
        bridge.PrimaryProfitForwardShadowError,
        match="Top2/Top3",
    ):
        bridge.build_primary_profit_forward_shadow(
            repo,
            "20260828",
            selected_at=D28_SELECTED_AT,
        )


def test_historical_validation_ignores_mutable_pointer_but_rejects_dated_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=10)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    mutable_index = repo / "outputs/decision/executable_profit_research/index.json"
    mutable_index.write_bytes(b"later-D-current-pointer\n")
    bridge.validate_primary_profit_forward_shadow(payload, repo_root=repo)

    dated_projection = (
        repo
        / "outputs/decision/executable_profit_research/projection_20260828.json"
    )
    dated_projection.write_bytes(dated_projection.read_bytes() + b"\n")
    with pytest.raises(
        bridge.PrimaryProfitForwardShadowError,
        match="mixed projection SHA drifted",
    ):
        bridge.validate_primary_profit_forward_shadow(payload, repo_root=repo)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("name", "伪造名称"),
        ("shadow_max_price", 999999.99),
    ],
)
def test_historical_validation_rebinds_every_shadow_row_to_exact_p1_and_p0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    forged_value: object,
) -> None:
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=2)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    payload["rows"][0][field] = forged_value
    if field in payload["shadow_top2"]["rows"][0]:
        payload["shadow_top2"]["rows"][0][field] = forged_value
    payload["selection_identity_sha256"] = bridge._canonical_sha256(
        bridge._identity_payload(payload)
    )
    payload["snapshot_sha256"] = bridge._canonical_sha256(
        bridge._snapshot_payload(payload)
    )
    with pytest.raises(
        bridge.PrimaryProfitForwardShadowError,
        match="published P1 mixed projection",
    ):
        bridge.validate_primary_profit_forward_shadow(payload, repo_root=repo)


@pytest.mark.parametrize("relative", [bridge.OUTPUT_ROOT, bridge.PUBLIC_ROOT])
def test_shadow_output_rejects_symlinked_ancestor_without_external_write(
    tmp_path: Path,
    relative: Path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / relative.parts[0]).symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        bridge.PrimaryProfitForwardShadowError,
        match="symlink|escaped|unsafe",
    ):
        bridge._safe_directory(repo, relative, label="test Shadow output")

    assert not (outside / Path(*relative.parts[1:])).exists()


def test_selection_materializer_uses_new_pointer_and_preserves_p1_and_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, mixed_bytes = _fixture_bundle(
        tmp_path,
        monkeypatch,
        candidate_count=2,
    )
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    legacy_index = repo / bridge.OUTPUT_ROOT / "index.json"
    _write(legacy_index, b"legacy-v1-pointer-must-not-change\n")
    legacy_before = legacy_index.read_bytes()
    json_path, csv_path, index_path, index = (
        bridge.materialize_primary_profit_forward_shadow(
            repo,
            payload,
            _now=D28_SELECTED_AT + timedelta(minutes=1),
        )
    )
    assert json_path.is_file() and csv_path.is_file()
    assert index_path == repo / bridge.PRIMARY_INDEX_PATH
    assert index["latest_signal_date"] == "20260828"
    assert legacy_index.read_bytes() == legacy_before
    assert (
        repo
        / "outputs/decision/executable_profit_research/projection_20260828.json"
    ).read_bytes() == mixed_bytes
    assert not any("action" in path.name.lower() for path in repo.rglob("*"))


def test_materialized_selection_csv_must_equal_canonical_payload_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=2)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    selection_path, csv_path, _, _ = bridge.materialize_primary_profit_forward_shadow(
        repo,
        payload,
        _now=D28_SELECTED_AT + timedelta(minutes=1),
    )
    materialized = json.loads(selection_path.read_text(encoding="utf-8"))
    csv_path.write_bytes(b"same-declared-hash-but-not-canonical\n")
    materialized["downloads"]["csv_sha256"] = bridge._sha256(csv_path)
    selection_path.write_bytes(bridge._pretty_json_bytes(materialized))

    with pytest.raises(
        bridge.PrimaryProfitForwardShadowError,
        match="canonical bytes",
    ):
        bridge.validate_primary_profit_forward_shadow_repository_chain(
            repo,
            "20260828",
        )


def test_public_sidecar_contains_names_pending_truth_and_no_action_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=2)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    selection_path, _, _, _ = bridge.materialize_primary_profit_forward_shadow(
        repo,
        payload,
        _now=D28_SELECTED_AT + timedelta(minutes=1),
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    summary = {
        "as_of_date": "20260828",
        "snapshot_sha256": "a" * 64,
        "public_start_signal_date": "20260828",
        "scope": {"minimum_signal_date": "20260828"},
        "cohorts": {"all_selected_slots": {"selected_slots": 2}},
        "forward_signal_date_progress_180": {
            "observed_signal_dates": 1,
            "target_signal_dates": 180,
        },
        "probability_diagnostics": {"status": "UNCALIBRATED"},
    }
    summary_path = _write(
        repo / bridge.STATISTICS_PATH,
        json.dumps(summary, sort_keys=True).encode("utf-8"),
    )
    state = bridge.build_primary_profit_shadow_public_state(
        repo,
        selection_path=selection_path,
        selection=selection,
        summary_path=summary_path,
        summary=summary,
    )
    bridge.validate_primary_profit_forward_shadow_public_state(state)
    assert [row["name"] for row in state["latest_selected_rows"]] == [
        "海鸥股份",
        "合锻智能",
    ]
    assert {row["t_status"] for row in state["latest_selected_rows"]} == {
        "PENDING_T_NOT_REACHED"
    }
    assert {row["t1_status"] for row in state["latest_selected_rows"]} == {
        "PENDING_T1_NOT_REACHED"
    }
    assert state["boundaries"]["official_trade_action_allowed"] is False
    assert state["boundaries"]["may_create_trade_action"] is False


def test_real_d28_dry_freeze_materializes_and_validates_complete_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, mixed_bytes = _fixture_bundle(
        tmp_path,
        monkeypatch,
        candidate_count=10,
    )

    fake_settlement = types.ModuleType(
        "top10decision.decision.executable_profit_shadow_settlement"
    )
    fake_settlement.validate_statistics = lambda payload, **kwargs: None
    monkeypatch.setitem(
        sys.modules,
        "top10decision.decision.executable_profit_shadow_settlement",
        fake_settlement,
    )

    def rebuild_statistics(root: Path, *, as_of_date: str) -> tuple[Path, dict]:
        selection_path = (
            root / bridge.OUTPUT_ROOT / f"shadow_{as_of_date}.json"
        )
        summary = {
            "as_of_date": as_of_date,
            "snapshot_sha256": "a" * 64,
            "public_start_signal_date": "20260828",
            "scope": {"minimum_signal_date": "20260828"},
            "cohorts": {"all_selected_slots": {"selected_slots": 2}},
            "forward_signal_date_progress_180": {
                "observed_signal_dates": 1,
                "target_signal_dates": 180,
                "remaining_signal_dates": 179,
            },
            "probability_diagnostics": {"status": "UNCALIBRATED"},
            "input_files": [
                {
                    "path": selection_path.relative_to(root).as_posix(),
                    "sha256": bridge._sha256(selection_path),
                }
            ],
        }
        path = _write(
            root / bridge.STATISTICS_PATH,
            json.dumps(summary, sort_keys=True).encode("utf-8"),
        )
        return path, summary

    monkeypatch.setattr(bridge, "_rebuild_forward_statistics", rebuild_statistics)
    frozen = bridge.freeze_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    chain = bridge.validate_primary_profit_forward_shadow_repository_chain(
        repo,
        "20260828",
    )
    assert chain["selected_slots"] == 2
    assert chain["official_trade_action_created"] is False
    assert frozen["selection_json"].is_file()
    assert frozen["selection_csv"].is_file()
    assert frozen["selection_index"].name == "primary_mixed_index.json"
    assert frozen["statistics"].is_file()
    assert frozen["public_state"].name == "shadow_state_20260828_asof_20260828.json"
    assert frozen["public_index"].name == "shadow_index.json"
    state = json.loads(frozen["public_state"].read_text(encoding="utf-8"))
    assert [(row["ts_code"], row["name"]) for row in state["latest_selected_rows"]] == [
        ("603269.SH", "海鸥股份"),
        ("603011.SH", "合锻智能"),
    ]
    assert (
        repo
        / "outputs/decision/executable_profit_research/projection_20260828.json"
    ).read_bytes() == mixed_bytes
    assert not any("action" in path.name.lower() for path in repo.rglob("*"))


def test_exact_pair_grandfather_is_strict_byte_identical_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _fixture_bundle(tmp_path, monkeypatch, candidate_count=2)
    payload = bridge.build_primary_profit_forward_shadow(
        repo,
        "20260828",
        selected_at=D28_SELECTED_AT,
    )
    selection_path, _, _, _ = bridge.materialize_primary_profit_forward_shadow(
        repo,
        payload,
        _now=D28_SELECTED_AT + timedelta(minutes=1),
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection_binding = {
        "path": selection_path.relative_to(repo).as_posix(),
        "sha256": bridge._sha256(selection_path),
    }
    summary = {
        "as_of_date": "20260828",
        "snapshot_sha256": "a" * 64,
        "scope": {
            "minimum_signal_date": "20260824",
            "selection_dates": 2,
        },
        "cohorts": {"all_selected_slots": {"selected_slots": 2}},
        "forward_signal_date_progress_180": {
            "observed_signal_dates": 2,
            "target_signal_dates": 180,
            "remaining_signal_dates": 178,
        },
        "probability_diagnostics": {"status": "UNCALIBRATED"},
        "input_files": [selection_binding],
    }
    summary_path = _write(
        repo / bridge.STATISTICS_PATH,
        json.dumps(summary, sort_keys=True).encode("utf-8"),
    )
    monkeypatch.setattr(
        bridge,
        "_summary_has_public_cumulative_cutover",
        lambda candidate: True,
    )
    state = bridge.build_primary_profit_shadow_public_state(
        repo,
        selection_path=selection_path,
        selection=selection,
        summary_path=summary_path,
        summary=summary,
    )
    monkeypatch.setattr(
        bridge,
        "GRANDFATHERED_PUBLIC_STATE_SNAPSHOT_SHA256",
        state["snapshot_sha256"],
    )
    monkeypatch.setattr(
        bridge,
        "GRANDFATHERED_PUBLIC_STATISTICS_SNAPSHOT_SHA256",
        summary["snapshot_sha256"],
    )
    monkeypatch.setattr(
        bridge,
        "GRANDFATHERED_PUBLIC_STATISTICS_FILE_SHA256",
        bridge._sha256(summary_path),
    )
    fake_settlement = types.ModuleType(
        "top10decision.decision.executable_profit_shadow_settlement"
    )
    fake_settlement.validate_statistics = lambda payload, **kwargs: None
    monkeypatch.setitem(
        sys.modules,
        "top10decision.decision.executable_profit_shadow_settlement",
        fake_settlement,
    )
    state_path, index_path, _ = bridge._materialize_public_state(repo, state)
    protected = [
        summary_path,
        state_path,
        index_path,
        selection_path,
        repo / bridge.OUTPUT_ROOT / "shadow_20260828.csv",
        repo / bridge.PRIMARY_INDEX_PATH,
    ]
    before = {path: path.read_bytes() for path in protected}

    def forbidden_rebuild(*args: object, **kwargs: object) -> None:
        raise AssertionError("same-pair projection attempted to rebuild statistics")

    monkeypatch.setattr(bridge, "_rebuild_forward_statistics", forbidden_rebuild)
    projected = bridge.project_primary_profit_forward_shadow_state(
        repo,
        "20260828",
        "20260828",
    )
    chain = bridge.validate_primary_profit_forward_shadow_repository_chain(
        repo,
        "20260828",
    )
    assert projected["materialization"] == "EXACT_PAIR_BYTE_IDENTICAL_NO_OP"
    assert projected["grandfathered_pre_cutover"] is True
    assert chain["public_cumulative_status"] == "GRANDFATHERED_PRE_CUTOVER_HIDDEN"
    assert {path: path.read_bytes() for path in protected} == before
    assert not any("action" in path.name.lower() for path in repo.rglob("*"))


def test_cli_and_workflow_interfaces_are_exported() -> None:
    assert callable(bridge.validate_primary_profit_forward_shadow_repository_chain)
    assert callable(bridge.project_primary_profit_forward_shadow_state)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/freeze_primary_profit_forward_shadow.py"),
            "--help",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--signal-date" in completed.stdout
    assert "--as-of-date" in completed.stdout
