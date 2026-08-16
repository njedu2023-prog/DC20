#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pfill_engine.py

定位：
- P_fill 在线推理 / 计算引擎
- 负责把“候选池 + 证据层”转换为可直接参与 EV 的 p_fill_pred
- 优先加载学习模型；若模型缺失或推理失败，则自动回退到规则模型
- 不写文件，不直接做排序，不处理 weights / reports

职责边界：
- 输入：已经按候选池裁剪后的 DataFrame（通常来自 ingest.build_model_input）
- 输出：附加 P_fill 推理结果后的 DataFrame
- 不负责：
  1) 跨仓库拉数
  2) 真值构建
  3) 训练
  4) EV 融合
  5) 落盘输出

当前版本：
- v5：严格按 pfill_meta.json 中显式落盘的 numeric_cols / categorical_cols 构造线上推理输入
- 关键修复：与 train_pfill.py 的 GBM 编码方式保持一致——类别列在线上也编码为 float，而不是直接喂 pandas category
- 若 models/pfill_lr.joblib 或 models/pfill_lgbm.joblib 存在，则优先走学习模型
- 学习模型输出做裁剪到 [0.02, 0.98]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

from top10decision.models.fill_model import fill_model_rule
from top10decision.decision.contracts import PFILL_EXECUTION_CONTRACT, PFILL_TRUTH_VERSION

PRED_MIN = 0.02
PRED_MAX = 0.98
CALIBRATED_PRED_MAX = 0.93
CALIBRATION_BASE_FLOOR = 0.55
CALIBRATION_BASE_CAP = 0.84
MODEL_LOGIT_SHRINK = 0.34
RULE_LOGIT_SHRINK = 0.55
ERRMSG_MAXLEN = 240
MISSING_CATEGORY_TOKEN = "__MISSING__"
LEARNING_ACCEPTANCE_RELATIVE_PATH = Path("outputs/learning/learning_acceptance_latest.json")
MIN_MODEL_INDEPENDENT_DATES = 20

# 仅在 meta 未显式给出 categorical_cols 时的兜底集合
CATEGORY_HINT_COLS = [
    "board",
    "area",
    "market",
    "limit_type",
    "prior_board",
]


def _detect_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _clip_prob_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(PRED_MIN).clip(lower=PRED_MIN, upper=PRED_MAX)


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


@dataclass
class PFillModelBundle:
    model: Any
    model_kind: str
    model_path: str
    feature_mode: str
    meta_path: str
    fill_base_rate: Optional[float]
    feature_cols: list[str]
    categorical_cols: list[str]
    numeric_cols: list[str]
    category_like_cols_detected: list[str]


