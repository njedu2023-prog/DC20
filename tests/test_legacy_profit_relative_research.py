from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from scripts.build_decision_three_rank_snapshot import (
    build_runtime_candidate_frame,
    load_recovery_inputs,
)
from top10decision.decision.legacy_profit_relative_research import (
    LegacyProfitRelativeResearchError,
    OUTPUT_ROOT,
    PROJECTION_SCHEMA,
    SCORE_SEMANTICS,
    SEALED_PROFIT_ARTIFACT_SHA256,
    SEALED_VALIDATION_PATH,
    _payload_snapshot,
    _pretty_json_bytes,
    _projection_csv_bytes,
    _sha256_bytes,
    build_projection,
    score_legacy_profit_relative_rows,
    validate_projection,
    validate_repository_chain,
)
from top10decision.decision.three_engine_models import (
    load_research_only_legacy_three_engine_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SIGNAL_DATE = "20260821"
THREE_RANK_PATH = ROOT / "outputs/decision/three_rank_top10_20260821.json"
THREE_RANK_CSV_PATH = ROOT / "outputs/decision/three_rank_top10_20260821.csv"
PROMOTION_ARTIFACT_PATH = (
    ROOT
    / "data/decision_three_engines/recovery/20260821/model_snapshot/promotion.joblib"
)
RECOVERY_MANIFEST = (
    ROOT / "data/decision_three_engines/recovery/20260821/manifest.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def d21_inputs():
    pool, bars_by_code, _recovery = load_recovery_inputs(ROOT)
    runtime = build_runtime_candidate_frame(ROOT, pool, bars_by_code)
    loaded = load_research_only_legacy_three_engine_snapshot(
        ROOT / SEALED_VALIDATION_PATH,
        root=ROOT,
    )
    three_rank = json.loads(THREE_RANK_PATH.read_text(encoding="utf-8"))
    return runtime, loaded, three_rank


def test_d21_legacy_profit_relative_order_is_exact_and_official_fields_stay_null(
    d21_inputs,
) -> None:
    runtime, loaded, three_rank = d21_inputs
    before = _sha256(THREE_RANK_PATH)
    rows, feature_snapshot = score_legacy_profit_relative_rows(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        three_rank=three_rank,
        loaded=loaded,
    )
    assert [row["ts_code"] for row in rows] == [
        "002412.SZ",
        "000710.SZ",
        "000017.SZ",
        "002038.SZ",
        "000931.SZ",
        "002903.SZ",
        "603958.SH",
        "002491.SZ",
        "603626.SH",
    ]
    assert [row["legacy_profit_relative_rank"] for row in rows] == list(
        range(1, 10)
    )
    assert [row["legacy_profit_raw_score"] for row in rows] == pytest.approx(
        [
            0.461910,
            0.405427,
            0.401507,
            0.394002,
            0.386569,
            0.373120,
            0.367237,
            0.366787,
            0.359590,
        ],
        abs=5e-7,
    )
    assert rows[0]["legacy_profit_relative_percentile"] == 1.0
    assert rows[-1]["legacy_profit_relative_percentile"] == pytest.approx(1 / 9)
    assert feature_snapshot == three_rank["feature_snapshot_sha256"]
    assert all(row["profit_rank"] is None for row in three_rank["rows"])
    assert all(
        row["predicted_profit_probability"] is None
        for row in three_rank["rows"]
    )
    assert _sha256(THREE_RANK_PATH) == before


def test_projection_is_not_a_probability_or_formal_rank(d21_inputs) -> None:
    runtime, loaded, _three_rank = d21_inputs
    payload = build_projection(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        loaded=loaded,
        runtime_source={
            "source_kind": "sealed_20260821_recovery",
            "path": RECOVERY_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(RECOVERY_MANIFEST),
        },
    )
    validate_projection(payload)
    assert payload["schema_version"] == PROJECTION_SCHEMA
    assert payload["research_only"] is True
    assert payload["actual_execution_claimed"] is False
    assert payload["model"]["official_status"] == "NOT_READY_VALIDATION_GATE"
    assert payload["model"]["formal_ranking_ready"] is False
    assert payload["model"]["formal_probability_ready"] is False
    assert payload["model"]["probability_claimed"] is False
    assert payload["model"]["score_semantics"] == SCORE_SEMANTICS
    assert payload["model"]["sealed_artifact_sha256"] == (
        SEALED_PROFIT_ARTIFACT_SHA256
    )
    assert payload["model"]["validation_gate_pass_count"] == 20
    assert payload["model"]["validation_gate_total_count"] == 26
    assert payload["model"]["validation_gate_score_pct"] == 76.9
    assert payload["model"]["p_fill_integrated"] is False
    assert payload["execution"] == {
        "decision": "NO_TRADE",
        "buy_count": 0,
        "order_count": 0,
        "broker_connected": False,
        "human_decision_support_only": True,
    }
    forbidden = {"profit_rank", "predicted_profit_probability", "action"}
    assert all(not forbidden.intersection(row) for row in payload["rows"])


def test_research_projection_cannot_mutate_official_membership_or_order(
    d21_inputs,
) -> None:
    runtime, loaded, three_rank = d21_inputs
    official_paths = (
        THREE_RANK_PATH,
        THREE_RANK_CSV_PATH,
        PROMOTION_ARTIFACT_PATH,
    )
    before_hashes = {path: _sha256(path) for path in official_paths}
    before_members = three_rank["top10_members_sha256"]
    before_order = [
        (row["ts_code"], row["promotion_rank"])
        for row in three_rank["rows"]
    ]

    payload = build_projection(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        loaded=loaded,
        runtime_source={
            "source_kind": "sealed_20260821_recovery",
            "path": RECOVERY_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(RECOVERY_MANIFEST),
        },
    )

    assert payload["top10_members_sha256"] == before_members
    assert sorted(
        (row["ts_code"], row["promotion_rank"])
        for row in payload["rows"]
    ) == sorted(before_order)
    assert {path: _sha256(path) for path in official_paths} == before_hashes


@pytest.mark.parametrize("mode", ["missing", "all_missing"])
def test_missing_runtime_feature_fails_closed(d21_inputs, mode: str) -> None:
    runtime, loaded, three_rank = d21_inputs
    required = loaded.payloads["profit"]["bundle"].feature_builder.numeric_columns[0]
    broken = runtime.copy()
    if mode == "missing":
        broken = broken.drop(columns=[required])
    else:
        broken[required] = np.nan

    with pytest.raises(
        (LegacyProfitRelativeResearchError, ValueError),
        match="feature|missing|empty",
    ):
        score_legacy_profit_relative_rows(
            ROOT,
            signal_date=SIGNAL_DATE,
            runtime_candidates=broken,
            three_rank=three_rank,
            loaded=loaded,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.update(exec_date=payload["signal_date"]),
            "chronology",
        ),
        (
            lambda payload: payload["ranking_contract"].update(
                membership_or_promotion_rank_may_change=True
            ),
            "ranking safety",
        ),
        (
            lambda payload: payload["execution"].update(buy_count=1),
            "claimed execution",
        ),
        (
            lambda payload: payload["model"].update(probability_claimed=True),
            "model binding",
        ),
        (
            lambda payload: payload["model"].update(
                sealed_artifact_sha256="0" * 64
            ),
            "model binding",
        ),
    ],
)
def test_safety_boundary_tamper_is_rejected_after_rehash(
    d21_inputs,
    mutate,
    message: str,
) -> None:
    runtime, loaded, _three_rank = d21_inputs
    payload = build_projection(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        loaded=loaded,
        runtime_source={
            "source_kind": "sealed_20260821_recovery",
            "path": RECOVERY_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(RECOVERY_MANIFEST),
        },
    )
    forged = copy.deepcopy(payload)
    mutate(forged)
    forged["snapshot_sha256"] = _payload_snapshot(forged)
    with pytest.raises(LegacyProfitRelativeResearchError, match=message):
        validate_projection(forged)


