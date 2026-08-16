#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
decisio_adapter.py

硬规则：
- 适配器只做字段映射：不做业务计算

兼容 a-top10 decisio 输出字段（pred_decisio_latest.csv 等）：
- target_trade_date <- verify_date
- Probability <- prob
并兜底：
- ts_code <- code
- name   <- stock_name
"""

from __future__ import annotations

import pandas as pd


def normalize_pred_fields(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    d = df.copy()

    # ts_code / name 兜底
    if "ts_code" not in d.columns and "code" in d.columns:
        d["ts_code"] = d["code"]
    if "name" not in d.columns and "stock_name" in d.columns:
        d["name"] = d["stock_name"]

    # 交易日字段
    if "target_trade_date" not in d.columns:
        if "verify_date" in d.columns:
            d["target_trade_date"] = d["verify_date"]
        else:
            d["target_trade_date"] = ""
    if "trade_date" not in d.columns:
        d["trade_date"] = ""

    # 概率字段
    if "Probability" not in d.columns and "prob" in d.columns:
        d["Probability"] = d["prob"]

    # 保证存在（值允许为空）：避免后续逻辑空引用
    for c in ("prob", "StrengthScore", "ThemeBoost", "board"):
        if c not in d.columns:
            d[c] = ""

    return d