def _load_pfill_meta(meta_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_selected_model_kind(meta: Dict[str, Any]) -> str:
    direct = str(meta.get("selected_model", "") or "").strip().lower()
    if direct in {"lgbm", "lr"}:
        return direct
    nested = meta.get("model_selection", {}) if isinstance(meta, dict) else {}
    if isinstance(nested, dict):
        selected = str(nested.get("selected_model", "") or "").strip().lower()
        if selected in {"lgbm", "lr"}:
            return selected
    return "lgbm"


def _metric_value(metrics: Dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(metrics.get(key))
    except Exception:
        return default
    return value if np.isfinite(value) else default


def _model_acceptance_status(root: Path, meta: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    path = root / LEARNING_ACCEPTANCE_RELATIVE_PATH
    details: Dict[str, Any] = {
        "pfill_model_acceptance_path": str(path),
        "pfill_model_acceptance_pass": False,
        "pfill_model_acceptance_anchor_trade_date": "",
        "pfill_model_independent_dates": 0,
    }
    if not path.exists():
        return False, "acceptance_file_missing", details
    try:
        acceptance = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, "acceptance_file_invalid", details
    section = acceptance.get("pfill", {}) if isinstance(acceptance, dict) else {}
    if not isinstance(section, dict):
        return False, "acceptance_pfill_section_invalid", details

    window = meta.get("window", {}) if isinstance(meta.get("window", {}), dict) else {}
    independent_dates = int(window.get("n_loaded_dates", 0) or 0)
    meta_anchor = str(meta.get("anchor_trade_date", "") or "").strip()
    acceptance_anchor = str(section.get("anchor_trade_date", "") or "").strip()
    selected_kind = _resolve_selected_model_kind(meta)
    acceptance_kind = str(section.get("selected_model", "") or "").strip().lower()
    details.update(
        {
            "pfill_model_acceptance_anchor_trade_date": acceptance_anchor,
            "pfill_model_independent_dates": independent_dates,
        }
    )
    checks = (
        (str(meta.get("status", "") or "").lower() == "trained", "model_meta_not_trained"),
        (str(meta.get("label_version", "") or "") == PFILL_TRUTH_VERSION, "pfill_truth_version_mismatch"),
        (str(meta.get("execution_contract", "") or "") == PFILL_EXECUTION_CONTRACT, "pfill_execution_contract_mismatch"),
        (str(section.get("label_version", "") or "") == PFILL_TRUTH_VERSION, "acceptance_truth_version_mismatch"),
        (str(section.get("execution_contract", "") or "") == PFILL_EXECUTION_CONTRACT, "acceptance_execution_contract_mismatch"),
        (section.get("acceptance_pass") is True, "acceptance_pass_false"),
        (section.get("selected_model_pass") is True, "selected_model_pass_false"),
        (bool(meta_anchor) and meta_anchor == acceptance_anchor, "acceptance_anchor_mismatch"),
        (selected_kind == acceptance_kind, "acceptance_model_kind_mismatch"),
        (independent_dates >= MIN_MODEL_INDEPENDENT_DATES, "insufficient_independent_dates"),
        (int(section.get("loaded_trade_dates", 0) or 0) >= MIN_MODEL_INDEPENDENT_DATES, "acceptance_dates_too_few"),
    )
    for passed, reason in checks:
        if not passed:
            return False, reason, details

    metrics = section.get("selected_model_metrics", {})
    if not isinstance(metrics, dict):
        return False, "selected_model_metrics_invalid", details
    if _metric_value(metrics, "valid_dates", 0.0) < 4:
        return False, "valid_dates_too_few", details
    if _metric_value(metrics, "auc", float("nan")) < 0.55:
        return False, "auc_below_0_55", details
    if _metric_value(metrics, "brier_skill_vs_train_rate", float("nan")) <= 0.0:
        return False, "brier_skill_not_positive", details
    details["pfill_model_acceptance_pass"] = True
    return True, "", details


def _resolve_fill_base_rate(meta: Dict[str, Any]) -> Optional[float]:
    dist = meta.get("label_distribution", {}) if isinstance(meta, dict) else {}
    if not isinstance(dist, dict):
        return None
    try:
        pos = float(dist.get("ready_all_pos", 0) or 0)
        neg = float(dist.get("ready_all_neg", 0) or 0)
    except Exception:
        return None
    total = pos + neg
    if total <= 0:
        return None
    return pos / total


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
    explicit: list[str] = []
    if isinstance(features, dict):
        raw = features.get("categorical_cols", [])
        if isinstance(raw, list):
            for c in raw:
                s = str(c).strip()
                if s and s in feature_cols:
                    explicit.append(s)
    if explicit:
        return explicit

    # 兜底：兼容旧 meta
    return [c for c in CATEGORY_HINT_COLS if c in feature_cols]


def _resolve_numeric_cols(meta: Dict[str, Any], feature_cols: list[str], categorical_cols: list[str]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    explicit: list[str] = []
    if isinstance(features, dict):
        raw = features.get("numeric_cols", [])
        if isinstance(raw, list):
            for c in raw:
                s = str(c).strip()
                if s and s in feature_cols:
                    explicit.append(s)
    if explicit:
        return explicit

    cat_set = set(categorical_cols)
    return [c for c in feature_cols if c not in cat_set]


def _resolve_category_like_cols(meta: Dict[str, Any], feature_cols: list[str]) -> list[str]:
    features = meta.get("features", {}) if isinstance(meta, dict) else {}
    raw = features.get("category_like_cols_detected", []) if isinstance(features, dict) else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for c in raw:
        s = str(c).strip()
        if s and s in feature_cols:
            out.append(s)
    return out


def _resolve_pfill_model(project_root: Optional[Path] = None) -> Tuple[Optional[PFillModelBundle], Dict[str, Any]]:
    root = project_root or _detect_project_root()

    lgbm_path = _existing_model_path(root, ["pfill_lgbm.joblib"])
    lr_path = _existing_model_path(root, ["pfill_lr.joblib"])
    meta_path = _existing_meta_path(root, ["pfill_meta.json"])
    meta: Dict[str, Any] = _load_pfill_meta(meta_path) if meta_path is not None else {}
    feature_cols: list[str] = []
    categorical_cols: list[str] = []
    numeric_cols: list[str] = []
    category_like_cols_detected: list[str] = []
    fill_base_rate = _resolve_fill_base_rate(meta)
    feature_cols = _resolve_feature_cols(meta)
    categorical_cols = _resolve_category_cols(meta, feature_cols)
    numeric_cols = _resolve_numeric_cols(meta, feature_cols, categorical_cols)
    category_like_cols_detected = _resolve_category_like_cols(meta, feature_cols)
    selected_kind = _resolve_selected_model_kind(meta)
    chosen = lgbm_path if selected_kind == "lgbm" else lr_path
    accepted, rejection_reason, acceptance_details = _model_acceptance_status(root, meta)

    base_audit: Dict[str, Any] = {
        "pfill_model_loaded": False,
        "pfill_model_kind": selected_kind if chosen else "",
        "pfill_model_path": str(chosen) if chosen else "",
        "pfill_model_feature_mode": "",
        "pfill_model_meta_path": str(meta_path) if meta_path else "",
        "pfill_model_expected_n_features": len(feature_cols),
        "pfill_model_expected_categorical_cols": "|".join(categorical_cols),
        "pfill_model_expected_numeric_feature_count": len(numeric_cols),
        "pfill_model_category_like_cols_detected": "|".join(category_like_cols_detected),
        **acceptance_details,
    }
    if chosen is None:
        return None, {**base_audit, "pfill_model_degrade_reason": f"selected_model_missing:{selected_kind}"}
    if not accepted:
        return None, {
            **base_audit,
            "pfill_model_degrade_reason": f"model_rejected_by_learning_acceptance:{rejection_reason}",
        }

    try:
        model = joblib.load(chosen)
        model_kind = selected_kind
        feature_mode = "meta_feature_contract" if feature_cols else "pipeline_auto"
        return PFillModelBundle(
            model=model,
            model_kind=model_kind,
            model_path=str(chosen),
            feature_mode=feature_mode,
            meta_path=str(meta_path) if meta_path else "",
            fill_base_rate=fill_base_rate,
            feature_cols=feature_cols,
            categorical_cols=categorical_cols,
            numeric_cols=numeric_cols,
            category_like_cols_detected=category_like_cols_detected,
        ), {
            "pfill_model_loaded": True,
            "pfill_model_kind": model_kind,
            "pfill_model_path": str(chosen),
            "pfill_model_feature_mode": feature_mode,
            "pfill_model_meta_path": str(meta_path) if meta_path else "",
            "pfill_model_expected_n_features": len(feature_cols),
            "pfill_model_expected_categorical_cols": "|".join(categorical_cols),
            "pfill_model_expected_numeric_feature_count": len(numeric_cols),
            "pfill_model_category_like_cols_detected": "|".join(category_like_cols_detected),
            **acceptance_details,
            "pfill_model_degrade_reason": "",
        }
    except Exception as e:
        return None, {
            **base_audit,
            "pfill_model_degrade_reason": f"model_load_failed:{type(e).__name__}:{_safe_errmsg(e)}",
        }


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
    "eret_truth_version",
    "return_holding_mode",
    "buy_window_start",
    "buy_window_end",
    "p_fill_pred",
    "p_fill_pred_raw",
    "p_fill_pred_rule",
    "p_fill_pred_final",
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


def _coerce_category_to_float(col: pd.Series) -> pd.Series:
    cat = col.astype("string").fillna(MISSING_CATEGORY_TOKEN)
    cat = cat.replace({
        "<NA>": MISSING_CATEGORY_TOKEN,
        "nan": MISSING_CATEGORY_TOKEN,
        "None": MISSING_CATEGORY_TOKEN,
        "": MISSING_CATEGORY_TOKEN,
    })
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

    as_str = col.astype("string").replace({
        "<NA>": np.nan,
        "nan": np.nan,
        "None": np.nan,
        "": np.nan,
    })
    return pd.to_numeric(as_str, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _normalize_feature_frame(x: pd.DataFrame, numeric_cols: list[str], categorical_cols: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=x.index)
    numeric_set = set(numeric_cols)
    categorical_set = set(categorical_cols)

    for c in x.columns:
        col = x[c]
        if c in categorical_set:
            out[c] = _coerce_category_to_float(col)
        elif c in numeric_set:
            out[c] = _coerce_numeric_series(col)
        else:
            # 理论上不会走到这里；兜底仍按 numeric 处理，保证模型输入为纯数值
            out[c] = _coerce_numeric_series(col)

    return out


def _build_feature_frame(df: pd.DataFrame, bundle: PFillModelBundle) -> pd.DataFrame:
    if bundle.feature_cols:
        x = pd.DataFrame(index=df.index)
        for col in bundle.feature_cols:
            if col in df.columns:
                x[col] = df[col]
            else:
                x[col] = np.nan
        x = x.loc[:, bundle.feature_cols]
        x.columns = [str(c) for c in x.columns]
        return _normalize_feature_frame(x, bundle.numeric_cols, bundle.categorical_cols)

    x = _select_feature_frame(df)
    x.columns = [str(c) for c in x.columns]
    # fallback：若 meta 缺失，全部按 numeric 兜底
    return _normalize_feature_frame(x, list(x.columns), [])


def _summarize_alignment(df: pd.DataFrame, bundle: PFillModelBundle, x: pd.DataFrame) -> Dict[str, Any]:
    expected = bundle.feature_cols or list(x.columns)
    expected_set = set(expected)
    incoming_cols = [str(c) for c in df.columns]
    incoming_set = set(incoming_cols)

    missing_cols = [c for c in expected if c not in incoming_set]
    unexpected_cols = [c for c in incoming_cols if c not in expected_set]

    numeric_cols = [c for c in x.columns if pd.api.types.is_numeric_dtype(x[c])]

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
    }


def _predict_by_model(bundle: PFillModelBundle, df: pd.DataFrame) -> Tuple[pd.Series, Dict[str, Any]]:
    x = _build_feature_frame(df, bundle)
    audit = _summarize_alignment(df=df, bundle=bundle, x=x)

    if hasattr(bundle.model, "predict_proba"):
        proba = bundle.model.predict_proba(x)
        arr = np.asarray(proba)
        if arr.ndim == 2 and arr.shape[1] >= 2:
            pred = arr[:, 1]
        else:
            pred = arr.reshape(-1)
    else:
        pred = bundle.model.predict(x)

    if isinstance(pred, pd.Series):
        out = pred.copy()
    else:
        out = pd.Series(np.asarray(pred).reshape(-1), index=df.index, name="p_fill_pred_model")

    return _clip_prob_series(out), audit


def _effective_calibration_base(fill_base_rate: Optional[float]) -> float:
    if fill_base_rate is None or not np.isfinite(fill_base_rate):
        return CALIBRATION_BASE_CAP
    return float(np.clip(fill_base_rate, CALIBRATION_BASE_FLOOR, CALIBRATION_BASE_CAP))


def _logit(p: pd.Series) -> pd.Series:
    x = pd.to_numeric(p, errors="coerce").fillna(PRED_MIN).clip(lower=PRED_MIN, upper=PRED_MAX)
    return np.log(x / (1.0 - x))


def _sigmoid(x: pd.Series) -> pd.Series:
    return 1.0 / (1.0 + np.exp(-x))


def _score_0_1_series(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series([default] * len(df), index=df.index, dtype="float64")
    s = pd.to_numeric(df[col], errors="coerce").fillna(default).astype("float64")
    s = s.where(~((s > 1.0) & (s <= 100.0)), s / 100.0)
    return s.clip(lower=0.0, upper=1.0)


def _bool_0_1_series(df: pd.DataFrame, col: str, default: bool = False) -> pd.Series:
    if col not in df.columns:
        return pd.Series([1.0 if default else 0.0] * len(df), index=df.index, dtype="float64")
    s = df[col]
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(default).astype("float64")
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").fillna(1.0 if default else 0.0).ne(0).astype("float64")
    normalized = s.astype("string").str.strip().str.lower()
    truthy = normalized.isin({"1", "1.0", "true", "yes", "y", "t", "ok", "available", "matched", "ready", "valid"})
    falsy = normalized.isin({"0", "0.0", "false", "no", "n", "f", "", "missing", "unavailable", "invalid"})
    out = pd.Series([default] * len(df), index=df.index)
    out = out.where(~truthy, True)
    out = out.where(~falsy, False)
    return out.astype("float64")


def _pfill_execution_adjustment(df: pd.DataFrame) -> pd.Series:
    adj = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    if df.empty:
        return adj

    has_intraday = any(
        c in df.columns
        for c in (
            "intraday_available",
            "intraday_quality_score",
            "intraday_confidence_score",
            "intraday_soft_risk_score",
            "intraday_risk_score",
            "intraday_hard_risk_flag",
            "late_withdraw_score",
            "reseal_score",
            "auction_strength_score",
            "open_board_count",
            "max_drawdown_after_limit",
        )
    )
    if not has_intraday:
        return adj

    available = _bool_0_1_series(df, "intraday_available", default=False)
    hard_risk = _bool_0_1_series(df, "intraday_hard_risk_flag", default=False)
    quality = _score_0_1_series(df, "intraday_quality_score", default=0.0)
    confidence = _score_0_1_series(df, "intraday_confidence_score", default=0.0)
    soft_risk = _score_0_1_series(df, "intraday_soft_risk_score", default=0.0)
    risk = _score_0_1_series(df, "intraday_risk_score", default=soft_risk.mean() if len(df) else 0.0)
    late = _score_0_1_series(df, "late_withdraw_score", default=0.0)
    reseal = _score_0_1_series(df, "reseal_score", default=0.0)
    auction = _score_0_1_series(df, "auction_strength_score", default=0.0)
    drawdown = _score_0_1_series(df, "max_drawdown_after_limit", default=0.0)
    open_board_raw = df["open_board_count"] if "open_board_count" in df.columns else pd.Series([0.0] * len(df), index=df.index)
    open_board = pd.to_numeric(open_board_raw, errors="coerce").fillna(0.0).clip(lower=0.0, upper=5.0) / 5.0

    adj += (quality - 0.5) * 0.040
    adj += (confidence - 0.5) * 0.030
    adj += auction * 0.025
    adj += reseal * 0.020
    adj -= soft_risk * 0.050
    adj -= risk * 0.030
    adj -= hard_risk * 0.080
    adj -= late * 0.040
    adj -= drawdown * 0.035
    adj -= open_board * 0.030
    adj -= (1.0 - available) * 0.015

    return adj.clip(lower=-0.16, upper=0.08)


def _rank_spread_adjustment(reference: pd.Series, width: float = 0.050) -> pd.Series:
    ref = pd.to_numeric(reference, errors="coerce")
    if len(ref) < 2 or ref.nunique(dropna=True) < 2:
        return pd.Series([0.0] * len(ref), index=ref.index, dtype="float64")
    return (ref.rank(method="average", pct=True) - 0.5) * width


def _calibrate_pfill_output(
    raw_pred: pd.Series,
    rule_pred: pd.Series,
    df: pd.DataFrame,
    fill_base_rate: Optional[float] = None,
) -> pd.Series:
    """
    Convert the model/rule probability into the displayed and ranked P_fill.

    The learned P_fill label is heavily imbalanced toward ready-fill positives.
    A direct model probability therefore saturates near 0.98 and loses ranking
    power. This calibration keeps the same public field while shrinking the
    imbalanced base rate, blending rule evidence, and applying minute/auction
    execution quality as an internal adjustment.
    """
    raw = _clip_prob_series(raw_pred)
    rule = _clip_prob_series(rule_pred)
    base = _effective_calibration_base(fill_base_rate)
    base_logit = float(np.log(base / (1.0 - base)))

    model_component = _sigmoid(base_logit + MODEL_LOGIT_SHRINK * (_logit(raw) - base_logit))
    rule_component = _sigmoid(base_logit + RULE_LOGIT_SHRINK * (_logit(rule) - base_logit))
    calibrated = model_component * 0.72 + rule_component * 0.28

    reference = raw * 0.55 + rule * 0.45
    calibrated = calibrated + _rank_spread_adjustment(reference) + _pfill_execution_adjustment(df)
    return pd.to_numeric(calibrated, errors="coerce").fillna(PRED_MIN).clip(lower=PRED_MIN, upper=CALIBRATED_PRED_MAX)


def apply_pfill_engine(
    df: pd.DataFrame,
    project_root: Optional[Path] = None,
) -> pd.DataFrame:
    """
    输入：
    - df: 候选池 + 证据层 DataFrame

    输出：
    - 原表附加以下字段：
      p_fill_pred
      p_fill_pred_rule
      p_fill_pred_model
      p_fill_pred_final
      p_fill_model_loaded
      p_fill_model_kind
      p_fill_model_path
      p_fill_model_feature_mode
      p_fill_pred_src
      p_fill_degrade_reason
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()

    out = df.copy()

    rule_pred = fill_model_rule(out)
    rule_pred = _clip_prob_series(rule_pred)

    bundle, audit = _resolve_pfill_model(project_root=project_root)

    model_pred = pd.Series([np.nan] * len(out), index=out.index, name="p_fill_pred_model")
    pred_src = "rule"
    degrade_reason = str(audit.get("pfill_model_degrade_reason", "") or "")
    predict_audit: Dict[str, Any] = {
        "expected_n_features": int(audit.get("pfill_model_expected_n_features", 0) or 0),
        "actual_n_features": 0,
        "missing_feature_count": 0,
        "missing_feature_sample": "",
        "unexpected_feature_count": 0,
        "unexpected_feature_sample": "",
        "categorical_feature_count_online": 0,
        "categorical_feature_sample_online": "",
        "numeric_feature_count_online": 0,
        "online_dtypes_sample": "",
    }

    if bundle is not None:
        try:
            model_pred, predict_audit = _predict_by_model(bundle, out)
            pred_src = f"model:{bundle.model_kind}"
            degrade_reason = ""
        except Exception as e:
            pred_src = "rule"
            degrade_reason = f"model_predict_failed:{type(e).__name__}:{_safe_errmsg(e)}"

    final_pred_raw = pd.to_numeric(model_pred, errors="coerce")
    final_pred_raw = final_pred_raw.where(final_pred_raw.notna(), rule_pred)
    final_pred = _calibrate_pfill_output(
        raw_pred=final_pred_raw,
        rule_pred=rule_pred,
        df=out,
        fill_base_rate=bundle.fill_base_rate if bundle is not None else None,
    )

    out["p_fill_pred_rule"] = rule_pred
    out["p_fill_pred_model"] = pd.to_numeric(model_pred, errors="coerce")
    out["p_fill_pred_final"] = final_pred
    out["p_fill_pred"] = final_pred

    out["p_fill_model_loaded"] = bool(audit.get("pfill_model_loaded", False))
    out["p_fill_model_kind"] = str(audit.get("pfill_model_kind", ""))
    out["p_fill_model_path"] = str(audit.get("pfill_model_path", ""))
    out["p_fill_model_feature_mode"] = str(audit.get("pfill_model_feature_mode", ""))
    out["p_fill_model_meta_path"] = str(audit.get("pfill_model_meta_path", ""))
    out["p_fill_model_expected_n_features"] = int(audit.get("pfill_model_expected_n_features", 0) or 0)
    out["p_fill_expected_categorical_cols"] = str(audit.get("pfill_model_expected_categorical_cols", ""))
    out["p_fill_expected_numeric_feature_count"] = int(audit.get("pfill_model_expected_numeric_feature_count", 0) or 0)
    out["p_fill_model_category_like_cols_detected"] = str(audit.get("pfill_model_category_like_cols_detected", ""))
    out["p_fill_model_acceptance_path"] = str(audit.get("pfill_model_acceptance_path", ""))
    out["p_fill_model_acceptance_pass"] = bool(audit.get("pfill_model_acceptance_pass", False))
    out["p_fill_model_acceptance_anchor_trade_date"] = str(
        audit.get("pfill_model_acceptance_anchor_trade_date", "")
    )
    out["p_fill_model_independent_dates"] = int(audit.get("pfill_model_independent_dates", 0) or 0)

    out["p_fill_model_actual_n_features"] = int(predict_audit.get("actual_n_features", 0) or 0)
    out["p_fill_missing_feature_count"] = int(predict_audit.get("missing_feature_count", 0) or 0)
    out["p_fill_missing_feature_sample"] = str(predict_audit.get("missing_feature_sample", ""))
    out["p_fill_unexpected_feature_count"] = int(predict_audit.get("unexpected_feature_count", 0) or 0)
    out["p_fill_unexpected_feature_sample"] = str(predict_audit.get("unexpected_feature_sample", ""))
    out["p_fill_categorical_feature_count_online"] = int(predict_audit.get("categorical_feature_count_online", 0) or 0)
    out["p_fill_categorical_feature_sample_online"] = str(predict_audit.get("categorical_feature_sample_online", ""))
    out["p_fill_numeric_feature_count_online"] = int(predict_audit.get("numeric_feature_count_online", 0) or 0)
    out["p_fill_online_dtypes_sample"] = str(predict_audit.get("online_dtypes_sample", ""))

    out["p_fill_pred_src"] = pred_src
    out["p_fill_degrade_reason"] = degrade_reason

    return out


__all__ = [
    "apply_pfill_engine",
]
