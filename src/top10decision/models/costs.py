#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ============================================================
# Cost / RiskPenalty 模块（Decision 主线）
# ------------------------------------------------------------
# 目标：
# 1) 不再停留在“固定 8bp + regime 二值开关”的占位状态
# 2) 升级为可交易、可解释、可逐股分化的轻量实用版本
# 3) 保持向后兼容：老调用方式不报错
#
# 说明：
# - 当前阶段仍属于“规则增强版”，不是学习模型版
# - 本文件已支持“分项归因”，下一步只需 run_v2.py 接线即可落到候选表
# ============================================================

# -----------------------------
# 默认参数（可后续外置配置）
# -----------------------------
COST_BP_DEFAULT = 8.0  # 兼容旧逻辑：默认总成本 8bp

# 基础手续费 / 过户 / 交易摩擦的保守近似（单边）
BASE_FEE_BP = 2.0

# 默认滑点 bp（按板块）
SLIPPAGE_MAIN_BP = 5.0
SLIPPAGE_GROWTH_BP = 7.0   # 创业板
SLIPPAGE_STAR_BP = 9.0     # 科创板

# 冲击成本参数
IMPACT_TURNOVER_REF = 8.0
IMPACT_MIN_BP = 0.0
IMPACT_MAX_BP = 18.0

# 额外成本参数（bp）
OPEN_COST_WEAK_BP = 2.0
OPEN_COST_BROKEN_BP = 4.0
OPEN_COST_MULTI_OPEN_MAX_BP = 6.0

SEAL_WEAK_BP_LOW = 1.5
SEAL_WEAK_BP_VERY_LOW = 3.0

AMOUNT_SMALL_BP = 2.0
AMOUNT_VERY_SMALL_BP = 4.0

LOW_PRICE_BP = 1.0
PENNY_PRICE_BP = 2.0

# 风险惩罚默认值（兼容旧逻辑）
RISK_PENALTY_OFF = 0.00
RISK_PENALTY_ON = 0.02

# 风险惩罚上限，避免把 EV 一次性扣穿得太离谱
RISK_PENALTY_CAP = 0.06

# 各风险子项权重（百分比收益口径，不是 bp）
W_REGIME_DEFENSE = 0.020
W_LIMIT_OPEN = 0.012
W_HIGH_OPEN_TIMES = 0.010
W_VOLATILITY = 0.008
W_LOW_LIQUIDITY = 0.010
W_HOT_THEME = 0.006
W_GEM_STAR = 0.004
W_EXTREME_TURNOVER = 0.005
W_WEAK_SEAL_LOW = 0.002
W_WEAK_SEAL_VERY_LOW = 0.004

# 新增：ST / 类 ST 风险惩罚
W_ST_LIKE = 0.020

# 第二轮精修新增
W_LIQUIDITY_AMOUNT = 0.006
W_TAIL_RISK = 0.006

# 分钟级摘要风险（来自 a-top10 outputs/decisio/pred_decisio_*.csv）
# 约束：旧输入没有 intraday_* 字段时必须完全不惩罚，避免历史链路漂移。
W_INTRADAY_HARD_RISK = 0.010
W_INTRADAY_SOFT_RISK = 0.006
W_INTRADAY_LOW_CONFIDENCE = 0.003
W_INTRADAY_MISSING = 0.002
W_LATE_WITHDRAW = 0.005
W_RESEAL_WEAKNESS = 0.004
W_AUCTION_WEAKNESS = 0.003

# 精修参数：同类风险压缩上限
EXECUTION_FRAGILITY_CAP = 0.018
CROWDING_CAP = 0.010
ST_WITH_OTHERS_CAP = 0.022
INTRADAY_EXECUTION_CAP = 0.012

# ============================================================
# 基础工具
# ============================================================

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        if pd.isna(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        if pd.isna(v):
            return default
        return str(v).strip()
    except Exception:
        return default


def _clip(v: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, v)))


def _normalize_bp(bp: float) -> float:
    return float(bp) / 10000.0


def _pick_first(row: pd.Series, *candidates: str, default: Any = None) -> Any:
    for c in candidates:
        if c in row.index:
            v = row.get(c)
            if v is not None and not pd.isna(v):
                return v
    return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass

    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v) != 0.0

    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "t"}:
        return True
    if s in {"0", "false", "no", "n", "f", ""}:
        return False
    return default


