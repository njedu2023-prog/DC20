#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
import pandas as pd


# =========================
# IO 契约常量（不允许改动含义）
# =========================

TOPK_DEFAULT = 100
TOPN_DEFAULT = 10

W_MAX_DEFAULT = 0.12
THEME_CAP_DEFAULT = 0.35
GROSS_CAP_DEFAULT = 1.00


# =========================
# A 股交易日历兜底
# =========================
#
# 说明：
# - 这里不引入第三方交易日历依赖，避免 GitHub Actions 增加不稳定依赖。
# - 周六/周日默认非交易日；中国调休上班日不等于 A 股交易日。
# - 这里维护的是“交易所休市日”兜底表，用于修正 T 日 -> T+1 执行日。
# - 若未来年份需要扩展，只需要追加 YYYYMMDD 字符串，不改变上下游路径。
#
# 当前先覆盖 2026 年已知 A 股主要休市日，重点修复：
#   signal_date=20260430 时，exec_date 必须滚到 20260506，而不是 20260501。
A_SHARE_EXCHANGE_HOLIDAYS = {
    # 2026 New Year
    "20260101",
    "20260102",

    # 2026 Spring Festival
    "20260216",
    "20260217",
    "20260218",
    "20260219",
    "20260220",
    "20260223",

    # 2026 Qingming Festival observed
    "20260406",

    # 2026 Labour Day
    "20260501",
    "20260504",
    "20260505",

    # 2026 Dragon Boat Festival
    "20260619",

    # 2026 Mid-Autumn Festival
    "20260925",

    # 2026 National Day
    "20261001",
    "20261002",
    "20261005",
    "20261006",
    "20261007",
}

A_SHARE_CALENDAR_YEARS = {2026}
TRADE_CALENDAR_PATH = Path(__file__).resolve().parents[3] / "data" / "market" / "trade_cal_sse.csv"


# =========================
# 通用工具（保持原行为）
# =========================

def ensure_cols(df: pd.DataFrame, cols: list[str]) -> None:
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise ValueError(f"缺少必要字段：{miss}. 现有字段：{list(df.columns)}")


