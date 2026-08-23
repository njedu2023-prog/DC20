from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.train_three_engine_models import (
    _load_runtime_ledger_contract,
    write_training_artifacts,
)
from top10decision.decision.three_engine_models import (
    FORBIDDEN_FEATURE_COLUMNS,
    LoadedThreeEngineArtifacts,
    RUNTIME_ALIGNED_MARKET_FEATURES,
    RUNTIME_FEATURE_CONTRACT_VERSION,
    RUNTIME_PROMOTION_PRIOR_FEATURES,
    THREE_ENGINE_FEATURE_CONTRACT,
    ThreeEngineArtifactError,
    ThreeEngineConfig,
    attach_runtime_promotion_priors,
    date_balanced_weights,
    load_three_engine_artifacts,
    model_artifact_payload,
    normalize_supervised_ledger,
    score_three_engine_snapshot,
    train_three_engine_models,
)


def _synthetic_ledger(days: int = 120, pool: int = 14) -> pd.DataFrame:
    calendar = pd.bdate_range("2024-01-02", periods=days + 2)
    rows: list[dict[str, object]] = []
    for day in range(days):
        signal_date = calendar[day].strftime("%Y%m%d")
        buy_date = calendar[day + 1].strftime("%Y%m%d")
        exit_date = calendar[day + 2].strftime("%Y%m%d")
        for member in range(pool):
            promotion_phase = ((member * 5 + day * 3) % pool) / (pool - 1)
            promotion_signal = promotion_phase * 2.0 - 1.0
            return_phase = ((member * 7 + day * 5) % pool) / (pool - 1)
            return_signal = return_phase * 2.0 - 1.0
            promotion_hit = int(promotion_signal > 0.05)
            market_fill = int((member + day) % 6 != 0)
            net_return = (
                -0.055
                if return_signal < -0.30
                else 0.045
                if return_signal > 0.0
                else -0.010
            )
            rows.append(
                {
                    "signal_date": signal_date,
                    "buy_date": buy_date,
                    "target_exit_date": exit_date,
                    "ts_code": f"{member + 1:06d}.SZ",
                    "stage": 2 if member % 2 == 0 else 3,
                    "board": "SZ_MAIN",
                    "mechanism_limit_pct": 10.0,
                    "promotion_hit": promotion_hit,
                    "market_fill": market_fill,
                    "big_loss_hit": (
                        int(net_return <= -0.03) if market_fill else np.nan
                    ),
                    "profit_hit": int(net_return > 0.0) if market_fill else np.nan,
                    "net_return": net_return if market_fill else np.nan,
                    "focus_pool_size": pool,
                    "stage2_pool_size": (pool + 1) // 2,
                    "stage3_pool_size": pool // 2,
                    "stage_pool_share": 0.5,
                    "d_pct_change": 10.0,
                    "d_turnover_pct": 4.0 + promotion_phase * 8.0,
                    "d_amount": 1.0e8 + member * 1.0e6,
                    "returns_1d": promotion_signal,
                    "ret_5d": return_signal,
                    "volatility_5d": abs(return_signal) + 0.01,
                    "five_year_promotion_signal": promotion_signal,
                    "five_year_return_signal": return_signal,
                    "five_year_stock_prior_rate": promotion_phase,
                    # These future/output columns must never enter a feature builder.
                    "t_open": 10.0 + member,
                    "predicted_profit_probability": 0.99,
                    "promotion_rank": 1,
                }
            )
    return pd.DataFrame(rows)


def _test_config() -> ThreeEngineConfig:
    return ThreeEngineConfig(
        promotion_warmup_dates=45,
        outcome_warmup_dates=20,
        outer_block_dates=15,
        embargo_dates=1,
        final_holdout_dates=20,
        inner_fit_fraction=0.50,
        inner_calibration_fraction=0.20,
        minimum_inner_fit_dates=8,
        minimum_inner_calibration_dates=3,
        minimum_inner_selection_dates=3,
        minimum_fit_rows=30,
        minimum_class_rows=2,
        minimum_history_dates=30,
        minimum_history_rows=200,
        minimum_outcome_history_dates=20,
        minimum_outcome_history_rows=100,
        minimum_oos_dates=10,
        minimum_oos_rows=50,
        minimum_stage_oos_rows=5,
        maximum_ece=1.0,
        minimum_auc=0.0,
        minimum_brier_improvement=-1.0,
        bootstrap_samples=20,
        bootstrap_block_dates=2,
        model_kinds=("lr",),
        calibration_methods=("identity",),
        release_mode=False,
    )