def _smooth_step(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """
    简单线性平滑：
    - x <= x0 => y0
    - x >= x1 => y1
    - 中间线性插值
    """
    if x <= x0:
        return float(y0)
    if x >= x1:
        return float(y1)
    ratio = (x - x0) / (x1 - x0)
    return float(y0 + ratio * (y1 - y0))


def _board_bucket(row: pd.Series) -> str:
    """
    粗分板块：
    - STAR: 科创板 688*
    - GROWTH: 创业板 300*
    - MAIN: 其余主板/中小板等
    """
    ts_code = _safe_str(_pick_first(row, "ts_code", default=""))
    board = _safe_str(_pick_first(row, "board", "market_board", default="")).upper()

    if ts_code.startswith("688") or "STAR" in board or "科创" in board:
        return "STAR"
    if ts_code.startswith("300") or "GEM" in board or "创业" in board:
        return "GROWTH"
    return "MAIN"


def _turnover_rate(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(
            row,
            "turnover_rate",
            "turnover_rate_f",
            "换手率",
            default=np.nan,
        ),
        default=np.nan,
    )


def _amp_pct(row: pd.Series) -> float:
    """
    日内振幅/波动代理，口径按百分数值处理，例如 8.3 表示 8.3%
    """
    return _safe_float(
        _pick_first(
            row,
            "amp",
            "amplitude",
            "pct_amplitude",
            "振幅",
            default=np.nan,
        ),
        default=np.nan,
    )


def _open_times(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(row, "open_times", "open_board_count", "炸板次数", default=np.nan),
        default=np.nan,
    )


def _theme_heat_score(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(
            row,
            "ThemeBoost",
            "theme_boost",
            "theme_heat",
            default=np.nan,
        ),
        default=np.nan,
    )


def _seal_strength(row: pd.Series) -> float:
    """
    封单强度代理，值越大越稳。
    """
    return _safe_float(
        _pick_first(row, "seal_amount", "封单额", default=np.nan),
        default=np.nan,
    )


def _trade_amount(row: pd.Series) -> float:
    """
    成交额代理，尽量兼容不同字段名。
    常见单位可能是元；如果未来源表单位变化，再统一修。
    """
    return _safe_float(
        _pick_first(
            row,
            "amount",
            "成交额",
            "turnover_amount",
            "deal_amount",
            default=np.nan,
        ),
        default=np.nan,
    )


def _close_price(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(
            row,
            "close",
            "收盘价",
            "last_price",
            default=np.nan,
        ),
        default=np.nan,
    )


def _tail_risk_score(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(row, "tail_risk_score", default=np.nan),
        default=np.nan,
    )


def _downside_vol(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(row, "downside_vol", default=np.nan),
        default=np.nan,
    )


def _max_drawdown_20d(row: pd.Series) -> float:
    return _safe_float(
        _pick_first(row, "max_drawdown_20d", default=np.nan),
        default=np.nan,
    )


def _limit_status(row: pd.Series) -> str:
    return _safe_str(
        _pick_first(row, "limit_status", "涨停状态", "封板状态", default="")
    ).upper()


def _is_limit_open_risk(row: pd.Series) -> bool:
    """
    开板/炸板风险代理：
    - open_times > 0
    - 或 limit_status 表示 OPEN / BROKEN / WEAK
    """
    ot = _open_times(row)
    if ot > 0:
        return True

    status = _limit_status(row)
    if status in {"OPEN", "BROKEN", "WEAK"}:
        return True
    return False


def _is_st_like_flag(row: pd.Series) -> bool:
    """
    ST / 类 ST 风险识别：
    优先读取输入字段：
    - is_st_like
    - is_st_like_limit
    同时兜底读取名称里是否含 ST / *ST
    """
    v1 = _pick_first(row, "is_st_like", default=None)
    if v1 is not None and not pd.isna(v1):
        if _safe_bool(v1, default=False):
            return True

    v2 = _pick_first(row, "is_st_like_limit", default=None)
    if v2 is not None and not pd.isna(v2):
        if _safe_bool(v2, default=False):
            return True

    name = _safe_str(_pick_first(row, "name", "name_fs", "name_limit", default="")).upper()
    if "ST" in name:
        return True

    return False


def _intraday_fields_present(row: pd.Series) -> bool:
    """
    判断本行是否带有 a-top10 的分钟级摘要字段。

    旧版 pred_source 不包含这些列时，Decision 主线必须保持原行为；
    因此所有分钟风险惩罚都以本函数为入口闸门。
    """
    return any(
        c in row.index
        for c in (
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
        )
    )


def _score_0_1(row: pd.Series, *cols: str, default: float = np.nan) -> float:
    v = _safe_float(_pick_first(row, *cols, default=default), default=default)
    if np.isnan(v):
        return v
    # 兼容 0~100 与 0~1 两种口径。
    if v > 1.0 and v <= 100.0:
        v = v / 100.0
    return _clip(v, 0.0, 1.0)


def _intraday_available_flag(row: pd.Series) -> bool:
    if not _intraday_fields_present(row):
        return False

    v = _pick_first(row, "intraday_available", default=None)
    if v is not None and not pd.isna(v):
        return _safe_bool(v, default=False)

    status = _safe_str(_pick_first(row, "intraday_status", default="")).lower()
    return status in {"ok", "available", "matched", "ready", "valid"}


def _intraday_status_text(row: pd.Series) -> str:
    return _safe_str(_pick_first(row, "intraday_status", default="")).lower()


def _slippage_bp_by_board(row: pd.Series) -> float:
    bucket = _board_bucket(row)
    if bucket == "STAR":
        return SLIPPAGE_STAR_BP
    if bucket == "GROWTH":
        return SLIPPAGE_GROWTH_BP
    return SLIPPAGE_MAIN_BP


# ============================================================
# Cost 模块
# ============================================================

def _impact_cost_bp(row: pd.Series) -> float:
    """
    冲击成本（bp）：
    用换手率做一个轻量但有分化能力的近似。
    """
    turnover = _turnover_rate(row)

    if np.isnan(turnover):
        return 1.5

    gap = abs(turnover - IMPACT_TURNOVER_REF)

    if turnover < 4:
        bp = 6.0 + (4.0 - turnover) * 1.5
    elif turnover > 18:
        bp = 4.0 + (turnover - 18.0) * 0.5
    else:
        bp = gap * 0.35

    return _clip(bp, IMPACT_MIN_BP, IMPACT_MAX_BP)


def _open_cost_bp(row: pd.Series) -> float:
    """
    开板/炸板导致的额外交易摩擦成本。
    """
    status = _limit_status(row)
    ot = _open_times(row)

    bp = 0.0
    if status == "WEAK":
        bp += OPEN_COST_WEAK_BP
    elif status in {"OPEN", "BROKEN"}:
        bp += OPEN_COST_BROKEN_BP

    if not np.isnan(ot) and ot > 0:
        bp += min(OPEN_COST_MULTI_OPEN_MAX_BP, ot * 1.2)

    return float(bp)


def _seal_cost_bp(row: pd.Series) -> float:
    """
    封单弱，意味着买入实现成本更高。
    """
    seal = _seal_strength(row)
    if np.isnan(seal):
        return 0.0
    if seal <= 2_000_000:
        return SEAL_WEAK_BP_VERY_LOW
    if seal <= 5_000_000:
        return SEAL_WEAK_BP_LOW
    return 0.0


def _amount_cost_bp(row: pd.Series) -> float:
    """
    小成交额流动性惩罚。
    """
    amount = _trade_amount(row)
    if np.isnan(amount):
        return 0.0
    if amount <= 30_000_000:
        return AMOUNT_VERY_SMALL_BP
    if amount <= 80_000_000:
        return AMOUNT_SMALL_BP
    return 0.0


def _price_cost_bp(row: pd.Series) -> float:
    """
    低价股微观结构摩擦成本。
    """
    px = _close_price(row)
    if np.isnan(px):
        return 0.0
    if px < 3:
        return PENNY_PRICE_BP
    if px < 5:
        return LOW_PRICE_BP
    return 0.0


def _cost_components_bp(row: pd.Series) -> dict[str, float]:
    base_fee_bp = BASE_FEE_BP
    slippage_bp = _slippage_bp_by_board(row)
    impact_bp = _impact_cost_bp(row)
    open_bp = _open_cost_bp(row)
    seal_bp = _seal_cost_bp(row)
    amount_bp = _amount_cost_bp(row)
    price_bp = _price_cost_bp(row)

    total_bp = (
        base_fee_bp
        + slippage_bp
        + impact_bp
        + open_bp
        + seal_bp
        + amount_bp
        + price_bp
    )

    return {
        "cost_base_bp": float(base_fee_bp),
        "cost_slippage_bp": float(slippage_bp),
        "cost_impact_bp": float(impact_bp),
        "cost_open_bp": float(open_bp),
        "cost_seal_bp": float(seal_bp),
        "cost_amount_bp": float(amount_bp),
        "cost_price_bp": float(price_bp),
        "cost_total_bp": float(total_bp),
    }


def _cost_from_row(row: pd.Series) -> float:
    """
    返回单只股票的总成本（小数收益口径）
    EV 中按：
        EV = P_fill * E_ret - Cost - RiskPenalty
    所以这里直接返回小数，例如 0.0008 = 8bp
    """
    parts = _cost_components_bp(row)
    return _normalize_bp(parts["cost_total_bp"])


def cost_breakdown_row(row: pd.Series) -> dict[str, float]:
    """
    返回单行成本分项（小数收益口径，便于直接落表）。
    """
    parts_bp = _cost_components_bp(row)
    return {k: _normalize_bp(v) for k, v in parts_bp.items()}


def cost_breakdown_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    返回 DataFrame 级别的成本分项表。
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "cost_base_bp",
                "cost_slippage_bp",
                "cost_impact_bp",
                "cost_open_bp",
                "cost_seal_bp",
                "cost_amount_bp",
                "cost_price_bp",
                "cost_total_bp",
            ]
        )

    out = df.apply(lambda r: pd.Series(_cost_components_bp(r)), axis=1)
    # 统一转成小数收益口径
    for c in out.columns:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0).astype(float) / 10000.0
    return out


