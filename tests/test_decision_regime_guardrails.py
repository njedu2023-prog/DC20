#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import pandas as pd

from top10decision.regime.simple_regime import simple_regime
from top10decision.risk.guardrails import guardrails


def test_guardrails_pass_normal_intraday_input() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": f"{i:06d}.SZ",
                "name": "normal",
                "intraday_available": 1,
                "intraday_hard_risk_flag": 0,
                "intraday_risk_score": 0.1,
                "pct_chg": 1.0,
            }
            for i in range(20)
        ]
    )

    result = guardrails(df)

    assert result.stop_trading is False
    assert result.topk == 100


def test_guardrails_stop_when_intraday_hard_risk_dominates() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": f"{i:06d}.SZ",
                "name": "hard-risk",
                "intraday_available": 1,
                "intraday_hard_risk_flag": 1,
                "intraday_risk_score": 0.9,
                "pct_chg": -1.0,
            }
            for i in range(20)
        ]
    )

    result = guardrails(df)

    assert result.stop_trading is True
    assert "INTRADAY_HARD_RISK_RATE" in result.reason


def test_regime_reduces_budget_for_intraday_hard_risk() -> None:
    df = pd.DataFrame(
        [
            {
                "ts_code": f"{i:06d}.SZ",
                "name": "hard-risk",
                "intraday_available": 1,
                "intraday_hard_risk_flag": 1,
                "intraday_risk_score": 0.9,
                "pct_chg": -1.0,
            }
            for i in range(20)
        ]
    )

    result = simple_regime(df)

    assert result.regime in {"CAUTION", "RISK_OFF"}
    assert result.risk_budget < 1.0
