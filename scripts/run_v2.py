#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
top10-decision — V2 runner (Orchestrator Only)

硬规则符合性：
1) 数据入口只允许一个：src/top10decision/ingest.py（本文件不读 URL/不读旧文件）
2) sync 独立：scripts/sync_pred_source.py（本文件不做跨仓库拉取）
3) adapters 仅字段映射：src/top10decision/adapters/decisio_adapter.py
4) models / engines 只算分数/概率：src/top10decision/models/*, src/top10decision/engines/*
5) 写文件只在 writers：src/top10decision/writers/*
6) run_v2.py 只编排：不再包含业务细节函数

本次升级：
- 优先使用 ingest.build_model_input() 读取 pred + FS 的统一输入
- 若 FS 缺失，则自动降级回 pred_only
- P_fill / E_ret 不再直接写死规则函数，升级为优先 engine 推理、失败自动回退规则
- 将输入层状态与 engine 审计状态写入 report / eval，便于验收
- 新增手动 trade_date 契约：支持 --trade-date / TRADE_DATE，并对输入表做显式过滤
- 接入 Cost / RiskPenalty 分项归因输出，落入 decision_candidates 便于业务审查
- 在 Decision 报告中追加 Full Candidate Pool（全候选池精简展示表）
- 新增 Artifacts 下方的高 EV / 低 RiskPenalty 筛选表
- TopN Targets / Full Candidate Pool 均按 EV 降序展示
- 新增 TopEVR 信号文件输出（latest + dated，可空表）
- V1 聚合升级：增强 P_fill / RiskPenalty 在 EV 中的话语权，并输出 EV 审计字段
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
from pathlib import Path
from typing import Any, Callable, List

import pandas as pd

from top10decision.ingest import (
    build_model_input,
    get_input_status,
    load_pred_snapshot,
)
from top10decision.decision.eligibility import filter_standard_limit_universe
from top10decision.regime.simple_regime import simple_regime
from top10decision.risk.guardrails import guardrails
from top10decision.strategies.score_router import score_router

from top10decision.models.fill_model import fill_model_rule
from top10decision.models.overnight_model import overnight_model_rule
from top10decision.models.costs import (
    cost_estimate_rule,
    risk_penalty_rule,
    cost_breakdown_df,
    risk_breakdown_df,
)

from top10decision.weights.engine import WeightCaps, build_weights_with_backups

from top10decision.writers.filesystem import (
    ensure_dirs,
    ensure_execution_table,
    ensure_learning_table,
)
from top10decision.writers.artifacts import (
    write_candidates_snapshot,
    write_weights,
    write_signals,
    write_top_evr_signals,
    build_signal_df_for_joinquant,
    build_top_evr_signal_df,
)
from top10decision.writers.reports import write_decision_report, write_eval_json
from top10decision.writers.io_contract import (
    TOPK_DEFAULT,
    TOPN_DEFAULT,
    W_MAX_DEFAULT,
    THEME_CAP_DEFAULT,
    GROSS_CAP_DEFAULT,
)
from top10decision.writers.io_contract import (
    norm_ymd,
    get_first_value,
    choose_exec_date,
    choose_exit_date,
    fmt_num,
)


WEIGHT_OUTPUT_COLUMNS = [
    "exec_date",
    "ts_code",
    "name",
    "weight",
    "target_rank",
    "backup_rank",
    "ev_pred",
]
MISSING_LEARNING_ACCEPTANCE_REASON = (
    "model_rejected_by_learning_acceptance:acceptance_file_missing"
)
MISSING_LEARNING_ACCEPTANCE_NO_TRADE = (
    "NO_TRADE_MISSING_LEARNING_ACCEPTANCE"
)
MODEL_EXECUTION_NOT_AUTHORIZED_NO_TRADE = (
    "NO_TRADE_MODEL_EXECUTION_NOT_AUTHORIZED"
)


def _strict_uniform_bool_column(df: pd.DataFrame, column: str) -> bool | None:
    """Return one native bool only for a complete uniform boolean column."""

    if df is None or df.empty or column not in df.columns:
        return None
    values = df[column]
    if not pd.api.types.is_bool_dtype(values.dtype) or values.isna().any():
        return None
    unique = values.astype(bool).unique().tolist()
    return bool(unique[0]) if len(unique) == 1 else None


def _strict_uniform_text_column(df: pd.DataFrame, column: str) -> str | None:
    """Return text only when every row carries the same native string."""

    if df is None or df.empty or column not in df.columns:
        return None
    values = df[column]
    if not values.map(lambda value: type(value) is str).all():
        return None
    unique = values.unique().tolist()
    return str(unique[0]) if len(unique) == 1 else None


def _requires_missing_acceptance_no_trade(
    pfill_audit: dict[str, Any],
    eret_audit: dict[str, Any],
) -> bool:
    """Match only the exact frozen missing-acceptance degradation."""

    return bool(
        pfill_audit.get("p_fill_model_loaded") is False
        and pfill_audit.get("p_fill_model_acceptance_pass") is False
        and pfill_audit.get("p_fill_degrade_reason")
        == MISSING_LEARNING_ACCEPTANCE_REASON
        and eret_audit.get("eret_model_loaded") is False
        and eret_audit.get("eret_model_acceptance_pass") is False
        and eret_audit.get("eret_degrade_reason")
        == MISSING_LEARNING_ACCEPTANCE_REASON
    )


def _execution_gate_reason(
    pfill_audit: dict[str, Any],
    eret_audit: dict[str, Any],
) -> str:
    """Authorize execution only for two accepted, loaded model predictions."""

    if _requires_missing_acceptance_no_trade(pfill_audit, eret_audit):
        return MISSING_LEARNING_ACCEPTANCE_NO_TRADE

    pfill_authorized = bool(
        pfill_audit.get("p_fill_model_loaded") is True
        and pfill_audit.get("p_fill_model_acceptance_pass") is True
        and pfill_audit.get("p_fill_pred_src") in {"model:lgbm", "model:lr"}
        and pfill_audit.get("p_fill_degrade_reason") == ""
    )
    eret_authorized = bool(
        eret_audit.get("eret_model_loaded") is True
        and eret_audit.get("eret_model_acceptance_pass") is True
        and eret_audit.get("eret_pred_src") in {"model:lgbm", "model:lr"}
        and eret_audit.get("eret_degrade_reason") == ""
    )
    if pfill_authorized and eret_authorized:
        return ""
    return MODEL_EXECUTION_NOT_AUTHORIZED_NO_TRADE


def _build_execution_weights(
    routed_df: pd.DataFrame,
    *,
    caps: WeightCaps,
    exec_date: str,
    pfill_audit: dict[str, Any],
    eret_audit: dict[str, Any],
) -> tuple[pd.DataFrame, str]:
    """Build weights, or close the executable surface for unaccepted models."""

    targets, backups = build_weights_with_backups(
        routed_df,
        topn=TOPN_DEFAULT,
        caps=caps,
    )
    weights_out = pd.concat([targets, backups], ignore_index=True)
    weights_out["exec_date"] = norm_ymd(exec_date)

    execution_gate = _execution_gate_reason(pfill_audit, eret_audit)
    if execution_gate:
        # Keep the complete ranked observation pool so exec_date remains
        # independently inferable, but close every executable weight.  Rows
        # become deterministic backups only; no target rank survives.
        weights_out["weight"] = 0.0
        weights_out["target_rank"] = ""
        weights_out = weights_out.sort_values(
            by=["ev_pred", "ts_code"],
            ascending=[False, True],
            kind="mergesort",
        ).reset_index(drop=True)
        weights_out["ev_pred"] = 0.0
        weights_out["backup_rank"] = list(range(1, len(weights_out) + 1))
        return (
            weights_out[WEIGHT_OUTPUT_COLUMNS].copy(),
            execution_gate,
        )

    return weights_out[WEIGHT_OUTPUT_COLUMNS].copy(), ""


def _ensure_required_cols(df: pd.DataFrame, required_cols: list[str]) -> None:
    if df is None or df.empty:
        raise RuntimeError("输入数据为空，无法继续运行。")
    for c in required_cols:
        if c not in df.columns:
            raise RuntimeError(f"缺少必要字段 {c}，请检查 ingest / adapter / pred_source_latest 输入链路。")


def _safe_first_value(df: pd.DataFrame, col: str, fallback: Any = "") -> Any:
    try:
        v = get_first_value(df, col)
        if v is None:
            return fallback
        if isinstance(v, str) and v.strip() == "":
            return fallback
        return v
    except Exception:
        return fallback


def _normalize_trade_date_str(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s == "":
        return ""
    s = re.sub(r"\.0$", "", s)
    s = re.sub(r"[^0-9]", "", s)
    return s


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run top10-decision V2 orchestrator.")
    parser.add_argument(
        "--trade-date",
        default="",
        help="手动指定 Decision 输入 trade_date（YYYYMMDD）。留空=自动从输入表推断主日期。",
    )
    return parser.parse_args()


def _resolve_requested_trade_date(args: argparse.Namespace) -> str:
    cli_td = _normalize_trade_date_str(getattr(args, "trade_date", ""))
    env_td = _normalize_trade_date_str(os.environ.get("TRADE_DATE", ""))

    requested = cli_td or env_td
    if requested and not re.fullmatch(r"\d{8}", requested):
        raise RuntimeError(f"非法 trade_date: {requested}，期望 YYYYMMDD。")
    return requested


def _build_input_bundle() -> tuple[pd.DataFrame, dict[str, Any], str, str]:
    """
    返回：
    - input_df: 本轮实际使用的输入表
    - input_status: ingest 层状态
    - input_mode: pred_plus_fs / pred_only
    - fs_degrade_reason: 降级原因
    """
    input_status = get_input_status()
    merged_df = build_model_input(include_limit=True, include_truth=False)

    fs_base_loaded = bool(input_status.get("features_base_loaded", False))
    fs_limit_loaded = bool(input_status.get("features_limit_loaded", False))

    if merged_df is not None and not merged_df.empty and fs_base_loaded:
        input_mode = "pred_plus_fs"
        fs_degrade_reason = ""
        input_df = merged_df.copy()
    else:
        input_df = load_pred_snapshot()
        input_mode = "pred_only"
        missing_parts = []
        if not fs_base_loaded:
            missing_parts.append("features_base_missing")
        if not fs_limit_loaded:
            missing_parts.append("features_limit_missing")
        fs_degrade_reason = ",".join(missing_parts) if missing_parts else "merged_input_unavailable"

    if input_df is None or input_df.empty:
        raise RuntimeError("ingest 返回空数据：pred / FS 输入均为空或不可读。")

    return input_df, input_status, input_mode, fs_degrade_reason


def _prepare_runtime_input(
    input_df: pd.DataFrame,
    requested_trade_date: str = "",
) -> pd.DataFrame:
    """
    运行前最小清洗：
    - 保障必要字段
    - 若指定 requested_trade_date，则强制按该日期过滤
    - 否则按输入表内主日期（mode/max）过滤
    """
    _ensure_required_cols(input_df, ["ts_code", "name"])

    out = input_df.copy()

    if "trade_date" not in out.columns:
        if requested_trade_date:
            raise RuntimeError(
                "输入表缺少 trade_date 字段，无法按手动指定 trade_date 过滤。"
            )
        return out

    td_series = (
        out["trade_date"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    out["_trade_date_norm_tmp"] = td_series
    available_trade_dates = sorted(
        {
            _normalize_trade_date_str(v)
            for v in out["_trade_date_norm_tmp"].dropna().tolist()
            if _normalize_trade_date_str(v)
        }
    )

    if requested_trade_date:
        filtered = out.loc[
            out["_trade_date_norm_tmp"].map(_normalize_trade_date_str) == requested_trade_date
        ].copy()
        if filtered.empty:
            available_preview = ",".join(available_trade_dates[:20]) if available_trade_dates else "none"
            raise RuntimeError(
                f"手动指定 trade_date={requested_trade_date}，但输入表中无匹配行。"
                f" available_trade_dates={available_preview}"
            )
        out = filtered
    else:
        td_vals = out["_trade_date_norm_tmp"].dropna().astype(str).str.strip()
        td_vals = td_vals[td_vals != ""]
        if not td_vals.empty:
            mode_vals = td_vals.mode()
            trade_date = str(mode_vals.iloc[0]) if len(mode_vals) > 0 else str(td_vals.max())
            filtered = out.loc[out["_trade_date_norm_tmp"] == trade_date].copy()
            if not filtered.empty:
                out = filtered

    out = out.drop(columns=["_trade_date_norm_tmp"], errors="ignore")
    return out


def _load_engine_apply_func(
    engine_file_name: str,
    func_name: str,
) -> Callable[..., pd.DataFrame] | None:
    """
    先尝试常规 import。
    若 engines/ 目录尚未放 __init__.py，允许退化为按文件路径加载。
    """
    try:
        if engine_file_name == "pfill_engine.py" and func_name == "apply_pfill_engine":
            from top10decision.engines.pfill_engine import apply_pfill_engine  # type: ignore
            return apply_pfill_engine
        if engine_file_name == "eret_engine.py" and func_name == "apply_eret_engine":
            from top10decision.engines.eret_engine import apply_eret_engine  # type: ignore
            return apply_eret_engine
    except Exception:
        pass

    try:
        root = Path(__file__).resolve().parent.parent
        engine_path = root / "src" / "top10decision" / "engines" / engine_file_name
        if not engine_path.exists():
            return None

        spec = importlib.util.spec_from_file_location(
            f"_dynamic_{engine_file_name.replace('.py', '')}",
            engine_path,
        )
        if spec is None or spec.loader is None:
            return None

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, func_name, None)
    except Exception:
        return None


def _run_pfill_engine(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    apply_func = _load_engine_apply_func("pfill_engine.py", "apply_pfill_engine")
    out = df.copy()

    if apply_func is None:
        out["p_fill_pred"] = fill_model_rule(out)
        out["p_fill_pred_rule"] = out["p_fill_pred"]
        out["p_fill_pred_model"] = pd.NA
        out["p_fill_pred_final"] = out["p_fill_pred"]
        out["p_fill_model_loaded"] = False
        out["p_fill_model_kind"] = ""
        out["p_fill_model_path"] = ""
        out["p_fill_model_feature_mode"] = ""
        out["p_fill_pred_src"] = "rule_direct_fallback"
        out["p_fill_degrade_reason"] = "engine_import_failed"
    else:
        out = apply_func(out)

    audit = {
        "p_fill_pred_src": _strict_uniform_text_column(out, "p_fill_pred_src"),
        "p_fill_model_loaded": _strict_uniform_bool_column(out, "p_fill_model_loaded"),
        "p_fill_model_acceptance_pass": _strict_uniform_bool_column(
            out, "p_fill_model_acceptance_pass"
        ),
        "p_fill_model_kind": str(_safe_first_value(out, "p_fill_model_kind", "")),
        "p_fill_model_path": str(_safe_first_value(out, "p_fill_model_path", "")),
        "p_fill_degrade_reason": _strict_uniform_text_column(out, "p_fill_degrade_reason"),
    }
    return out, audit


def _run_eret_engine(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    apply_func = _load_engine_apply_func("eret_engine.py", "apply_eret_engine")
    out = df.copy()

    if apply_func is None:
        out["e_ret_pred"] = overnight_model_rule(
            out,
            regime=str(_safe_first_value(out, "regime_name", "RISK_ON")),
        )
        out["eret_pred"] = out["e_ret_pred"]
        out["eret_pred_rule"] = out["e_ret_pred"]
        out["eret_pred_model"] = pd.NA
        out["eret_pred_final"] = out["e_ret_pred"]
        out["eret_model_loaded"] = False
        out["eret_model_kind"] = ""
        out["eret_model_path"] = ""
        out["eret_model_feature_mode"] = ""
        out["eret_pred_src"] = "rule_direct_fallback"
        out["eret_degrade_reason"] = "engine_import_failed"
    else:
        out = apply_func(out)

    if "e_ret_pred" not in out.columns and "eret_pred" in out.columns:
        out["e_ret_pred"] = out["eret_pred"]
    if "eret_pred" not in out.columns and "e_ret_pred" in out.columns:
        out["eret_pred"] = out["e_ret_pred"]

    audit = {
        "eret_pred_src": _strict_uniform_text_column(out, "eret_pred_src"),
        "eret_model_loaded": _strict_uniform_bool_column(out, "eret_model_loaded"),
        "eret_model_acceptance_pass": _strict_uniform_bool_column(
            out, "eret_model_acceptance_pass"
        ),
        "eret_model_kind": str(_safe_first_value(out, "eret_model_kind", "")),
        "eret_model_path": str(_safe_first_value(out, "eret_model_path", "")),
        "eret_degrade_reason": _strict_uniform_text_column(out, "eret_degrade_reason"),
    }
    return out, audit


def _coerce_float_series(
    values: Any,
    index: pd.Index,
    default: float = 0.0,
) -> pd.Series:
    """
    将标量 / Series / list 统一转成与 index 对齐的 float Series。
    目的：
    - 兼容旧版 costs.py 返回标量
    - 支持新版 costs.py 返回逐股 Series
    """
    if isinstance(values, pd.Series):
        out = pd.to_numeric(values, errors="coerce")
        out = out.reindex(index)
        return out.fillna(default).astype(float)

    if isinstance(values, (list, tuple)):
        out = pd.Series(list(values))
        out = pd.to_numeric(out, errors="coerce")
        out.index = index[: len(out)]
        out = out.reindex(index)
        return out.fillna(default).astype(float)

    try:
        scalar = float(values)
    except Exception:
        scalar = float(default)
    return pd.Series(scalar, index=index, dtype=float)


def _attach_cost_risk_columns(
    df: pd.DataFrame,
    regime_name: str,
) -> pd.DataFrame:
    """
    将 Cost / RiskPenalty 的分项归因与总值统一挂到候选表。
    兼容：
    - costs.py 提供 breakdown_df
    - 若某些列缺失，退回总值规则函数补齐
    """
    out = df.copy()

    # ---- Cost breakdown ----
    try:
        cost_parts = cost_breakdown_df(out)
    except Exception:
        cost_parts = pd.DataFrame(index=out.index)

    if cost_parts is not None and not cost_parts.empty:
        cost_parts = cost_parts.reindex(out.index)
        for c in cost_parts.columns:
            out[c] = pd.to_numeric(cost_parts[c], errors="coerce").fillna(0.0).astype(float)

    if "cost_total_bp" in out.columns:
        out["cost_est"] = pd.to_numeric(out["cost_total_bp"], errors="coerce").fillna(0.0).astype(float)
    else:
        out["cost_est"] = _coerce_float_series(
            cost_estimate_rule(out),
            out.index,
            default=0.0,
        )

    # ---- Risk breakdown ----
    try:
        risk_parts = risk_breakdown_df(regime_name, out)
    except Exception:
        risk_parts = pd.DataFrame(index=out.index)

    if risk_parts is not None and not risk_parts.empty:
        risk_parts = risk_parts.reindex(out.index)
        for c in risk_parts.columns:
            out[c] = pd.to_numeric(risk_parts[c], errors="coerce").fillna(0.0).astype(float)

    if "risk_total_penalty" in out.columns:
        out["risk_penalty"] = pd.to_numeric(out["risk_total_penalty"], errors="coerce").fillna(0.0).astype(float)
    else:
        out["risk_penalty"] = _coerce_float_series(
            risk_penalty_rule(regime_name, out),
            out.index,
            default=0.0,
        )

    return out


def _safe_numeric_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(default).astype(float)
    return pd.Series(default, index=df.index, dtype=float)


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


def _truthy_series(s: pd.Series) -> pd.Series:
    text = s.astype(str).str.strip().str.lower()
    return text.isin({"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"})


def _score_series_0_1(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    s = _safe_numeric_col(df, col, default=default)
    s = s.where(s <= 1.0, s / 100.0)
    return s.clip(lower=0.0, upper=1.0)


def _json_float(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        return 0.0
    if pd.isna(x):
        return 0.0
    return x


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


def _append_intraday_risk_summary(lines: List[str], stats: dict[str, Any]) -> None:
    lines.append("## Intraday Risk Status\n\n")
    lines.append(f"- fields_present: **{stats.get('fields_present', False)}**\n")
    lines.append(f"- available_rows: **{stats.get('available_rows', 0)}** / **{stats.get('rows', 0)}**\n")
    lines.append(f"- hard_risk_rows: **{stats.get('hard_risk_rows', 0)}**\n")
    lines.append(f"- intraday_ev_bonus_mean: **{fmt_num(stats.get('intraday_ev_bonus_mean', 0.0), 6)}**\n")
    lines.append(f"- intraday_penalty_extra_mean: **{fmt_num(stats.get('intraday_penalty_extra_mean', 0.0), 6)}**\n")
    lines.append(f"- intraday_execution_penalty_mean: **{fmt_num(stats.get('intraday_execution_penalty_mean', 0.0), 6)}**\n\n")


def _series_stats(df: pd.DataFrame, col: str) -> dict[str, float]:
    s = _safe_numeric_col(df, col, 0.0) if df is not None else pd.Series(dtype=float)
    if s.empty:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": float(s.min()),
        "max": float(s.max()),
        "mean": float(s.mean()),
    }


def _build_decision_diagnostics(
    candidates_df: pd.DataFrame,
    selected_rows: int,
    risk_budget: float,
    stop_reason: str = "",
) -> dict[str, Any]:
    """
    Report/eval only diagnostics.

    This does not alter the trading decision. It explains whether no-trade came
    from model return, fill probability, cost/risk, extra penalties, or weight
    gating.
    """
    df = pd.DataFrame() if candidates_df is None else candidates_df.copy()
    rows = int(len(df))
    diag: dict[str, Any] = {
        "rows_scored": rows,
        "selected_rows": int(selected_rows),
        "risk_budget": float(risk_budget),
        "stop_reason": str(stop_reason or ""),
    }

    for col in [
        "p_fill_pred",
        "e_ret_pred",
        "cost_est",
        "risk_penalty",
        "ev_base",
        "pfill_penalty_extra",
        "risk_penalty_extra",
        "intraday_penalty_extra",
        "intraday_ev_bonus",
        "ev_penalty_total_extra",
        "ev_final",
        "ev_pred",
    ]:
        stats = _series_stats(df, col)
        diag[f"{col}_min"] = stats["min"]
        diag[f"{col}_max"] = stats["max"]
        diag[f"{col}_mean"] = stats["mean"]

    ev = _safe_numeric_col(df, "ev_pred", 0.0) if rows else pd.Series(dtype=float)
    ev_base = _safe_numeric_col(df, "ev_base", 0.0) if rows else pd.Series(dtype=float)
    e_ret = _safe_numeric_col(df, "e_ret_pred", 0.0) if rows else pd.Series(dtype=float)
    p_fill = _safe_numeric_col(df, "p_fill_pred", 0.0) if rows else pd.Series(dtype=float)
    risk = _safe_numeric_col(df, "risk_penalty", 0.0) if rows else pd.Series(dtype=float)

    diag["positive_ev_rows"] = int((ev > 0).sum()) if rows else 0
    diag["positive_ev_base_rows"] = int((ev_base > 0).sum()) if rows else 0
    diag["positive_e_ret_rows"] = int((e_ret > 0).sum()) if rows else 0
    diag["high_pfill_rows"] = int((p_fill >= 0.90).sum()) if rows else 0
    diag["low_risk_rows"] = int((risk < 0.01).sum()) if rows else 0

    penalty_means = {
        "cost_est": float(diag.get("cost_est_mean", 0.0)),
        "risk_penalty": float(diag.get("risk_penalty_mean", 0.0)),
        "pfill_penalty_extra": float(diag.get("pfill_penalty_extra_mean", 0.0)),
        "risk_penalty_extra": float(diag.get("risk_penalty_extra_mean", 0.0)),
        "intraday_penalty_extra": float(diag.get("intraday_penalty_extra_mean", 0.0)),
    }
    diag["top_mean_penalty_drivers"] = [
        {"name": k, "mean": float(v)}
        for k, v in sorted(penalty_means.items(), key=lambda kv: kv[1], reverse=True)
        if float(v) > 0
    ][:5]

    if rows == 0:
        reason = "input_empty"
    elif stop_reason:
        reason = f"guardrail_stop:{stop_reason}"
    elif selected_rows > 0:
        reason = "selected_positive_weight"
    elif diag["positive_ev_rows"] <= 0:
        if diag["positive_e_ret_rows"] <= 0:
            reason = "no_positive_e_ret_after_model"
        elif diag["positive_ev_base_rows"] <= 0:
            reason = "positive_e_ret_cannot_cover_cost_and_risk"
        else:
            reason = "base_ev_positive_but_extra_penalties_filter"
    elif risk_budget <= 0:
        reason = "risk_budget_zero"
    else:
        reason = "weight_engine_filtered_positive_ev"
    diag["primary_no_trade_reason"] = reason

    if rows:
        top = df.copy()
        top["_diag_ev_sort"] = _safe_numeric_col(top, "ev_pred", 0.0)
        top = top.sort_values(
            by=["_diag_ev_sort", "ts_code"],
            ascending=[False, True],
            kind="mergesort",
        ).head(5)
        diag["top_ev_rows"] = [
            {
                "ts_code": str(r.get("ts_code", "")),
                "name": str(r.get("name", "")),
                "ev_pred": _json_float(r.get("ev_pred", 0.0)),
                "ev_base": _json_float(r.get("ev_base", 0.0)),
                "p_fill_pred": _json_float(r.get("p_fill_pred", 0.0)),
                "e_ret_pred": _json_float(r.get("e_ret_pred", 0.0)),
                "cost_est": _json_float(r.get("cost_est", 0.0)),
                "risk_penalty": _json_float(r.get("risk_penalty", 0.0)),
                "extra_penalty": _json_float(r.get("ev_penalty_total_extra", 0.0)),
                "intraday_ev_bonus": _json_float(r.get("intraday_ev_bonus", 0.0)),
            }
            for _, r in top.iterrows()
        ]
    else:
        diag["top_ev_rows"] = []

    return diag


def _append_decision_diagnostics(lines: List[str], diag: dict[str, Any]) -> None:
    lines.append("## Decision Diagnostics\n\n")
    lines.append(f"- primary_no_trade_reason: **{diag.get('primary_no_trade_reason', 'unknown')}**\n")
    lines.append(f"- rows_scored: **{diag.get('rows_scored', 0)}**\n")
    lines.append(f"- selected_rows: **{diag.get('selected_rows', 0)}**\n")
    lines.append(f"- positive_ev_rows: **{diag.get('positive_ev_rows', 0)}**\n")
    lines.append(f"- positive_ev_base_rows: **{diag.get('positive_ev_base_rows', 0)}**\n")
    lines.append(f"- positive_e_ret_rows: **{diag.get('positive_e_ret_rows', 0)}**\n")
    lines.append(f"- high_pfill_rows: **{diag.get('high_pfill_rows', 0)}**\n")
    lines.append(f"- low_risk_rows: **{diag.get('low_risk_rows', 0)}**\n")
    lines.append(f"- max_EV: **{fmt_num(diag.get('ev_pred_max', 0.0), 6)}**\n")
    lines.append(f"- max_EV_base: **{fmt_num(diag.get('ev_base_max', 0.0), 6)}**\n")
    lines.append(f"- max_E_ret: **{fmt_num(diag.get('e_ret_pred_max', 0.0), 6)}**\n")
    lines.append(f"- mean_cost: **{fmt_num(diag.get('cost_est_mean', 0.0), 6)}**\n")
    lines.append(f"- mean_risk_penalty: **{fmt_num(diag.get('risk_penalty_mean', 0.0), 6)}**\n")
    lines.append(f"- mean_extra_penalty_total: **{fmt_num(diag.get('ev_penalty_total_extra_mean', 0.0), 6)}**\n\n")


def _apply_ev_upgrade_v1(df: pd.DataFrame) -> pd.DataFrame:
    """
    Net trade EV contract.

    Cost and risk_penalty already contain execution, liquidity, crowding and
    intraday components. Re-applying those components here double-counted risk
    and forced almost every candidate negative. Legacy audit columns remain at
    zero so downstream schemas do not break.
    """
    out = df.copy()

    p_fill = _safe_numeric_col(out, "p_fill_pred", 0.0).clip(lower=0.0, upper=1.0)
    e_ret = _safe_numeric_col(out, "e_ret_pred", 0.0)
    cost_est = _safe_numeric_col(out, "cost_est", 0.0).clip(lower=0.0)
    risk_penalty = _safe_numeric_col(out, "risk_penalty", 0.0).clip(lower=0.0)

    out["ev_base"] = (p_fill * e_ret - cost_est - risk_penalty).astype(float)
    for column in (
        "pfill_penalty_soft",
        "pfill_penalty_hard",
        "pfill_penalty_extra",
        "risk_penalty_extra",
        "intraday_penalty_extra",
        "intraday_ev_bonus",
        "ev_penalty_total_extra",
    ):
        out[column] = 0.0
    out["ev_final"] = out["ev_base"].astype(float)
    out["ev_formula_version"] = "v2_net_trade_contract_no_double_count"
    out["ev_pred"] = out["ev_final"].astype(float)

    return out


def _build_report_candidate_view(
    routed_df: pd.DataFrame,
    weights_out: pd.DataFrame,
) -> pd.DataFrame:
    """
    生成报告展示用的候选池精简表，字段口径与 TopN Targets 保持一致：
    ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty

    说明：
    - 不在这里生成 rank；rank 由后续排序后再生成
    - weight 从 weights_out 映射；未入选/非候补默认为 0
    - 若某票同时出现在 target / backup 中，取最大的 weight（当前逻辑通常不会冲突）
    """
    base = routed_df.copy()

    weight_view = (
        weights_out[["ts_code", "weight"]]
        .copy()
        .groupby("ts_code", as_index=False)["weight"]
        .max()
    )

    view = base.merge(weight_view, on="ts_code", how="left")
    view["weight"] = pd.to_numeric(view["weight"], errors="coerce").fillna(0.0).astype(float)

    stage = pd.Series([""] * len(view), index=view.index, dtype="object")
    for c in ("晋阶", "advance_stage", "stage"):
        if c in view.columns:
            stage = view[c].astype(str).replace({"nan": "", "None": "", "<NA>": ""})
            break

    out = pd.DataFrame({
        "ts_code": view.get("ts_code", "").astype(str),
        "name": view.get("name", "").astype(str),
        "晋阶": stage,
        "weight": pd.to_numeric(view.get("weight", 0.0), errors="coerce").fillna(0.0).astype(float),
        "EV": pd.to_numeric(view.get("ev_pred", 0.0), errors="coerce").fillna(0.0).astype(float),
        "P_fill": pd.to_numeric(view.get("p_fill_pred", 0.0), errors="coerce").fillna(0.0).astype(float),
        "E_ret": pd.to_numeric(view.get("e_ret_pred", 0.0), errors="coerce").fillna(0.0).astype(float),
        "Cost": pd.to_numeric(view.get("cost_est", 0.0), errors="coerce").fillna(0.0).astype(float),
        "RiskPenalty": pd.to_numeric(view.get("risk_penalty", 0.0), errors="coerce").fillna(0.0).astype(float),
    })
    return out


def _sort_report_candidate_view(df: pd.DataFrame) -> pd.DataFrame:
    """
    报告口径统一排序：
    - EV 降序
    - ts_code 升序作为稳定次序
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts_code", "name", "晋阶", "weight", "EV", "P_fill", "E_ret", "Cost", "RiskPenalty"])

    out = df.copy()
    out["EV"] = pd.to_numeric(out["EV"], errors="coerce").fillna(0.0).astype(float)
    out = out.sort_values(
        by=["EV", "ts_code"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return out


def _build_high_ev_low_risk_view(df: pd.DataFrame) -> pd.DataFrame:
    """
    报告中新增的中间筛选表：
    - EV > 3%
    - RiskPenalty < 1%

    说明：
    - 输入 df 应为已经整理并排序好的 report_pool_df
    - 本函数只做筛选，不再二次改写排序逻辑
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["rank", "ts_code", "name", "晋阶", "weight", "EV", "P_fill", "E_ret", "Cost", "RiskPenalty"])

    out = df.copy()
    out["EV"] = pd.to_numeric(out["EV"], errors="coerce")
    out["RiskPenalty"] = pd.to_numeric(out["RiskPenalty"], errors="coerce")

    out = out.loc[
        (out["EV"] > 0.03) &
        (out["RiskPenalty"] < 0.01)
    ].copy().reset_index(drop=True)

    out["rank"] = out.index + 1
    return out


def _append_candidate_markdown_table(
    lines: List[str],
    title: str,
    df: pd.DataFrame,
) -> None:
    """
    向报告 lines 追加统一格式的候选表 markdown。
    """
    lines.append(f"## {title}\n\n")
    lines.append("| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |\n")
    lines.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|\n")

    for _, r in df.iterrows():
        lines.append(
            f"| {int(pd.to_numeric(r.get('rank', 0), errors='coerce'))} | "
            f"{r.get('ts_code', '')} | "
            f"{r.get('name', '')} | "
            f"{r.get('晋阶', '')} | "
            f"{fmt_num(r.get('weight', 0.0), 6)} | "
            f"{fmt_num(r.get('EV', 0.0), 6)} | "
            f"{fmt_num(r.get('P_fill', 0.0), 6)} | "
            f"{fmt_num(r.get('E_ret', 0.0), 6)} | "
            f"{fmt_num(r.get('Cost', 0.0), 6)} | "
            f"{fmt_num(r.get('RiskPenalty', 0.0), 6)} |\n"
        )
    lines.append("\n")


def main() -> int:
    args = _parse_args()
    requested_trade_date = _resolve_requested_trade_date(args)

    ensure_dirs()

    # ✅ 唯一入口：统一通过 ingest 层取数
    input_df, input_status, input_mode, fs_degrade_reason = _build_input_bundle()
    input_df = _prepare_runtime_input(input_df, requested_trade_date=requested_trade_date)
    input_df, universe_audit = filter_standard_limit_universe(
        input_df,
        code_col="ts_code",
        name_col="name",
    )
    input_status["universe_eligibility"] = universe_audit
    if input_df.empty:
        raise RuntimeError("Decision candidate pool is empty after excluding price-limit mechanisms above 10%")

    # 日志：用于验收“是否仍在吃 Top10(10行)”
    n_rows = int(len(input_df))
    if 0 < n_rows <= TOPN_DEFAULT:
        print(
            f"[WARN] input_df rows={n_rows} <= TOPN({TOPN_DEFAULT}). "
            "这通常意味着数据源仍是 Top10（10行）而非 decisio 全量。"
        )

    print(f"[run_v2] input_mode={input_mode}")
    if requested_trade_date:
        print(f"[run_v2] requested_trade_date={requested_trade_date}")
    if fs_degrade_reason:
        print(f"[run_v2] fs_degrade_reason={fs_degrade_reason}")
    print(f"[run_v2] universe_eligibility={universe_audit}")

    reg = simple_regime(input_df)
    gr = guardrails(input_df)

    regime_name = str(getattr(reg, "regime", "RISK_ON"))
    risk_budget = float(getattr(reg, "risk_budget", 1.0))

    # topk：不超过数据本身行数；guardrails 若不给/给0，则用默认
    gr_topk = int(getattr(gr, "topk", TOPK_DEFAULT)) if hasattr(gr, "topk") else TOPK_DEFAULT
    if gr_topk <= 0:
        gr_topk = TOPK_DEFAULT
    topk = min(max(10, gr_topk), max(10, len(input_df)))

    # 不在模型/EV 之前截断：上游候选池超过 TOPK 时，必须先完整评分，
    # 再按最终 EV 选择 TopK，避免高 EV 候选因为原始行序被提前丢掉。
    routed_df = score_router(input_df).copy()

    trade_date = get_first_value(routed_df, "trade_date")
    trade_date = requested_trade_date or trade_date
    target_trade_date = get_first_value(routed_df, "target_trade_date")
    exec_date = choose_exec_date(trade_date, target_trade_date)
    exit_date = choose_exit_date(exec_date)

    # STOP 分支：保持原逻辑行为（只输出空 weights + 基础表 + 报告）
    if getattr(gr, "stop_trading", False):
        stop_note = getattr(gr, "reason", "STOP_TRADING")

        exec_path = ensure_execution_table(exec_date=exec_date)
        learning_path = ensure_learning_table()

        cand_snapshot = routed_df.copy()
        cand_snapshot["signal_date"] = norm_ymd(trade_date)
        cand_snapshot["exec_date"] = norm_ymd(exec_date)
        cand_snapshot["exit_date"] = norm_ymd(exit_date)
        cand_snapshot["p_fill_pred"] = 0.0
        cand_snapshot["e_ret_pred"] = 0.0
        cand_snapshot["input_mode"] = input_mode
        cand_snapshot["fs_degrade_reason"] = fs_degrade_reason
        cand_snapshot["requested_trade_date"] = requested_trade_date
        cand_snapshot["p_fill_pred_src"] = "stop_zero"
        cand_snapshot["eret_pred_src"] = "stop_zero"
        cand_snapshot = _attach_cost_risk_columns(cand_snapshot, regime_name=regime_name)
        cand_snapshot["ev_base"] = 0.0
        cand_snapshot["pfill_penalty_soft"] = 0.0
        cand_snapshot["pfill_penalty_hard"] = 0.0
        cand_snapshot["pfill_penalty_extra"] = 0.0
        cand_snapshot["risk_penalty_extra"] = 0.0
        cand_snapshot["intraday_penalty_extra"] = 0.0
        cand_snapshot["intraday_ev_bonus"] = 0.0
        cand_snapshot["ev_penalty_total_extra"] = 0.0
        cand_snapshot["ev_final"] = 0.0
        cand_snapshot["ev_formula_version"] = "v2_net_trade_contract_no_double_count_stop_zero"
        cand_snapshot["ev_pred"] = 0.0
        intraday_risk_stats = _build_intraday_risk_summary(cand_snapshot)
        decision_diagnostics = _build_decision_diagnostics(
            cand_snapshot,
            selected_rows=0,
            risk_budget=risk_budget,
            stop_reason=stop_note,
        )

        cand_path = write_candidates_snapshot(cand_snapshot, signal_date=trade_date)

        weights_df = pd.DataFrame(columns=WEIGHT_OUTPUT_COLUMNS)
        weights_latest_path, weights_dated_path = write_weights(weights_df, exec_date=exec_date)

        top_evr_signal_df = build_top_evr_signal_df(
            candidates_df=cand_snapshot,
            risk_budget=risk_budget,
            regime_name=regime_name,
            trade_date=trade_date,
            target_trade_date=target_trade_date,
        )
        top_evr_latest_path, top_evr_dated_path = write_top_evr_signals(
            top_evr_signal_df,
            trade_date=trade_date,
        )

        report_lines: List[str] = []
        report_lines.append(f"# Decision Report ({exec_date or 'unknown'})\n\n")
        report_lines.append(f"**停手：{stop_note}**\n\n")
        report_lines.append(f"- signal_date: **{trade_date or 'unknown'}**\n")
        report_lines.append(f"- exec_date: **{exec_date or 'unknown'}**\n")
        report_lines.append(f"- exit_date: **{exit_date or 'unknown'}**\n")
        report_lines.append("## Input Status\n\n")
        report_lines.append(f"- input_mode: **{input_mode}**\n")
        report_lines.append(f"- requested_trade_date: **{requested_trade_date or 'auto'}**\n")
        report_lines.append(f"- fs_degrade_reason: **{fs_degrade_reason or 'none'}**\n")
        report_lines.append(f"- pred_loaded: **{input_status.get('pred_loaded', False)}**\n")
        report_lines.append(f"- features_base_loaded: **{input_status.get('features_base_loaded', False)}**\n")
        report_lines.append(f"- features_limit_loaded: **{input_status.get('features_limit_loaded', False)}**\n")
        report_lines.append(f"- truth_close_loaded: **{input_status.get('truth_close_loaded', False)}**\n")
        report_lines.append(f"- meta_loaded: **{input_status.get('meta_loaded', False)}**\n")
        report_lines.append("- p_fill_pred_src: **stop_zero**\n")
        report_lines.append("- eret_pred_src: **stop_zero**\n\n")

        _append_intraday_risk_summary(report_lines, intraday_risk_stats)
        _append_decision_diagnostics(report_lines, decision_diagnostics)

        report_lines.append("## Artifacts\n\n")
        report_lines.append(f"- candidates_snapshot: `{cand_path}`\n")
        report_lines.append(f"- execution_table: `{exec_path}`\n")
        report_lines.append(f"- learning_table: `{learning_path}`\n")
        report_lines.append(f"- weights_latest: `{weights_latest_path}`\n")
        report_lines.append(f"- weights_dated: `{weights_dated_path}`\n")
        report_lines.append(f"- top_evr_latest: `{top_evr_latest_path}`\n")
        report_lines.append(f"- top_evr_dated: `{top_evr_dated_path}`\n")

        report_path = write_decision_report(exec_date, "".join(report_lines))

        write_eval_json(
            exec_date,
            {
                "exec_date": exec_date,
                "exit_date": exit_date,
                "signal_date": trade_date,
                "requested_trade_date": requested_trade_date,
                "stop_trading": True,
                "reason": stop_note,
                "regime": regime_name,
                "risk_budget": risk_budget,
                "regime_reason": str(getattr(reg, "reason", "") or ""),
                "guardrail_reason": str(getattr(gr, "reason", "") or ""),
                "input_mode": input_mode,
                "fs_degrade_reason": fs_degrade_reason,
                "input_status": input_status,
                "universe_eligibility": universe_audit,
                "intraday_risk": intraday_risk_stats,
                "decision_diagnostics": decision_diagnostics,
                "engine_status": {
                    "p_fill_pred_src": "stop_zero",
                    "eret_pred_src": "stop_zero",
                },
                "paths": {
                    "candidates": cand_path,
                    "execution": exec_path,
                    "learning": learning_path,
                    "weights_latest": weights_latest_path,
                    "weights_dated": weights_dated_path,
                    "top_evr_latest": top_evr_latest_path,
                    "top_evr_dated": top_evr_dated_path,
                    "decision_report": report_path,
                },
            },
        )

        return 0

    # ===== 正常分支：接入 P_fill / E_ret engine =====
    routed_df = routed_df.copy()
    routed_df["signal_date"] = norm_ymd(trade_date)
    routed_df["exec_date"] = norm_ymd(exec_date)
    routed_df["exit_date"] = norm_ymd(exit_date)
    routed_df["regime_name"] = regime_name
    routed_df["requested_trade_date"] = requested_trade_date

    routed_df, pfill_audit = _run_pfill_engine(routed_df)
    routed_df, eret_audit = _run_eret_engine(routed_df)

    routed_df = _attach_cost_risk_columns(routed_df, regime_name=regime_name)
    routed_df = _apply_ev_upgrade_v1(routed_df)
    routed_df["input_mode"] = input_mode
    routed_df["fs_degrade_reason"] = fs_degrade_reason
    routed_df = routed_df.sort_values(
        by=["ev_pred", "ts_code"],
        ascending=[False, True],
        kind="mergesort",
    ).head(topk).reset_index(drop=True)
    intraday_risk_stats = _build_intraday_risk_summary(routed_df)

    cand_path = write_candidates_snapshot(routed_df.copy(), signal_date=trade_date)

    caps = WeightCaps(
        w_max=W_MAX_DEFAULT,
        theme_cap=THEME_CAP_DEFAULT,
        gross_cap=GROSS_CAP_DEFAULT * max(0.0, min(1.0, risk_budget)),
    )
    # weights：目标 + 候补（同一文件，候补 weight=0）。冻结部署没有
    # learning_acceptance 时仍保留候选观察面，但可执行权重必须全部归零。
    weights_out, execution_gate = _build_execution_weights(
        routed_df,
        caps=caps,
        exec_date=exec_date,
        pfill_audit=pfill_audit,
        eret_audit=eret_audit,
    )
    weights_latest_path, weights_dated_path = write_weights(weights_out, exec_date=exec_date)

    # signals：只输出目标（weight>0）——保持旧 joinquant 契约
    signal_df = build_signal_df_for_joinquant(
        weights_df=weights_out,
        risk_budget=risk_budget,
        regime_name=regime_name,
        trade_date=trade_date,
        target_trade_date=target_trade_date,
    )
    write_signals(signal_df, trade_date=trade_date)

    # TopEVR：动态票数，允许空表。执行门关闭时必须同步关闭这一旁路。
    top_evr_candidates = routed_df.iloc[0:0] if execution_gate else routed_df
    top_evr_signal_df = build_top_evr_signal_df(
        candidates_df=top_evr_candidates,
        risk_budget=risk_budget,
        regime_name=regime_name,
        trade_date=trade_date,
        target_trade_date=target_trade_date,
    )
    top_evr_latest_path, top_evr_dated_path = write_top_evr_signals(
        top_evr_signal_df,
        trade_date=trade_date,
    )

    exec_path = ensure_execution_table(exec_date=exec_date)
    learning_path = ensure_learning_table()

    # decision report（内容/表结构保持原脚本逻辑，仅增加输入审计与全候选池表）
    top_targets = (
        weights_out[weights_out["weight"].astype(float) > 0]
        .copy()
        .sort_values("target_rank")
    )
    decision_diagnostics = _build_decision_diagnostics(
        routed_df,
        selected_rows=int(len(top_targets)),
        risk_budget=risk_budget,
        stop_reason=execution_gate,
    )

    # 报告展示口径：先统一成精简表，再按 EV 降序拆成 TopN / 剩余候选
    report_pool_df = _build_report_candidate_view(routed_df, weights_out)
    report_pool_df = _sort_report_candidate_view(report_pool_df)

    # 新增：插在 Artifacts 下方、TopN Targets 上方的中间筛选表
    high_ev_low_risk_df = _build_high_ev_low_risk_view(report_pool_df)

    selected_mask = pd.to_numeric(report_pool_df["weight"], errors="coerce").fillna(0.0) > 0

    topn_report_df = report_pool_df.loc[selected_mask].copy().reset_index(drop=True)
    topn_report_df["rank"] = topn_report_df.index + 1

    full_candidate_report_df = report_pool_df.loc[~selected_mask].copy().reset_index(drop=True)
    full_candidate_report_df["rank"] = full_candidate_report_df.index + len(topn_report_df) + 1

    lines: List[str] = []
    lines.append(f"# Decision Report ({exec_date or 'unknown'})\n\n")
    lines.append(f"- signal_date: **{trade_date or 'unknown'}**\n")
    lines.append(f"- exec_date: **{exec_date or 'unknown'}**\n")
    lines.append(f"- exit_date: **{exit_date or 'unknown'}**\n")
    lines.append(f"- requested_trade_date: **{requested_trade_date or 'auto'}**\n")
    lines.append(f"- regime: **{regime_name}**\n")
    lines.append(f"- risk_budget: **{fmt_num(risk_budget, 4)}**\n")
    lines.append(f"- regime_reason: **{str(getattr(reg, 'reason', '') or 'none')}**\n")
    lines.append(f"- guardrail_reason: **{str(getattr(gr, 'reason', '') or 'none')}**\n")
    lines.append(f"- execution_gate: **{execution_gate or 'MODEL_ACCEPTED'}**\n")
    lines.append(f"- input_mode: **{input_mode}**\n")
    lines.append(f"- fs_degrade_reason: **{fs_degrade_reason or 'none'}**\n\n")

    lines.append("## Input Status\n\n")
    lines.append(f"- pred_loaded: **{input_status.get('pred_loaded', False)}**\n")
    lines.append(f"- pred_rows: **{input_status.get('pred_rows', 0)}**\n")
    lines.append(f"- features_base_loaded: **{input_status.get('features_base_loaded', False)}**\n")
    lines.append(f"- features_base_rows: **{input_status.get('features_base_rows', 0)}**\n")
    lines.append(f"- features_limit_loaded: **{input_status.get('features_limit_loaded', False)}**\n")
    lines.append(f"- features_limit_rows: **{input_status.get('features_limit_rows', 0)}**\n")
    lines.append(f"- truth_close_loaded: **{input_status.get('truth_close_loaded', False)}**\n")
    lines.append(f"- truth_close_rows: **{input_status.get('truth_close_rows', 0)}**\n")
    lines.append(f"- meta_loaded: **{input_status.get('meta_loaded', False)}**\n\n")

    lines.append("## Engine Status\n\n")
    lines.append(f"- p_fill_pred_src: **{pfill_audit.get('p_fill_pred_src', '') or 'unknown'}**\n")
    lines.append(f"- p_fill_model_loaded: **{pfill_audit.get('p_fill_model_loaded', False)}**\n")
    lines.append(f"- p_fill_model_acceptance_pass: **{pfill_audit.get('p_fill_model_acceptance_pass', False)}**\n")
    lines.append(f"- p_fill_model_kind: **{pfill_audit.get('p_fill_model_kind', '') or 'none'}**\n")
    lines.append(f"- p_fill_degrade_reason: **{pfill_audit.get('p_fill_degrade_reason', '') or 'none'}**\n")
    lines.append(f"- eret_pred_src: **{eret_audit.get('eret_pred_src', '') or 'unknown'}**\n")
    lines.append(f"- eret_model_loaded: **{eret_audit.get('eret_model_loaded', False)}**\n")
    lines.append(f"- eret_model_acceptance_pass: **{eret_audit.get('eret_model_acceptance_pass', False)}**\n")
    lines.append(f"- eret_model_kind: **{eret_audit.get('eret_model_kind', '') or 'none'}**\n")
    lines.append(f"- eret_degrade_reason: **{eret_audit.get('eret_degrade_reason', '') or 'none'}**\n\n")

    _append_intraday_risk_summary(lines, intraday_risk_stats)
    _append_decision_diagnostics(lines, decision_diagnostics)

    lines.append("## Artifacts\n\n")
    lines.append(f"- candidates_snapshot: `{cand_path}`\n")
    lines.append(f"- execution_table: `{exec_path}`\n")
    lines.append(f"- learning_table: `{learning_path}`\n")
    lines.append(f"- weights_latest: `{weights_latest_path}`\n")
    lines.append(f"- weights_dated: `{weights_dated_path}`\n")
    lines.append(f"- top_evr_latest: `{top_evr_latest_path}`\n")
    lines.append(f"- top_evr_dated: `{top_evr_dated_path}`\n\n")

    _append_candidate_markdown_table(lines, "EV > 3% & RiskPenalty < 1%", high_ev_low_risk_df)
    _append_candidate_markdown_table(lines, "TopN Targets", topn_report_df)
    _append_candidate_markdown_table(lines, "Full Candidate Pool", full_candidate_report_df)

    report_path = write_decision_report(exec_date, "".join(lines))

    eval_payload = {
        "signal_date": trade_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "requested_trade_date": requested_trade_date,
        "regime": regime_name,
        "risk_budget": risk_budget,
        "regime_reason": str(getattr(reg, "reason", "") or ""),
        "guardrail_reason": str(getattr(gr, "reason", "") or ""),
        "execution_gate": execution_gate or "MODEL_ACCEPTED",
        "topk": int(len(routed_df)),
        "picked": int(len(top_targets)),
        "cost_est_mean": float(pd.to_numeric(routed_df["cost_est"], errors="coerce").fillna(0.0).mean()),
        "cost_est_min": float(pd.to_numeric(routed_df["cost_est"], errors="coerce").fillna(0.0).min()),
        "cost_est_max": float(pd.to_numeric(routed_df["cost_est"], errors="coerce").fillna(0.0).max()),
        "risk_penalty_mean": float(pd.to_numeric(routed_df["risk_penalty"], errors="coerce").fillna(0.0).mean()),
        "risk_penalty_min": float(pd.to_numeric(routed_df["risk_penalty"], errors="coerce").fillna(0.0).min()),
        "risk_penalty_max": float(pd.to_numeric(routed_df["risk_penalty"], errors="coerce").fillna(0.0).max()),
        "ev_base_mean": float(pd.to_numeric(routed_df["ev_base"], errors="coerce").fillna(0.0).mean()),
        "ev_final_mean": float(pd.to_numeric(routed_df["ev_final"], errors="coerce").fillna(0.0).mean()),
        "pfill_penalty_extra_mean": float(pd.to_numeric(routed_df["pfill_penalty_extra"], errors="coerce").fillna(0.0).mean()),
        "risk_penalty_extra_mean": float(pd.to_numeric(routed_df["risk_penalty_extra"], errors="coerce").fillna(0.0).mean()),
        "intraday_penalty_extra_mean": float(pd.to_numeric(routed_df["intraday_penalty_extra"], errors="coerce").fillna(0.0).mean()),
        "intraday_ev_bonus_mean": float(pd.to_numeric(routed_df["intraday_ev_bonus"], errors="coerce").fillna(0.0).mean()),
        "ev_formula_version": str(_safe_first_value(routed_df, "ev_formula_version", "v2_net_trade_contract_no_double_count")),
        "input_mode": input_mode,
        "fs_degrade_reason": fs_degrade_reason,
        "input_status": input_status,
        "universe_eligibility": universe_audit,
        "intraday_risk": intraday_risk_stats,
        "decision_diagnostics": decision_diagnostics,
        "engine_status": {
            "p_fill_pred_src": pfill_audit.get("p_fill_pred_src", ""),
            "p_fill_model_loaded": pfill_audit.get("p_fill_model_loaded", False),
            "p_fill_model_acceptance_pass": pfill_audit.get("p_fill_model_acceptance_pass", False),
            "p_fill_model_kind": pfill_audit.get("p_fill_model_kind", ""),
            "p_fill_model_path": pfill_audit.get("p_fill_model_path", ""),
            "p_fill_degrade_reason": pfill_audit.get("p_fill_degrade_reason", ""),
            "eret_pred_src": eret_audit.get("eret_pred_src", ""),
            "eret_model_loaded": eret_audit.get("eret_model_loaded", False),
            "eret_model_acceptance_pass": eret_audit.get("eret_model_acceptance_pass", False),
            "eret_model_kind": eret_audit.get("eret_model_kind", ""),
            "eret_model_path": eret_audit.get("eret_model_path", ""),
            "eret_degrade_reason": eret_audit.get("eret_degrade_reason", ""),
        },
        "paths": {
            "candidates": cand_path,
            "execution": exec_path,
            "learning": learning_path,
            "weights_latest": weights_latest_path,
            "weights_dated": weights_dated_path,
            "top_evr_latest": top_evr_latest_path,
            "top_evr_dated": top_evr_dated_path,
            "decision_report": report_path,
        },
        "report_stats": {
            "high_ev_low_risk_rows": int(len(high_ev_low_risk_df)),
            "topn_rows": int(len(topn_report_df)),
            "full_candidate_rows": int(len(full_candidate_report_df)),
        },
    }
    write_eval_json(exec_date, eval_payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
