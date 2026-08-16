#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
build_eret_truth.py

用途：
- 基于 T 日 pred_source 候选池（不是全市场）
- 严格消费 sample_maturity_latest.csv，不自猜日期
- 只处理 ERET_READY=1 的成熟样本
- 优先复用 fill_truth_{trade_date}.csv 中的 entry_price_proxy_t1 作为买入价口径
- 使用执行日下一交易日(target_date)的9:30开盘集合竞价成交价构建 E_ret 真值层

输出：
- data/market/eret_truth_{trade_date}.csv
- data/market/eret_truth_{trade_date}.meta.json

当前阶段只做“真值层 / 标签层”：
- realized_ret_open_to_tplus1_open_0930（主目标）
- realized_ret_t1_to_t2（Decision 兼容别名，数值与主目标一致）
- exit_price_tplus1_timed
- eret_label_quality
- eret_sample_eligible

不负责：
- 训练样本宽表拼装
- 模型训练
- run_v2.py 接入

锚定原则：
1. 候选池入口必须是 pred_source，FS/raw 只能补字段，不得反向扩池
2. E_ret 的买入价口径优先复用 fill_truth / entry_price_proxy_t1
3. 训练日期机制必须复用 sample_maturity，不得另起一套时间逻辑
4. E_ret 服务于 EV，不是独立漂浮展示值；因此保留 fill 状态与样本可训练性审计
5. 退出规则固定为T+1 9:30开盘，不读取T+1盘中或收盘信息
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from top10decision.decision.contracts import (
    DECISION_EXECUTION_CONTRACT,
    ERET_COMPAT_TARGET_COLUMN,
    ERET_HOLDING_MODE,
    ERET_TARGET_COLUMN,
    ERET_TRUTH_VERSION,
    EXIT_LATEST_TIME,
)
from top10decision.decision.exit_policy import corporate_action_safe_return, simulate_tplus1_exit


# =========================
# 基础工具
# =========================

