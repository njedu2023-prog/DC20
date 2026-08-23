from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from top10decision.auction_v3.config import AuctionV3Config
from top10decision.auction_v3.engine import (
    THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS,
    AuctionV3Engine,
)
from top10decision.decision.three_engine_models import ThreeEngineArtifactError


ROOT = Path(__file__).resolve().parents[1]


def _engine(tmp_path: Path) -> AuctionV3Engine:
    return AuctionV3Engine(AuctionV3Config(root=tmp_path))


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
    rows["top10_members_sha256"] = "b" * 64
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


def test_official_a_scores_complete_pool_and_freezes_same_top10_set(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    pool = _pool(12)
    legacy = _legacy_scored(pool)
    snapshot = _official_snapshot(pool)

    with mock.patch(
        "top10decision.auction_v3.engine.load_three_engine_artifacts",
        return_value=object(),
    ) as loader, mock.patch(
        "top10decision.auction_v3.engine.score_three_engine_snapshot",
        return_value=snapshot,
    ) as scorer:
        result = engine._apply_three_engine_runtime(legacy, pool, "20260820")

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
        "top10decision.auction_v3.engine.load_three_engine_artifacts",
        return_value=object(),
    ), mock.patch(
        "top10decision.auction_v3.engine.score_three_engine_snapshot",
        return_value=snapshot,
    ):
        result = engine._apply_three_engine_runtime(legacy, pool, "20260820")

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
        "top10decision.auction_v3.engine.load_three_engine_artifacts",
        side_effect=ThreeEngineArtifactError("promotion artifact hash mismatch"),
    ):
        result = engine._apply_three_engine_runtime(legacy, pool, "20260820")

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
        "top10decision.auction_v3.engine.load_three_engine_artifacts",
        return_value=object(),
    ), mock.patch(
        "top10decision.auction_v3.engine.score_three_engine_snapshot",
        return_value=snapshot,
    ):
        result = engine._apply_three_engine_runtime(legacy, pool, "20260820")

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

    features = engine._three_engine_d_close_market_features(dates[-1], code, dates)
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
    base = engine._current_base(signal_date, candidates)
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
        expected_market = engine._three_engine_d_close_market_features(
            signal_date, row.ts_code, dates
        )
        for name in promotion_context:
            assert np.isclose(getattr(row, name), expected_context[name])
        for name in runtime_market:
            assert np.isclose(getattr(row, name), expected_market[name])

    result = engine._apply_three_engine_runtime(
        base.copy(),
        base.copy(),
        signal_date,
    )
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
    for head, expected in {
        "promotion": (26, 26, 100.0),
        "big_loss": (17, 26, 65.4),
        "profit": (20, 26, 76.9),
    }.items():
        pass_count, total_count, score = expected
        assert set(result[f"{head}_validation_gate_pass_count"]) == {pass_count}
        assert set(result[f"{head}_validation_gate_total_count"]) == {total_count}
        assert set(result[f"{head}_validation_gate_score_pct"]) == {score}
    assert set(result["p_fill_shadow_validation_gate_pass_count"]) == {26}
    assert set(result["p_fill_shadow_validation_gate_total_count"]) == {26}
    assert set(result["p_fill_shadow_validation_gate_score_pct"]) == {100.0}


def test_force_cannot_rewrite_historical_dated_prediction(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    signal_date = "20260105"
    dated = engine.config.prediction_root / f"pred_{signal_date}.csv"
    frozen = pd.DataFrame(
        [
            {
                "signal_date": signal_date,
                "expected_buy_date": "20260106",
                "ts_code": "600000.SH",
                "promotion_rank": 1,
            }
        ]
    )
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