def test_three_heads_use_chronological_oof_top10_without_cross_head_features() -> None:
    result = train_three_engine_models(_synthetic_ledger(), config=_test_config())

    promotion_oof = result.promotion.oof
    assert not promotion_oof.empty
    assert set(promotion_oof.groupby("signal_date").size()) == {14}
    assert set(result.oof_top10.groupby("signal_date").size()) == {10}
    for _, group in result.oof_top10.groupby("signal_date"):
        assert group["promotion_rank"].astype(int).tolist() == list(range(1, 11))
        assert group["top10_members_sha256"].nunique() == 1

    top10_keys = set(
        result.oof_top10[["signal_date", "ts_code"]].itertuples(
            index=False, name=None
        )
    )
    for head in (result.big_loss, result.profit, result.p_fill_shadow):
        assert not head.oof.empty
        head_keys = set(
            head.oof[["signal_date", "ts_code"]].itertuples(
                index=False, name=None
            )
        )
        assert head_keys <= top10_keys
        assert set(head.oof.groupby("signal_date").size()) == {10}
        train_end = head.oof[f"{head.spec.name}_oof_train_end"].astype(str)
        assert train_end.lt(head.oof["signal_date"].astype(str)).all()

    assert result.big_loss.production_bundle is not None
    assert result.profit.production_bundle is not None
    assert result.big_loss.production_bundle.model is not result.profit.production_bundle.model
    assert (
        result.big_loss.production_bundle.calibrator
        is not result.profit.production_bundle.calibrator
    )
    feature_names = set(result.feature_builder.feature_names)
    assert not feature_names.intersection(FORBIDDEN_FEATURE_COLUMNS)
    assert not any(name.startswith("predicted_") for name in feature_names)
    assert not any(name.endswith("_rank") for name in feature_names)
    assert "five_year_promotion_signal" not in feature_names
    assert result.validation["independence"]["cross_head_output_features_absent"]


def test_final_holdout_proxy_contract_and_stage_audit_are_explicit() -> None:
    result = train_three_engine_models(_synthetic_ledger(), config=_test_config())
    validation = result.validation

    assert validation["feature_contract"] == THREE_ENGINE_FEATURE_CONTRACT
    assert validation["label_contract"]["price_source"] == (
        "public-market/exchange daily-bar open price proxy"
    )
    assert validation["label_contract"]["actual_order_fill_observed"] is False
    assert validation["label_contract"]["actual_execution_claimed"] is False
    for head in ("promotion", "big_loss", "profit"):
        audit = validation["heads"][head]
        assert set(audit["stage_breakdown"]) == {"2_to_3", "3_to_4"}
        assert audit["final_independent_holdout"]["calendar_dates"] == 20
        assert audit["final_independent_holdout"]["model_refit_within_holdout"] is False
        assert audit["production"]["constant_rank_forbidden"] is True
        assert audit["rank_variation"]["nonconstant_date_fraction"] == 1.0
    assert validation["heads"]["big_loss"]["training_scope"] == (
        "historical_promotion_oof_top10_market_fill_proxy_eq_1"
    )
    assert validation["heads"]["profit"]["training_scope"] == (
        "historical_promotion_oof_top10_market_fill_proxy_eq_1"
    )


def test_single_class_promotion_fails_closed_without_constant_artifact() -> None:
    ledger = _synthetic_ledger()
    ledger["promotion_hit"] = 0
    result = train_three_engine_models(ledger, config=_test_config())

    assert result.promotion.oof.empty
    assert result.promotion.production_bundle is None
    assert result.oof_top10.empty
    assert result.validation["ready"] is False
    assert result.validation["heads"]["promotion"]["promoted"] is False
    assert "nonconstant_production_model" in result.validation["heads"][
        "promotion"
    ]["gate_failures"]
    payload = model_artifact_payload(result, "promotion")
    assert payload["bundle"] is None
    assert payload["status"].startswith("NOT_READY")
    assert payload["model_version"] == ""


