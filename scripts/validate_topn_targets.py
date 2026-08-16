# -*- coding: utf-8 -*-
"""
TopN Targets / Final Rank Top10 验证脚本｜最终收口版

核心口径：
1. 原需求：TopN Targets 每日预测名单做 D -> T 后验验证；
2. 若 TopN Targets 表为空，则在“总表”中取前 10 名；
3. 工程实现上，“总表”的真实数据源不是 markdown 展示表，而是：
      data/decision/decision_candidates_YYYYMMDD.csv
4. 因此本脚本最终采用：
      - report 只用于读取 signal_date / exec_date 元信息；
      - candidates CSV 用于读取最终总排序；
      - 按 rank 升序优先；若无 rank，则按 EV 降序；
      - 取前 topn，标记 validation_source = final_rank_top10_from_candidates；
5. D 日 = signal_date；
6. T 日 = exec_date；
7. T 日涨跌验证严格按 D_close -> T_close 计算；
8. 缺行情、停牌、字段缺失不静默填 0，全部标注 validation_status / validation_note；
9. 每次运行重算历史输出，天然幂等。

建议运行：
    python scripts/validate_topn_targets.py
    python scripts/validate_topn_targets.py --start-date 20260301 --end-date 20260506
    python scripts/validate_topn_targets.py --topn 10

兼容：
    也可复制为 scripts/backfill_topn_targets_validation.py 使用。
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_DIR = REPO_ROOT / "outputs" / "decision"
DECISION_DIR = REPO_ROOT / "data" / "decision"
OUTPUT_DIR = REPO_ROOT / "outputs" / "validation"
DOCS_DIR = REPO_ROOT / "docs"

HISTORY_CSV = OUTPUT_DIR / "topn_targets_validation_history.csv"
LATEST_CSV = OUTPUT_DIR / "topn_targets_validation_latest.csv"
SUMMARY_JSON = OUTPUT_DIR / "topn_targets_validation_summary.json"
SUMMARY_MD = OUTPUT_DIR / "topn_targets_validation_summary.md"
BY_DATE_CSV = OUTPUT_DIR / "topn_targets_validation_by_date.csv"
BY_RANK_CSV = OUTPUT_DIR / "topn_targets_validation_by_rank.csv"
BY_EV_RISK_CSV = OUTPUT_DIR / "topn_targets_validation_by_ev_risk.csv"
HTML_PATH = DOCS_DIR / "topn_targets_validation.html"

REPORT_PATTERN = re.compile(r"decision_report_(\d{8})\.md$")
CANDIDATES_PATTERN = re.compile(r"decision_candidates_(\d{8})\.csv$")

DATE_COLUMNS = ["trade_date", "date", "交易日期", "日期"]
CODE_COLUMNS = ["ts_code", "code", "symbol", "股票代码", "证券代码"]
NAME_COLUMNS = ["name", "stock_name", "股票名称", "证券简称", "名称"]
CLOSE_COLUMNS = ["close", "close_price", "收盘", "收盘价", "last_close"]
PCT_COLUMNS = ["pct_chg", "pct_change", "change_pct", "涨跌幅"]
UP_LIMIT_COLUMNS = ["up_limit", "limit_up", "涨停价"]
DOWN_LIMIT_COLUMNS = ["down_limit", "limit_down", "跌停价"]

EV_COLUMNS = ["EV", "ev"]
RISK_COLUMNS = ["RiskPenalty", "risk_penalty"]
RANK_COLUMNS = ["rank", "TopN_rank", "decision_rank", "EV_rank", "ev_rank"]

MARKET_SEARCH_DIRS = [
    REPO_ROOT / "data" / "market",
    REPO_ROOT / "data" / "raw",
    REPO_ROOT / "data" / "pred",
    REPO_ROOT / "data" / "pred" / "archive",
    REPO_ROOT / "data" / "decision",
    REPO_ROOT / "data",
]


@dataclass
class MarketLookupResult:
    status: str
    note: str
    d_close: Optional[float] = None
    t_close: Optional[float] = None
    t_return_pct: Optional[float] = None
    pct_chg: Optional[float] = None
    up_limit: Optional[float] = None
    down_limit: Optional[float] = None
    name: Optional[str] = None


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def norm_date(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"^\*+|\*+$", "", s)
    s = s.replace("`", "")
    if re.fullmatch(r"\d{8}\.0", s):
        s = s[:8]
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return digits[:8]
    return None


def fmt_date(value: Optional[str]) -> str:
    d = norm_date(value)
    if not d:
        return ""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def parse_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def find_first_col(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    lower_map = {str(c).lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        lc = c.lower()
        if lc in lower_map:
            return lower_map[lc]
    return None


def safe_read_csv(path: Path) -> Optional[pd.DataFrame]:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None
    except Exception as exc:
        print(f"[WARN] 读取 CSV 失败: {path} | {exc}", file=sys.stderr)
        return None


def parse_report_meta(text: str) -> Dict[str, str]:
    """
    兼容：
      signal_date: 20260430
      - signal_date: **20260430**
      - signal_date: `20260430`
    """
    meta: Dict[str, str] = {}
    for key in ["signal_date", "exec_date", "requested_trade_date"]:
        m = re.search(
            rf"(?m)^\s*(?:[-*+]\s*)?{re.escape(key)}\s*:\s*(.+?)\s*$",
            text,
        )
        if m:
            val = m.group(1).strip()
            val = re.sub(r"^\*+|\*+$", "", val)
            val = val.replace("`", "").strip()
            meta[key] = val
    return meta


def list_report_files() -> List[Tuple[str, Path]]:
    files: List[Tuple[str, Path]] = []
    if not REPORT_DIR.exists():
        return files
    for p in sorted(REPORT_DIR.glob("decision_report_*.md")):
        m = REPORT_PATTERN.search(p.name)
        if m:
            files.append((m.group(1), p))
    return files


def candidate_path_for_signal_date(signal_date: str) -> Path:
    return DECISION_DIR / f"decision_candidates_{signal_date}.csv"


def fallback_candidate_paths(report_date: str) -> List[Path]:
    paths = [
        DECISION_DIR / f"decision_candidates_{report_date}.csv",
    ]
    if DECISION_DIR.exists():
        for p in sorted(DECISION_DIR.glob("decision_candidates_*.csv")):
            if p not in paths:
                paths.append(p)
    return paths


def read_final_rank_top10_from_candidates(
    report_date: str,
    signal_date: str,
    report_path: Path,
    topn: int,
) -> Tuple[pd.DataFrame, str]:
    """
    最终需求口径：
    TopN Targets 为空时，取总表前10。
    工程实现：总表 = decision_candidates_YYYYMMDD.csv 的最终排序结果。
    优先用 signal_date 对应 candidates；不存在时再尝试 report_date 或其它候选文件。
    """
    paths = [candidate_path_for_signal_date(signal_date)]
    for p in fallback_candidate_paths(report_date):
        if p not in paths:
            paths.append(p)

    chosen: Optional[Path] = None
    df: Optional[pd.DataFrame] = None

    for p in paths:
        tmp = safe_read_csv(p)
        if tmp is not None and not tmp.empty:
            chosen = p
            df = tmp
            break

    if df is None or df.empty or chosen is None:
        return pd.DataFrame(), f"missing candidates csv for signal_date={signal_date}, report_date={report_date}"

    code_col = find_first_col(df, CODE_COLUMNS)
    if not code_col:
        return pd.DataFrame(), f"candidates missing ts_code/code column: {chosen}"

    rank_col = find_first_col(df, RANK_COLUMNS)
    ev_col = find_first_col(df, EV_COLUMNS)

    work = df.copy()

    if rank_col:
        work["_final_rank_sort"] = pd.to_numeric(work[rank_col], errors="coerce")
        work = work.sort_values("_final_rank_sort", ascending=True, na_position="last")
    elif ev_col:
        work["_ev_sort"] = pd.to_numeric(work[ev_col], errors="coerce")
        work = work.sort_values("_ev_sort", ascending=False, na_position="last")
    else:
        work["_fallback_order"] = range(1, len(work) + 1)

    top = work.head(topn).copy()

    if "TopN_rank" not in top.columns:
        if rank_col:
            top["TopN_rank"] = pd.to_numeric(top[rank_col], errors="coerce")
            if top["TopN_rank"].isna().all():
                top["TopN_rank"] = range(1, len(top) + 1)
        else:
            top["TopN_rank"] = range(1, len(top) + 1)

    top["validation_source"] = "final_rank_top10_from_candidates"
    top["report_file"] = report_path.name
    top["candidate_file"] = str(chosen.relative_to(REPO_ROOT))

    for c in ["_final_rank_sort", "_ev_sort", "_fallback_order"]:
        if c in top.columns:
            top = top.drop(columns=[c])

    return top, "ok"


def discover_csv_files() -> List[Path]:
    out: List[Path] = []
    seen = set()
    for base in MARKET_SEARCH_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*.csv"):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            normalized = str(p).replace("\\", "/")
            if "outputs/validation" in normalized:
                continue
            out.append(p)
    return out


def first_float_from_row(row: pd.Series, cols: Sequence[str]) -> Optional[float]:
    lower_map = {str(c).lower(): c for c in row.index}
    for c in cols:
        if c in row.index:
            v = parse_float(row.get(c))
            if v is not None:
                return v
        lc = c.lower()
        if lc in lower_map:
            v = parse_float(row.get(lower_map[lc]))
            if v is not None:
                return v
    return None


def first_str_from_row(row: pd.Series, cols: Sequence[str]) -> Optional[str]:
    lower_map = {str(c).lower(): c for c in row.index}
    for c in cols:
        if c in row.index and pd.notna(row.get(c)):
            s = str(row.get(c)).strip()
            if s:
                return s
        lc = c.lower()
        if lc in lower_map and pd.notna(row.get(lower_map[lc])):
            s = str(row.get(lower_map[lc])).strip()
            if s:
                return s
    return None


class MarketStore:
    def __init__(self, csv_files: Sequence[Path]):
        self.csv_files = list(csv_files)
        self._frames: Dict[Path, Optional[pd.DataFrame]] = {}

    def _load(self, path: Path) -> Optional[pd.DataFrame]:
        if path not in self._frames:
            self._frames[path] = safe_read_csv(path)
        return self._frames[path]

    def get_row(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        ts_code = str(ts_code).strip()
        trade_date = norm_date(trade_date)
        if not ts_code or not trade_date:
            return None

        ordered = sorted(
            self.csv_files,
            key=lambda p: (0 if trade_date in p.name else 1, len(str(p))),
        )

        for path in ordered:
            df = self._load(path)
            if df is None or df.empty:
                continue

            dcol = find_first_col(df, DATE_COLUMNS)
            ccol = find_first_col(df, CODE_COLUMNS)
            close_col = find_first_col(df, CLOSE_COLUMNS)
            if not dcol or not ccol or not close_col:
                continue

            try:
                dates = df[dcol].map(norm_date)
                codes = df[ccol].astype(str).str.strip()
                mask = (dates == trade_date) & (codes == ts_code)
                if mask.any():
                    return df.loc[mask].iloc[0]
            except Exception:
                continue

        return None

    def validate_stock(self, ts_code: str, d_date: str, t_date: str) -> MarketLookupResult:
        d_row = self.get_row(ts_code, d_date)
        t_row = self.get_row(ts_code, t_date)

        d_close = None
        if d_row is not None:
            d_close = first_float_from_row(d_row, CLOSE_COLUMNS)

        if t_row is None:
            return MarketLookupResult(
                status="待验证" if d_close is not None else "缺行情",
                note="未找到 T 日行情",
                d_close=d_close,
            )

        t_close = first_float_from_row(t_row, CLOSE_COLUMNS)
        pct = first_float_from_row(t_row, PCT_COLUMNS)
        up_limit = first_float_from_row(t_row, UP_LIMIT_COLUMNS)
        down_limit = first_float_from_row(t_row, DOWN_LIMIT_COLUMNS)
        name = first_str_from_row(t_row, NAME_COLUMNS)

        if t_close is None:
            return MarketLookupResult(
                status="缺行情",
                note="找到 T 日记录但缺少 T_close",
                d_close=d_close,
            )

        if d_close is None or d_close == 0:
            if pct is not None:
                return MarketLookupResult(
                    status="已验证",
                    note="缺 D_close，使用行情 pct_chg",
                    d_close=d_close,
                    t_close=t_close,
                    t_return_pct=pct,
                    pct_chg=pct,
                    up_limit=up_limit,
                    down_limit=down_limit,
                    name=name,
                )
            return MarketLookupResult(
                status="缺行情",
                note="缺 D_close，无法计算涨跌幅",
                d_close=d_close,
                t_close=t_close,
            )

        ret = (t_close / d_close - 1.0) * 100.0
        return MarketLookupResult(
            status="已验证",
            note="",
            d_close=d_close,
            t_close=t_close,
            t_return_pct=ret,
            pct_chg=pct,
            up_limit=up_limit,
            down_limit=down_limit,
            name=name,
        )


def detect_limit_hit(
    ret: Optional[float],
    close_price: Optional[float],
    up_limit: Optional[float],
    down_limit: Optional[float],
) -> Tuple[bool, bool, str]:
    note = ""
    up_hit = False
    down_hit = False

    if close_price is not None and up_limit is not None and up_limit > 0:
        up_hit = close_price >= up_limit * 0.999
    elif ret is not None:
        up_hit = ret >= 9.85 or ret >= 19.7 or ret >= 29.5
        if up_hit:
            note += "涨停估算;"

    if close_price is not None and down_limit is not None and down_limit > 0:
        down_hit = close_price <= down_limit * 1.001
    elif ret is not None:
        down_hit = ret <= -9.85 or ret <= -19.7 or ret <= -29.5
        if down_hit:
            note += "跌停估算;"

    return up_hit, down_hit, note


def classify_result(ret: Optional[float], up_limit_hit: bool, down_limit_hit: bool) -> str:
    if ret is None or pd.isna(ret):
        return "未验证"
    if up_limit_hit:
        return "涨停"
    if down_limit_hit:
        return "跌停"
    if ret > 0.05:
        return "上涨"
    if ret < -0.05:
        return "下跌"
    return "平盘"


def build_rows_from_report(
    report_date: str,
    report_path: Path,
    market: MarketStore,
    topn: int,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict]:
    text = report_path.read_text(encoding="utf-8", errors="ignore")
    meta = parse_report_meta(text)

    signal_date = norm_date(meta.get("signal_date")) or report_date
    exec_date = norm_date(meta.get("exec_date"))

    if not exec_date:
        print(f"[SKIP] {report_path.name}: 缺 exec_date")
        return []

    if start_date and signal_date < start_date:
        return []
    if end_date and signal_date > end_date:
        return []

    top_df, read_note = read_final_rank_top10_from_candidates(
        report_date=report_date,
        signal_date=signal_date,
        report_path=report_path,
        topn=topn,
    )
    if top_df.empty:
        print(f"[SKIP] {report_path.name}: {read_note}")
        return []

    code_col = find_first_col(top_df, CODE_COLUMNS)
    if not code_col:
        print(f"[SKIP] {report_path.name}: final rank top10 缺 ts_code")
        return []

    rows: List[Dict] = []

    for _, row in top_df.iterrows():
        ts_code = str(row.get(code_col, "")).strip()
        if not ts_code:
            continue

        lookup = market.validate_stock(ts_code, signal_date, exec_date)
        ret = lookup.t_return_pct
        up_hit, down_hit, limit_note = detect_limit_hit(
            ret,
            lookup.t_close,
            lookup.up_limit,
            lookup.down_limit,
        )
        result = classify_result(ret, up_hit, down_hit)

        base = row.to_dict()
        name = first_str_from_row(row, NAME_COLUMNS) or lookup.name or ""

        base.update(
            {
                "D_trade_date": signal_date,
                "D_trade_date_fmt": fmt_date(signal_date),
                "T_trade_date": exec_date,
                "T_trade_date_fmt": fmt_date(exec_date),
                "validation_pair": f"D：{fmt_date(signal_date)} → T：{fmt_date(exec_date)}",
                "ts_code_norm": ts_code,
                "name_norm": name,
                "D_close": lookup.d_close,
                "T_close": lookup.t_close,
                "T_return_pct": ret,
                "T_result": result,
                "T_limit_hit": bool(up_hit),
                "T_down_limit_hit": bool(down_hit),
                "validation_status": lookup.status,
                "validation_note": ";".join(
                    x
                    for x in [
                        lookup.note,
                        limit_note,
                        "source=final_rank_top10_from_candidates",
                    ]
                    if x
                ),
            }
        )
        rows.append(base)

    print(
        f"[OK] report={report_path.name} D={signal_date} T={exec_date} "
        f"rows={len(rows)} source=final_rank_top10_from_candidates"
    )
    return rows


def summarize(history: pd.DataFrame) -> Dict:
    verified = (
        history[history.get("validation_status", "") == "已验证"].copy()
        if not history.empty
        else pd.DataFrame()
    )
    total = int(len(verified))

    if total == 0:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "verified_samples": 0,
            "up_count": 0,
            "up_rate": None,
            "limit_up_count": 0,
            "limit_up_rate": None,
            "down_count": 0,
            "down_rate": None,
            "flat_count": 0,
            "flat_rate": None,
            "mean_return_pct": None,
            "median_return_pct": None,
            "max_return_pct": None,
            "min_return_pct": None,
            "source": "data/decision/decision_candidates_YYYYMMDD.csv::final_rank_top10",
            "conclusion": "暂无已验证样本；若 history 有行但 verified=0，请检查 T 日行情是否存在。",
        }

    ret = pd.to_numeric(verified["T_return_pct"], errors="coerce")
    up_count = int((ret > 0.05).sum())
    down_count = int((ret < -0.05).sum())
    flat_count = int(((ret >= -0.05) & (ret <= 0.05)).sum())
    limit_up_count = int(
        verified["T_limit_hit"].astype(str).str.lower().isin(["true", "1"]).sum()
    )

    def rate(x: int) -> float:
        return round(x / total * 100.0, 4) if total else None

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "verified_samples": total,
        "up_count": up_count,
        "up_rate": rate(up_count),
        "limit_up_count": limit_up_count,
        "limit_up_rate": rate(limit_up_count),
        "down_count": down_count,
        "down_rate": rate(down_count),
        "flat_count": flat_count,
        "flat_rate": rate(flat_count),
        "mean_return_pct": round(float(ret.mean()), 4) if ret.notna().any() else None,
        "median_return_pct": round(float(ret.median()), 4) if ret.notna().any() else None,
        "max_return_pct": round(float(ret.max()), 4) if ret.notna().any() else None,
        "min_return_pct": round(float(ret.min()), 4) if ret.notna().any() else None,
        "source": "data/decision/decision_candidates_YYYYMMDD.csv::final_rank_top10",
        "conclusion": "已生成 final_rank_top10 后验验证统计。",
    }


def fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    try:
        if isinstance(v, float) and math.isnan(v):
            return "N/A"
        return f"{float(v):.2f}%"
    except Exception:
        return str(v)


def group_by_date(history: pd.DataFrame) -> pd.DataFrame:
    verified = (
        history[history.get("validation_status", "") == "已验证"].copy()
        if not history.empty
        else pd.DataFrame()
    )
    cols = [
        "D_trade_date",
        "T_trade_date",
        "TopN数量",
        "上涨数",
        "上涨率",
        "涨停数",
        "涨停率",
        "平均涨跌幅",
        "中位涨跌幅",
    ]
    if verified.empty:
        return pd.DataFrame(columns=cols)

    verified["ret_num"] = pd.to_numeric(verified["T_return_pct"], errors="coerce")
    rows = []
    for (d, t), g in verified.groupby(["D_trade_date", "T_trade_date"], dropna=False):
        n = len(g)
        up = int((g["ret_num"] > 0.05).sum())
        limit_up = int(g["T_limit_hit"].astype(str).str.lower().isin(["true", "1"]).sum())
        rows.append(
            {
                "D_trade_date": d,
                "T_trade_date": t,
                "TopN数量": n,
                "上涨数": up,
                "上涨率": round(up / n * 100, 4) if n else None,
                "涨停数": limit_up,
                "涨停率": round(limit_up / n * 100, 4) if n else None,
                "平均涨跌幅": round(float(g["ret_num"].mean()), 4) if n else None,
                "中位涨跌幅": round(float(g["ret_num"].median()), 4) if n else None,
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values("D_trade_date")


def metrics_row(label: str, g: pd.DataFrame) -> Dict:
    n = len(g)
    if n == 0:
        return {
            "排名层级": label,
            "样本数": 0,
            "上涨率": None,
            "涨停率": None,
            "平均涨跌幅": None,
            "中位涨跌幅": None,
        }

    ret = pd.to_numeric(g["T_return_pct"], errors="coerce")
    up = int((ret > 0.05).sum())
    limit_up = int(g["T_limit_hit"].astype(str).str.lower().isin(["true", "1"]).sum())
    return {
        "排名层级": label,
        "样本数": n,
        "上涨率": round(up / n * 100, 4),
        "涨停率": round(limit_up / n * 100, 4),
        "平均涨跌幅": round(float(ret.mean()), 4) if ret.notna().any() else None,
        "中位涨跌幅": round(float(ret.median()), 4) if ret.notna().any() else None,
    }


def group_by_rank(history: pd.DataFrame) -> pd.DataFrame:
    verified = (
        history[history.get("validation_status", "") == "已验证"].copy()
        if not history.empty
        else pd.DataFrame()
    )
    cols = ["排名层级", "样本数", "上涨率", "涨停率", "平均涨跌幅", "中位涨跌幅"]
    if verified.empty or "TopN_rank" not in verified.columns:
        return pd.DataFrame(columns=cols)

    verified["rank_num"] = pd.to_numeric(verified["TopN_rank"], errors="coerce")
    buckets = [
        ("Top1", lambda x: x == 1),
        ("Top1-2", lambda x: (x >= 1) & (x <= 2)),
        ("Top1-3", lambda x: (x >= 1) & (x <= 3)),
        ("Top1-4", lambda x: (x >= 1) & (x <= 4)),
        ("Top1-5", lambda x: (x >= 1) & (x <= 5)),
        ("Top6-10", lambda x: (x >= 6) & (x <= 10)),
    ]

    rows = []
    for label, func in buckets:
        rows.append(metrics_row(label, verified[func(verified["rank_num"])]))
    return pd.DataFrame(rows, columns=cols)


def range_label(group: str, q1: float, q2: float) -> str:
    if group.startswith("低"):
        return f"≤ {q1:.6g}"
    if group.startswith("中"):
        return f"{q1:.6g} ~ {q2:.6g}"
    return f"> {q2:.6g}"


def group_by_ev_risk(history: pd.DataFrame) -> pd.DataFrame:
    verified = (
        history[history.get("validation_status", "") == "已验证"].copy()
        if not history.empty
        else pd.DataFrame()
    )
    cols = [
        "组合",
        "EV范围",
        "RiskPenalty范围",
        "样本数",
        "上涨率",
        "涨停率",
        "平均涨跌幅",
        "中位涨跌幅",
        "结论",
    ]
    if verified.empty:
        return pd.DataFrame(columns=cols)

    ev_col = find_first_col(verified, EV_COLUMNS)
    risk_col = find_first_col(verified, RISK_COLUMNS)
    if not ev_col or not risk_col:
        return pd.DataFrame(columns=cols)

    verified["_ev_num"] = pd.to_numeric(verified[ev_col], errors="coerce")
    verified["_risk_num"] = pd.to_numeric(verified[risk_col], errors="coerce")
    verified = verified[verified["_ev_num"].notna() & verified["_risk_num"].notna()].copy()
    if verified.empty:
        return pd.DataFrame(columns=cols)

    ev_q1 = float(verified["_ev_num"].quantile(1 / 3))
    ev_q2 = float(verified["_ev_num"].quantile(2 / 3))
    risk_q1 = float(verified["_risk_num"].quantile(1 / 3))
    risk_q2 = float(verified["_risk_num"].quantile(2 / 3))

    def ev_group(v: float) -> str:
        if v <= ev_q1:
            return "低EV"
        if v <= ev_q2:
            return "中EV"
        return "高EV"

    def risk_group(v: float) -> str:
        if v <= risk_q1:
            return "低Risk"
        if v <= risk_q2:
            return "中Risk"
        return "高Risk"

    verified["_ev_group"] = verified["_ev_num"].map(ev_group)
    verified["_risk_group"] = verified["_risk_num"].map(risk_group)

    conclusion_map = {
        "高EV + 低Risk": "最优候选池",
        "高EV + 中Risk": "可观察",
        "高EV + 高Risk": "高收益高波动",
        "中EV + 低Risk": "稳健但弹性不足",
        "中EV + 中Risk": "中性",
        "中EV + 高Risk": "风险偏高",
        "低EV + 低Risk": "防守型但弹性不足",
        "低EV + 中Risk": "弱解释力",
        "低EV + 高Risk": "应弱化或剔除",
    }

    rows = []
    for eg in ["高EV", "中EV", "低EV"]:
        for rg in ["低Risk", "中Risk", "高Risk"]:
            g = verified[(verified["_ev_group"] == eg) & (verified["_risk_group"] == rg)]
            n = len(g)
            ret = pd.to_numeric(g["T_return_pct"], errors="coerce") if n else pd.Series(dtype=float)
            up = int((ret > 0.05).sum()) if n else 0
            limit_up = int(g["T_limit_hit"].astype(str).str.lower().isin(["true", "1"]).sum()) if n else 0
            combo = f"{eg} + {rg}"
            rows.append(
                {
                    "组合": combo,
                    "EV范围": range_label(eg, ev_q1, ev_q2),
                    "RiskPenalty范围": range_label(rg, risk_q1, risk_q2),
                    "样本数": n,
                    "上涨率": round(up / n * 100, 4) if n else None,
                    "涨停率": round(limit_up / n * 100, 4) if n else None,
                    "平均涨跌幅": round(float(ret.mean()), 4) if n and ret.notna().any() else None,
                    "中位涨跌幅": round(float(ret.median()), 4) if n and ret.notna().any() else None,
                    "结论": conclusion_map.get(combo, ""),
                }
            )
    return pd.DataFrame(rows, columns=cols)


def correlation_summary(history: pd.DataFrame) -> Dict:
    verified = (
        history[history.get("validation_status", "") == "已验证"].copy()
        if not history.empty
        else pd.DataFrame()
    )
    out = {
        "corr_EV_return": None,
        "corr_RiskPenalty_return": None,
        "corr_EV_up": None,
        "corr_RiskPenalty_down": None,
        "sample_note": "样本不足",
    }

    if verified.empty or len(verified) < 3:
        return out

    ev_col = find_first_col(verified, EV_COLUMNS)
    risk_col = find_first_col(verified, RISK_COLUMNS)
    if not ev_col or not risk_col:
        out["sample_note"] = "缺 EV 或 RiskPenalty 字段"
        return out

    verified["_ev_num"] = pd.to_numeric(verified[ev_col], errors="coerce")
    verified["_risk_num"] = pd.to_numeric(verified[risk_col], errors="coerce")
    verified["_ret_num"] = pd.to_numeric(verified["T_return_pct"], errors="coerce")
    verified["_up_flag"] = (verified["_ret_num"] > 0.05).astype(int)
    verified["_down_flag"] = (verified["_ret_num"] < -0.05).astype(int)

    use = verified[["_ev_num", "_risk_num", "_ret_num", "_up_flag", "_down_flag"]].dropna()
    if len(use) < 3:
        return out

    def corr(a: str, b: str) -> Optional[float]:
        try:
            v = use[a].corr(use[b])
            if pd.isna(v):
                return None
            return round(float(v), 6)
        except Exception:
            return None

    out["corr_EV_return"] = corr("_ev_num", "_ret_num")
    out["corr_RiskPenalty_return"] = corr("_risk_num", "_ret_num")
    out["corr_EV_up"] = corr("_ev_num", "_up_flag")
    out["corr_RiskPenalty_down"] = corr("_risk_num", "_down_flag")

    n = len(use)
    if n < 30:
        out["sample_note"] = "样本<30，只展示，不下结论"
    elif n < 100:
        out["sample_note"] = "样本30-99，初步趋势"
    elif n < 300:
        out["sample_note"] = "样本100-299，可阶段性评估"
    else:
        out["sample_note"] = "样本>=300，可作为较稳定评估依据"
    return out


def write_summary_md(summary: Dict, corr: Dict) -> None:
    lines = [
        "# TopN Targets / Final Rank Top10 验证统计摘要",
        "",
        f"- 数据源：{summary.get('source', '')}",
        f"- 生成时间：{summary.get('generated_at', '')}",
        f"- 历史累计样本总数：{summary.get('verified_samples', 0)}",
        f"- 上涨数量：{summary.get('up_count', 0)}",
        f"- 上涨率：{fmt_pct(summary.get('up_rate'))}",
        f"- 涨停数量：{summary.get('limit_up_count', 0)}",
        f"- 涨停率：{fmt_pct(summary.get('limit_up_rate'))}",
        f"- 下跌数量：{summary.get('down_count', 0)}",
        f"- 下跌率：{fmt_pct(summary.get('down_rate'))}",
        f"- 平盘数量：{summary.get('flat_count', 0)}",
        f"- 平盘率：{fmt_pct(summary.get('flat_rate'))}",
        f"- 平均涨跌幅：{fmt_pct(summary.get('mean_return_pct'))}",
        f"- 中位涨跌幅：{fmt_pct(summary.get('median_return_pct'))}",
        f"- 最大涨幅：{fmt_pct(summary.get('max_return_pct'))}",
        f"- 最大跌幅：{fmt_pct(summary.get('min_return_pct'))}",
        "",
        "## EV / RiskPenalty 相关性",
        "",
        f"- corr_EV_return：{corr.get('corr_EV_return')}",
        f"- corr_RiskPenalty_return：{corr.get('corr_RiskPenalty_return')}",
        f"- corr_EV_up：{corr.get('corr_EV_up')}",
        f"- corr_RiskPenalty_down：{corr.get('corr_RiskPenalty_down')}",
        f"- 样本说明：{corr.get('sample_note')}",
        "",
        summary.get("conclusion", ""),
        "",
    ]
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")


def df_to_html(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<p class='note'>暂无数据</p>"

    show = df.head(max_rows).copy()

    priority = [
        "TopN_rank",
        "rank",
        "D_trade_date_fmt",
        "T_trade_date_fmt",
        "validation_pair",
        "ts_code",
        "ts_code_norm",
        "name",
        "name_norm",
        "weight",
        "EV",
        "P_fill",
        "E_ret",
        "Cost",
        "RiskPenalty",
        "D_close",
        "T_close",
        "T_return_pct",
        "T_result",
        "T_limit_hit",
        "validation_status",
        "validation_note",
        "validation_source",
        "candidate_file",
        "report_file",
    ]
    cols = [c for c in priority if c in show.columns] + [c for c in show.columns if c not in priority]
    show = show[cols[:55]]

    def fmt_cell(col: str, val) -> str:
        if pd.isna(val):
            return ""
        if col == "T_return_pct":
            try:
                f = float(val)
                cls = "up" if f > 0.05 else "down" if f < -0.05 else "flat"
                sign = "+" if f > 0 else ""
                return f"<span class='{cls}'>{sign}{f:.2f}%</span>"
            except Exception:
                return html.escape(str(val))
        if col in {"上涨率", "涨停率", "下跌率", "平盘率", "平均涨跌幅", "中位涨跌幅"}:
            try:
                return f"{float(val):.2f}%"
            except Exception:
                return html.escape(str(val))
        return html.escape(str(val))

    header = "".join(f"<th>{html.escape(str(c))}</th>" for c in show.columns)
    rows = []
    for _, r in show.iterrows():
        rows.append(
            "<tr>"
            + "".join(f"<td>{fmt_cell(c, r[c])}</td>" for c in show.columns)
            + "</tr>"
        )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_html(
    summary: Dict,
    corr: Dict,
    latest: pd.DataFrame,
    by_date: pd.DataFrame,
    by_rank: pd.DataFrame,
    by_ev_risk: pd.DataFrame,
    history: pd.DataFrame,
) -> None:
    latest_title = ""
    if not latest.empty and "D_trade_date_fmt" in latest.columns and "T_trade_date_fmt" in latest.columns:
        latest_title = f"D：{latest['D_trade_date_fmt'].iloc[0]} → T：{latest['T_trade_date_fmt'].iloc[0]}"

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TopN Targets / Final Rank Top10 验证系统</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #24292f; }}
    h1, h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }}
    .meta {{ color: #57606a; margin-bottom: 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }}
    .card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; background: #f6f8fa; }}
    .card b {{ display: block; font-size: 20px; margin-top: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px 0; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; white-space: nowrap; }}
    th {{ background: #f6f8fa; position: sticky; top: 0; }}
    .table-wrap {{ overflow-x: auto; }}
    .up {{ color: #cf222e; font-weight: 600; }}
    .down {{ color: #1a7f37; font-weight: 600; }}
    .flat {{ color: #57606a; }}
    .note {{ color: #57606a; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>TopN Targets / Final Rank Top10 验证系统</h1>
  <div class="meta">数据源：{html.escape(str(summary.get("source", "")))}；生成时间：{html.escape(str(summary.get("generated_at", "")))}；最新验证：{html.escape(latest_title)}</div>

  <h2>累计统计</h2>
  <div class="cards">
    <div class="card">历史累计样本总数<b>{summary.get("verified_samples", 0)}</b></div>
    <div class="card">上涨率<b>{fmt_pct(summary.get("up_rate"))}</b></div>
    <div class="card">涨停率<b>{fmt_pct(summary.get("limit_up_rate"))}</b></div>
    <div class="card">下跌率<b>{fmt_pct(summary.get("down_rate"))}</b></div>
    <div class="card">平盘率<b>{fmt_pct(summary.get("flat_rate"))}</b></div>
    <div class="card">平均涨跌幅<b>{fmt_pct(summary.get("mean_return_pct"))}</b></div>
    <div class="card">中位涨跌幅<b>{fmt_pct(summary.get("median_return_pct"))}</b></div>
  </div>

  <h2>EV / RiskPenalty 相关性</h2>
  <div class="cards">
    <div class="card">EV 与收益相关<b>{corr.get("corr_EV_return")}</b></div>
    <div class="card">Risk 与收益相关<b>{corr.get("corr_RiskPenalty_return")}</b></div>
    <div class="card">EV 与上涨相关<b>{corr.get("corr_EV_up")}</b></div>
    <div class="card">Risk 与下跌相关<b>{corr.get("corr_RiskPenalty_down")}</b></div>
  </div>
  <p class="note">{html.escape(str(corr.get("sample_note", "")))}</p>

  <h2>最新一期验证明细</h2>
  <div class="note">验证名单来自 data/decision/decision_candidates_YYYYMMDD.csv 的最终总排序前10；当 TopN Targets 展示表为空时，该口径等价于“总表前10”。</div>
  <div class="table-wrap">{df_to_html(latest, max_rows=30)}</div>

  <h2>按 D 日统计</h2>
  <div class="table-wrap">{df_to_html(by_date, max_rows=150)}</div>

  <h2>TopN 排名有效性统计</h2>
  <div class="table-wrap">{df_to_html(by_rank, max_rows=20)}</div>

  <h2>EV × RiskPenalty 分层统计</h2>
  <div class="table-wrap">{df_to_html(by_ev_risk, max_rows=30)}</div>

  <h2>历史明细最近 100 行</h2>
  <div class="table-wrap">{df_to_html(history.tail(100), max_rows=100)}</div>
</body>
</html>
"""
    HTML_PATH.write_text(html_text, encoding="utf-8")


