# -*- coding: utf-8 -*-
"""
TopN Targets / Final Weights Top10 后验验证脚本｜weights 源最终版

核心口径：
1. 本脚本验证 top10-decision 计算引擎最终输出给下游/页面/信号链的排序结果。
2. 最终排序源固定为 docs/weights/weights_YYYYMMDD.csv，而不是：
   - outputs/decision/decision_report_YYYYMMDD.md 展示表；
   - data/decision/decision_candidates_YYYYMMDD.csv 候选计算明细表。
3. weights 文件排序规则：
   - 优先读取 target_rank 非空记录，按 target_rank 升序；
   - 若 target_rank 数量不足 topn，则继续读取 backup_rank 非空记录，按 backup_rank 升序补足；
   - 最终形成 TopN_rank = 1..topn；
   - 标记 validation_source = weights_target_then_backup。
4. D 日 / T 日口径：
   - T 日优先取 weights 文件中的 exec_date；若缺失则取文件名 weights_YYYYMMDD；
   - D 日优先从 outputs/decision/decision_report_T日.md 中解析 signal_date；
   - 若 report 缺失或解析失败，则尝试读取同文件中的 requested_trade_date；
   - 最后兜底为 weights 文件名日期，但会在 validation_note 中标注。
5. T 日真实涨跌验证严格按 D_close -> T_close 计算。
6. 缺行情、停牌、字段缺失不静默填 0，全部标注 validation_status / validation_note。
7. 每次运行重算历史输出，天然幂等。

建议运行：
python scripts/backfill_topn_targets_validation.py --topn 10 --start-date 20260302 --end-date 20260428
python scripts/backfill_topn_targets_validation.py --topn 10
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
WEIGHTS_DIR = REPO_ROOT / "docs" / "weights"
DECISION_DIR = REPO_ROOT / "data" / "decision"  # 只作为 EV/Risk 补充源，不作为排序源
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

WEIGHTS_PATTERN = re.compile(r"weights_(\d{8})\.csv$")
REPORT_PATTERN = re.compile(r"decision_report_(\d{8})\.md$")
CANDIDATES_IN_TEXT_PATTERN = re.compile(r"decision_candidates_(\d{8})\.csv")

DATE_COLUMNS = ["trade_date", "date", "交易日期", "日期"]
CODE_COLUMNS = ["ts_code", "code", "symbol", "股票代码", "证券代码"]
NAME_COLUMNS = ["name", "stock_name", "股票名称", "证券简称", "名称"]
CLOSE_COLUMNS = ["close", "close_price", "收盘", "收盘价", "last_close"]
PCT_COLUMNS = ["pct_chg", "pct_change", "change_pct", "涨跌幅"]
UP_LIMIT_COLUMNS = ["up_limit", "limit_up", "涨停价"]
DOWN_LIMIT_COLUMNS = ["down_limit", "limit_down", "跌停价"]

EV_COLUMNS = ["ev_pred", "EV", "ev", "ev_final", "EV_pred"]
RISK_COLUMNS = ["risk_penalty", "RiskPenalty", "risk_total_penalty"]

MARKET_SEARCH_DIRS = [
    REPO_ROOT / "data" / "market",
    REPO_ROOT / "data" / "raw",
    REPO_ROOT / "data" / "pred",
    REPO_ROOT / "data" / "pred" / "archive",
    REPO_ROOT / "data" / "decision",
    REPO_ROOT / "data",
]

SOURCE_LABEL = "docs/weights/weights_YYYYMMDD.csv::target_rank_then_backup_rank"
VALIDATION_SOURCE = "weights_target_then_backup"


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
    s = str(value).strip().replace("\ufeff", "")
    if not s:
        return None
    s = re.sub(r"^\*+|\*+$", "", s).replace("`", "")
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


def find_first_col(df_or_row, candidates: Sequence[str]) -> Optional[str]:
    cols = list(df_or_row.columns) if hasattr(df_or_row, "columns") else list(df_or_row.index)
    lower_map = {str(c).lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
        lc = c.lower()
        if lc in lower_map:
            return lower_map[lc]
    return None


def safe_read_csv(path: Path) -> Optional[pd.DataFrame]:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
        return pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return None
    except Exception as exc:
        print(f"[WARN] 读取 CSV 失败: {path} | {exc}", file=sys.stderr)
        return None


def parse_report_meta(text: str, report_path: Optional[Path] = None) -> Dict[str, str]:
    """兼容 Markdown 列表与加粗写法：- signal_date: **20260430**"""
    meta: Dict[str, str] = {}
    for key in ["signal_date", "exec_date", "requested_trade_date", "target_trade_date"]:
        m = re.search(rf"(?m)^\s*(?:[-*+]\s*)?{re.escape(key)}\s*:\s*(.+?)\s*$", text)
        if m:
            val = m.group(1).strip()
            val = re.sub(r"^\*+|\*+$", "", val).replace("`", "").strip()
            nd = norm_date(val)
            if nd:
                meta[key] = nd

    if "signal_date" not in meta:
        m = CANDIDATES_IN_TEXT_PATTERN.search(text)
        if m:
            meta["signal_date"] = m.group(1)

    if report_path is not None and "exec_date" not in meta:
        m = REPORT_PATTERN.search(report_path.name)
        if m:
            meta["exec_date"] = m.group(1)

    return meta


def list_weight_files() -> List[Tuple[str, Path]]:
    files: List[Tuple[str, Path]] = []
    if not WEIGHTS_DIR.exists():
        return files
    for p in sorted(WEIGHTS_DIR.glob("weights_*.csv")):
        if p.name == "weights_latest.csv":
            continue
        m = WEIGHTS_PATTERN.search(p.name)
        if m:
            files.append((m.group(1), p))
    return files


def report_path_for_exec_date(exec_date: str) -> Path:
    return REPORT_DIR / f"decision_report_{exec_date}.md"


def resolve_signal_and_exec_date(weight_file_date: str, weights_df: pd.DataFrame) -> Tuple[str, str, str]:
    """返回 signal_date, exec_date, note。"""
    note_parts: List[str] = []

    exec_date = None
    if "exec_date" in weights_df.columns and not weights_df.empty:
        exec_date = norm_date(weights_df["exec_date"].dropna().iloc[0]) if not weights_df["exec_date"].dropna().empty else None
    exec_date = exec_date or weight_file_date

    signal_date = None
    report_path = report_path_for_exec_date(exec_date)
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8", errors="ignore")
        meta = parse_report_meta(text, report_path=report_path)
        signal_date = norm_date(meta.get("signal_date")) or norm_date(meta.get("requested_trade_date"))
        if not signal_date:
            note_parts.append(f"report存在但未解析到signal_date:{report_path.name}")
    else:
        note_parts.append(f"缺report:{report_path.name}")

    if not signal_date:
        # 最后兜底：如果 weights 里有 trade_date / requested_trade_date 等字段，尝试读取。
        for c in ["signal_date", "trade_date", "requested_trade_date", "target_trade_date"]:
            if c in weights_df.columns and not weights_df.empty:
                vals = weights_df[c].dropna()
                if not vals.empty:
                    signal_date = norm_date(vals.iloc[0])
                    if signal_date:
                        note_parts.append(f"signal_date由weights.{c}兜底")
                        break

    if not signal_date:
        signal_date = weight_file_date
        note_parts.append("signal_date兜底为weights文件日期，需人工复核D/T口径")

    return signal_date, exec_date, ";".join(note_parts)


def read_final_topn_from_weights(weight_file_date: str, weight_path: Path, topn: int) -> Tuple[pd.DataFrame, str, str, str]:
    df = safe_read_csv(weight_path)
    if df is None or df.empty:
        return pd.DataFrame(), weight_file_date, weight_file_date, f"missing or empty weights csv: {weight_path}"

    code_col = find_first_col(df, CODE_COLUMNS)
    if not code_col:
        return pd.DataFrame(), weight_file_date, weight_file_date, f"weights missing ts_code/code column: {weight_path}"

    signal_date, exec_date, date_note = resolve_signal_and_exec_date(weight_file_date, df)

    work = df.copy()
    for c in ["target_rank", "backup_rank", "weight", "ev_pred"]:
        if c in work.columns:
            work[c] = pd.to_numeric(work[c], errors="coerce")

    selected_parts: List[pd.DataFrame] = []

    if "target_rank" in work.columns:
        targets = work[work["target_rank"].notna()].copy()
        if not targets.empty:
            targets["_rank_sort"] = targets["target_rank"]
            targets["_source_rank_type"] = "target_rank"
            targets = targets.sort_values("_rank_sort", ascending=True, na_position="last")
            selected_parts.append(targets)

    selected_codes = set()
    if selected_parts:
        selected_codes = set(selected_parts[0][code_col].astype(str).str.strip())

    if "backup_rank" in work.columns:
        backups = work[work["backup_rank"].notna()].copy()
        if selected_codes:
            backups = backups[~backups[code_col].astype(str).str.strip().isin(selected_codes)]
        if not backups.empty:
            backups["_rank_sort"] = backups["backup_rank"]
            backups["_source_rank_type"] = "backup_rank"
            backups = backups.sort_values("_rank_sort", ascending=True, na_position="last")
            selected_parts.append(backups)

    if not selected_parts:
        # 极端兜底：没有 target_rank / backup_rank，则按 ev_pred 降序。
        ev_col = find_first_col(work, EV_COLUMNS)
        if ev_col:
            work["_rank_sort"] = -pd.to_numeric(work[ev_col], errors="coerce")
            work["_source_rank_type"] = "ev_pred_fallback"
            selected_parts.append(work.sort_values("_rank_sort", ascending=True, na_position="last"))
        else:
            work["_rank_sort"] = range(1, len(work) + 1)
            work["_source_rank_type"] = "file_order_fallback"
            selected_parts.append(work)

    top = pd.concat(selected_parts, ignore_index=True).head(topn).copy()
    if top.empty:
        return pd.DataFrame(), signal_date, exec_date, f"no rows selected from weights: {weight_path}"

    top["TopN_rank"] = range(1, len(top) + 1)
    top["validation_source"] = VALIDATION_SOURCE
    top["weights_file"] = str(weight_path.relative_to(REPO_ROOT))
    top["weights_file_date"] = weight_file_date
    top["source_rank_type"] = top.get("_source_rank_type", "")
    top["source_rank_value"] = top.get("_rank_sort", "")

    for c in ["_rank_sort", "_source_rank_type"]:
        if c in top.columns:
            top = top.drop(columns=[c])

    return top, signal_date, exec_date, date_note or "ok"


def candidate_path_candidates(signal_date: str, exec_date: str) -> List[Path]:
    paths = [
        DECISION_DIR / f"decision_candidates_{signal_date}.csv",
        DECISION_DIR / f"decision_candidates_{exec_date}.csv",
    ]
    out: List[Path] = []
    for p in paths:
        if p not in out:
            out.append(p)
    return out


def augment_ev_risk_from_candidates(top_df: pd.DataFrame, signal_date: str, exec_date: str) -> pd.DataFrame:
    """排序源仍是 weights；这里只补充 RiskPenalty 等解释字段。"""
    if top_df.empty:
        return top_df
    code_col = find_first_col(top_df, CODE_COLUMNS)
    if not code_col:
        return top_df

    for p in candidate_path_candidates(signal_date, exec_date):
        cdf = safe_read_csv(p)
        if cdf is None or cdf.empty:
            continue
        c_code = find_first_col(cdf, CODE_COLUMNS)
        if not c_code:
            continue
        keep_cols = [c_code]
        for c in ["risk_penalty", "RiskPenalty", "risk_total_penalty", "ev_final", "ev_pred", "EV", "ev", "p_fill_pred", "e_ret_pred", "eret_pred", "cost_est"]:
            if c in cdf.columns and c not in keep_cols:
                keep_cols.append(c)
        right = cdf[keep_cols].copy()
        right["_merge_code"] = right[c_code].astype(str).str.strip()
        left = top_df.copy()
        left["_merge_code"] = left[code_col].astype(str).str.strip()
        merged = left.merge(right.drop(columns=[c_code]), on="_merge_code", how="left", suffixes=("", "_candidate"))
        merged = merged.drop(columns=["_merge_code"])
        merged["candidate_aug_file"] = str(p.relative_to(REPO_ROOT))
        return merged
    return top_df


def discover_csv_files() -> List[Path]:
    out: List[Path] = []
    seen = set()
    for base in MARKET_SEARCH_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*.csv"):
            normalized = str(p).replace("\\", "/")
            if "outputs/validation" in normalized:
                continue
            if "/docs/weights/" in normalized:
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(p)
    return out


def first_float_from_row(row: pd.Series, cols: Sequence[str]) -> Optional[float]:
    col = find_first_col(row, cols)
    if col:
        return parse_float(row.get(col))
    return None


def first_str_from_row(row: pd.Series, cols: Sequence[str]) -> Optional[str]:
    col = find_first_col(row, cols)
    if col and pd.notna(row.get(col)):
        s = str(row.get(col)).strip()
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

        ordered = sorted(self.csv_files, key=lambda p: (0 if trade_date in p.name else 1, len(str(p))))
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

        d_close = first_float_from_row(d_row, CLOSE_COLUMNS) if d_row is not None else None

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
            return MarketLookupResult(status="缺行情", note="找到 T 日记录但缺少 T_close", d_close=d_close)

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
            return MarketLookupResult(status="缺行情", note="缺 D_close，无法计算涨跌幅", d_close=d_close, t_close=t_close)

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


def detect_limit_hit(ret: Optional[float], close_price: Optional[float], up_limit: Optional[float], down_limit: Optional[float]) -> Tuple[bool, bool, str]:
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


def build_rows_from_weights(weight_file_date: str, weight_path: Path, market: MarketStore, topn: int, start_date: Optional[str], end_date: Optional[str]) -> List[Dict]:
    top_df, signal_date, exec_date, read_note = read_final_topn_from_weights(weight_file_date, weight_path, topn)

    if start_date and exec_date < start_date:
        return []
    if end_date and exec_date > end_date:
        return []

    if top_df.empty:
        print(f"[SKIP] {weight_path.name}: {read_note}")
        return []

    top_df = augment_ev_risk_from_candidates(top_df, signal_date, exec_date)

    code_col = find_first_col(top_df, CODE_COLUMNS)
    if not code_col:
        print(f"[SKIP] {weight_path.name}: selected weights topn 缺 ts_code")
        return []

    rows: List[Dict] = []
    for _, row in top_df.iterrows():
        ts_code = str(row.get(code_col, "")).strip()
        if not ts_code:
            continue

        lookup = market.validate_stock(ts_code, signal_date, exec_date)
        ret = lookup.t_return_pct
        up_hit, down_hit, limit_note = detect_limit_hit(ret, lookup.t_close, lookup.up_limit, lookup.down_limit)
        result = classify_result(ret, up_hit, down_hit)

        base = row.to_dict()
        name = first_str_from_row(row, NAME_COLUMNS) or lookup.name or ""
        note_parts = [lookup.note, limit_note, read_note if read_note != "ok" else "", f"source={VALIDATION_SOURCE}"]

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
                "validation_note": ";".join(x for x in note_parts if x),
            }
        )
        rows.append(base)

    print(f"[OK] weights={weight_path.name} D={signal_date} T={exec_date} rows={len(rows)} source={VALIDATION_SOURCE}")
    return rows



def _verified(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty or "validation_status" not in history.columns:
        return pd.DataFrame()
    return history[history["validation_status"] == "已验证"].copy()


def _ret_series(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "T_return_pct" not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df["T_return_pct"], errors="coerce")


def _limit_up_count(df: pd.DataFrame) -> int:
    if df is None or df.empty or "T_limit_hit" not in df.columns:
        return 0
    return int(df["T_limit_hit"].astype(str).str.lower().isin(["true", "1"]).sum())


def _safe_mean(s: pd.Series) -> Optional[float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    return round(float(s.mean()), 4)


def _safe_median(s: pd.Series) -> Optional[float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    return round(float(s.median()), 4)


def _safe_max(s: pd.Series) -> Optional[float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    return round(float(s.max()), 4)


def _safe_min(s: pd.Series) -> Optional[float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    return round(float(s.min()), 4)


def _rate(count: int, total: int) -> Optional[float]:
    return round(count / total * 100.0, 4) if total else None


def summarize(history: pd.DataFrame) -> Dict:
    """核心摘要：只围绕预测后的真实涨跌比率与幅度。"""
    verified = _verified(history)
    raw_samples = int(len(history)) if history is not None and not history.empty else 0
    total = int(len(verified))
    base = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": SOURCE_LABEL,
        "raw_samples": raw_samples,
        "verified_samples": total,
        "unverified_samples": max(raw_samples - total, 0),
    }
    if total == 0:
        base.update(
            {
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
                "avg_gain_pct": None,
                "avg_loss_pct": None,
                "gain_loss_ratio": None,
                "conclusion": "暂无已验证样本；请优先检查 T 日行情是否存在。",
            }
        )
        return base

    ret = _ret_series(verified)
    up_mask = ret > 0.05
    down_mask = ret < -0.05
    flat_mask = (ret >= -0.05) & (ret <= 0.05)
    up_count = int(up_mask.sum())
    down_count = int(down_mask.sum())
    flat_count = int(flat_mask.sum())
    limit_up_count = _limit_up_count(verified)
    avg_gain = _safe_mean(ret[ret > 0.05])
    avg_loss = _safe_mean(ret[ret < -0.05])
    if avg_gain is not None and avg_loss is not None and avg_loss != 0:
        gain_loss_ratio = round(abs(avg_gain / avg_loss), 4)
    else:
        gain_loss_ratio = None

    base.update(
        {
            "up_count": up_count,
            "up_rate": _rate(up_count, total),
            "limit_up_count": limit_up_count,
            "limit_up_rate": _rate(limit_up_count, total),
            "down_count": down_count,
            "down_rate": _rate(down_count, total),
            "flat_count": flat_count,
            "flat_rate": _rate(flat_count, total),
            "mean_return_pct": _safe_mean(ret),
            "median_return_pct": _safe_median(ret),
            "max_return_pct": _safe_max(ret),
            "min_return_pct": _safe_min(ret),
            "avg_gain_pct": avg_gain,
            "avg_loss_pct": avg_loss,
            "gain_loss_ratio": gain_loss_ratio,
            "conclusion": "已生成基于 docs/weights 最终排序的后验验证统计；核心看真实上涨率、涨停率、平均/中位涨跌幅、最大涨跌幅。",
        }
    )
    return base


def fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    try:
        if isinstance(v, float) and math.isnan(v):
            return "N/A"
        return f"{float(v):.2f}%"
    except Exception:
        return str(v)


def fmt_num(v) -> str:
    if v is None:
        return "N/A"
    try:
        if isinstance(v, float) and math.isnan(v):
            return "N/A"
        return f"{float(v):.4f}"
    except Exception:
        return str(v)


def metrics_row(label: str, g: pd.DataFrame) -> Dict:
    n = len(g)
    ret = _ret_series(g)
    up = int((ret > 0.05).sum()) if n else 0
    down = int((ret < -0.05).sum()) if n else 0
    limit_up = _limit_up_count(g) if n else 0
    return {
        "分组": label,
        "样本数": n,
        "上涨数": up,
        "上涨率": _rate(up, n),
        "下跌数": down,
        "下跌率": _rate(down, n),
        "涨停数": limit_up,
        "涨停率": _rate(limit_up, n),
        "平均涨跌幅": _safe_mean(ret),
        "中位涨跌幅": _safe_median(ret),
        "最大涨幅": _safe_max(ret),
        "最大跌幅": _safe_min(ret),
    }


def group_by_date(history: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "D_trade_date", "T_trade_date", "原始TopN数量", "已验证数量", "未验证数量",
        "上涨数", "上涨率", "下跌数", "下跌率", "涨停数", "涨停率",
        "平均涨跌幅", "中位涨跌幅", "最大涨幅", "最大跌幅",
    ]
    if history is None or history.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for (d, t), g_all in history.groupby(["D_trade_date", "T_trade_date"], dropna=False):
        g = _verified(g_all)
        n_all = len(g_all)
        n = len(g)
        ret = _ret_series(g)
        up = int((ret > 0.05).sum()) if n else 0
        down = int((ret < -0.05).sum()) if n else 0
        limit_up = _limit_up_count(g) if n else 0
        rows.append(
            {
                "D_trade_date": d,
                "T_trade_date": t,
                "原始TopN数量": n_all,
                "已验证数量": n,
                "未验证数量": max(n_all - n, 0),
                "上涨数": up,
                "上涨率": _rate(up, n),
                "下跌数": down,
                "下跌率": _rate(down, n),
                "涨停数": limit_up,
                "涨停率": _rate(limit_up, n),
                "平均涨跌幅": _safe_mean(ret),
                "中位涨跌幅": _safe_median(ret),
                "最大涨幅": _safe_max(ret),
                "最大跌幅": _safe_min(ret),
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(["D_trade_date", "T_trade_date"])


def group_by_rank(history: pd.DataFrame) -> pd.DataFrame:
    verified = _verified(history)
    cols = ["分组", "样本数", "上涨数", "上涨率", "下跌数", "下跌率", "涨停数", "涨停率", "平均涨跌幅", "中位涨跌幅", "最大涨幅", "最大跌幅"]
    if verified.empty or "TopN_rank" not in verified.columns:
        return pd.DataFrame(columns=cols)
    verified["rank_num"] = pd.to_numeric(verified["TopN_rank"], errors="coerce")
    buckets = [
        ("Top1", lambda x: x == 1),
        ("Top1-2", lambda x: (x >= 1) & (x <= 2)),
        ("Top1-4", lambda x: (x >= 1) & (x <= 4)),
        ("Top1-10", lambda x: (x >= 1) & (x <= 10)),
        ("Top6-10", lambda x: (x >= 6) & (x <= 10)),
    ]
    return pd.DataFrame([metrics_row(label, verified[func(verified["rank_num"])]) for label, func in buckets], columns=cols)


def group_by_source_rank(history: pd.DataFrame) -> pd.DataFrame:
    verified = _verified(history)
    cols = ["分组", "样本数", "上涨数", "上涨率", "下跌数", "下跌率", "涨停数", "涨停率", "平均涨跌幅", "中位涨跌幅", "最大涨幅", "最大跌幅"]
    if verified.empty or "source_rank_type" not in verified.columns:
        return pd.DataFrame(columns=cols)
    rows = []
    for label, g in verified.groupby("source_rank_type", dropna=False):
        rows.append(metrics_row(str(label), g))
    return pd.DataFrame(rows, columns=cols)


def group_by_return_bucket(history: pd.DataFrame) -> pd.DataFrame:
    verified = _verified(history)
    cols = ["真实涨跌幅区间", "样本数", "占比"]
    if verified.empty:
        return pd.DataFrame(columns=cols)
    ret = _ret_series(verified)
    buckets = [
        ("≤ -10%", ret <= -10),
        ("-10% ~ -5%", (ret > -10) & (ret <= -5)),
        ("-5% ~ 0%", (ret > -5) & (ret < -0.05)),
        ("平盘附近", (ret >= -0.05) & (ret <= 0.05)),
        ("0% ~ 3%", (ret > 0.05) & (ret <= 3)),
        ("3% ~ 7%", (ret > 3) & (ret <= 7)),
        ("7% ~ 10%", (ret > 7) & (ret < 9.85)),
        ("≥ 9.85% / 涨停附近", ret >= 9.85),
    ]
    total = len(ret.dropna())
    rows = []
    for label, mask in buckets:
        n = int(mask.sum())
        rows.append({"真实涨跌幅区间": label, "样本数": n, "占比": _rate(n, total)})
    return pd.DataFrame(rows, columns=cols)


def group_by_ev_risk(history: pd.DataFrame) -> pd.DataFrame:
    verified = _verified(history)
    cols = ["组合", "EV范围", "RiskPenalty范围", "样本数", "上涨率", "涨停率", "平均涨跌幅", "中位涨跌幅", "结论"]
    if verified.empty:
        return pd.DataFrame(columns=cols)

    ev_col = find_first_col(verified, EV_COLUMNS)
    risk_col = find_first_col(verified, RISK_COLUMNS)
    if not ev_col:
        return pd.DataFrame(columns=cols)

    verified["_ev_num"] = pd.to_numeric(verified[ev_col], errors="coerce")
    if risk_col:
        verified["_risk_num"] = pd.to_numeric(verified[risk_col], errors="coerce")
    else:
        verified["_risk_num"] = 0.0
    verified = verified[verified["_ev_num"].notna() & verified["_risk_num"].notna()].copy()
    if verified.empty:
        return pd.DataFrame(columns=cols)

    ev_q1 = float(verified["_ev_num"].quantile(1 / 3))
    ev_q2 = float(verified["_ev_num"].quantile(2 / 3))
    risk_q1 = float(verified["_risk_num"].quantile(1 / 3))
    risk_q2 = float(verified["_risk_num"].quantile(2 / 3))

    def bucket(v: float, q1: float, q2: float, prefix: str) -> str:
        if v <= q1:
            return f"低{prefix}"
        if v <= q2:
            return f"中{prefix}"
        return f"高{prefix}"

    def range_label(group: str, q1: float, q2: float) -> str:
        if group.startswith("低"):
            return f"≤ {q1:.6g}"
        if group.startswith("中"):
            return f"{q1:.6g} ~ {q2:.6g}"
        return f"> {q2:.6g}"

    verified["_ev_group"] = verified["_ev_num"].map(lambda v: bucket(v, ev_q1, ev_q2, "EV"))
    verified["_risk_group"] = verified["_risk_num"].map(lambda v: bucket(v, risk_q1, risk_q2, "Risk"))

    conclusion_map = {
        "高EV + 低Risk": "优先观察：理论收益强且风险低",
        "高EV + 中Risk": "可观察：收益强但需看承接",
        "高EV + 高Risk": "高波动：可能大涨也可能回撤",
        "中EV + 低Risk": "稳健但弹性一般",
        "中EV + 中Risk": "中性",
        "中EV + 高Risk": "风险偏高",
        "低EV + 低Risk": "防守但收益弱",
        "低EV + 中Risk": "弱解释力",
        "低EV + 高Risk": "应弱化或剔除",
    }

    rows = []
    for eg in ["高EV", "中EV", "低EV"]:
        for rg in ["低Risk", "中Risk", "高Risk"]:
            g = verified[(verified["_ev_group"] == eg) & (verified["_risk_group"] == rg)]
            n = len(g)
            ret = _ret_series(g) if n else pd.Series(dtype=float)
            up = int((ret > 0.05).sum()) if n else 0
            limit_up = _limit_up_count(g) if n else 0
            combo = f"{eg} + {rg}"
            rows.append(
                {
                    "组合": combo,
                    "EV范围": range_label(eg, ev_q1, ev_q2),
                    "RiskPenalty范围": range_label(rg, risk_q1, risk_q2),
                    "样本数": n,
                    "上涨率": _rate(up, n),
                    "涨停率": _rate(limit_up, n),
                    "平均涨跌幅": _safe_mean(ret),
                    "中位涨跌幅": _safe_median(ret),
                    "结论": conclusion_map.get(combo, ""),
                }
            )
    return pd.DataFrame(rows, columns=cols)


def correlation_summary(history: pd.DataFrame) -> Dict:
    verified = _verified(history)
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
    if not ev_col:
        out["sample_note"] = "缺 EV/ev_pred 字段"
        return out

    verified["_ev_num"] = pd.to_numeric(verified[ev_col], errors="coerce")
    if risk_col:
        verified["_risk_num"] = pd.to_numeric(verified[risk_col], errors="coerce")
    else:
        verified["_risk_num"] = pd.NA
    verified["_ret_num"] = pd.to_numeric(verified["T_return_pct"], errors="coerce")
    verified["_up_flag"] = (verified["_ret_num"] > 0.05).astype(int)
    verified["_down_flag"] = (verified["_ret_num"] < -0.05).astype(int)

    use_ev = verified[["_ev_num", "_ret_num", "_up_flag"]].dropna()
    use_risk = verified[["_risk_num", "_ret_num", "_down_flag"]].dropna()

    def corr(df: pd.DataFrame, a: str, b: str) -> Optional[float]:
        try:
            if len(df) < 3:
                return None
            v = df[a].corr(df[b])
            if pd.isna(v):
                return None
            return round(float(v), 6)
        except Exception:
            return None

    out["corr_EV_return"] = corr(use_ev, "_ev_num", "_ret_num")
    out["corr_EV_up"] = corr(use_ev, "_ev_num", "_up_flag")
    out["corr_RiskPenalty_return"] = corr(use_risk, "_risk_num", "_ret_num")
    out["corr_RiskPenalty_down"] = corr(use_risk, "_risk_num", "_down_flag")

    n = len(use_ev)
    if n < 30:
        out["sample_note"] = "样本<30，只展示，不下结论"
    elif n < 100:
        out["sample_note"] = "样本30-99，初步趋势"
    elif n < 300:
        out["sample_note"] = "样本100-299，可阶段性评估"
    else:
        out["sample_note"] = "样本>=300，可作为较稳定评估依据"
    if not risk_col:
        out["sample_note"] += "；RiskPenalty 需从 candidates 补充，若为空则只验证 EV"
    return out


def write_summary_md(summary: Dict, corr: Dict) -> None:
    lines = [
        "# top10-decision 最终 weights Top10 后验验证摘要",
        "",
        f"- 数据源：{summary.get('source', '')}",
        f"- 生成时间：{summary.get('generated_at', '')}",
        f"- 原始预测样本数：{summary.get('raw_samples', 0)}",
        f"- 已验证样本数：{summary.get('verified_samples', 0)}",
        f"- 未验证样本数：{summary.get('unverified_samples', 0)}",
        f"- 上涨数量：{summary.get('up_count', 0)}",
        f"- 上涨率：{fmt_pct(summary.get('up_rate'))}",
        f"- 涨停数量：{summary.get('limit_up_count', 0)}",
        f"- 涨停率：{fmt_pct(summary.get('limit_up_rate'))}",
        f"- 下跌数量：{summary.get('down_count', 0)}",
        f"- 下跌率：{fmt_pct(summary.get('down_rate'))}",
        f"- 平均涨跌幅：{fmt_pct(summary.get('mean_return_pct'))}",
        f"- 中位涨跌幅：{fmt_pct(summary.get('median_return_pct'))}",
        f"- 平均上涨幅度：{fmt_pct(summary.get('avg_gain_pct'))}",
        f"- 平均下跌幅度：{fmt_pct(summary.get('avg_loss_pct'))}",
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


def _format_cell(col: str, val) -> str:
    if pd.isna(val):
        return ""
    pct_cols = {"上涨率", "涨停率", "下跌率", "平盘率", "平均涨跌幅", "中位涨跌幅", "最大涨幅", "最大跌幅", "占比"}
    if col in pct_cols or col == "T_return_pct":
        try:
            f = float(val)
            sign = "+" if col == "T_return_pct" and f > 0 else ""
            return f"{sign}{f:.2f}%"
        except Exception:
            return html.escape(str(val))
    if col in {"D_close", "T_close", "weight", "ev_pred", "ev_final", "risk_penalty", "RiskPenalty", "source_rank_value"}:
        try:
            return f"{float(val):.4f}"
        except Exception:
            return html.escape(str(val))
    return html.escape(str(val))


def df_to_html(df: pd.DataFrame, max_rows: int = 50, columns: Optional[Sequence[str]] = None) -> str:
    if df is None or df.empty:
        return "<p>暂无数据</p>"
    show = df.head(max_rows).copy()
    if columns:
        cols = [c for c in columns if c in show.columns]
        show = show[cols]
    header = "".join(f"<th>{html.escape(str(c))}</th>" for c in show.columns)
    rows = []
    for _, r in show.iterrows():
        rows.append("<tr>" + "".join(f"<td>{_format_cell(c, r[c])}</td>" for c in show.columns) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def _metric_card(label: str, value: str, cls: str = "") -> str:
    return f"<div class='card {cls}'><div class='label'>{html.escape(label)}</div><div class='value'>{html.escape(value)}</div></div>"


def render_html(
    summary: Dict,
    corr: Dict,
    latest: pd.DataFrame,
    by_date: pd.DataFrame,
    by_rank: pd.DataFrame,
    by_source_rank: pd.DataFrame,
    by_return_bucket: pd.DataFrame,
    by_ev_risk: pd.DataFrame,
    history: pd.DataFrame,
) -> None:
    latest_title = ""
    if not latest.empty and "D_trade_date_fmt" in latest.columns and "T_trade_date_fmt" in latest.columns:
        latest_title = f"D：{latest['D_trade_date_fmt'].iloc[0]} → T：{latest['T_trade_date_fmt'].iloc[0]}"

    latest_cols = [
        "TopN_rank", "source_rank_type", "source_rank_value", "target_rank", "backup_rank",
        "ts_code", "ts_code_norm", "name", "name_norm", "weight", "ev_pred",
        "D_close", "T_close", "T_return_pct", "T_result", "T_limit_hit", "validation_status", "validation_note",
    ]
    history_cols = [
        "D_trade_date", "T_trade_date", "TopN_rank", "source_rank_type", "ts_code_norm", "name_norm",
        "weight", "ev_pred", "D_close", "T_close", "T_return_pct", "T_result", "validation_status", "validation_note",
    ]

    cards = "".join(
        [
            _metric_card("已验证样本", str(summary.get("verified_samples", 0))),
            _metric_card("上涨率", fmt_pct(summary.get("up_rate")), "good"),
            _metric_card("涨停率", fmt_pct(summary.get("limit_up_rate")), "good"),
            _metric_card("下跌率", fmt_pct(summary.get("down_rate")), "bad"),
            _metric_card("平均涨跌幅", fmt_pct(summary.get("mean_return_pct"))),
            _metric_card("中位涨跌幅", fmt_pct(summary.get("median_return_pct"))),
            _metric_card("平均上涨幅度", fmt_pct(summary.get("avg_gain_pct"))),
            _metric_card("平均下跌幅度", fmt_pct(summary.get("avg_loss_pct"))),
            _metric_card("最大涨幅", fmt_pct(summary.get("max_return_pct"))),
            _metric_card("最大跌幅", fmt_pct(summary.get("min_return_pct"))),
        ]
    )

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>top10-decision 最终 weights Top10 后验验证</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #24292f; }}
h1 {{ font-size: 30px; margin-bottom: 8px; }}
h2 {{ margin-top: 30px; border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }}
.meta {{ color: #57606a; font-weight: 600; line-height: 1.7; }}
.notice {{ border-left: 5px solid #0969da; background: #ddf4ff; padding: 12px 14px; margin: 16px 0; line-height: 1.7; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ border: 1px solid #d0d7de; border-radius: 10px; padding: 14px; background: #f6f8fa; }}
.card.good {{ background:#dafbe1; }}
.card.bad {{ background:#ffebe9; }}
.card .label {{ color:#57606a; font-weight:700; }}
.card .value {{ font-size:24px; font-weight:800; margin-top:6px; }}
.table-wrap {{ overflow-x:auto; margin-top: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; white-space: nowrap; }}
th {{ background: #f6f8fa; position: sticky; top: 0; }}
.note {{ color:#57606a; line-height: 1.65; }}
.small {{ font-size: 12px; color:#57606a; }}
</style>
</head>
<body>
<h1>top10-decision 最终 weights Top10 后验验证</h1>
<p class="meta">数据源：{html.escape(str(summary.get('source', '')))}<br/>生成时间：{html.escape(str(summary.get('generated_at', '')))}；最新验证：{html.escape(latest_title)}</p>
<div class="notice">
<strong>核心验证问题：</strong>预测排序之后，真实 T 日到底上涨多少、下跌多少、涨停多少。<br/>
<strong>排序口径：</strong>先取 docs/weights/weights_YYYYMMDD.csv 的 target_rank；不足 TopN 时用 backup_rank 补足。decision_candidates 只作为 EV/RiskPenalty 补充源，不作为主排序源。
</div>

<h2>一、累计真实涨跌表现</h2>
<div class="cards">{cards}</div>
<p class="small">原始预测样本：{summary.get('raw_samples', 0)}；已验证：{summary.get('verified_samples', 0)}；未验证：{summary.get('unverified_samples', 0)}。统计分母默认使用“已验证样本”。</p>

<h2>二、真实涨跌幅分布</h2>
<p class="note">用于判断系统不只是“涨跌方向”是否有效，还要看真实收益幅度是否足够覆盖交易成本和风险。</p>
{df_to_html(by_return_bucket, max_rows=20)}

<h2>三、Top1 / Top2 / Top4 / Top10 真实有效性</h2>
<p class="note">这是后续决定只买 Top1、Top2、Top4 还是 Top10 的核心依据。</p>
{df_to_html(by_rank, max_rows=20)}

<h2>四、target_rank 与 backup_rank 分组对比</h2>
<p class="note">target_rank 代表真正目标排序；backup_rank 代表补充/备选排序，二者必须分开看。</p>
{df_to_html(by_source_rank, max_rows=20)}

<h2>五、按 D/T 日统计</h2>
{df_to_html(by_date, max_rows=120)}

<h2>六、最新一期验证明细</h2>
{df_to_html(latest, max_rows=20, columns=latest_cols)}

<h2>七、EV / RiskPenalty 解释力</h2>
<div class="cards">
  {_metric_card('corr_EV_return', str(corr.get('corr_EV_return')))}
  {_metric_card('corr_RiskPenalty_return', str(corr.get('corr_RiskPenalty_return')))}
  {_metric_card('corr_EV_up', str(corr.get('corr_EV_up')))}
  {_metric_card('corr_RiskPenalty_down', str(corr.get('corr_RiskPenalty_down')))}
</div>
<p class="note">样本说明：{html.escape(str(corr.get('sample_note', '')))}</p>
{df_to_html(by_ev_risk, max_rows=20)}

<h2>八、历史明细预览</h2>
{df_to_html(history, max_rows=100, columns=history_cols)}
</body>
</html>
"""
    HTML_PATH.write_text(html_text, encoding="utf-8")


