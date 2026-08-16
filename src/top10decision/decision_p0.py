# -*- coding: utf-8 -*-

"""
decision_p0.py

定位：
- P0 执行信号层
- 输入：已经完成 EV / P_fill / E_ret / Cost / RiskPenalty 排序后的候选 DataFrame
- 输出：JoinQuant / 执行侧可消费的最小信号表

本次修复原则：
1. 不改上下游路径。
2. 不改输出字段结构。
3. 不重做 workflow。
4. 修复一个核心风险：当所有候选 EV <= 0 时，不再机械等权买入。
5. 若存在 EV 字段，只给正 EV 候选分配权重；若没有正 EV，则保留前 10 行但 target_weight = 0。
6. 若上游没有 EV 字段，则保持旧版 P0 等权行为，避免兼容性断链。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from top10decision.utils import to_jq_code


TOP_N = 10

EV_COL_CANDIDATES = [
    "EV",
    "ev",
    "ev_pred",
    "EV_pred",
    "expected_value",
    "ExpectedValue",
]


def _resolve_ev_col(df: pd.DataFrame) -> Optional[str]:
    """Return the first available EV-like column name."""
    for col in EV_COL_CANDIDATES:
        if col in df.columns:
            return col
    return None


def _ensure_trade_date_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "trade_date" not in out.columns:
        out["trade_date"] = ""
    if "target_trade_date" not in out.columns:
        out["target_trade_date"] = ""
    return out


def _finalize_signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = _ensure_trade_date_cols(df)
    if "jq_code" not in out.columns:
        out["jq_code"] = out["ts_code"].apply(to_jq_code)

    out["risk_budget"] = 1.0
    out["regime"] = "RISK_ON"

    return out[
        [
            "trade_date",
            "target_trade_date",
            "jq_code",
            "target_weight",
            "risk_budget",
            "regime",
            "reason",
        ]
    ]


def decision_p0(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build P0 execution signal.

    Legacy behavior:
    - If no EV-like column exists, keep old behavior: top 10 equal weight.

    EV-aware behavior:
    - If EV exists, allocate only to EV > 0 candidates.
    - If all EV <= 0, emit top 10 rows with target_weight = 0.0.
      This preserves schema and path, while preventing forced negative-EV buying.
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "target_trade_date",
                "jq_code",
                "target_weight",
                "risk_budget",
                "regime",
                "reason",
            ]
        )

    work = df.copy()
    ev_col = _resolve_ev_col(work)

    # 兼容旧链路：上游如果没有 EV 字段，不强行阻断。
    if ev_col is None:
        out = work.head(TOP_N).copy()
        out["target_weight"] = 1.0 / len(out) if len(out) > 0 else 0.0
        out["reason"] = "P0_equal_weight_no_ev_column"
        return _finalize_signal_frame(out)

    work["_p0_ev_numeric"] = pd.to_numeric(work[ev_col], errors="coerce").fillna(-999.0)

    # 上游通常已经按 EV 降序排序；这里再稳妥排序一次。
    work = work.sort_values("_p0_ev_numeric", ascending=False, kind="mergesort")

    eligible = work[work["_p0_ev_numeric"] > 0].head(TOP_N).copy()

    if not eligible.empty:
        eligible["target_weight"] = 1.0 / len(eligible)
        eligible["reason"] = "P0_positive_ev_equal_weight"
        return _finalize_signal_frame(eligible.drop(columns=["_p0_ev_numeric"], errors="ignore"))

    # 所有候选都是非正 EV：保留前 10 行，权重置 0，避免执行侧误买。
    out = work.head(TOP_N).copy()
    out["target_weight"] = 0.0
    out["reason"] = "P0_no_trade_all_non_positive_ev"
    return _finalize_signal_frame(out.drop(columns=["_p0_ev_numeric"], errors="ignore"))


__all__ = [
    "decision_p0",
]
