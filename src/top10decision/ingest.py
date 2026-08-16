#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ingest.py

硬规则：
- 数据入口只允许一个：本模块
- runner 不直接读 URL / 不直接读其它旧文件
- 当前 prior 入口固定为：data/pred/pred_source_latest.csv
- FS 入口固定为：data/market/features_base_{trade_date}.csv|parquet
                 data/market/features_limit_{trade_date}.csv|parquet
                 data/market/truth_close_{trade_date}.csv|parquet
                 data/market/_meta_{trade_date}.json

注意：
- 字段兼容/映射只在 adapters 做
- 本模块负责“统一读取 + 统一 merge + 统一降级”
- 当前阶段允许 FS 缺失；缺失时返回空表，不应打断旧主线
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from top10decision.adapters.decisio_adapter import normalize_pred_fields


PRED_SNAPSHOT_PATH = Path("data/pred/pred_source_latest.csv")
MARKET_DIR = Path("data/market")

KEY_COLS = ["trade_date", "ts_code"]
PRED_TRACE_COLS = ["run_id", "commit_sha", "generated_at_utc"]


def _read_csv_any(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    return pd.DataFrame()


def _read_parquet_any(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def _read_table_any(path_no_suffix: Path) -> pd.DataFrame:
    """
    优先读取 parquet，其次 csv。
    例如传入 data/market/features_base_20260306
    会依次尝试：
    - data/market/features_base_20260306.parquet
    - data/market/features_base_20260306.csv
    """
    parquet_path = path_no_suffix.with_suffix(".parquet")
    csv_path = path_no_suffix.with_suffix(".csv")

    if parquet_path.exists():
        df = _read_parquet_any(parquet_path)
        if not df.empty:
            return df

    if csv_path.exists():
        df = _read_csv_any(csv_path)
        if not df.empty:
            return df

    return pd.DataFrame()


def _normalize_key_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)

    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].astype(str).str.strip()

    return out


def _coerce_trade_date_value(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"\.0$", "", s)
    if not s:
        return None
    return s


def _extract_trade_date_from_name(path: Path, prefix: str) -> str | None:
    """
    从文件名中抽取 trade_date：
    例如 features_base_20260306.csv -> 20260306
    """
    if path is None:
        return None
    name = path.name
    m = re.match(rf"^{re.escape(prefix)}_(\d{{8}})\.(csv|parquet|json)$", name)
    if not m:
        return None
    return m.group(1)


def _find_latest_trade_date_by_prefix(prefix: str) -> str | None:
    if not MARKET_DIR.exists():
        return None

    candidates: list[str] = []

    for p in MARKET_DIR.glob(f"{prefix}_*.csv"):
        dt = _extract_trade_date_from_name(p, prefix)
        if dt:
            candidates.append(dt)

    for p in MARKET_DIR.glob(f"{prefix}_*.parquet"):
        dt = _extract_trade_date_from_name(p, prefix)
        if dt:
            candidates.append(dt)

    if not candidates:
        return None

    return sorted(set(candidates))[-1]


def _guess_trade_date_from_pred(df_pred: pd.DataFrame) -> str | None:
    if df_pred is None or df_pred.empty or "trade_date" not in df_pred.columns:
        return None

    vals = (
        df_pred["trade_date"]
        .dropna()
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )

    vals = vals[vals != ""]
    if vals.empty:
        return None

    # 取众数优先，退化取最大值
    mode_vals = vals.mode()
    if mode_vals is not None and len(mode_vals) > 0:
        return str(mode_vals.iloc[0])

    return str(vals.max())


def _resolve_trade_date(trade_date: str | None = None) -> str | None:
    td = _coerce_trade_date_value(trade_date)
    if td:
        return td

    pred = load_pred_snapshot()
    td = _guess_trade_date_from_pred(pred)
    if td:
        return td

    for prefix in ("features_base", "features_limit", "truth_close"):
        td = _find_latest_trade_date_by_prefix(prefix)
        if td:
            return td

    return None


def _safe_json_load(path: Path) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}