def write_outputs(history: pd.DataFrame, topn: int) -> None:
    ensure_dirs()
    if history.empty:
        history.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
        latest = pd.DataFrame()
    else:
        history = history.sort_values(["T_trade_date", "TopN_rank"], ascending=[True, True])
        history.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
        latest_t = history["T_trade_date"].dropna().max()
        latest = history[history["T_trade_date"] == latest_t].copy()
        latest.to_csv(LATEST_CSV, index=False, encoding="utf-8-sig")

    summary = summarize(history)
    corr = correlation_summary(history)
    payload = dict(summary)
    payload.update(corr)
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_md(summary, corr)

    by_date = group_by_date(history)
    by_rank = group_by_rank(history)
    by_source_rank = group_by_source_rank(history)
    by_return_bucket = group_by_return_bucket(history)
    by_ev_risk = group_by_ev_risk(history)

    by_date.to_csv(BY_DATE_CSV, index=False, encoding="utf-8-sig")
    by_rank.to_csv(BY_RANK_CSV, index=False, encoding="utf-8-sig")
    by_ev_risk.to_csv(BY_EV_RISK_CSV, index=False, encoding="utf-8-sig")
    # 兼容旧 workflow 输出列表，不强制新增文件名；页面直接渲染分布与 target/backup 分组。
    render_html(summary, corr, latest, by_date, by_rank, by_source_rank, by_return_bucket, by_ev_risk, history)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill TopN validation from docs/weights final ranking.")
    p.add_argument("--topn", type=int, default=10)
    p.add_argument("--start-date", type=str, default=None, help="按 T/exec_date 过滤，YYYYMMDD")
    p.add_argument("--end-date", type=str, default=None, help="按 T/exec_date 过滤，YYYYMMDD")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ensure_dirs()
    topn = int(args.topn or 10)
    start_date = norm_date(args.start_date)
    end_date = norm_date(args.end_date)

    weight_files = list_weight_files()
    if not weight_files:
        print(f"[WARN] no weights files found in {WEIGHTS_DIR}")
        write_outputs(pd.DataFrame(), topn=topn)
        return 0

    market = MarketStore(discover_csv_files())
    all_rows: List[Dict] = []
    for weight_file_date, weight_path in weight_files:
        rows = build_rows_from_weights(weight_file_date, weight_path, market, topn, start_date, end_date)
        all_rows.extend(rows)

    history = pd.DataFrame(all_rows)
    write_outputs(history, topn=topn)

    verified_rows = 0
    if not history.empty and "validation_status" in history.columns:
        verified_rows = int((history["validation_status"] == "已验证").sum())
    print(f"[DONE] source={VALIDATION_SOURCE} history_rows={len(history)} verified_rows={verified_rows}")
    print(f"[DONE] outputs: {HISTORY_CSV}, {SUMMARY_JSON}, {HTML_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