def cost_estimate_rule(
    data: pd.DataFrame | pd.Series | None = None,
) -> pd.Series | float:
    """
    向后兼容：
    1) 老调用：cost_estimate_rule() -> float
    2) 新调用：cost_estimate_rule(row: Series) -> float
    3) 新调用：cost_estimate_rule(df: DataFrame) -> Series
    """
    if data is None:
        return _normalize_bp(COST_BP_DEFAULT)

    if isinstance(data, pd.Series):
        return float(_cost_from_row(data))

    if isinstance(data, pd.DataFrame):
        if data.empty:
            return pd.Series(dtype=float)
        return data.apply(_cost_from_row, axis=1).astype(float)

    return _normalize_bp(COST_BP_DEFAULT)


# ============================================================
# RiskPenalty 模块（精修版第二轮）
# ============================================================

def _risk_regime_penalty(row: pd.Series, regime: str) -> float:
    regime_u = _safe_str(regime, default="RISK_ON").upper()
    if regime_u in ("RISK_OFF", "OFF", "DEFENSE"):
        return W_REGIME_DEFENSE
    return 0.0


def _risk_open_penalty(row: pd.Series) -> float:
    """
    开板/炸板基础惩罚：
    - WEAK < OPEN/BROKEN
    - 仅 open_times>0 但无状态时，给较轻惩罚
    """
    status = _limit_status(row)
    ot = _open_times(row)

    if status == "WEAK":
        return 0.006
    if status in {"OPEN", "BROKEN"}:
        return 0.010
    if not np.isnan(ot) and ot > 0:
        return 0.005
    return 0.0


