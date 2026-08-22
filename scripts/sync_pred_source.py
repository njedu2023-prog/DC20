#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sync_pred_source.py

职责（硬规则）：
- 跨仓库拉取必须独立：sync 不能混在 runner
- 将外部/本地预测源写入固定快照：data/pred/pred_source_latest.csv
- 在可识别 trade_date 时，同时落历史归档：
  data/pred/archive/pred_source_{trade_date}.csv
- 不做任何业务计算/字段适配（适配在 adapters）

环境变量：
- TOP10_PRED_URL   : 远端 CSV（GitHub Raw 等）
- TOP10_PRED_PATH  : 本地 CSV 路径（调试用）
- TRADE_DATE       : 指定交易日 YYYYMMDD（优先级最高，可选）

输出（IO 契约输入快照，latest 绝对不改动）：
- data/pred/pred_source_latest.csv
- data/pred/archive/pred_source_{trade_date}.csv  （若能识别出 trade_date）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from urllib import error as urllib_error
from urllib.parse import urlparse
import csv
import io
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_PATH = Path("data/pred/pred_source_latest.csv")
ARCHIVE_DIR = Path("data/pred/archive")
META_PATH = Path("data/pred/_pred_source_meta.json")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_TOKEN_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
CANONICAL_SOURCE_DATE_COLUMNS = ("trade_date", "signal_date", "date")

INTRADAY_REQUIRED_COLS = {
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
}


