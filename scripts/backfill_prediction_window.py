#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
历史预测链回放脚本
scripts/backfill_prediction_window.py

目标：
1. 按日期范围回放 top10-decision 的历史预测链
2. 将 a-top10 的历史预测源文件同步到 top10-decision/data/pred/archive/
3. 同步并归档 latest 快照：data/pred/pred_source_latest.csv
4. 可选调用：
   - scripts/sync_market_raw.py
   - scripts/build_market_fs.py
   - scripts/run_v2.py
5. 产出回放执行清单，便于验收与排查

核心设计原则：
- 不硬绑定你现有各脚本的 CLI 细节
- 对 sync/build/run 采用“命令模板”方式调用，最大化兼容现有工程
- 预测源同步逻辑内置在本脚本中，先把 dated archive 建起来
- 默认优先使用 a-top10/outputs/decisio/pred_decisio_{trade_date}.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence


# =========================
# 常量
# =========================

DEFAULT_PRED_URL_TEMPLATES = [
    "https://raw.githubusercontent.com/njedu2023-prog/a-top10/main/outputs/decisio/pred_decisio_{trade_date}.csv",
    "https://raw.githubusercontent.com/njedu2023-prog/a-top10/main/outputs/learning/pred_top10_{trade_date}.csv",
]

DEFAULT_SYNC_MARKET_CMD = "python scripts/sync_market_raw.py --trade-date {trade_date}"
DEFAULT_BUILD_FS_CMD = "python scripts/build_market_fs.py --trade-date {trade_date}"
DEFAULT_RUN_CMD = "python scripts/run_v2.py --trade-date {trade_date}"


# =========================
# 数据结构
# =========================

@dataclass
class DayResult:
    trade_date: str
    pred_url_used: str = ""
    pred_archive_path: str = ""
    pred_latest_path: str = ""
    pred_sync_ok: bool = False
    market_sync_ok: bool = False
    fs_build_ok: bool = False
    run_ok: bool = False
    skipped_market_sync: bool = False
    skipped_fs_build: bool = False
    skipped_run: bool = False
    error_stage: str = ""
    error_message: str = ""
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0


# =========================
# 工具函数
# =========================

def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def parse_yyyymmdd(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y%m%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"无效日期格式：{s}，要求 YYYYMMDD") from e


def iter_dates(start_date: str, end_date: str, trade_calendar_file: Path) -> List[str]:
    start_dt = parse_yyyymmdd(start_date)
    end_dt = parse_yyyymmdd(end_date)
    if end_dt < start_dt:
        raise ValueError(f"end_date({end_date}) 不能早于 start_date({start_date})")

    if not trade_calendar_file.exists():
        raise FileNotFoundError(f"严格 A 股交易日历不存在：{trade_calendar_file}")
    with trade_calendar_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        if not {"cal_date", "is_open"}.issubset(fields):
            raise RuntimeError(
                f"严格 A 股交易日历必须包含 cal_date/is_open：{trade_calendar_file}"
            )
        calendar: dict[str, bool] = {}
        for row in reader:
            day = "".join(ch for ch in str(row.get("cal_date") or "") if ch.isdigit())[:8]
            flag = str(row.get("is_open") or "").strip()
            if len(day) != 8 or flag not in {"0", "1"}:
                raise RuntimeError(f"交易日历存在非法行：{row}")
            calendar[day] = flag == "1"

    out: List[str] = []
    cur = start_dt
    while cur <= end_dt:
        day = cur.strftime("%Y%m%d")
        if day not in calendar:
            raise RuntimeError(f"交易日历未覆盖回放日期：{day}")
        if calendar[day]:
            out.append(day)
        cur += timedelta(days=1)
    return out


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_text_from_url(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; top10-decision-backfill/1.0)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8-sig")


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", newline="")


def run_shell_command(cmd: str, cwd: Path, env: Optional[dict] = None) -> None:
    print(f"[CMD] {cmd}")
    subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        env=env,
        check=True,
    )


