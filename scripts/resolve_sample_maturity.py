#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
resolve_sample_maturity.py

职责：
- 将“样本成熟度 / 训练日期机制”独立成正式基础设施。
- 给定 current_run_date、raw 快照目录、候选 trade_date。
- 输出每个 trade_date 的：
  trade_date
  exec_date
  target_date
  sample_maturity
  PFILL_READY
  ERET_READY
  FULLY_READY

核心规则：
- exec_date = trade_date 之后第 1 个 A 股交易日
- target_date = trade_date 之后第 2 个 A 股交易日
- PFILL_READY: exec_date 已有 raw 快照，且 exec_date <= current_run_date
- ERET_READY : target_date 已有 raw 快照，且 target_date <= current_run_date
- FULLY_READY = PFILL_READY and ERET_READY

重要修复：
- 旧版用 raw 快照中“真实存在的日期序列”直接推断 exec_date / target_date。
  这会在节假日前后出错：例如 raw 只有到 20260430 时，系统无法预先解析
  20260430 -> 20260506 -> 20260507。
- 新版将“交易日链解析”和“数据是否成熟”拆开：
  1) exec_date / target_date 用交易日历解析；
  2) READY 状态才用 raw 是否存在判断。
- 因此，在 20260506 凌晨，即便 raw 尚无 20260506 / 20260507，
  也能正确输出：
  20260430 -> 20260506 -> 20260507，但 PFILL_READY=0, ERET_READY=0。

交易日历来源：
- 只接受显式包含 cal_date/is_open 的交易所日历快照。
- 默认读取 data/market/trade_cal_sse.csv；也可用 --trade-calendar-file 覆盖。
- raw 目录只决定标签是否成熟，永远不能补充或推断交易日。
- 任一候选日期到 T+1 退出日期之间存在日历缺口时直接失败，禁止工作日猜测。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADE_CALENDAR_FILE = PROJECT_ROOT / "data" / "market" / "trade_cal_sse.csv"


# =========================
# 数据结构
# =========================

@dataclass(frozen=True)
class SampleMaturityRow:
    trade_date: str
    exec_date: str
    target_date: str
    sample_maturity: str
    PFILL_READY: int
    ERET_READY: int
    FULLY_READY: int


# =========================
# 基础工具
# =========================

def is_yyyymmdd(s: str) -> bool:
    return isinstance(s, str) and len(s) == 8 and s.isdigit()


def norm_date_str(s: object) -> Optional[str]:
    """
    归一化日期到 YYYYMMDD。
    允许输入：
    - 20260307
    - "20260307"
    - "2026-03-07"
    - "2026/03/07"
    """
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None

    text = text.replace("-", "").replace("/", "")
    if is_yyyymmdd(text):
        return text
    return None


def parse_yyyymmdd(s: str) -> date:
    d = norm_date_str(s)
    if not d:
        raise ValueError(f"非法日期：{s!r}")
    return datetime.strptime(d, "%Y%m%d").date()


def fmt_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def sort_trade_dates(dates: Iterable[str]) -> List[str]:
    uniq = {d for d in dates if is_yyyymmdd(d)}
    return sorted(uniq)


def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# =========================
# raw 快照交易日解析
# =========================

def discover_raw_trade_dates(raw_root: Path) -> List[str]:
    """
    从 raw 根目录发现所有已有行情快照日期。

    兼容目录形态：
    - data/market/raw/2026/20260307/
    - data/market/raw/20260307/
    - data/market/raw/2026/20260307/*.csv

    注意：
    raw 日期只代表“已有数据”，不能再被当成完整交易日历。
    """
    if not raw_root.exists():
        raise FileNotFoundError(f"raw_root 不存在：{raw_root}")

    found: Set[str] = set()

    for p in raw_root.rglob("*"):
        if not p.exists():
            continue

        name = p.name
        if p.is_dir() and is_yyyymmdd(name):
            found.add(name)
            continue

        parent_name = p.parent.name if p.parent else ""
        if is_yyyymmdd(parent_name):
            found.add(parent_name)

    dates = sorted(found)
    if not dates:
        raise RuntimeError(f"未在 raw_root 下发现任何交易日目录：{raw_root}")

    return dates