def _risk_open_times_penalty(row: pd.Series) -> float:
    """
    炸板次数惩罚：
    1次开始轻罚，2次明显，之后逐步接近上限
    """
    ot = _open_times(row)
    if np.isnan(ot) or ot <= 0:
        return 0.0
    if ot <= 1:
        return 0.002
    if ot <= 2:
        return 0.0045
    return min(W_HIGH_OPEN_TIMES, 0.006 + (ot - 2) * 0.0015)


def _risk_volatility_penalty(row: pd.Series) -> float:
    """
    波动惩罚连续化：
    - 6% 以下不罚
    - 6%~12% 平滑抬升
    - 12% 以上接近上限
    """
    amp = _amp_pct(row)
    if np.isnan(amp):
        return 0.0
    if amp <= 6:
        return 0.0
    if amp <= 12:
        return _smooth_step(amp, 6, 12, 0.0015, W_VOLATILITY)
    return W_VOLATILITY


def _risk_liquidity_penalty(row: pd.Series) -> float:
    """
    低流动性惩罚（换手率维度）：
    - turnover 越低，风险越高
    """
    turnover = _turnover_rate(row)
    if np.isnan(turnover):
        return 0.003
    if turnover <= 2:
        return W_LOW_LIQUIDITY
    if turnover <= 6:
        return _smooth_step(turnover, 2, 6, W_LOW_LIQUIDITY, 0.0015)
    return 0.0


