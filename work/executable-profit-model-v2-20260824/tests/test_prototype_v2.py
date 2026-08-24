from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


WORK = Path(__file__).resolve().parents[1]
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

import prototype_v2 as v2  # noqa: E402


class _ClassStep:
    classes_ = np.asarray(v2.BUCKETS)


class _FixedClassifier:
    named_steps = {"model": _ClassStep()}

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        return np.tile(np.asarray([[0.20, 0.30, 0.50]]), (len(features), 1))


class _FixedRegressor:
    def __init__(self, value: float):
        self.value = value

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.repeat(self.value, len(features))


def _bundle() -> v2.ConditionalBundle:
    return v2.ConditionalBundle(
        candidate="fixed",
        feature_columns=("x",),
        classifier=_FixedClassifier(),
        temperature=1.0,
        mean_regressor=_FixedRegressor(0.02),
        lower_regressor=_FixedRegressor(-0.04),
        fit_start="20260101",
        fit_end="20260110",
        component_start="20260113",
        component_end="20260120",
        final_start="20260123",
        final_end="20260130",
        conditional_profit_base_rate=0.4,
        final_audit={},
    )


def test_joint_probability_is_exact_product_and_respects_both_upper_bounds() -> None:
    frame = pd.DataFrame(
        {
            "x": [1.0, 2.0],
            "frozen_p_fill_probability": [0.80, 0.30],
        }
    )
    scored = v2.score_bundle(_bundle(), frame)
    expected = frame["frozen_p_fill_probability"].to_numpy() * 0.50
    actual = scored["predicted_executable_net_profit_probability"].to_numpy()
    assert np.allclose(actual, expected, atol=1e-15, rtol=0.0)
    assert np.all(actual <= scored["frozen_p_fill_probability"])
    assert np.all(actual <= scored["predicted_conditional_profit_probability"])
    assert "final_calibrator" not in _bundle().__dict__


def test_frozen_p_fill_changes_only_product_component_not_conditional_probability() -> None:
    frame = pd.DataFrame(
        {
            "x": [7.0, 7.0, 7.0],
            "frozen_p_fill_probability": [0.0, 0.40, 1.0],
        }
    )
    scored = v2.score_bundle(_bundle(), frame)
    assert scored["predicted_conditional_profit_probability"].tolist() == [
        0.5,
        0.5,
        0.5,
    ]
    assert np.allclose(
        scored["predicted_executable_net_profit_probability"].to_numpy(),
        [0.0, 0.20, 0.50],
        atol=1e-15,
        rtol=0.0,
    )


def test_primary_joint_probability_precedes_all_tie_break_fields() -> None:
    frame = pd.DataFrame(
        [
            {
                "signal_date": "20260105",
                "ts_code": "000001.SZ",
                "predicted_executable_net_profit_probability": 0.39,
                "expected_net_return_lcb": 1.0,
                "predicted_conditional_big_loss_probability": 0.0,
            },
            {
                "signal_date": "20260105",
                "ts_code": "999999.SH",
                "predicted_executable_net_profit_probability": 0.40,
                "expected_net_return_lcb": -1.0,
                "predicted_conditional_big_loss_probability": 1.0,
            },
        ]
    )
    ranked = v2.freeze_top2(frame).sort_values("executable_profit_shadow_rank")
    assert ranked["ts_code"].tolist() == ["999999.SH", "000001.SZ"]


def test_top2_tie_break_contract_is_lcb_then_big_loss_then_code() -> None:
    frame = pd.DataFrame(
        [
            {
                "signal_date": "20260105",
                "ts_code": "000004.SZ",
                "predicted_executable_net_profit_probability": 0.4,
                "expected_net_return_lcb": -0.02,
                "predicted_conditional_big_loss_probability": 0.10,
            },
            {
                "signal_date": "20260105",
                "ts_code": "000003.SZ",
                "predicted_executable_net_profit_probability": 0.4,
                "expected_net_return_lcb": -0.01,
                "predicted_conditional_big_loss_probability": 0.20,
            },
            {
                "signal_date": "20260105",
                "ts_code": "000002.SZ",
                "predicted_executable_net_profit_probability": 0.4,
                "expected_net_return_lcb": -0.01,
                "predicted_conditional_big_loss_probability": 0.10,
            },
            {
                "signal_date": "20260105",
                "ts_code": "000001.SZ",
                "predicted_executable_net_profit_probability": 0.4,
                "expected_net_return_lcb": -0.01,
                "predicted_conditional_big_loss_probability": 0.10,
            },
        ]
    )
    ranked = v2.freeze_top2(frame).sort_values("executable_profit_shadow_rank")
    assert ranked["ts_code"].tolist() == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
        "000004.SZ",
    ]