def test_equal_raw_scores_keep_equal_dense_rank_without_code_tiebreak(
    d21_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, loaded, three_rank = d21_inputs
    bundle = loaded.payloads["profit"]["bundle"]

    def tied_components(frame):
        raw = np.linspace(0.9, 0.1, len(frame))
        raw[0:2] = 0.8
        return raw.copy(), raw

    monkeypatch.setattr(bundle, "predict_components", tied_components)
    rows, _feature_snapshot = score_legacy_profit_relative_rows(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        three_rank=three_rank,
        loaded=loaded,
    )
    tied = [row for row in rows if row["legacy_profit_raw_score"] == 0.8]
    assert len(tied) == 2
    assert {row["legacy_profit_relative_rank"] for row in tied} == {1}
    assert {row["rank_group_size"] for row in tied} == {2}
    assert all(row["rank_tied"] is True for row in tied)
    assert len({row["legacy_profit_relative_percentile"] for row in tied}) == 1


def test_projection_rejects_formal_field_injection(d21_inputs) -> None:
    runtime, loaded, _three_rank = d21_inputs
    payload = build_projection(
        ROOT,
        signal_date=SIGNAL_DATE,
        runtime_candidates=runtime,
        loaded=loaded,
        runtime_source={
            "source_kind": "sealed_20260821_recovery",
            "path": RECOVERY_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(RECOVERY_MANIFEST),
        },
    )
    forged = copy.deepcopy(payload)
    forged["rows"][0]["profit_rank"] = 1
    forged["snapshot_sha256"] = "0" * 64
    with pytest.raises(
        LegacyProfitRelativeResearchError,
        match="row fields|formal/action",
    ):
        validate_projection(forged)


def test_public_chain_matches_a_deterministic_sealed_rebuild() -> None:
    evidence = validate_repository_chain(ROOT)
    assert evidence["signal_date"] == SIGNAL_DATE
    assert evidence["candidate_count"] == 9
    assert evidence["deterministic_rebuild_match"] is True
    assert (ROOT / OUTPUT_ROOT / "projection_20260821.json").is_file()
    assert (ROOT / OUTPUT_ROOT / "projection_20260821.csv").is_file()
    assert (ROOT / OUTPUT_ROOT / "index.json").is_file()


def test_public_chain_rejects_forged_self_consistent_scores(
    tmp_path: Path,
) -> None:
    recovery_relative = Path("data/decision_three_engines/recovery/20260821")
    shutil.copytree(ROOT / recovery_relative, tmp_path / recovery_relative)
    recovery_builder = Path("scripts/build_decision_three_rank_snapshot.py")
    (tmp_path / recovery_builder).parent.mkdir(parents=True)
    shutil.copy2(ROOT / recovery_builder, tmp_path / recovery_builder)
    decision_output = tmp_path / "outputs/decision"
    decision_output.mkdir(parents=True)
    for name in (
        "three_rank_top10_20260821.json",
        "three_rank_top10_20260821.csv",
    ):
        shutil.copy2(ROOT / "outputs/decision" / name, decision_output / name)
    shutil.copytree(ROOT / OUTPUT_ROOT, tmp_path / OUTPUT_ROOT)

    projection_path = tmp_path / OUTPUT_ROOT / "projection_20260821.json"
    csv_path = tmp_path / OUTPUT_ROOT / "projection_20260821.csv"
    index_path = tmp_path / OUTPUT_ROOT / "index.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    # Keep the same order and every declarative boundary, but change one model
    # score and recompute all self-consistency hashes. Only a deterministic
    # rebuild from the sealed model can detect this forgery.
    projection["rows"][0]["legacy_profit_raw_score"] -= 0.001
    projection["snapshot_sha256"] = _payload_snapshot(projection)
    csv_bytes = _projection_csv_bytes(projection)
    projection["downloads"]["csv_sha256"] = _sha256_bytes(csv_bytes)
    csv_path.write_bytes(csv_bytes)
    projection_path.write_bytes(_pretty_json_bytes(projection))

    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["latest_projection_json_sha256"] = _sha256(projection_path)
    index["latest_projection_csv_sha256"] = _sha256(csv_path)
    index["latest_projection_snapshot_sha256"] = projection[
        "snapshot_sha256"
    ]
    index_path.write_bytes(_pretty_json_bytes(index))

    with pytest.raises(
        LegacyProfitRelativeResearchError,
        match="deterministic sealed rebuild",
    ):
        validate_repository_chain(tmp_path)
