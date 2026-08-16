#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
validate_io_contract.py

目标：锁死 top10-decision 的 IO 契约（路径/命名/字段不允许悄悄改变）

核心契约：
- signals 的 dated 文件命名使用 trade_date：
  docs/signals/top10_{trade_date}.csv
- candidates_snapshot 命名使用 signal_date（本系统 signal_date == trade_date）：
  data/decision/decision_candidates_{trade_date}.csv
- weights/report/eval/execution 使用 exec_date：
  docs/weights/weights_{exec_date}.csv
  outputs/decision/decision_report_{exec_date}.md
  outputs/decision/eval_{exec_date}.json
  data/decision/decision_execution_{exec_date}.csv

本版修复：
- 允许 top10_latest.csv 为空表。
- 空 signal 表示“本轮没有正 EV 标的，不交易”，不是 IO 失败。
- 空 signal 时，trade_date 不再从 signal 行推导，而从 eval_{exec_date}.json 的 signal_date 推导。
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from top10decision.writers.io_contract import is_a_share_trading_day
except Exception:
    is_a_share_trading_day = None  # type: ignore


# =========================
# helpers
# =========================

def _fail(msg: str) -> None:
    print(f"[CONTRACT][FAIL] {msg}", file=sys.stderr)
    raise SystemExit(2)


def _warn(msg: str) -> None:
    print(f"[CONTRACT][WARN] {msg}")


def _ok(msg: str) -> None:
    print(f"[CONTRACT][OK] {msg}")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Validate Decision IO and optional semantic health contract.")
    ap.add_argument(
        "--strict-semantic",
        action="store_true",
        help="同时校验学习验收、A股交易日历、线上特征缺失等语义健康指标。",
    )
    return ap.parse_args()


def _ensure_exists(p: Path, label: str) -> None:
    if not p.exists():
        _fail(f"缺少产物：{label} -> {p.as_posix()}")
    _ok(f"存在：{label} -> {p.as_posix()}")