def test_date_balancing_gives_each_date_equal_total_weight() -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20240102"] * 2 + ["20240103"] * 5,
            "ts_code": [f"{index:06d}.SZ" for index in range(7)],
        }
    )
    weights = date_balanced_weights(frame)
    totals = pd.Series(weights).groupby(frame["signal_date"]).sum()
    assert np.isclose(totals.iloc[0], totals.iloc[1])


def test_normalizer_rejects_duplicate_keys_and_keeps_proxy_labels_null() -> None:
    ledger = _synthetic_ledger(days=3, pool=4)
    normalized = normalize_supervised_ledger(ledger)
    nonfill = normalized[normalized["market_fill"].eq(0)]
    assert nonfill["big_loss_hit"].isna().all()
    assert nonfill["profit_hit"].isna().all()

    duplicate = pd.concat([ledger, ledger.head(1)], ignore_index=True)
    try:
        normalize_supervised_ledger(duplicate)
    except ValueError as exc:
        assert "duplicate key" in str(exc)
    else:
        raise AssertionError("duplicate training keys must fail closed")


def _ready_loaded_artifacts(
    result,
    runtime_prior_ledger: pd.DataFrame,
) -> LoadedThreeEngineArtifacts:
    payloads = {
        head: model_artifact_payload(result, head)
        for head in ("promotion", "big_loss", "profit", "p_fill_shadow")
    }
    metadata: dict[str, dict[str, object]] = {}
    for index, (head, payload) in enumerate(payloads.items(), start=1):
        bundle = payload["bundle"]
        assert bundle is not None
        status = "READY" if head != "p_fill_shadow" else "SHADOW_READY"
        payload["status"] = status
        payload["promoted"] = head != "p_fill_shadow"
        payload["model_version"] = f"{head}_test_v1"
        payload["model_as_of_date"] = bundle.trained_signal_end
        metadata[head] = {
            "status": status,
            "version": payload["model_version"],
            "as_of_date": payload["model_as_of_date"],
            "artifact_sha256": f"{index:x}" * 64,
            "path": "",
            "validation_gate_pass_count": 26,
            "validation_gate_total_count": 26,
            "validation_gate_score_pct": 100.0,
        }
    return LoadedThreeEngineArtifacts(
        root=Path("."),
        validation_path=Path("validation.json"),
        validation={},
        payloads=payloads,
        metadata=metadata,
        runtime_ledger_path=Path("ledger.csv.gz"),
        runtime_ledger_sha256="a" * 64,
        runtime_prior_ledger=runtime_prior_ledger[
            ["signal_date", "stage", "board", "promotion_hit"]
        ].copy(),
    )


def test_runtime_scores_a_full_pool_then_b_c_only_on_frozen_top10() -> None:
    ledger = _synthetic_ledger()
    result = train_three_engine_models(ledger, config=_test_config())
    loaded = _ready_loaded_artifacts(result, ledger)
    last_date = ledger["signal_date"].max()
    signal_date = (
        pd.Timestamp(last_date) + pd.offsets.BDay(1)
    ).strftime("%Y%m%d")
    candidates = ledger[ledger["signal_date"].eq(last_date)].copy()
    candidates["signal_date"] = signal_date

    scored = score_three_engine_snapshot(
        candidates,
        loaded,
        signal_date=signal_date,
    )

    assert scored.status == "READY"
    assert scored.promotion_pool_size == 14
    assert scored.rows["top10_selected"].sum() == 10
    selected = scored.rows[scored.rows["top10_selected"].eq(1)]
    unselected = scored.rows[scored.rows["top10_selected"].eq(0)]
    for rank in ("promotion_rank", "big_loss_safety_rank", "profit_rank"):
        assert sorted(selected[rank].astype(int)) == list(range(1, 11))
    assert unselected["big_loss_safety_rank"].isna().all()
    assert unselected["profit_rank"].isna().all()
    assert scored.rows["feature_snapshot_sha256"].nunique() == 1
    assert scored.rows["top10_members_sha256"].nunique() == 1
    for head in ("promotion", "big_loss", "profit"):
        assert scored.rows[f"{head}_model_status"].eq("READY").all()
        assert scored.rows[f"{head}_model_artifact_sha256"].str.len().eq(64).all()
        assert scored.rows[f"{head}_validation_gate_pass_count"].eq(26).all()
        assert scored.rows[f"{head}_validation_gate_total_count"].eq(26).all()
        assert scored.rows[f"{head}_validation_gate_score_pct"].eq(100.0).all()
    assert scored.rows["p_fill_shadow_validation_gate_pass_count"].eq(26).all()
    assert scored.rows["p_fill_shadow_validation_gate_total_count"].eq(26).all()
    assert scored.rows["p_fill_shadow_validation_gate_score_pct"].eq(100.0).all()

    missing = score_three_engine_snapshot(
        candidates.drop(columns=["ret_5d"]),
        loaded,
        signal_date=signal_date,
    )
    assert missing.status == "NOT_READY_PROMOTION"
    assert missing.rows["top10_selected"].sum() == 0
    assert missing.rows["promotion_rank"].isna().all()
    assert missing.diagnostics["missing_feature_columns"] == ["ret_5d"]
    assert missing.diagnostics["runtime_feature_gate_passed"] is False

    loaded.metadata["profit"]["status"] = "NOT_READY_VALIDATION_GATE"
    rescored = score_three_engine_snapshot(
        candidates.assign(
            profit_rank=1,
            predicted_profit_probability=0.99,
            promotion_validation_gate_pass_count=0,
            promotion_validation_gate_total_count=1,
            promotion_validation_gate_score_pct=0.0,
        ),
        loaded,
        signal_date=signal_date,
    )
    assert rescored.status == "PARTIAL_MODELS_NOT_READY"
    assert rescored.rows["profit_rank"].isna().all()
    assert rescored.rows["predicted_profit_probability"].isna().all()
    assert rescored.rows["promotion_validation_gate_pass_count"].eq(26).all()
    assert rescored.rows["promotion_validation_gate_total_count"].eq(26).all()
    assert rescored.rows["promotion_validation_gate_score_pct"].eq(100.0).all()


