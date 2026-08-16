#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import pandas as pd

from top10decision.models.costs import risk_breakdown_df, risk_penalty_rule


def test_intraday_absent_keeps_new_penalties_neutral() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "name": "legacy",
                "open_times": 0,
                "turnover_rate": 5.0,
            }
        ]
    )

    breakdown = risk_breakdown_df("RISK_ON", df)

    assert float(breakdown.loc[0, "risk_intraday_hard_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_intraday_soft_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_intraday_confidence_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_intraday_missing_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_late_withdraw_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_reseal_weakness_penalty"]) == 0.0
    assert float(breakdown.loc[0, "risk_auction_weakness_penalty"]) == 0.0
    assert float(breakdown.loc[0, "intraday_execution_penalty"]) == 0.0


def test_intraday_fields_add_execution_and_hard_risk_penalty() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": "600001.SH",
                "name": "minute-risk",
                "open_board_count": 2,
                "intraday_available": True,
                "intraday_status": "ok",
                "intraday_hard_risk_flag": True,
                "intraday_soft_risk_score": 0.8,
                "intraday_confidence_score": 0.2,
                "late_withdraw_score": 0.9,
                "reseal_score": 0.1,
                "auction_strength_score": 0.2,
            }
        ]
    )

    breakdown = risk_breakdown_df("RISK_ON", df)
    total = float(risk_penalty_rule("RISK_ON", df).iloc[0])

    assert float(breakdown.loc[0, "risk_intraday_hard_penalty"]) == 0.010
    assert float(breakdown.loc[0, "risk_intraday_soft_penalty"]) > 0.0
    assert float(breakdown.loc[0, "risk_intraday_confidence_penalty"]) > 0.0
    assert float(breakdown.loc[0, "risk_late_withdraw_penalty"]) > 0.0
    assert float(breakdown.loc[0, "risk_reseal_weakness_penalty"]) > 0.0
    assert float(breakdown.loc[0, "risk_auction_weakness_penalty"]) > 0.0
    assert float(breakdown.loc[0, "intraday_execution_penalty"]) == 0.012
    assert total >= float(breakdown.loc[0, "risk_intraday_hard_penalty"])
