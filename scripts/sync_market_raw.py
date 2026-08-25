#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sync_market_raw.py

目标：
- 从 a-share-top3-data 仓库同步 market 原始多源文件
- 按日期分目录落到本仓库：
    data/market/raw/{YYYY}/{YYYYMMDD}/{filename}
- 同时维护 latest 镜像目录：
    data/market/raw/latest/{filename}
- 记录同步审计：
    data/market/raw/{YYYY}/{YYYYMMDD}/_sync_meta.json
    data/market/raw/latest/_sync_meta.json

已确认的上游主路径结构：
- data/raw/{YYYY}/{YYYYMMDD}/{filename}

例如：
- data/raw/2026/20260306/daily.csv

职责边界：
- 本脚本只负责“同步 raw 原料”
- 不负责 FS 构建
- 不负责 Decision / Premium 计算
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


RAW_DIR = Path("data/market/raw")
LATEST_DIR_NAME = "latest"
TIMEOUT = 20
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_RE = re.compile(r"^20\d{6}$")
DEFAULT_TRADE_CALENDAR = Path("data/market/trade_cal_sse.csv")
MAX_CONTEXT_OPEN_SESSIONS = 64


@dataclass(frozen=True)
class SourceSpec:
    local_stem: str
    upstream_name: str
    required: bool = False
    date_scoped: bool = True


SOURCE_SPECS: list[SourceSpec] = [
    SourceSpec("daily", "daily.csv", required=True),
    SourceSpec("daily_basic", "daily_basic.csv", required=True),
    SourceSpec("hot_boards", "hot_boards.csv", required=False),
    SourceSpec("intraday_features", "intraday_features.csv", required=False),
    SourceSpec("limit_break_d", "limit_break_d.csv", required=False),
    SourceSpec("limit_list_d", "limit_list_d.csv", required=False),
    SourceSpec("limit_up_tags", "limit_up_tags.csv", required=False),
    SourceSpec("moneyflow_hsgt", "moneyflow_hsgt.csv", required=False),
    SourceSpec("namechange", "namechange.csv", required=False, date_scoped=False),
    SourceSpec("stk_auction", "stk_auction.csv", required=False),
    SourceSpec("stk_limit", "stk_limit.csv", required=True),
    SourceSpec("stock_basic", "stock_basic.csv", required=True, date_scoped=False),
    SourceSpec("top_list", "top_list.csv", required=False),
]