def detect_repo_root(cli_repo_root: Optional[str]) -> Path:
    if cli_repo_root:
        return Path(cli_repo_root).resolve()

    here = Path(__file__).resolve()
    # scripts/backfill_prediction_window.py -> repo root = parent.parent
    guessed = here.parent.parent
    return guessed


def format_cmd_template(template: str, trade_date: str) -> str:
    return template.format(trade_date=trade_date, ymd=trade_date)


def append_manifest_csv(path: Path, rows: Sequence[DayResult]) -> None:
    ensure_dir(path.parent)
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(DayResult(trade_date="").__dict__.keys())
    file_exists = path.exists()

    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_manifest_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def try_fetch_prediction_source(
    trade_date: str,
    url_templates: Sequence[str],
    timeout: int,
) -> tuple[str, str]:
    """
    返回：(used_url, text)
    """
    errors = []
    for tpl in url_templates:
        url = tpl.format(trade_date=trade_date, ymd=trade_date)
        try:
            text = read_text_from_url(url, timeout=timeout)
            if not text.strip():
                errors.append(f"{url} -> 空文件")
                continue
            if "," not in text and "\t" not in text:
                errors.append(f"{url} -> 内容看起来不像 CSV")
                continue
            return url, text
        except urllib.error.HTTPError as e:
            errors.append(f"{url} -> HTTP {e.code}")
        except urllib.error.URLError as e:
            errors.append(f"{url} -> URL 错误: {e}")
        except Exception as e:
            errors.append(f"{url} -> {type(e).__name__}: {e}")

    raise RuntimeError(
        "所有预测源候选地址都失败了：\n" + "\n".join(errors)
    )


def sync_prediction_source(
    repo_root: Path,
    trade_date: str,
    url_templates: Sequence[str],
    timeout: int,
    archive_subdir: str,
) -> tuple[str, Path, Path]:
    """
    下载指定 trade_date 的预测源，落库为：
    - archive dated 文件
    - latest 文件
    """
    used_url, text = try_fetch_prediction_source(
        trade_date=trade_date,
        url_templates=url_templates,
        timeout=timeout,
    )

    pred_root = repo_root / "data" / "pred"
    archive_dir = pred_root / archive_subdir
    ensure_dir(archive_dir)

    archive_path = archive_dir / f"pred_source_{trade_date}.csv"
    latest_path = pred_root / "pred_source_latest.csv"

    write_text(archive_path, text)
    shutil.copyfile(archive_path, latest_path)

    return used_url, archive_path, latest_path


# =========================
# 主流程
# =========================

