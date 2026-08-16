# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import pandas as pd


STD_SIGNAL_COLS = [
    "rank",
    "ts_code",
    "name",
    "weight",
    "EV",
    "P_fill",
    "E_ret",
    "Cost",
    "RiskPenalty",
]


def _first_value(df: pd.DataFrame, col: str) -> str:
    if df is None or df.empty or col not in df.columns:
        return ""
    s = df[col].dropna()
    return "" if s.empty else str(s.iloc[0])


def _fmt_num(x, nd: int = 6) -> str:
    try:
        if pd.isna(x):
            return ""
        return f"{float(x):.{nd}f}".rstrip("0").rstrip(".")
    except Exception:
        return "" if x is None else str(x)


def _copy_std_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    统一出报告时需要的标准字段别名，不删除原列，只补标准列。
    """
    out = df.copy()

    if "EV" not in out.columns:
        for c in ["ev_pred"]:
            if c in out.columns:
                out["EV"] = pd.to_numeric(out[c], errors="coerce")
                break

    if "RiskPenalty" not in out.columns:
        for c in ["risk_penalty", "risk_total_penalty"]:
            if c in out.columns:
                out["RiskPenalty"] = pd.to_numeric(out[c], errors="coerce")
                break

    if "P_fill" not in out.columns:
        for c in ["p_fill_pred", "p_fill_pred_final"]:
            if c in out.columns:
                out["P_fill"] = pd.to_numeric(out[c], errors="coerce")
                break

    if "E_ret" not in out.columns:
        for c in ["e_ret_pred", "eret_pred", "eret_pred_final"]:
            if c in out.columns:
                out["E_ret"] = pd.to_numeric(out[c], errors="coerce")
                break

    if "Cost" not in out.columns:
        for c in ["cost_est"]:
            if c in out.columns:
                out["Cost"] = pd.to_numeric(out[c], errors="coerce")
                break

    if "weight" not in out.columns:
        for c in ["weight_exec"]:
            if c in out.columns:
                out["weight"] = pd.to_numeric(out[c], errors="coerce")
                break

    return out


def _ensure_signal_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = _copy_std_cols(df)
    for c in STD_SIGNAL_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    return out


def _render_signal_table(df: pd.DataFrame) -> list[str]:
    """
    渲染与 TopN Targets 同结构的英文表。
    """
    d = _ensure_signal_cols(df)

    lines: list[str] = []
    lines.append("<table><tr>")
    for c in STD_SIGNAL_COLS:
        lines.append(f"<th>{c}</th>")
    lines.append("</tr>")

    if d is None or d.empty:
        lines.append("<tr><td colspan='9'></td></tr></table>\n")
        return lines

    for _, r in d.iterrows():
        lines.append("<tr>")
        lines.append(f"<td>{'' if pd.isna(r.get('rank', '')) else r.get('rank', '')}</td>")
        lines.append(f"<td>{'' if pd.isna(r.get('ts_code', '')) else r.get('ts_code', '')}</td>")
        lines.append(f"<td>{'' if pd.isna(r.get('name', '')) else r.get('name', '')}</td>")
        lines.append(f"<td>{_fmt_num(r.get('weight', ''), 6)}</td>")
        lines.append(f"<td>{_fmt_num(r.get('EV', ''), 6)}</td>")
        lines.append(f"<td>{_fmt_num(r.get('P_fill', ''), 6)}</td>")
        lines.append(f"<td>{_fmt_num(r.get('E_ret', ''), 6)}</td>")
        lines.append(f"<td>{_fmt_num(r.get('Cost', ''), 6)}</td>")
        lines.append(f"<td>{_fmt_num(r.get('RiskPenalty', ''), 6)}</td>")
        lines.append("</tr>")
    lines.append("</table>\n")
    return lines


def _build_evrp_window(df: pd.DataFrame) -> pd.DataFrame:
    """
    新增中间表窗口：
    EV > 3%
    RiskPenalty < 1%

    注意：
    这里严格保持原有候选池排序，不做重新按 EV 排序，
    只做筛选，不改变既有价值排序逻辑。
    """
    d = _ensure_signal_cols(df)

    d["EV"] = pd.to_numeric(d["EV"], errors="coerce")
    d["RiskPenalty"] = pd.to_numeric(d["RiskPenalty"], errors="coerce")

    window_df = d[(d["EV"] > 0.03) & (d["RiskPenalty"] < 0.01)].copy()
    return window_df.reset_index(drop=True)


def write_daily_report_human(
    merged_df: pd.DataFrame,
    out_path: str = "docs/reports/daily_latest.md",
    title: str = "Daily Decision Report (latest)",
) -> Path:
    """
    人类可读日报：
    - 保留旧中文简版用途
    - 新增 EV>3% & RiskPenalty<1% 中间窗口表
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    d = _ensure_signal_cols(merged_df)

    trade_date = _first_value(d, "trade_date")
    target_trade_date = _first_value(d, "target_trade_date")
    exec_date = _first_value(d, "exec_date")

    topn = d.head(10).copy()
    evrp_window = _build_evrp_window(d)

    lines: list[str] = []
    lines.append(f"# {title}\n\n")
    lines.append(f"- trade_date（信号生成日）: **{trade_date if trade_date else '未知'}**\n")
    lines.append(f"- target_trade_date（执行交易日）: **{target_trade_date if target_trade_date else '未知/未填'}**\n")
    lines.append(f"- exec_date（报告执行日）: **{exec_date if exec_date else '未知/未填'}**\n\n")

    lines.append("## EV>3% & RiskPenalty<1%\n\n")
    lines.extend(_render_signal_table(evrp_window))
    lines.append("\n")

    lines.append("## TopN Targets\n\n")
    lines.extend(_render_signal_table(topn))
    lines.append("\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
