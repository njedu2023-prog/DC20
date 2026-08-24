from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


WORK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK_ROOT))

import benchmark  # noqa: E402


def test_label_cutoff_is_strict_and_rejects_invalid_dates() -> None:
    frame = pd.DataFrame(
        {
            "scheduled_exit_date": ["20250102", "20250103", "20250106"],
        }
    )
    assert benchmark.label_available_mask(frame, "20250103").tolist() == [True, False, False]

    invalid = pd.DataFrame({"scheduled_exit_date": ["2025-01-02"]})
    with pytest.raises(benchmark.BenchmarkError, match="invalid scheduled exit date"):
        benchmark.label_available_mask(invalid, "20250103")


def test_same_day_and_future_labels_cannot_change_fitted_model() -> None:
    rows = 4000
    available_rows = 3200
    feature = np.linspace(-3.0, 3.0, rows)
    fill = np.where(np.arange(rows) % 8 == 0, 0, 1).astype(float)
    profit = np.where(np.arange(rows) % 3 == 0, 1, 0).astype(float)
    profit[fill == 0] = np.nan
    frame = pd.DataFrame(
        {
            "scheduled_exit_date": np.where(
                np.arange(rows) < available_rows,
                "20241231",
                "20250102",
            ),
            "x": feature,
            "public_market_buyable_proxy": fill,
            "conditional_profit_hit": profit,
        }
    )
    first = benchmark.fit_two_stage(
        frame,
        feature_columns=["x"],
        kind="lr",
        label_available_before="20250102",
    )

    poisoned = frame.copy()
    unavailable = poisoned["scheduled_exit_date"].ge("20250102")
    poisoned.loc[unavailable, "public_market_buyable_proxy"] = 1 - poisoned.loc[
        unavailable, "public_market_buyable_proxy"
    ]
    poisoned.loc[unavailable, "conditional_profit_hit"] = np.where(
        poisoned.loc[unavailable, "public_market_buyable_proxy"].eq(1),
        1.0,
        np.nan,
    )
    second = benchmark.fit_two_stage(
        poisoned,
        feature_columns=["x"],
        kind="lr",
        label_available_before="20250102",
    )

    query = pd.DataFrame({"x": [-2.0, 0.0, 2.0]})
    for first_values, second_values in zip(first.predict(query), second.predict(query)):
        np.testing.assert_allclose(first_values, second_values, rtol=0, atol=0)
    assert first.training_audit == second.training_audit
    assert first.training_audit["maximum_used_scheduled_exit_date"] == "20241231"


def test_top2_policy_keeps_two_fixed_slots_and_cash() -> None:
    panel = pd.DataFrame(
        {
            "signal_date": ["20250102", "20250103", "20250103"],
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "stage": [2, 2, 3],
            "predicted_executable_profit_probability": [0.9, 0.8, 0.7],
            "predicted_fill_probability": [0.9, 0.8, 0.7],
            "predicted_profit_given_fill_probability": [1.0, 1.0, 1.0],
            "strategy_slot_net_return": [0.10, 0.04, -0.02],
            "strategy_slot_net_return_2x_cost": [0.09, 0.03, -0.03],
            "public_market_buyable_proxy": [1, 1, 0],
            "conditional_big_loss_hit": [0, 0, np.nan],
            "executable_profit_proxy_hit": [1, 1, 0],
        }
    )
    daily = benchmark.top2_daily(panel)
    first = daily.loc[daily["signal_date"].eq("20250102")].iloc[0]
    second = daily.loc[daily["signal_date"].eq("20250103")].iloc[0]
    assert first["daily_top2_net_return"] == pytest.approx(0.05)
    assert first["daily_top2_profit_rate"] == pytest.approx(0.5)
    assert first["cash_slots"] == 1
    assert second["daily_top2_net_return"] == pytest.approx(0.01)
    assert second["daily_top2_profit_rate"] == pytest.approx(0.5)
    assert second["cash_slots"] == 0

    diagnostics = benchmark.top2_risk_cost_diagnostics(panel)
    assert diagnostics["fixed_slots"] == 4
    assert diagnostics["cash_slots"] == 1
    assert diagnostics["mean_daily_top2_net_return_base_cost"] == pytest.approx(0.03)
    assert diagnostics["mean_daily_top2_net_return_double_cost"] == pytest.approx(0.0225)
    assert diagnostics["top2_slot_big_loss_rate"] == 0
    assert set(diagnostics["stage_breakdown_selected_candidates"]) == {"2_to_3", "3_to_4"}


def test_block_bootstrap_is_reproducible() -> None:
    values = np.linspace(-0.02, 0.03, 50)
    assert benchmark.block_bootstrap_ci(values, seed=1234) == benchmark.block_bootstrap_ci(
        values,
        seed=1234,
    )


def test_generated_artifacts_enforce_product_and_reject_release() -> None:
    report_path = WORK_ROOT / "outputs/benchmark_report.json"
    predictions_path = WORK_ROOT / "outputs/benchmark_predictions.csv.gz"
    if not report_path.is_file() or not predictions_path.is_file():
        pytest.skip("benchmark artifacts have not been generated")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    predictions = pd.read_csv(predictions_path, low_memory=False)
    np.testing.assert_allclose(
        predictions["predicted_executable_profit_probability"],
        predictions["predicted_fill_probability"]
        * predictions["predicted_profit_given_fill_probability"],
        rtol=0,
        atol=1e-15,
    )
    assert report["status"] == "RESEARCH_ONLY_NO_RELEASE"
    assert report["official_trade_action_allowed"] is False
    assert report["retrospective_confirmation_window_has_been_viewed"] is True
    assert report["independent_untouched_confirmation_available"] is False
    assert report["forward_release_evidence_available"] is False
    assert report["joint_probability_identity_enforced"] is True
    assert all(
        item["decision"] == "REJECT_NOT_CONFIRMED"
        for item in report["decisions"].values()
    )
    for split, audits in report.get("training_audits", {}).items():
        assert audits, f"missing training audit for {split}"
        for audit in audits.values():
            assert audit["maximum_used_scheduled_exit_date"] < audit["cutoff_exclusive"]

    challenger_audit = json.loads(
        (WORK_ROOT / "outputs/internal_forward_challenger_audit.json").read_text(encoding="utf-8")
    )
    challenger_path = WORK_ROOT / challenger_audit["artifact"]["path"]
    assert challenger_audit["status"] == "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY"
    assert challenger_audit["official_trade_action_allowed"] is False
    assert challenger_audit["front_end_rank_allowed"] is False
    assert challenger_audit["retrospective_confirmation_window_has_been_viewed"] is True
    assert challenger_audit["independent_untouched_confirmation_available"] is False
    assert challenger_audit["forward_release_evidence_available"] is False
    assert challenger_audit["challenger_selection_used_viewed_retrospective_results"] is True
    assert challenger_audit["deterministic_refit"]["exact_prediction_arrays_equal"] is True
    assert challenger_audit["deterministic_refit"]["exact_pickle_bytes_equal"] is True
    assert hashlib.sha256(challenger_path.read_bytes()).hexdigest() == challenger_audit["artifact"]["sha256"]