def _risk_liquidity_amount_penalty(row: pd.Series) -> float:
    """
    低流动性惩罚（成交额维度）：
    - 小成交额股票更容易出现成交脆弱性
    """
    amount = _trade_amount(row)
    if np.isnan(amount):
        return 0.0
    if amount <= 80_000_000:
        return W_LIQUIDITY_AMOUNT
    if amount <= 300_000_000:
        return _smooth_step(amount, 80_000_000, 300_000_000, W_LIQUIDITY_AMOUNT, 0.001)
    return 0.0


def _risk_extreme_turnover_penalty(row: pd.Series) -> float:
    """
    极高换手拥挤惩罚连续化：
    - 18 开始轻微惩罚
    - 30 左右接近上限
    """
    turnover = _turnover_rate(row)
    if np.isnan(turnover):
        return 0.0
    if turnover <= 18:
        return 0.0
    if turnover <= 30:
        return _smooth_step(turnover, 18, 30, 0.001, W_EXTREME_TURNOVER)
    return W_EXTREME_TURNOVER


def _risk_theme_penalty(row: pd.Series) -> float:
    """
    题材热度惩罚连续化：
    - 5 以下不罚
    - 5~8 平滑抬升
    - 8 以上接近上限
    """
    theme_heat = _theme_heat_score(row)
    if np.isnan(theme_heat):
        return 0.0
    if theme_heat <= 5:
        return 0.0
    if theme_heat <= 8:
        return _smooth_step(theme_heat, 5, 8, 0.0015, W_HOT_THEME)
    return W_HOT_THEME


def _risk_board_penalty(row: pd.Series) -> float:
    bucket = _board_bucket(row)
    if bucket == "STAR":
        return W_GEM_STAR
    if bucket == "GROWTH":
        return 0.003
    return 0.0


def _risk_seal_penalty(row: pd.Series) -> float:
    """
    封单弱惩罚平滑化
    """
    seal = _seal_strength(row)
    if np.isnan(seal):
        return 0.0
    if seal <= 2_000_000:
        return W_WEAK_SEAL_VERY_LOW
    if seal <= 5_000_000:
        return _smooth_step(
            seal,
            2_000_000,
            5_000_000,
            W_WEAK_SEAL_VERY_LOW,
            W_WEAK_SEAL_LOW,
        )
    if seal <= 10_000_000:
        return _smooth_step(
            seal,
            5_000_000,
            10_000_000,
            W_WEAK_SEAL_LOW,
            0.0,
        )
    return 0.0


def _risk_st_penalty(row: pd.Series) -> float:
    """
    ST / 类 ST 风险惩罚。
    """
    if _is_st_like_flag(row):
        return W_ST_LIKE
    return 0.0


