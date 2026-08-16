#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
build_eret_trainset.py

用途：
- 基于 trade_date 构建 E_ret 学习宽表
- 合并四层输入：
    1) features_base_{trade_date}.csv
    2) features_limit_{trade_date}.csv
    3) eret_truth_{trade_date}.csv
    4) pred_source_latest / 归档 prior 快照（若存在）

输出：
- data/market/eret_trainset_{trade_date}.csv
- data/market/eret_trainset_{trade_date}.meta.json

本版修复重点：
- 强制保护 Feature Store V2 历史滚动字段，避免进入 E_ret 训练集后继续全空。
- 合并后对 ret_2d/ret_5d/ret_10d、volatility、atr、downside_vol、max_drawdown、tail_risk、bid_ask/spread 做专项审计。
- 若 trainset 中字段为空但 features_base 中有值，自动用 features_base 的非空字段回填。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from top10decision.decision.contracts import (
    ERET_COMPAT_TARGET_COLUMN,
    ERET_HOLDING_MODE,
    ERET_TARGET_COLUMN,
    ERET_TRUTH_VERSION,
)


# =========================================================
# Feature Store V2 关键字段
# =========================================================

FS_V2_HISTORY_FEATURES: List[str] = [
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "volatility_5d",
    "volatility_10d",
    "volatility_20d",
    "atr",
    "downside_vol",
    "max_drawdown_20d",
    "tail_risk_score",
    "bid_ask_proxy",
    "spread_proxy",
]


# =========================================================
# 基础工具
# =========================================================

