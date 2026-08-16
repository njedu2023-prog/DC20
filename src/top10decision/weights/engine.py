#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import pandas as pd

from top10decision.writers.io_contract import TOPN_DEFAULT


def _ensure_cols(df: pd.DataFrame, cols: list[str]) -> None:
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise ValueError(f"缺少必要字段：{miss}. 现有字段：{list(df.columns)}")


def _pick_theme(row: pd.Series) -> str:
    for k in ("theme", "Theme", "board", "industry", "sector"):
        v = row.get(k, "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


@dataclass
class WeightCaps:
    w_max: float
    theme_cap: float
    gross_cap: float


def _empty_targets_like(df: pd.DataFrame) -> pd.DataFrame:
    out = df.iloc[0:0].copy()
    out["weight"] = pd.Series(dtype=float)
    out["target_rank"] = pd.Series(dtype=object)
    out["backup_rank"] = pd.Series(dtype=object)
    return out


def _as_backups(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    out["weight"] = 0.0
    out["target_rank"] = ""
    out["backup_rank"] = list(range(1, len(out) + 1))
    return out


def build_weights_with_backups(
    candidates: pd.DataFrame,
    topn: int,
    caps: WeightCaps,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    返回：
    - targets：TopN 目标（weight > 0, target_rank）
    - backups：候补池（weight = 0, backup_rank）

    关键约束：
    - 只有 ev_pred > 0 的候选才允许进入 targets。
    - 如果全池 ev_pred <= 0，则 targets 为空，所有候选进入 backups 且 weight=0。
    - backups 保留完整排序用于观察，但永远不产生执行权重。
    """
    _ensure_cols(candidates, ["ts_code", "name", "ev_pred"])

    df = candidates.copy()
    df["ev_pred"] = pd.to_numeric(df["ev_pred"], errors="coerce").fillna(float("-inf"))
    df = df.sort_values("ev_pred", ascending=False, kind="mergesort").reset_index(drop=True)
    df["theme"] = df.apply(_pick_theme, axis=1)

    if topn <= 0:
        topn = TOPN_DEFAULT

    # 先做正 EV 资格闸门。
    # 这一步是执行安全线：负 EV 只能作为观察/候补，不能被赋予执行权重。
    eligible = df[df["ev_pred"] > 0].copy().reset_index(drop=True)

    if eligible.empty:
        targets = _empty_targets_like(df)
        backups = _as_backups(df)
        return targets.reset_index(drop=True), backups

    picked_idx = []
    theme_used: Dict[str, float] = {}
    gross_used = 0.0

    base_w = min(float(caps.gross_cap), 1.0) / float(topn)
    per_name_w = min(base_w, float(caps.w_max))

    for i in range(len(eligible)):
        if len(picked_idx) >= topn:
            break

        th = eligible.loc[i, "theme"] or ""
        w = per_name_w

        if th:
            used = theme_used.get(th, 0.0)
            if used + w > float(caps.theme_cap):
                continue

        if gross_used + w > float(caps.gross_cap) + 1e-9:
            break

        picked_idx.append(i)
        gross_used += w
        if th:
            theme_used[th] = theme_used.get(th, 0.0) + w

    if not picked_idx:
        targets = _empty_targets_like(df)
        backups = _as_backups(df)
        return targets.reset_index(drop=True), backups

    targets = eligible.loc[picked_idx].copy().reset_index(drop=True)
    targets["weight"] = per_name_w
    targets["target_rank"] = list(range(1, len(targets) + 1))
    targets["backup_rank"] = ""

    picked_codes = set(targets["ts_code"].astype(str))
    rest = df[~df["ts_code"].astype(str).isin(picked_codes)].copy()
    backups = _as_backups(rest)

    return targets.reset_index(drop=True), backups.reset_index(drop=True)
