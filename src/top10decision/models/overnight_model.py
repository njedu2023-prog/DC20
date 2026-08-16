#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import pandas as pd


def _safe_float(x, default=0.0) -> float:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _safe_bool(x, default: bool = False) -> bool:
    try:
        if x is None or pd.isna(x):
            return default
    except Exception:
        pass
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return float(x) != 0.0
    s = str(x).strip().lower()
    if s in {"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"}:
        return True
    if s in {"0", "0.0", "false", "no", "n", "f", "", "missing", "unavailable", "invalid"}:
        return False
    return default


def _score_0_1(x, default=0.0) -> float:
    v = _safe_float(x, default=default)
    if pd.isna(v):
        return float(default)
    if v > 1.0 and v <= 100.0:
        v = v / 100.0
    return max(0.0, min(1.0, float(v)))


def _series_or_default(df: pd.DataFrame, col: str, default=None) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def overnight_model_rule(df: pd.DataFrame, regime: str) -> pd.Series:
    """
    只计算 e_ret_pred，不写文件。

    分钟级红利接入：
    - 优质封板路径、较强回封和集合竞价强度给小幅 E_ret 加成；
    - 硬风险、尾盘撤单、弱回封和分钟缺失状态给惩罚；
    - 旧输入没有 intraday_* 字段时保持原行为。
    """
    prob = df.get("Probability", df.get("prob", df.get("probability", pd.Series([None] * len(df), index=df.index))))
    strength = df.get("StrengthScore", df.get("strength", pd.Series([None] * len(df), index=df.index)))
    theme = df.get("ThemeBoost", df.get("theme", pd.Series([None] * len(df), index=df.index)))

    intraday_available = _series_or_default(df, "intraday_available", False)
    intraday_status = _series_or_default(df, "intraday_status", "")
    intraday_quality = _series_or_default(df, "intraday_quality_score", 0.0)
    intraday_confidence = _series_or_default(df, "intraday_confidence_score", 0.0)
    intraday_soft_risk = _series_or_default(df, "intraday_soft_risk_score", 0.0)
    intraday_hard_risk = _series_or_default(df, "intraday_hard_risk_flag", False)
    late_withdraw = _series_or_default(df, "late_withdraw_score", 0.0)
    reseal_score = _series_or_default(df, "reseal_score", 0.0)
    auction_strength = _series_or_default(df, "auction_strength_score", 0.0)
    has_intraday = any(c in df.columns for c in (
        "intraday_available",
        "intraday_quality_score",
        "intraday_confidence_score",
        "intraday_soft_risk_score",
        "intraday_hard_risk_flag",
        "late_withdraw_score",
        "reseal_score",
        "auction_strength_score",
    ))

    e = []
    for i in range(len(df)):
        p = _safe_float(prob.iloc[i], default=0.3)
        s = _safe_float(strength.iloc[i], default=0.0)
        t = _safe_float(theme.iloc[i], default=0.0)

        ei = (max(0.0, min(1.0, p)) - 0.2) * 0.03
        ei += max(-2.0, min(10.0, s)) * 0.001
        ei += max(-1.0, min(3.0, t)) * 0.003

        if has_intraday:
            available = _safe_bool(intraday_available.iloc[i], default=False)
            status = str(intraday_status.iloc[i] or "").strip().lower()
            quality = _score_0_1(intraday_quality.iloc[i], default=0.0)
            confidence = _score_0_1(intraday_confidence.iloc[i], default=0.0)
            soft_risk = _score_0_1(intraday_soft_risk.iloc[i], default=0.0)
            hard_risk = _safe_bool(intraday_hard_risk.iloc[i], default=False)
            late_weak = _score_0_1(late_withdraw.iloc[i], default=0.0)
            reseal = _score_0_1(reseal_score.iloc[i], default=0.0)
            auction = _score_0_1(auction_strength.iloc[i], default=0.0)

            intraday_positive = available * quality * confidence * (1.0 - soft_risk)
            ei += intraday_positive * (0.0035 + 0.0015 * reseal + 0.0010 * auction)
            ei -= soft_risk * 0.004
            ei -= late_weak * 0.0035
            ei -= (1.0 - reseal) * 0.0015 if available else 0.0
            if hard_risk:
                ei -= 0.010
            if (not available) and status not in {"", "ok", "available", "matched", "ready", "valid"}:
                ei -= 0.002

        if str(regime).upper().strip() in ("RISK_OFF", "OFF", "DEFENSE"):
            ei -= 0.006

        ei = max(-0.05, min(0.08, ei))
        e.append(ei)

    return pd.Series(e, index=df.index, name="e_ret_pred")
