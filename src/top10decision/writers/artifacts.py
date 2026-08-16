#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

import pandas as pd

from top10decision.utils import to_jq_code
from top10decision.adapters.joinquant.write_latest_signal import write_latest_signal
from top10decision.writers.io_contract import (
    norm_ymd,
    ensure_cols,
    SIGNAL_LATEST,
    SIGNAL_DATED_FMT,
    WEIGHTS_LATEST,
    WEIGHTS_DATED_FMT,
    CANDIDATES_FMT,
)

# =========================
# 新增：TopEVR 信号固定路径
# =========================
TOP_EVR_SIGNAL_LATEST = Path("docs/signals/TopEVR_latest.csv")
TOP_EVR_SIGNAL_DATED_FMT = "docs/signals/TopEVR_{yyyymmdd}.csv"


# =========================
# 内部小工具：元数据
# =========================
def _commit_sha() -> str:
    # GitHub Actions: GITHUB_SHA；本地可为空
    return str(os.getenv("GITHUB_SHA", "")).strip()


def _generated_at_bjt() -> str:
    # 北京时间 ISO（不依赖 pytz）
    try:
        ts = pd.Timestamp.now(tz="Asia/Shanghai")
    except Exception:
        ts = pd.Timestamp.now()
    return ts.strftime("%Y-%m-%d %H:%M:%S%z")


def _ensure_explain_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    candidates_snapshot 强契约：
    解释字段缺失必须补齐。
    这里不做任何“重新计算”，只补默认值，防止 silent 退化。

    本版新增：
    - 强制保留 / 补齐 P_fill 与 E_ret 诊断字段。
    - 目的不是改变排序，而是让 decision_candidates_YYYYMMDD.csv 能直接审计：
      1) raw 是否全负
      2) clip 是否命中
      3) 特征缺失比例是否异常
      4) P_fill 是否触顶
    """
    out = df.copy()

    defaults = {
        # 日期链路（如缺就空）
        "signal_date": "",
        "exec_date": "",
        "exit_date": "",

        # 预测与解释：旧主字段
        "p_fill_pred": 0.0,
        "e_ret_pred": 0.0,
        "cost_est": 0.0,
        "risk_penalty": 0.0,
        "ev_pred": 0.0,

        # P_fill 诊断字段：只补齐，不重新计算
        "p_fill_pred_raw": "",
        "p_fill_pred_model": "",
        "p_fill_pred_final": "",
        "p_fill_cap_hit": "",
        "p_fill_clip_hit": "",
        "p_fill_clip_direction": "",
        "p_fill_cap_hit_rate": "",

        # E_ret raw / final 诊断字段：只补齐，不重新计算
        "eret_pred_raw": "",
        "e_ret_pred_raw": "",
        "eret_pred_model_raw": "",
        "eret_pred_model": "",
        "eret_pred_model_clipped": "",
        "eret_pred_final": "",
        "eret_pred": "",
        "eret_pred_rule": "",

        # E_ret clip 诊断字段
        "eret_clip_hit": "",
        "e_ret_clip_hit": "",
        "eret_clip_direction": "",
        "e_ret_clip_direction": "",
        "eret_clip_lower_hit": "",
        "eret_clip_upper_hit": "",
        "eret_clip_hit_count": "",
        "eret_clip_hit_rate": "",

        # E_ret 特征对齐 / 缺失诊断字段
        "eret_model_loaded": "",
        "eret_model_kind": "",
        "eret_model_path": "",
        "eret_model_feature_mode": "",
        "eret_model_meta_path": "",
        "eret_model_expected_n_features": "",
        "eret_model_actual_n_features": "",
        "eret_missing_feature_count": "",
        "eret_missing_feature_sample": "",
        "eret_unexpected_feature_count": "",
        "eret_unexpected_feature_sample": "",
        "eret_expected_categorical_cols": "",
        "eret_expected_numeric_feature_count": "",
        "eret_model_category_like_cols_detected": "",
        "eret_categorical_feature_count_online": "",
        "eret_categorical_feature_sample_online": "",
        "eret_numeric_feature_count_online": "",
        "eret_online_dtypes_sample": "",
        "eret_feature_missing_cell_count": "",
        "eret_feature_missing_cell_ratio": "",
        "e_ret_feature_missing_ratio": "",
        "eret_feature_rows_with_missing_count": "",
        "eret_feature_row_missing_ratio_mean": "",
        "eret_feature_column_missing_ratio_max": "",

        # E_ret 分布诊断字段
        "eret_raw_min": "",
        "eret_raw_max": "",
        "eret_raw_mean": "",
        "eret_raw_std": "",
        "eret_final_min": "",
        "eret_final_max": "",
        "eret_final_mean": "",
        "eret_final_std": "",
        "eret_negative_count": "",
        "eret_negative_rate": "",
        "eret_positive_count": "",
        "eret_positive_rate": "",

        # E_ret 来源字段
        "eret_pred_src": "",
        "eret_degrade_reason": "",
        "eret_regime_used": "",

        # 闭环需要但允许空
        "weight_exec": "",
        "regime": "",
        "risk_budget": "",
        "version": 1,
        "generated_at_bjt": _generated_at_bjt(),
        "commit_sha": _commit_sha(),
    }

    for c, v in defaults.items():
        if c not in out.columns:
            out[c] = v

    # name/ts_code 如果缺，应该在上游就报错；这里仍保护一下
    if "ts_code" not in out.columns:
        raise RuntimeError("write_candidates_snapshot: 缺少必要字段 ts_code（上游输出不合格）")
    if "name" not in out.columns:
        out["name"] = ""

    return out


def _empty_signal_df() -> pd.DataFrame:
    """
    返回聚宽信号的空表骨架。
    允许 top10 / TopEVR 在 0 行时也能稳定写 latest / dated。
    语义：
    - 空表不是异常；
    - 空表表示本轮没有满足执行条件的正 EV 标的；
    - 保留完整表头，避免交易端读文件时报错。
    """
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


def _ensure_signal_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    确保信号表就算 0 行也有完整表头。
    """
    if df is None:
        return _empty_signal_df()

    out = df.copy()
    required = {
        "trade_date": "",
        "target_trade_date": "",
        "jq_code": "",
        "target_weight": 0.0,
        "risk_budget": 0.0,
        "regime": "",
        "reason": "",
    }
    for c, default in required.items():
        if c not in out.columns:
            out[c] = default

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
    ].copy()


