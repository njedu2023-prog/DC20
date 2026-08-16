#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
eret_engine.py

定位：
- E_ret 在线推理 / 计算引擎
- 负责把“候选池 + 证据层”转换为可直接参与 EV 的 eret_pred
- 优先加载学习模型；若模型缺失或推理失败，则自动回退到规则模型
- 不写文件，不直接做排序，不处理 weights / reports

职责边界：
- 输入：已经按候选池裁剪后的 DataFrame（通常来自 ingest.build_model_input）
- 输出：附加 E_ret 推理结果后的 DataFrame
- 不负责：
  1) 跨仓库拉数
  2) 真值构建
  3) 训练
  4) EV 融合
  5) 落盘输出

当前版本：
- v5：学习模型只有在验收文件、模型元数据和独立交易日门槛全部一致时才可上线
- LR 保留 NaN 给训练流水线的中位数填补；LGBM 使用与训练一致的 -999 缺失哨兵
- 分钟证据不可用时，相关盘中特征统一视为缺失，避免把占位值当成真实信号
- 同时输出 raw / clipped / final，便于定位 E_ret 分布与裁剪问题
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

from top10decision.decision.contracts import ERET_HOLDING_MODE, ERET_TRUTH_VERSION

try:
    from top10decision.models.overnight_model import overnight_model_rule
except Exception:  # pragma: no cover
    def overnight_model_rule(df: pd.DataFrame, regime: str = "RISK_ON") -> pd.Series:
        return pd.Series(np.zeros(len(df)), index=df.index, name="eret_rule_fallback")


PRED_MIN = -0.30
PRED_MAX = 0.30
ERRMSG_MAXLEN = 240
MIN_MODEL_INDEPENDENT_DATES = 20
LEARNING_ACCEPTANCE_RELATIVE_PATH = Path("outputs/learning/learning_acceptance_latest.json")
CATEGORY_HINT_COLS = [
    "board",
    "area",
    "market",
    "limit_type",
    "prior_board",
]

# 这些字段依赖分钟级路径。intraday_available=0 时，上游写入的 0/0.5
# 只是占位符，不能进入学习模型成为真实观测。
INTRADAY_DEPENDENT_FEATURE_COLS = {
    "minute_rows",
    "limit_touch_count",
    "open_board_count",
    "max_drawdown_after_limit",
    "reseal_count",
    "reseal_minutes_avg",
    "late_volume_ratio",
    "late_price_weakness",
    "late_limit_hold_minutes",
    "late_withdraw_score",
    "reseal_score",
    "intraday_quality_score",
    "intraday_confidence_score",
    "intraday_risk_score",
    "intraday_soft_risk_score",
    "intraday_hard_risk_flag",
}


def _detect_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _to_numeric_ret_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _clip_ret_series(s: pd.Series) -> pd.Series:
    return _to_numeric_ret_series(s).clip(lower=PRED_MIN, upper=PRED_MAX)


def _clip_direction(raw: pd.Series, clipped: pd.Series) -> pd.Series:
    raw_num = _to_numeric_ret_series(raw)
    clipped_num = _to_numeric_ret_series(clipped)

    direction = pd.Series("", index=raw_num.index, dtype="object")
    direction = direction.mask(raw_num < PRED_MIN, "lower")
    direction = direction.mask(raw_num > PRED_MAX, "upper")
    direction = direction.mask((raw_num >= PRED_MIN) & (raw_num <= PRED_MAX), "")
    direction = direction.where(clipped_num.notna(), "")
    return direction


def _existing_model_path(root: Path, candidates: list[str]) -> Optional[Path]:
    for name in candidates:
        p = root / "models" / name
        if p.exists():
            return p
    return None


def _existing_meta_path(root: Path, candidates: list[str]) -> Optional[Path]:
    for name in candidates:
        p = root / "models" / name
        if p.exists():
            return p
    return None