def _rename_conflict_cols(
    df: pd.DataFrame,
    protected_cols: set[str],
    suffix: str,
) -> pd.DataFrame:
    """
    对冲突字段重命名，避免静默覆盖。
    KEY_COLS 不改名。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    renames: dict[str, str] = {}

    for col in out.columns:
        if col in KEY_COLS:
            continue
        if col in protected_cols:
            renames[col] = f"{col}{suffix}"

    if renames:
        out = out.rename(columns=renames)
    return out


def load_pred_snapshot() -> pd.DataFrame:
    """
    唯一 prior 入口：读取 data/pred/pred_source_latest.csv
    - 不读取 env url/path
    - 不 fallback 到旧 load_latest_pred
    """
    df = _read_csv_any(PRED_SNAPSHOT_PATH)
    if df is None or df.empty:
        return pd.DataFrame()

    df = normalize_pred_fields(df)
    df = _normalize_key_cols(df)
    return df


def load_market_features_base(trade_date: str | None = None) -> pd.DataFrame:
    td = _resolve_trade_date(trade_date)
    if not td:
        return pd.DataFrame()

    df = _read_table_any(MARKET_DIR / f"features_base_{td}")
    return _normalize_key_cols(df)


def load_market_features_limit(trade_date: str | None = None) -> pd.DataFrame:
    td = _resolve_trade_date(trade_date)
    if not td:
        return pd.DataFrame()

    df = _read_table_any(MARKET_DIR / f"features_limit_{td}")
    return _normalize_key_cols(df)


def load_truth_close(trade_date: str | None = None) -> pd.DataFrame:
    td = _resolve_trade_date(trade_date)
    if not td:
        return pd.DataFrame()

    df = _read_table_any(MARKET_DIR / f"truth_close_{td}")
    return _normalize_key_cols(df)


def load_market_meta(trade_date: str | None = None) -> dict[str, Any]:
    td = _resolve_trade_date(trade_date)
    if not td:
        return {}

    meta_path = MARKET_DIR / f"_meta_{td}.json"
    return _safe_json_load(meta_path)


def build_model_input(
    trade_date: str | None = None,
    include_limit: bool = True,
    include_truth: bool = False,
) -> pd.DataFrame:
    """
    统一构造训练/推理输入表。

    规则：
    - 主表始终以 pred_source_latest 为入口（prior）
    - features_base 左连接到 pred
    - include_limit=True 时再左连接 features_limit
    - include_truth=True 时再左连接 truth_close（训练/评估用）
    - 同名冲突字段必须重命名，禁止静默覆盖
    """
    pred = load_pred_snapshot()
    if pred.empty:
        return pd.DataFrame()

    td = _resolve_trade_date(trade_date)
    if td and "trade_date" in pred.columns:
        pred_td = pred["trade_date"].astype(str)
        pred_filtered = pred.loc[pred_td == td].copy()
        if not pred_filtered.empty:
            pred = pred_filtered

    pred = _normalize_key_cols(pred)

    out = pred.copy()

    # 先保护 prior 字段，后续 FS 同名字段必须改名
    protected_cols = set(out.columns)

    base = load_market_features_base(td)
    if not base.empty:
        base = _rename_conflict_cols(base, protected_cols=protected_cols, suffix="_fs")
        out = out.merge(base, on=KEY_COLS, how="left")
        protected_cols = set(out.columns)

    if include_limit:
        limit_df = load_market_features_limit(td)
        if not limit_df.empty:
            limit_df = _rename_conflict_cols(
                limit_df,
                protected_cols=protected_cols,
                suffix="_limit",
            )
            out = out.merge(limit_df, on=KEY_COLS, how="left")
            protected_cols = set(out.columns)

    if include_truth:
        truth = load_truth_close(td)
        if not truth.empty:
            truth = _rename_conflict_cols(
                truth,
                protected_cols=protected_cols,
                suffix="_truth",
            )
            out = out.merge(truth, on=KEY_COLS, how="left")

    return out


def get_input_status(trade_date: str | None = None) -> dict[str, Any]:
    """
    返回当前输入层状态，供 runner / report 审计使用。
    """
    td = _resolve_trade_date(trade_date)

    pred = load_pred_snapshot()
    base = load_market_features_base(td)
    limit_df = load_market_features_limit(td)
    truth = load_truth_close(td)
    meta = load_market_meta(td)

    return {
        "trade_date": td,
        "pred_loaded": not pred.empty,
        "pred_rows": int(len(pred)) if not pred.empty else 0,
        "features_base_loaded": not base.empty,
        "features_base_rows": int(len(base)) if not base.empty else 0,
        "features_limit_loaded": not limit_df.empty,
        "features_limit_rows": int(len(limit_df)) if not limit_df.empty else 0,
        "truth_close_loaded": not truth.empty,
        "truth_close_rows": int(len(truth)) if not truth.empty else 0,
        "meta_loaded": bool(meta),
        "meta": meta,
    }
