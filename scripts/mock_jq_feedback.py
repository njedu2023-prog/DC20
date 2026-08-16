#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Mock JoinQuant feedback for offline self-test (P1)

功能：
1) 自动寻找最新 candidates_snapshot：
   data/decision/decision_candidates_YYYYMMDD.csv
2) 生成两张模拟反馈表：
   - data/jq_feedback/fills.csv
   - data/jq_feedback/returns.csv
3) 调用 scripts/merge_feedback_to_learning_table.py 合并进 learning_table

说明：
- exec_date 默认取 candidates_snapshot 里的 exec_date；没有则用“signal_date+1”兜底（仅用于自测）
- ret_exec 用随机小波动生成，便于你验证 learning_table 合并逻辑
- 该脚本只用于工程自测，不参与实盘
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from top10decision.writers.filesystem import ensure_dirs
from top10decision.writers.io_contract import norm_ymd


CAND_DIR = Path("data/decision")
FB_DIR = Path("data/jq_feedback")
FILLS_PATH = FB_DIR / "fills.csv"
RETS_PATH = FB_DIR / "returns.csv"


def _find_latest_candidates() -> Optional[Path]:
    if not CAND_DIR.exists():
        return None
    files = sorted(CAND_DIR.glob("decision_candidates_*.csv"))
    if not files:
        return None
    return files[-1]


def _read_candidates(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    if df is None or df.empty:
        raise RuntimeError(f"candidates_snapshot 为空：{path}")
    if "ts_code" not in df.columns:
        raise RuntimeError(f"candidates_snapshot 缺少 ts_code：{path}")

    if "signal_date" not in df.columns:
        # 兜底：从文件名提取 decision_candidates_YYYYMMDD.csv
        ymd = path.stem.split("_")[-1]
        df["signal_date"] = ymd

    df["signal_date"] = df["signal_date"].apply(lambda x: norm_ymd(str(x)) or "")
    if "exec_date" in df.columns:
        df["exec_date"] = df["exec_date"].apply(lambda x: norm_ymd(str(x)) or "")
    else:
        df["exec_date"] = ""

    if "name" not in df.columns:
        df["name"] = ""

    return df


def _derive_exec_date(df: pd.DataFrame) -> str:
    # 优先用快照内 exec_date
    ed = ""
    try:
        ed = str(df["exec_date"].iloc[0]).strip()
    except Exception:
        ed = ""
    ed = norm_ymd(ed)
    if ed:
        return ed

    # 兜底：signal_date + 1（仅自测）
    sd = norm_ymd(str(df["signal_date"].iloc[0]).strip())
    if not sd:
        return ""
    try:
        ts = pd.Timestamp(sd)
        return ts.add(pd.Timedelta(days=1)).strftime("%Y%m%d")
    except Exception:
        return ""


def _gen_mock_fills(df: pd.DataFrame, exec_date: str) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code", "")).strip()
        if not ts_code:
            continue

        target_amount = 100000.0
        fill_rate = random.choice([0.0, 0.3, 0.6, 0.9, 1.0])
        filled_amount = target_amount * fill_rate

        buy_price = round(random.uniform(10, 50), 2)
        buy_time = "09:45:00" if fill_rate > 0 else ""
        fail_reason = "" if fill_rate > 0 else "MOCK_NO_LIQUIDITY"

        rows.append(
            {
                "exec_date": exec_date,
                "ts_code": ts_code,
                "target_amount": target_amount,
                "filled_amount": filled_amount,
                "buy_price": buy_price,
                "buy_time": buy_time,
                "fail_reason": fail_reason,
            }
        )
    return pd.DataFrame(rows)


def _gen_mock_returns(df: pd.DataFrame, exec_date: str) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code", "")).strip()
        if not ts_code:
            continue

        sell_price = round(random.uniform(10, 50), 2)
        ret_exec = round(random.uniform(-0.03, 0.06), 5)

        rows.append(
            {
                "exec_date": exec_date,
                "ts_code": ts_code,
                "sell_price": sell_price,
                "ret_exec": ret_exec,
            }
        )
    return pd.DataFrame(rows)


def _run_merge_script() -> None:
    # ✅ 不 import scripts（scripts 不是包），直接调用脚本，兼容本地与 Actions
    cmd = [sys.executable, "scripts/merge_feedback_to_learning_table.py"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "merge_feedback_to_learning_table.py 执行失败。\n"
            f"STDOUT:\n{r.stdout}\n\nSTDERR:\n{r.stderr}"
        )
    if r.stdout.strip():
        print(r.stdout.strip())


def main() -> int:
    random.seed(42)
    ensure_dirs()
    FB_DIR.mkdir(parents=True, exist_ok=True)

    cand_path = _find_latest_candidates()
    if cand_path is None:
        raise RuntimeError("找不到 candidates_snapshot：data/decision/decision_candidates_*.csv。请先跑一次 scripts/run_v2.py。")

    df = _read_candidates(cand_path)
    exec_date = _derive_exec_date(df)
    if not exec_date:
        raise RuntimeError("无法推导 exec_date（自测也失败）。请检查 candidates_snapshot 是否含 exec_date/signal_date。")

    fills = _gen_mock_fills(df, exec_date)
    rets = _gen_mock_returns(df, exec_date)

    fills.to_csv(FILLS_PATH, index=False, encoding="utf-8-sig")
    rets.to_csv(RETS_PATH, index=False, encoding="utf-8-sig")

    print(f"[OK] wrote mock fills: {FILLS_PATH} rows={len(fills)}")
    print(f"[OK] wrote mock returns: {RETS_PATH} rows={len(rets)}")

    _run_merge_script()
    print("[DONE] mock feedback merged into learning_table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