# =========================
# 交易日历解析
# =========================

def _closed_dates_from_ranges(ranges: Sequence[tuple[str, str]]) -> Set[str]:
    out: Set[str] = set()
    for start_s, end_s in ranges:
        start = parse_yyyymmdd(start_s)
        end = parse_yyyymmdd(end_s)
        for d in daterange(start, end):
            out.add(fmt_yyyymmdd(d))
    return out


def builtin_a_share_closed_dates() -> Set[str]:
    """
    内置 A 股 2026 主要休市日 fallback。

    该 fallback 的目的不是替代官方交易日历，而是在 GitHub Actions 的
    calendar 文件覆盖不完整时，向未来补足交易日链，至少能正确解析
    20260430 -> 20260506 -> 20260507 这类节假日跨越链。

    如果仓库提供正式交易日历文件，最终日历会合并：外部日历 + fallback + raw 已有日期。
    """
    closed_ranges = [
        # 2026 元旦
        ("20260101", "20260103"),
        # 2026 春节
        ("20260215", "20260223"),
        # 2026 清明节
        ("20260404", "20260406"),
        # 2026 劳动节：关键修复区间
        ("20260501", "20260505"),
        # 2026 端午节
        ("20260619", "20260621"),
        # 2026 中秋节
        ("20260925", "20260927"),
        # 2026 国庆节 fallback
        ("20261001", "20261008"),
    ]
    return _closed_dates_from_ranges(closed_ranges)