def norm_ymd(x: object) -> str:
    s = str(x or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return s


def to_float(x: object) -> float:
    try:
        if pd.isna(x):
            return float("nan")
        return float(x)
    except Exception:
        return float("nan")


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


# =========================
# 路径
# =========================

@dataclass
class Paths:
    project_root: Path
    market_dir: Path
    raw_root: Path
    pred_dir: Path
    pred_latest: Path
    pred_archive: Path
    fill_truth: Path
    maturity_csv: Path
    out_csv: Path
    out_meta: Path


def detect_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_paths(
    trade_date: str,
    maturity_csv: str = "",
    project_root: Optional[Path] = None,
) -> Paths:
    root = project_root or detect_project_root()
    market_dir = root / "data" / "market"
    raw_root = market_dir / "raw"
    pred_dir = root / "data" / "pred"
    pred_latest = pred_dir / "pred_source_latest.csv"
    pred_archive = pred_dir / "archive" / f"pred_source_{trade_date}.csv"
    fill_truth = market_dir / f"fill_truth_{trade_date}.csv"
    out_csv = market_dir / f"eret_truth_{trade_date}.csv"
    out_meta = market_dir / f"eret_truth_{trade_date}.meta.json"
    maturity_csv_path = Path(maturity_csv) if maturity_csv else (market_dir / "sample_maturity_latest.csv")

    return Paths(
        project_root=root,
        market_dir=market_dir,
        raw_root=raw_root,
        pred_dir=pred_dir,
        pred_latest=pred_latest,
        pred_archive=pred_archive,
        fill_truth=fill_truth,
        maturity_csv=maturity_csv_path,
        out_csv=out_csv,
        out_meta=out_meta,
    )


def snapshot_dir(raw_root: Path, ymd: str) -> Path:
    return raw_root / ymd[:4] / ymd


# =========================
# 成熟度解析结果读取
# =========================

@dataclass
class MaturityInfo:
    trade_date: str
    exec_date: str
    target_date: str
    sample_maturity: str
    label_ready_fill: int
    label_ready_ret: int
    fully_ready: int


def _parse_ready_flag(x: object) -> int:
    s = str(x or "").strip()
    return 1 if s in {"1", "true", "True", "TRUE"} else 0


def load_maturity_info(
    maturity_csv: Path,
    trade_date: str,
    exec_date_override: str = "",
    target_date_override: str = "",
) -> MaturityInfo:
    if not maturity_csv.exists():
        raise FileNotFoundError(
            f"缺少样本成熟度解析结果：{maturity_csv}。"
            f"请先运行 scripts/resolve_sample_maturity.py。"
        )

    df = safe_read_csv(maturity_csv)
    if df.empty:
        raise ValueError(f"样本成熟度文件为空：{maturity_csv}")
    if "trade_date" not in df.columns:
        raise ValueError(f"样本成熟度文件缺少 trade_date 列：{maturity_csv}")

    out = df.copy()
    out["trade_date"] = out["trade_date"].map(norm_ymd)
    hit = out[out["trade_date"] == trade_date].copy()
    if hit.empty:
        raise ValueError(
            f"样本成熟度文件中找不到 trade_date={trade_date}：{maturity_csv}"
        )

    row = hit.iloc[-1]
    exec_date = norm_ymd(exec_date_override) or norm_ymd(row.get("exec_date"))
    target_date = norm_ymd(target_date_override) or norm_ymd(row.get("target_date"))
    sample_maturity = str(row.get("sample_maturity") or "").strip()
    pfill_ready = _parse_ready_flag(row.get("PFILL_READY"))
    eret_ready = _parse_ready_flag(row.get("ERET_READY"))
    fully_ready = _parse_ready_flag(row.get("FULLY_READY"))

    if not exec_date:
        raise ValueError(
            f"trade_date={trade_date} 在成熟度文件中 exec_date 为空：{maturity_csv}"
        )
    if not target_date:
        raise ValueError(
            f"trade_date={trade_date} 在成熟度文件中 target_date 为空，"
            f"当前不满足 E_ret 真值构建条件：{maturity_csv}"
        )
    if eret_ready != 1:
        raise ValueError(
            f"trade_date={trade_date} 当前 ERET_READY != 1，"
            f"不得构建 eret_truth。sample_maturity={sample_maturity}"
        )

    return MaturityInfo(
        trade_date=trade_date,
        exec_date=exec_date,
        target_date=target_date,
        sample_maturity=sample_maturity,
        label_ready_fill=pfill_ready,
        label_ready_ret=eret_ready,
        fully_ready=fully_ready,
    )


# =========================
# 候选池 / fill_truth / T+1 退出真值
# =========================

def load_candidate_pool(paths: Paths, trade_date: str) -> Tuple[pd.DataFrame, str]:
    src_path = paths.pred_archive if paths.pred_archive.exists() else paths.pred_latest
    if not src_path.exists():
        raise FileNotFoundError(
            f"找不到 pred_source 候选池：{paths.pred_archive} / {paths.pred_latest}"
        )

    pred = safe_read_csv(src_path)
    if pred.empty:
        raise ValueError(f"pred_source 候选池为空：{src_path}")

    pred = ensure_ts_code(pred, context="pred_source")
    if "trade_date" in pred.columns:
        pred["trade_date"] = pred["trade_date"].map(norm_ymd)
        filtered = pred[pred["trade_date"] == trade_date].copy()
        if not filtered.empty:
            pred = filtered
        else:
            pred = pred.copy()
            pred["trade_date"] = trade_date
    else:
        pred["trade_date"] = trade_date

    pred = pred.drop_duplicates(subset=["trade_date", "ts_code"]).copy()
    if pred.empty:
        raise ValueError(f"pred_source 在 trade_date={trade_date} 下无候选样本：{src_path}")
    return pred, str(src_path)


def load_fill_truth(path: Path, trade_date: str) -> pd.DataFrame:
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(
            f"找不到或读不到 fill_truth 文件：{path}。"
            f"E_ret 必须优先复用 fill_truth / entry_price_proxy_t1。"
        )

    df = ensure_ts_code(df, context="fill_truth")
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].map(norm_ymd)
        df = df[df["trade_date"] == trade_date].copy()
    else:
        df["trade_date"] = trade_date

    need_cols = ["entry_price_proxy_t1", "entry_price_proxy_mode", "y_fill", "fill_label_quality"]
    miss = [c for c in need_cols if c not in df.columns]
    if miss:
        raise ValueError(f"fill_truth 缺少关键列：{miss}；文件={path}")

    keep_cols = [
        "trade_date",
        "exec_date",
        "target_date",
        "ts_code",
        "sample_maturity",
        "label_ready_fill",
        "label_ready_ret",
        "y_fill",
        "fill_label_quality",
        "entry_price_proxy_t1",
        "entry_price_proxy_mode",
        "buy_window_start",
        "buy_window_end",
        "open_t1",
        "high_t1",
        "low_t1",
        "close_t1",
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].drop_duplicates(subset=["trade_date", "ts_code"], keep="first").copy()
    return df