# =========================
# signals
# =========================
def write_signals(latest_df: pd.DataFrame, trade_date: str) -> None:
    """
    signals IO 契约：
    - docs/signals/top10_latest.csv
    - docs/signals/top10_YYYYMMDD.csv

    关键修复：
    - 旧逻辑拒绝空 latest_df，目的是防止 silent 退化。
    - 但现在 weights 层已经有正 EV 执行闸门：
      当全池 EV <= 0 时，空 signal 是正确的“今日不交易”状态。
    - 因此这里允许空表写出，但必须保留完整 schema。
    """
    out = _ensure_signal_schema(latest_df)

    # 非空时仍做最小字段保护；空表由 _ensure_signal_schema 保证表头。
    if not out.empty:
        ensure_cols(out, ["jq_code", "target_weight", "trade_date", "target_trade_date"])

    # 写 latest
    write_latest_signal(out, out_path=str(SIGNAL_LATEST))

    # 写 dated（按参数 trade_date 决定文件名）
    td = norm_ymd(trade_date)
    if td:
        write_latest_signal(out, out_path=SIGNAL_DATED_FMT.format(yyyymmdd=td))


def write_top_evr_signals(latest_df: pd.DataFrame, trade_date: str) -> Tuple[str, str]:
    """
    TopEVR 信号：
    - docs/signals/TopEVR_latest.csv
    - docs/signals/TopEVR_YYYYMMDD.csv

    与 top10 信号不同点：
    - 允许空表写出
    - 空表也必须保留完整字段，避免聚宽端读文件时报错
    """
    Path("docs/signals").mkdir(parents=True, exist_ok=True)

    out = _ensure_signal_schema(latest_df)

    latest_path = str(TOP_EVR_SIGNAL_LATEST)
    write_latest_signal(out, out_path=latest_path)

    td = norm_ymd(trade_date)
    dated_path = TOP_EVR_SIGNAL_DATED_FMT.format(yyyymmdd=td) if td else "docs/signals/TopEVR_unknown.csv"
    write_latest_signal(out, out_path=dated_path)

    return latest_path, dated_path


# =========================
# weights
# =========================
def write_weights(weights_df: pd.DataFrame, exec_date: str) -> Tuple[str, str]:
    """
    weights IO 契约：
    - docs/weights/weights_latest.csv
    - docs/weights/weights_YYYYMMDD.csv
    """
    Path("docs/weights").mkdir(parents=True, exist_ok=True)

    latest_path = str(WEIGHTS_LATEST)
    dated = norm_ymd(exec_date)
    dated_path = WEIGHTS_DATED_FMT.format(yyyymmdd=dated) if dated else "docs/weights/weights_unknown.csv"

    weights_df.to_csv(latest_path, index=False, encoding="utf-8-sig")
    weights_df.to_csv(dated_path, index=False, encoding="utf-8-sig")
    return latest_path, dated_path


# =========================
# candidates_snapshot
# =========================
def write_candidates_snapshot(cand_df: pd.DataFrame, signal_date: str) -> str:
    """
    candidates IO 契约：
    - data/decision/decision_candidates_YYYYMMDD.csv

    强契约：
    - 保证解释字段齐全（p_fill_pred/e_ret_pred/cost_est/risk_penalty/ev_pred）
    - 保证 E_ret / P_fill 诊断字段齐全，便于定位 raw、clip、missing、cap 等问题
    - 写入审计元数据（commit_sha/generated_at/version）
    """
    if cand_df is None or cand_df.empty:
        raise RuntimeError("write_candidates_snapshot: cand_df 为空，拒绝写 candidates_snapshot。")

    Path("data/decision").mkdir(parents=True, exist_ok=True)
    sd = norm_ymd(signal_date) or "unknown"
    path = CANDIDATES_FMT.format(yyyymmdd=sd)

    out = _ensure_explain_cols(cand_df)

    # 若上游没写 signal_date，则用函数参数兜底
    if out["signal_date"].astype(str).eq("").all():
        out["signal_date"] = norm_ymd(signal_date)

    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# =========================