UPSTREAM_META_NAME = "_meta.json"


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _norm_trade_date(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    s = re.sub(r"\.0$", "", s)
    if re.fullmatch(r"\d{8}", s):
        return s
    return None


def _trade_year(trade_date: str) -> str:
    return trade_date[:4]


def _base_raw_url(owner: str, repo: str, branch: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"


def _build_fallback_relpaths(
    filename: str,
    trade_date: str | None,
    *,
    strict_dated: bool = False,
) -> list[str]:
    """
    主路径已确认，但仍保留少量兜底，便于上游结构微调时不至于完全失效。
    """
    if strict_dated:
        if not trade_date:
            raise RuntimeError("strict dated market source requires trade_date")
        return [f"data/raw/{_trade_year(trade_date)}/{trade_date}/{filename}"]

    paths: list[str] = []

    if trade_date:
        year = _trade_year(trade_date)
        paths.extend([
            f"data/raw/{year}/{trade_date}/{filename}",
            f"data/raw/{trade_date}/{filename}",
            f"data/{trade_date}/{filename}",
            f"{trade_date}/{filename}",
        ])

    paths.extend([
        f"data/raw/latest/{filename}",
        f"data/latest/{filename}",
        f"data/{filename}",
        filename,
    ])

    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _build_candidate_urls(
    owner: str,
    repo: str,
    branch: str,
    filename: str,
    trade_date: str | None,
    *,
    strict_dated: bool = False,
) -> list[str]:
    base = _base_raw_url(owner, repo, branch)
    rels = _build_fallback_relpaths(
        filename,
        trade_date,
        strict_dated=strict_dated,
    )
    return [f"{base}/{rel}" for rel in rels]


def _retry_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _http_get_text(
    url: str,
    token: str | None = None,
    *,
    attempts: int = 3,
) -> tuple[bool, str, int]:
    headers = {
        "User-Agent": "top10-decision-sync-market-raw/1.2",
        "Accept": "text/plain,application/json;q=0.9,*/*;q=0.8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    bounded_attempts = min(max(1, int(attempts)), 3)
    last_code = 0
    for attempt in range(1, bounded_attempts + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        except (requests.Timeout, requests.ConnectionError):
            if attempt < bounded_attempts:
                _retry_sleep(0.1 * attempt)
                continue
            return False, "", 0
        except Exception:
            return False, "", 0
        last_code = int(resp.status_code)
        if resp.status_code == 200:
            resp.encoding = resp.encoding or "utf-8"
            return True, resp.text, resp.status_code
        retryable = resp.status_code == 429 or 500 <= resp.status_code <= 599
        if not retryable or attempt == bounded_attempts:
            return False, "", resp.status_code
        _retry_sleep(0.1 * attempt)
    return False, "", last_code


def _fetch_first_available(urls: list[str], token: str | None = None) -> tuple[str | None, str | None, int | None]:
    last_code: int | None = None
    for url in urls:
        ok, text, code = _http_get_text(url, token=token)
        last_code = code
        if ok and text:
            return url, text, code
    return None, None, last_code


def _infer_trade_date_from_csv_text(text: str) -> str | None:
    if not text:
        return None

    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        first_row = next(reader, None)
        if not first_row:
            return None

        for key in ("trade_date", "date"):
            if key in first_row:
                td = _norm_trade_date(first_row.get(key))
                if td:
                    return td
    except Exception:
        return None

    return None


def _all_csv_rows_match_trade_date(text: str, expected_trade_date: str) -> bool:
    """Require every row on a dated surface to name exactly one D session."""

    if not text or DATE_RE.fullmatch(expected_trade_date) is None:
        return False
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        columns = list(reader.fieldnames or [])
        date_column = next(
            (name for name in ("trade_date", "date") if name in columns),
            "",
        )
        if not date_column or columns.count(date_column) != 1:
            return False
        rows = 0
        for row in reader:
            rows += 1
            if _norm_trade_date(row.get(date_column)) != expected_trade_date:
                return False
        return rows > 0
    except (csv.Error, UnicodeError, TypeError):
        return False


def _fetch_first_matching_trade_date(
    urls: list[str],
    *,
    expected_trade_date: str | None,
    date_scoped: bool,
    token: str | None = None,
    require_all_rows_match: bool = False,
) -> tuple[str | None, str | None, int | None, str | None, str]:
    last_code: int | None = None
    rejected_dates: list[str] = []
    for url in urls:
        ok, text, code = _http_get_text(url, token=token)
        last_code = code
        if not ok or not text:
            continue
        source_trade_date = _infer_trade_date_from_csv_text(text)
        if expected_trade_date and date_scoped:
            exact_rows = (
                _all_csv_rows_match_trade_date(text, expected_trade_date)
                if require_all_rows_match
                else source_trade_date == expected_trade_date
            )
            if not exact_rows:
                rejected_dates.append(source_trade_date or "missing")
                continue
        return url, text, code, source_trade_date, ""

    if rejected_dates:
        actual = ",".join(dict.fromkeys(rejected_dates))
        error = (
            "trade_date_mismatch:"
            f"requested={expected_trade_date},actual={actual}"
        )
    else:
        error = "not_found_in_candidate_urls"
    return None, None, last_code, None, error


def _infer_trade_date_from_meta(meta: dict[str, Any]) -> str | None:
    if not meta:
        return None

    for key in ("trade_date", "asof_date", "snapshot_date", "date"):
        td = _norm_trade_date(meta.get(key))
        if td:
            return td

    nested = meta.get("meta")
    if isinstance(nested, dict):
        for key in ("trade_date", "asof_date", "snapshot_date", "date"):
            td = _norm_trade_date(nested.get(key))
            if td:
                return td

    return None


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_staged_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _commit_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _validate_transaction_targets(payloads: dict[Path, bytes | None]) -> None:
    if not payloads:
        raise RuntimeError("market transaction has no outputs")
    for target, data in payloads.items():
        if target.exists() and not target.is_file():
            raise RuntimeError(f"market transaction target is not a file: {target}")
        if data is not None and not isinstance(data, bytes):
            raise RuntimeError(f"market transaction payload is not bytes: {target}")


def _transactional_replace(payloads: dict[Path, bytes | None]) -> None:
    """Install files/deletions as one rollback-capable market generation."""

    _validate_transaction_targets(payloads)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".market-sync-", dir=RAW_DIR))
    staged: list[tuple[Path, Path | None, str | None]] = []
    committed: list[tuple[Path, Path | None]] = []
    try:
        for index, (target, data) in enumerate(payloads.items()):
            if data is None:
                staged.append((target, None, None))
                continue
            staged_path = stage_dir / f"new-{index}"
            _write_staged_file(staged_path, data)
            expected = hashlib.sha256(data).hexdigest()
            if hashlib.sha256(staged_path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"market staged hash mismatch: {target}")
            staged.append((target, staged_path, expected))
        _validate_transaction_targets(payloads)

        for index, (target, staged_path, expected) in enumerate(staged):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = stage_dir / f"old-{index}" if target.exists() else None
            if backup is not None:
                os.replace(target, backup)
            try:
                if staged_path is not None:
                    _commit_replace(staged_path, target)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                        raise RuntimeError(f"market installed hash mismatch: {target}")
            except Exception:
                if target.exists():
                    target.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                raise
            committed.append((target, backup))
    except Exception:
        for target, backup in reversed(committed):
            if target.exists():
                target.unlink()
            if backup is not None and backup.exists():
                os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _load_upstream_meta(
    owner: str,
    repo: str,
    branch: str,
    trade_date: str | None,
    token: str | None,
    *,
    strict_dated: bool = False,
) -> tuple[dict[str, Any], str | None]:
    urls = _build_candidate_urls(
        owner,
        repo,
        branch,
        UPSTREAM_META_NAME,
        trade_date,
        strict_dated=strict_dated,
    )
    hit_url, text, _ = _fetch_first_available(urls, token=token)
    if not hit_url or not text:
        return {}, None

    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("upstream _meta.json is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("upstream _meta.json must contain a JSON object")
    return payload, hit_url


def _dated_dir(trade_date: str) -> Path:
    return RAW_DIR / _trade_year(trade_date) / trade_date


def _latest_dir() -> Path:
    return RAW_DIR / LATEST_DIR_NAME


def _build_dated_path(upstream_name: str, trade_date: str) -> Path:
    return _dated_dir(trade_date) / upstream_name


def _build_latest_path(upstream_name: str) -> Path:
    return _latest_dir() / upstream_name


def _build_meta_dated_path(trade_date: str) -> Path:
    return _dated_dir(trade_date) / "_sync_meta.json"


def _build_meta_latest_path() -> Path:
    return _latest_dir() / "_sync_meta.json"


def _legacy_flat_candidates(
    trade_date: str,
    *,
    include_latest: bool = True,
) -> list[Path]:
    paths: list[Path] = [RAW_DIR / f"_sync_meta_{trade_date}.json"]
    if include_latest:
        paths.append(RAW_DIR / "_sync_meta_latest.json")
    for spec in SOURCE_SPECS:
        paths.append(RAW_DIR / f"{spec.local_stem}_{trade_date}.csv")
        if include_latest:
            paths.append(RAW_DIR / f"{spec.local_stem}_latest.csv")
    return paths


def _cleanup_legacy_flat_files(trade_date: str) -> list[str]:
    removed: list[str] = []
    for path in _legacy_flat_candidates(trade_date):
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(str(path))
        except Exception:
            pass
    return removed


def _strict_sse_context_window(
    calendar_path: Path,
    trade_date: str,
    open_sessions: int,
) -> list[str]:
    """Return the exact trailing SSE-open window ending at D."""

    if not 1 <= int(open_sessions) <= MAX_CONTEXT_OPEN_SESSIONS:
        raise RuntimeError(
            "market context open-session count must be within "
            f"[1,{MAX_CONTEXT_OPEN_SESSIONS}]"
        )
    if (
        calendar_path.is_symlink()
        or not calendar_path.is_file()
        or calendar_path.stat().st_size <= 0
    ):
        raise RuntimeError("strict SSE calendar is missing, empty, or unsafe")
    try:
        with calendar_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if list(reader.fieldnames or []) != [
                "exchange",
                "cal_date",
                "is_open",
                "pretrade_date",
            ]:
                raise RuntimeError("strict SSE calendar columns are invalid")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RuntimeError("strict SSE calendar is unreadable") from exc
    if not rows:
        raise RuntimeError("strict SSE calendar has no rows")
    seen: set[str] = set()
    previous_calendar_date = ""
    previous_open_date = ""
    open_dates: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        cal_date = str(row.get("cal_date") or "")
        pretrade_date = str(row.get("pretrade_date") or "")
        is_open = str(row.get("is_open") or "")
        if row.get("exchange") != "SSE":
            raise RuntimeError(
                f"strict SSE calendar exchange is invalid at row {row_number}"
            )
        if (
            DATE_RE.fullmatch(cal_date) is None
            or DATE_RE.fullmatch(pretrade_date) is None
            or is_open not in {"0", "1"}
            or cal_date in seen
            or (previous_calendar_date and cal_date <= previous_calendar_date)
        ):
            raise RuntimeError(
                f"strict SSE calendar row is invalid at row {row_number}"
            )
        seen.add(cal_date)
        previous_calendar_date = cal_date
        if previous_open_date and pretrade_date != previous_open_date:
            raise RuntimeError(
                "strict SSE calendar pretrade chain is invalid at "
                f"row {row_number}"
            )
        if is_open == "1":
            previous_open_date = cal_date
            open_dates.append(cal_date)
    if trade_date not in open_dates:
        raise RuntimeError("requested market context D is not an open SSE session")
    eligible = [date for date in open_dates if date <= trade_date]
    if len(eligible) < open_sessions:
        raise RuntimeError("strict SSE calendar has insufficient prior open sessions")
    window = eligible[-open_sessions:]
    if window[-1] != trade_date:
        raise RuntimeError("strict SSE context window does not end at D")
    return window


def _file_is_regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _strict_generation_valid(
    trade_date: str,
    resolved_commit: str,
    *,
    require_latest: bool,
) -> bool:
    """Verify one exact-commit dated generation before it can be reused."""

    meta_path = _build_meta_dated_path(trade_date)
    if not _file_is_regular(meta_path):
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    if not isinstance(meta, dict):
        return False
    source_repo = meta.get("source_repo")
    if (
        meta.get("trade_date") != trade_date
        or meta.get("requested_trade_date") != trade_date
        or meta.get("resolved_trade_date") != trade_date
        or meta.get("strict_dated_source") is not True
        or meta.get("dated_only") is not (not require_latest)
        or not isinstance(source_repo, dict)
        or source_repo.get("resolved_commit") != resolved_commit
        or meta.get("required_failures") != []
        or meta.get("write_failures") != []
    ):
        return False
    exact_base = _base_raw_url(
        str(source_repo.get("owner") or ""),
        str(source_repo.get("repo") or ""),
        resolved_commit,
    )
    expected_meta_url = (
        f"{exact_base}/data/raw/{trade_date[:4]}/{trade_date}/{UPSTREAM_META_NAME}"
    )
    if meta.get("upstream_meta_url") != expected_meta_url:
        return False
    files = meta.get("files")
    if not isinstance(files, list):
        return False
    by_name = {
        str(item.get("name") or ""): item
        for item in files
        if isinstance(item, dict)
    }
    if set(by_name) != {spec.local_stem for spec in SOURCE_SPECS}:
        return False
    for spec in SOURCE_SPECS:
        item = by_name[spec.local_stem]
        dated_path = _build_dated_path(spec.upstream_name, trade_date)
        if item.get("success") is not True:
            if spec.required or dated_path.exists():
                return False
            if require_latest and _build_latest_path(spec.upstream_name).exists():
                return False
            continue
        expected_url = (
            f"{exact_base}/data/raw/{trade_date[:4]}/{trade_date}/"
            f"{spec.upstream_name}"
        )
        if (
            item.get("source_url") != expected_url
            or item.get("dated_path") != str(dated_path)
            or not _file_is_regular(dated_path)
        ):
            return False
        payload = dated_path.read_bytes()
        if (
            item.get("bytes") != len(payload)
            or item.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            return False
        if spec.date_scoped:
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeError:
                return False
            if (
                item.get("source_trade_date") != trade_date
                or not _all_csv_rows_match_trade_date(text, trade_date)
            ):
                return False
        if require_latest:
            latest_path = _build_latest_path(spec.upstream_name)
            if (
                item.get("latest_path") != str(latest_path)
                or not _file_is_regular(latest_path)
                or latest_path.read_bytes() != payload
            ):
                return False
        elif item.get("latest_path") not in (None, ""):
            return False
    if require_latest:
        latest_meta = _build_meta_latest_path()
        if not _file_is_regular(latest_meta) or latest_meta.read_bytes() != meta_path.read_bytes():
            return False
    return True


def _sync_strict_sse_context(
    *,
    trade_date: str,
    resolved_commit: str,
    calendar_path: Path,
    open_sessions: int,
) -> int:
    dates = _strict_sse_context_window(
        calendar_path,
        trade_date,
        open_sessions,
    )
    script = Path(__file__).resolve()
    for context_date in dates:
        require_latest = context_date == trade_date
        if _strict_generation_valid(
            context_date,
            resolved_commit,
            require_latest=require_latest,
        ):
            print(
                "[sync_market_raw] context_reuse "
                f"trade_date={context_date} commit={resolved_commit}"
            )
            continue
        command = [
            sys.executable,
            str(script),
            "--trade-date",
            context_date,
            "--strict-dated-source",
        ]
        if not require_latest:
            command.append("--dated-only")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            print(
                "[sync_market_raw] ERROR: strict SSE context sync failed "
                f"for {context_date}"
            )
            return 2
        if not _strict_generation_valid(
            context_date,
            resolved_commit,
            require_latest=require_latest,
        ):
            print(
                "[sync_market_raw] ERROR: strict SSE context generation "
                f"failed validation for {context_date}"
            )
            return 2
    print(
        "[sync_market_raw] strict_sse_context="
        + ",".join(dates)
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 a-share-top3-data 的 market raw 多源文件")
    parser.add_argument("--trade-date", dest="trade_date", default=None, help="交易日 YYYYMMDD")
    parser.add_argument(
        "--ensure-sse-open-context",
        type=int,
        default=0,
        metavar="N",
        help="严格补齐截至D的最近N个SSE开市日原始上下文",
    )
    parser.add_argument(
        "--trade-calendar-file",
        default=str(DEFAULT_TRADE_CALENDAR),
        help="owned strict SSE calendar",
    )
    parser.add_argument(
        "--strict-dated-source",
        action="store_true",
        help="只允许固定commit下data/raw/YYYY/YYYYMMDD路径",
    )
    parser.add_argument(
        "--dated-only",
        action="store_true",
        help="内部上下文模式：仅写dated generation，不改latest",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    trade_date = _norm_trade_date(args.trade_date or os.getenv("TRADE_DATE"))
    owner = os.getenv("MARKET_RAW_OWNER", "njedu2023-prog")
    repo = os.getenv("MARKET_RAW_REPO", "a-share-top3-data")
    branch = os.getenv("MARKET_RAW_BRANCH", "main")
    resolved_commit = os.getenv("MARKET_RAW_COMMIT", "")
    github_token = os.getenv("GITHUB_TOKEN", "").strip() or None

    if COMMIT_RE.fullmatch(resolved_commit) is None:
        print(
            "[sync_market_raw] ERROR: MARKET_RAW_COMMIT must be a 40-hex commit"
        )
        return 2

    if args.dated_only and not args.strict_dated_source:
        print("[sync_market_raw] ERROR: --dated-only requires --strict-dated-source")
        return 2
    if args.ensure_sse_open_context:
        if args.dated_only or not args.strict_dated_source or trade_date is None:
            print(
                "[sync_market_raw] ERROR: strict SSE context requires explicit "
                "--trade-date and --strict-dated-source"
            )
            return 2
        try:
            return _sync_strict_sse_context(
                trade_date=trade_date,
                resolved_commit=resolved_commit,
                calendar_path=Path(args.trade_calendar_file),
                open_sessions=args.ensure_sse_open_context,
            )
        except RuntimeError as exc:
            print(f"[sync_market_raw] ERROR: {exc}")
            return 2

    try:
        upstream_meta, upstream_meta_url = _load_upstream_meta(
            owner=owner,
            repo=repo,
            branch=resolved_commit,
            trade_date=trade_date,
            token=github_token,
            strict_dated=args.strict_dated_source,
        )
    except RuntimeError as exc:
        print(f"[sync_market_raw] ERROR: {exc}")
        return 2

    resolved_trade_date = trade_date or _infer_trade_date_from_meta(upstream_meta)

    results: list[dict[str, Any]] = []
    required_failures: list[str] = []
    downloaded_texts: dict[str, str] = {}

    for spec in SOURCE_SPECS:
        urls = _build_candidate_urls(
            owner=owner,
            repo=repo,
            branch=resolved_commit,
            filename=spec.upstream_name,
            trade_date=trade_date,
            strict_dated=args.strict_dated_source,
        )

        hit_url, text, last_code, source_trade_date, error = (
            _fetch_first_matching_trade_date(
                urls,
                expected_trade_date=trade_date,
                date_scoped=spec.date_scoped,
                token=github_token,
                require_all_rows_match=args.strict_dated_source,
            )
        )

        if hit_url and text:
            if resolved_trade_date is None and spec.local_stem == "daily":
                resolved_trade_date = source_trade_date

            downloaded_texts[spec.local_stem] = text

            results.append({
                "name": spec.local_stem,
                "upstream_name": spec.upstream_name,
                "required": spec.required,
                "date_scoped": spec.date_scoped,
                "success": True,
                "source_url": hit_url,
                "source_trade_date": source_trade_date,
                "status_code": last_code,
                "error": "",
            })
        else:
            results.append({
                "name": spec.local_stem,
                "upstream_name": spec.upstream_name,
                "required": spec.required,
                "date_scoped": spec.date_scoped,
                "success": False,
                "source_url": "",
                "source_trade_date": None,
                "status_code": last_code,
                "error": error,
            })
            if spec.required:
                required_failures.append(spec.local_stem)

    if resolved_trade_date is None:
        print("[sync_market_raw] ERROR: 无法解析 trade_date（既未显式传入，也无法从上游 meta/daily 推断）")
        return 2

    for item in results:
        if not item["success"] or not item["date_scoped"]:
            continue
        source_trade_date = item.get("source_trade_date")
        if source_trade_date == resolved_trade_date:
            continue
        item["success"] = False
        item["error"] = (
            "trade_date_mismatch:"
            f"requested={resolved_trade_date},"
            f"actual={source_trade_date or 'missing'}"
        )
        downloaded_texts.pop(str(item["name"]), None)
        if item["required"]:
            required_failures.append(str(item["name"]))

    if required_failures:
        print(f"[sync_market_raw] resolved_trade_date={resolved_trade_date}")
        print(f"[sync_market_raw] source_repo={owner}/{repo}@{branch}")
        for item in results:
            status = "OK" if item.get("success") else "FAIL"
            print(
                f"[sync_market_raw] {status} {item['name']} "
                f"url={item.get('source_url', '')} "
                f"source_trade_date={item.get('source_trade_date') or ''} "
                f"error={item.get('error', '')}"
            )
        print(
            "[sync_market_raw] ERROR: required files unavailable for the "
            f"requested session -> {sorted(set(required_failures))}"
        )
        return 2

    enriched_results: list[dict[str, Any]] = []
    transaction_payloads: dict[Path, bytes | None] = {}

    target_dated_dir = _dated_dir(resolved_trade_date)
    target_latest_dir = _latest_dir()

    for item in results:
        spec = next(s for s in SOURCE_SPECS if s.local_stem == item["name"])

        if not item["success"]:
            enriched_results.append(item)
            continue

        text = downloaded_texts[spec.local_stem]
        payload = text.encode("utf-8")

        dated_path = _build_dated_path(spec.upstream_name, resolved_trade_date)
        latest_path = _build_latest_path(spec.upstream_name)

        item["dated_path"] = str(dated_path)
        item["latest_path"] = "" if args.dated_only else str(latest_path)
        item["bytes"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        enriched_results.append(item)
        transaction_payloads[dated_path] = payload
        if not args.dated_only:
            transaction_payloads[latest_path] = payload

    if args.strict_dated_source:
        for item in results:
            if item.get("success"):
                continue
            spec = next(
                source
                for source in SOURCE_SPECS
                if source.local_stem == item["name"]
            )
            transaction_payloads[
                _build_dated_path(spec.upstream_name, resolved_trade_date)
            ] = None
            if not args.dated_only:
                transaction_payloads[_build_latest_path(spec.upstream_name)] = None

    legacy_targets = [
        path
        for path in _legacy_flat_candidates(
            resolved_trade_date,
            include_latest=not args.dated_only,
        )
        if path.exists() and path.is_file()
    ]
    legacy_removed = [str(path) for path in legacy_targets]

    sync_meta = {
        "trade_date": resolved_trade_date,
        "created_at_utc": _now_utc(),
        "source_repo": {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "resolved_commit": resolved_commit,
        },
        "requested_trade_date": trade_date,
        "resolved_trade_date": resolved_trade_date,
        "strict_dated_source": bool(args.strict_dated_source),
        "dated_only": bool(args.dated_only),
        "raw_storage_pattern": "data/market/raw/{YYYY}/{YYYYMMDD}/{filename}",
        "raw_latest_pattern": "data/market/raw/latest/{filename}",
        "upstream_primary_pattern": "data/raw/{YYYY}/{YYYYMMDD}/{filename}",
        "upstream_meta_url": upstream_meta_url or "",
        "upstream_meta": upstream_meta,
        "files": enriched_results,
        "required_failures": sorted(set(required_failures)),
        "write_failures": [],
        "legacy_cleanup": {
            "enabled": True,
            "removed_files": legacy_removed,
            "removed_count": len(legacy_removed),
        },
        "summary": {
            "success_count": sum(1 for x in enriched_results if x.get("success")),
            "failure_count": sum(1 for x in enriched_results if not x.get("success")),
            "required_failure_count": len(set(required_failures)),
        },
    }

    meta_dated_path = _build_meta_dated_path(resolved_trade_date)
    meta_latest_path = _build_meta_latest_path()
    meta_bytes = (json.dumps(sync_meta, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    transaction_payloads[meta_dated_path] = meta_bytes
    if not args.dated_only:
        transaction_payloads[meta_latest_path] = meta_bytes
    for path in legacy_targets:
        transaction_payloads[path] = None
    try:
        _transactional_replace(transaction_payloads)
    except Exception as exc:
        print(
            f"[sync_market_raw] ERROR: atomic generation install failed: {type(exc).__name__}: {exc}"
        )
        return 2

    print(f"[sync_market_raw] resolved_trade_date={resolved_trade_date}")
    print(f"[sync_market_raw] source_repo={owner}/{repo}@{branch}")
    print(f"[sync_market_raw] dated_dir={target_dated_dir}")
    print(
        "[sync_market_raw] latest_dir="
        f"{target_latest_dir if not args.dated_only else 'not_written'}"
    )

    for item in enriched_results:
        status = "OK" if item.get("success") else "FAIL"
        print(
            f"[sync_market_raw] {status} {item['name']} "
            f"url={item.get('source_url', '')} "
            f"source_trade_date={item.get('source_trade_date') or ''} "
            f"error={item.get('error', '')} "
            f"dated={item.get('dated_path', '')} "
            f"latest={item.get('latest_path', '')}"
        )

    if legacy_removed:
        print(f"[sync_market_raw] legacy_flat_removed_count={len(legacy_removed)}")
        for p in legacy_removed:
            print(f"[sync_market_raw] legacy_flat_removed={p}")
    else:
        print("[sync_market_raw] legacy_flat_removed_count=0")

    print(f"[sync_market_raw] meta_dated={meta_dated_path}")
    print(
        "[sync_market_raw] meta_latest="
        f"{meta_latest_path if not args.dated_only else 'not_written'}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
