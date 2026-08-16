# -*- coding: utf-8 -*-

from dataclasses import dataclass

import pandas as pd


@dataclass
class RegimeResult:
    regime: str
    risk_budget: float
    reason: str


def _num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series(default, index=df.index if df is not None else pd.RangeIndex(0), dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default).astype(float)


def _mean(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    s = _num(df, col, default=default)
    return float(s.mean()) if len(s) else float(default)


def _truthy_rate(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    s = df[col].astype(str).str.strip().str.lower()
    truthy = s.isin({"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"})
    return float(truthy.mean()) if len(truthy) else 0.0


def simple_regime(df: pd.DataFrame) -> RegimeResult:
    """
    轻量市场状态识别。

    输出只控制 Decision 层风险预算，不替代上游选股模型。
    """
    if df is None or df.empty:
        return RegimeResult(regime="RISK_OFF", risk_budget=0.0, reason="empty_input")

    pct_chg_mean = _mean(df, "pct_chg")
    tail_risk_mean = _mean(df, "tail_risk_score")
    vol_mean = max(_mean(df, "volatility_10d"), _mean(df, "volatility_20d"))
    north_money = _mean(df, "north_money_market")
    hard_intraday_rate = _truthy_rate(df, "intraday_hard_risk_flag")
    intraday_risk_mean = max(_mean(df, "intraday_risk_score"), _mean(df, "intraday_soft_risk_score"))

    risk_score = 0.0
    reasons: list[str] = []

    if pct_chg_mean <= -2.0:
        risk_score += 1.0
        reasons.append(f"pct_chg_mean={pct_chg_mean:.3f}")
    if tail_risk_mean >= 0.055:
        risk_score += 1.0
        reasons.append(f"tail_risk_mean={tail_risk_mean:.4f}")
    if vol_mean >= 0.045:
        risk_score += 1.0
        reasons.append(f"volatility_mean={vol_mean:.4f}")
    if north_money <= -100.0:
        risk_score += 0.75
        reasons.append(f"north_money={north_money:.2f}")
    if hard_intraday_rate >= 0.35:
        risk_score += 1.5
        reasons.append(f"intraday_hard_rate={hard_intraday_rate:.3f}")
    if intraday_risk_mean >= 0.60 or intraday_risk_mean >= 60.0:
        risk_score += 0.75
        reasons.append(f"intraday_risk_mean={intraday_risk_mean:.3f}")

    reason = ",".join(reasons) if reasons else "risk_metrics_normal"
    if risk_score >= 3.0:
        return RegimeResult(regime="RISK_OFF", risk_budget=0.45, reason=reason)
    if risk_score >= 1.5:
        return RegimeResult(regime="CAUTION", risk_budget=0.70, reason=reason)
    return RegimeResult(regime="RISK_ON", risk_budget=1.0, reason=reason)