def load_target_daily(raw_root: Path, target_date: str) -> pd.DataFrame:
    base = snapshot_dir(raw_root, target_date)
    path = base / "daily.csv"
    df = safe_read_csv(path)
    if df.empty:
        raise FileNotFoundError(f"找不到或读不到 T+1 退出日 daily 真值文件：{path}")

    df = ensure_ts_code(df, context="target_daily")
    rename_map: Dict[str, str] = {}
    for srcs, target in [
        (["trade_date", "日期"], "market_target_date"),
        (["open", "开盘价"], "open_t2"),
        (["high", "最高价"], "high_t2"),
        (["low", "最低价"], "low_t2"),
        (["close", "收盘价"], "close_t2"),
        (["pre_close", "昨收价"], "pre_close_t2"),
        (["vol", "volume", "成交量"], "vol_t2"),
        (["amount", "成交额"], "amount_t2"),
        (["pct_chg", "涨跌幅"], "pct_chg_t2"),
    ]:
        hit = first_existing(df, srcs)
        if hit is not None:
            rename_map[hit] = target

    out = df.rename(columns=rename_map)
    keep = ["ts_code"] + [c for c in rename_map.values() if c in out.columns]
    out = out[keep].copy()
    if "market_target_date" not in out.columns:
        out["market_target_date"] = target_date
    out["market_target_date"] = out["market_target_date"].map(norm_ymd).replace("", target_date)
    out = out.drop_duplicates(subset=["ts_code"], keep="first").copy()

    auction = safe_read_csv(base / "stk_auction.csv")
    if not auction.empty:
        auction = ensure_ts_code(auction, context="target_stk_auction")
        price_col = first_existing(auction, ["price", "auction_price", "竞价成交价"])
        amount_col = first_existing(auction, ["amount", "auction_amount", "竞价成交额"])
        keep_auction = ["ts_code"] + [c for c in (price_col, amount_col) if c is not None]
        auction = auction[keep_auction].copy()
        rename_auction = {}
        if price_col is not None:
            rename_auction[price_col] = "auction_price_t2"
        if amount_col is not None:
            rename_auction[amount_col] = "auction_amount_t2"
        auction = auction.rename(columns=rename_auction).drop_duplicates("ts_code", keep="last")
        out = out.merge(auction, on="ts_code", how="left")

    limits = safe_read_csv(base / "stk_limit.csv")
    if not limits.empty:
        limits = ensure_ts_code(limits, context="target_stk_limit")
        down_col = first_existing(limits, ["down_limit", "跌停价"])
        if down_col is not None:
            limits = limits[["ts_code", down_col]].rename(columns={down_col: "down_limit_t2"})
            limits = limits.drop_duplicates("ts_code", keep="last")
            out = out.merge(limits, on="ts_code", how="left")
    return out


# =========================
# 标签逻辑
# =========================

