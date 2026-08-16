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


def fill_model_rule(df: pd.DataFrame) -> pd.Series:
    """
    只计算 p_fill_pred，不写文件。

    分钟级红利接入：
    - 高质量、高置信、低软风险的封板路径提升成交可实现性；
    - 硬风险、尾盘弱路径和缺失分钟审计降低成交可实现性；
    - 旧输入没有 intraday_* 字段时保持原行为。
    """
    base = 0.35
    open_times = df.get("open_times", pd.Series([None] * len(df), index=df.index))
    seal_amount = df.get("seal_amount", pd.Series([None] * len(df), index=df.index))
    turnover = df.get("turnover_rate", pd.Series([None] * len(df), index=df.index))

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

    p = []
    for i in range(len(df)):
        ot = _safe_float(open_times.iloc[i], default=float("nan"))
        sa = _safe_float(seal_amount.iloc[i], default=float("nan"))
        tr = _safe_float(turnover.iloc[i], default=float("nan"))

        pi = base
        if not pd.isna(ot):
            pi += min(max(ot, 0.0), 5.0) * 0.06
        if not pd.isna(sa):
            pi -= min(sa / 1e8, 5.0) * 0.05
        if not pd.isna(tr):
            pi += min(max(tr, 0.0), 20.0) * 0.005

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
            pi += intraday_positive * (0.04 + 0.02 * reseal + 0.01 * auction)
            pi -= soft_risk * 0.05
            pi -= late_weak * 0.04
            if hard_risk:
                pi -= 0.12
            if (not available) and status not in {"", "ok", "available", "matched", "ready", "valid"}:
                pi -= 0.03

        pi = max(0.02, min(0.98, pi))
        p.append(pi)

    return pd.Series(p, index=df.index, name="p_fill_pred")
