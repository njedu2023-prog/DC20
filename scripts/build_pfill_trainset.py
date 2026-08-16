#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
build_pfill_trainset.py

用途：
- 基于 trade_date 构建 P_fill 学习宽表
- 合并四层输入：
    1) features_base_{trade_date}.csv
    2) features_limit_{trade_date}.csv
    3) fill_truth_{trade_date}.csv
    4) pred_source_latest / 归档 prior 快照（若存在）

输出：
- data/market/pfill_trainset_{trade_date}.csv
- data/market/pfill_trainset_{trade_date}.meta.json

当前只做“样本拼装层”：
- 统一主键 trade_date + ts_code
- 清理重复列 / 冲突列
- 审计 coverage / prior 来源 / 标签分布

不负责：
- 模型训练
- 模型推理
- run_v2.py 接入
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

from top10decision.decision.contracts import PFILL_EXECUTION_CONTRACT, PFILL_TRUTH_VERSION


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
    hit = first_existing(out, ["trade_date", "signal_date", "日期"])
    if hit is None:
        out["trade_date"] = trade_date
    else:
        if hit != "trade_date":
            out = out.rename(columns={hit: "trade_date"})
        out["trade_date"] = out["trade_date"].map(norm_ymd)
        out["trade_date"] = out["trade_date"].replace("", trade_date).fillna(trade_date)
    return out


