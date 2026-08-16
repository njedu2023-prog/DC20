#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import pandas as pd

from top10decision.engines.pfill_engine import _calibrate_pfill_output, apply_pfill_engine


def test_pfill_calibration_desaturates_model_cap() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": "600001.SH",
                "intraday_available": 1,
                "intraday_quality_score": 0.25,
                "intraday_confidence_score": 0.30,
                "intraday_soft_risk_score": 0.60,
                "intraday_hard_risk_flag": 1,
                "late_withdraw_score": 0.80,
                "auction_strength_score": 0.15,
                "reseal_score": 0.10,
                "open_board_count": 4,
            },
            {
                "ts_code": "600002.SH",
                "intraday_available": 1,
                "intraday_quality_score": 0.60,
                "intraday_confidence_score": 0.65,
                "intraday_soft_risk_score": 0.20,
                "intraday_hard_risk_flag": 0,
                "late_withdraw_score": 0.25,
                "auction_strength_score": 0.50,
                "reseal_score": 0.45,
                "open_board_count": 1,
            },
            {
                "ts_code": "600003.SH",
                "intraday_available": 1,
                "intraday_quality_score": 0.88,
                "intraday_confidence_score": 0.85,
                "intraday_soft_risk_score": 0.05,
                "intraday_hard_risk_flag": 0,
                "late_withdraw_score": 0.05,
                "auction_strength_score": 0.80,
                "reseal_score": 0.80,
                "open_board_count": 0,
            },
        ]
    )
    raw = pd.Series([0.98, 0.98, 0.98], index=df.index)
    rule = pd.Series([0.58, 0.72, 0.86], index=df.index)

    calibrated = _calibrate_pfill_output(raw, rule, df, fill_base_rate=0.963)

    assert float(calibrated.max()) < 0.98
    assert calibrated.nunique() == 3
    assert float(calibrated.iloc[0]) < float(calibrated.iloc[1]) < float(calibrated.iloc[2])


def test_apply_pfill_engine_keeps_public_field_contract(tmp_path) -> None:
    df = pd.DataFrame(
        [
            {"ts_code": "600001.SH", "open_times": 1, "turnover_rate": 4.0},
            {"ts_code": "600002.SH", "open_times": 4, "turnover_rate": 12.0},
        ]
    )

    out = apply_pfill_engine(df, project_root=tmp_path)

    assert "p_fill_pred" in out.columns
    assert "p_fill_pred_final" in out.columns
    assert "p_fill_effective" not in out.columns
    assert "p_fill_edge" not in out.columns
    assert "p_fill_calibrated" not in out.columns
