# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd


DEFAULT_SIGNAL_LATEST = "docs/signals/top10_latest.csv"
DEFAULT_EVRP_SIGNAL_DIR = "docs/signals_evrp"
DEFAULT_EVRP_SIGNAL_LATEST = "docs/signals_evrp/top10_evrp_latest.csv"


def _ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _first_existing_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _normalize_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    统一常用字段名，尽量兼容当前仓库已存在的不同口径。
    不会删除原字段，只会补标准别名字段。
    """
    out = df.copy()

    ev_col = _first_existing_col(out, ["EV", "ev_pred"])
    if ev_col and "EV" not in out.columns:
        out["EV"] = pd.to_numeric(out[ev_col], errors="coerce")

    risk_col = _first_existing_col(out, ["RiskPenalty", "risk_penalty", "risk_total_penalty"])
    if risk_col and "RiskPenalty" not in out.columns:
        out["RiskPenalty"] = pd.to_numeric(out[risk_col], errors="coerce")

    pfill_col = _first_existing_col(out, ["P_fill", "p_fill_pred", "p_fill_pred_final"])
    if pfill_col and "P_fill" not in out.columns:
        out["P_fill"] = pd.to_numeric(out[pfill_col], errors="coerce")

    eret_col = _first_existing_col(out, ["E_ret", "e_ret_pred", "eret_pred", "eret_pred_final"])
    if eret_col and "E_ret" not in out.columns:
        out["E_ret"] = pd.to_numeric(out[eret_col], errors="coerce")

    cost_col = _first_existing_col(out, ["Cost", "cost_est"])
    if cost_col and "Cost" not in out.columns:
        out["Cost"] = pd.to_numeric(out[cost_col], errors="coerce")

    weight_col = _first_existing_col(out, ["weight", "weight_exec"])
    if weight_col and "weight" not in out.columns:
        out["weight"] = pd.to_numeric(out[weight_col], errors="coerce")

    return out


def _resolve_trade_date(df: pd.DataFrame, trade_date: Optional[str] = None) -> Optional[str]:
    """
    优先使用显式传入的 trade_date；
    否则从表内常见日期字段中自动提取一个用于 dated 文件命名。
    """
    if trade_date:
        return str(trade_date)

    for col in ["exec_date", "target_trade_date", "requested_trade_date", "verify_date", "trade_date"]:
        if col in df.columns and len(df) > 0:
            val = df[col].iloc[0]
            if pd.notna(val) and str(val).strip():
                return str(val).strip()

    return None


def write_latest_signal(df: pd.DataFrame, out_path: str = DEFAULT_SIGNAL_LATEST) -> Path:
    """
    旧接口保留：写 latest 信号。
    """
    out_path = _ensure_parent(Path(out_path))
    df.to_csv(out_path, index=False)
    return out_path


def write_latest_and_dated_signal(
    df: pd.DataFrame,
    latest_path: str = DEFAULT_SIGNAL_LATEST,
    dated_dir: Optional[str] = None,
    dated_prefix: str = "top10",
    trade_date: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    """
    通用信号双写：
    1) latest
    2) dated（如果能解析到日期）
    """
    latest = _ensure_parent(Path(latest_path))
    df.to_csv(latest, index=False)

    if dated_dir is None:
        dated_dir = str(latest.parent)

    resolved_trade_date = _resolve_trade_date(df, trade_date)
    dated_path: Optional[Path] = None

    if resolved_trade_date:
        dated_path = _ensure_parent(Path(dated_dir) / f"{dated_prefix}_{resolved_trade_date}.csv")
        df.to_csv(dated_path, index=False)

    return latest, dated_path


def build_evrp_window_signal(
    df: pd.DataFrame,
    ev_threshold: float = 0.03,
    risk_threshold: float = 0.01,
) -> pd.DataFrame:
    """
    构建新窗口信号：
    EV > 3%
    RiskPenalty < 1%
    """
    base = _normalize_signal_columns(df)

    if "EV" not in base.columns:
        raise ValueError("build_evrp_window_signal: missing EV/ev_pred column.")
    if "RiskPenalty" not in base.columns:
        raise ValueError("build_evrp_window_signal: missing RiskPenalty/risk_penalty column.")

    out = base.copy()
    out["EV"] = pd.to_numeric(out["EV"], errors="coerce")
    out["RiskPenalty"] = pd.to_numeric(out["RiskPenalty"], errors="coerce")

    filtered = out[
        (out["EV"] > float(ev_threshold)) &
        (out["RiskPenalty"] < float(risk_threshold))
    ].copy()

    sort_cols = [c for c in ["EV", "rank"] if c in filtered.columns]
    ascending = [False if c == "EV" else True for c in sort_cols]
    if sort_cols:
        filtered = filtered.sort_values(sort_cols, ascending=ascending, kind="stable").reset_index(drop=True)

    return filtered


def write_evrp_window_signal(
    df: pd.DataFrame,
    out_dir: str = DEFAULT_EVRP_SIGNAL_DIR,
    latest_filename: str = "top10_evrp_latest.csv",
    dated_prefix: str = "top10_evrp",
    trade_date: Optional[str] = None,
    ev_threshold: float = 0.03,
    risk_threshold: float = 0.01,
) -> Tuple[pd.DataFrame, Path, Optional[Path]]:
    """
    输出新聚宽窗口信号：
    - 新目录：docs/signals_evrp/
    - latest：top10_evrp_latest.csv
    - dated： top10_evrp_YYYYMMDD.csv
    """
    signal_df = build_evrp_window_signal(
        df=df,
        ev_threshold=ev_threshold,
        risk_threshold=risk_threshold,
    )

    latest_path = str(Path(out_dir) / latest_filename)
    latest, dated = write_latest_and_dated_signal(
        df=signal_df,
        latest_path=latest_path,
        dated_dir=out_dir,
        dated_prefix=dated_prefix,
        trade_date=trade_date,
    )
    return signal_df, latest, dated