def infer_eret_label(
    row: pd.Series,
) -> Tuple[
    Optional[float],
    str,
    int,
    Optional[float],
    str,
    int,
    str,
    Optional[float],
    Optional[float],
    str,
    str,
]:
    y_fill = row.get("y_fill")
    entry = to_float(row.get("entry_price_proxy_t1"))
    auction_open = to_float(row.get("auction_price_t2"))
    daily_open = to_float(row.get("open_t2"))
    exit_open = auction_open if pd.notna(auction_open) and auction_open > 0 else daily_open
    result = simulate_tplus1_exit(
        entry_price=entry,
        buy_close=row.get("close_t1"),
        target_pre_close=row.get("pre_close_t2"),
        open_price=exit_open,
        high_price=row.get("high_t2"),
        low_price=row.get("low_t2"),
        close_price=row.get("close_t2"),
        down_limit=row.get("down_limit_t2"),
        require_intraday=False,
    )

    if pd.isna(y_fill):
        return (
            None,
            "missing_fill_label",
            0,
            result.exit_price,
            result.source,
            int(result.executable),
            result.reason,
            result.take_profit_price,
            result.stop_loss_price,
            result.latest_exit_time,
            result.policy_version,
        )

    try:
        y_fill_int = int(float(y_fill))
    except Exception:
        y_fill_int = 0

    if y_fill_int != 1:
        return None, "not_filled_no_trade", 0, result.exit_price, result.source, int(result.executable), result.reason, result.take_profit_price, result.stop_loss_price, result.latest_exit_time, result.policy_version
    if pd.isna(entry) or entry <= 0:
        return None, "missing_entry_price_proxy", 0, result.exit_price, result.source, int(result.executable), result.reason, result.take_profit_price, result.stop_loss_price, result.latest_exit_time, result.policy_version
    if not result.executable or result.exit_price is None or result.exit_price <= 0:
        return None, result.reason or "missing_tplus1_exit", 0, result.exit_price, result.source, 0, result.reason, result.take_profit_price, result.stop_loss_price, result.latest_exit_time, result.policy_version

    realized_ret = corporate_action_safe_return(
        entry,
        result.exit_price,
        row.get("close_t1"),
        row.get("pre_close_t2"),
    )
    if not pd.notna(realized_ret):
        return None, "invalid_timed_exit_return", 0, result.exit_price, result.source, 1, result.reason, result.take_profit_price, result.stop_loss_price, result.latest_exit_time, result.policy_version
    return (
        float(realized_ret),
        f"strong_tplus1_open_0930_{result.reason}",
        1,
        float(result.exit_price),
        result.source,
        1,
        result.reason,
        result.take_profit_price,
        result.stop_loss_price,
        result.latest_exit_time,
        result.policy_version,
    )


def _compat_close_return(row: pd.Series) -> Optional[float]:
    try:
        if int(float(row.get("y_fill"))) != 1:
            return None
    except Exception:
        return None
    value = corporate_action_safe_return(
        row.get("entry_price_proxy_t1"),
        row.get("close_t2"),
        row.get("close_t1"),
        row.get("pre_close_t2"),
    )
    return float(value) if pd.notna(value) else None


# =========================
# 主流程
# =========================

