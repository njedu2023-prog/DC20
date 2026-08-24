from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from top10decision.decision import executable_profit_shadow as shadow
from top10decision.decision.three_rank import (
    build_three_rank_contract,
    materialize_three_rank_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
PINNED_REPO = Path(os.environ.get("DC20_TEST_PINNED_REPO_ROOT", ROOT)).resolve()
WORK_ROOT = ROOT / shadow.DEFAULT_WORK_ROOT
CONTRACT_PATH = ROOT / shadow.DEFAULT_CONTRACT_PATH


def _source_row(
    source: pd.Series,
    rank: int,
    signal_date: str,
    candidate_count: int = 10,
) -> dict[str, object]:
    transition = "2→3" if int(source["stage"]) == 2 else "3→4"
    row: dict[str, object] = {
        "ts_code": str(source["ts_code"]),
        "name": f"样本{rank}",
        "industry": "测试行业",
        "stage_transition": transition,
        "top10_selected": 1,
        "three_rank_contract_version": "decision_three_rank_v1",
        "promotion_pool_size": candidate_count,
        "promotion_rank": rank,
        "predicted_promotion_probability": 0.99 - rank * 0.03,
        "big_loss_safety_rank": None,
        "predicted_big_loss_probability": None,
        "profit_rank": None,
        "predicted_profit_probability": None,
        "feature_snapshot_sha256": "f" * 64,
        "p_fill_shadow_probability": 0.5,
        "p_fill_shadow_status": "SHADOW_READY",
        "p_fill_shadow_model_version": "p_fill_shadow_research_v1",
        "p_fill_shadow_model_as_of_date": "20260819",
        "p_fill_shadow_model_artifact_sha256": "4" * 64,
        "p_fill_shadow_validation_gate_pass_count": 26,
        "p_fill_shadow_validation_gate_total_count": 26,
        "p_fill_shadow_validation_gate_score_pct": 100.0,
    }
    for index, head in enumerate(("promotion", "big_loss", "profit"), start=1):
        ready = head == "promotion"
        row.update(
            {
                f"{head}_model_status": (
                    "READY" if ready else "NOT_READY_VALIDATION_GATE"
                ),
                f"{head}_model_version": f"{head}_v1",
                f"{head}_model_as_of_date": "20260819",
                f"{head}_model_artifact_sha256": str(index) * 64,
                f"{head}_validation_gate_pass_count": 26 if ready else 17,
                f"{head}_validation_gate_total_count": 26,
                f"{head}_validation_gate_score_pct": 100.0 if ready else 65.4,
            }
        )
    return row


@pytest.fixture(scope="session")
def loaded() -> shadow.LoadedInternalChallenger:
    return shadow.load_internal_challenger(
        PINNED_REPO,
        work_root=WORK_ROOT,
        contract_path=CONTRACT_PATH,
    )


@pytest.fixture(scope="session")
def source_sample(loaded: shadow.LoadedInternalChallenger) -> pd.DataFrame:
    history = pd.read_csv(
        PINNED_REPO / shadow.DEFAULT_HISTORY_LEDGER_PATH,
        low_memory=False,
    )
    codes = history["ts_code"].fillna("").astype(str)
    main_board = codes.str.fullmatch(
        r"(?:(?:600|601|603|605)\d{3}\.SH|(?:000|001|002|003)\d{3}\.SZ)"
    )
    sample = (
        history.loc[main_board]
        .drop_duplicates("ts_code", keep="last")
        .tail(10)
        .copy()
        .reset_index(drop=True)
    )
    assert len(sample) == 10
    assert set(loaded.raw_base_features).issubset(sample.columns)
    return sample


def _case(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
    *,
    signal_date: str = "20260824",
    candidate_count: int = 10,
) -> tuple[dict, pd.DataFrame, str]:
    dates = {
        "20260824": ("20260825", "20260826", "2026-08-24T08:00:00Z"),
        "20260825": ("20260826", "20260827", "2026-08-25T08:00:00Z"),
    }
    exec_date, exit_date, generated_at = dates[signal_date]
    selected_source = source_sample.head(candidate_count).copy()
    rows = [
        _source_row(row, rank, signal_date, candidate_count)
        for rank, (_, row) in enumerate(selected_source.iterrows(), start=1)
    ]
    frozen = build_three_rank_contract(
        {
            "generated_at_utc": generated_at,
            "signal_date": signal_date,
            "exec_date": exec_date,
            "exit_date": exit_date,
            "stage_watchlist": rows,
            "candidates": [],
            "model": {},
        }
    )
    feature_columns = [
        "ts_code",
        "stage",
        "board",
        *[
            column
            for column in loaded.raw_base_features
            if column not in {"stage", "board"}
        ],
    ]
    features = selected_source[feature_columns].copy()
    features.insert(0, "signal_date", signal_date)
    features["feature_snapshot_sha256"] = "f" * 64
    features["generated_at_utc"] = generated_at
    source_bytes = features.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return frozen, features, hashlib.sha256(source_bytes).hexdigest()


@pytest.fixture(scope="session")
def scored(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> dict:
    frozen, features, source_sha = _case(loaded, source_sample)
    return shadow._score_internal_forward_shadow_frame(
        repo_root=PINNED_REPO,
        frozen_top10=frozen,
        base_features=features,
        loaded=loaded,
        d_feature_source_name="pred_20260824.csv",
        d_feature_source_sha256=source_sha,
    )


def test_hash_bound_repository_challenger_is_strict_not_ready(
    loaded: shadow.LoadedInternalChallenger,
) -> None:
    assert loaded.bundle["status"] == shadow.ARTIFACT_STATUS
    assert len(loaded.feature_columns) == 156
    assert len(loaded.raw_base_features) == 44
    assert len(loaded.lagged_features) == 108
    assert loaded.audit["front_end_rank_allowed"] is False
    assert loaded.audit["official_trade_action_allowed"] is False


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("ARTIFACT_INDEX.json", "artifact index SHA drifted"),
        ("outputs/internal_forward_challenger.pkl", "model SHA drifted"),
        ("outputs/internal_forward_challenger_audit.json", "audit SHA drifted"),
        ("lagged_priors.py", "lagged_code SHA drifted"),
    ],
)
def test_hash_tamper_fails_before_pickle_load(
    tmp_path: Path,
    relative: str,
    expected: str,
) -> None:
    work = tmp_path / "work"
    for item in (
        "ARTIFACT_INDEX.json",
        "lagged_priors.py",
        "outputs/internal_forward_challenger.pkl",
        "outputs/internal_forward_challenger_audit.json",
        "outputs/lagged_priors_manifest.json",
    ):
        source = WORK_ROOT / item
        target = work / item
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    target = work / relative
    target.write_bytes(target.read_bytes() + b"tamper")
    with pytest.raises(shadow.ExecutableProfitShadowError, match=expected):
        shadow.load_internal_challenger(
            PINNED_REPO,
            work_root=work,
            contract_path=CONTRACT_PATH,
        )