def norm_ymd(x: object) -> str:
    s = str(x or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return s


def first_existing(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    cols = {str(c).strip(): c for c in df.columns}
    for c in candidates:
        if c in cols:
            return cols[c]

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        hit = lower_map.get(str(c).lower())
        if hit is not None:
            return hit
    return None


def safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    return pd.read_csv(path)


def ensure_ts_code(df: pd.DataFrame, context: str) -> pd.DataFrame:
    hit = first_existing(df, ["ts_code", "code", "symbol", "证券代码"])
    if hit is None:
        raise ValueError(f"{context} 缺少 ts_code/code/symbol 列")
    out = df.copy()
    if hit != "ts_code":
        out = out.rename(columns={hit: "ts_code"})
    out["ts_code"] = out["ts_code"].astype(str).str.strip()
    return out


def ensure_trade_date(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    out = df.copy()
    if "trade_date" not in out.columns:
        out["trade_date"] = trade_date
    out["trade_date"] = out["trade_date"].map(norm_ymd)
    out = out[out["trade_date"] == trade_date].copy()
    return out


def dedupe_by_key(df: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    keys = [k for k in keys if k in df.columns]
    if not keys:
        return df.copy()
    return df.drop_duplicates(subset=list(keys), keep="first").copy()


def cols_not_all_nan(df: pd.DataFrame, cols: Sequence[str]) -> List[str]:
    out: List[str] = []
    for c in cols:
        if c in df.columns and not df[c].isna().all():
            out.append(c)
    return out


def add_missing_indicator(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[f"{c}__is_missing"] = out[c].isna().astype(int)
    return out


def nonnull_ratio(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns or len(df) == 0:
        return 0.0
    return round(float(df[col].notna().mean()), 6)


# =========================================================
# 路径
# =========================================================

@dataclass
class Paths:
    project_root: Path
    market_dir: Path
    features_base: Path
    features_limit: Path
    eret_truth: Path
    out_csv: Path
    out_meta: Path


def detect_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_paths(trade_date: str, project_root: Optional[Path] = None) -> Paths:
    root = project_root or detect_project_root()
    market_dir = root / "data" / "market"
    return Paths(
        project_root=root,
        market_dir=market_dir,
        features_base=market_dir / f"features_base_{trade_date}.csv",
        features_limit=market_dir / f"features_limit_{trade_date}.csv",
        eret_truth=market_dir / f"eret_truth_{trade_date}.csv",
        out_csv=market_dir / f"eret_trainset_{trade_date}.csv",
        out_meta=market_dir / f"eret_trainset_{trade_date}.meta.json",
    )


# =========================================================
# prior 搜索
# =========================================================

@dataclass
class PriorSource:
    path: Optional[Path]
    mode: str
    df: pd.DataFrame


def candidate_prior_paths(root: Path, trade_date: str) -> List[Tuple[str, Path]]:
    return [
        ("dated_pred_source_archive", root / "data" / "pred" / "archive" / f"pred_source_{trade_date}.csv"),
        ("dated_pred_source", root / "data" / "pred" / f"pred_source_{trade_date}.csv"),
        ("latest_pred_source", root / "data" / "pred" / "pred_source_latest.csv"),
        ("dated_pred_top10", root / "data" / "pred" / f"pred_top10_{trade_date}.csv"),
        ("latest_pred_top10", root / "data" / "pred" / "pred_top10_latest.csv"),
        ("dated_docs_signals", root / "docs" / "signals" / f"top10_{trade_date}.csv"),
        ("latest_docs_signals", root / "docs" / "signals" / "top10_latest.csv"),
        ("dated_decision_candidates", root / "data" / "decision" / f"decision_candidates_{trade_date}.csv"),
        ("latest_decision_candidates", root / "data" / "decision" / "decision_candidates_latest.csv"),
        ("dated_decision_rank", root / "outputs" / "decision" / f"pred_decision_{trade_date}.csv"),
        ("latest_decision_rank", root / "outputs" / "decision" / "pred_decision_latest.csv"),
    ]


def pick_prior_source(root: Path, trade_date: str) -> PriorSource:
    for mode, path in candidate_prior_paths(root, trade_date):
        df = safe_read_csv(path)
        if df.empty:
            continue
        try:
            df = ensure_ts_code(df, context=f"prior({mode})")
        except Exception:
            continue
        df = ensure_trade_date(df, trade_date)
        df = dedupe_by_key(df, ["trade_date", "ts_code"])
        if df.empty:
            continue
        return PriorSource(path=path, mode=mode, df=df)
    return PriorSource(path=None, mode="missing", df=pd.DataFrame(columns=["trade_date", "ts_code"]))


# =========================================================
# 读入与标准化
# =========================================================

def load_features_base(path: Path, trade_date: str) -> pd.DataFrame:
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(f"找不到或读不到 features_base 文件：{path}")
    df = ensure_ts_code(df, context="features_base")
    df = ensure_trade_date(df, trade_date)
    df = dedupe_by_key(df, ["trade_date", "ts_code"])
    return df


def load_features_limit(path: Path, trade_date: str) -> pd.DataFrame:
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(f"找不到或读不到 features_limit 文件：{path}")
    df = ensure_ts_code(df, context="features_limit")
    df = ensure_trade_date(df, trade_date)
    df = dedupe_by_key(df, ["trade_date", "ts_code"])
    return df


def load_eret_truth(path: Path, trade_date: str) -> pd.DataFrame:
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(f"找不到或读不到 eret_truth 文件：{path}")
    df = ensure_ts_code(df, context="eret_truth")
    df = ensure_trade_date(df, trade_date)
    df = dedupe_by_key(df, ["trade_date", "ts_code"])

    required = [
        "label_ready_ret",
        "eret_sample_eligible",
        "eret_label_quality",
        ERET_TARGET_COLUMN,
        ERET_COMPAT_TARGET_COLUMN,
        "premium_ret_t1_to_t2",
        "entry_price_t1",
        "exit_price_tplus1_timed",
        "exit_price_tplus1_open",
        "eret_truth_version",
        "return_holding_mode",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"eret_truth 缺少关键列：{missing}")
    return df


# =========================================================
# 列裁剪与重命名
# =========================================================

def choose_prior_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    key_cols = ["trade_date", "ts_code"]
    prefer_cols = [
        "name", "rank", "score", "prob", "prob_prior", "probability",
        "strengthscore", "StrengthScore", "strength_score",
        "themeboost", "ThemeBoost", "theme_boost",
        "board", "industry", "concept", "reason", "label", "hot_rank",
        "turnover_rate", "seal_amount", "open_times",
    ]
    banned_cols = {
        "y_fill", "fill_label_quality", "entry_price_proxy_t1", "entry_price_proxy_mode",
        "premiumret", "premium_ret", "premium_ret_t1_to_t2",
        "e_ret", "e_ret_pred", "ev", "ev_score",
        ERET_TARGET_COLUMN, ERET_COMPAT_TARGET_COLUMN, "target_weight", "target_date", "verify_status",
        "eret_sample_eligible", "eret_label_quality", "label_ready_ret",
    }

    keep = list(key_cols)
    for c in df.columns:
        cl = str(c).strip()
        if cl in key_cols or cl in banned_cols:
            continue
        if cl in prefer_cols:
            keep.append(cl)

    if len(keep) == len(key_cols):
        extra: List[str] = []
        for c in df.columns:
            if c in key_cols or c in banned_cols:
                continue
            extra.append(c)
        keep.extend(extra[:20])

    out = df[cols_not_all_nan(df, keep)].copy()

    rename_map: Dict[str, str] = {}
    alias_groups = [
        (["prob", "probability"], "prob_prior"),
        (["StrengthScore", "strengthscore"], "strength_score"),
        (["ThemeBoost", "themeboost"], "theme_boost"),
    ]
    for srcs, target in alias_groups:
        hit = first_existing(out, srcs)
        if hit is not None and target not in out.columns:
            rename_map[hit] = target
    out = out.rename(columns=rename_map)

    protected = {"trade_date", "ts_code", "name", "rank"}
    final_rename: Dict[str, str] = {}
    for c in out.columns:
        if c in protected or str(c).startswith("prior_"):
            continue
        final_rename[c] = f"prior_{c}"
    out = out.rename(columns=final_rename)
    return out


def choose_truth_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "trade_date", "exec_date", "target_date", "entry_date", "exit_date", "ts_code", "name",
        "sample_maturity", "label_ready_fill", "label_ready_ret",
        "y_fill", "fill_label_quality", "eret_sample_eligible", "eret_label_quality",
        "entry_price_t1", "entry_price_proxy_t1", "entry_price_proxy_mode",
        "entry_price_t_opening_auction",
        "exit_price_tplus1_timed", "exit_price_tplus1_open", "exit_price_source", "exit_on_time",
        "exit_reason", "take_profit_price_tplus1", "stop_loss_price_tplus1",
        "latest_exit_time", "exit_policy_version",
        "exit_price_t2_close", "close_t2",
        ERET_TARGET_COLUMN, ERET_COMPAT_TARGET_COLUMN, "premium_ret_t1_to_t2",
        "eret_truth_version", "return_holding_mode",
        "execution_contract",
        "buy_window_start", "buy_window_end",
        "open_t1", "high_t1", "low_t1", "close_t1",
        "up_limit_t1", "down_limit_t1", "limit_type_t1", "open_times_t1",
        "break_open_times_t1", "first_seal_time_t1", "last_seal_time_t1",
        "seal_amount_t1", "is_suspended_t1",
        "open_t2", "high_t2", "low_t2", "close_t2", "vol_t2", "amount_t2", "pct_chg_t2",
    ]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()


# =========================================================
# Feature Store V2 字段保护与审计
# =========================================================

def fs_v2_nonnull_report(df: pd.DataFrame) -> Dict[str, float]:
    return {c: nonnull_ratio(df, c) for c in FS_V2_HISTORY_FEATURES}


def restore_fs_v2_features_from_base(train: pd.DataFrame, base_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """
    强制让 E_ret trainset 吃到 features_base 的 V2 历史滚动字段。

    设计原则：
    - 不修改样本集合，只按 trade_date + ts_code 补字段。
    - 若 train 中同名字段已存在且有值，保留。
    - 若 train 中同名字段不存在或全空，而 base 中有值，用 base 回填。
    - 若 train/base 都没有值，只记录审计，不伪造数据。
    """
    out = train.copy()
    audit: Dict[str, object] = {
        "protected_cols": list(FS_V2_HISTORY_FEATURES),
        "base_nonnull_rate": fs_v2_nonnull_report(base_df),
        "before_nonnull_rate": fs_v2_nonnull_report(out),
        "restored_cols": [],
        "missing_in_base": [],
        "still_all_null_after_restore": [],
    }

    key_cols = ["trade_date", "ts_code"]
    if not all(c in base_df.columns for c in key_cols) or not all(c in out.columns for c in key_cols):
        audit["key_error"] = "train/base 缺少 trade_date 或 ts_code，无法执行 FS V2 回填。"
        return out, audit

    available = [c for c in FS_V2_HISTORY_FEATURES if c in base_df.columns]
    audit["missing_in_base"] = [c for c in FS_V2_HISTORY_FEATURES if c not in base_df.columns]

    if not available:
        audit["after_nonnull_rate"] = fs_v2_nonnull_report(out)
        return out, audit

    fs_part = base_df[key_cols + available].copy()
    fs_part = dedupe_by_key(fs_part, key_cols)

    for c in available:
        src = f"{c}__fs_v2_base"
        if src in out.columns:
            out = out.drop(columns=[src])
        out = out.merge(fs_part[key_cols + [c]].rename(columns={c: src}), on=key_cols, how="left")

        before = nonnull_ratio(out, c)
        src_rate = nonnull_ratio(out, src)

        if c not in out.columns:
            out[c] = out[src]
            restored = src_rate > 0
        elif before <= 0 and src_rate > 0:
            out[c] = out[src]
            restored = True
        else:
            out[c] = out[c].combine_first(out[src])
            restored = nonnull_ratio(out, c) > before

        if restored:
            audit["restored_cols"].append(c)

        out = out.drop(columns=[src])

    after_report = fs_v2_nonnull_report(out)
    audit["after_nonnull_rate"] = after_report
    audit["still_all_null_after_restore"] = [c for c, rate in after_report.items() if rate <= 0]
    return out, audit


# =========================================================
# 合并
# =========================================================

def split_feature_columns(
    base_df: pd.DataFrame,
    limit_df: pd.DataFrame,
    truth_df: pd.DataFrame,
    prior_df: pd.DataFrame,
) -> Dict[str, List[str]]:
    keys = {"trade_date", "ts_code"}
    return {
        "base_cols": [c for c in base_df.columns if c not in keys],
        "limit_cols": [c for c in limit_df.columns if c not in keys],
        "truth_cols": [c for c in truth_df.columns if c not in keys],
        "prior_cols": [c for c in prior_df.columns if c not in keys],
    }


def build_trainset(
    trade_date: str,
    paths: Paths,
    add_missing_flags: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    base_df = load_features_base(paths.features_base, trade_date)
    limit_df = load_features_limit(paths.features_limit, trade_date)
    truth_df_raw = load_eret_truth(paths.eret_truth, trade_date)
    truth_df_raw = truth_df_raw[
        truth_df_raw["eret_truth_version"].astype(str).eq(ERET_TRUTH_VERSION)
        & truth_df_raw["return_holding_mode"].astype(str).eq(ERET_HOLDING_MODE)
    ].copy()
    if truth_df_raw.empty:
        raise ValueError(
            f"trade_date={trade_date} 没有符合 {ERET_TRUTH_VERSION} 的T日竞价到T+1择机退出 E_ret 真值"
        )
    truth_df = choose_truth_columns(truth_df_raw)

    if "label_ready_ret" in truth_df.columns:
        truth_df = truth_df[truth_df["label_ready_ret"].fillna(0).astype(int) == 1].copy()
    if "eret_sample_eligible" in truth_df.columns:
        truth_df = truth_df[truth_df["eret_sample_eligible"].fillna(0).astype(int) == 1].copy()

    if truth_df.empty:
        raise ValueError(
            f"trade_date={trade_date} 在 eret_truth 中没有可训练样本："
            f"要求 label_ready_ret=1 且 eret_sample_eligible=1"
        )

    prior = pick_prior_source(paths.project_root, trade_date)
    prior_df = choose_prior_columns(prior.df)

    train = truth_df.copy()

    base_merge_cols = ["trade_date", "ts_code"] + [c for c in base_df.columns if c not in {"trade_date", "ts_code"}]
    train = train.merge(
        base_df[base_merge_cols],
        on=["trade_date", "ts_code"],
        how="left",
        suffixes=("", "__dup_base"),
    )

    # 二次保险：合并后立刻从 features_base 强制恢复 FS V2 字段。
    train, fs_v2_audit = restore_fs_v2_features_from_base(train, base_df)

    limit_add_cols = [c for c in limit_df.columns if c not in {"trade_date", "ts_code"} and c not in train.columns]
    limit_overlap_cols = [c for c in limit_df.columns if c not in {"trade_date", "ts_code"} and c in train.columns]

    if limit_add_cols:
        train = train.merge(
            limit_df[["trade_date", "ts_code"] + limit_add_cols],
            on=["trade_date", "ts_code"],
            how="left",
            suffixes=("", "__dup_limit"),
        )

    for c in limit_overlap_cols:
        tmp_col = f"{c}__from_limit"
        sub = limit_df[["trade_date", "ts_code", c]].rename(columns={c: tmp_col})
        train = train.merge(sub, on=["trade_date", "ts_code"], how="left")
        train[c] = train[c].combine_first(train[tmp_col]) if c in train.columns else train[tmp_col]
        train = train.drop(columns=[tmp_col])

    if not prior_df.empty:
        prior_cols = [c for c in prior_df.columns if c not in {"trade_date", "ts_code"}]
        train = train.merge(
            prior_df[["trade_date", "ts_code"] + prior_cols],
            on=["trade_date", "ts_code"],
            how="left",
            suffixes=("", "__dup_prior"),
        )

    dup_cols = [
        c for c in train.columns
        if c.endswith("__dup_base") or c.endswith("__dup_limit") or c.endswith("__dup_prior")
    ]
    if dup_cols:
        train = train.drop(columns=dup_cols)

    # 三次保险：limit/prior 合并后再次恢复，防止后续同名覆盖。
    train, fs_v2_audit_final = restore_fs_v2_features_from_base(train, base_df)
    fs_v2_audit["final_after_all_merges"] = fs_v2_audit_final

    train = dedupe_by_key(train, ["trade_date", "ts_code"])

    if "name" not in train.columns and "prior_name" in train.columns:
        train = train.rename(columns={"prior_name": "name"})

    train["dataset_split"] = "raw_train_pool"

    coverage_cols = [
        ERET_TARGET_COLUMN,
        ERET_COMPAT_TARGET_COLUMN,
        "premium_ret_t1_to_t2",
        "entry_price_t1",
        "entry_price_t_opening_auction",
        "exit_price_tplus1_open",
        "exit_price_t2_close",
        "y_fill",
        "fill_label_quality",
        "open_t1",
        "open_times_t1",
        "seal_amount_t1",
    ]
    existing_cov_cols = [c for c in coverage_cols if c in train.columns]
    train["feature_coverage_score"] = train[existing_cov_cols].notna().mean(axis=1).round(4) if existing_cov_cols else 0.0

    label_q = train["eret_label_quality"].astype(str) if "eret_label_quality" in train.columns else pd.Series([], dtype=str)
    prior_core_cols = [c for c in ["prior_prob_prior", "prior_strength_score", "prior_theme_boost", "rank"] if c in train.columns]
    if prior_core_cols:
        train["is_cold_start"] = (
            label_q.str.contains("weak|missing", case=False, na=False) |
            train[prior_core_cols].isna().all(axis=1)
        ).astype(int)
    else:
        train["is_cold_start"] = 1

    train["sample_weight"] = 1.0
    if "rank" in train.columns:
        rank_num = pd.to_numeric(train["rank"], errors="coerce")
        train.loc[rank_num.notna() & (rank_num <= 10), "sample_weight"] = 1.25
        train.loc[rank_num.notna() & (rank_num <= 3), "sample_weight"] = 1.5

    if "fill_label_quality" in train.columns:
        quality_s = train["fill_label_quality"].astype(str)
        train.loc[quality_s.str.startswith("strong", na=False), "sample_weight"] += 0.15
        train.loc[quality_s.str.startswith("weak", na=False), "sample_weight"] -= 0.15

    if "eret_label_quality" in train.columns:
        eret_q = train["eret_label_quality"].astype(str)
        train.loc[eret_q.str.startswith("strong", na=False), "sample_weight"] += 0.10
        train.loc[eret_q.str.contains("missing|weak", case=False, na=False), "sample_weight"] -= 0.10

    train["sample_weight"] = train["sample_weight"].clip(lower=0.5, upper=2.0)

    if add_missing_flags:
        important_missing_cols = [
            "open_times_t1", "seal_amount_t1", "limit_type_t1", "is_suspended_t1",
            "prior_prob_prior", "prior_strength_score", "prior_theme_boost",
            "pct_chg_t2", "amount_t2",
        ] + FS_V2_HISTORY_FEATURES
        train = add_missing_indicator(train, [c for c in important_missing_cols if c in train.columns])

    front = [
        "trade_date", "exec_date", "target_date", "entry_date", "exit_date",
        "ts_code", "name", "rank",
        "sample_maturity", "label_ready_fill", "label_ready_ret",
        "y_fill", "fill_label_quality",
        "eret_sample_eligible", "eret_label_quality",
        ERET_TARGET_COLUMN, ERET_COMPAT_TARGET_COLUMN, "premium_ret_t1_to_t2",
        "entry_price_t1", "entry_price_t_opening_auction", "entry_price_proxy_t1", "entry_price_proxy_mode",
        "exit_price_tplus1_timed", "exit_price_tplus1_open", "exit_price_source", "exit_on_time",
        "exit_reason", "take_profit_price_tplus1", "stop_loss_price_tplus1",
        "latest_exit_time", "exit_policy_version",
        "exit_price_t2_close", "close_t2",
        "dataset_split", "sample_weight", "is_cold_start", "feature_coverage_score",
        "eret_truth_version", "return_holding_mode",
    ]
    front = [c for c in front if c in train.columns]
    remain = [c for c in train.columns if c not in front]
    train = train[front + remain].copy()

    meta = build_meta(
        trade_date=trade_date,
        train_df=train,
        paths=paths,
        prior=prior,
        base_df=base_df,
        limit_df=limit_df,
        truth_df=truth_df,
        prior_df=prior_df,
        split_cols=split_feature_columns(base_df, limit_df, truth_df, prior_df),
        fs_v2_audit=fs_v2_audit,
    )
    return train, meta


# =========================================================
# meta
# =========================================================

def build_meta(
    trade_date: str,
    train_df: pd.DataFrame,
    paths: Paths,
    prior: PriorSource,
    base_df: pd.DataFrame,
    limit_df: pd.DataFrame,
    truth_df: pd.DataFrame,
    prior_df: pd.DataFrame,
    split_cols: Dict[str, List[str]],
    fs_v2_audit: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    coverage_cols = [
        ERET_TARGET_COLUMN, ERET_COMPAT_TARGET_COLUMN, "premium_ret_t1_to_t2",
        "entry_price_t1", "entry_price_t_opening_auction", "exit_price_tplus1_timed", "exit_price_tplus1_open", "exit_price_t2_close",
        "y_fill", "fill_label_quality",
        "open_t1", "open_times_t1", "seal_amount_t1",
        "prior_prob_prior", "prior_strength_score", "prior_theme_boost", "rank",
    ] + FS_V2_HISTORY_FEATURES
    coverage = {c: nonnull_ratio(train_df, c) for c in coverage_cols if c in train_df.columns}

    y_fill_dist = {str(k): int(v) for k, v in train_df["y_fill"].value_counts(dropna=False).to_dict().items()} if "y_fill" in train_df.columns else {}
    eret_quality_dist = {str(k): int(v) for k, v in train_df["eret_label_quality"].astype(str).value_counts(dropna=False).to_dict().items()} if "eret_label_quality" in train_df.columns else {}
    fill_quality_dist = {str(k): int(v) for k, v in train_df["fill_label_quality"].astype(str).value_counts(dropna=False).to_dict().items()} if "fill_label_quality" in train_df.columns else {}
    maturity_dist = {str(k): int(v) for k, v in train_df["sample_maturity"].astype(str).value_counts(dropna=False).to_dict().items()} if "sample_maturity" in train_df.columns else {}

    target_stats: Dict[str, Optional[float]] = {}
    target_col = ERET_TARGET_COLUMN
    if target_col in train_df.columns and len(train_df):
        s = pd.to_numeric(train_df[target_col], errors="coerce")
        target_stats = {
            "mean": round(float(s.mean()), 6) if s.notna().any() else None,
            "median": round(float(s.median()), 6) if s.notna().any() else None,
            "min": round(float(s.min()), 6) if s.notna().any() else None,
            "max": round(float(s.max()), 6) if s.notna().any() else None,
            "positive_rate": round(float((s > 0).mean()), 6) if s.notna().any() else None,
        }

    fs_v2_trainset_nonnull = fs_v2_nonnull_report(train_df)
    fs_v2_base_nonnull = fs_v2_nonnull_report(base_df)

    meta: Dict[str, object] = {
        "trade_date": trade_date,
        "rows": int(len(train_df)),
        "eret_truth_ready_only": True,
        "eret_sample_eligible_only": True,
        "target_column": ERET_TARGET_COLUMN,
        "eret_truth_version": ERET_TRUTH_VERSION,
        "return_holding_mode": ERET_HOLDING_MODE,
        "source": {
            "features_base": str(paths.features_base),
            "features_limit": str(paths.features_limit),
            "eret_truth": str(paths.eret_truth),
            "prior_mode": prior.mode,
            "prior_path": str(prior.path) if prior.path else "",
        },
        "output": str(paths.out_csv),
        "input_rows": {
            "features_base": int(len(base_df)),
            "features_limit": int(len(limit_df)),
            "eret_truth_after_filter": int(len(truth_df)),
            "prior": int(len(prior_df)),
        },
        "coverage": coverage,
        "fs_v2_history_feature_audit": {
            "base_nonnull_rate": fs_v2_base_nonnull,
            "trainset_nonnull_rate": fs_v2_trainset_nonnull,
            "all_core_fields_present_in_trainset": all(c in train_df.columns for c in FS_V2_HISTORY_FEATURES),
            "all_core_fields_present_in_base": all(c in base_df.columns for c in FS_V2_HISTORY_FEATURES),
            "still_all_null_in_trainset": [c for c, rate in fs_v2_trainset_nonnull.items() if rate <= 0],
            "restore_detail": fs_v2_audit or {},
        },
        "y_fill_distribution": y_fill_dist,
        "eret_quality_distribution": eret_quality_dist,
        "fill_quality_distribution": fill_quality_dist,
        "sample_maturity_distribution": maturity_dist,
        "target_stats": target_stats,
        "column_groups": {
            "base_cols": split_cols["base_cols"],
            "limit_cols": split_cols["limit_cols"],
            "truth_cols": split_cols["truth_cols"],
            "prior_cols": split_cols["prior_cols"],
        },
        "cold_start_ratio": round(float(train_df["is_cold_start"].mean()), 6) if "is_cold_start" in train_df.columns and len(train_df) else 0.0,
        "feature_coverage_score_mean": round(float(train_df["feature_coverage_score"].mean()), 6) if "feature_coverage_score" in train_df.columns and len(train_df) else 0.0,
        "sample_weight_mean": round(float(train_df["sample_weight"].mean()), 6) if "sample_weight" in train_df.columns and len(train_df) else 0.0,
        "notes": [
            "本文件是 E_ret 训练前宽表，不是训练结果文件。",
            "训练集只保留 label_ready_ret=1 且 eret_sample_eligible=1 的样本。",
            "主目标为T日开盘竞价买入到T+1日9:30开盘集合竞价退出的可执行收益。",
            "realized_ret_t1_to_t2 仅为Decision兼容别名；premium_ret_t1_to_t2继续保留原收盘口径，premium不受影响。",
            "dataset_split 当前只标 raw_train_pool，正式 train/valid/test 时间切分在 train_eret.py 再做。",
            "prior 若缺失不会阻断样本拼装，但会在 meta 中记录 prior_mode=missing。",
            "FS V2 历史滚动字段已做强制保护与专项审计，防止 E_ret 训练窗口继续吃空特征。",
        ],
    }
    return meta


# =========================================================
# CLI
# =========================================================

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="构建 E_ret 训练宽表 eret_trainset_{trade_date}.csv")
    ap.add_argument("--trade-date", required=True, help="D 信号日，格式 YYYYMMDD")
    ap.add_argument("--no-missing-flags", action="store_true", help="关闭缺失指示器列生成")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    trade_date = norm_ymd(args.trade_date)
    if len(trade_date) != 8:
        raise ValueError("--trade-date 必须是 YYYYMMDD")

    paths = build_paths(trade_date=trade_date)
    paths.market_dir.mkdir(parents=True, exist_ok=True)

    train_df, meta = build_trainset(
        trade_date=trade_date,
        paths=paths,
        add_missing_flags=(not args.no_missing_flags),
    )

    train_df.to_csv(paths.out_csv, index=False, encoding="utf-8-sig")
    paths.out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[build_eret_trainset] trade_date={trade_date}")
    print(f"[build_eret_trainset] rows={len(train_df)}")
    print(f"[build_eret_trainset] out={paths.out_csv}")
    print(f"[build_eret_trainset] meta={paths.out_meta}")
    print(f"[build_eret_trainset] prior_mode={meta['source']['prior_mode']}")
    print(f"[build_eret_trainset] fs_v2_trainset_nonnull={meta['fs_v2_history_feature_audit']['trainset_nonnull_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