def norm_ymd(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass

    s = str(v).strip()
    if not s:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    if len(s) == 8 and s.isdigit():
        return s
    try:
        i = int(float(s))
        s2 = str(i)
        return s2 if (len(s2) == 8 and s2.isdigit()) else s2
    except Exception:
        return s


def get_first_value(df: pd.DataFrame, col: str) -> str:
    if df is None or df.empty or col not in df.columns:
        return ""
    s = df[col].dropna()
    if s.empty:
        return ""
    if col in ("trade_date", "target_trade_date", "exec_date", "exit_date", "signal_date", "verify_date"):
        return norm_ymd(s.iloc[0])
    return str(s.iloc[0])


def fmt_num(x, nd=6):
    try:
        if pd.isna(x):
            return ""
        return f"{float(x):.{nd}f}".rstrip("0").rstrip(".")
    except Exception:
        return "" if x is None else str(x)


def _parse_ymd(value: str) -> datetime | None:
    ymd = norm_ymd(value)
    if not (len(ymd) == 8 and ymd.isdigit()):
        return None
    try:
        return datetime.strptime(ymd, "%Y%m%d")
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_exchange_calendar() -> dict[str, bool]:
    """Load the Tushare/SSE calendar snapshot when the workflow has synced it."""
    path = TRADE_CALENDAR_PATH
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        frame = pd.read_csv(path, dtype={"cal_date": str})
    except Exception as exc:
        raise RuntimeError(f"Cannot read strict A-share calendar: {path}: {exc}") from exc
    if not {"cal_date", "is_open"}.issubset(frame.columns):
        raise RuntimeError(
            f"Strict A-share calendar must contain cal_date/is_open: {path}; "
            f"columns={list(frame.columns)}"
        )
    result: dict[str, bool] = {}
    for index, row in frame.iterrows():
        ymd = norm_ymd(row.get("cal_date"))
        if len(ymd) != 8:
            raise RuntimeError(f"Invalid cal_date at row={index + 2}: {row.get('cal_date')!r}")
        try:
            flag = int(float(row.get("is_open")))
        except Exception as exc:
            raise RuntimeError(
                f"Invalid is_open at row={index + 2}: {row.get('is_open')!r}"
            ) from exc
        if flag not in {0, 1}:
            raise RuntimeError(f"is_open must be 0/1 at row={index + 2}: {flag}")
        is_open = flag == 1
        if ymd in result and result[ymd] != is_open:
            raise RuntimeError(f"Conflicting calendar rows for {ymd}: {path}")
        result[ymd] = is_open
    if not result:
        raise RuntimeError(f"Strict A-share calendar has no rows: {path}")
    return result


def _assert_a_share_calendar_covered(dt: datetime) -> None:
    """
    严格交易日历：只允许使用已显式维护过交易所休市日的年份。
    未覆盖年份继续用“周末+假日猜测”会直接产生错误执行日，所以这里硬失败。
    """
    synced = _load_exchange_calendar()
    if dt.strftime("%Y%m%d") in synced:
        return
    synced_years = {int(key[:4]) for key in synced if len(key) == 8 and key[:4].isdigit()}
    if dt.year not in A_SHARE_CALENDAR_YEARS and dt.year not in synced_years:
        covered = ",".join(str(y) for y in sorted(A_SHARE_CALENDAR_YEARS))
        raise RuntimeError(
            f"A-share trading calendar does not cover year={dt.year}; "
            f"covered_years={covered}. Sync trade_cal_sse.csv or update the official fallback first."
        )
    if dt.year in synced_years and dt.strftime("%Y%m%d") not in synced:
        raise RuntimeError(
            f"Synced A-share trading calendar is incomplete for {dt.strftime('%Y%m%d')}; "
            "refusing weekday inference."
        )


def is_a_share_trading_day(value: str) -> bool:
    """
    判断是否为 A 股交易日。
    - 周六/周日非交易日。
    - A_SHARE_EXCHANGE_HOLIDAYS 中列出的交易所休市日非交易日。
    """
    dt = _parse_ymd(value)
    if dt is None:
        return False
    _assert_a_share_calendar_covered(dt)
    ymd = dt.strftime("%Y%m%d")
    synced = _load_exchange_calendar()
    if ymd in synced:
        return synced[ymd]
    if dt.weekday() >= 5:
        return False
    if ymd in A_SHARE_EXCHANGE_HOLIDAYS:
        return False
    return True


def next_a_share_trading_day(value: str, include_self: bool = False, max_scan_days: int = 30) -> str:
    """
    返回指定日期之后的下一个 A 股交易日。

    include_self=True:
      - 如果 value 本身是交易日，则返回 value；
      - 如果 value 是休市日，则向后滚动到下一交易日。

    include_self=False:
      - 一定从 value 的下一自然日开始找交易日。
    """
    dt = _parse_ymd(value)
    if dt is None:
        raise ValueError(f"Invalid YYYYMMDD date for A-share calendar: {value}")
    _assert_a_share_calendar_covered(dt)

    cursor = dt if include_self else (dt + timedelta(days=1))
    for _ in range(max_scan_days):
        ymd = cursor.strftime("%Y%m%d")
        if is_a_share_trading_day(ymd):
            return ymd
        cursor += timedelta(days=1)

    raise RuntimeError(
        f"Cannot find next A-share trading day from {norm_ymd(value)} "
        f"within {max_scan_days} days; calendar is incomplete."
    )


def choose_exec_date(trade_date: str, target_trade_date: str) -> str:
    """
    决定实际执行日 exec_date。

    原逻辑：
      return target_trade_date or trade_date

    问题：
      对 A 股不安全。比如 signal_date=20260430，target_trade_date=20260501，
      但 20260501-20260505 是劳动节休市窗口，真实 exec_date 应该是 20260506。

    新逻辑：
      1. 若 target_trade_date 有值：
         - 若它晚于 trade_date，则以 target_trade_date 为候选执行日；
         - 若它不是 A 股交易日，向后滚到最近交易日。
      2. 若 target_trade_date 缺失或不晚于 trade_date：
         - 从 trade_date 的下一真实 A 股交易日开始找。
      3. 若 trade_date 无法解析：
         - 保持原兼容行为，返回 target_trade_date 或 trade_date。
    """
    td = norm_ymd(trade_date)
    ttd = norm_ymd(target_trade_date)

    td_dt = _parse_ymd(td)
    ttd_dt = _parse_ymd(ttd)

    if td_dt is None:
        if ttd:
            return next_a_share_trading_day(ttd, include_self=True)
        raise ValueError(f"Invalid trade_date for exec date resolution: {trade_date}")

    if ttd_dt is not None and ttd_dt > td_dt:
        return next_a_share_trading_day(ttd, include_self=True)

    return next_a_share_trading_day(td, include_self=False)


def choose_exit_date(exec_date: str) -> str:
    """
    T+1 卖出/验证日：执行日后的下一真实 A 股交易日。
    """
    return next_a_share_trading_day(exec_date, include_self=False)


# =========================
# 固定路径（IO 契约：绝对不改）
# =========================

# 输入快照
PRED_SNAPSHOT_PATH = Path("data/pred/pred_source_latest.csv")

# 输出：signals
SIGNAL_LATEST = Path("docs/signals/top10_latest.csv")
SIGNAL_DATED_FMT = "docs/signals/top10_{yyyymmdd}.csv"

# 输出：weights
WEIGHTS_LATEST = Path("docs/weights/weights_latest.csv")
WEIGHTS_DATED_FMT = "docs/weights/weights_{yyyymmdd}.csv"

# 输出：decision candidates
CANDIDATES_FMT = "data/decision/decision_candidates_{yyyymmdd}.csv"

# 输出：execution table
EXECUTION_FMT = "data/decision/decision_execution_{yyyymmdd}.csv"

# 输出：learning table
LEARNING_PATH = Path("data/decision/decision_learning.csv")

# 输出：report / eval
REPORT_FMT = "outputs/decision/decision_report_{yyyymmdd}.md"
EVAL_FMT = "outputs/decision/eval_{yyyymmdd}.json"