def test_same_day_and_future_outcomes_do_not_change_lagged_priors(
    loaded: shadow.LoadedInternalChallenger,
) -> None:
    target = pd.DataFrame(
        [
            {
                "signal_date": "20260824",
                "ts_code": "600001.SH",
                "stage": 2,
                "board": "SH_MAIN",
                "promotion_rank": 1,
            }
        ]
    )
    prior = {
        "signal_date": "20260819",
        "target_exit_date": "20260821",
        "ts_code": "600001.SH",
        "stage": 2,
        "board": "SH_MAIN",
        "market_fill": 1,
        "profit_hit": 1,
        "big_loss_hit": 0,
        "net_return": 0.02,
    }
    poisoned = [
        {
            **prior,
            "signal_date": "20260820",
            "target_exit_date": "20260824",
            "profit_hit": 0,
            "big_loss_hit": 1,
            "net_return": -0.50,
        },
        {
            **prior,
            "signal_date": "20260821",
            "target_exit_date": "20260825",
            "profit_hit": 0,
            "big_loss_hit": 1,
            "net_return": -0.80,
        },
    ]
    open_dates = loaded.lagged_priors.read_sse_open_dates(
        PINNED_REPO / shadow.DEFAULT_CALENDAR_PATH
    )
    baseline = shadow.build_strict_lagged_priors(
        history=pd.DataFrame([prior]),
        targets=target,
        open_dates=open_dates,
        lagged_module=loaded.lagged_priors,
    )
    challenged = shadow.build_strict_lagged_priors(
        history=pd.DataFrame([prior, *poisoned]),
        targets=target,
        open_dates=open_dates,
        lagged_module=loaded.lagged_priors,
    )
    pd.testing.assert_frame_equal(
        baseline[list(loaded.lagged_features)],
        challenged[list(loaded.lagged_features)],
        check_exact=True,
    )
    assert challenged["lagged_prior_max_history_exit_date"].iloc[0] == "20260821"