# joinquant signal builder（保持旧契约）
# =========================
def build_signal_df_for_joinquant(
    weights_df: pd.DataFrame,
    risk_budget: float,
    regime_name: str,
    trade_date: str,
    target_trade_date: str,
) -> pd.DataFrame:
    """
    兼容旧 joinquant 信号格式：
    - 只输出 weight>0 的目标行（候补不进入 signals）

    输出字段保持原契约：
    ["trade_date","target_trade_date","jq_code","target_weight","risk_budget","regime","reason"]
    """
    ensure_cols(weights_df, ["ts_code", "weight"])

    df = weights_df.copy()
    df = df[df["weight"].astype(float) > 0].copy()

    if df.empty:
        out = _empty_signal_df()
        out["trade_date"] = norm_ymd(trade_date)
        out["target_trade_date"] = norm_ymd(target_trade_date)
        out["risk_budget"] = float(risk_budget)
        out["regime"] = str(regime_name)
        out["reason"] = "NO_TRADE_NO_POSITIVE_EV"
        return _ensure_signal_schema(out)

    df["jq_code"] = df["ts_code"].apply(to_jq_code)
    df["trade_date"] = norm_ymd(trade_date)
    df["target_trade_date"] = norm_ymd(target_trade_date)
    df["risk_budget"] = float(risk_budget)
    df["regime"] = str(regime_name)
    df["reason"] = "P0_EV_weight"
    df["target_weight"] = df["weight"].astype(float)

    return df[
        ["trade_date", "target_trade_date", "jq_code", "target_weight", "risk_budget", "regime", "reason"]
    ].copy()


def build_top_evr_signal_df(
    candidates_df: pd.DataFrame,
    risk_budget: float,
    regime_name: str,
    trade_date: str,
    target_trade_date: str,
    ev_threshold: float = 0.03,
    risk_threshold: float = 0.01,
) -> pd.DataFrame:
    """
    新增 TopEVR 动态信号：

    规则：
    - EV > 3%
    - RiskPenalty < 1%

    输出：
    - 不固定票数
    - 0 行也允许
    - 字段结构与 joinquant signal 契约一致
    """
    if candidates_df is None or candidates_df.empty:
        out = _empty_signal_df()
        out["trade_date"] = norm_ymd(trade_date)
        out["target_trade_date"] = norm_ymd(target_trade_date)
        out["risk_budget"] = float(risk_budget)
        out["regime"] = str(regime_name)
        out["reason"] = "TopEVR_EV>3pct_Risk<1pct"
        return _ensure_signal_schema(out)

    ensure_cols(candidates_df, ["ts_code"])

    df = candidates_df.copy()

    if "ev_pred" not in df.columns:
        raise ValueError("build_top_evr_signal_df: 缺少 ev_pred 字段。")
    if "risk_penalty" not in df.columns:
        raise ValueError("build_top_evr_signal_df: 缺少 risk_penalty 字段。")

    df["ev_pred"] = pd.to_numeric(df["ev_pred"], errors="coerce")
    df["risk_penalty"] = pd.to_numeric(df["risk_penalty"], errors="coerce")

    df = df[
        (df["ev_pred"] > float(ev_threshold)) & (df["risk_penalty"] < float(risk_threshold))
    ].copy()

    if df.empty:
        out = _empty_signal_df()
        out["trade_date"] = norm_ymd(trade_date)
        out["target_trade_date"] = norm_ymd(target_trade_date)
        out["risk_budget"] = float(risk_budget)
        out["regime"] = str(regime_name)
        out["reason"] = "TopEVR_EV>3pct_Risk<1pct"
        return _ensure_signal_schema(out)

    df = df.sort_values(["ev_pred", "ts_code"], ascending=[False, True], kind="stable").reset_index(drop=True)

    n = len(df)
    equal_weight = 1.0 / float(n) if n > 0 else 0.0

    # 关键修复：
    # 先让 out 具备与 df 对齐的 index，再写标量列，
    # 否则 trade_date / target_trade_date 会因为先写入空 DataFrame 而变成空值
    out = pd.DataFrame(index=df.index.copy())
    out["jq_code"] = df["ts_code"].apply(to_jq_code)
    out["trade_date"] = norm_ymd(trade_date)
    out["target_trade_date"] = norm_ymd(target_trade_date)
    out["target_weight"] = equal_weight
    out["risk_budget"] = float(risk_budget)
    out["regime"] = str(regime_name)
    out["reason"] = "TopEVR_EV>3pct_Risk<1pct"

    return _ensure_signal_schema(out.reset_index(drop=True))