def _retry_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _download_bytes(url: str, *, attempts: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "top10-decision-sync"})
    bounded_attempts = min(max(1, int(attempts)), 3)
    for attempt in range(1, bounded_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib_error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == bounded_attempts:
                raise
        except (urllib_error.URLError, TimeoutError):
            if attempt == bounded_attempts:
                raise
        _retry_sleep(0.1 * attempt)
    raise RuntimeError("prediction download retry loop exhausted")


def _read_local_bytes(src: Path) -> bytes:
    return src.read_bytes()


def _write_bytes(dst: Path, data: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _extract_trade_date(text: str) -> str:
    """
    从字符串中提取 8 位日期 YYYYMMDD。
    仅做弱推断，不校验是否为真实交易日。
    """
    if not text:
        return ""

    m = DATE_TOKEN_RE.search(text)
    return m.group(1) if m else ""


def _strict_trade_date(value: object, *, label: str) -> str:
    raw = str(value or "")
    if re.fullmatch(r"20\d{6}", raw) is None:
        raise RuntimeError(f"{label} must be exactly YYYYMMDD: {raw!r}")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d")
    except ValueError as exc:
        raise RuntimeError(f"{label} is not a real calendar date: {raw!r}") from exc
    if parsed.strftime("%Y%m%d") != raw:
        raise RuntimeError(f"{label} is not canonical YYYYMMDD: {raw!r}")
    return raw


def _extract_trade_date_from_csv_bytes(data: bytes) -> str:
    """
    全量、严格解析 CSV 的 canonical source date。

    列优先级固定为 trade_date、signal_date、date；verify_date 和
    target_trade_date 仅是下游日期，绝不能作为 source date。所选列的每一行
    都必须是同一个真实 YYYYMMDD。若 trade_date 与 signal_date 同时存在，
    还要求二者逐行一致。
    """
    text = _decode_csv_text(data)
    if not text:
        raise RuntimeError("prediction CSV is empty or cannot be decoded")

    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = list(reader.fieldnames or [])
    except (csv.Error, UnicodeError) as exc:
        raise RuntimeError("prediction CSV header cannot be parsed") from exc

    source_column = next(
        (column for column in CANONICAL_SOURCE_DATE_COLUMNS if column in columns),
        "",
    )
    if not source_column:
        raise RuntimeError(
            "prediction CSV has no canonical source date column "
            "(trade_date, signal_date, or date)"
        )
    for column in CANONICAL_SOURCE_DATE_COLUMNS:
        if columns.count(column) > 1:
            raise RuntimeError(f"prediction CSV has duplicate source date column: {column}")

    compare_trade_signal = "trade_date" in columns and "signal_date" in columns
    resolved = ""
    rows = 0
    try:
        for row_number, row in enumerate(reader, start=2):
            rows += 1
            row_date = _strict_trade_date(
                row.get(source_column),
                label=f"prediction CSV row {row_number} {source_column}",
            )
            if compare_trade_signal:
                trade_date = _strict_trade_date(
                    row.get("trade_date"),
                    label=f"prediction CSV row {row_number} trade_date",
                )
                signal_date = _strict_trade_date(
                    row.get("signal_date"),
                    label=f"prediction CSV row {row_number} signal_date",
                )
                if trade_date != signal_date:
                    raise RuntimeError(
                        "prediction CSV trade_date/signal_date mismatch at row "
                        f"{row_number}: trade_date={trade_date}, signal_date={signal_date}"
                    )
            if not resolved:
                resolved = row_date
            elif row_date != resolved:
                raise RuntimeError(
                    "prediction CSV contains mixed source dates: "
                    f"first={resolved}, row_{row_number}={row_date}"
                )
    except csv.Error as exc:
        raise RuntimeError("prediction CSV body cannot be parsed") from exc

    if rows == 0:
        raise RuntimeError("prediction CSV has no data rows")
    return resolved


def _extract_trade_date_from_basename(source: str, *, label: str) -> str:
    matches = DATE_TOKEN_RE.findall(source)
    if not matches:
        return ""
    validated = {
        _strict_trade_date(match, label=f"{label} basename date") for match in matches
    }
    if len(validated) != 1:
        raise RuntimeError(
            f"{label} basename has ambiguous trade dates: {sorted(validated)}"
        )
    return next(iter(validated))


def _decode_csv_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return ""


def _csv_profile(data: bytes) -> dict:
    text = _decode_csv_text(data)
    if not text:
        return {
            "rows_sampled": 0,
            "columns": [],
            "trade_date": "",
            "target_trade_date": "",
            "has_intraday_fields": False,
            "missing_intraday_fields": sorted(INTRADAY_REQUIRED_COLS),
        }

    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = list(reader.fieldnames or [])
    except Exception:
        columns = []
        reader = None

    trade_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    rows = 0
    if reader is not None:
        for i, row in enumerate(reader):
            if i >= 1000:
                break
            rows += 1
            td = _extract_trade_date(str(row.get("trade_date") or row.get("signal_date") or ""))
            ttd = _extract_trade_date(str(row.get("verify_date") or row.get("target_trade_date") or ""))
            if td:
                trade_counts[td] = trade_counts.get(td, 0) + 1
            if ttd:
                target_counts[ttd] = target_counts.get(ttd, 0) + 1

    col_set = set(columns)
    missing_intraday = sorted(INTRADAY_REQUIRED_COLS - col_set)
    return {
        "rows_sampled": rows,
        "columns": columns,
        "trade_date": sorted(trade_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if trade_counts else "",
        "target_trade_date": sorted(target_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if target_counts else "",
        "has_intraday_fields": not missing_intraday,
        "missing_intraday_fields": missing_intraday,
    }


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validated_remote_source(
    url: str,
    resolved_commit: str,
) -> dict[str, str]:
    commit = str(resolved_commit or "")
    if COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeError("TOP10_PRED_RESOLVED_COMMIT must be a 40-hex commit")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "raw.githubusercontent.com":
        raise RuntimeError("remote prediction source must use raw.githubusercontent.com")
    parts = parsed.path.lstrip("/").split("/", 3)
    if len(parts) != 4 or not all(parts):
        raise RuntimeError("remote prediction source URL is malformed")
    owner, repo, url_ref, relative_path = parts
    if COMMIT_RE.fullmatch(url_ref) is None:
        raise RuntimeError("mutable prediction source ref is forbidden")
    if url_ref != commit:
        raise RuntimeError("prediction source URL commit differs from resolved commit")
    return {
        "owner": owner,
        "repo": repo,
        "resolved_commit": commit,
        "relative_path": relative_path,
    }


def _meta_bytes(
    *,
    source: str,
    source_ref: str,
    data: bytes,
    trade_date: str,
    resolved_commit: str = "",
    source_repository: str = "",
) -> bytes:
    profile = _csv_profile(data)
    body_sha256 = hashlib.sha256(data).hexdigest()
    payload = {
        "created_at_utc": _now_utc(),
        "source": source,
        "source_ref": source_ref,
        "sha256": body_sha256,
        "body_sha256": body_sha256,
        "body_bytes": len(data),
        "resolved_commit": resolved_commit,
        "source_repository": source_repository,
        "resolved_trade_date": trade_date,
        "csv_profile": profile,
        "consistency": {
            "snapshot_path": str(SNAPSHOT_PATH),
            "archive_path": str(ARCHIVE_DIR / f"pred_source_{trade_date}.csv") if trade_date else "",
            "has_intraday_fields": bool(profile.get("has_intraday_fields")),
            "target_trade_date": profile.get("target_trade_date", ""),
        },
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_staged_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _commit_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _validate_transaction_targets(payloads: dict[Path, bytes]) -> None:
    if not payloads:
        raise RuntimeError("prediction transaction has no outputs")
    for target, data in payloads.items():
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"prediction transaction target is not a file: {target}")
        if not isinstance(data, bytes):
            raise RuntimeError(f"prediction transaction payload is not bytes: {target}")


def _transactional_replace(payloads: dict[Path, bytes]) -> None:
    """Install one pred generation, rolling back every prior target on failure."""

    _validate_transaction_targets(payloads)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".pred-sync-", dir=SNAPSHOT_PATH.parent))
    staged: list[tuple[Path, Path, str]] = []
    committed: list[tuple[Path, Path | None]] = []
    try:
        for index, (target, data) in enumerate(payloads.items()):
            staged_path = stage_dir / f"new-{index}"
            _write_staged_file(staged_path, data)
            expected = hashlib.sha256(data).hexdigest()
            if hashlib.sha256(staged_path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"prediction staged hash mismatch: {target}")
            staged.append((target, staged_path, expected))
        _validate_transaction_targets(payloads)

        for index, (target, staged_path, expected) in enumerate(staged):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = stage_dir / f"old-{index}" if target.exists() else None
            if backup is not None:
                os.replace(target, backup)
            try:
                _commit_replace(staged_path, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                    raise RuntimeError(f"prediction installed hash mismatch: {target}")
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


def _existing_snapshot_trade_date() -> str:
    if not SNAPSHOT_PATH.exists():
        return ""
    try:
        return _extract_trade_date_from_csv_bytes(SNAPSHOT_PATH.read_bytes())
    except Exception:
        return ""


def _guard_not_older_than_existing(new_trade_date: str) -> None:
    if not new_trade_date:
        return
    if os.getenv("TRADE_DATE"):
        return
    if str(os.getenv("TOP10_ALLOW_OLDER_PRED", "")).strip().lower() in {"1", "true", "yes"}:
        return

    old_trade_date = _existing_snapshot_trade_date()
    if old_trade_date and new_trade_date < old_trade_date:
        raise RuntimeError(
            f"拒绝用更旧的 pred_source 覆盖 latest：new={new_trade_date}, existing={old_trade_date}. "
            "如为人工回放，请设置 TRADE_DATE 或 TOP10_ALLOW_OLDER_PRED=1。"
        )


def _resolve_trade_date(url: str, path: str, data: bytes | None = None) -> str:
    """
    CSV 内文是 canonical source date；环境变量和 source basename 只能作为
    一致性约束，不能覆盖内文。URL 只检查 path basename，绝不扫描 commit SHA。
    """
    if data is None:
        raise RuntimeError("prediction CSV body is required to resolve trade_date")
    csv_trade_date = _extract_trade_date_from_csv_bytes(data)

    env_trade_date = os.getenv("TRADE_DATE") or ""
    if env_trade_date:
        env_trade_date = _strict_trade_date(env_trade_date, label="TRADE_DATE")
        if env_trade_date != csv_trade_date:
            raise RuntimeError(
                "TRADE_DATE differs from prediction CSV source date: "
                f"env={env_trade_date}, csv={csv_trade_date}"
            )

    source_date = ""
    source_label = ""
    if url:
        source_label = "TOP10_PRED_URL"
        basename = Path(urlparse(url).path).name
        source_date = _extract_trade_date_from_basename(
            basename,
            label=source_label,
        )
    elif path:
        source_label = "TOP10_PRED_PATH"
        source_date = _extract_trade_date_from_basename(
            Path(path).name,
            label=source_label,
        )
    if source_date and source_date != csv_trade_date:
        raise RuntimeError(
            f"{source_label} basename date differs from prediction CSV source date: "
            f"basename={source_date}, csv={csv_trade_date}"
        )

    return csv_trade_date


def _generation_payloads(
    *,
    data: bytes,
    trade_date: str,
    meta: bytes,
) -> dict[Path, bytes]:
    payloads = {SNAPSHOT_PATH: data, META_PATH: meta}
    if not trade_date:
        print("[SYNC][WARN] 未识别到 trade_date；本次仅更新 latest，不落 archive。")
        return payloads
    archive_path = ARCHIVE_DIR / f"pred_source_{trade_date}.csv"
    if archive_path.is_symlink():
        raise RuntimeError(f"prediction archive target must not be a symlink: {archive_path}")
    if archive_path.exists():
        if not archive_path.is_file():
            raise RuntimeError(
                f"prediction archive target is not a regular file: {archive_path}"
            )
        existing = archive_path.read_bytes()
        existing_sha = hashlib.sha256(existing).hexdigest()
        incoming_sha = hashlib.sha256(data).hexdigest()
        if existing != data:
            raise RuntimeError(
                "immutable prediction archive conflict before latest/meta write: "
                f"path={archive_path}, existing_sha256={existing_sha}, "
                f"incoming_sha256={incoming_sha}"
            )
        print(
            "[SYNC] immutable archive already matches; preserving existing file "
            f"-> {archive_path} sha256={existing_sha}"
        )
        return payloads
    payloads[archive_path] = data
    return payloads


def main() -> int:
    url = (os.getenv("TOP10_PRED_URL") or "").strip()
    path = (os.getenv("TOP10_PRED_PATH") or "").strip()

    if not url and not path:
        print("[SYNC][ERR] 未提供 TOP10_PRED_URL / TOP10_PRED_PATH，无法同步预测源。", file=sys.stderr)
        return 2

    if url:
        try:
            remote = _validated_remote_source(
                url,
                os.getenv("TOP10_PRED_RESOLVED_COMMIT", ""),
            )
        except RuntimeError as exc:
            print(f"[SYNC][ERR] {exc}", file=sys.stderr)
            return 2
        print(f"[SYNC] use TOP10_PRED_URL={url}")
        data = _download_bytes(url)
        try:
            trade_date = _resolve_trade_date(url=url, path=path, data=data)
            print(f"[SYNC] resolved trade_date={trade_date}")
            _guard_not_older_than_existing(trade_date)
            meta = _meta_bytes(
                source="url",
                source_ref=url,
                data=data,
                trade_date=trade_date,
                resolved_commit=remote["resolved_commit"],
                source_repository=f"{remote['owner']}/{remote['repo']}",
            )
            payloads = _generation_payloads(
                data=data,
                trade_date=trade_date,
                meta=meta,
            )
        except RuntimeError as exc:
            print(f"[SYNC][ERR] {exc}", file=sys.stderr)
            return 2
        _transactional_replace(payloads)
        print(f"[SYNC] wrote snapshot -> {SNAPSHOT_PATH}")
        if ARCHIVE_DIR / f"pred_source_{trade_date}.csv" in payloads:
            print(f"[SYNC] wrote archive  -> {ARCHIVE_DIR / f'pred_source_{trade_date}.csv'}")
        else:
            print(f"[SYNC] kept archive   -> {ARCHIVE_DIR / f'pred_source_{trade_date}.csv'}")
        print(f"[SYNC] wrote meta      -> {META_PATH}")
        return 0

    p = Path(path)
    if not p.exists():
        print(f"[SYNC][ERR] TOP10_PRED_PATH 不存在：{p}", file=sys.stderr)
        return 2

    print(f"[SYNC] use TOP10_PRED_PATH={p}")
    data = _read_local_bytes(p)
    try:
        trade_date = _resolve_trade_date(url=url, path=path, data=data)
        print(f"[SYNC] resolved trade_date={trade_date}")
        _guard_not_older_than_existing(trade_date)
        meta = _meta_bytes(
            source="path",
            source_ref=str(p),
            data=data,
            trade_date=trade_date,
        )
        payloads = _generation_payloads(
            data=data,
            trade_date=trade_date,
            meta=meta,
        )
    except RuntimeError as exc:
        print(f"[SYNC][ERR] {exc}", file=sys.stderr)
        return 2
    _transactional_replace(payloads)
    print(f"[SYNC] wrote snapshot -> {SNAPSHOT_PATH}")
    if ARCHIVE_DIR / f"pred_source_{trade_date}.csv" in payloads:
        print(f"[SYNC] wrote archive  -> {ARCHIVE_DIR / f'pred_source_{trade_date}.csv'}")
    else:
        print(f"[SYNC] kept archive   -> {ARCHIVE_DIR / f'pred_source_{trade_date}.csv'}")
    print(f"[SYNC] wrote meta      -> {META_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
