#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from top10decision.writers.io_contract import norm_ymd, REPORT_FMT, EVAL_FMT, fmt_num


INTRADAY_INPUT_COLS = [
    "intraday_available",
    "intraday_status",
    "intraday_quality_score",
    "intraday_soft_risk_score",
    "intraday_hard_risk_flag",
    "intraday_risk_score",
    "late_withdraw_score",
    "reseal_score",
    "open_board_count",
    "auction_strength_score",
    "intraday_confidence_score",
]

INTRADAY_PENALTY_COLS = [
    "risk_intraday_hard_penalty",
    "risk_intraday_soft_penalty",
    "risk_intraday_confidence_penalty",
    "risk_intraday_missing_penalty",
    "risk_late_withdraw_penalty",
    "risk_reseal_weakness_penalty",
    "risk_auction_weakness_penalty",
    "intraday_execution_penalty",
]


def _read_csv_any(path: Path) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    return pd.DataFrame()


def _truthy_series(s: pd.Series) -> pd.Series:
    text = s.astype(str).str.strip().str.lower()
    return text.isin({"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"})


def _safe_numeric_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(default).astype(float)
    return pd.Series(default, index=df.index, dtype=float)


def _json_float(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        return 0.0
    if pd.isna(x):
        return 0.0
    return x


def _candidate_path_from_report(report_md: str) -> Path | None:
    if not report_md:
        return None
    m = re.search(r"candidates_snapshot:\s*`([^`]+)`", report_md)
    if not m:
        return None
    return Path(m.group(1).strip())


def _build_intraday_risk_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {
            "fields_present": False,
            "rows": 0,
            "available_rows": 0,
            "hard_risk_rows": 0,
            "intraday_ev_bonus_mean": 0.0,
            "intraday_penalty_extra_mean": 0.0,
            "intraday_execution_penalty_mean": 0.0,
            "status_counts": {},
            "top_penalty_rows": [],
        }

    cols_present = [c for c in INTRADAY_INPUT_COLS if c in df.columns]
    out: dict[str, Any] = {
        "fields_present": bool(cols_present),
        "present_columns": cols_present,
        "rows": int(len(df)),
    }

    available = _truthy_series(df["intraday_available"]) if "intraday_available" in df.columns else pd.Series(False, index=df.index)
    out["available_rows"] = int(available.sum())

    if "intraday_status" in df.columns:
        status = df["intraday_status"].fillna("").astype(str).str.strip()
        out["status_counts"] = {str(k): int(v) for k, v in status.value_counts(dropna=False).head(12).items()}
    else:
        out["status_counts"] = {}

    out["hard_risk_rows"] = int(_truthy_series(df["intraday_hard_risk_flag"]).sum()) if "intraday_hard_risk_flag" in df.columns else 0

    for col in INTRADAY_PENALTY_COLS + ["intraday_ev_bonus", "intraday_penalty_extra"]:
        s = _safe_numeric_col(df, col, 0.0)
        out[f"{col}_mean"] = float(s.mean()) if len(s) else 0.0
        out[f"{col}_max"] = float(s.max()) if len(s) else 0.0
        out[f"{col}_sum"] = float(s.sum()) if len(s) else 0.0

    if "intraday_execution_penalty" in df.columns:
        top = df.copy()
        top["_intraday_penalty_sort"] = _safe_numeric_col(top, "intraday_execution_penalty", 0.0)
        top = top.sort_values(
            by=["_intraday_penalty_sort", "ts_code"],
            ascending=[False, True],
            kind="mergesort",
        ).head(10)
        out["top_penalty_rows"] = [
            {
                "ts_code": str(r.get("ts_code", "")),
                "name": str(r.get("name", "")),
                "intraday_status": str(r.get("intraday_status", "")),
                "intraday_execution_penalty": _json_float(r.get("intraday_execution_penalty", 0.0)),
                "intraday_penalty_extra": _json_float(r.get("intraday_penalty_extra", 0.0)),
                "intraday_ev_bonus": _json_float(r.get("intraday_ev_bonus", 0.0)),
                "ev_pred": _json_float(r.get("ev_pred", 0.0)),
            }
            for _, r in top.iterrows()
        ]
    else:
        out["top_penalty_rows"] = []

    return out


def _build_intraday_risk_summary_from_path(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _build_intraday_risk_summary(pd.DataFrame())
    return _build_intraday_risk_summary(_read_csv_any(path))


def _intraday_report_section(stats: dict[str, Any]) -> str:
    if not stats or not stats.get("fields_present", False):
        return ""
    return (
        "\n## Intraday Risk Status\n\n"
        f"- fields_present: **{stats.get('fields_present', False)}**\n"
        f"- available_rows: **{stats.get('available_rows', 0)}** / **{stats.get('rows', 0)}**\n"
        f"- hard_risk_rows: **{stats.get('hard_risk_rows', 0)}**\n"
        f"- intraday_ev_bonus_mean: **{fmt_num(stats.get('intraday_ev_bonus_mean', 0.0), 6)}**\n"
        f"- intraday_penalty_extra_mean: **{fmt_num(stats.get('intraday_penalty_extra_mean', 0.0), 6)}**\n"
        f"- intraday_execution_penalty_mean: **{fmt_num(stats.get('intraday_execution_penalty_mean', 0.0), 6)}**\n"
    )


def _augment_eval_payload(payload: dict) -> dict:
    out = dict(payload or {})
    path_value = ((out.get("paths") or {}).get("candidates") or "")
    stats = _build_intraday_risk_summary_from_path(Path(path_value) if path_value else None)
    if stats.get("fields_present", False):
        out.setdefault("intraday_risk", stats)
        out.setdefault("intraday_penalty_extra_mean", float(stats.get("intraday_penalty_extra_mean", 0.0)))
        out.setdefault("intraday_ev_bonus_mean", float(stats.get("intraday_ev_bonus_mean", 0.0)))
        out.setdefault("intraday_execution_penalty_mean", float(stats.get("intraday_execution_penalty_mean", 0.0)))
    return out


def write_decision_report(exec_date: str, report_md: str) -> str:
    Path("outputs/decision").mkdir(parents=True, exist_ok=True)
    ed = norm_ymd(exec_date) or "unknown"
    path = REPORT_FMT.format(yyyymmdd=ed)
    out_md = report_md or ""
    if "## Intraday Risk Status" not in out_md:
        stats = _build_intraday_risk_summary_from_path(_candidate_path_from_report(out_md))
        out_md = out_md + _intraday_report_section(stats)
    Path(path).write_text(out_md, encoding="utf-8")
    return path


def write_eval_json(exec_date: str, payload: dict) -> str:
    Path("outputs/decision").mkdir(parents=True, exist_ok=True)
    ed = norm_ymd(exec_date) or "unknown"
    path = EVAL_FMT.format(yyyymmdd=ed)
    out_payload = _augment_eval_payload(payload)
    Path(path).write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