def _write_runtime_manifest(ledger_path: Path, manifest_path: Path) -> dict:
    contract = {
        "version": RUNTIME_FEATURE_CONTRACT_VERSION,
        "columns": list(RUNTIME_ALIGNED_MARKET_FEATURES),
        "available_by_d_close": True,
        "future_columns_used": [],
    }
    manifest = {
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "runtime_feature_contract": contract,
        "source": {
            "prior_grid_truth_cutoff_rule": "strictly_before_signal_date"
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return contract


def test_runtime_priors_ignore_same_and_future_truth_but_use_earlier_truth() -> None:
    ledger = _synthetic_ledger(days=12, pool=6)
    signal_date = sorted(ledger["signal_date"].unique())[7]
    candidates = ledger[ledger["signal_date"].eq(signal_date)].head(2).copy()
    candidates["stage"] = [2, 3]
    candidates["board"] = "SZ_MAIN"

    baseline = attach_runtime_promotion_priors(
        candidates,
        ledger,
        signal_date=signal_date,
    )
    same_or_future = ledger.copy()
    not_earlier = same_or_future["signal_date"].ge(signal_date)
    same_or_future.loc[not_earlier, "promotion_hit"] = (
        1 - same_or_future.loc[not_earlier, "promotion_hit"].astype(int)
    )
    unchanged = attach_runtime_promotion_priors(
        candidates,
        same_or_future,
        signal_date=signal_date,
    )
    np.testing.assert_allclose(
        baseline[list(RUNTIME_PROMOTION_PRIOR_FEATURES)].to_numpy(dtype=float),
        unchanged[list(RUNTIME_PROMOTION_PRIOR_FEATURES)].to_numpy(dtype=float),
        rtol=0.0,
        atol=0.0,
    )

    earlier = ledger.copy()
    earlier_index = earlier.index[
        earlier["signal_date"].lt(signal_date)
        & earlier["stage"].eq(2)
        & earlier["board"].eq("SZ_MAIN")
    ][0]
    earlier.at[earlier_index, "promotion_hit"] = (
        1 - int(earlier.at[earlier_index, "promotion_hit"])
    )
    changed = attach_runtime_promotion_priors(
        candidates,
        earlier,
        signal_date=signal_date,
    )
    assert not np.allclose(
        baseline[list(RUNTIME_PROMOTION_PRIOR_FEATURES)].to_numpy(dtype=float),
        changed[list(RUNTIME_PROMOTION_PRIOR_FEATURES)].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-15,
    )


def test_hash_bound_loader_rejects_tampered_ledger_and_joblib(
    tmp_path: Path,
) -> None:
    ledger = _synthetic_ledger()
    result = train_three_engine_models(ledger, config=_test_config())
    ledger_path = tmp_path / "ledger.csv.gz"
    ledger.to_csv(ledger_path, index=False, compression="gzip")
    model_dir = tmp_path / "models"
    validation_path = model_dir / "validation.json"
    oof_path = tmp_path / "oof.csv.gz"
    manifest_path = tmp_path / "manifest.json"
    runtime_contract = _write_runtime_manifest(ledger_path, manifest_path)
    write_training_artifacts(
        result,
        ledger_path=ledger_path,
        model_dir=model_dir,
        validation_path=validation_path,
        oof_path=oof_path,
        ledger_manifest_path=manifest_path,
        runtime_feature_contract=runtime_contract,
    )

    loaded = load_three_engine_artifacts(validation_path, root=tmp_path)
    assert set(loaded.payloads) == {
        "promotion",
        "big_loss",
        "profit",
        "p_fill_shadow",
    }
    assert loaded.runtime_ledger_sha256 == hashlib.sha256(
        ledger_path.read_bytes()
    ).hexdigest()
    assert not loaded.runtime_prior_ledger.empty

    validation_bytes = validation_path.read_bytes()
    validation = json.loads(validation_bytes)
    for head in ("promotion", "big_loss", "profit", "p_fill_shadow"):
        checks = validation["heads"][head]["gate_checks"]
        assert checks and all(type(value) is bool for value in checks.values())
        expected_pass = sum(value is True for value in checks.values())
        assert loaded.metadata[head]["validation_gate_pass_count"] == expected_pass
        assert loaded.metadata[head]["validation_gate_total_count"] == len(checks)
        assert loaded.metadata[head]["validation_gate_score_pct"] == round(
            100.0 * expected_pass / len(checks), 1
        )

    # A claimed summary is deliberately ignored; only the checked boolean map
    # can produce the runtime/UI score.
    validation["heads"]["promotion"]["validation_gate_score_pct"] = 0.0
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    reloaded = load_three_engine_artifacts(validation_path, root=tmp_path)
    assert reloaded.metadata["promotion"]["validation_gate_score_pct"] == round(
        100.0
        * sum(
            value is True
            for value in validation["heads"]["promotion"]["gate_checks"].values()
        )
        / len(validation["heads"]["promotion"]["gate_checks"]),
        1,
    )

    validation["heads"]["promotion"]["gate_checks"] = {}
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(ThreeEngineArtifactError, match="nonempty booleans"):
        load_three_engine_artifacts(validation_path, root=tmp_path)

    validation = json.loads(validation_bytes)
    first_gate = next(iter(validation["heads"]["promotion"]["gate_checks"]))
    validation["heads"]["promotion"]["gate_checks"][first_gate] = 1
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(ThreeEngineArtifactError, match="nonempty booleans"):
        load_three_engine_artifacts(validation_path, root=tmp_path)
    validation_path.write_bytes(validation_bytes)

    original_ledger = ledger_path.read_bytes()
    ledger_path.write_bytes(original_ledger + b"tamper")
    with pytest.raises(ThreeEngineArtifactError, match="ledger hash mismatch"):
        load_three_engine_artifacts(validation_path, root=tmp_path)
    ledger_path.write_bytes(original_ledger)

    promotion_path = model_dir / "promotion.joblib"
    with promotion_path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ThreeEngineArtifactError, match="hash mismatch"):
        load_three_engine_artifacts(validation_path, root=tmp_path)


def test_training_cli_binds_exact_runtime_feature_manifest(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.csv.gz"
    ledger_path.write_bytes(b"immutable-ledger")
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "runtime_feature_contract": {
            "version": RUNTIME_FEATURE_CONTRACT_VERSION,
            "columns": list(RUNTIME_ALIGNED_MARKET_FEATURES),
            "available_by_d_close": True,
            "future_columns_used": [],
        },
        "source": {
            "prior_grid_truth_cutoff_rule": "strictly_before_signal_date"
        },
    }
    manifest_path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    assert _load_runtime_ledger_contract(ledger_path, manifest_path) == manifest[
        "runtime_feature_contract"
    ]

    manifest["runtime_feature_contract"]["columns"] = ["returns_1d"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="column inventory drifted"):
        _load_runtime_ledger_contract(ledger_path, manifest_path)
