#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
build_market_fs.py

目标：
- 从 data/market/raw/ 的多源原始文件构建 Feature Store 四件套：
  1) data/market/features_base_{trade_date}.csv
  2) data/market/features_limit_{trade_date}.csv
  3) data/market/truth_close_{trade_date}.csv
  4) data/market/_meta_{trade_date}.json

当前输入主结构（新规范）：
- data/market/raw/{YYYY}/{YYYYMMDD}/daily.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/daily_basic.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/stock_basic.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/stk_limit.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/limit_list_d.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/limit_break_d.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/limit_up_tags.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/hot_boards.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/top_list.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/moneyflow_hsgt.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/namechange.csv
- data/market/raw/{YYYY}/{YYYYMMDD}/_sync_meta.json

兼容过渡：
- 仍兼容旧扁平结构 data/market/raw/{stem}_{trade_date}.csv
- 仍兼容旧扁平结构 data/market/raw/_sync_meta_{trade_date}.json

设计原则：
- FS 是标准化特征层，不是原始快照层
- 主键统一：trade_date + ts_code
- 优先保证结构正确、审计完整，再逐步提高丰富度
- 缺失字段要显式反映到 meta，不静默伪造
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


MARKET_DIR = Path("data/market")
RAW_DIR = MARKET_DIR / "raw"
KEY_COLS = ["trade_date", "ts_code"]

SOURCE_FILE_MAP: dict[str, str] = {
    "daily": "daily.csv",
    "daily_basic": "daily_basic.csv",
    "intraday_features": "intraday_features.csv",
    "stock_basic": "stock_basic.csv",
    "stk_auction": "stk_auction.csv",
    "stk_limit": "stk_limit.csv",
    "limit_list_d": "limit_list_d.csv",
    "limit_break_d": "limit_break_d.csv",
    "limit_up_tags": "limit_up_tags.csv",
    "hot_boards": "hot_boards.csv",
    "top_list": "top_list.csv",
    "moneyflow_hsgt": "moneyflow_hsgt.csv",
    "namechange": "namechange.csv",
}


