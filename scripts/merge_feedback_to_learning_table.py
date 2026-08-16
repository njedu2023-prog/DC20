#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Merge JoinQuant feedback into decision_learning.csv (P1 Strongly Recommended)

输入（固定目录）：
- data/jq_feedback/fills.csv
  必须列：exec_date, ts_code, target_amount, filled_amount
  可选列：buy_price, buy_time, fail_reason

- data/jq_feedback/returns.csv
  必须列：exec_date, ts_code, sell_price, ret_exec
  可选列：exit_date

输出（固定契约）：
- data/decision/decision_learning.csv
  若不存在会自动创建（由 ensure_learning_table 负责）
  若已存在会按 (exec_date, ts_code) upsert 更新，不丢历史

原则：
- 强制使用北京时间日期 YYYYMMDD
- 以 exec_date + ts_code 为主键合并
- 允许只合并 fills（returns 晚一天再补）
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from top10decision.writers.filesystem import ensure_dirs, ensure_learning_table, LEARNING_COLUMNS
from top10decision.writers.io_contract import norm_ymd


FILL_PATH = Path("data/jq_feedback/fills.csv")
RET_PATH = Path("data/jq_feedback/returns.csv")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    return df if df is not None else pd.DataFrame()


def _require_cols(df: pd.DataFrame, cols: List[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"{name} 缺少必要列：{missing}. 当前列={list(df.columns)}")


def _clean_key_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "exec_date" in out.columns:
        out["exec_date"] = out["exec_date"].apply(lambda x: norm_ymd(str(x)))
    if "exit_date" in out.columns:
        out["exit_date"] = out["exit_date"].apply(lambda x: norm_ymd(str(x)))
    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].astype(str).str.strip()
    return out


def _to_float(s: str) -> float:
    try:
        if s is None:
            return 0.0
        x = str(s).strip()
        if x == "":
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _build_fill_patch(fills: pd.DataFrame) -> pd.DataFrame:
    """
    生成用于 upsert 的补丁表（以 exec_date, ts_code 为键）
    """
    fills = _clean_key_fields(fills)
    _require_cols(fills, ["exec_date", "ts_code", "target_amount", "filled_amount"], "fills.csv")

    f = fills.copy()
    f["target_amount"] = f["target_amount"].apply(_to_float)
    f["filled_amount"] = f["filled_amount"].apply(_to_float)

    # fill_rate_real
    def _rate(row) -> float:
        ta = float(row.get("target_amount", 0.0))
        fa = float(row.get("filled_amount", 0.0))
        return 0.0 if ta <= 0 else max(0.0, min(1.0, fa / ta))

    f["fill_rate_real"] = f.apply(_rate, axis=1)
    f["filled_flag"] = f["fill_rate_real"].apply(lambda x: 1 if x > 0 else 0)

    # 可选：buy_price / buy_time / fail_reason
    if "buy_price" not in f.columns:
        f["buy_price"] = ""
    if "buy_time" not in f.columns:
        f["buy_time"] = ""
    if "fail_reason" not in f.columns:
        f["fail_reason"] = ""

    # 只保留 learning_table 相关列（其余丢弃）
    patch_cols = ["exec_date", "ts_code", "filled_flag", "fill_rate_real", "buy_price"]
    patch = f[patch_cols].copy()
    return patch


def _build_ret_patch(rets: pd.DataFrame) -> pd.DataFrame:
    """
    生成收益补丁表（以 exec_date, ts_code 为键）
    """
    rets = _clean_key_fields(rets)
    _require_cols(rets, ["exec_date", "ts_code", "sell_price", "ret_exec"], "returns.csv")

    r = rets.copy()
    if "exit_date" not in r.columns:
        r["exit_date"] = ""  # 可空，后续可补齐 T+1 实际退出日

    # 允许 ret_exec 是百分比或小数，先原样存入（你系统自己统一口径）
    r["sell_price"] = r["sell_price"].astype(str).fillna("")
    r["ret_exec"] = r["ret_exec"].astype(str).fillna("")

    patch_cols = ["exec_date", "ts_code", "exit_date", "sell_price", "ret_exec", "e_ret_real"]
    r["e_ret_real"] = r["ret_exec"]
    patch = r[patch_cols].copy()
    return patch


def _upsert_learning_table(learning_path: str, patch: pd.DataFrame) -> Tuple[int, int]:
    """
    对 decision_learning.csv 做 upsert：
    - 主键：exec_date + ts_code
    - 已存在则更新 patch 列
    - 不存在则追加新行（其它字段留空）
    返回：(updated_rows, inserted_rows)
    """
    if patch is None or patch.empty:
        return 0, 0

    # 读取学习表（filesystem.ensure_learning_table 已保证 schema）
    base = pd.read_csv(learning_path, dtype=str, encoding="utf-8-sig")
    base = base if base is not None else pd.DataFrame(columns=LEARNING_COLUMNS)

    # 补齐列（防御）
    for c in LEARNING_COLUMNS:
        if c not in base.columns:
            base[c] = ""

    base["exec_date"] = base["exec_date"].astype(str).fillna("")
    base["ts_code"] = base["ts_code"].astype(str).fillna("")

    patch["exec_date"] = patch["exec_date"].astype(str).fillna("")
    patch["ts_code"] = patch["ts_code"].astype(str).fillna("")

    key_cols = ["exec_date", "ts_code"]

    # 建索引
    base_idx: Dict[Tuple[str, str], int] = {}
    for i, row in base[key_cols].iterrows():
        base_idx[(str(row["exec_date"]), str(row["ts_code"]))] = int(i)

    updated = 0
    inserted = 0

    for _, prow in patch.iterrows():
        k = (str(prow["exec_date"]), str(prow["ts_code"]))
        if k[0] == "" or k[1] == "":
            continue

        if k in base_idx:
            i = base_idx[k]
            for c in patch.columns:
                if c in base.columns:
                    base.at[i, c] = str(prow.get(c, ""))
            updated += 1
        else:
            new_row = {c: "" for c in LEARNING_COLUMNS}
            new_row["exec_date"] = k[0]
            new_row["ts_code"] = k[1]
            for c in patch.columns:
                if c in new_row:
                    new_row[c] = str(prow.get(c, ""))
            base = pd.concat([base, pd.DataFrame([new_row])], ignore_index=True)
            inserted += 1

    # 重排并落盘
    base = base[[c for c in LEARNING_COLUMNS]].copy()
    base.to_csv(learning_path, index=False, encoding="utf-8-sig")
    return updated, inserted


def main() -> int:
    ensure_dirs()
    learning_path = ensure_learning_table()

    fills = _read_csv(FILL_PATH)
    rets = _read_csv(RET_PATH)

    total_updated = 0
    total_inserted = 0

    if not fills.empty:
        fill_patch = _build_fill_patch(fills)
        u, i = _upsert_learning_table(learning_path, fill_patch)
        total_updated += u
        total_inserted += i
        print(f"[OK] merged fills: updated={u}, inserted={i}")
    else:
        print("[WARN] fills.csv 不存在或为空：仅跳过成交合并。")

    if not rets.empty:
        ret_patch = _build_ret_patch(rets)
        u, i = _upsert_learning_table(learning_path, ret_patch)
        total_updated += u
        total_inserted += i
        print(f"[OK] merged returns: updated={u}, inserted={i}")
    else:
        print("[WARN] returns.csv 不存在或为空：仅跳过收益合并（可在卖出日后再补）。")

    print(f"[DONE] learning_table={learning_path}, total_updated={total_updated}, total_inserted={total_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
