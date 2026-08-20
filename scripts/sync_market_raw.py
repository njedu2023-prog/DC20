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


def _build_fallback_relpaths(filename: str, trade_date: str | None) -> list[str]:
    """
    主路径已确认，但仍保留少量兜底，便于上游结构微调时不至于完全失效。
    """
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


def _build_candidate_urls(owner: str, repo: str, branch: str, filename: str, trade_date: str | None) -> list[str]:
    base = _base_raw_url(owner, repo, branch)
    rels = _build_fallback_relpaths(filename, trade_date)
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


def _fetch_first_matching_trade_date(
    urls: list[str],
    *,
    expected_trade_date: str | None,
    date_scoped: bool,
    token: str | None = None,
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
            if source_trade_date != expected_trade_date:
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


def _load_upstream_meta(owner: str, repo: str, branch: str, trade_date: str | None, token: str | None) -> tuple[dict[str, Any], str | None]:
    urls = _build_candidate_urls(owner, repo, branch, UPSTREAM_META_NAME, trade_date)
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


def _legacy_flat_candidates(trade_date: str) -> list[Path]:
    paths: list[Path] = [
        RAW_DIR / f"_sync_meta_{trade_date}.json",
        RAW_DIR / "_sync_meta_latest.json",
    ]
    for spec in SOURCE_SPECS:
        paths.append(RAW_DIR / f"{spec.local_stem}_{trade_date}.csv")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 a-share-top3-data 的 market raw 多源文件")
    parser.add_argument("--trade-date", dest="trade_date", default=None, help="交易日 YYYYMMDD")
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

    try:
        upstream_meta, upstream_meta_url = _load_upstream_meta(
            owner=owner,
            repo=repo,
            branch=resolved_commit,
            trade_date=trade_date,
            token=github_token,
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
        )

        hit_url, text, last_code, source_trade_date, error = (
            _fetch_first_matching_trade_date(
                urls,
                expected_trade_date=trade_date,
                date_scoped=spec.date_scoped,
                token=github_token,
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
        item["latest_path"] = str(latest_path)
        item["bytes"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        enriched_results.append(item)
        transaction_payloads[dated_path] = payload
        transaction_payloads[latest_path] = payload

    legacy_targets = [
        path
        for path in _legacy_flat_candidates(resolved_trade_date)
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
    print(f"[sync_market_raw] latest_dir={target_latest_dir}")

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
    print(f"[sync_market_raw] meta_latest={meta_latest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