def process_one_day(
    repo_root: Path,
    trade_date: str,
    url_templates: Sequence[str],
    timeout: int,
    archive_subdir: str,
    sync_market_cmd_template: str,
    build_fs_cmd_template: str,
    run_cmd_template: str,
    skip_market_sync: bool,
    skip_build_fs: bool,
    skip_run: bool,
) -> DayResult:
    started = time.time()
    result = DayResult(
        trade_date=trade_date,
        started_at=utc_now_iso(),
        skipped_market_sync=skip_market_sync,
        skipped_fs_build=skip_build_fs,
        skipped_run=skip_run,
    )

    try:
        # 1) 同步预测源并落库 archive + latest
        used_url, archive_path, latest_path = sync_prediction_source(
            repo_root=repo_root,
            trade_date=trade_date,
            url_templates=url_templates,
            timeout=timeout,
            archive_subdir=archive_subdir,
        )
        result.pred_url_used = used_url
        result.pred_archive_path = str(archive_path.relative_to(repo_root))
        result.pred_latest_path = str(latest_path.relative_to(repo_root))
        result.pred_sync_ok = True
        print(f"[OK] pred synced: {trade_date} -> {result.pred_archive_path}")

        # 2) 同步 market raw
        if not skip_market_sync:
            cmd = format_cmd_template(sync_market_cmd_template, trade_date)
            run_shell_command(cmd, cwd=repo_root)
            result.market_sync_ok = True
            print(f"[OK] market raw synced: {trade_date}")
        else:
            print(f"[SKIP] market sync: {trade_date}")

        # 3) 构建 FS
        if not skip_build_fs:
            cmd = format_cmd_template(build_fs_cmd_template, trade_date)
            run_shell_command(cmd, cwd=repo_root)
            result.fs_build_ok = True
            print(f"[OK] market fs built: {trade_date}")
        else:
            print(f"[SKIP] fs build: {trade_date}")

        # 4) 跑 decision 主引擎
        if not skip_run:
            cmd = format_cmd_template(run_cmd_template, trade_date)
            run_shell_command(cmd, cwd=repo_root)
            result.run_ok = True
            print(f"[OK] run_v2 done: {trade_date}")
        else:
            print(f"[SKIP] run_v2: {trade_date}")

    except subprocess.CalledProcessError as e:
        result.error_stage = "subprocess"
        result.error_message = f"命令失败，exit_code={e.returncode}"
    except Exception as e:
        result.error_stage = "python"
        result.error_message = f"{type(e).__name__}: {e}"

    result.finished_at = utc_now_iso()
    result.elapsed_seconds = round(time.time() - started, 3)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="按日期范围回放 top10-decision 历史预测链"
    )
    p.add_argument("--start-date", required=True, help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", required=True, help="结束日期 YYYYMMDD")
    p.add_argument(
        "--repo-root",
        default="",
        help="仓库根目录；默认自动推断为当前脚本上两级目录",
    )
    p.add_argument(
        "--archive-subdir",
        default="archive",
        help="预测源历史归档子目录，默认 data/pred/archive/",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="下载预测源超时秒数，默认 30",
    )
    p.add_argument(
        "--weekdays-only",
        action="store_true",
        help="保留参数，仅用于旧命令兼容；当前始终严格按 A 股交易日历处理",
    )
    p.add_argument(
        "--trade-calendar-file",
        default="",
        help="严格 A 股交易日历 CSV；默认 data/market/trade_cal_sse.csv",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        help="单日失败后继续处理后续日期",
    )

    # 预测源 URL 模板：允许重复传入多个
    p.add_argument(
        "--pred-url-template",
        action="append",
        default=[],
        help=(
            "预测源 URL 模板，可重复传入。支持 {trade_date} / {ymd} 占位符。"
            "不传时使用默认候选模板。"
        ),
    )

    # 可选阶段开关
    p.add_argument("--skip-market-sync", action="store_true", help="跳过 sync_market_raw")
    p.add_argument("--skip-build-fs", action="store_true", help="跳过 build_market_fs")
    p.add_argument("--skip-run", action="store_true", help="跳过 run_v2")

    # 命令模板
    p.add_argument(
        "--sync-market-cmd",
        default=DEFAULT_SYNC_MARKET_CMD,
        help=f"同步 raw 命令模板，默认：{DEFAULT_SYNC_MARKET_CMD}",
    )
    p.add_argument(
        "--build-fs-cmd",
        default=DEFAULT_BUILD_FS_CMD,
        help=f"构建 FS 命令模板，默认：{DEFAULT_BUILD_FS_CMD}",
    )
    p.add_argument(
        "--run-cmd",
        default=DEFAULT_RUN_CMD,
        help=f"执行主引擎命令模板，默认：{DEFAULT_RUN_CMD}",
    )

    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    repo_root = detect_repo_root(args.repo_root)
    trade_calendar_file = (
        Path(args.trade_calendar_file).resolve()
        if args.trade_calendar_file
        else repo_root / "data" / "market" / "trade_cal_sse.csv"
    )
    dates = iter_dates(
        start_date=args.start_date,
        end_date=args.end_date,
        trade_calendar_file=trade_calendar_file,
    )
    if not dates:
        print("[ERROR] 没有可处理的日期")
        return 2

    url_templates = args.pred_url_template or DEFAULT_PRED_URL_TEMPLATES

    print("=" * 80)
    print("历史预测链回放启动")
    print(f"repo_root      : {repo_root}")
    print(f"date_range     : {args.start_date} -> {args.end_date}")
    print(f"dates          : {dates}")
    print(f"trade_calendar : {trade_calendar_file}")
    print(f"archive_subdir : {args.archive_subdir}")
    print(f"url_templates  :")
    for i, tpl in enumerate(url_templates, start=1):
        print(f"  {i}. {tpl}")
    print(f"skip_market    : {args.skip_market_sync}")
    print(f"skip_fs        : {args.skip_build_fs}")
    print(f"skip_run       : {args.skip_run}")
    print("=" * 80)

    manifest_rows: List[DayResult] = []
    hard_failed = False
    started_at = utc_now_iso()

    for trade_date in dates:
        print("\n" + "-" * 80)
        print(f"[START] {trade_date}")
        day_result = process_one_day(
            repo_root=repo_root,
            trade_date=trade_date,
            url_templates=url_templates,
            timeout=args.timeout,
            archive_subdir=args.archive_subdir,
            sync_market_cmd_template=args.sync_market_cmd,
            build_fs_cmd_template=args.build_fs_cmd,
            run_cmd_template=args.run_cmd,
            skip_market_sync=args.skip_market_sync,
            skip_build_fs=args.skip_build_fs,
            skip_run=args.skip_run,
        )
        manifest_rows.append(day_result)

        ok = (
            day_result.pred_sync_ok
            and (day_result.market_sync_ok or day_result.skipped_market_sync)
            and (day_result.fs_build_ok or day_result.skipped_fs_build)
            and (day_result.run_ok or day_result.skipped_run)
        )

        if ok:
            print(f"[DONE] {trade_date} SUCCESS")
        else:
            print(f"[FAIL] {trade_date} stage={day_result.error_stage} msg={day_result.error_message}")
            if not args.continue_on_error:
                hard_failed = True
                break

    finished_at = utc_now_iso()

    success_count = 0
    fail_count = 0
    for row in manifest_rows:
        row_ok = (
            row.pred_sync_ok
            and (row.market_sync_ok or row.skipped_market_sync)
            and (row.fs_build_ok or row.skipped_fs_build)
            and (row.run_ok or row.skipped_run)
        )
        if row_ok:
            success_count += 1
        else:
            fail_count += 1

    manifest_dir = repo_root / "outputs" / "backfill"
    ensure_dir(manifest_dir)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = manifest_dir / f"backfill_prediction_window_{args.start_date}_{args.end_date}_{ts}.json"
    latest_json_path = manifest_dir / "backfill_prediction_window_latest.json"
    csv_path = manifest_dir / "backfill_prediction_window_history.csv"

    payload = {
        "started_at": started_at,
        "finished_at": finished_at,
        "repo_root": str(repo_root),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dates": dates,
        "trade_calendar_file": str(trade_calendar_file),
        "calendar_contract": "strict_a_share_exchange_calendar_only",
        "success_count": success_count,
        "fail_count": fail_count,
        "skip_market_sync": args.skip_market_sync,
        "skip_build_fs": args.skip_build_fs,
        "skip_run": args.skip_run,
        "pred_url_templates": url_templates,
        "sync_market_cmd": args.sync_market_cmd,
        "build_fs_cmd": args.build_fs_cmd,
        "run_cmd": args.run_cmd,
        "results": [asdict(x) for x in manifest_rows],
    }

    write_manifest_json(json_path, payload)
    write_manifest_json(latest_json_path, payload)
    append_manifest_csv(csv_path, manifest_rows)

    print("\n" + "=" * 80)
    print("历史预测链回放结束")
    print(f"success_count : {success_count}")
    print(f"fail_count    : {fail_count}")
    print(f"json_manifest : {json_path.relative_to(repo_root)}")
    print(f"latest_json   : {latest_json_path.relative_to(repo_root)}")
    print(f"history_csv   : {csv_path.relative_to(repo_root)}")
    print("=" * 80)

    if hard_failed or fail_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