def _safe_errmsg(exc: Exception, maxlen: int = ERRMSG_MAXLEN) -> str:
    msg = str(exc).replace("\n", " ").replace("\r", " ").strip()
    if not msg:
        msg = repr(exc)
    msg = " ".join(msg.split())
    if len(msg) > maxlen:
        msg = msg[:maxlen] + "..."
    return msg


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_json_dict(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _model_acceptance_status(root: Path, meta: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Fail closed unless the acceptance artifact exactly matches the model metadata."""
    acceptance_path = root / LEARNING_ACCEPTANCE_RELATIVE_PATH
    details: Dict[str, Any] = {
        "eret_model_acceptance_path": str(acceptance_path),
        "eret_model_acceptance_pass": False,
        "eret_model_acceptance_anchor_trade_date": "",
        "eret_model_independent_dates": 0,
    }

    if not acceptance_path.exists():
        return False, "acceptance_file_missing", details

    acceptance = _safe_json_dict(acceptance_path)
    if not acceptance:
        return False, "acceptance_file_invalid", details

    eret = acceptance.get("eret", {})
    if not isinstance(eret, dict):
        return False, "acceptance_eret_section_invalid", details

    meta_anchor = str(meta.get("anchor_trade_date", "") or "").strip()
    acceptance_anchor = str(eret.get("anchor_trade_date", "") or "").strip()
    selected_kind = _resolve_selected_model_kind(meta)
    acceptance_kind = str(eret.get("selected_model", "") or "").strip().lower()
    window = meta.get("window", {}) if isinstance(meta.get("window", {}), dict) else {}
    independent_dates = _safe_int(window.get("n_loaded_dates", 0), 0)

    details.update(
        {
            "eret_model_acceptance_anchor_trade_date": acceptance_anchor,
            "eret_model_independent_dates": independent_dates,
        }
    )

    checks = (
        (str(meta.get("status", "") or "").strip().lower() == "trained", "model_meta_not_trained"),
        (str(meta.get("eret_truth_version", "") or "") == ERET_TRUTH_VERSION, "eret_truth_version_mismatch"),
        (str(meta.get("return_holding_mode", "") or "") == ERET_HOLDING_MODE, "eret_holding_mode_mismatch"),
        (str(eret.get("status", "") or "").strip().lower() == "trained", "acceptance_status_not_trained"),
        (str(eret.get("eret_truth_version", "") or "") == ERET_TRUTH_VERSION, "acceptance_truth_version_mismatch"),
        (str(eret.get("return_holding_mode", "") or "") == ERET_HOLDING_MODE, "acceptance_holding_mode_mismatch"),
        (eret.get("selected_model_pass") is True, "selected_model_pass_false"),
        (eret.get("acceptance_pass") is True, "acceptance_pass_false"),
        (bool(meta_anchor) and meta_anchor == acceptance_anchor, "acceptance_anchor_mismatch"),
        (acceptance_kind == selected_kind, "acceptance_model_kind_mismatch"),
        (independent_dates >= MIN_MODEL_INDEPENDENT_DATES, "insufficient_independent_dates"),
        (_safe_int(eret.get("loaded_trade_dates", 0), 0) >= MIN_MODEL_INDEPENDENT_DATES, "acceptance_dates_too_few"),
    )
    for passed, reason in checks:
        if not passed:
            return False, reason, details

    selected_metrics = eret.get("selected_model_metrics", {})
    if not isinstance(selected_metrics, dict):
        return False, "selected_model_metrics_invalid", details

    daily_rank_ic = _metric_value(selected_metrics, "daily_spearman_corr_mean", float("nan"))
    rmse_skill = _metric_value(selected_metrics, "rmse_skill_vs_train_mean", float("nan"))
    if not np.isfinite(daily_rank_ic) or daily_rank_ic <= 0.0:
        return False, "daily_rank_ic_not_positive", details
    if not np.isfinite(rmse_skill) or rmse_skill <= 0.0:
        return False, "rmse_skill_not_positive", details

    details["eret_model_acceptance_pass"] = True
    return True, "", details


@dataclass
class ERetModelBundle:
    model: Any
    model_kind: str
    model_path: str
    feature_mode: str
    meta_path: str
    feature_cols: list[str]
    categorical_cols: list[str]
    numeric_cols: list[str]
    category_like_cols_detected: list[str]


def _load_eret_meta(meta_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_feature_cols(meta: Dict[str, Any]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    cols = features.get("feature_cols", []) if isinstance(features, dict) else []
    if not isinstance(cols, list):
        return []

    out: list[str] = []
    for c in cols:
        if c is None:
            continue
        s = str(c).strip()
        if not s:
            continue
        out.append(s)
    return out


def _resolve_category_cols(meta: Dict[str, Any], feature_cols: list[str]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    explicit = []
    if isinstance(features, dict):
        raw = features.get("categorical_cols", [])
        if isinstance(raw, list):
            for c in raw:
                s = str(c).strip()
                if s and s in feature_cols:
                    explicit.append(s)
    if explicit:
        return explicit

    resolved = [c for c in CATEGORY_HINT_COLS if c in feature_cols]
    meta_n_categorical = 0
    if isinstance(features, dict):
        try:
            meta_n_categorical = int(features.get("n_categorical", 0) or 0)
        except Exception:
            meta_n_categorical = 0
    if meta_n_categorical > 0 and len(resolved) > meta_n_categorical:
        resolved = resolved[:meta_n_categorical]
    return resolved


def _resolve_numeric_cols(meta: Dict[str, Any], feature_cols: list[str], categorical_cols: list[str]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    explicit = []
    if isinstance(features, dict):
        raw = features.get("numeric_cols", [])
        if isinstance(raw, list):
            for c in raw:
                s = str(c).strip()
                if s and s in feature_cols:
                    explicit.append(s)
    if explicit:
        return explicit
    categorical_set = set(categorical_cols)
    return [c for c in feature_cols if c not in categorical_set]


def _resolve_category_like_cols(meta: Dict[str, Any]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    raw = features.get("category_like_cols_detected", []) if isinstance(features, dict) else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for c in raw:
        s = str(c).strip()
        if s:
            out.append(s)
    return out


def _metric_value(metrics: Dict[str, Any], key: str, default: float) -> float:
    try:
        value = metrics.get(key)
        if value is None:
            return default
        out = float(value)
        if not np.isfinite(out):
            return default
        return out
    except Exception:
        return default


def _resolve_selected_model_kind(meta: Dict[str, Any]) -> str:
    if not isinstance(meta, dict):
        return "lgbm"

    direct = str(meta.get("selected_model", "") or "").strip().lower()
    if direct in {"lgbm", "lr"}:
        return direct

    selection = meta.get("model_selection", {})
    if isinstance(selection, dict):
        nested = str(selection.get("selected_model", "") or "").strip().lower()
        if nested in {"lgbm", "lr"}:
            return nested

    metrics_valid = meta.get("metrics_valid", {})
    if isinstance(metrics_valid, dict):
        scored: list[tuple[tuple[float, float, float, int], str]] = []
        for preference, kind in enumerate(("lgbm", "lr")):
            metrics = metrics_valid.get(kind, {})
            if not isinstance(metrics, dict):
                metrics = {}
            rmse = _metric_value(metrics, "rmse", float("inf"))
            mae = _metric_value(metrics, "mae", float("inf"))
            directional_acc = _metric_value(metrics, "directional_acc", -1.0)
            scored.append(((rmse, mae, -directional_acc, preference), kind))
        scored.sort(key=lambda x: x[0])
        if scored and np.isfinite(scored[0][0][0]):
            return scored[0][1]

    return "lgbm"


def _build_model_missing_audit(meta_path: Optional[Path]) -> Dict[str, Any]:
    return {
        "eret_model_loaded": False,
        "eret_model_kind": "",
        "eret_model_path": "",
        "eret_model_feature_mode": "",
        "eret_model_meta_path": str(meta_path) if meta_path else "",
        "eret_model_expected_n_features": 0,
        "eret_model_expected_categorical_cols": "",
        "eret_model_expected_numeric_feature_count": 0,
        "eret_model_category_like_cols_detected": "",
        "eret_model_acceptance_path": "",
        "eret_model_acceptance_pass": False,
        "eret_model_acceptance_anchor_trade_date": "",
        "eret_model_independent_dates": 0,
        "eret_model_degrade_reason": "model_missing_use_rule",
    }


def _build_model_load_failed_audit(
    chosen: Path,
    meta_path: Optional[Path],
    feature_cols: list[str],
    categorical_cols: list[str],
    numeric_cols: list[str],
    category_like_cols_detected: list[str],
    err: Exception,
    acceptance_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "eret_model_loaded": False,
        "eret_model_kind": "",
        "eret_model_path": str(chosen),
        "eret_model_feature_mode": "",
        "eret_model_meta_path": str(meta_path) if meta_path else "",
        "eret_model_expected_n_features": len(feature_cols),
        "eret_model_expected_categorical_cols": "|".join(categorical_cols),
        "eret_model_expected_numeric_feature_count": len(numeric_cols),
        "eret_model_category_like_cols_detected": "|".join(category_like_cols_detected),
        **(acceptance_details or {
            "eret_model_acceptance_path": "",
            "eret_model_acceptance_pass": False,
            "eret_model_acceptance_anchor_trade_date": "",
            "eret_model_independent_dates": 0,
        }),
        "eret_model_degrade_reason": f"model_load_failed:{type(err).__name__}:{_safe_errmsg(err)}",
    }


def _build_model_rejected_audit(
    meta_path: Optional[Path],
    feature_cols: list[str],
    categorical_cols: list[str],
    numeric_cols: list[str],
    category_like_cols_detected: list[str],
    reason: str,
    acceptance_details: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "eret_model_loaded": False,
        "eret_model_kind": "",
        "eret_model_path": "",
        "eret_model_feature_mode": "",
        "eret_model_meta_path": str(meta_path) if meta_path else "",
        "eret_model_expected_n_features": len(feature_cols),
        "eret_model_expected_categorical_cols": "|".join(categorical_cols),
        "eret_model_expected_numeric_feature_count": len(numeric_cols),
        "eret_model_category_like_cols_detected": "|".join(category_like_cols_detected),
        **acceptance_details,
        "eret_model_degrade_reason": f"model_rejected_by_learning_acceptance:{reason}",
    }


def _resolve_eret_model(project_root: Optional[Path] = None) -> Tuple[Optional[ERetModelBundle], Dict[str, Any]]:
    root = project_root or _detect_project_root()

    lr_path = _existing_model_path(root, ["eret_lr.joblib"])
    lgbm_path = _existing_model_path(root, ["eret_lgbm.joblib"])
    meta_path = _existing_meta_path(root, ["eret_meta.json"])

    meta: Dict[str, Any] = {}
    feature_cols: list[str] = []
    categorical_cols: list[str] = []
    numeric_cols: list[str] = []
    category_like_cols_detected: list[str] = []

    if meta_path is not None:
        meta = _load_eret_meta(meta_path)
        feature_cols = _resolve_feature_cols(meta)
        categorical_cols = _resolve_category_cols(meta, feature_cols)
        numeric_cols = _resolve_numeric_cols(meta, feature_cols, categorical_cols)
        category_like_cols_detected = _resolve_category_like_cols(meta)

    path_by_kind = {
        "lr": lr_path,
        "lgbm": lgbm_path,
    }
    selected_kind = _resolve_selected_model_kind(meta)
    ordered_kinds = [selected_kind] + [k for k in ("lgbm", "lr") if k != selected_kind]

    candidates: list[Path] = []
    for kind in ordered_kinds:
        p = path_by_kind.get(kind)
        if p is not None and p not in candidates:
            candidates.append(p)

    if not candidates:
        return None, _build_model_missing_audit(meta_path)

    accepted, rejection_reason, acceptance_details = _model_acceptance_status(root, meta)
    if not accepted:
        return None, _build_model_rejected_audit(
            meta_path=meta_path,
            feature_cols=feature_cols,
            categorical_cols=categorical_cols,
            numeric_cols=numeric_cols,
            category_like_cols_detected=category_like_cols_detected,
            reason=rejection_reason,
            acceptance_details=acceptance_details,
        )

    last_err: Optional[Exception] = None
    last_path: Optional[Path] = None

    for chosen in candidates:
        try:
            model = joblib.load(chosen)
            model_kind = "lgbm" if "lgbm" in chosen.name.lower() else "lr"
            feature_mode = "meta_feature_contract" if feature_cols else "pipeline_auto"
            return ERetModelBundle(
                model=model,
                model_kind=model_kind,
                model_path=str(chosen),
                feature_mode=feature_mode,
                meta_path=str(meta_path) if meta_path else "",
                feature_cols=feature_cols,
                categorical_cols=categorical_cols,
                numeric_cols=numeric_cols,
                category_like_cols_detected=category_like_cols_detected,
            ), {
                "eret_model_loaded": True,
                "eret_model_kind": model_kind,
                "eret_model_path": str(chosen),
                "eret_model_feature_mode": feature_mode,
                "eret_model_meta_path": str(meta_path) if meta_path else "",
                "eret_model_expected_n_features": len(feature_cols),
                "eret_model_expected_categorical_cols": "|".join(categorical_cols),
                "eret_model_expected_numeric_feature_count": len(numeric_cols),
                "eret_model_category_like_cols_detected": "|".join(category_like_cols_detected),
                **acceptance_details,
                "eret_model_degrade_reason": "",
            }
        except Exception as e:
            last_err = e
            last_path = chosen
            continue

    if last_path is None or last_err is None:
        return None, _build_model_missing_audit(meta_path)

    return None, _build_model_load_failed_audit(
        chosen=last_path,
        meta_path=meta_path,
        feature_cols=feature_cols,
        categorical_cols=categorical_cols,
        numeric_cols=numeric_cols,
        category_like_cols_detected=category_like_cols_detected,
        err=last_err,
        acceptance_details=acceptance_details,
    )


LEAKAGE_COLS = {
    "realized_ret_open_to_tplus1_open_0930",
    "realized_ret_open_to_tplus1_timed_exit",
    "realized_ret_open_to_next_open",
    "realized_ret_t1_to_t2",
    "premium_ret_t1_to_t2",
    "target_date",
    "exit_date",
    "exit_price_t2_close",
    "exit_price_tplus1_open",
    "exit_price_tplus1_timed",
    "exit_price_source",
    "exit_on_time",
    "exit_reason",
    "take_profit_price_tplus1",
    "stop_loss_price_tplus1",
    "latest_exit_time",
    "exit_policy_version",
    "close_t2",
    "open_t2",
    "high_t2",
    "low_t2",
    "vol_t2",
    "amount_t2",
    "pct_chg_t2",
    "exec_date",
    "entry_date",
    "entry_price_t1",
    "entry_price_t_opening_auction",
    "entry_price_proxy_t1",
    "entry_price_proxy_mode",
    "sample_maturity",
    "label_ready_fill",
    "label_ready_ret",
    "y_fill",
    "fill_label_quality",
    "eret_sample_eligible",
    "eret_label_quality",
    "dataset_split",
    "sample_weight",
    "eret_truth_version",
    "return_holding_mode",
    "buy_window_start",
    "buy_window_end",
    "e_ret_pred",
    "eret_pred",
    "eret_pred_raw",
    "eret_pred_model_raw",
    "eret_pred_model_clipped",
    "eret_pred_rule",
    "eret_pred_final",
}

ID_COLS = {
    "trade_date",
    "ts_code",
    "name",
    "run_id",
    "commit_sha",
    "generated_at_utc",
    "signal_date",
}


def _select_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    feature_cols = []
    for c in df.columns:
        if c in LEAKAGE_COLS or c in ID_COLS:
            continue
        if str(c).startswith("Unnamed:"):
            continue
        feature_cols.append(c)

    if not feature_cols:
        feature_cols = [c for c in df.columns if c not in ID_COLS]

    return df[feature_cols].copy()


def _encode_category_series(col: pd.Series) -> pd.Series:
    cat = col.astype("string").fillna("__MISSING__")
    cat = cat.replace({"<NA>": "__MISSING__", "nan": "__MISSING__", "None": "__MISSING__", "": "__MISSING__"})
    uniq = pd.Index(cat.unique())
    mapping = {v: i for i, v in enumerate(uniq)}
    return cat.map(mapping).fillna(-1).astype(float)


def _coerce_numeric_series(col: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(col):
        return col.astype("int64")
    if pd.api.types.is_numeric_dtype(col):
        return pd.to_numeric(col, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if pd.api.types.is_datetime64_any_dtype(col):
        return pd.to_numeric(col.dt.strftime("%Y%m%d"), errors="coerce")
    as_str = col.astype("string").replace({"<NA>": np.nan, "nan": np.nan, "None": np.nan, "": np.nan})
    return pd.to_numeric(as_str, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _normalize_feature_frame(x: pd.DataFrame, categorical_cols: list[str], numeric_cols: list[str]) -> pd.DataFrame:
    out = x.copy()
    categorical_set = set(categorical_cols)
    numeric_set = set(numeric_cols)

    for c in out.columns:
        col = out[c]
        if c in categorical_set:
            out[c] = _encode_category_series(col)
        elif c in numeric_set:
            out[c] = _coerce_numeric_series(col)
        else:
            out[c] = _coerce_numeric_series(col)

    return out.astype(float)


def _mask_unavailable_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "intraday_available" not in out.columns:
        return out

    raw = out["intraday_available"]
    numeric_available = pd.to_numeric(raw, errors="coerce").fillna(0.0).gt(0.0)
    text_available = raw.astype("string").str.strip().str.lower().isin({"true", "yes", "y", "ok"})
    unavailable = ~(numeric_available | text_available)
    for col in INTRADAY_DEPENDENT_FEATURE_COLS:
        if col in out.columns:
            out.loc[unavailable, col] = np.nan
    return out


def _build_feature_frame(df: pd.DataFrame, bundle: ERetModelBundle) -> pd.DataFrame:
    source = _mask_unavailable_intraday_features(df)
    if bundle.feature_cols:
        # 严格按照训练 meta 的 feature_cols 构造线上输入：
        # 1) 列集合固定
        # 2) 列顺序固定
        # 3) 缺失列先置 NaN，后续按模型训练契约处理
        x = pd.DataFrame(index=df.index)
        for col in bundle.feature_cols:
            if col in source.columns:
                x[col] = source[col]
            else:
                x[col] = np.nan
        x = x.loc[:, bundle.feature_cols]
        x.columns = [str(c) for c in x.columns]
        return _normalize_feature_frame(x, bundle.categorical_cols, bundle.numeric_cols)

    x = _select_feature_frame(source)
    x.columns = [str(c) for c in x.columns]
    return _normalize_feature_frame(x, bundle.categorical_cols, bundle.numeric_cols)


def _summarize_alignment(df: pd.DataFrame, bundle: ERetModelBundle, x: pd.DataFrame) -> Dict[str, Any]:
    expected = bundle.feature_cols or list(x.columns)
    expected_set = set(expected)
    incoming_cols = [str(c) for c in df.columns]
    incoming_set = set(incoming_cols)

    missing_cols = [c for c in expected if c not in incoming_set]
    unexpected_cols = [c for c in incoming_cols if c not in expected_set]
    numeric_cols = [c for c in x.columns if pd.api.types.is_numeric_dtype(x[c])]

    total_cells = int(x.shape[0] * x.shape[1])
    missing_cells = int(x.isna().sum().sum())
    missing_ratio = float(missing_cells / total_cells) if total_cells > 0 else 0.0
    rows_with_missing = int(x.isna().any(axis=1).sum()) if len(x) else 0
    row_missing_ratio_mean = float(x.isna().mean(axis=1).mean()) if len(x) else 0.0
    column_missing_ratio_max = float(x.isna().mean(axis=0).max()) if x.shape[1] else 0.0

    return {
        "expected_n_features": len(expected),
        "actual_n_features": int(x.shape[1]),
        "missing_feature_count": len(missing_cols),
        "missing_feature_sample": "|".join(missing_cols[:8]),
        "unexpected_feature_count": len(unexpected_cols),
        "unexpected_feature_sample": "|".join(unexpected_cols[:8]),
        "categorical_feature_count_online": len(bundle.categorical_cols),
        "categorical_feature_sample_online": "|".join(bundle.categorical_cols[:8]),
        "numeric_feature_count_online": len(numeric_cols),
        "online_dtypes_sample": "|".join(f"{c}:{x[c].dtype}" for c in list(x.columns)[:8]),
        "feature_missing_cell_count": missing_cells,
        "feature_missing_cell_ratio": missing_ratio,
        "feature_rows_with_missing_count": rows_with_missing,
        "feature_row_missing_ratio_mean": row_missing_ratio_mean,
        "feature_column_missing_ratio_max": column_missing_ratio_max,
    }


def _prepare_model_input(x: pd.DataFrame, model_kind: str) -> pd.DataFrame:
    clean = x.replace([np.inf, -np.inf], np.nan).astype(float)
    if str(model_kind).strip().lower() == "lgbm":
        # train_eret.encode_gbm_frame 使用同一个缺失哨兵。
        return clean.fillna(-999.0)
    # LR Pipeline 内含 SimpleImputer(strategy="median")，必须保留 NaN。
    return clean


def _predict_by_model(bundle: ERetModelBundle, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, Dict[str, Any]]:
    x = _build_feature_frame(df, bundle)
    audit = _summarize_alignment(df=df, bundle=bundle, x=x)
    x_model = _prepare_model_input(x, bundle.model_kind)

    pred = bundle.model.predict(x_model)
    pred_array = np.asarray(pred).reshape(-1)
    if len(pred_array) != len(df):
        raise ValueError(f"model_prediction_length_mismatch:{len(pred_array)}!={len(df)}")
    raw = pd.Series(pred_array, index=df.index, name="eret_pred_model_raw")
    raw = pd.to_numeric(raw, errors="coerce")
    if raw.isna().any() or not np.isfinite(raw.to_numpy(dtype=float)).all():
        raise ValueError("model_prediction_contains_non_finite_values")
    clipped = _clip_ret_series(raw)
    clipped.name = "eret_pred_model"
    return raw, clipped, audit


def _get_regime_name(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "RISK_ON"
    for col in ["regime_name", "regime"]:
        if col in df.columns:
            try:
                v = df[col].dropna().astype(str).str.strip()
                v = v[v != ""]
                if not v.empty:
                    return str(v.iloc[0])
            except Exception:
                pass
    return "RISK_ON"


def apply_eret_engine(
    df: pd.DataFrame,
    project_root: Optional[Path] = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()

    out = df.copy()
    regime_name = _get_regime_name(out)

    rule_pred = overnight_model_rule(out, regime=regime_name)
    if not isinstance(rule_pred, pd.Series):
        rule_pred = pd.Series(np.asarray(rule_pred).reshape(-1), index=out.index, name="eret_rule")
    rule_pred_raw = _to_numeric_ret_series(rule_pred)
    rule_pred = _clip_ret_series(rule_pred_raw)

    bundle, audit = _resolve_eret_model(project_root=project_root)

    model_pred_raw = pd.Series([np.nan] * len(out), index=out.index, name="eret_pred_model_raw")
    model_pred = pd.Series([np.nan] * len(out), index=out.index, name="eret_pred_model")
    pred_src = "rule"
    degrade_reason = str(audit.get("eret_model_degrade_reason", "") or "")
    predict_audit: Dict[str, Any] = {
        "expected_n_features": int(audit.get("eret_model_expected_n_features", 0) or 0),
        "actual_n_features": 0,
        "missing_feature_count": 0,
        "missing_feature_sample": "",
        "unexpected_feature_count": 0,
        "unexpected_feature_sample": "",
        "categorical_feature_count_online": 0,
        "categorical_feature_sample_online": "",
        "numeric_feature_count_online": 0,
        "online_dtypes_sample": "",
        "feature_missing_cell_count": 0,
        "feature_missing_cell_ratio": 0.0,
        "feature_rows_with_missing_count": 0,
        "feature_row_missing_ratio_mean": 0.0,
        "feature_column_missing_ratio_max": 0.0,
    }

    if bundle is not None:
        try:
            model_pred_raw, model_pred, predict_audit = _predict_by_model(bundle, out)
            pred_src = f"model:{bundle.model_kind}"
            degrade_reason = ""
        except Exception as e:
            pred_src = "rule"
            degrade_reason = f"model_predict_failed:{type(e).__name__}:{_safe_errmsg(e)}"

    final_raw = pd.to_numeric(model_pred_raw, errors="coerce")
    final_raw = final_raw.where(final_raw.notna(), rule_pred_raw)
    final_raw = _to_numeric_ret_series(final_raw)

    final_pred = pd.to_numeric(model_pred, errors="coerce")
    final_pred = final_pred.where(final_pred.notna(), rule_pred)
    final_pred = _clip_ret_series(final_pred)

    clip_hit = (final_raw < PRED_MIN) | (final_raw > PRED_MAX)
    clip_direction = _clip_direction(final_raw, final_pred)

    out["eret_pred_rule"] = rule_pred
    out["eret_pred_rule_raw"] = rule_pred_raw
    out["eret_pred_model_raw"] = pd.to_numeric(model_pred_raw, errors="coerce")
    out["eret_pred_model_clipped"] = pd.to_numeric(model_pred, errors="coerce")
    # 兼容旧字段语义：eret_pred_model 仍表示参与 final 的模型值（裁剪后）。
    out["eret_pred_model"] = pd.to_numeric(model_pred, errors="coerce")

    # 新增诊断字段：raw / clipped / final 分离。
    out["eret_pred_raw"] = final_raw
    out["e_ret_pred_raw"] = final_raw
    out["eret_pred_final"] = final_pred
    out["eret_pred"] = final_pred
    out["e_ret_pred"] = final_pred

    out["eret_clip_hit"] = clip_hit.astype(int)
    out["e_ret_clip_hit"] = clip_hit.astype(int)
    out["eret_clip_direction"] = clip_direction
    out["e_ret_clip_direction"] = clip_direction
    out["eret_clip_lower_hit"] = (final_raw < PRED_MIN).astype(int)
    out["eret_clip_upper_hit"] = (final_raw > PRED_MAX).astype(int)

    out["eret_model_loaded"] = bool(audit.get("eret_model_loaded", False))
    out["eret_model_kind"] = str(audit.get("eret_model_kind", ""))
    out["eret_model_path"] = str(audit.get("eret_model_path", ""))
    out["eret_model_feature_mode"] = str(audit.get("eret_model_feature_mode", ""))
    out["eret_model_meta_path"] = str(audit.get("eret_model_meta_path", ""))
    out["eret_model_expected_n_features"] = int(audit.get("eret_model_expected_n_features", 0) or 0)
    out["eret_expected_categorical_cols"] = str(audit.get("eret_model_expected_categorical_cols", ""))
    out["eret_expected_numeric_feature_count"] = int(audit.get("eret_model_expected_numeric_feature_count", 0) or 0)
    out["eret_model_category_like_cols_detected"] = str(audit.get("eret_model_category_like_cols_detected", ""))
    out["eret_model_acceptance_path"] = str(audit.get("eret_model_acceptance_path", ""))
    out["eret_model_acceptance_pass"] = bool(audit.get("eret_model_acceptance_pass", False))
    out["eret_model_acceptance_anchor_trade_date"] = str(
        audit.get("eret_model_acceptance_anchor_trade_date", "")
    )
    out["eret_model_independent_dates"] = int(audit.get("eret_model_independent_dates", 0) or 0)

    out["eret_model_actual_n_features"] = int(predict_audit.get("actual_n_features", 0) or 0)
    out["eret_missing_feature_count"] = int(predict_audit.get("missing_feature_count", 0) or 0)
    out["eret_missing_feature_sample"] = str(predict_audit.get("missing_feature_sample", ""))
    out["eret_unexpected_feature_count"] = int(predict_audit.get("unexpected_feature_count", 0) or 0)
    out["eret_unexpected_feature_sample"] = str(predict_audit.get("unexpected_feature_sample", ""))
    out["eret_categorical_feature_count_online"] = int(predict_audit.get("categorical_feature_count_online", 0) or 0)
    out["eret_categorical_feature_sample_online"] = str(predict_audit.get("categorical_feature_sample_online", ""))
    out["eret_numeric_feature_count_online"] = int(predict_audit.get("numeric_feature_count_online", 0) or 0)
    out["eret_online_dtypes_sample"] = str(predict_audit.get("online_dtypes_sample", ""))

    out["eret_feature_missing_cell_count"] = int(predict_audit.get("feature_missing_cell_count", 0) or 0)
    out["eret_feature_missing_cell_ratio"] = float(predict_audit.get("feature_missing_cell_ratio", 0.0) or 0.0)
    out["e_ret_feature_missing_ratio"] = float(predict_audit.get("feature_missing_cell_ratio", 0.0) or 0.0)
    out["eret_feature_rows_with_missing_count"] = int(predict_audit.get("feature_rows_with_missing_count", 0) or 0)
    out["eret_feature_row_missing_ratio_mean"] = float(predict_audit.get("feature_row_missing_ratio_mean", 0.0) or 0.0)
    out["eret_feature_column_missing_ratio_max"] = float(predict_audit.get("feature_column_missing_ratio_max", 0.0) or 0.0)

    # 全局分布审计：重复写入每一行，便于后续 candidates / weights / report 任一层直接读取。
    out["eret_raw_min"] = float(final_raw.min()) if len(final_raw) else 0.0
    out["eret_raw_max"] = float(final_raw.max()) if len(final_raw) else 0.0
    out["eret_raw_mean"] = float(final_raw.mean()) if len(final_raw) else 0.0
    out["eret_raw_std"] = float(final_raw.std(ddof=0)) if len(final_raw) else 0.0
    out["eret_final_min"] = float(final_pred.min()) if len(final_pred) else 0.0
    out["eret_final_max"] = float(final_pred.max()) if len(final_pred) else 0.0
    out["eret_final_mean"] = float(final_pred.mean()) if len(final_pred) else 0.0
    out["eret_final_std"] = float(final_pred.std(ddof=0)) if len(final_pred) else 0.0
    out["eret_clip_hit_count"] = int(clip_hit.sum())
    out["eret_clip_hit_rate"] = float(clip_hit.mean()) if len(clip_hit) else 0.0
    out["eret_negative_count"] = int((final_pred < 0).sum())
    out["eret_negative_rate"] = float((final_pred < 0).mean()) if len(final_pred) else 0.0
    out["eret_positive_count"] = int((final_pred > 0).sum())
    out["eret_positive_rate"] = float((final_pred > 0).mean()) if len(final_pred) else 0.0

    out["eret_pred_src"] = pred_src
    out["eret_degrade_reason"] = degrade_reason
    out["eret_regime_used"] = regime_name

    return out


__all__ = [
    "apply_eret_engine",
]
