from __future__ import annotations

import tempfile
import shutil
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from top10decision.auction_v3.config import AuctionV3Config
from top10decision.auction_v3.engine import AuctionV3Engine
from top10decision.decision.three_engine_models import (
    PROMOTION_SOURCE_FEATURES,
    RUNTIME_ALIGNED_POOL_FEATURES,
    THREE_ENGINE_VALIDATION_GATE_NAMES,
    ThreeEngineArtifactError,
    load_three_engine_artifacts,
    top10_members_sha256,
)
from top10decision.decision.three_rank import (
    THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS,
    ThreeEngineRuntimeMixin,
    apply_three_engine_runtime,
    augment_three_engine_runtime_base,
    three_engine_d_close_market_features,
)


ROOT = Path(__file__).resolve().parents[1]


def test_future_dated_prediction_keeps_all_d_only_promotion_source_features() -> None:
    """The internal profit shadow must consume the already-frozen D surface."""

    source = inspect.getsource(ThreeEngineRuntimeMixin.build_prediction)
    assert "*PROMOTION_SOURCE_FEATURES" in source
    assert len(PROMOTION_SOURCE_FEATURES) == 18


class _RuntimeEngine(ThreeEngineRuntimeMixin, AuctionV3Engine):
    pass


def test_runtime_adapter_never_overrides_canonical_current_base() -> None:
    assert "_current_base" not in ThreeEngineRuntimeMixin.__dict__
    assert callable(
        ThreeEngineRuntimeMixin.__dict__["build_three_engine_inference_pool"]
    )


def _engine(tmp_path: Path) -> AuctionV3Engine:
    return _RuntimeEngine(AuctionV3Config(root=tmp_path))


def _pool(size: int = 12) -> pd.DataFrame:
    codes = [f"600{i:03d}.SH" for i in range(size)]
    return pd.DataFrame(
        {
            "signal_date": "20260820",
            "ts_code": codes,
            "stage": ["2→3" if index % 2 == 0 else "3→4" for index in range(size)],
            "board": "SH_MAIN",
            "returns_1d": np.linspace(9.9, 10.1, size),
            "d_close": np.linspace(10.0, 20.0, size),
        }
    )


def _legacy_scored(pool: pd.DataFrame) -> pd.DataFrame:
    scored = pool.copy()
    scored["promotion_rank"] = list(reversed(range(1, len(pool) + 1)))
    scored["promotion_rank_score"] = np.linspace(0.1, 0.2, len(pool))
    scored["predicted_promotion_probability"] = 0.11
    scored["predicted_big_loss_probability"] = 0.77
    scored["predicted_profit_probability"] = 0.22
    return scored


def _official_snapshot(pool: pd.DataFrame, *, ready: bool = True) -> SimpleNamespace:
    rows = pool.copy()
    count = len(rows)
    selected_count = min(10, count) if ready else 0
    rows["promotion_pool_size"] = count
    rows["three_rank_contract_version"] = "decision_three_rank_v1"
    rows["feature_snapshot_sha256"] = "a" * 64
    rows["top10_selected"] = [int(index < selected_count) for index in range(count)]
    rows["promotion_rank"] = (
        pd.Series(range(1, count + 1), dtype="Int64")
        if ready
        else pd.Series(pd.NA, index=rows.index, dtype="Int64")
    )
    rows["promotion_rank_score"] = np.linspace(0.99, 0.01, count) if ready else np.nan
    rows["predicted_promotion_probability"] = (
        np.linspace(0.90, 0.10, count) if ready else np.nan
    )
    for rank_name, score_name, probability_name in (
        ("big_loss_safety_rank", "big_loss_rank_score", "predicted_big_loss_probability"),
        ("profit_rank", "profit_rank_score", "predicted_profit_probability"),
        ("p_fill_shadow_rank", "p_fill_shadow_score", "p_fill_shadow_probability"),
    ):
        rows[rank_name] = pd.Series(pd.NA, index=rows.index, dtype="Int64")
        rows[score_name] = np.nan
        rows[probability_name] = np.nan
        if ready:
            rows.loc[: selected_count - 1, rank_name] = range(1, selected_count + 1)
            rows.loc[: selected_count - 1, score_name] = np.linspace(
                0.1, 0.9, selected_count
            )
            rows.loc[: selected_count - 1, probability_name] = np.linspace(
                0.1, 0.9, selected_count
            )
    snapshot_date = (
        str(rows["signal_date"].iloc[0])
        if "signal_date" in rows.columns
        else "20260820"
    )
    rows["top10_members_sha256"] = top10_members_sha256(
        snapshot_date,
        rows.loc[rows["top10_selected"].eq(1), "ts_code"].astype(str),
    )
    rows["p_fill_shadow_status"] = "SHADOW_READY" if ready else "SHADOW_NOT_READY_RUNTIME_FEATURES"
    rows["p_fill_shadow_model_version"] = "p_fill_shadow-v1"
    rows["p_fill_shadow_model_as_of_date"] = "20260811"
    rows["p_fill_shadow_model_artifact_sha256"] = "4" * 64
    rows["p_fill_shadow_validation_gate_pass_count"] = 26
    rows["p_fill_shadow_validation_gate_total_count"] = 26
    rows["p_fill_shadow_validation_gate_score_pct"] = 100.0
    gate_scores = {
        "promotion": (26, 26, 100.0),
        "big_loss": (17, 26, 65.4),
        "profit": (20, 26, 76.9),
    }
    for head in ("promotion", "big_loss", "profit"):
        pass_count, total_count, score = gate_scores[head]
        rows[f"{head}_model_status"] = "READY" if ready else "NOT_READY_MISSING_RUNTIME_FEATURES"
        rows[f"{head}_model_version"] = f"{head}-v1"
        rows[f"{head}_model_as_of_date"] = "20260811"
        rows[f"{head}_model_artifact_sha256"] = head[0] * 64
        rows[f"{head}_validation_gate_pass_count"] = pass_count
        rows[f"{head}_validation_gate_total_count"] = total_count
        rows[f"{head}_validation_gate_score_pct"] = score
    # Guard the test fixture whenever the production projection adds a field.
    assert set(THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS).issubset(rows.columns)
    return SimpleNamespace(
        rows=rows,
        status="READY" if ready else "NOT_READY_PROMOTION",
        diagnostics={"runtime_feature_gate_passed": ready},
    )