def test_fixed_two_capital_slots_keep_empty_slot_as_cash() -> None:
    frame = pd.DataFrame(
        [
            {
                "signal_date": "20260105",
                "ts_code": "000001.SZ",
                "executable_profit_shadow_rank": 1,
                "promotion_rank": 1,
                "frozen_p_fill_rank": 1,
                "strategy_slot_net_return": 0.10,
                "strategy_slot_net_return_2x_cost": 0.0955,
                "executable_profit_proxy_hit": 1,
                "public_market_buyable_proxy": 1,
                "conditional_big_loss_hit": 0,
            }
        ]
    )
    daily = v2.policy_daily(frame, "executable_profit_top2")
    assert daily.loc["20260105", "capital_slots"] == 2
    assert daily.loc["20260105", "cash_slots"] == 1
    assert daily.loc["20260105", "return"] == 0.05
    assert daily.loc["20260105", "profit_rate"] == 0.5


def _synthetic_timing_frame(open_dates: list[str]) -> pd.DataFrame:
    bucket_cycle = ["BIG_LOSS", "NON_PROFIT", "PROFIT"]
    rows = []
    for index, date in enumerate(open_dates[:100]):
        rows.append(
            {
                "signal_date": date,
                "scheduled_exit_date": open_dates[index + 2],
                "public_market_buyable_proxy": 1,
                "conditional_return_bucket": bucket_cycle[index % 3],
            }
        )
    return pd.DataFrame(rows)


def test_inner_partitions_assert_all_three_exit_boundaries() -> None:
    open_dates = [f"2026{month:02d}{day:02d}" for month in range(1, 8) for day in range(1, 21)]
    frame = _synthetic_timing_frame(open_dates)
    config = v2.V2Config(
        minimum_inner_fit_dates=30,
        minimum_component_dates=15,
        minimum_final_audit_dates=15,
    )
    _, _, _, audit = v2.inner_partitions(
        frame,
        open_dates,
        test_start=open_dates[105],
        config=config,
    )
    assert audit["fit_exit_before_component"] is True
    assert audit["component_exit_before_final"] is True
    assert audit["final_exit_before_test"] is True


def test_inner_partitions_fail_closed_when_final_truth_reaches_test() -> None:
    open_dates = [f"2026{month:02d}{day:02d}" for month in range(1, 8) for day in range(1, 21)]
    frame = _synthetic_timing_frame(open_dates)
    frame.loc[frame.index[-1], "scheduled_exit_date"] = open_dates[105]
    config = v2.V2Config(
        minimum_inner_fit_dates=30,
        minimum_component_dates=15,
        minimum_final_audit_dates=15,
    )
    with pytest.raises(ValueError, match="strict inner truth-timing"):
        v2.inner_partitions(
            frame,
            open_dates,
            test_start=open_dates[105],
            config=config,
        )


def test_common_mature_panel_uses_identical_dates_for_all_policies() -> None:
    rows = []
    for date_index, date in enumerate(("20260105", "20260106")):
        for rank in (1, 2, 3):
            pending = date_index == 1 and rank == 3
            rows.append(
                {
                    "signal_date": date,
                    "ts_code": f"00000{rank}.SZ",
                    "executable_profit_shadow_rank": rank,
                    "promotion_rank": rank,
                    "frozen_p_fill_rank": rank,
                    "strategy_slot_net_return": np.nan if pending else 0.01,
                    "strategy_slot_net_return_2x_cost": np.nan if pending else 0.0055,
                    "executable_profit_proxy_hit": np.nan if pending else 1,
                    "public_market_buyable_proxy": 1,
                    "conditional_big_loss_hit": np.nan if pending else 0,
                }
            )
    panel, daily = v2.common_mature_panel(pd.DataFrame(rows), v2.V2Config())
    assert panel["common_mature_dates"] == 1
    for policy in v2.POLICIES:
        assert daily[policy].index.tolist() == ["20260105"]