def build_eret_truth(
    trade_date: str,
    exec_date: str,
    target_date: str,
    sample_maturity: str,
    label_ready_fill: int,
    label_ready_ret: int,
    paths: Paths,
) -> Tuple[pd.DataFrame, str, str, str]:
    pred, pred_source_path = load_candidate_pool(paths, trade_date)
    fill_truth = load_fill_truth(paths.fill_truth, trade_date)
    t2 = load_target_daily(paths.raw_root, target_date)

    pred_base_cols = ["trade_date", "ts_code"]
    for c in [
        "name",
        "rank",
        "prob",
        "StrengthScore",
        "ThemeBoost",
        "board",
        "run_id",
        "run_attempt",
        "commit_sha",
        "generated_at_utc",
    ]:
        if c in pred.columns:
            pred_base_cols.append(c)

    out = pred[pred_base_cols].drop_duplicates(subset=["trade_date", "ts_code"]).copy()
    # fill_truth is already the audited <=10% mechanism universe. An inner
    # join prevents excluded 20%/30%/no-limit securities re-entering E_ret.
    out = out.merge(fill_truth, on=["trade_date", "ts_code"], how="inner")
    out = out.merge(t2, on="ts_code", how="left")
    out["exec_date"] = out.get("exec_date", exec_date)
    out["exec_date"] = out["exec_date"].map(norm_ymd).replace("", exec_date)
    out["target_date"] = out.get("target_date", target_date)
    out["target_date"] = out["target_date"].map(norm_ymd).replace("", target_date)
    out["sample_maturity"] = out.get("sample_maturity", sample_maturity)
    out["sample_maturity"] = out["sample_maturity"].fillna(sample_maturity)
    out["label_ready_fill"] = out.get("label_ready_fill", label_ready_fill)
    out["label_ready_fill"] = out["label_ready_fill"].fillna(label_ready_fill).astype(int)
    out["label_ready_ret"] = int(label_ready_ret)

    labels = out.apply(infer_eret_label, axis=1, result_type="expand")
    labels.columns = [
        ERET_TARGET_COLUMN,
        "eret_label_quality",
        "eret_sample_eligible",
        "exit_price_tplus1_timed",
        "exit_price_source",
        "exit_on_time",
        "exit_reason",
        "take_profit_price_tplus1",
        "stop_loss_price_tplus1",
        "latest_exit_time",
        "exit_policy_version",
    ]
    out = pd.concat([out, labels], axis=1)

    out[ERET_COMPAT_TARGET_COLUMN] = out[ERET_TARGET_COLUMN]
    out["premium_ret_t1_to_t2"] = out.apply(_compat_close_return, axis=1)
    out["entry_date"] = out["exec_date"]
    out["exit_date"] = out["target_date"]
    out["entry_price_t1"] = out["entry_price_proxy_t1"]
    out["entry_price_t_opening_auction"] = out["entry_price_proxy_t1"]
    missing = pd.Series(float("nan"), index=out.index, dtype=float)
    auction_open = pd.to_numeric(out["auction_price_t2"], errors="coerce") if "auction_price_t2" in out.columns else missing
    daily_open = pd.to_numeric(out["open_t2"], errors="coerce") if "open_t2" in out.columns else missing
    out["exit_price_tplus1_open"] = auction_open.fillna(daily_open)
    out["exit_price_t2_close"] = out.get("close_t2")
    out["eret_truth_version"] = ERET_TRUTH_VERSION
    out["return_holding_mode"] = ERET_HOLDING_MODE
    out["execution_contract"] = DECISION_EXECUTION_CONTRACT

    front = [
        "trade_date",
        "exec_date",
        "target_date",
        "entry_date",
        "exit_date",
        "ts_code",
        "name",
        "rank",
        "prob",
        "StrengthScore",
        "ThemeBoost",
        "board",
        "sample_maturity",
        "label_ready_fill",
        "label_ready_ret",
        "y_fill",
        "fill_label_quality",
        "eret_sample_eligible",
        "eret_label_quality",
        "entry_price_t1",
        "entry_price_t_opening_auction",
        "entry_price_proxy_t1",
        "entry_price_proxy_mode",
        "take_profit_price_tplus1",
        "stop_loss_price_tplus1",
        "latest_exit_time",
        "exit_policy_version",
        "exit_price_tplus1_timed",
        "exit_price_tplus1_open",
        "exit_price_source",
        "exit_on_time",
        "exit_reason",
        "exit_price_t2_close",
        "close_t2",
        ERET_TARGET_COLUMN,
        ERET_COMPAT_TARGET_COLUMN,
        "premium_ret_t1_to_t2",
        "eret_truth_version",
        "return_holding_mode",
        "execution_contract",
    ]
    remain = [c for c in out.columns if c not in front]
    out = out[[c for c in front if c in out.columns] + remain].copy()

    return out, pred_source_path, str(paths.fill_truth), str(snapshot_dir(paths.raw_root, target_date) / "daily.csv")


