# -*- coding: utf-8 -*-

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class GuardrailResult:
    stop_trading: bool
    reason: str
    topk: int = 100


def _num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series(default, index=df.index if df is not None else pd.RangeIndex(0), dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default).astype(float)


def _truthy_rate(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    s = df[col].astype(str).str.strip().str.lower()
    truthy = s.isin({"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"})
    return float(truthy.mean()) if len(truthy) else 0.0


def _mean(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    s = _num(df, col, default=default)
    return float(s.mean()) if len(s) else float(default)


def _max(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    s = _num(df, col, default=default)
    return float(s.max()) if len(s) else float(default)


def _has_any_col(df: pd.DataFrame, cols: list[str]) -> bool:
    return df is not None and any(c in df.columns for c in cols)


def guardrails(df: pd.DataFrame) -> GuardrailResult:
    """
    Decision 执行闸门。

    这里只使用运行前已经可见的输入证据，不依赖 EV 结果，避免循环依赖。
    """
    if df is None or df.empty:
        return GuardrailResult(True, "EMPTY_INPUT", topk=0)

    rows = int(len(df))
    if rows < 5:
        return GuardrailResult(True, f"TOO_FEW_CANDIDATES:{rows}", topk=0)

    reasons: list[str] = []
    topk = 100

    intraday_cols = [
        "intraday_available",
        "intraday_hard_risk_flag",
        "intraday_risk_score",
        "intraday_soft_risk_score",
        "late_withdraw_score",
        "open_board_count",
    ]
    if _has_any_col(df, intraday_cols):
        hard_rate = _truthy_rate(df, "intraday_hard_risk_flag")
        available_rate = _truthy_rate(df, "intraday_available")
        intraday_risk_mean = max(_mean(df, "intraday_risk_score"), _mean(df, "intraday_soft_risk_score"))
        open_board_max = _max(df, "open_board_count")

        if hard_rate >= 0.60 and rows >= 10:
            return GuardrailResult(True, f"INTRADAY_HARD_RISK_RATE:{hard_rate:.3f}", topk=0)
        if hard_rate >= 0.35:
            reasons.append(f"intraday_hard_rate={hard_rate:.3f}")
            topk = min(topk, 60)
        if available_rate > 0 and available_rate < 0.50:
            reasons.append(f"intraday_coverage_low={available_rate:.3f}")
            topk = min(topk, 80)
        if intraday_risk_mean >= 0.65 or intraday_risk_mean >= 65.0:
            reasons.append(f"intraday_risk_mean={intraday_risk_mean:.3f}")
            topk = min(topk, 60)
        if open_board_max >= 5:
            reasons.append(f"open_board_max={open_board_max:.1f}")
            topk = min(topk, 80)

    pct_chg_mean = _mean(df, "pct_chg")
    tail_risk_mean = _mean(df, "tail_risk_score")
    volatility_mean = max(_mean(df, "volatility_10d"), _mean(df, "volatility_20d"))
    north_money = _mean(df, "north_money_market")

    if pct_chg_mean <= -4.5 and rows >= 20:
        return GuardrailResult(True, f"MARKET_SEVERE_DOWNTURN:pct_chg_mean={pct_chg_mean:.3f}", topk=0)

    if tail_risk_mean >= 0.08:
        reasons.append(f"tail_risk_mean={tail_risk_mean:.4f}")
        topk = min(topk, 70)
    if volatility_mean >= 0.055:
        reasons.append(f"volatility_mean={volatility_mean:.4f}")
        topk = min(topk, 70)
    if north_money <= -150.0:
        reasons.append(f"north_money={north_money:.2f}")
        topk = min(topk, 70)

    if reasons:
        return GuardrailResult(False, "CAUTION:" + ",".join(reasons), topk=topk)
    return GuardrailResult(False, "PASS", topk=topk)