def test_prediction_projects_exact_hard_range_surface_without_changing_actions(
    tmp_path: Path,
) -> None:
    canonical = pd.DataFrame(
        {
            "ts_code": ["600001.SH", "600002.SH", "600003.SH", "600004.SH"],
            "limit_times": [1.0, 2.0, 3.0, 4.0],
            "stage": ["1→2", "2→3", "3→4", "4→5"],
            "action": ["WATCH", "SHADOW_ONLY", "REJECT", "WATCH"],
            "selected": [0, 0, 0, 0],
            "observation_rank": [pd.NA, 2, 1, pd.NA],
            "trade_rank": [pd.NA, 1, 2, pd.NA],
            "canonical_marker": [101, 202, 303, 404],
            "promotion_rank": [pd.NA, 8, 7, pd.NA],
            "promotion_rank_score": [np.nan, 0.8, 0.7, np.nan],
            "predicted_promotion_probability": [np.nan, 0.58, 0.57, np.nan],
            "predicted_big_loss_probability": [np.nan, 0.18, 0.19, np.nan],
            "predicted_profit_probability": [np.nan, 0.38, 0.39, np.nan],
        }
    )
    inference_pool = canonical.loc[
        canonical["limit_times"].isin((2.0, 3.0))
    ].copy().reset_index(drop=True)
    inference_pool["board"] = "SH_MAIN"
    pool_values = {
        "mechanism_limit_pct": [10.0, 10.0],
        "focus_pool_size": [2.0, 2.0],
        "stage_pool_size": [1.0, 1.0],
        "stage2_pool_size": [1.0, 1.0],
        "stage3_pool_size": [1.0, 1.0],
        "stage_pool_share": [0.5, 0.5],
        "same_industry_stage_count": [1.0, 1.0],
        "market_max_limit_times": [3.0, 3.0],
        "open_board_count": [2.0, 3.0],
        "reseal_score": [0.2, 0.3],
        "late_withdraw": [0.0, 1.0],
    }
    for column, values in pool_values.items():
        inference_pool[column] = values
        canonical[column] = -999.0

    class _CanonicalPredictionStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "data" / "decision" / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20990102", "20990103"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return True

        def _current_base(
            self,
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> pd.DataFrame:
            return canonical.copy()

        def build_prediction(
            self,
            _signal_date: str,
            _candidates: pd.DataFrame,
            _bundle: object,
            _backtest_metrics: dict[str, object],
            *,
            force: bool = False,
        ) -> pd.DataFrame:
            del force
            return canonical.copy()

    class _ProjectionEngine(ThreeEngineRuntimeMixin, _CanonicalPredictionStub):
        def build_three_engine_inference_pool(
            self,
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> pd.DataFrame:
            return inference_pool.copy()

    engine = _ProjectionEngine()
    engine.config.prediction_root.mkdir(parents=True, exist_ok=True)
    partial_path = engine.config.prediction_root / "pred_20260820.csv"
    canonical.to_csv(partial_path, index=False, encoding="utf-8-sig")
    partial_bytes = partial_path.read_bytes()
    snapshot = _official_snapshot(inference_pool)
    with mock.patch(
        "top10decision.decision.three_rank.load_three_engine_artifacts",
        return_value=object(),
    ), mock.patch(
        "top10decision.decision.three_rank.score_three_engine_snapshot",
        return_value=snapshot,
    ) as scorer:
        result = engine.build_prediction(
            "20260820",
            canonical[["ts_code"]],
            object(),
            {},
        )

    assert list(result["ts_code"]) == ["600002.SH", "600003.SH"]
    assert partial_path.read_bytes() != partial_bytes
    pd.testing.assert_frame_equal(
        scorer.call_args.args[0].reset_index(drop=True),
        inference_pool.reset_index(drop=True),
        check_dtype=False,
    )
    projected = result.set_index("ts_code")
    expected = inference_pool.set_index("ts_code")
    for column in RUNTIME_ALIGNED_POOL_FEATURES:
        pd.testing.assert_series_equal(
            projected[column],
            expected[column],
            check_names=False,
            check_dtype=False,
        )
    for column in (
        "action",
        "selected",
        "observation_rank",
        "trade_rank",
        "canonical_marker",
    ):
        pd.testing.assert_series_equal(
            projected[column],
            canonical.set_index("ts_code").loc[projected.index, column],
            check_names=False,
            check_dtype=False,
        )


@pytest.mark.parametrize("existing_projection", [False, True])
def test_overlay_failure_restores_exact_projection_bytes(
    tmp_path: Path,
    existing_projection: bool,
) -> None:
    canonical = pd.DataFrame(
        {
            "signal_date": ["20260820"],
            "ts_code": ["600001.SH"],
            "stage": ["2→3"],
        }
    )

    class _CanonicalWriterStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20990102", "20990103"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return True

        def build_prediction(self, *_args, **_kwargs) -> pd.DataFrame:
            self.config.prediction_root.mkdir(parents=True, exist_ok=True)
            canonical.to_csv(
                self.config.prediction_root / "pred_20260820.csv",
                index=False,
                encoding="utf-8-sig",
            )
            canonical.to_csv(
                self.config.prediction_root / "pred_latest.csv",
                index=False,
                encoding="utf-8-sig",
            )
            return canonical.copy()

    class _BrokenOverlayEngine(ThreeEngineRuntimeMixin, _CanonicalWriterStub):
        @staticmethod
        def build_three_engine_inference_pool(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> pd.DataFrame:
            raise RuntimeError("overlay exploded")

    engine = _BrokenOverlayEngine()
    dated = engine.config.prediction_root / "pred_20260820.csv"
    latest = engine.config.prediction_root / "pred_latest.csv"
    before_dated = None
    before_latest = None
    if existing_projection:
        dated.parent.mkdir(parents=True)
        incomplete = pd.DataFrame(
            {"signal_date": ["20260820"], "ts_code": ["OLD.SZ"]}
        )
        incomplete.to_csv(dated, index=False, encoding="utf-8-sig")
        incomplete.assign(signal_date="20260819").to_csv(
            latest,
            index=False,
            encoding="utf-8-sig",
        )
        before_dated = dated.read_bytes()
        before_latest = latest.read_bytes()

    with pytest.raises(RuntimeError, match="overlay exploded"):
        engine.build_prediction("20260820", canonical, object(), {})

    if existing_projection:
        assert dated.read_bytes() == before_dated
        assert latest.read_bytes() == before_latest
    else:
        assert not dated.exists()
        assert not latest.exists()


@pytest.mark.parametrize("existing_projection", [False, True])
def test_empty_hard_range_pool_fails_before_scoring_and_restores_projection_bytes(
    tmp_path: Path,
    existing_projection: bool,
) -> None:
    canonical = pd.DataFrame(
        {
            "signal_date": ["20260820"],
            "ts_code": ["600001.SH"],
            "stage": ["2→3"],
        }
    )

    class _CanonicalWriterStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20990102", "20990103"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return True

        def build_prediction(self, *_args, **_kwargs) -> pd.DataFrame:
            self.config.prediction_root.mkdir(parents=True, exist_ok=True)
            canonical.to_csv(
                self.config.prediction_root / "pred_20260820.csv",
                index=False,
                encoding="utf-8-sig",
            )
            canonical.to_csv(
                self.config.prediction_root / "pred_latest.csv",
                index=False,
                encoding="utf-8-sig",
            )
            return canonical.copy()

    class _EmptyPoolEngine(ThreeEngineRuntimeMixin, _CanonicalWriterStub):
        @staticmethod
        def build_three_engine_inference_pool(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> pd.DataFrame:
            return pd.DataFrame(columns=["ts_code"])

    engine = _EmptyPoolEngine()
    dated = engine.config.prediction_root / "pred_20260820.csv"
    latest = engine.config.prediction_root / "pred_latest.csv"
    before_dated = None
    before_latest = None
    if existing_projection:
        dated.parent.mkdir(parents=True)
        incomplete = pd.DataFrame(
            {"signal_date": ["20260820"], "ts_code": ["OLD.SZ"]}
        )
        incomplete.to_csv(dated, index=False, encoding="utf-8-sig")
        incomplete.assign(signal_date="20260819").to_csv(
            latest,
            index=False,
            encoding="utf-8-sig",
        )
        before_dated = dated.read_bytes()
        before_latest = latest.read_bytes()

    with mock.patch(
        "top10decision.decision.three_rank.score_three_engine_snapshot"
    ) as scorer:
        with pytest.raises(
            RuntimeError,
            match=(
                "three-engine hard-range inference pool is empty "
                "for signal_date=20260820"
            ),
        ):
            engine.build_prediction("20260820", canonical, object(), {})

    scorer.assert_not_called()
    if existing_projection:
        assert dated.read_bytes() == before_dated
        assert latest.read_bytes() == before_latest
    else:
        assert not dated.exists()
        assert not latest.exists()


@pytest.mark.parametrize("existing_incomplete", [False, True])
def test_post_freeze_incomplete_projection_blocks_before_super_or_file_creation(
    tmp_path: Path,
    existing_incomplete: bool,
) -> None:
    calls = 0

    class _ClosedCanonicalStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20260821", "20260824"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return False

        def build_prediction(self, *_args, **_kwargs) -> pd.DataFrame:
            nonlocal calls
            calls += 1
            raise AssertionError("post-freeze validation must run before super")

    class _ClosedProjectionEngine(ThreeEngineRuntimeMixin, _ClosedCanonicalStub):
        pass

    engine = _ClosedProjectionEngine()
    dated = engine.config.prediction_root / "pred_20260820.csv"
    before = None
    if existing_incomplete:
        dated.parent.mkdir(parents=True)
        pd.DataFrame(
            {"signal_date": ["20260820"], "ts_code": ["600001.SH"]}
        ).to_csv(dated, index=False, encoding="utf-8-sig")
        before = dated.read_bytes()

    with pytest.raises(RuntimeError, match="historical D prediction"):
        engine.build_prediction("20260820", pd.DataFrame(), object(), {})

    assert calls == 0
    assert not (engine.config.prediction_root / "pred_latest.csv").exists()
    if existing_incomplete:
        assert dated.read_bytes() == before
    else:
        assert not dated.exists()


def test_complete_post_freeze_projection_recovers_latest_without_recompute(
    tmp_path: Path,
) -> None:
    class _ClosedCanonicalStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20260821", "20260824"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return False

        @staticmethod
        def build_prediction(*_args, **_kwargs) -> pd.DataFrame:
            raise AssertionError("complete frozen projection must be reused")

    class _ClosedProjectionEngine(ThreeEngineRuntimeMixin, _ClosedCanonicalStub):
        pass

    engine = _ClosedProjectionEngine()
    engine.config.prediction_root.mkdir(parents=True)
    complete = _official_snapshot(_pool(2)).rows
    complete["three_engine_runtime_status"] = "READY"
    complete["three_engine_runtime_feature_gate_passed"] = 1
    complete["three_engine_runtime_artifacts_hash_bound"] = 1
    complete["three_engine_runtime_input_pool_complete"] = 1
    complete["three_engine_runtime_failure"] = ""
    dated = engine.config.prediction_root / "pred_20260820.csv"
    complete.to_csv(dated, index=False, encoding="utf-8-sig")
    before = dated.read_bytes()

    result = engine.build_prediction("20260820", pd.DataFrame(), object(), {})

    assert dated.read_bytes() == before
    latest = engine.config.prediction_root / "pred_latest.csv"
    assert latest.is_file()
    pd.testing.assert_frame_equal(
        result.reset_index(drop=True),
        pd.read_csv(latest, encoding="utf-8-sig").reset_index(drop=True),
        check_dtype=False,
    )


@pytest.mark.parametrize("repair_pool_size", [False, True])
def test_post_freeze_truncated_projection_fails_internal_identity(
    tmp_path: Path,
    repair_pool_size: bool,
) -> None:
    calls = 0

    class _ClosedCanonicalStub:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                root=tmp_path,
                prediction_root=tmp_path / "predictions",
            )

        @staticmethod
        def _prediction_dates(
            _signal_date: str,
            _candidates: pd.DataFrame,
        ) -> tuple[str, str]:
            return "20260821", "20260824"

        @staticmethod
        def _prediction_revision_allowed(_expected_buy: str) -> bool:
            return False

        def build_prediction(self, *_args, **_kwargs) -> pd.DataFrame:
            nonlocal calls
            calls += 1
            raise AssertionError("truncated frozen projection must not reach super")

    class _ClosedProjectionEngine(ThreeEngineRuntimeMixin, _ClosedCanonicalStub):
        pass

    engine = _ClosedProjectionEngine()
    engine.config.prediction_root.mkdir(parents=True)
    complete = _official_snapshot(_pool(3)).rows
    complete["three_engine_runtime_status"] = "READY"
    complete["three_engine_runtime_feature_gate_passed"] = 1
    complete["three_engine_runtime_artifacts_hash_bound"] = 1
    complete["three_engine_runtime_input_pool_complete"] = 1
    complete["three_engine_runtime_failure"] = ""
    truncated = complete.iloc[:-1].copy()
    if repair_pool_size:
        # Repair the obvious row count while retaining the forged member hash.
        # The frozen projection must still fail closed on its internal identity.
        truncated["promotion_pool_size"] = len(truncated)
    dated = engine.config.prediction_root / "pred_20260820.csv"
    truncated.to_csv(dated, index=False, encoding="utf-8-sig")
    before = dated.read_bytes()

    with pytest.raises(RuntimeError, match="historical D prediction is incomplete"):
        engine.build_prediction("20260820", pd.DataFrame(), object(), {})

    assert calls == 0
    assert dated.read_bytes() == before
    assert not (engine.config.prediction_root / "pred_latest.csv").exists()


def test_official_a_scores_complete_pool_and_freezes_same_top10_set(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    pool = _pool(12)
    legacy = _legacy_scored(pool)
    snapshot = _official_snapshot(pool)

    with mock.patch(
        "top10decision.decision.three_rank.load_three_engine_artifacts",
        return_value=object(),
    ) as loader, mock.patch(
        "top10decision.decision.three_rank.score_three_engine_snapshot",
        return_value=snapshot,
    ) as scorer:
        result = apply_three_engine_runtime(
            engine, legacy, pool, "20260820"
        )

    loader.assert_called_once()
    scored_pool = scorer.call_args.args[0]
    assert set(scored_pool["ts_code"]) == set(pool["ts_code"])
    assert len(scored_pool) == 12
    assert set(result.loc[result["top10_selected"].eq(1), "ts_code"]) == set(
        pool.iloc[:10]["ts_code"]
    )
    assert result.loc[result["top10_selected"].eq(1), "big_loss_safety_rank"].notna().all()
    assert result.loc[result["top10_selected"].eq(0), "big_loss_safety_rank"].isna().all()


def test_legacy_selector_is_namespaced_shadow_and_cannot_overwrite_official_ranks(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    pool = _pool(12)
    legacy = _legacy_scored(pool)
    snapshot = _official_snapshot(pool)
    with mock.patch(
        "top10decision.decision.three_rank.load_three_engine_artifacts",
        return_value=object(),
    ), mock.patch(
        "top10decision.decision.three_rank.score_three_engine_snapshot",
        return_value=snapshot,
    ):
        result = apply_three_engine_runtime(
            engine, legacy, pool, "20260820"
        )

    assert result["promotion_rank"].tolist() == list(range(1, 13))
    assert result["legacy_shadow_promotion_rank"].tolist() == list(
        reversed(range(1, 13))
    )
    assert set(result["predicted_big_loss_probability"].dropna()) != {0.77}
    assert set(result["legacy_shadow_predicted_big_loss_probability"]) == {0.77}


def test_artifact_hash_drift_fails_closed_without_legacy_rank_fallback(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    validation = (
        tmp_path / "models" / "decision_three_engines" / "validation_latest.json"
    )
    validation.parent.mkdir(parents=True, exist_ok=True)
    validation.write_text("{}", encoding="utf-8")
    pool = _pool(12)
    legacy = _legacy_scored(pool)
    with mock.patch(
        "top10decision.decision.three_rank.load_three_engine_artifacts",
        side_effect=ThreeEngineArtifactError("promotion artifact hash mismatch"),
    ):
        result = apply_three_engine_runtime(
            engine, legacy, pool, "20260820"
        )

    assert result["top10_selected"].eq(0).all()
    assert result["promotion_rank"].isna().all()
    assert result["predicted_promotion_probability"].isna().all()
    assert set(result["promotion_model_status"]) == {
        "NOT_READY_ARTIFACT_PROVENANCE"
    }
    assert result["three_engine_runtime_artifacts_hash_bound"].eq(0).all()
    assert result["legacy_shadow_promotion_rank"].notna().all()


def test_missing_or_all_empty_runtime_features_never_receive_official_fallback(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    pool = _pool(12).drop(columns=["returns_1d"])
    legacy = _legacy_scored(pool)
    snapshot = _official_snapshot(pool, ready=False)
    with mock.patch(
        "top10decision.decision.three_rank.load_three_engine_artifacts",
        return_value=object(),
    ), mock.patch(
        "top10decision.decision.three_rank.score_three_engine_snapshot",
        return_value=snapshot,
    ):
        result = apply_three_engine_runtime(
            engine, legacy, pool, "20260820"
        )

    assert result["three_engine_runtime_feature_gate_passed"].eq(0).all()
    assert result["top10_selected"].eq(0).all()
    assert result["promotion_rank"].isna().all()
    assert set(result["promotion_model_status"]) == {
        "NOT_READY_MISSING_RUNTIME_FEATURES"
    }


def test_d_close_market_features_match_registered_ledger_names(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    dates = [date.strftime("%Y%m%d") for date in pd.bdate_range("2026-07-20", periods=21)]
    code = "600000.SH"
    previous = 10.0
    for index, date in enumerate(dates):
        close = previous * (1.10 if index == len(dates) - 1 else 1.002)
        row = pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "open": previous * 1.001,
                    "close": close,
                    "high": max(close, previous) * 1.002,
                    "low": min(close, previous) * 0.998,
                    "vol": 1000.0 + index * 10.0,
                }
            ]
        ).set_index("ts_code", drop=False)
        engine._market_cache[(date, "daily")] = row
        previous = close

    features = three_engine_d_close_market_features(
        engine, dates[-1], code, dates
    )
    expected = {
        "returns_1d",
        "high_low_range",
        "candle_body",
        "gap_open",
        "vol",
        "volume_ratio",
        "volatility_5d",
        "volatility_10d",
        "volatility_20d",
        "atr",
        "ret_2d",
        "ret_5d",
        "ret_10d",
        "bid_ask_proxy",
        "spread_proxy",
    }
    assert set(features) == expected
    assert all(np.isfinite(features[name]) for name in expected)
    assert abs(features["returns_1d"] - 10.0) < 1e-9


def test_runtime_d_pct_change_matches_canonical_pre_close_fallbacks(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    signal_date = "20260820"
    codes = ["600001.SH", "600002.SH"]
    daily = pd.DataFrame(
        [
            {
                "ts_code": codes[0],
                "close": 11.0,
                "pre_close_est": 10.0,
                "pct_chg": 999.0,
            },
            {
                "ts_code": codes[1],
                "close": 11.0,
                "pct_chg": 10.0,
            },
        ]
    ).set_index("ts_code", drop=False)
    engine._market_cache[(signal_date, "daily")] = daily
    engine._market_cache[(signal_date, "daily_basic")] = pd.DataFrame()
    engine.market_dates = lambda: [signal_date]  # type: ignore[method-assign]
    base = pd.DataFrame(
        {
            "signal_date": signal_date,
            "ts_code": codes,
            "industry": ["I1", "I2"],
            "limit_times": [2.0, 3.0],
            "stage": ["2→3", "3→4"],
        }
    )

    result = augment_three_engine_runtime_base(engine, signal_date, base)

    np.testing.assert_allclose(
        result["d_pct_change"].to_numpy(dtype=float),
        [10.0, 10.0],
        rtol=0.0,
        atol=1e-12,
    )


def test_future_d_full_current_base_uses_hash_bound_partial_release(
    tmp_path: Path,
) -> None:
    """Exercise the real builder, not a pre-filled synthetic scorer frame."""

    for relative in (
        "models/decision_three_engines",
        "data/decision_three_engines",
        "data/auction_v3/promotion_prior",
    ):
        shutil.copytree(ROOT / relative, tmp_path / relative)

    codes = [
        "605398.SH",
        "605399.SH",
        "605488.SH",
        "605500.SH",
        "605555.SH",
        "605567.SH",
        "605577.SH",
        "605580.SH",
        "605588.SH",
        "605589.SH",
        "605598.SH",
        "605599.SH",
    ]
    dates = [
        date.strftime("%Y%m%d")
        for date in pd.bdate_range("2026-07-20", "2026-08-21")
    ]
    signal_date = dates[-1]
    previous = {code: 10.0 + index * 0.5 for index, code in enumerate(codes)}
    for date_index, trade_date in enumerate(dates):
        daily_rows: list[dict[str, object]] = []
        limit_rows: list[dict[str, object]] = []
        for code_index, code in enumerate(codes):
            pre_close = previous[code]
            up_limit = round(pre_close * 1.10 + 1e-10, 2)
            streak = 2 if code_index % 2 == 0 else 3
            close = (
                up_limit
                if date_index >= len(dates) - streak
                else round(pre_close * 1.002, 2)
            )
            open_price = round(pre_close * 1.001, 2)
            high = max(close, open_price)
            low = round(min(close, open_price) * 0.995, 2)
            daily_rows.append(
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "pre_close": pre_close,
                    "vol": 1_000_000 + code_index * 10_000,
                    "amount": 50_000_000 + code_index * 100_000,
                    "pct_chg": 100.0 * (close / pre_close - 1.0),
                }
            )
            limit_rows.append(
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "up_limit": up_limit,
                    "down_limit": round(pre_close * 0.90, 2),
                }
            )
            previous[code] = close
        market_root = (
            tmp_path
            / "data"
            / "market"
            / "raw"
            / trade_date[:4]
            / trade_date
        )
        market_root.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(daily_rows).to_csv(market_root / "daily.csv", index=False)
        pd.DataFrame(limit_rows).to_csv(
            market_root / "stk_limit.csv", index=False
        )

    candidates = pd.DataFrame(
        {
            "ts_code": codes,
            "name": [f"TEST{index}" for index in range(len(codes))],
            "industry": [f"I{index % 3}" for index in range(len(codes))],
            "source_rank": range(1, len(codes) + 1),
            "decision_limit_pct": 10.0,
        }
    )
    engine = _engine(tmp_path)
    base = engine.build_three_engine_inference_pool(
        signal_date,
        candidates,
    )
    assert len(base) == 12
    assert base["stage"].value_counts().to_dict() == {"2→3": 6, "3→4": 6}

    promotion_context = [
        "five_year_pre_streak_1d_return",
        "five_year_pre_streak_3d_return",
        "five_year_pre_streak_volatility",
        "five_year_pre_streak_limit_up_count",
        "five_year_recent_limit_up_count",
        "five_year_days_since_prior_limit_up",
        "five_year_streak_runup",
        "five_year_price_log",
    ]
    runtime_market = [
        "returns_1d",
        "high_low_range",
        "candle_body",
        "gap_open",
        "vol",
        "volume_ratio",
        "volatility_5d",
        "volatility_10d",
        "volatility_20d",
        "atr",
        "ret_2d",
        "ret_5d",
        "ret_10d",
        "bid_ask_proxy",
        "spread_proxy",
    ]
    assert base[promotion_context].notna().all().all()
    assert base[runtime_market].notna().all().all()
    for row in base.itertuples(index=False):
        expected_context = engine._promotion_source_context_features(
            signal_date, row.ts_code, dates
        )
        expected_market = three_engine_d_close_market_features(
            engine, signal_date, row.ts_code, dates
        )
        for name in promotion_context:
            assert np.isclose(getattr(row, name), expected_context[name])
            for name in runtime_market:
                assert np.isclose(getattr(row, name), expected_market[name])

    validation_path = (
        tmp_path / "models/decision_three_engines/validation_latest.json"
    )
    try:
        copied_artifacts = load_three_engine_artifacts(
            validation_path,
            root=tmp_path,
        )
        copied_promotion_ready = (
            copied_artifacts.metadata["promotion"]["status"] == "READY"
        )
    except ThreeEngineArtifactError:
        copied_promotion_ready = False

    result = apply_three_engine_runtime(
        engine,
        base.copy(),
        base.copy(),
        signal_date,
    )
    if not copied_promotion_ready:
        assert set(result["three_engine_runtime_status"]) == {
            "NOT_READY_PROMOTION"
        }
        assert result["top10_selected"].eq(0).all()
        for column in (
            "promotion_rank",
            "predicted_promotion_probability",
            "big_loss_safety_rank",
            "predicted_big_loss_probability",
            "profit_rank",
            "predicted_profit_probability",
        ):
            assert result[column].isna().all()
        return

    assert set(result["three_engine_runtime_status"]) == {
        "PARTIAL_MODELS_NOT_READY"
    }
    assert set(result["promotion_model_status"]) == {"READY"}
    assert set(result["big_loss_model_status"]) == {
        "NOT_READY_VALIDATION_GATE"
    }
    assert set(result["profit_model_status"]) == {
        "NOT_READY_VALIDATION_GATE"
    }
    assert result["three_engine_runtime_feature_gate_passed"].eq(1).all()
    assert result["three_engine_runtime_artifacts_hash_bound"].eq(1).all()
    assert int(result["top10_selected"].sum()) == 10
    assert sorted(result["promotion_rank"].astype(int)) == list(range(1, 13))
    assert result["big_loss_safety_rank"].isna().all()
    assert result["predicted_big_loss_probability"].isna().all()
    assert result["profit_rank"].isna().all()
    assert result["predicted_profit_probability"].isna().all()
    # Gate pass counts are evidence produced by each immutable training run;
    # they may legitimately change as the five-year ledger advances.  The
    # strict artifact loader independently recomputes them from gate_checks,
    # so this runtime test verifies exact propagation from that validated
    # metadata instead of pinning one historical run's scores.
    for head in ("promotion", "big_loss", "profit"):
        metadata = copied_artifacts.metadata[head]
        gate_checks = copied_artifacts.validation["heads"][head]["gate_checks"]
        pass_count = metadata["validation_gate_pass_count"]
        total_count = metadata["validation_gate_total_count"]
        score = metadata["validation_gate_score_pct"]
        assert set(gate_checks) == set(THREE_ENGINE_VALIDATION_GATE_NAMES)
        assert total_count == len(THREE_ENGINE_VALIDATION_GATE_NAMES)
        assert pass_count == sum(
            value is True
            for value in gate_checks.values()
        )
        assert set(copied_artifacts.validation["heads"][head]["gate_failures"]) == {
            name for name, passed in gate_checks.items() if passed is False
        }
        assert score == round(100.0 * pass_count / total_count, 1)
        if metadata["status"] == "READY":
            assert pass_count == total_count
            assert score == 100.0
        else:
            assert metadata["status"].startswith("NOT_READY_")
            assert pass_count < total_count
            assert score < 100.0
        assert set(result[f"{head}_validation_gate_pass_count"]) == {pass_count}
        assert set(result[f"{head}_validation_gate_total_count"]) == {total_count}
        assert set(result[f"{head}_validation_gate_score_pct"]) == {score}
    shadow_metadata = copied_artifacts.metadata["p_fill_shadow"]
    shadow_gate_checks = copied_artifacts.validation["heads"]["p_fill_shadow"][
        "gate_checks"
    ]
    shadow_pass_count = shadow_metadata["validation_gate_pass_count"]
    shadow_total_count = shadow_metadata["validation_gate_total_count"]
    shadow_score = shadow_metadata["validation_gate_score_pct"]
    assert set(shadow_gate_checks) == set(THREE_ENGINE_VALIDATION_GATE_NAMES)
    assert shadow_total_count == len(THREE_ENGINE_VALIDATION_GATE_NAMES)
    assert shadow_pass_count == sum(
        value is True
        for value in shadow_gate_checks.values()
    )
    assert set(
        copied_artifacts.validation["heads"]["p_fill_shadow"]["gate_failures"]
    ) == {
        name for name, passed in shadow_gate_checks.items() if passed is False
    }
    assert shadow_score == round(
        100.0 * shadow_pass_count / shadow_total_count,
        1,
    )
    if shadow_metadata["status"] == "SHADOW_READY":
        assert shadow_pass_count == shadow_total_count
        assert shadow_score == 100.0
    else:
        assert shadow_metadata["status"].startswith((
            "SHADOW_NOT_READY",
            "NOT_READY_",
        ))
        assert shadow_pass_count < shadow_total_count
        assert shadow_score < 100.0
    assert set(result["p_fill_shadow_validation_gate_pass_count"]) == {
        shadow_pass_count
    }
    assert set(result["p_fill_shadow_validation_gate_total_count"]) == {
        shadow_total_count
    }
    assert set(result["p_fill_shadow_validation_gate_score_pct"]) == {
        shadow_score
    }


def test_force_cannot_rewrite_complete_historical_dated_prediction(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    signal_date = "20260105"
    dated = engine.config.prediction_root / f"pred_{signal_date}.csv"
    pool = _pool(1)
    pool["signal_date"] = signal_date
    pool["ts_code"] = "600000.SH"
    frozen = _official_snapshot(pool).rows
    frozen["expected_buy_date"] = "20260106"
    frozen["three_engine_runtime_status"] = "READY"
    frozen["three_engine_runtime_feature_gate_passed"] = 1
    frozen["three_engine_runtime_artifacts_hash_bound"] = 1
    frozen["three_engine_runtime_input_pool_complete"] = 1
    frozen["three_engine_runtime_failure"] = ""
    frozen.to_csv(dated, index=False)
    before = dated.read_bytes()
    candidates = pd.DataFrame(
        [{"ts_code": "600001.SH", "verify_date": "20260106"}]
    )
    with mock.patch.object(
        engine,
        "_prediction_revision_allowed",
        return_value=False,
    ), mock.patch.object(
        engine,
        "_current_base",
        side_effect=AssertionError("historical prediction must not be rebuilt"),
    ):
        result = engine.build_prediction(
            signal_date,
            candidates,
            bundle=None,
            backtest_metrics={},
            force=True,
        )

    assert dated.read_bytes() == before
    assert result["ts_code"].tolist() == ["600000.SH"]
    assert (engine.config.prediction_root / "pred_latest.csv").is_file()