def _risk_tail_penalty(row: pd.Series) -> float:
    """
    第二轮新增：尾部风险惩罚
    优先使用 tail_risk_score；
    若缺失，则退回 downside_vol / max_drawdown_20d 的轻量近似。

    Feature Store 中这些字段是 decimal return 口径：
    - 0.08 表示 8%
    - max_drawdown_20d 常为负值，这里用绝对回撤幅度比较
    """
    trs = _tail_risk_score(row)
    if not np.isnan(trs):
        if trs <= 0.08:
            return 0.0
        if trs <= 0.24:
            return _smooth_step(trs, 0.08, 0.24, 0.0015, W_TAIL_RISK)
        return W_TAIL_RISK

    dv = _downside_vol(row)
    if not np.isnan(dv):
        if dv <= 0.03:
            return 0.0
        if dv <= 0.10:
            return _smooth_step(dv, 0.03, 0.10, 0.001, 0.0045)
        return 0.0045

    mdd = _max_drawdown_20d(row)
    if not np.isnan(mdd):
        mdd_abs = abs(float(mdd))
        if mdd_abs <= 0.08:
            return 0.0
        if mdd_abs <= 0.20:
            return _smooth_step(mdd_abs, 0.08, 0.20, 0.001, 0.004)
        return 0.004

    return 0.0


def _risk_intraday_hard_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0
    if _safe_bool(_pick_first(row, "intraday_hard_risk_flag", default=False), default=False):
        return W_INTRADAY_HARD_RISK
    return 0.0


def _risk_intraday_soft_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0

    risk_score = _score_0_1(
        row,
        "intraday_risk_score",
        "intraday_soft_risk_score",
        default=np.nan,
    )
    if np.isnan(risk_score):
        return 0.0
    return float(risk_score * W_INTRADAY_SOFT_RISK)


def _risk_intraday_confidence_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0

    confidence = _score_0_1(row, "intraday_confidence_score", default=np.nan)
    quality = _score_0_1(row, "intraday_quality_score", default=np.nan)

    vals = [v for v in (confidence, quality) if not np.isnan(v)]
    if not vals:
        return 0.0

    weakness = 1.0 - max(vals)
    if weakness <= 0.20:
        return 0.0
    return float(_smooth_step(weakness, 0.20, 0.70, 0.0005, W_INTRADAY_LOW_CONFIDENCE))


def _risk_intraday_missing_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0
    if _intraday_available_flag(row):
        return 0.0

    status = _intraday_status_text(row)
    if status in {"", "ok", "available", "matched", "ready", "valid"}:
        return 0.0
    return W_INTRADAY_MISSING


def _risk_late_withdraw_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0

    score = _score_0_1(row, "late_withdraw_score", default=np.nan)
    if np.isnan(score) or score <= 0.20:
        return 0.0
    return float(_smooth_step(score, 0.20, 0.80, 0.0005, W_LATE_WITHDRAW))


def _risk_reseal_weakness_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0

    score = _score_0_1(row, "reseal_score", default=np.nan)
    if np.isnan(score):
        return 0.0
    weakness = 1.0 - score
    if weakness <= 0.25:
        return 0.0
    return float(_smooth_step(weakness, 0.25, 0.80, 0.0005, W_RESEAL_WEAKNESS))


def _risk_auction_weakness_penalty(row: pd.Series) -> float:
    if not _intraday_fields_present(row):
        return 0.0

    score = _score_0_1(row, "auction_strength_score", default=np.nan)
    if np.isnan(score):
        return 0.0
    weakness = 1.0 - score
    if weakness <= 0.30:
        return 0.0
    return float(_smooth_step(weakness, 0.30, 0.85, 0.0005, W_AUCTION_WEAKNESS))


