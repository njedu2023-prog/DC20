#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from top10decision.writers.io_contract import norm_ymd


# =========================
# 固定契约：Execution / Learning
# =========================

EXECUTION_COLUMNS: List[str] = [
    "exec_date",
    "ts_code",
    "jq_code",
    "filled_flag",
    "buy_time",
    "buy_price",
    "fail_reason",
    "buy_slippage_bp",
]

# ✅ 强烈建议（P1）：学习闭环“唯一真相表”字段（稳定契约，不随意改名）
LEARNING_COLUMNS: List[str] = [
    # 日期链路（核心）
    "signal_date",          # 信号日 T
    "exec_date",            # 买入执行日 T+1
    "exit_date",            # 卖出日 T+1（或一字跌停顺延，可后补）

    # 标的标识
    "ts_code",
    "jq_code",
    "name",

    # 交易权重与真实执行
    "weight_exec",          # 执行权重（来自 decision 输出）
    "filled_flag",          # 是否成交（或成交达到阈值）
    "fill_rate_real",       # 真实成交率（成交金额/目标金额）

    # 价格与收益（真实）
    "buy_price",
    "sell_price",
    "ret_exec",             # 真实收益（百分比或小数，按你现有定义）
    "e_ret_real",           # 真实隔夜收益（可与 ret_exec 同义，允许冗余以便兼容）

    # 预测与解释字段（来自 top10-decision）
    "p_fill_pred",
    "e_ret_pred",
    "cost_est",
    "risk_penalty",
    "ev_pred",

    # 真实 EV（回填）
    "ev_real",

    # 环境/状态（用于分层评估）
    "regime",
    "risk_budget",

    # 审计元数据（可追溯）
    "version",
    "generated_at_bjt",
    "commit_sha",
]


# =========================
# 目录
# =========================

def ensure_dirs() -> None:
    Path("data/pred").mkdir(parents=True, exist_ok=True)
    Path("data/decision").mkdir(parents=True, exist_ok=True)
    Path("data/jq_feedback").mkdir(parents=True, exist_ok=True)

    Path("docs/signals").mkdir(parents=True, exist_ok=True)
    Path("docs/reports").mkdir(parents=True, exist_ok=True)
    Path("docs/weights").mkdir(parents=True, exist_ok=True)

    Path("outputs/decision").mkdir(parents=True, exist_ok=True)
    Path("outputs/learning").mkdir(parents=True, exist_ok=True)

    # 给后续审计/复盘预留
    Path("logs/decision_v3").mkdir(parents=True, exist_ok=True)


# =========================
# Execution Table
# =========================

def ensure_execution_table(exec_date: str) -> str:
    Path("data/decision").mkdir(parents=True, exist_ok=True)
    ed = norm_ymd(exec_date) or "unknown"
    path = f"data/decision/decision_execution_{ed}.csv"

    if not Path(path).exists():
        empty = pd.DataFrame(columns=EXECUTION_COLUMNS)
        empty.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        _ensure_csv_has_schema(path, EXECUTION_COLUMNS)

    return path


# =========================
# Learning Table
# =========================

def ensure_learning_table() -> str:
    """
    学习闭环“唯一真相表”：
    - 首次创建：写入固定表头
    - 已存在旧表：自动补齐缺失列、按新 schema 重排（不丢数据）
    """
    Path("data/decision").mkdir(parents=True, exist_ok=True)
    path = "data/decision/decision_learning.csv"

    if not Path(path).exists():
        empty = pd.DataFrame(columns=LEARNING_COLUMNS)
        empty.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    _ensure_csv_has_schema(path, LEARNING_COLUMNS)
    return path


# =========================
# 内部工具：补齐/重排 schema（不丢数据）
# =========================

def _ensure_csv_has_schema(path: str, schema_cols: List[str]) -> None:
    """
    若 CSV 已存在但列不符合 schema：
    - 读取全部数据
    - 添加缺失列（填空）
    - 丢弃未知列（保守策略：避免野列扩散；如你想保留可改成追加在末尾）
    - 按 schema_cols 顺序重写原文件
    """
    p = Path(path)
    if not p.exists():
        return

    try:
        df = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
    except Exception:
        # 万一编码异常/空文件，直接重建表头（不写数据）
        empty = pd.DataFrame(columns=schema_cols)
        empty.to_csv(p, index=False, encoding="utf-8-sig")
        return

    # 旧表可能是空表但有列
    existing_cols = list(df.columns) if df is not None else []

    # 1) 添加缺失列
    for c in schema_cols:
        if c not in existing_cols:
            df[c] = ""

    # 2) 丢弃未知列（强契约：避免“越写越乱”）
    df = df[[c for c in schema_cols]]

    # 3) 重写
    df.to_csv(p, index=False, encoding="utf-8-sig")