# -----------------------------
# 通用工具
# -----------------------------
def _read_csv_any(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    return pd.DataFrame()


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


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _normalize_trade_date(v: Any, fallback: str | None = None) -> str | None:
    if v is None:
        return fallback
    s = str(v).strip()
    s = re.sub(r"\.0$", "", s)
    if not s:
        return fallback
    if re.fullmatch(r"\d{8}", s):
        return s
    return fallback


def _normalize_ts_code(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_numeric(v: Any) -> pd.Series:
    return pd.to_numeric(v, errors="coerce")


def _extract_trade_date_from_name(path: Path, stem_prefix: str) -> str | None:
    if path is None:
        return None
    m = re.match(rf"^{re.escape(stem_prefix)}_(\d{{8}})\.csv$", path.name)
    if not m:
        return None
    return m.group(1)


def _trade_year(trade_date: str) -> str:
    return trade_date[:4]


def _raw_dated_dir(trade_date: str) -> Path:
    return RAW_DIR / _trade_year(trade_date) / trade_date


def _raw_latest_dir() -> Path:
    return RAW_DIR / "latest"


def _legacy_raw_path(stem: str, trade_date: str) -> Path:
    return RAW_DIR / f"{stem}_{trade_date}.csv"


def _legacy_sync_meta_path(trade_date: str) -> Path:
    return RAW_DIR / f"_sync_meta_{trade_date}.json"


def _find_latest_raw_trade_date() -> str | None:
    if not RAW_DIR.exists():
        return None

    vals: list[str] = []

    # 新结构：data/market/raw/{YYYY}/{YYYYMMDD}/daily.csv
    for year_dir in RAW_DIR.iterdir():
        if not year_dir.is_dir() or year_dir.name == "latest":
            continue
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir():
                continue
            if re.fullmatch(r"\d{8}", day_dir.name) and (day_dir / "daily.csv").exists():
                vals.append(day_dir.name)

    # 旧结构兼容：data/market/raw/daily_{trade_date}.csv
    for p in RAW_DIR.glob("daily_*.csv"):
        td = _extract_trade_date_from_name(p, "daily")
        if td:
            vals.append(td)

    if not vals:
        return None
    return sorted(set(vals))[-1]


def _resolve_trade_date(trade_date: str | None = None) -> str | None:
    td = _normalize_trade_date(trade_date)
    if td:
        return td
    return _find_latest_raw_trade_date()


def _raw_path(stem: str, trade_date: str) -> Path:
    filename = SOURCE_FILE_MAP.get(stem, f"{stem}.csv")
    new_path = _raw_dated_dir(trade_date) / filename
    if new_path.exists():
        return new_path

    legacy_path = _legacy_raw_path(stem, trade_date)
    if legacy_path.exists():
        return legacy_path

    # 默认返回新路径，便于 meta 记录新规范
    return new_path


def _sync_meta_path(trade_date: str) -> Path:
    new_path = _raw_dated_dir(trade_date) / "_sync_meta.json"
    if new_path.exists():
        return new_path

    legacy_path = _legacy_sync_meta_path(trade_date)
    if legacy_path.exists():
        return legacy_path

    return new_path


def _load_raw_table(stem: str, trade_date: str) -> pd.DataFrame:
    return _read_csv_any(_raw_path(stem, trade_date))


HISTORY_FEATURE_COLS = [
    "volatility_5d",
    "volatility_10d",
    "volatility_20d",
    "atr",
    "downside_vol",
    "max_drawdown_20d",
    "tail_risk_score",
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "bid_ask_proxy",
    "spread_proxy",
]


def _pct_to_decimal_series(s: Any) -> pd.Series:
    """
    Tushare daily.pct_chg is normally stored in percentage points.
    Example: 10.0 means +10%, not +1000%.
    Keep the original pct_chg column unchanged, but use decimal returns for
    pre_close and rolling feature calculations.
    """
    return pd.to_numeric(s, errors="coerce") / 100.0


def _list_available_raw_trade_dates_until(trade_date: str, max_dates: int = 45) -> list[str]:
    if not trade_date or not RAW_DIR.exists():
        return []

    vals: set[str] = set()

    # 新结构：data/market/raw/{YYYY}/{YYYYMMDD}/daily.csv
    for year_dir in RAW_DIR.iterdir():
        if not year_dir.is_dir() or year_dir.name == "latest":
            continue
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir():
                continue
            td = _normalize_trade_date(day_dir.name)
            if td and td <= trade_date and (day_dir / "daily.csv").exists():
                vals.add(td)

    # 旧结构兼容：data/market/raw/daily_{trade_date}.csv
    for p in RAW_DIR.glob("daily_*.csv"):
        td = _extract_trade_date_from_name(p, "daily")
        if td and td <= trade_date and p.exists():
            vals.add(td)

    dates = sorted(vals)
    if max_dates and max_dates > 0:
        dates = dates[-max_dates:]
    return dates


def _load_history_daily(trade_date: str, max_dates: int = 45) -> pd.DataFrame:
    """
    读取 anchor trade_date 之前的历史 daily.csv，用于计算滚动收益和波动特征。
    只依赖 raw daily.csv，不影响现有多源合并链路。
    """
    dates = _list_available_raw_trade_dates_until(trade_date, max_dates=max_dates)
    frames: list[pd.DataFrame] = []

    for td in dates:
        raw = _load_raw_table("daily", td)
        if raw is None or raw.empty:
            continue
        try:
            std = _std_daily(raw, td)
        except Exception:
            continue
        if std is not None and not std.empty:
            frames.append(std)

    if not frames:
        return pd.DataFrame(columns=KEY_COLS)

    hist = pd.concat(frames, ignore_index=True)
    hist["trade_date"] = hist["trade_date"].apply(lambda x: _normalize_trade_date(x, fallback=""))
    hist["ts_code"] = hist["ts_code"].apply(_normalize_ts_code)
    hist = hist.dropna(subset=["trade_date", "ts_code"]).copy()
    hist = hist.drop_duplicates(subset=KEY_COLS, keep="last").reset_index(drop=True)
    return hist


def _rolling_max_drawdown(close: pd.Series) -> float:
    s = pd.to_numeric(close, errors="coerce").dropna()
    if len(s) < 2:
        return float("nan")
    running_max = s.cummax()
    dd = s / running_max - 1.0
    return float(dd.min())


def _rolling_downside_vol(ret: pd.Series) -> float:
    s = pd.to_numeric(ret, errors="coerce")
    s = s[s < 0]
    if len(s) < 2:
        return 0.0 if len(ret.dropna()) >= 2 else float("nan")
    return float(s.std(ddof=0))


def _compute_history_features(trade_date: str) -> pd.DataFrame:
    """
    计算当前 trade_date 可用的真实历史特征。
    输出主键：trade_date + ts_code。
    """
    cols = KEY_COLS + HISTORY_FEATURE_COLS
    hist = _load_history_daily(trade_date, max_dates=45)
    if hist is None or hist.empty:
        return pd.DataFrame(columns=cols)

    x = hist.copy()
    for c in ["open", "high", "low", "close", "vol", "amount", "pct_chg", "pre_close_est"]:
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
        else:
            x[c] = pd.NA

    x = x.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = x.groupby("ts_code", group_keys=False)

    prev_close = g["close"].shift(1)
    x["daily_ret_calc"] = g["close"].pct_change()

    # 多周期累计收益，统一使用 decimal return。
    for n in (2, 5, 10):
        x[f"ret_{n}d"] = g["close"].transform(lambda s, n=n: s / s.shift(n) - 1.0)

    # 波动率，统一使用 decimal daily return。
    for n in (5, 10, 20):
        min_periods = max(2, min(n, n // 2))
        x[f"volatility_{n}d"] = g["daily_ret_calc"].transform(
            lambda s, n=n, min_periods=min_periods: s.rolling(n, min_periods=min_periods).std(ddof=0)
        )

    tr_abs = pd.concat(
        [
            (x["high"] - x["low"]).abs(),
            (x["high"] - prev_close).abs(),
            (x["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    base_close = prev_close.where(prev_close > 0, x["close"])
    x["true_range_pct"] = tr_abs / base_close.replace(0, pd.NA)
    x["atr"] = g["true_range_pct"].transform(lambda s: s.rolling(14, min_periods=5).mean())

    x["downside_vol"] = g["daily_ret_calc"].transform(
        lambda s: s.rolling(20, min_periods=5).apply(_rolling_downside_vol, raw=False)
    )
    x["max_drawdown_20d"] = g["close"].transform(
        lambda s: s.rolling(20, min_periods=5).apply(_rolling_max_drawdown, raw=False)
    )
    x["tail_risk_score"] = (
        x["max_drawdown_20d"].abs().fillna(0.0)
        + x["downside_vol"].fillna(0.0)
        + x["volatility_20d"].fillna(0.0)
    )

    # 没有真实盘口数据时，使用日内高低区间作为可解释代理。
    x["bid_ask_proxy"] = (x["high"] - x["low"]) / x["close"].replace(0, pd.NA)
    x["spread_proxy"] = (x["high"] - x["low"]) / x["pre_close_est"].replace(0, pd.NA)

    out = x.loc[x["trade_date"] == trade_date, cols].copy()
    out = out.drop_duplicates(subset=KEY_COLS, keep="last").reset_index(drop=True)
    return out


def _ensure_keys(df: pd.DataFrame, trade_date_fallback: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].apply(
            lambda x: _normalize_trade_date(x, fallback=trade_date_fallback)
        )
    else:
        out["trade_date"] = trade_date_fallback

    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].apply(_normalize_ts_code)
    elif "code" in out.columns:
        out["ts_code"] = out["code"].apply(_normalize_ts_code)
    else:
        out["ts_code"] = None

    out = out.dropna(subset=["trade_date", "ts_code"]).copy()
    out = out.drop_duplicates(subset=KEY_COLS, keep="last").reset_index(drop=True)
    return out


def _merge_left(base: pd.DataFrame, extra: pd.DataFrame, on: list[str]) -> pd.DataFrame:
    if base is None or base.empty:
        return extra.copy() if extra is not None else pd.DataFrame()
    if extra is None or extra.empty:
        return base.copy()
    return base.merge(extra, on=on, how="left")


# -----------------------------
# 各 raw 表标准化
# -----------------------------
def _std_daily(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, trade_date, open, high, low, close, vol, amount, pct_chg

    注意：
    - pct_chg 原样保留为上游字段；Tushare 常见口径是百分比点，
      例如 10.0 表示 +10%。
    - pre_close_est 使用 pct_chg / 100 反推，避免把 +10% 误当成 +1000%。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)

    std = out[KEY_COLS].copy()
    std["open"] = _to_numeric(out["open"]) if "open" in out.columns else None
    std["high"] = _to_numeric(out["high"]) if "high" in out.columns else None
    std["low"] = _to_numeric(out["low"]) if "low" in out.columns else None
    std["close"] = _to_numeric(out["close"]) if "close" in out.columns else None
    std["vol"] = _to_numeric(out["vol"]) if "vol" in out.columns else None
    std["amount"] = _to_numeric(out["amount"]) if "amount" in out.columns else None
    std["pct_chg"] = _to_numeric(out["pct_chg"]) if "pct_chg" in out.columns else None

    close = std["close"]
    pct_decimal = _pct_to_decimal_series(std["pct_chg"])
    std["pre_close_est"] = close / (1.0 + pct_decimal)
    return std


def _std_daily_basic(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, trade_date, turnover_rate, turnover_rate_f, volume_ratio, total_mv, float_mv
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)
    std = out[KEY_COLS].copy()

    std["turnover_rate"] = _to_numeric(out["turnover_rate"]) if "turnover_rate" in out.columns else None
    std["turnover_rate_f"] = _to_numeric(out["turnover_rate_f"]) if "turnover_rate_f" in out.columns else None
    std["volume_ratio"] = _to_numeric(out["volume_ratio"]) if "volume_ratio" in out.columns else None
    std["total_mv"] = _to_numeric(out["total_mv"]) if "total_mv" in out.columns else None
    std["float_mv"] = _to_numeric(out["float_mv"]) if "float_mv" in out.columns else None

    return std


def _std_stock_basic(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, symbol, name, area, industry, market, list_date
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = df.copy()
    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].apply(_normalize_ts_code)
    elif "code" in out.columns:
        out["ts_code"] = out["code"].apply(_normalize_ts_code)
    else:
        out["ts_code"] = None

    out["trade_date"] = trade_date
    out = out.dropna(subset=["ts_code"]).copy()
    out = out.drop_duplicates(subset=["ts_code"], keep="last").reset_index(drop=True)

    std = out[KEY_COLS].copy()
    std["symbol"] = out["symbol"] if "symbol" in out.columns else None
    std["name"] = out["name"] if "name" in out.columns else None
    std["area"] = out["area"] if "area" in out.columns else None
    std["industry"] = out["industry"] if "industry" in out.columns else None
    std["market"] = out["market"] if "market" in out.columns else None
    std["list_date"] = out["list_date"] if "list_date" in out.columns else None
    return std


def _std_stk_limit(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, trade_date, up_limit, down_limit
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)
    std = out[KEY_COLS].copy()
    std["up_limit"] = _to_numeric(out["up_limit"]) if "up_limit" in out.columns else None
    std["down_limit"] = _to_numeric(out["down_limit"]) if "down_limit" in out.columns else None
    return std


def _std_limit_list(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    trade_date, ts_code, name, limit_type, close, up_limit, down_limit,
    open_times, fd_amount, first_time, last_time, seal_amount
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)

    std = out[KEY_COLS].copy()
    std["name"] = out["name"] if "name" in out.columns else None
    std["limit_type"] = out["limit_type"] if "limit_type" in out.columns else None
    std["close_limit"] = _to_numeric(out["close"]) if "close" in out.columns else None
    std["up_limit_list"] = _to_numeric(out["up_limit"]) if "up_limit" in out.columns else None
    std["down_limit_list"] = _to_numeric(out["down_limit"]) if "down_limit" in out.columns else None
    std["open_times_limit"] = _to_numeric(out["open_times"]) if "open_times" in out.columns else None
    std["fd_amount_limit"] = _to_numeric(out["fd_amount"]) if "fd_amount" in out.columns else None
    std["first_time_limit"] = out["first_time"] if "first_time" in out.columns else None
    std["last_time_limit"] = out["last_time"] if "last_time" in out.columns else None
    std["seal_amount_limit"] = _to_numeric(out["seal_amount"]) if "seal_amount" in out.columns else None
    return std


def _std_limit_break(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    trade_date, ts_code, name, open_times, first_time, last_time, fd_amount, seal_amount
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)

    std = out[KEY_COLS].copy()
    std["name_break"] = out["name"] if "name" in out.columns else None
    std["open_times_break"] = _to_numeric(out["open_times"]) if "open_times" in out.columns else None
    std["first_time_break"] = out["first_time"] if "first_time" in out.columns else None
    std["last_time_break"] = out["last_time"] if "last_time" in out.columns else None
    std["fd_amount_break"] = _to_numeric(out["fd_amount"]) if "fd_amount" in out.columns else None
    std["seal_amount_break"] = _to_numeric(out["seal_amount"]) if "seal_amount" in out.columns else None
    return std


def _std_limit_up_tags(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    trade_date, ts_code, name, industry, is_hot_board, board_rank,
    board_limit_up_count, is_st_like
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)

    std = out[KEY_COLS].copy()
    std["name_tag"] = out["name"] if "name" in out.columns else None
    std["industry_tag"] = out["industry"] if "industry" in out.columns else None
    std["is_hot_board"] = _to_numeric(out["is_hot_board"]) if "is_hot_board" in out.columns else None
    std["board_rank"] = _to_numeric(out["board_rank"]) if "board_rank" in out.columns else None
    std["board_limit_up_count"] = _to_numeric(out["board_limit_up_count"]) if "board_limit_up_count" in out.columns else None
    std["is_st_like"] = _to_numeric(out["is_st_like"]) if "is_st_like" in out.columns else None
    return std


def _std_hot_boards(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    trade_date, industry, limit_up_count, rank
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["board", "hot_boards_score", "board_crowding_rank"])

    out = df.copy()
    std = pd.DataFrame()
    std["board"] = out["industry"].astype(str).str.strip() if "industry" in out.columns else None
    std["hot_boards_score"] = _to_numeric(out["limit_up_count"]) if "limit_up_count" in out.columns else None
    std["board_crowding_rank"] = _to_numeric(out["rank"]) if "rank" in out.columns else None

    std = std.dropna(subset=["board"]).copy()
    std = std.drop_duplicates(subset=["board"], keep="last").reset_index(drop=True)
    return std


def _std_intraday_features(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    标准化分钟级聚合特征。

    上游 raw 明细仍保留在 a-share-top3-data；Decision 只同步聚合后的
    intraday_features.csv，避免把大量 1min 明细塞进决策仓库。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df, trade_date)
    if out.empty:
        return pd.DataFrame(columns=KEY_COLS)

    std = out[KEY_COLS].copy()
    has_minute = _to_numeric(out.get("has_minute_data")).fillna(0.0)
    std["intraday_available"] = has_minute
    std["intraday_status"] = has_minute.map(lambda x: "ok" if float(x or 0.0) > 0 else "missing_minute")
    std["intraday_missing_reason"] = std["intraday_status"].map(lambda x: "" if x == "ok" else "no_minute_data")
    std["minute_freq"] = out.get("minute_freq")
    std["minute_rows"] = _to_numeric(out.get("minute_rows"))
    std["first_limit_time"] = out.get("first_limit_time")
    std["last_limit_time"] = out.get("last_limit_time")
    std["limit_touch_count"] = _to_numeric(out.get("limit_touch_count"))
    std["open_board_count"] = _to_numeric(out.get("open_board_count"))
    std["max_drawdown_after_limit"] = _to_numeric(out.get("max_drawdown_after_limit"))
    std["reseal_count"] = _to_numeric(out.get("reseal_count"))
    std["reseal_minutes_avg"] = _to_numeric(out.get("reseal_minutes_avg"))
    std["late_volume_ratio"] = _to_numeric(out.get("late_volume_ratio"))
    std["late_price_weakness"] = _to_numeric(out.get("late_price_weakness"))
    std["late_limit_hold_minutes"] = _to_numeric(out.get("late_limit_hold_minutes"))
    std["late_withdraw_score"] = _to_numeric(out.get("late_withdraw_score"))
    std["reseal_score"] = _to_numeric(out.get("reseal_acceptance_score"))
    std["intraday_quality_score"] = _to_numeric(out.get("limitup_quality_score"))
    std["intraday_confidence_score"] = _to_numeric(out.get("limitup_path_score"))
    std["intraday_risk_score"] = _to_numeric(out.get("intraday_risk_score"))
    std["intraday_soft_risk_score"] = std["intraday_risk_score"]
    std["intraday_tag"] = out.get("intraday_tag")
    risk_score = std["intraday_risk_score"].fillna(0.0)
    std["intraday_hard_risk_flag"] = (
        (risk_score >= 65.0)
        | std["intraday_tag"].astype(str).str.contains("risk|weak|bad", case=False, na=False)
    ).astype(float)
    return std


def _std_stk_auction(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df, trade_date)
    if out.empty:
        return pd.DataFrame(columns=KEY_COLS)

    std = out[KEY_COLS].copy()
    std["auction_vol"] = _to_numeric(out.get("vol"))
    std["auction_price"] = _to_numeric(out.get("price"))
    std["auction_amount"] = _to_numeric(out.get("amount"))
    amt = std["auction_amount"].replace([float("inf"), -float("inf")], pd.NA)
    if amt.notna().sum() > 1:
        std["auction_strength_score"] = amt.rank(pct=True).fillna(0.0)
    else:
        std["auction_strength_score"] = 0.0
    return std


def _std_top_list(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, trade_date, name, close, pct_change, turnover_rate, amount,
    l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = _ensure_keys(df.copy(), trade_date)

    std = out[KEY_COLS].copy()
    std["name_top"] = out["name"] if "name" in out.columns else None
    std["top_close"] = _to_numeric(out["close"]) if "close" in out.columns else None
    std["top_pct_change"] = _to_numeric(out["pct_change"]) if "pct_change" in out.columns else None
    std["top_turnover_rate"] = _to_numeric(out["turnover_rate"]) if "turnover_rate" in out.columns else None
    std["top_amount"] = _to_numeric(out["amount"]) if "amount" in out.columns else None
    std["l_sell"] = _to_numeric(out["l_sell"]) if "l_sell" in out.columns else None
    std["l_buy"] = _to_numeric(out["l_buy"]) if "l_buy" in out.columns else None
    std["l_amount"] = _to_numeric(out["l_amount"]) if "l_amount" in out.columns else None
    std["top_list_net_buy"] = _to_numeric(out["net_amount"]) if "net_amount" in out.columns else None
    std["top_net_rate"] = _to_numeric(out["net_rate"]) if "net_rate" in out.columns else None
    std["amount_rate"] = _to_numeric(out["amount_rate"]) if "amount_rate" in out.columns else None
    std["float_values"] = _to_numeric(out["float_values"]) if "float_values" in out.columns else None
    std["top_reason"] = out["reason"] if "reason" in out.columns else None
    return std


def _std_moneyflow_hsgt_market(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, trade_date, ggt_ss, ggt_sz, hgt, sgt, north_money, south_money

    当前按市场级数据处理：
    trade_date -> north_money_market / south_money_market / hgt_market / sgt_market
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["trade_date", "north_money_market", "south_money_market", "hgt_market", "sgt_market"]
        )

    out = df.copy()
    if "trade_date" not in out.columns:
        return pd.DataFrame(
            columns=["trade_date", "north_money_market", "south_money_market", "hgt_market", "sgt_market"]
        )

    out["trade_date"] = out["trade_date"].apply(lambda x: _normalize_trade_date(x, fallback=trade_date))
    out = out.dropna(subset=["trade_date"]).copy()

    std = pd.DataFrame()
    std["trade_date"] = out["trade_date"]
    std["north_money_market"] = _to_numeric(out["north_money"]) if "north_money" in out.columns else None
    std["south_money_market"] = _to_numeric(out["south_money"]) if "south_money" in out.columns else None
    std["hgt_market"] = _to_numeric(out["hgt"]) if "hgt" in out.columns else None
    std["sgt_market"] = _to_numeric(out["sgt"]) if "sgt" in out.columns else None

    std = std.drop_duplicates(subset=["trade_date"], keep="last").reset_index(drop=True)
    return std


def _std_namechange(df: pd.DataFrame) -> pd.DataFrame:
    """
    已确认表头：
    ts_code, name, start_date, end_date, change_reason
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts_code", "name_change_name", "start_date", "end_date", "change_reason"])

    out = df.copy()
    if "ts_code" not in out.columns:
        return pd.DataFrame(columns=["ts_code", "name_change_name", "start_date", "end_date", "change_reason"])

    out["ts_code"] = out["ts_code"].apply(_normalize_ts_code)
    out["start_date"] = out["start_date"].apply(_normalize_trade_date) if "start_date" in out.columns else None
    out["end_date"] = out["end_date"].apply(_normalize_trade_date) if "end_date" in out.columns else None

    out = out.dropna(subset=["ts_code"]).copy()

    # 取每个 ts_code 最新一条记录
    sort_cols = []
    if "start_date" in out.columns:
        sort_cols.append("start_date")
    if sort_cols:
        out = out.sort_values(sort_cols).drop_duplicates(subset=["ts_code"], keep="last")
    else:
        out = out.drop_duplicates(subset=["ts_code"], keep="last")

    std = pd.DataFrame()
    std["ts_code"] = out["ts_code"]
    std["name_change_name"] = out["name"] if "name" in out.columns else None
    std["name_change_start_date"] = out["start_date"] if "start_date" in out.columns else None
    std["name_change_end_date"] = out["end_date"] if "end_date" in out.columns else None
    std["latest_change_reason"] = out["change_reason"] if "change_reason" in out.columns else None
    return std.reset_index(drop=True)


# -----------------------------
# raw 装载
# -----------------------------
def load_raw_bundle(trade_date: str) -> dict[str, pd.DataFrame]:
    return {
        "daily": _std_daily(_load_raw_table("daily", trade_date), trade_date),
        "daily_basic": _std_daily_basic(_load_raw_table("daily_basic", trade_date), trade_date),
        "intraday_features": _std_intraday_features(_load_raw_table("intraday_features", trade_date), trade_date),
        "stock_basic": _std_stock_basic(_load_raw_table("stock_basic", trade_date), trade_date),
        "stk_auction": _std_stk_auction(_load_raw_table("stk_auction", trade_date), trade_date),
        "stk_limit": _std_stk_limit(_load_raw_table("stk_limit", trade_date), trade_date),
        "limit_list_d": _std_limit_list(_load_raw_table("limit_list_d", trade_date), trade_date),
        "limit_break_d": _std_limit_break(_load_raw_table("limit_break_d", trade_date), trade_date),
        "limit_up_tags": _std_limit_up_tags(_load_raw_table("limit_up_tags", trade_date), trade_date),
        "hot_boards": _std_hot_boards(_load_raw_table("hot_boards", trade_date), trade_date),
        "top_list": _std_top_list(_load_raw_table("top_list", trade_date), trade_date),
        "moneyflow_hsgt_market": _std_moneyflow_hsgt_market(
            _load_raw_table("moneyflow_hsgt", trade_date), trade_date
        ),
        "namechange": _std_namechange(_load_raw_table("namechange", trade_date)),
    }


def build_master_table(bundle: dict[str, pd.DataFrame], trade_date: str) -> pd.DataFrame:
    daily = bundle.get("daily", pd.DataFrame())
    daily_basic = bundle.get("daily_basic", pd.DataFrame())
    intraday_features = bundle.get("intraday_features", pd.DataFrame())
    stock_basic = bundle.get("stock_basic", pd.DataFrame())
    stk_auction = bundle.get("stk_auction", pd.DataFrame())
    stk_limit = bundle.get("stk_limit", pd.DataFrame())
    limit_list_d = bundle.get("limit_list_d", pd.DataFrame())
    limit_break_d = bundle.get("limit_break_d", pd.DataFrame())
    limit_up_tags = bundle.get("limit_up_tags", pd.DataFrame())
    top_list = bundle.get("top_list", pd.DataFrame())
    namechange = bundle.get("namechange", pd.DataFrame())

    if daily is None or daily.empty:
        return pd.DataFrame(columns=KEY_COLS)

    master = daily.copy()
    for tbl in [
        daily_basic,
        stock_basic,
        stk_limit,
        limit_list_d,
        limit_break_d,
        limit_up_tags,
        top_list,
        intraday_features,
        stk_auction,
    ]:
        master = _merge_left(master, tbl, on=KEY_COLS)

    # namechange 是静态 ts_code 级辅助表
    if namechange is not None and not namechange.empty:
        master = master.merge(namechange, on="ts_code", how="left")

    # 优先使用个股级标签表的行业，再回退到 stock_basic 行业
    if "industry_tag" in master.columns:
        master["board"] = master["industry_tag"]
    elif "industry" in master.columns:
        master["board"] = master["industry"]
    else:
        master["board"] = None

    # 板块热度映射
    hot_boards = bundle.get("hot_boards", pd.DataFrame())
    if hot_boards is not None and not hot_boards.empty and "board" in master.columns:
        master["board"] = master["board"].astype(str).str.strip()
        master = master.merge(hot_boards, on="board", how="left")

    # 市场级资金流按 trade_date 广播
    money_market = bundle.get("moneyflow_hsgt_market", pd.DataFrame())
    if money_market is not None and not money_market.empty:
        master = master.merge(money_market, on="trade_date", how="left")

    master["trade_date"] = master["trade_date"].apply(lambda x: _normalize_trade_date(x, fallback=trade_date))
    master["ts_code"] = master["ts_code"].apply(_normalize_ts_code)
    master = master.dropna(subset=["trade_date", "ts_code"]).copy()
    master = master.drop_duplicates(subset=KEY_COLS, keep="last").reset_index(drop=True)

    # namechange 风险标记
    if "name_change_start_date" in master.columns:
        master["has_namechange_record"] = master["name_change_start_date"].notna().astype(float)
    else:
        master["has_namechange_record"] = 0.0

    return master


# -----------------------------
# Feature 构造
# -----------------------------
def build_features_base(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = df[KEY_COLS].copy()
    out["name"] = df.get("name")
    out["symbol"] = df.get("symbol")
    out["board"] = df.get("board")
    out["area"] = df.get("area")
    out["market"] = df.get("market")
    out["list_date"] = df.get("list_date")

    # 基础行情
    out["open"] = _to_numeric(df.get("open"))
    out["high"] = _to_numeric(df.get("high"))
    out["low"] = _to_numeric(df.get("low"))
    out["close"] = _to_numeric(df.get("close"))
    out["pct_chg"] = _to_numeric(df.get("pct_chg"))
    out["pre_close_est"] = _to_numeric(df.get("pre_close_est"))

    # 收益与价格行为
    out["returns_1d"] = out["pct_chg"]
    pre = out["pre_close_est"]
    out["high_low_range"] = (out["high"] - out["low"]) / pre
    out["candle_body"] = (out["close"] - out["open"]) / pre
    out["gap_open"] = (out["open"] - pre) / pre

    # 流动性
    out["vol"] = _to_numeric(df.get("vol"))
    out["amount"] = _to_numeric(df.get("amount"))
    out["turnover_rate"] = _to_numeric(df.get("turnover_rate"))
    out["turnover_rate_f"] = _to_numeric(df.get("turnover_rate_f"))
    out["volume_ratio"] = _to_numeric(df.get("volume_ratio"))
    out["amihud_illiquidity"] = out["pct_chg"].abs() / out["amount"]

    # 历史收益 / 波动 / 尾部风险特征
    trade_date_for_history = ""
    if "trade_date" in out.columns and not out["trade_date"].dropna().empty:
        trade_date_for_history = _normalize_trade_date(out["trade_date"].dropna().iloc[0], fallback="") or ""

    history_features = _compute_history_features(trade_date_for_history) if trade_date_for_history else pd.DataFrame()
    if history_features is not None and not history_features.empty:
        out = out.merge(history_features, on=KEY_COLS, how="left")

    for c in HISTORY_FEATURE_COLS:
        if c not in out.columns:
            out[c] = pd.NA

    # 估值市值
    out["total_mv"] = _to_numeric(df.get("total_mv"))
    out["float_mv"] = _to_numeric(df.get("float_mv"))
    out["pe_ttm"] = _to_numeric(df.get("pe_ttm"))
    out["pb"] = _to_numeric(df.get("pb"))

    # 资金流/情绪
    out["north_money_market"] = _to_numeric(df.get("north_money_market"))
    out["south_money_market"] = _to_numeric(df.get("south_money_market"))
    out["hgt_market"] = _to_numeric(df.get("hgt_market"))
    out["sgt_market"] = _to_numeric(df.get("sgt_market"))
    out["market_regime"] = None

    # 板块热度
    out["hot_boards_score"] = _to_numeric(df.get("hot_boards_score"))
    out["board_crowding_rank"] = _to_numeric(df.get("board_crowding_rank"))

    # 个股热板属性（来自 limit_up_tags）
    out["is_hot_board"] = _to_numeric(df.get("is_hot_board"))
    out["board_rank"] = _to_numeric(df.get("board_rank"))
    out["board_limit_up_count"] = _to_numeric(df.get("board_limit_up_count"))
    out["is_st_like"] = _to_numeric(df.get("is_st_like"))

    # 龙虎榜/异动
    out["top_list_net_buy"] = _to_numeric(df.get("top_list_net_buy"))
    out["top_net_rate"] = _to_numeric(df.get("top_net_rate"))
    out["amount_rate"] = _to_numeric(df.get("amount_rate"))
    out["float_values"] = _to_numeric(df.get("float_values"))
    out["abnormal_volume"] = _to_numeric(df.get("amount_rate"))

    # 名称变更辅助
    out["has_namechange_record"] = _to_numeric(df.get("has_namechange_record"))
    out["latest_change_reason"] = df.get("latest_change_reason")

    # 竞价聚合特征（分钟/竞价红利的基础证据）
    out["auction_vol"] = _to_numeric(df.get("auction_vol"))
    out["auction_price"] = _to_numeric(df.get("auction_price"))
    out["auction_amount"] = _to_numeric(df.get("auction_amount"))
    out["auction_strength_score"] = _to_numeric(df.get("auction_strength_score"))

    return out


def build_features_limit(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = df[KEY_COLS].copy()
    out["name"] = df.get("name")

    # limit_list_d 为主
    out["limit_type"] = df.get("limit_type")
    out["open_times"] = _to_numeric(df.get("open_times_limit"))
    out["fd_amount"] = _to_numeric(df.get("fd_amount_limit"))
    out["seal_amount"] = _to_numeric(df.get("seal_amount_limit"))
    out["first_seal_time"] = df.get("first_time_limit")
    out["last_seal_time"] = df.get("last_time_limit")

    # 分钟级聚合特征。若 a-top10 pred 已经带同名字段，ingest 会保护 prior 字段；
    # 若 pred 未带，则这些 FS 字段直接进入 Decision 排序和风险层。
    out["intraday_available"] = _to_numeric(df.get("intraday_available"))
    out["intraday_status"] = df.get("intraday_status")
    out["intraday_missing_reason"] = df.get("intraday_missing_reason")
    out["minute_freq"] = df.get("minute_freq")
    out["minute_rows"] = _to_numeric(df.get("minute_rows"))
    out["first_limit_time"] = df.get("first_limit_time")
    out["last_limit_time"] = df.get("last_limit_time")
    out["limit_touch_count"] = _to_numeric(df.get("limit_touch_count"))
    out["open_board_count"] = _to_numeric(df.get("open_board_count"))
    out["max_drawdown_after_limit"] = _to_numeric(df.get("max_drawdown_after_limit"))
    out["reseal_count"] = _to_numeric(df.get("reseal_count"))
    out["reseal_minutes_avg"] = _to_numeric(df.get("reseal_minutes_avg"))
    out["late_volume_ratio"] = _to_numeric(df.get("late_volume_ratio"))
    out["late_price_weakness"] = _to_numeric(df.get("late_price_weakness"))
    out["late_limit_hold_minutes"] = _to_numeric(df.get("late_limit_hold_minutes"))
    out["late_withdraw_score"] = _to_numeric(df.get("late_withdraw_score"))
    out["reseal_score"] = _to_numeric(df.get("reseal_score"))
    out["intraday_quality_score"] = _to_numeric(df.get("intraday_quality_score"))
    out["intraday_confidence_score"] = _to_numeric(df.get("intraday_confidence_score"))
    out["intraday_risk_score"] = _to_numeric(df.get("intraday_risk_score"))
    out["intraday_soft_risk_score"] = _to_numeric(df.get("intraday_soft_risk_score"))
    out["intraday_hard_risk_flag"] = _to_numeric(df.get("intraday_hard_risk_flag"))
    out["intraday_tag"] = df.get("intraday_tag")
    out["auction_strength_score"] = _to_numeric(df.get("auction_strength_score"))
    out["auction_amount"] = _to_numeric(df.get("auction_amount"))

    # limit_break_d 补充
    out["open_times_break"] = _to_numeric(df.get("open_times_break"))
    out["fd_amount_break"] = _to_numeric(df.get("fd_amount_break"))
    out["seal_amount_break"] = _to_numeric(df.get("seal_amount_break"))
    out["first_time_break"] = df.get("first_time_break")
    out["last_time_break"] = df.get("last_time_break")

    # 涨跌停
    out["up_limit"] = _to_numeric(df.get("up_limit"))
    out["down_limit"] = _to_numeric(df.get("down_limit"))

    # 来自 limit_up_tags 的热板属性
    out["is_hot_board"] = _to_numeric(df.get("is_hot_board"))
    out["board_rank"] = _to_numeric(df.get("board_rank"))
    out["board_limit_up_count"] = _to_numeric(df.get("board_limit_up_count"))
    out["is_st_like"] = _to_numeric(df.get("is_st_like"))

    close = _to_numeric(df.get("close"))
    up_limit = _to_numeric(df.get("up_limit"))
    out["is_limit_up"] = (close >= up_limit).astype("float")

    # 保守代理：有 break 记录视作 break>=1
    # 注意：这里必须从 out 中取列，而不是再次对 df.get(...) 直接 fillna。
    # 当 df 中该列不存在时，df.get(...) 可能返回标量 NaN，进而触发
    # AttributeError: "numpy.float64" object has no attribute "fillna"。
    break_exists = out["open_times_break"].fillna(0.0)
    out["break_count_proxy"] = (break_exists > 0).astype(float)

    score = pd.Series(0.0, index=df.index)

    ot = out["open_times"].fillna(0.0)
    score += 1.0 / (1.0 + ot)

    sa = out["seal_amount"].fillna(0.0)
    max_sa = sa.max()
    if pd.notna(max_sa) and max_sa > 0:
        score += sa / max_sa

    bc = out["break_count_proxy"].fillna(0.0)
    score += 1.0 / (1.0 + bc)

    # 热板加分
    ihb = out["is_hot_board"].fillna(0.0)
    score += 0.25 * ihb

    out["limit_up_strength"] = score.replace([float("inf"), -float("inf")], pd.NA)

    return out


def build_truth_close(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=KEY_COLS)

    out = df[KEY_COLS].copy()
    out["name"] = df.get("name")
    out["close"] = _to_numeric(df.get("close"))
    out["pre_close_est"] = _to_numeric(df.get("pre_close_est"))
    out["pct_chg"] = _to_numeric(df.get("pct_chg"))
    out["up_limit"] = _to_numeric(df.get("up_limit"))
    out["down_limit"] = _to_numeric(df.get("down_limit"))
    return out


# -----------------------------
# Meta
# -----------------------------
def _coverage_stats(df: pd.DataFrame, required_cols: list[str]) -> dict[str, Any]:
    if df is None or df.empty:
        return {
            "rows": 0,
            "columns": [],
            "coverage_ratio": 0.0,
            "missing_ratio": 1.0,
            "non_null_rate_by_col": {},
        }

    non_null_rate_by_col = {}
    covered = 0
    for c in required_cols:
        if c in df.columns:
            rate = float(df[c].notna().mean())
        else:
            rate = 0.0
        non_null_rate_by_col[c] = round(rate, 6)
        if rate > 0:
            covered += 1

    coverage_ratio = covered / len(required_cols) if required_cols else 0.0
    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "coverage_ratio": round(float(coverage_ratio), 6),
        "missing_ratio": round(float(1.0 - coverage_ratio), 6),
        "non_null_rate_by_col": non_null_rate_by_col,
    }


def _raw_input_stats(bundle: dict[str, pd.DataFrame], trade_date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stem, df in bundle.items():
        if stem == "moneyflow_hsgt_market":
            path = _raw_path("moneyflow_hsgt", trade_date)
        elif stem == "namechange":
            path = _raw_path("namechange", trade_date)
        else:
            path = _raw_path(stem, trade_date)

        out[stem] = {
            "path": str(path),
            "loaded": bool(df is not None and not df.empty),
            "rows": int(len(df)) if df is not None and not df.empty else 0,
            "columns": list(df.columns) if df is not None and not df.empty else [],
        }

    sync_meta = _safe_json_load(_sync_meta_path(trade_date))
    out["_sync_meta"] = {
        "path": str(_sync_meta_path(trade_date)),
        "loaded": _sync_meta_path(trade_date).exists(),
        "content": sync_meta,
    }
    return out


def build_meta(
    trade_date: str,
    bundle: dict[str, pd.DataFrame],
    master_df: pd.DataFrame,
    base_df: pd.DataFrame,
    limit_df: pd.DataFrame,
    truth_df: pd.DataFrame,
) -> dict[str, Any]:
    base_required = [
        "open", "high", "low", "close", "pre_close_est", "returns_1d",
        "vol", "amount", "turnover_rate", "volume_ratio",
        "volatility_5d", "volatility_10d", "volatility_20d",
        "atr", "downside_vol", "max_drawdown_20d", "tail_risk_score",
        "ret_2d", "ret_5d", "ret_10d", "bid_ask_proxy", "spread_proxy",
        "total_mv", "float_mv",
        "north_money_market", "south_money_market",
        "hot_boards_score", "board_crowding_rank",
        "is_hot_board", "board_limit_up_count", "is_st_like",
        "auction_amount", "auction_strength_score",
    ]
    limit_required = [
        "is_limit_up", "limit_type", "open_times", "seal_amount",
        "first_seal_time", "last_seal_time", "break_count_proxy",
        "is_hot_board", "board_rank", "limit_up_strength",
        "intraday_available", "intraday_quality_score",
        "intraday_risk_score", "late_withdraw_score", "reseal_score",
        "open_board_count", "auction_strength_score",
    ]
    truth_required = ["close", "pre_close_est", "pct_chg"]

    sha = os.getenv("GITHUB_SHA", "")
    base_stats = _coverage_stats(base_df, base_required)
    limit_stats = _coverage_stats(limit_df, limit_required)
    truth_stats = _coverage_stats(truth_df, truth_required)

    richness_estimate = round(
        (base_stats["coverage_ratio"] + limit_stats["coverage_ratio"] + truth_stats["coverage_ratio"]) / 3.0,
        6,
    )

    upstream_sync_meta = _safe_json_load(_sync_meta_path(trade_date))

    return {
        "trade_date": trade_date,
        "created_at_utc": _now_utc(),
        "commit_sha": sha,
        "fs_version": "v0.6-intraday-auction-features",
        "richness_target": 0.75,
        "richness_estimate": richness_estimate,
        "raw_inputs": _raw_input_stats(bundle, trade_date),
        "upstream_sync_summary": {
            "requested_trade_date": upstream_sync_meta.get("requested_trade_date"),
            "resolved_trade_date": upstream_sync_meta.get("resolved_trade_date"),
            "summary": upstream_sync_meta.get("summary", {}),
            "required_failures": upstream_sync_meta.get("required_failures", []),
        },
        "master_rows": int(len(master_df)) if master_df is not None else 0,
        "tables": {
            "features_base": base_stats,
            "features_limit": limit_stats,
            "truth_close": truth_stats,
        },
        "notes": [
            "本版本已按真实上游表头修正 raw -> FS 的字段映射。",
            "daily.csv 无 pre_close，当前使用 close 和 pct_chg/100 反推 pre_close_est。",
            "已接入历史 daily.csv，计算 ret_2d/5d/10d 与 volatility_5d/10d/20d。",
            "已新增 atr/downside_vol/max_drawdown_20d/tail_risk_score 作为风险骨架特征。",
            "moneyflow_hsgt 当前按市场级数据处理，并按 trade_date 广播到个股特征层。",
            "limit_up_tags 当前已按真实个股热板属性表接入。",
            "namechange 当前作为审计/风险辅助层接入。",
            "已接入 intraday_features.csv 和 stk_auction.csv，供 Decision 排序与风险层使用。",
        ],
    }


# -----------------------------
# 输出
# -----------------------------
def write_outputs(
    trade_date: str,
    base_df: pd.DataFrame,
    limit_df: pd.DataFrame,
    truth_df: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    _ensure_dir(MARKET_DIR)

    base_path = MARKET_DIR / f"features_base_{trade_date}.csv"
    limit_path = MARKET_DIR / f"features_limit_{trade_date}.csv"
    truth_path = MARKET_DIR / f"truth_close_{trade_date}.csv"
    meta_path = MARKET_DIR / f"_meta_{trade_date}.json"

    base_df.to_csv(base_path, index=False, encoding="utf-8-sig")
    limit_df.to_csv(limit_path, index=False, encoding="utf-8-sig")
    truth_df.to_csv(truth_path, index=False, encoding="utf-8-sig")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# -----------------------------
# main
# -----------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 market Feature Store 四件套")
    parser.add_argument("--trade-date", dest="trade_date", default=None, help="交易日 YYYYMMDD")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    trade_date = _resolve_trade_date(args.trade_date)

    if not trade_date:
        print("[build_market_fs] ERROR: 无法解析 trade_date，且 raw 目录中无可用 dated daily 文件")
        return 2

    bundle = load_raw_bundle(trade_date)
    daily_df = bundle.get("daily", pd.DataFrame())

    if daily_df.empty:
        print(f"[build_market_fs] ERROR: 缺少核心输入 daily.csv（按 trade_date={trade_date} 解析）")
        return 2

    master_df = build_master_table(bundle, trade_date)
    if master_df.empty:
        print(f"[build_market_fs] ERROR: 多源合并后 master 为空: trade_date={trade_date}")
        return 2

    base_df = build_features_base(master_df)
    limit_df = build_features_limit(master_df)
    truth_df = build_truth_close(master_df)
    meta = build_meta(
        trade_date=trade_date,
        bundle=bundle,
        master_df=master_df,
        base_df=base_df,
        limit_df=limit_df,
        truth_df=truth_df,
    )

    write_outputs(
        trade_date=trade_date,
        base_df=base_df,
        limit_df=limit_df,
        truth_df=truth_df,
        meta=meta,
    )

    print(f"[build_market_fs] OK trade_date={trade_date}")
    print(f"[build_market_fs] master_rows={len(master_df)}")
    print("[build_market_fs] outputs:")
    print(f"  - data/market/features_base_{trade_date}.csv")
    print(f"  - data/market/features_limit_{trade_date}.csv")
    print(f"  - data/market/truth_close_{trade_date}.csv")
    print(f"  - data/market/_meta_{trade_date}.json")
    print(f"[build_market_fs] richness_estimate={meta.get('richness_estimate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