def _risk_components(row: pd.Series, regime: str = "RISK_ON") -> dict[str, float]:
    # 原始分项
    risk_regime_penalty = _risk_regime_penalty(row, regime)
    risk_open_penalty = _risk_open_penalty(row)
    risk_open_times_penalty = _risk_open_times_penalty(row)
    risk_volatility_penalty = _risk_volatility_penalty(row)
    risk_liquidity_penalty = _risk_liquidity_penalty(row)
    risk_liquidity_amount_penalty = _risk_liquidity_amount_penalty(row)
    risk_extreme_turnover_penalty = _risk_extreme_turnover_penalty(row)
    risk_theme_penalty = _risk_theme_penalty(row)
    risk_board_penalty = _risk_board_penalty(row)
    risk_seal_penalty = _risk_seal_penalty(row)
    risk_st_penalty = _risk_st_penalty(row)
    risk_tail_penalty = _risk_tail_penalty(row)
    risk_intraday_hard_penalty = _risk_intraday_hard_penalty(row)
    risk_intraday_soft_penalty = _risk_intraday_soft_penalty(row)
    risk_intraday_confidence_penalty = _risk_intraday_confidence_penalty(row)
    risk_intraday_missing_penalty = _risk_intraday_missing_penalty(row)
    risk_late_withdraw_penalty = _risk_late_withdraw_penalty(row)
    risk_reseal_weakness_penalty = _risk_reseal_weakness_penalty(row)
    risk_auction_weakness_penalty = _risk_auction_weakness_penalty(row)

    intraday_execution_raw = (
        risk_intraday_soft_penalty
        + risk_intraday_confidence_penalty
        + risk_intraday_missing_penalty
        + risk_late_withdraw_penalty
        + risk_reseal_weakness_penalty
        + risk_auction_weakness_penalty
    )
    intraday_execution_penalty = _clip(
        intraday_execution_raw,
        0.0,
        INTRADAY_EXECUTION_CAP,
    )

    # -------------------------
    # 第二轮精修：执行脆弱性聚合
    # 主风险：开板 / 炸板次数 取更强者
    # 次风险：封单弱、低换手、低成交额只做补充
    # -------------------------
    execution_fragility_primary = max(risk_open_penalty, risk_open_times_penalty)
    execution_fragility_support = (
        0.60 * risk_seal_penalty
        + 0.55 * risk_liquidity_penalty
        + 0.45 * risk_liquidity_amount_penalty
        + 0.75 * intraday_execution_penalty
    )
    execution_fragility_overlap = 0.30 * min(risk_open_penalty, risk_open_times_penalty)

    execution_fragility_raw = (
        execution_fragility_primary
        + execution_fragility_support
        + execution_fragility_overlap
    )
    execution_fragility_penalty = _clip(
        execution_fragility_raw,
        0.0,
        EXECUTION_FRAGILITY_CAP,
    )

    # -------------------------
    # 第二轮精修：拥挤/博弈聚合
    # 主项取更强者，副项只补一点
    # -------------------------
    crowding_primary = max(risk_extreme_turnover_penalty, risk_theme_penalty)
    crowding_secondary = 0.35 * min(risk_extreme_turnover_penalty, risk_theme_penalty)

    crowding_raw = crowding_primary + crowding_secondary
    crowding_penalty = _clip(
        crowding_raw,
        0.0,
        CROWDING_CAP,
    )

    # -------------------------
    # ST 与其它风险共存时，避免简单堆满
    # -------------------------
    st_penalty_effective = risk_st_penalty
    if risk_st_penalty > 0:
        st_penalty_effective = min(
            risk_st_penalty,
            max(
                0.012,
                ST_WITH_OTHERS_CAP
                - execution_fragility_penalty
                - crowding_penalty,
            ),
        )

    total = (
        risk_regime_penalty
        + execution_fragility_penalty
        + risk_volatility_penalty
        + risk_tail_penalty
        + risk_intraday_hard_penalty
        + crowding_penalty
        + risk_board_penalty
        + st_penalty_effective
    )
    risk_total_penalty = _clip(total, 0.0, RISK_PENALTY_CAP)

    return {
        # 原始分项
        "risk_regime_penalty": risk_regime_penalty,
        "risk_open_penalty": risk_open_penalty,
        "risk_open_times_penalty": risk_open_times_penalty,
        "risk_volatility_penalty": risk_volatility_penalty,
        "risk_liquidity_penalty": risk_liquidity_penalty,
        "risk_liquidity_amount_penalty": risk_liquidity_amount_penalty,
        "risk_extreme_turnover_penalty": risk_extreme_turnover_penalty,
        "risk_theme_penalty": risk_theme_penalty,
        "risk_board_penalty": risk_board_penalty,
        "risk_seal_penalty": risk_seal_penalty,
        "risk_st_penalty": risk_st_penalty,
        "risk_tail_penalty": risk_tail_penalty,
        "risk_intraday_hard_penalty": risk_intraday_hard_penalty,
        "risk_intraday_soft_penalty": risk_intraday_soft_penalty,
        "risk_intraday_confidence_penalty": risk_intraday_confidence_penalty,
        "risk_intraday_missing_penalty": risk_intraday_missing_penalty,
        "risk_late_withdraw_penalty": risk_late_withdraw_penalty,
        "risk_reseal_weakness_penalty": risk_reseal_weakness_penalty,
        "risk_auction_weakness_penalty": risk_auction_weakness_penalty,
        "intraday_execution_raw": intraday_execution_raw,
        "intraday_execution_penalty": intraday_execution_penalty,

        # 聚合分项
        "execution_fragility_primary": execution_fragility_primary,
        "execution_fragility_support": execution_fragility_support,
        "execution_fragility_overlap": execution_fragility_overlap,
        "execution_fragility_raw": execution_fragility_raw,
        "execution_fragility_penalty": execution_fragility_penalty,

        "crowding_primary": crowding_primary,
        "crowding_secondary": crowding_secondary,
        "crowding_raw": crowding_raw,
        "crowding_penalty": crowding_penalty,

        "st_penalty_effective": st_penalty_effective,

        # 总值
        "risk_total_penalty": risk_total_penalty,
    }