def _read_csv_any(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    _fail(f"无法读取 CSV：{path.as_posix()}")
    return pd.DataFrame()


def _ensure_cols(df: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        _fail(f"{label} 缺少必要列：{missing}；现有列：{list(df.columns)}")
    _ok(f"{label} 列验收通过（至少包含：{list(required)}）")


def _norm_ymd(v) -> str:
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


def _first_ymd_from_col(df: pd.DataFrame, col: str) -> str:
    if df is None or df.empty or col not in df.columns:
        return ""
    s = df[col].dropna()
    if s.empty:
        return ""
    return _norm_ymd(s.iloc[0])


def _read_eval_payload(exec_date: str) -> tuple[Optional[Path], dict]:
    if not exec_date or len(exec_date) != 8:
        return None, {}
    eval_json = Path(f"outputs/decision/eval_{exec_date}.json")
    if not eval_json.exists():
        return eval_json, {}
    try:
        payload = json.loads(eval_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return eval_json, {}
        return eval_json, payload
    except Exception:
        return eval_json, {}


def _read_json_any(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _max_numeric_col(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    s = pd.to_numeric(df[col], errors="coerce")
    return float(s.fillna(0.0).max()) if len(s) else 0.0


def _mean_numeric_col(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    s = pd.to_numeric(df[col], errors="coerce")
    return float(s.fillna(0.0).mean()) if len(s) else 0.0


def _allows_unpromoted_no_trade(
    action_plan: dict,
    *,
    picked: int,
) -> bool:
    model = action_plan.get("model") or {}
    return bool(
        action_plan.get("status_code") == "NO_TRADE_MODEL_NOT_PROMOTED"
        and action_plan.get("formal_buy_count") == 0
        and model.get("promoted") is False
        and picked <= 0
    )


def _validate_semantic_health(
    *,
    exec_date: str,
    trade_date: str,
    payload: dict,
    cand_df: pd.DataFrame,
) -> None:
    if is_a_share_trading_day is None:
        _fail("无法导入 A 股交易日历校验函数 top10decision.writers.io_contract.is_a_share_trading_day")
    if not is_a_share_trading_day(exec_date):  # type: ignore[misc]
        _fail(f"exec_date={exec_date} 不是严格 A 股交易日")
    _ok(f"exec_date={exec_date} 通过严格 A 股交易日历校验")

    picked = int(pd.to_numeric(pd.Series([payload.get("picked", 0)]), errors="coerce").fillna(0).iloc[0])
    learning_path = Path("outputs/learning/learning_acceptance_latest.json")
    learning = _read_json_any(learning_path)
    if not learning:
        _fail(f"严格语义校验需要学习验收产物：{learning_path.as_posix()}")
    if learning.get("overall_pass") is not True:
        action_plan = _read_json_any(Path("outputs/decision/action_plan_latest.json"))
        if _allows_unpromoted_no_trade(action_plan, picked=picked):
            _warn(
                "learning_acceptance overall_pass=false；"
                "V9模型未晋级且正式买入/picked均为0，Top1/Top2影子验证继续累计，按严格NO_TRADE放行"
            )
        else:
            _fail(f"learning_acceptance overall_pass != true: {learning.get('overall_pass')}")
    else:
        _ok("learning_acceptance overall_pass=true")

    for prefix in ("pfill", "eret"):
        missing_col = f"{prefix}_model_missing_feature_count"
        missing_max = _max_numeric_col(cand_df, missing_col)
        if missing_max > 0:
            _fail(f"{missing_col} max={missing_max}，线上输入缺少训练特征")

    eret_missing_ratio = _mean_numeric_col(cand_df, "eret_model_feature_missing_cell_ratio")
    if eret_missing_ratio > 0.05:
        _fail(f"eret_model_feature_missing_cell_ratio mean={eret_missing_ratio:.6f} > 0.05")

    if picked <= 0:
        _warn("picked=0：严格语义校验允许 NO_TRADE，但已显式告警")

    if _norm_ymd(trade_date) != _norm_ymd(payload.get("signal_date", trade_date)):
        _fail("strict semantic trade_date 与 eval.signal_date 不一致")


def _get_learning_required_cols() -> List[str]:
    """
    P1：learning_table 的字段不允许漂移。
    优先从 writers.filesystem 导入 LEARNING_COLUMNS（最稳），导入失败再用兜底。
    """
    must = [
        "signal_date",
        "exec_date",
        "exit_date",
        "ts_code",
        "jq_code",
        "name",
        "weight_exec",
        "filled_flag",
        "fill_rate_real",
        "buy_price",
        "sell_price",
        "ret_exec",
        "p_fill_pred",
        "e_ret_pred",
        "cost_est",
        "risk_penalty",
        "ev_pred",
        "e_ret_real",
        "ev_real",
        "regime",
        "risk_budget",
        "version",
        "generated_at_bjt",
        "commit_sha",
    ]

    try:
        from top10decision.writers.filesystem import LEARNING_COLUMNS  # type: ignore
        miss_in_schema = [c for c in must if c not in LEARNING_COLUMNS]
        if miss_in_schema:
            _fail(f"writers.filesystem.LEARNING_COLUMNS 缺少关键字段：{miss_in_schema}（请先修复 schema 定义）")
        return must
    except Exception:
        return must


# =========================
# contract check
# =========================

def main() -> int:
    args = _parse_args()

    # ---- 固定 latest
    signal_latest = Path("docs/signals/top10_latest.csv")
    weights_latest = Path("docs/weights/weights_latest.csv")
    learning_table = Path("data/decision/decision_learning.csv")

    _ensure_exists(signal_latest, "signals_latest")
    _ensure_exists(weights_latest, "weights_latest")
    _ensure_exists(learning_table, "decision_learning")

    # ---- latest CSV 字段
    sig_df = _read_csv_any(signal_latest)
    _ensure_cols(
        sig_df,
        ["trade_date", "target_trade_date", "jq_code", "target_weight", "risk_budget", "regime", "reason"],
        "signals_latest.csv",
    )

    w_df = _read_csv_any(weights_latest)
    _ensure_cols(
        w_df,
        ["exec_date", "ts_code", "name", "weight", "target_rank", "backup_rank", "ev_pred"],
        "weights_latest.csv",
    )

    learn_df = _read_csv_any(learning_table)
    _ensure_cols(
        learn_df,
        _get_learning_required_cols(),
        "decision_learning.csv",
    )

    # ---- exec_date 优先从 weights_latest 推导
    exec_date = _first_ymd_from_col(w_df, "exec_date")
    if not exec_date or len(exec_date) != 8:
        _fail(f"无法从 weights_latest.csv 推导 exec_date（得到：{exec_date}）")

    # ---- 读取 eval，用于空 signal 时回推 signal_date/trade_date
    eval_json, payload = _read_eval_payload(exec_date)

    # ---- trade_date 推导：
    # 1) 非空 signal：从 signals_latest.trade_date 读取
    # 2) 空 signal：从 eval_{exec_date}.json 的 signal_date 读取
    # 3) 再兜底：从 eval paths/candidates 文件名中尝试读取，不成功则失败
    signal_empty = bool(sig_df.empty)
    trade_date = _first_ymd_from_col(sig_df, "trade_date")

    if signal_empty:
        _ok("signals_latest.csv 是空信号表：判定为 NO_TRADE_EMPTY_SIGNAL 合法状态")
        if payload:
            trade_date = _norm_ymd(payload.get("signal_date", ""))
            if not trade_date:
                trade_date = _norm_ymd(payload.get("trade_date", ""))
        if not trade_date and payload:
            paths = payload.get("paths", {})
            if isinstance(paths, dict):
                cand_path = str(paths.get("candidates", ""))
                # expected: data/decision/decision_candidates_YYYYMMDD.csv
                stem = Path(cand_path).stem if cand_path else ""
                maybe = stem.split("_")[-1] if stem else ""
                trade_date = _norm_ymd(maybe)
    else:
        if not trade_date:
            _fail(f"signals_latest.csv 非空但无法推导 trade_date（得到：{trade_date}）")

    if not trade_date or len(trade_date) != 8:
        _fail(f"无法推导 trade_date：signal_empty={signal_empty}, signal_trade_date={_first_ymd_from_col(sig_df, 'trade_date')}, eval_signal_date={payload.get('signal_date') if payload else ''}")

    _ok(f"推导日期：trade_date={trade_date} exec_date={exec_date}")

    # ---- dated：signals 用 trade_date
    signal_dated = Path(f"docs/signals/top10_{trade_date}.csv")
    _ensure_exists(signal_dated, "signals_dated(trade_date)")
    sig_dated_df = _read_csv_any(signal_dated)
    _ensure_cols(
        sig_dated_df,
        ["trade_date", "target_trade_date", "jq_code", "target_weight", "risk_budget", "regime", "reason"],
        f"signals_dated(top10_{trade_date}.csv)",
    )
    if signal_empty and sig_dated_df.empty:
        _ok(f"signals_dated(top10_{trade_date}.csv) 也是空信号表：NO_TRADE_EMPTY_SIGNAL_PASS")

    # ---- candidates_snapshot 用 trade_date（signal_date）
    candidates_snapshot = Path(f"data/decision/decision_candidates_{trade_date}.csv")
    _ensure_exists(candidates_snapshot, "decision_candidates(trade_date)")
    cand_df = _read_csv_any(candidates_snapshot)
    _ensure_cols(
        cand_df,
        ["ts_code", "name", "p_fill_pred", "e_ret_pred", "cost_est", "risk_penalty", "ev_pred", "signal_date", "exec_date"],
        f"decision_candidates_{trade_date}.csv",
    )

    # ---- weights/report/eval/execution 用 exec_date
    weights_dated = Path(f"docs/weights/weights_{exec_date}.csv")
    report_md = Path(f"outputs/decision/decision_report_{exec_date}.md")
    eval_json = Path(f"outputs/decision/eval_{exec_date}.json")
    execution_table = Path(f"data/decision/decision_execution_{exec_date}.csv")

    _ensure_exists(weights_dated, "weights_dated(exec_date)")
    _ensure_exists(report_md, "decision_report(exec_date)")
    _ensure_exists(eval_json, "eval_json(exec_date)")
    _ensure_exists(execution_table, "decision_execution(exec_date)")

    exec_df = _read_csv_any(execution_table)
    _ensure_cols(
        exec_df,
        ["exec_date", "ts_code", "jq_code", "filled_flag", "buy_time", "buy_price", "fail_reason", "buy_slippage_bp"],
        f"decision_execution_{exec_date}.csv",
    )

    # ---- eval json 基础结构
    try:
        payload = json.loads(eval_json.read_text(encoding="utf-8"))
    except Exception:
        _fail(f"无法读取/解析 eval JSON：{eval_json.as_posix()}")

    if _norm_ymd(payload.get("exec_date", "")) != exec_date:
        _warn(f"eval.exec_date 与 weights_latest.exec_date 不一致：payload={payload.get('exec_date')} weights={exec_date}")
    else:
        _ok("eval.exec_date 与 exec_date 一致")

    if _norm_ymd(payload.get("signal_date", "")) != trade_date:
        _warn(f"eval.signal_date 与推导 trade_date 不一致：payload={payload.get('signal_date')} trade_date={trade_date}")
    else:
        _ok("eval.signal_date 与 trade_date 一致")

    if "paths" not in payload or not isinstance(payload["paths"], dict):
        _fail("eval JSON 缺少 paths 字段或格式不对")
    _ok("eval JSON 结构验收通过")

    if args.strict_semantic:
        _validate_semantic_health(
            exec_date=exec_date,
            trade_date=trade_date,
            payload=payload,
            cand_df=cand_df,
        )

    # ---- 兜底：确保 outputs/decision 至少有内容
    decision_dir = Path("outputs/decision")
    if not decision_dir.exists():
        _fail("outputs/decision 目录不存在")
    if not list(decision_dir.glob("*")):
        _fail("outputs/decision 目录为空（不应发生）")
    _ok("outputs/decision 目录非空")

    if signal_empty:
        print(f"[CONTRACT][PASS] IO 契约验收通过：trade_date={trade_date} exec_date={exec_date} status=NO_TRADE_EMPTY_SIGNAL_PASS")
    else:
        print(f"[CONTRACT][PASS] IO 契约验收通过：trade_date={trade_date} exec_date={exec_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