def write_meta(
    df: pd.DataFrame,
    trade_date: str,
    exec_date: str,
    target_date: str,
    paths: Paths,
    pred_source_path: str,
    fill_truth_path: str,
    target_daily_path: str,
) -> None:
    eligible = df[df.get("eret_sample_eligible", 0) == 1].copy()

    payload = {
        "trade_date": trade_date,
        "exec_date": exec_date,
        "target_date": target_date,
        "rows": int(len(df)),
        "eret_sample_eligible_rows": int(len(eligible)),
        "sample_maturity": (
            df["sample_maturity"].astype(str).value_counts(dropna=False).to_dict()
            if "sample_maturity" in df.columns else {}
        ),
        "label_ready_fill": int(df["label_ready_fill"].fillna(0).sum()) if "label_ready_fill" in df.columns else 0,
        "label_ready_ret": int(df["label_ready_ret"].fillna(0).sum()) if "label_ready_ret" in df.columns else 0,
        "quality_counts": (
            df["eret_label_quality"].astype(str).value_counts(dropna=False).to_dict()
            if "eret_label_quality" in df.columns else {}
        ),
        "ret_stats": {
            "mean": float(eligible[ERET_TARGET_COLUMN].mean()) if not eligible.empty else None,
            "median": float(eligible[ERET_TARGET_COLUMN].median()) if not eligible.empty else None,
            "min": float(eligible[ERET_TARGET_COLUMN].min()) if not eligible.empty else None,
            "max": float(eligible[ERET_TARGET_COLUMN].max()) if not eligible.empty else None,
            "positive_rate": float((eligible[ERET_TARGET_COLUMN] > 0).mean()) if not eligible.empty else None,
        },
        "target_column": ERET_TARGET_COLUMN,
        "eret_truth_version": ERET_TRUTH_VERSION,
        "return_holding_mode": ERET_HOLDING_MODE,
        "execution_contract": DECISION_EXECUTION_CONTRACT,
        "exit_policy": {
            "version": str(df.get("exit_policy_version", pd.Series([""])).iloc[0]) if len(df) else "",
            "latest_exit_time": str(df.get("latest_exit_time", pd.Series([""])).iloc[0]) if len(df) else "",
            "source_counts": df.get("exit_price_source", pd.Series(dtype=str)).astype(str).value_counts().to_dict(),
        },
        "source": {
            "pred_source": pred_source_path,
            "fill_truth": fill_truth_path,
            "target_daily": target_daily_path,
            "target_auction": str(snapshot_dir(paths.raw_root, target_date) / "stk_auction.csv"),
            "sample_maturity_csv": str(paths.maturity_csv),
        },
        "output": str(paths.out_csv),
    }
    paths.out_meta.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="构建 E_ret 真值层 eret_truth_{trade_date}.csv")
    ap.add_argument("--trade-date", required=True, help="D 信号日，格式 YYYYMMDD")
    ap.add_argument(
        "--exec-date",
        default="",
        help="T 竞价买入日，格式 YYYYMMDD；默认从 sample_maturity_latest.csv 读取",
    )
    ap.add_argument(
        "--target-date",
        default="",
        help="T+1 退出日，格式 YYYYMMDD；默认从 sample_maturity_latest.csv 读取",
    )
    ap.add_argument(
        "--maturity-csv",
        default="",
        help="样本成熟度解析结果路径；留空默认 data/market/sample_maturity_latest.csv",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    trade_date = norm_ymd(args.trade_date)
    if len(trade_date) != 8:
        raise ValueError("--trade-date 必须是 YYYYMMDD")

    paths = build_paths(trade_date=trade_date, maturity_csv=args.maturity_csv)
    paths.market_dir.mkdir(parents=True, exist_ok=True)

    maturity_info = load_maturity_info(
        maturity_csv=paths.maturity_csv,
        trade_date=trade_date,
        exec_date_override=args.exec_date,
        target_date_override=args.target_date,
    )

    df, pred_source_path, fill_truth_path, target_daily_path = build_eret_truth(
        trade_date=trade_date,
        exec_date=maturity_info.exec_date,
        target_date=maturity_info.target_date,
        sample_maturity=maturity_info.sample_maturity,
        label_ready_fill=maturity_info.label_ready_fill,
        label_ready_ret=maturity_info.label_ready_ret,
        paths=paths,
    )

    df.to_csv(paths.out_csv, index=False, encoding="utf-8-sig")
    write_meta(
        df=df,
        trade_date=trade_date,
        exec_date=maturity_info.exec_date,
        target_date=maturity_info.target_date,
        paths=paths,
        pred_source_path=pred_source_path,
        fill_truth_path=fill_truth_path,
        target_daily_path=target_daily_path,
    )

    print(f"[build_eret_truth] trade_date={trade_date}")
    print(f"[build_eret_truth] exec_date={maturity_info.exec_date}")
    print(f"[build_eret_truth] target_date={maturity_info.target_date}")
    print(f"[build_eret_truth] sample_maturity={maturity_info.sample_maturity}")
    print(f"[build_eret_truth] label_ready_fill={maturity_info.label_ready_fill}")
    print(f"[build_eret_truth] label_ready_ret={maturity_info.label_ready_ret}")
    print(f"[build_eret_truth] rows={len(df)}")
    print(f"[build_eret_truth] eligible={(df['eret_sample_eligible'] == 1).sum() if 'eret_sample_eligible' in df.columns else 0}")
    print(f"[build_eret_truth] pred_source={pred_source_path}")
    print(f"[build_eret_truth] fill_truth={fill_truth_path}")
    print(f"[build_eret_truth] target_daily={target_daily_path}")
    print(f"[build_eret_truth] out={paths.out_csv}")
    print(f"[build_eret_truth] meta={paths.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