def save_outputs(history: pd.DataFrame) -> None:
    ensure_dirs()

    if history.empty:
        history.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
        pd.DataFrame().to_csv(LATEST_CSV, index=False, encoding="utf-8-sig")
        summary = summarize(history)
        corr = correlation_summary(history)
        SUMMARY_JSON.write_text(
            json.dumps({**summary, "correlation": corr}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_summary_md(summary, corr)
        render_html(summary, corr, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), history)
        return

    if "TopN_rank" in history.columns:
        history["_rank_sort"] = pd.to_numeric(history["TopN_rank"], errors="coerce")
        history = history.sort_values(["D_trade_date", "_rank_sort"], na_position="last").drop(columns=["_rank_sort"])
    else:
        history = history.sort_values(["D_trade_date"])

    history.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")

    max_d = history["D_trade_date"].dropna().astype(str).max() if "D_trade_date" in history.columns else None
    latest = history[history["D_trade_date"].astype(str) == max_d].copy() if max_d else pd.DataFrame()
    latest.to_csv(LATEST_CSV, index=False, encoding="utf-8-sig")

    summary = summarize(history)
    corr = correlation_summary(history)
    by_date = group_by_date(history)
    by_rank = group_by_rank(history)
    by_ev_risk = group_by_ev_risk(history)

    SUMMARY_JSON.write_text(
        json.dumps({**summary, "correlation": corr}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_summary_md(summary, corr)
    by_date.to_csv(BY_DATE_CSV, index=False, encoding="utf-8-sig")
    by_rank.to_csv(BY_RANK_CSV, index=False, encoding="utf-8-sig")
    by_ev_risk.to_csv(BY_EV_RISK_CSV, index=False, encoding="utf-8-sig")
    render_html(summary, corr, latest, by_date, by_rank, by_ev_risk, history)


def run_backfill(
    start_date: Optional[str],
    end_date: Optional[str],
    topn: int,
    force: bool,
) -> pd.DataFrame:
    ensure_dirs()

    start_date = norm_date(start_date) if start_date else None
    end_date = norm_date(end_date) if end_date else None

    reports = list_report_files()
    if not reports:
        print(f"[WARN] 未找到报告文件: {REPORT_DIR}/decision_report_YYYYMMDD.md")
        history = pd.DataFrame()
        save_outputs(history)
        return history

    market = MarketStore(discover_csv_files())

    rows: List[Dict] = []
    for report_date, path in reports:
        rows.extend(
            build_rows_from_report(
                report_date=report_date,
                report_path=path,
                market=market,
                topn=topn,
                start_date=start_date,
                end_date=end_date,
            )
        )

    history = pd.DataFrame(rows)
    save_outputs(history)

    verified_n = 0
    if not history.empty and "validation_status" in history.columns:
        verified_n = int((history["validation_status"] == "已验证").sum())

    print(f"[DONE] source=final_rank_top10_from_candidates history_rows={len(history)} verified_rows={verified_n}")
    print(f"[DONE] wrote: {HISTORY_CSV}")
    print(f"[DONE] wrote: {HTML_PATH}")
    return history


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate top10-decision TopN Targets; if empty, use final rank top10 from decision_candidates."
    )
    parser.add_argument("--start-date", default=None, help="起始 D 日，格式 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="结束 D 日，格式 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--topn", type=int, default=10, help="默认 TopN 数量")
    parser.add_argument("--force", action="store_true", help="保留参数：当前实现每次重算历史输出，天然幂等")
    args = parser.parse_args()

    run_backfill(
        start_date=args.start_date,
        end_date=args.end_date,
        topn=args.topn,
        force=args.force,
    )


if __name__ == "__main__":
    main()