def load_trade_calendar_from_csv(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"trade calendar csv 不存在：{path}")

    out: List[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError(f"交易日历 csv 表头为空：{path}")

        # 常见列名兼容
        date_col = None
        for cand in ("trade_date", "cal_date", "date", "day"):
            if cand in reader.fieldnames:
                date_col = cand
                break
        if date_col is None:
            # 若只有一列，则用第一列
            if len(reader.fieldnames) == 1:
                date_col = reader.fieldnames[0]
            else:
                raise RuntimeError(
                    f"交易日历 csv 未找到日期列，实际列为：{reader.fieldnames}"
                )

        # 可选 is_open 列：若存在，仅保留开市行
        is_open_col = None
        for cand in ("is_open", "open", "is_trade", "trade"):
            if cand in reader.fieldnames:
                is_open_col = cand
                break

        for row in reader:
            if is_open_col is not None:
                flag = str(row.get(is_open_col, "")).strip().lower()
                if flag in {"0", "false", "no", "n", "closed", "休市"}:
                    continue

            d = norm_date_str(row.get(date_col))
            if d:
                out.append(d)

    return sort_trade_dates(out)


def load_trade_calendar_from_json(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"trade calendar json 不存在：{path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []

    def consume_item(item: object) -> None:
        if isinstance(item, dict):
            if "is_open" in item:
                flag = str(item.get("is_open", "")).strip().lower()
                if flag in {"0", "false", "no", "n", "closed", "休市"}:
                    return
            for key in ("trade_date", "cal_date", "date", "day"):
                d = norm_date_str(item.get(key))
                if d:
                    out.append(d)
                    return
        else:
            d = norm_date_str(item)
            if d:
                out.append(d)

    if isinstance(data, list):
        for item in data:
            consume_item(item)
    elif isinstance(data, dict):
        vals = (
            data.get("trade_dates")
            or data.get("calendar")
            or data.get("rows")
            or data.get("data")
            or []
        )
        if isinstance(vals, list):
            for item in vals:
                consume_item(item)
    else:
        raise RuntimeError(f"不支持的交易日历 json 结构：{path}")

    return sort_trade_dates(out)


def load_trade_calendar_file(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_trade_calendar_from_csv(path)
    if suffix == ".json":
        return load_trade_calendar_from_json(path)

    # txt / md / 无后缀：逐行日期
    if not path.exists():
        raise FileNotFoundError(f"trade calendar file 不存在：{path}")
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        d = norm_date_str(line.strip())
        if d:
            out.append(d)
    return sort_trade_dates(out)


def load_strict_trade_calendar(path: Path) -> dict[str, bool]:
    """Load a complete exchange calendar with an explicit open/closed flag."""
    if not path.exists():
        raise FileNotFoundError(f"严格 A 股交易日历不存在：{path}")
    if path.suffix.lower() != ".csv":
        raise RuntimeError("严格 A 股交易日历必须是包含 cal_date/is_open 的 CSV")

    records: dict[str, bool] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        date_col = next((c for c in ("cal_date", "trade_date", "date") if c in fields), None)
        if date_col is None or "is_open" not in fields:
            raise RuntimeError(
                f"严格 A 股交易日历必须包含 cal_date/is_open，实际列={sorted(fields)}"
            )
        for line_no, row in enumerate(reader, start=2):
            cal_date = norm_date_str(row.get(date_col))
            if not cal_date:
                raise RuntimeError(f"交易日历第 {line_no} 行日期非法：{row.get(date_col)!r}")
            raw_flag = str(row.get("is_open", "")).strip().lower()
            if raw_flag in {"1", "true", "yes", "y", "open", "交易"}:
                is_open = True
            elif raw_flag in {"0", "false", "no", "n", "closed", "休市"}:
                is_open = False
            else:
                raise RuntimeError(f"交易日历第 {line_no} 行 is_open 非法：{raw_flag!r}")
            if cal_date in records and records[cal_date] != is_open:
                raise RuntimeError(f"交易日历日期重复且状态冲突：{cal_date}")
            records[cal_date] = is_open

    if not records:
        raise RuntimeError(f"严格 A 股交易日历为空：{path}")
    return records


def validate_strict_calendar_coverage(
    records: dict[str, bool],
    candidate_trade_dates: Sequence[str],
    current_run_date: str,
    max_scan_days: int = 40,
) -> None:
    """Fail closed unless every natural day through each D/T/T+1 chain is explicit."""
    if current_run_date not in records:
        raise RuntimeError(f"交易日历未覆盖 current_run_date={current_run_date}")

    for trade_date in sort_trade_dates(candidate_trade_dates):
        if trade_date not in records:
            raise RuntimeError(f"交易日历未覆盖候选 signal_date={trade_date}")
        if not records[trade_date]:
            raise RuntimeError(f"候选 signal_date={trade_date} 不是 A 股交易日")

        cursor = parse_yyyymmdd(trade_date) + timedelta(days=1)
        open_days = 0
        for _ in range(max_scan_days):
            ymd = fmt_yyyymmdd(cursor)
            if ymd not in records:
                raise RuntimeError(
                    f"交易日历在 {ymd} 存在缺口，无法严格解析 signal_date={trade_date} 的 T/T+1"
                )
            if records[ymd]:
                open_days += 1
                if open_days == 2:
                    break
            cursor += timedelta(days=1)
        else:
            raise RuntimeError(
                f"signal_date={trade_date} 后 {max_scan_days} 天内无法解析两个 A 股交易日"
            )


def build_builtin_trade_calendar(
    anchor_dates: Sequence[str],
    forward_days: int = 120,
    backward_days: int = 30,
) -> List[str]:
    """
    用周末规则 + 内置休市日生成 fallback 交易日历。
    raw 已有日期后续会并入，避免历史样本丢失。
    """
    anchors = sort_trade_dates(anchor_dates)
    if not anchors:
        raise RuntimeError("无法生成交易日历：没有任何 anchor date")

    start = parse_yyyymmdd(anchors[0]) - timedelta(days=max(0, backward_days))
    end = parse_yyyymmdd(anchors[-1]) + timedelta(days=max(1, forward_days))

    closed = builtin_a_share_closed_dates()
    open_days: List[str] = []
    for d in daterange(start, end):
        s = fmt_yyyymmdd(d)
        # 周一到周五，且不在休市日
        if d.weekday() < 5 and s not in closed:
            open_days.append(s)

    return sort_trade_dates(open_days)


def resolve_trade_calendar(
    raw_trade_dates: Sequence[str],
    candidate_trade_dates: Sequence[str],
    current_run_date: str,
    trade_calendar_file: Optional[Path] = None,
    calendar_forward_days: int = 120,
) -> List[str]:
    """
    读取严格交易所日历。raw 日期和工作日规则都不能扩展该日历。
    """
    del raw_trade_dates, calendar_forward_days
    calendar_path = trade_calendar_file or DEFAULT_TRADE_CALENDAR_FILE
    records = load_strict_trade_calendar(calendar_path)
    validate_strict_calendar_coverage(records, candidate_trade_dates, current_run_date)
    return sorted(day for day, is_open in records.items() if is_open)


def next_n_trade_date(trade_dates: Sequence[str], base_date: str, n_after: int) -> str:
    """
    返回 base_date 之后第 n_after 个交易日。
    n_after=1 -> 下一个交易日
    n_after=2 -> 下下个交易日

    若不存在，返回空字符串。
    """
    if n_after <= 0:
        raise ValueError("n_after 必须 >= 1")

    dates = sort_trade_dates(trade_dates)
    try:
        idx = dates.index(base_date)
    except ValueError:
        return ""

    target_idx = idx + n_after
    if target_idx >= len(dates):
        return ""
    return dates[target_idx]


# =========================
# 候选 trade_date 读取
# =========================

def load_trade_dates_from_csv(path: Path, column: str = "trade_date") -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"trade_dates csv 不存在：{path}")

    rows: List[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError(f"csv 表头为空：{path}")
        if column not in reader.fieldnames:
            raise RuntimeError(
                f"csv 缺少指定列：{column}，实际列为：{reader.fieldnames}"
            )
        for row in reader:
            d = norm_date_str(row.get(column))
            if d:
                rows.append(d)

    return sort_trade_dates(rows)


def load_trade_dates_from_json(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"trade_dates json 不存在：{path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                d = norm_date_str(item.get("trade_date"))
                if d:
                    out.append(d)
            else:
                d = norm_date_str(item)
                if d:
                    out.append(d)

    elif isinstance(data, dict):
        vals = data.get("trade_dates", [])
        if isinstance(vals, list):
            for item in vals:
                if isinstance(item, dict):
                    d = norm_date_str(item.get("trade_date"))
                    if d:
                        out.append(d)
                else:
                    d = norm_date_str(item)
                    if d:
                        out.append(d)
    else:
        raise RuntimeError(f"不支持的 json 结构：{path}")

    return sort_trade_dates(out)


def parse_trade_dates_args(
    trade_dates_inline: Optional[str],
    trade_dates_file: Optional[Path],
) -> List[str]:
    """
    候选 trade_date 输入优先级：
    1. --trade-dates "20260301,20260302"
    2. --trade-dates-file xxx.csv/json/txt
    """
    out: List[str] = []

    if trade_dates_inline:
        for part in trade_dates_inline.split(","):
            d = norm_date_str(part)
            if d:
                out.append(d)

    if trade_dates_file:
        suffix = trade_dates_file.suffix.lower()
        if suffix == ".csv":
            out.extend(load_trade_dates_from_csv(trade_dates_file))
        elif suffix == ".json":
            out.extend(load_trade_dates_from_json(trade_dates_file))
        else:
            if not trade_dates_file.exists():
                raise FileNotFoundError(f"trade_dates file 不存在：{trade_dates_file}")
            for line in trade_dates_file.read_text(encoding="utf-8").splitlines():
                d = norm_date_str(line.strip())
                if d:
                    out.append(d)

    out = sort_trade_dates(out)
    if not out:
        raise RuntimeError("未提供任何有效的 candidate trade_date")

    return out


# =========================
# 核心解析逻辑
# =========================

def compute_maturity_label(
    pfill_ready: bool,
    eret_ready: bool,
) -> str:
    if pfill_ready and eret_ready:
        return "FULLY_READY"
    if pfill_ready:
        return "PFILL_READY"
    return "UNREADY"


def resolve_sample_maturity_rows(
    current_run_date: str,
    all_trade_dates_from_raw: Sequence[str],
    candidate_trade_dates: Sequence[str],
    trade_calendar_dates: Sequence[str],
) -> List[SampleMaturityRow]:
    """
    基于交易日历解析样本成熟度。

    关键分离：
    - exec_date / target_date：来自交易日历。
    - READY：来自 raw 是否已有对应交易日快照 + current_run_date。
    """
    raw_dates = set(sort_trade_dates(all_trade_dates_from_raw))
    calendar_dates = sort_trade_dates(trade_calendar_dates)
    rows: List[SampleMaturityRow] = []

    for trade_date in sort_trade_dates(candidate_trade_dates):
        if trade_date not in calendar_dates:
            rows.append(
                SampleMaturityRow(
                    trade_date=trade_date,
                    exec_date="",
                    target_date="",
                    sample_maturity="UNREADY",
                    PFILL_READY=0,
                    ERET_READY=0,
                    FULLY_READY=0,
                )
            )
            continue

        exec_date = next_n_trade_date(calendar_dates, trade_date, 1)
        target_date = next_n_trade_date(calendar_dates, trade_date, 2)

        # 注意：这里必须要求 raw 中已有对应日期数据。
        # 不能仅因为 exec_date/target_date <= current_run_date 就标记成熟。
        pfill_ready = bool(exec_date) and exec_date <= current_run_date and exec_date in raw_dates
        eret_ready = bool(target_date) and target_date <= current_run_date and target_date in raw_dates
        fully_ready = pfill_ready and eret_ready

        rows.append(
            SampleMaturityRow(
                trade_date=trade_date,
                exec_date=exec_date,
                target_date=target_date,
                sample_maturity=compute_maturity_label(pfill_ready, eret_ready),
                PFILL_READY=1 if pfill_ready else 0,
                ERET_READY=1 if eret_ready else 0,
                FULLY_READY=1 if fully_ready else 0,
            )
        )

    return rows


# =========================
# 输出
# =========================

def write_csv(rows: Sequence[SampleMaturityRow], output_csv: Path) -> None:
    ensure_parent_dir(output_csv)
    fieldnames = [
        "trade_date",
        "exec_date",
        "target_date",
        "sample_maturity",
        "PFILL_READY",
        "ERET_READY",
        "FULLY_READY",
    ]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(
    rows: Sequence[SampleMaturityRow],
    output_json: Path,
    current_run_date: str,
    raw_root: str,
    trade_calendar_file: str,
    trade_calendar_count: int,
) -> None:
    ensure_parent_dir(output_json)
    payload = {
        "current_run_date": current_run_date,
        "raw_root": raw_root,
        "trade_calendar_file": trade_calendar_file,
        "trade_calendar_count": trade_calendar_count,
        "count": len(rows),
        "rows": [asdict(r) for r in rows],
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_summary(rows: Sequence[SampleMaturityRow]) -> None:
    total = len(rows)
    pfill_ready = sum(r.PFILL_READY for r in rows)
    eret_ready = sum(r.ERET_READY for r in rows)
    fully_ready = sum(r.FULLY_READY for r in rows)

    print(
        json.dumps(
            {
                "total": total,
                "PFILL_READY": pfill_ready,
                "ERET_READY": eret_ready,
                "FULLY_READY": fully_ready,
            },
            ensure_ascii=False,
        )
    )


# =========================
# CLI
# =========================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "解析样本成熟度（trade_date / exec_date / target_date / "
            "PFILL_READY / ERET_READY / FULLY_READY）"
        )
    )

    parser.add_argument(
        "--current-run-date",
        required=True,
        help="当前运行日，YYYYMMDD",
    )
    parser.add_argument(
        "--raw-root",
        default="data/market/raw",
        help="raw 快照根目录，默认 data/market/raw",
    )

    parser.add_argument(
        "--trade-calendar-file",
        default="",
        help=(
            "严格 A 股交易日历 CSV（必须含 cal_date/is_open）；"
            "默认 data/market/trade_cal_sse.csv。"
        ),
    )
    parser.add_argument(
        "--calendar-forward-days",
        type=int,
        default=120,
        help="保留参数，仅用于旧命令兼容；严格日历模式不会推断未来日期",
    )

    parser.add_argument(
        "--trade-dates",
        default="",
        help='候选 trade_date，逗号分隔，如 "20260301,20260302"',
    )
    parser.add_argument(
        "--trade-dates-file",
        default="",
        help="候选 trade_date 文件路径，支持 csv/json/txt",
    )
    parser.add_argument(
        "--trade-date-column",
        default="trade_date",
        help="当 trade_dates_file 为 csv 时，读取的列名，默认 trade_date",
    )

    parser.add_argument(
        "--output-csv",
        default="data/market/sample_maturity_latest.csv",
        help="输出 csv 路径",
    )
    parser.add_argument(
        "--output-json",
        default="data/market/sample_maturity_latest.json",
        help="输出 json 路径",
    )

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    current_run_date = norm_date_str(args.current_run_date)
    if not current_run_date:
        raise RuntimeError("--current-run-date 非法，必须是 YYYYMMDD")

    raw_root = Path(args.raw_root)
    trade_dates_file = Path(args.trade_dates_file) if args.trade_dates_file else None
    trade_calendar_file = (
        Path(args.trade_calendar_file) if args.trade_calendar_file else DEFAULT_TRADE_CALENDAR_FILE
    )

    raw_trade_dates = discover_raw_trade_dates(raw_root)

    candidate_trade_dates: List[str] = []
    if trade_dates_file and trade_dates_file.suffix.lower() == ".csv":
        candidate_trade_dates.extend(
            load_trade_dates_from_csv(trade_dates_file, column=args.trade_date_column)
        )
        if args.trade_dates:
            candidate_trade_dates.extend(
                [d for d in (norm_date_str(x) for x in args.trade_dates.split(",")) if d]
            )
        candidate_trade_dates = sort_trade_dates(candidate_trade_dates)
        if not candidate_trade_dates:
            raise RuntimeError("未提供任何有效的 candidate trade_date")
    else:
        candidate_trade_dates = parse_trade_dates_args(
            trade_dates_inline=args.trade_dates,
            trade_dates_file=trade_dates_file,
        )

    trade_calendar_dates = resolve_trade_calendar(
        raw_trade_dates=raw_trade_dates,
        candidate_trade_dates=candidate_trade_dates,
        current_run_date=current_run_date,
        trade_calendar_file=trade_calendar_file,
        calendar_forward_days=args.calendar_forward_days,
    )

    rows = resolve_sample_maturity_rows(
        current_run_date=current_run_date,
        all_trade_dates_from_raw=raw_trade_dates,
        candidate_trade_dates=candidate_trade_dates,
        trade_calendar_dates=trade_calendar_dates,
    )

    output_csv = Path(args.output_csv)
    output_json = Path(args.output_json)

    write_csv(rows, output_csv)
    write_json(
        rows,
        output_json=output_json,
        current_run_date=current_run_date,
        raw_root=str(raw_root),
        trade_calendar_file=str(trade_calendar_file),
        trade_calendar_count=len(trade_calendar_dates),
    )
    print_summary(rows)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[resolve_sample_maturity] ERROR: {e}", file=sys.stderr)
        raise