def dedupe_by_key(df: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return df.drop_duplicates(subset=list(keys), keep="first").copy()


def cols_not_all_nan(df: pd.DataFrame, cols: Iterable[str]) -> List[str]:
    keep: List[str] = []
    for c in cols:
        if c in df.columns and df[c].notna().any():
            keep.append(c)
    return keep


def add_missing_indicator(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[f"{c}__is_missing"] = out[c].isna().astype(int)
    return out


# =========================================================
# 路径
# =========================================================

@dataclass
class Paths:
    project_root: Path
    market_dir: Path
    features_base: Path
    features_limit: Path
    fill_truth: Path
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
        fill_truth=market_dir / f"fill_truth_{trade_date}.csv",
        out_csv=market_dir / f"pfill_trainset_{trade_date}.csv",
        out_meta=market_dir / f"pfill_trainset_{trade_date}.meta.json",
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
    """
    尽量兼容旧链路与新链路。
    优先 trade_date 归档，其次 latest。
    """
    cands: List[Tuple[str, Path]] = [
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
    return cands


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


def load_fill_truth(path: Path, trade_date: str) -> pd.DataFrame:
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(f"找不到或读不到 fill_truth 文件：{path}")
    df = ensure_ts_code(df, context="fill_truth")
    df = ensure_trade_date(df, trade_date)
    df = dedupe_by_key(df, ["trade_date", "ts_code"])

    required = ["y_fill", "fill_label_quality", "label_version", "execution_contract"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"fill_truth 缺少关键列：{missing}")
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
        "premiumret", "premium_ret", "e_ret", "e_ret_pred", "ev", "ev_score",
        "target_weight", "target_date", "verify_status",
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
        if c in protected or c.startswith("prior_"):
            continue
        final_rename[c] = f"prior_{c}"
    out = out.rename(columns=final_rename)
    return out


def choose_truth_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "trade_date", "ts_code", "exec_date", "target_date", "name",
        "sample_maturity", "label_ready_fill", "label_ready_ret",
        "y_fill", "fill_label_quality", "entry_price_proxy_t1", "entry_price_proxy_mode",
        "label_version", "execution_contract", "buy_window_start", "buy_window_end",
        "auction_price_t1", "auction_amount_t1", "auction_vol_t1",
        "minute_open_t1", "minute_open_available_t1",
        "open_t1", "high_t1", "low_t1", "close_t1",
        "up_limit_t1", "down_limit_t1", "limit_type_t1", "open_times_t1",
        "break_open_times_t1", "first_seal_time_t1", "last_seal_time_t1",
        "seal_amount_t1", "is_suspended_t1",
    ]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()


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
    truth_df_raw = load_fill_truth(paths.fill_truth, trade_date)
    truth_df_raw = truth_df_raw[
        truth_df_raw["label_version"].astype(str).eq(PFILL_TRUTH_VERSION)
        & truth_df_raw["execution_contract"].astype(str).eq(PFILL_EXECUTION_CONTRACT)
    ].copy()
    if truth_df_raw.empty:
        raise ValueError(
            f"trade_date={trade_date} 没有符合 {PFILL_TRUTH_VERSION} 的开盘竞价 P_fill 真值"
        )
    truth_df = choose_truth_columns(truth_df_raw)

    if "label_ready_fill" in truth_df.columns:
        truth_df = truth_df[truth_df["label_ready_fill"].fillna(0).astype(int) == 1].copy()

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

    dup_cols = [c for c in train.columns if c.endswith("__dup_base") or c.endswith("__dup_limit") or c.endswith("__dup_prior")]
    if dup_cols:
        train = train.drop(columns=dup_cols)

    train = dedupe_by_key(train, ["trade_date", "ts_code"])

    if "name" not in train.columns and "prior_name" in train.columns:
        train = train.rename(columns={"prior_name": "name"})

    train["dataset_split"] = "raw_train_pool"

    coverage_cols = ["y_fill", "fill_label_quality", "open_t1", "up_limit_t1", "open_times_t1", "seal_amount_t1"]
    existing_cov_cols = [c for c in coverage_cols if c in train.columns]
    train["feature_coverage_score"] = train[existing_cov_cols].notna().mean(axis=1).round(4) if existing_cov_cols else 0.0

    weak_truth = train["fill_label_quality"].astype(str).str.contains("weak", case=False, na=False)
    prior_core_cols = [c for c in ["prior_prob_prior", "prior_strength_score", "prior_theme_boost", "rank"] if c in train.columns]
    if prior_core_cols:
        train["is_cold_start"] = (weak_truth | train[prior_core_cols].isna().all(axis=1)).astype(int)
    else:
        train["is_cold_start"] = 1

    train["sample_weight"] = 1.0
    if "rank" in train.columns:
        rank_num = pd.to_numeric(train["rank"], errors="coerce")
        train.loc[rank_num.notna() & (rank_num <= 10), "sample_weight"] = 1.25
        train.loc[rank_num.notna() & (rank_num <= 3), "sample_weight"] = 1.5
    quality_s = train["fill_label_quality"].astype(str)
    train.loc[quality_s.str.startswith("strong", na=False), "sample_weight"] += 0.25
    train.loc[quality_s.str.startswith("weak", na=False), "sample_weight"] -= 0.25
    train["sample_weight"] = train["sample_weight"].clip(lower=0.5, upper=2.0)

    if add_missing_flags:
        important_missing_cols = [
            "open_times_t1", "seal_amount_t1", "limit_type_t1", "is_suspended_t1",
            "prior_prob_prior", "prior_strength_score", "prior_theme_boost",
        ]
        train = add_missing_indicator(train, [c for c in important_missing_cols if c in train.columns])

    front = [
        "trade_date", "exec_date", "target_date", "ts_code", "name", "rank",
        "sample_maturity", "label_ready_fill", "label_ready_ret",
        "y_fill", "fill_label_quality", "entry_price_proxy_t1", "entry_price_proxy_mode",
        "dataset_split", "sample_weight", "is_cold_start", "feature_coverage_score",
        "label_version", "execution_contract", "buy_window_start", "buy_window_end",
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
    )
    return train, meta


# =========================================================
# meta
# =========================================================

def nonnull_ratio(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns or len(df) == 0:
        return 0.0
    return round(float(df[col].notna().mean()), 6)


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
) -> Dict[str, object]:
    coverage_cols = [
        "y_fill", "fill_label_quality", "entry_price_proxy_t1", "open_t1", "up_limit_t1",
        "open_times_t1", "seal_amount_t1", "prior_prob_prior", "prior_strength_score",
        "prior_theme_boost", "rank",
    ]
    coverage = {c: nonnull_ratio(train_df, c) for c in coverage_cols if c in train_df.columns}

    y_dist = {str(k): int(v) for k, v in train_df["y_fill"].value_counts(dropna=False).to_dict().items()} if "y_fill" in train_df.columns else {}
    quality_dist = {str(k): int(v) for k, v in train_df["fill_label_quality"].astype(str).value_counts(dropna=False).to_dict().items()} if "fill_label_quality" in train_df.columns else {}
    maturity_dist = {str(k): int(v) for k, v in train_df["sample_maturity"].astype(str).value_counts(dropna=False).to_dict().items()} if "sample_maturity" in train_df.columns else {}

    meta: Dict[str, object] = {
        "trade_date": trade_date,
        "rows": int(len(train_df)),
        "fill_truth_ready_only": True,
        "label_version": PFILL_TRUTH_VERSION,
        "execution_contract": PFILL_EXECUTION_CONTRACT,
        "source": {
            "features_base": str(paths.features_base),
            "features_limit": str(paths.features_limit),
            "fill_truth": str(paths.fill_truth),
            "prior_mode": prior.mode,
            "prior_path": str(prior.path) if prior.path else "",
        },
        "output": str(paths.out_csv),
        "input_rows": {
            "features_base": int(len(base_df)),
            "features_limit": int(len(limit_df)),
            "fill_truth": int(len(truth_df)),
            "prior": int(len(prior_df)),
        },
        "coverage": coverage,
        "label_distribution": y_dist,
        "quality_distribution": quality_dist,
        "sample_maturity_distribution": maturity_dist,
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
            "本文件是 P_fill 训练前宽表，不是训练结果文件。",
            "训练集只保留 label_ready_fill=1 的成熟样本。",
            "P_fill 只学习开盘集合竞价是否可成交；盘中后来开板不算竞价成交。",
            "dataset_split 当前只标 raw_train_pool，正式 train/valid/test 时间切分在 train_pfill.py 再做。",
            "prior 若缺失不会阻断样本拼装，但会在 meta 中记录 prior_mode=missing。",
        ],
    }
    return meta


# =========================================================
# CLI
# =========================================================

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="构建 P_fill 训练宽表 pfill_trainset_{trade_date}.csv")
    ap.add_argument("--trade-date", required=True, help="T 日，格式 YYYYMMDD")
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

    print(f"[build_pfill_trainset] trade_date={trade_date}")
    print(f"[build_pfill_trainset] rows={len(train_df)}")
    print(f"[build_pfill_trainset] out={paths.out_csv}")
    print(f"[build_pfill_trainset] meta={paths.out_meta}")
    print(f"[build_pfill_trainset] prior_mode={meta['source']['prior_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