def test_exact_top10_membership_and_complete_d_surface_fail_closed(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    frozen, features, source_sha = _case(loaded, source_sample)
    drifted = features.copy()
    drifted.loc[0, "ts_code"] = "600999.SH"
    with pytest.raises(shadow.ExecutableProfitShadowError, match="membership drifted"):
        shadow._score_internal_forward_shadow_frame(
            repo_root=PINNED_REPO,
            frozen_top10=frozen,
            base_features=drifted,
            loaded=loaded,
            d_feature_source_name="pred_20260824.csv",
            d_feature_source_sha256=source_sha,
        )
    missing = features.copy()
    missing[loaded.raw_base_features[0]] = np.nan
    with pytest.raises(shadow.ExecutableProfitShadowError, match="entirely missing"):
        shadow._score_internal_forward_shadow_frame(
            repo_root=PINNED_REPO,
            frozen_top10=frozen,
            base_features=missing,
            loaded=loaded,
            d_feature_source_name="pred_20260824.csv",
            d_feature_source_sha256=source_sha,
        )
    old_surface = features.drop(columns=[loaded.raw_base_features[9]])
    with pytest.raises(shadow.ExecutableProfitShadowError, match="promotion-source features"):
        shadow._score_internal_forward_shadow_frame(
            repo_root=PINNED_REPO,
            frozen_top10=frozen,
            base_features=old_surface,
            loaded=loaded,
            d_feature_source_name="pred_20260824.csv",
            d_feature_source_sha256=source_sha,
        )


def test_non_main_board_code_fails_before_scoring(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    drifted_sample = source_sample.copy()
    drifted_sample.loc[0, "ts_code"] = "300001.SZ"
    drifted_sample.loc[0, "board"] = "SZ_MAIN"
    frozen, _, _ = _case(loaded, drifted_sample)
    open_dates = loaded.lagged_priors.read_sse_open_dates(
        PINNED_REPO / shadow.DEFAULT_CALENDAR_PATH
    )
    with pytest.raises(shadow.ExecutableProfitShadowError, match="non-main-board"):
        shadow._strict_top10_targets(frozen, open_dates)


def test_internal_contract_exact_sections_reject_decoy_values(
    loaded: shadow.LoadedInternalChallenger,
) -> None:
    drifted = copy.deepcopy(loaded.internal_contract)
    drifted["model_semantics"]["decoy_fill_output"] = "research_fill_proxy_score"
    with pytest.raises(
        shadow.ExecutableProfitShadowError,
        match="model semantics drifted",
    ):
        shadow._validate_internal_contract(
            drifted,
            formal_contract_sha256=shadow.EXPECTED_FORMAL_CONTRACT_SHA256,
        )


def test_repository_inputs_reject_symlink_ancestors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "bound.json").write_text("{}\n", encoding="utf-8")
    (repo / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        shadow.ExecutableProfitShadowError,
        match="unsafe|symlink|escaped",
    ):
        shadow._safe_file(repo, Path("linked/bound.json"), label="bound input")


def test_challenger_work_root_rejects_symlink_ancestor(tmp_path: Path) -> None:
    linked_work = tmp_path / "linked-work"
    linked_work.symlink_to(WORK_ROOT, target_is_directory=True)
    with pytest.raises(
        shadow.ExecutableProfitShadowError,
        match="work root is unsafe",
    ):
        shadow.load_internal_challenger(
            PINNED_REPO,
            work_root=linked_work,
            contract_path=CONTRACT_PATH,
        )


def test_file_scoring_binds_actual_bytes_and_filters_full_pred_surface(
    tmp_path: Path,
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    _, features, _ = _case(loaded, source_sample)
    features["top10_selected"] = 1
    rejected = features.iloc[[0]].copy()
    rejected["ts_code"] = "600999.SH"
    rejected["board"] = "SH_MAIN"
    rejected["top10_selected"] = 0
    full_surface = pd.concat([features, rejected], ignore_index=True)
    promotion_snapshot = shadow._promotion_feature_snapshot_sha256(
        full_surface,
        loaded,
        signal_date="20260824",
    )
    full_surface["feature_snapshot_sha256"] = promotion_snapshot
    rows = [
        _source_row(row, rank, "20260824", 11)
        for rank, (_, row) in enumerate(source_sample.iterrows(), start=1)
    ]
    rejected_source = source_sample.iloc[0].copy()
    rejected_source["ts_code"] = "600999.SH"
    rejected_source["board"] = "SH_MAIN"
    rejected_row = _source_row(rejected_source, 11, "20260824", 11)
    rejected_row["top10_selected"] = 0
    rows.append(rejected_row)
    for row in rows:
        row["feature_snapshot_sha256"] = promotion_snapshot
    frozen = build_three_rank_contract(
        {
            "generated_at_utc": "2026-08-24T08:00:00Z",
            "signal_date": "20260824",
            "exec_date": "20260825",
            "exit_date": "20260826",
            "stage_watchlist": rows,
            "candidates": [],
            "model": {},
        }
    )

    for relative, source_root in (
        (shadow.DEFAULT_FORMAL_CONTRACT_PATH, PINNED_REPO),
        (shadow.DEFAULT_CONTRACT_PATH, ROOT),
        (shadow.DEFAULT_FEATURE_MANIFEST_PATH, PINNED_REPO),
        (shadow.DEFAULT_HISTORY_LEDGER_PATH, PINNED_REPO),
        (shadow.DEFAULT_CALENDAR_PATH, PINNED_REPO),
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
    shutil.copytree(WORK_ROOT, tmp_path / shadow.DEFAULT_WORK_ROOT)
    materialize_three_rank_artifacts(tmp_path, frozen)
    source_path = (
        tmp_path
        / "outputs"
        / "auction_v3"
        / "predictions"
        / "pred_20260824.csv"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(
        full_surface.to_csv(index=False, lineterminator="\n").encode("utf-8")
    )
    payload = shadow.score_internal_forward_shadow(
        repo_root=tmp_path,
        d_feature_path=source_path,
    )
    assert payload["source_d_feature"]["file_sha256"] == hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    assert payload["source_d_feature"]["file_name"] == source_path.name
    assert payload["top10_count"] == 10


def test_proxy_identity_fixed_top2_and_not_ready_boundaries(scored: dict) -> None:
    assert scored["status"] == shadow.INTERNAL_STATUS
    assert scored["proxy_scores_uncalibrated"] is True
    assert scored["model"]["calibrated_probability_output"] is False
    assert scored["model"]["return_lcb_component_available"] is False
    assert scored["model"]["big_loss_tie_break_available"] is False
    assert [row["internal_shadow_order"] for row in scored["rows"]] == list(
        range(1, 11)
    )
    assert [row["ts_code"] for row in scored["shadow_top2"]["rows"]] == [
        row["ts_code"] for row in scored["rows"][:2]
    ]
    assert sum(row["internal_shadow_selected"] for row in scored["rows"]) == 2
    for row in scored["rows"]:
        assert row["research_joint_proxy_score"] == pytest.approx(
            row["research_fill_proxy_score"]
            * row["research_conditional_profit_score"],
            abs=1e-15,
            rel=0,
        )
        assert row["shadow_max_price"] > 0
        assert row["shadow_price_source_sha256"] == scored["source_d_feature"][
            "file_sha256"
        ]
        assert row["shadow_price_basis"] == "D_CLOSE_CONSERVATIVE_CAP"
    assert all(value is False for value in scored["boundaries"].values())


def test_recommended_price_is_frozen_before_outcome_truth(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    frozen, features, source_sha = _case(
        loaded,
        source_sample,
        candidate_count=2,
    )
    features["recommended_max_price"] = [11.11, 22.22]
    source_sha = hashlib.sha256(
        features.to_csv(index=False, lineterminator="\n").encode("utf-8")
    ).hexdigest()
    payload = shadow._score_internal_forward_shadow_frame(
        repo_root=PINNED_REPO,
        frozen_top10=frozen,
        base_features=features,
        loaded=loaded,
        d_feature_source_name="pred_20260824.csv",
        d_feature_source_sha256=source_sha,
    )
    by_code = {row["ts_code"]: row for row in payload["rows"]}
    for source_index, source_row in features.iterrows():
        row = by_code[str(source_row["ts_code"])]
        assert row["shadow_max_price"] == pytest.approx(
            float(source_row["recommended_max_price"]),
            abs=1e-12,
        )
        assert row["shadow_price_basis"] == "D_FROZEN_RECOMMENDED_MAX_PRICE"
        assert row["shadow_price_source_sha256"] == source_sha


@pytest.mark.parametrize(
    ("candidate_count", "expected_slots", "expected_status"),
    [
        (2, 2, "FROZEN_INTERNAL_RESEARCH_ONLY"),
        (1, 1, "FROZEN_INTERNAL_RESEARCH_ONLY"),
    ],
)
def test_complete_frozen_topn_never_backfills_shadow_slots(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
    candidate_count: int,
    expected_slots: int,
    expected_status: str,
) -> None:
    frozen, features, source_sha = _case(
        loaded,
        source_sample,
        candidate_count=candidate_count,
    )
    payload = shadow._score_internal_forward_shadow_frame(
        repo_root=PINNED_REPO,
        frozen_top10=frozen,
        base_features=features,
        loaded=loaded,
        d_feature_source_name="pred_20260824.csv",
        d_feature_source_sha256=source_sha,
    )
    assert payload["top10_count"] == candidate_count
    assert len(payload["rows"]) == candidate_count
    assert payload["shadow_top2"]["status"] == expected_status
    assert payload["shadow_top2"]["actual_slots"] == expected_slots
    assert len(payload["shadow_top2"]["rows"]) == expected_slots
    assert sum(row["internal_shadow_selected"] for row in payload["rows"]) == expected_slots


class _FixedModel:
    def __init__(self, probabilities: list[float]):
        self.probabilities = np.asarray(probabilities, dtype=float)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        values = self.probabilities[: len(frame)]
        return np.column_stack([1.0 - values, values])


def test_exact_top2_top3_joint_tie_fails_closed(
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    frozen, features, source_sha = _case(loaded, source_sample)
    bundle = dict(loaded.bundle)
    bundle["fill_model"] = _FixedModel([0.8] * 10)
    bundle["conditional_profit_model"] = _FixedModel(
        [0.95, 0.80, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.10]
    )
    tied = shadow.LoadedInternalChallenger(
        bundle=bundle,
        audit=loaded.audit,
        index=loaded.index,
        internal_contract=loaded.internal_contract,
        lagged_priors=loaded.lagged_priors,
        feature_columns=loaded.feature_columns,
        raw_base_features=loaded.raw_base_features,
        lagged_features=loaded.lagged_features,
        source_hashes=loaded.source_hashes,
    )
    with pytest.raises(shadow.ExecutableProfitShadowError, match="Top2/Top3"):
        shadow._score_internal_forward_shadow_frame(
            repo_root=PINNED_REPO,
            frozen_top10=frozen,
            base_features=features,
            loaded=tied,
            d_feature_source_name="pred_20260824.csv",
            d_feature_source_sha256=source_sha,
        )


def test_immutable_dated_materialization_is_idempotent_and_rejects_rewrite(
    tmp_path: Path,
    scored: dict,
) -> None:
    within_window = datetime(2026, 8, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = shadow._materialize_internal_forward_shadow_for_test(
        tmp_path,
        scored,
        now=within_window,
    )
    first_bytes = (first[0].read_bytes(), first[1].read_bytes(), first[2].read_bytes())
    outside_window = datetime(2026, 8, 27, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    second = shadow._materialize_internal_forward_shadow_for_test(
        tmp_path,
        scored,
        now=outside_window,
    )
    assert first[:3] == second[:3]
    assert first_bytes == (second[0].read_bytes(), second[1].read_bytes(), second[2].read_bytes())

    changed = copy.deepcopy(scored)
    changed["rows"][0]["name"] = "同日篡改"
    changed["snapshot_sha256"] = shadow._canonical_sha256(
        shadow._payload_without_materialization_fields(changed)
    )
    with pytest.raises(shadow.ExecutableProfitShadowError, match="retargeted|overwritten"):
        shadow._materialize_internal_forward_shadow_for_test(
            tmp_path,
            changed,
            now=within_window,
        )


def test_shadow_output_rejects_symlink_ancestor_before_write(
    tmp_path: Path,
    scored: dict,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "data").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        shadow.ExecutableProfitShadowError,
        match="symlink ancestor",
    ):
        shadow._materialize_internal_forward_shadow_for_test(
            tmp_path,
            scored,
            now=datetime(2026, 8, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    assert list(outside.iterdir()) == []


def test_backfill_is_rejected_before_any_old_d_file_is_written(
    tmp_path: Path,
    scored: dict,
    loaded: shadow.LoadedInternalChallenger,
    source_sample: pd.DataFrame,
) -> None:
    frozen, features, source_sha = _case(
        loaded,
        source_sample,
        signal_date="20260825",
    )
    later = shadow._score_internal_forward_shadow_frame(
        repo_root=PINNED_REPO,
        frozen_top10=frozen,
        base_features=features,
        loaded=loaded,
        d_feature_source_name="pred_20260825.csv",
        d_feature_source_sha256=source_sha,
    )
    shadow._materialize_internal_forward_shadow_for_test(
        tmp_path,
        later,
        now=datetime(2026, 8, 25, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    with pytest.raises(shadow.ExecutableProfitShadowError, match="backfill"):
        shadow._materialize_internal_forward_shadow_for_test(
            tmp_path,
            scored,
            now=datetime(2026, 8, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    old_json = (
        tmp_path
        / shadow.OUTPUT_RELATIVE_ROOT
        / "shadow_20260824.json"
    )
    old_csv = old_json.with_suffix(".csv")
    assert not old_json.exists()
    assert not old_csv.exists()


def test_pointer_repair_is_a_mutation_and_requires_live_window(
    tmp_path: Path,
    scored: dict,
) -> None:
    within_window = datetime(2026, 8, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    json_path, csv_path, index_path, _ = shadow._materialize_internal_forward_shadow_for_test(
        tmp_path,
        scored,
        now=within_window,
    )
    dated_bytes = (json_path.read_bytes(), csv_path.read_bytes())
    index_path.unlink()
    with pytest.raises(shadow.ExecutableProfitShadowError, match="outside D-close"):
        shadow._materialize_internal_forward_shadow_for_test(
            tmp_path,
            scored,
            now=datetime(2026, 8, 27, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    assert not index_path.exists()
    assert dated_bytes == (json_path.read_bytes(), csv_path.read_bytes())