def _risk_penalty_from_row(row: pd.Series, regime: str = "RISK_ON") -> float:
    parts = _risk_components(row, regime=regime)
    return float(parts["risk_total_penalty"])


def risk_breakdown_row(row: pd.Series, regime: str = "RISK_ON") -> dict[str, float]:
    return _risk_components(row, regime=regime)


def risk_breakdown_df(regime: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    返回 DataFrame 级别的风险惩罚分项表。
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "risk_regime_penalty",
                "risk_open_penalty",
                "risk_open_times_penalty",
                "risk_volatility_penalty",
                "risk_liquidity_penalty",
                "risk_liquidity_amount_penalty",
                "risk_extreme_turnover_penalty",
                "risk_theme_penalty",
                "risk_board_penalty",
                "risk_seal_penalty",
                "risk_st_penalty",
                "risk_tail_penalty",
                "risk_intraday_hard_penalty",
                "risk_intraday_soft_penalty",
                "risk_intraday_confidence_penalty",
                "risk_intraday_missing_penalty",
                "risk_late_withdraw_penalty",
                "risk_reseal_weakness_penalty",
                "risk_auction_weakness_penalty",
                "intraday_execution_raw",
                "intraday_execution_penalty",
                "execution_fragility_primary",
                "execution_fragility_support",
                "execution_fragility_overlap",
                "execution_fragility_raw",
                "execution_fragility_penalty",
                "crowding_primary",
                "crowding_secondary",
                "crowding_raw",
                "crowding_penalty",
                "st_penalty_effective",
                "risk_total_penalty",
            ]
        )

    return df.apply(lambda r: pd.Series(_risk_components(r, regime=regime)), axis=1)


def risk_penalty_rule(
    regime: str,
    data: pd.DataFrame | pd.Series | None = None,
) -> pd.Series | float:
    """
    向后兼容：
    1) 老调用：risk_penalty_rule(regime) -> float
    2) 新调用：risk_penalty_rule(regime, row) -> float
    3) 新调用：risk_penalty_rule(regime, df) -> Series
    """
    regime_u = _safe_str(regime, default="RISK_ON").upper()

    if data is None:
        if regime_u in ("RISK_OFF", "OFF", "DEFENSE"):
            return float(RISK_PENALTY_ON)
        return float(RISK_PENALTY_OFF)

    if isinstance(data, pd.Series):
        return float(_risk_penalty_from_row(data, regime=regime_u))

    if isinstance(data, pd.DataFrame):
        if data.empty:
            return pd.Series(dtype=float)
        return data.apply(
            lambda r: _risk_penalty_from_row(r, regime=regime_u), axis=1
        ).astype(float)

    if regime_u in ("RISK_OFF", "OFF", "DEFENSE"):
        return float(RISK_PENALTY_ON)
    return float(RISK_PENALTY_OFF)


# ============================================================
# EV 辅助函数
# ============================================================

def ev_rule(
    p_fill: pd.Series | float,
    e_ret: pd.Series | float,
    cost: pd.Series | float,
    risk_penalty: pd.Series | float,
) -> pd.Series | float:
    """
    统一 EV 计算口径：
        EV = P_fill * E_ret - Cost - RiskPenalty
    """
    return (p_fill * e_ret) - cost - risk_penalty
