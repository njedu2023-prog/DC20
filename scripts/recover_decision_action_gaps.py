#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.action_plan import build_report_index  # noqa: E402


DATE_RE = re.compile(r"^20\d{6}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
BJT = ZoneInfo("Asia/Shanghai")
SAME_DAY_RECOVERY_START = time(15, 5)
MANIFEST_PATH = Path("models/decision_model_freeze.json")
CALENDAR_PATH = Path("data/market/trade_cal_sse.csv")


class RecoveryError(RuntimeError):
    """Fail-closed input or output contract violation."""


@dataclass(frozen=True)
class RecoveryInputs:
    report_date: str
    signal_date: str
    exec_date: str
    exit_date: str
    report_path: Path
    eval_path: Path
    execution_path: Path
    candidates_path: Path
    candidate_rows: tuple[dict[str, str], ...]
    source_sha256: dict[str, str]


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise RecoveryError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _read_regular_bytes(root: Path, relative: Path) -> bytes:
    if relative.is_absolute() or ".." in relative.parts:
        raise RecoveryError(f"input path is not repository-relative: {relative}")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise RecoveryError(f"required regular input is missing: {relative.as_posix()}")
    return path.read_bytes()


def _load_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_json_object,
        )
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError(f"invalid JSON input {label}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"JSON input must be an object: {label}")
    return value


def _read_csv_bytes(raw: bytes, label: str) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
        headers = tuple(reader.fieldnames or ())
        if not headers or any(not name for name in headers) or len(set(headers)) != len(headers):
            raise RecoveryError(f"CSV header is missing or duplicated: {label}")
        rows: list[dict[str, str]] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                raise RecoveryError(f"CSV row has extra cells: {label}:{row_number}")
            row = {name: str(raw_row.get(name) or "").strip() for name in headers}
            if any(row.values()):
                rows.append(row)
        return headers, rows
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError(f"invalid CSV input {label}: {type(exc).__name__}") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise RecoveryError(f"{label} must be an exact YYYYMMDD string")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise RecoveryError(f"{label} is not a calendar date: {value}") from exc
    return value


def _parse_report_dates(values: Sequence[str]) -> list[str]:
    dates = [item.strip() for value in values for item in value.split(",") if item.strip()]
    if not 1 <= len(dates) <= 5:
        raise RecoveryError("--report-dates requires between 1 and 5 dates")
    for index, value in enumerate(dates):
        _require_date(value, f"report_dates[{index}]")
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise RecoveryError("--report-dates must be unique and strictly ascending")
    return dates


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RecoveryError(f"git exact-base check failed: {' '.join(args)}") from exc


def _require_exact_base(root: Path, base_sha: str) -> None:
    if not SHA_RE.fullmatch(base_sha):
        raise RecoveryError("--base-sha must be a lowercase 40-character Git SHA")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    if head != base_sha:
        raise RecoveryError(f"exact-base mismatch: HEAD={head} requested={base_sha}")


def _base_generated_at(root: Path, base_sha: str) -> str:
    """Return a replay-stable timestamp that is part of the exact Git base."""

    raw_epoch = _git(root, "show", "-s", "--format=%ct", base_sha).stdout.strip()
    if not re.fullmatch(r"[0-9]{1,12}", raw_epoch):
        raise RecoveryError("exact-base commit timestamp is invalid")
    try:
        value = datetime.fromtimestamp(int(raw_epoch), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise RecoveryError("exact-base commit timestamp is out of range") from exc
    return value.replace(microsecond=0).isoformat()


def _git_tree_entry(root: Path, base_sha: str, relative: Path) -> tuple[str, str, str] | None:
    result = _git(root, "ls-tree", base_sha, "--", relative.as_posix()).stdout.strip()
    if not result:
        return None
    lines = result.splitlines()
    if len(lines) != 1 or "\t" not in lines[0]:
        raise RecoveryError(f"ambiguous exact-base tree entry: {relative.as_posix()}")
    metadata, actual_path = lines[0].split("\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or actual_path != relative.as_posix():
        raise RecoveryError(f"invalid exact-base tree entry: {relative.as_posix()}")
    return fields[0], fields[1], fields[2]


def _assert_source_at_base(root: Path, base_sha: str, relative: Path, raw: bytes) -> None:
    entry = _git_tree_entry(root, base_sha, relative)
    if entry is None:
        raise RecoveryError(f"source is absent from exact base: {relative.as_posix()}")
    mode, kind, oid = entry
    if kind != "blob" or mode == "120000":
        raise RecoveryError(f"source is not a regular exact-base blob: {relative.as_posix()}")
    stored = _git(root, "cat-file", "blob", oid, text=False).stdout
    if stored != raw:
        raise RecoveryError(f"working source differs from exact base: {relative.as_posix()}")


def _git_target_exists(root: Path, base_sha: str, relative: Path) -> bool:
    return _git_tree_entry(root, base_sha, relative) is not None


def _exact_base_index_inventory(
    root: Path,
    base_sha: str,
) -> tuple[list[str], set[str]]:
    names = _git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        base_sha,
        "--",
        "outputs/decision",
    ).stdout.splitlines()
    report_dates = sorted(
        {
            match.group(1)
            for name in names
            if (match := re.fullmatch(
                r"outputs/decision/decision_report_(20\d{6})\.md",
                name,
            ))
        },
        reverse=True,
    )
    action_dates = {
        match.group(1)
        for name in names
        if (match := re.fullmatch(
            r"outputs/decision/action_plan_(20\d{6})\.json",
            name,
        ))
    }
    return report_dates, action_dates


def _calendar_rows(raw: bytes) -> dict[str, int]:
    headers, rows = _read_csv_bytes(raw, CALENDAR_PATH.as_posix())
    required = {"exchange", "cal_date", "is_open"}
    if not required.issubset(headers):
        raise RecoveryError(f"calendar missing columns: {sorted(required.difference(headers))}")
    result: dict[str, int] = {}
    for row_number, row in enumerate(rows, start=2):
        if row["exchange"] != "SSE":
            raise RecoveryError(f"calendar exchange must be SSE at row {row_number}")
        cal_date = _require_date(row["cal_date"], f"calendar.cal_date[{row_number}]")
        if cal_date in result:
            raise RecoveryError(f"calendar date is duplicated: {cal_date}")
        if row["is_open"] not in {"0", "1"}:
            raise RecoveryError(f"calendar is_open must be 0 or 1: {cal_date}")
        result[cal_date] = int(row["is_open"])
    if not result:
        raise RecoveryError("calendar is empty")
    return result


def _next_open(calendar: Mapping[str, int], value: str) -> str:
    later = sorted(day for day, is_open in calendar.items() if is_open == 1 and day > value)
    if not later:
        raise RecoveryError(f"calendar has no next open date after {value}")
    return later[0]


def _require_contiguous_calendar(calendar: Mapping[str, int], start: str, end: str) -> None:
    cursor = datetime.strptime(start, "%Y%m%d").date()
    stop = datetime.strptime(end, "%Y%m%d").date()
    while cursor <= stop:
        token = cursor.strftime("%Y%m%d")
        if token not in calendar:
            raise RecoveryError(f"calendar coverage gap in date chain: {token}")
        cursor += timedelta(days=1)


def _canonical_eval_path(value: Any, expected: Path, label: str) -> None:
    if not isinstance(value, str) or value != expected.as_posix():
        raise RecoveryError(f"eval {label} path must equal {expected.as_posix()}")


def _report_contract(raw: bytes, report_date: str, signal_date: str, exit_date: str) -> None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RecoveryError(f"report is not UTF-8: {report_date}") from exc
    required_fragments = (
        f"# Decision Report ({report_date})",
        f"- signal_date: **{signal_date}**",
        f"- exec_date: **{report_date}**",
        f"- exit_date: **{exit_date}**",
    )
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise RecoveryError(f"report date contract is incomplete: {report_date}")


def _execution_contract(raw: bytes, relative: Path) -> None:
    headers, rows = _read_csv_bytes(raw, relative.as_posix())
    required = {
        "exec_date",
        "ts_code",
        "jq_code",
        "filled_flag",
        "buy_time",
        "buy_price",
        "fail_reason",
        "buy_slippage_bp",
    }
    if not required.issubset(headers):
        raise RecoveryError(f"execution input missing columns: {relative.as_posix()}")
    if rows:
        raise RecoveryError(f"retrospective NO_TRADE recovery requires zero execution rows: {relative.as_posix()}")


def _candidate_contract(
    raw: bytes,
    relative: Path,
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
) -> tuple[dict[str, str], ...]:
    headers, rows = _read_csv_bytes(raw, relative.as_posix())
    required = {"ts_code", "name", "signal_date", "exec_date", "exit_date"}
    if not required.issubset(headers):
        raise RecoveryError(f"candidate input missing columns: {sorted(required.difference(headers))}")
    if not rows:
        raise RecoveryError(f"candidate input is empty: {relative.as_posix()}")
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        code = row["ts_code"].upper()
        if not CODE_RE.fullmatch(code) or code in seen:
            raise RecoveryError(f"candidate ts_code is invalid or duplicated at row {row_number}")
        seen.add(code)
        row["ts_code"] = code
        expected_dates = {
            "signal_date": signal_date,
            "exec_date": exec_date,
            "exit_date": exit_date,
        }
        for column, expected in expected_dates.items():
            if row[column] != expected:
                raise RecoveryError(f"candidate {column} mismatch at row {row_number}")
        if "trade_date" in headers and row["trade_date"] != signal_date:
            raise RecoveryError(f"candidate trade_date mismatch at row {row_number}")
        if "target_trade_date" in headers and row["target_trade_date"] != exec_date:
            raise RecoveryError(f"candidate target_trade_date mismatch at row {row_number}")
    return tuple(rows)


def _source_bytes(
    root: Path,
    base_sha: str,
    relative: Path,
    cache: dict[Path, bytes],
) -> bytes:
    if relative not in cache:
        raw = _read_regular_bytes(root, relative)
        _assert_source_at_base(root, base_sha, relative, raw)
        cache[relative] = raw
    return cache[relative]


def _load_inputs(
    root: Path,
    base_sha: str,
    report_date: str,
    calendar: Mapping[str, int],
    cache: dict[Path, bytes],
) -> RecoveryInputs:
    report_path = Path(f"outputs/decision/decision_report_{report_date}.md")
    eval_path = Path(f"outputs/decision/eval_{report_date}.json")
    report_raw = _source_bytes(root, base_sha, report_path, cache)
    eval_raw = _source_bytes(root, base_sha, eval_path, cache)
    evaluation = _load_json_bytes(eval_raw, eval_path.as_posix())
    signal_date = _require_date(evaluation.get("signal_date"), f"eval[{report_date}].signal_date")
    exec_date = _require_date(evaluation.get("exec_date"), f"eval[{report_date}].exec_date")
    exit_date = _require_date(evaluation.get("exit_date"), f"eval[{report_date}].exit_date")
    if exec_date != report_date:
        raise RecoveryError(f"eval exec_date must equal report date: {report_date}")
    if calendar.get(signal_date) != 1 or calendar.get(exec_date) != 1 or calendar.get(exit_date) != 1:
        raise RecoveryError(f"signal/report/exit must all be explicit open dates: {report_date}")
    _require_contiguous_calendar(calendar, signal_date, exit_date)
    if _next_open(calendar, signal_date) != exec_date:
        raise RecoveryError(f"signal to report/exec date chain mismatch: {report_date}")
    if _next_open(calendar, exec_date) != exit_date:
        raise RecoveryError(f"exec to exit date chain mismatch: {report_date}")

    candidates_path = Path(f"data/decision/decision_candidates_{signal_date}.csv")
    execution_path = Path(f"data/decision/decision_execution_{report_date}.csv")
    paths = evaluation.get("paths")
    if not isinstance(paths, dict):
        raise RecoveryError(f"eval paths must be an object: {report_date}")
    _canonical_eval_path(paths.get("candidates"), candidates_path, "candidates")
    _canonical_eval_path(paths.get("execution"), execution_path, "execution")
    _canonical_eval_path(paths.get("decision_report"), report_path, "decision_report")

    candidates_raw = _source_bytes(root, base_sha, candidates_path, cache)
    execution_raw = _source_bytes(root, base_sha, execution_path, cache)
    _report_contract(report_raw, report_date, signal_date, exit_date)
    _execution_contract(execution_raw, execution_path)
    candidate_rows = _candidate_contract(
        candidates_raw,
        candidates_path,
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
    )
    paths_for_hash = (
        MANIFEST_PATH,
        CALENDAR_PATH,
        report_path,
        eval_path,
        execution_path,
        candidates_path,
    )
    source_sha256 = {
        path.as_posix(): _sha256(cache[path])
        for path in sorted(paths_for_hash, key=lambda item: item.as_posix())
    }
    return RecoveryInputs(
        report_date=report_date,
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        report_path=report_path,
        eval_path=eval_path,
        execution_path=execution_path,
        candidates_path=candidates_path,
        candidate_rows=candidate_rows,
        source_sha256=source_sha256,
    )


def _candidate_identity(row: Mapping[str, str], rank: int) -> dict[str, Any]:
    industry = ""
    for name in ("industry", "industry_tag", "行业", "行业板块", "board"):
        if row.get(name):
            industry = str(row[name]).strip()
            break
    stage = ""
    for name in ("stage_transition", "advance_stage", "晋阶", "stage"):
        if row.get(name):
            stage = str(row[name]).strip()
            break
    return {
        "rank": rank,
        "action": "REJECT",
        "ts_code": row["ts_code"],
        "name": str(row.get("name") or "").strip(),
        "industry": industry,
        "stage_transition": stage,
        "target_weight": 0.0,
        "trade_rank": 0,
        "trade_gate_pass": 0,
        "trade_shadow_selected": 0,
        "trade_selected": 0,
        "trade_selector_promoted": 0,
        "market_order_allowed": 0,
        "risk_gate_pass": 0,
        "order_type": "LIMIT_ONLY_MANUAL",
        "recommended_max_gap": None,
        "recommended_max_price": None,
        "max_auction_change_pct": None,
        "observation_max_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
        "rejection_reason": "历史缺口回溯恢复；未运行盘前竞价选择，禁止形成交易建议",
        "source_candidate_row": rank,
    }


def _build_plan(inputs: RecoveryInputs, base_sha: str, generated_at: str) -> dict[str, Any]:
    candidates = [
        _candidate_identity(row, rank)
        for rank, row in enumerate(inputs.candidate_rows, start=1)
    ]
    stage_watchlist: list[dict[str, Any]] = []
    for watch_rank, candidate in enumerate(candidates[:10], start=1):
        item = dict(candidate)
        item.update(
            {
                "stage_watch_rank": watch_rank,
                "watch_label": "仅观察",
            }
        )
        stage_watchlist.append(item)
    return {
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "generated_at_utc": generated_at,
        "report_date": inputs.report_date,
        "report_file": inputs.report_path.name,
        "signal_date": inputs.signal_date,
        "exec_date": inputs.exec_date,
        "exit_date": inputs.exit_date,
        "timing_status": "RETROSPECTIVE_LATE_GENERATION",
        "retrospective": True,
        "live_delivery_met": False,
        "status_code": "NO_TRADE_MISSED_LIVE_AUCTION",
        "status_label": "不交易：已错过盘前竞价窗口，仅恢复历史展示",
        "formal_buy_count": 0,
        "shadow_count": 0,
        "stage_watch_count": len(stage_watchlist),
        "stage_watch_eligible_count": len(candidates),
        "stage_watch_display_limit": 10,
        "risk_budget": 0.0,
        "guidance_only": True,
        "broker_connected": False,
        "order_execution": "manual_only",
        "model": {
            "version": "retrospective_gap_recovery_v1",
            "ready": False,
            "promoted": False,
            "prediction_matches_report": False,
            "selection_run": False,
            "retrospective_only": True,
            "trade_selector": {
                "version": "not_run_for_retrospective_recovery",
                "ready": False,
                "promoted": False,
            },
            "promotion_failures": [
                "retrospective_generation_has_no_premarket_auction_evidence"
            ],
        },
        "recovery": {
            "schema_version": "decision_action_gap_recovery_v1",
            "mode": "RETROSPECTIVE_NO_TRADE_ONLY",
            "base_sha": base_sha,
            "source_sha256": inputs.source_sha256,
            "external_data_read": False,
            "minute_data_read": False,
            "t_truth_read": False,
            "t1_truth_read": False,
            "action_plan_latest_changed": False,
            "candidate_contract": "REJECT_ONLY_ZERO_WEIGHT_NO_RECOMMENDED_PRICE",
        },
        "observation_validation": {
            "mode": "NOT_READ_FOR_RETROSPECTIVE_RECOVERY",
            "rows": 0,
            "t_validated_rows": 0,
            "final_rows": 0,
        },
        "stage_watchlist": stage_watchlist,
        "candidates": candidates,
    }


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _require_isolated_output(root: Path, output_root: Path) -> Path:
    if output_root.exists() and output_root.is_symlink():
        raise RecoveryError("--output-root must not be a symlink")
    resolved = output_root.resolve()
    if resolved == root or root in resolved.parents or resolved in root.parents:
        raise RecoveryError("--output-root must be isolated from the repository tree")
    return resolved


def _candidate_target(
    output_root: Path,
    relative: Path,
    *,
    parent_must_exist: bool = False,
) -> Path:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RecoveryError(f"candidate path is not relative: {relative.as_posix()}")
    if output_root.is_symlink():
        raise RecoveryError("--output-root must not become a symlink")
    if output_root.exists() and not output_root.is_dir():
        raise RecoveryError("--output-root must remain a directory")

    parent = output_root
    for component in relative.parts[:-1]:
        parent = parent / component
        if parent.is_symlink():
            raise RecoveryError(
                f"candidate output parent must not be a symlink: {relative.as_posix()}"
            )
        if parent.exists() and not parent.is_dir():
            raise RecoveryError(
                f"candidate output parent must be a directory: {relative.as_posix()}"
            )
    if parent_must_exist and not parent.is_dir():
        raise RecoveryError(
            f"candidate output parent is missing: {relative.as_posix()}"
        )
    resolved_parent = parent.resolve(strict=parent_must_exist)
    if resolved_parent != output_root and output_root not in resolved_parent.parents:
        raise RecoveryError(
            f"candidate output parent escapes --output-root: {relative.as_posix()}"
        )
    return output_root / relative


def _write_all_exclusive(output_root: Path, files: Mapping[Path, bytes]) -> None:
    for relative in files:
        target = _candidate_target(output_root, relative)
        if target.exists() or target.is_symlink():
            raise RecoveryError(f"candidate output conflict: {relative.as_posix()}")
    temporary: list[tuple[Path, Path, Path]] = []
    published: list[Path] = []
    try:
        for relative, raw in files.items():
            target = _candidate_target(output_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = _candidate_target(
                output_root,
                relative,
                parent_must_exist=True,
            )
            if target.exists() or target.is_symlink():
                raise RecoveryError(
                    f"candidate output conflict: {relative.as_posix()}"
                )
            temp_path = target.with_name(f".{target.name}.recover.tmp")
            if temp_path.exists() or temp_path.is_symlink():
                raise RecoveryError(f"temporary output conflict: {relative.as_posix()}")
            descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.append((temp_path, target, relative))
        for temp_path, target, relative in temporary:
            checked_target = _candidate_target(
                output_root,
                relative,
                parent_must_exist=True,
            )
            if checked_target != target:
                raise RecoveryError(
                    f"candidate output target changed: {relative.as_posix()}"
                )
            if target.exists() or target.is_symlink():
                raise RecoveryError(
                    f"candidate output conflict: {relative.as_posix()}"
                )
            os.replace(temp_path, target)
            published.append(target)
    except Exception as exc:
        for temp_path, _, _ in temporary:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        for target in published:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError(
            f"candidate write failed: {type(exc).__name__}"
        ) from exc


def recover(
    root: Path | str,
    report_dates: Sequence[str],
    base_sha: str,
    output_root: Path | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_input = Path(root)
    if root_input.is_symlink():
        raise RecoveryError("--root must not be a symlink")
    root_path = root_input.resolve()
    if not root_path.is_dir():
        raise RecoveryError("--root must be a regular repository directory")
    dates = _parse_report_dates(report_dates)
    _require_exact_base(root_path, base_sha)
    candidate_root = _require_isolated_output(root_path, Path(output_root))

    local_now = (now or datetime.now(timezone.utc)).astimezone(BJT)
    today = local_now.strftime("%Y%m%d")
    for report_date in dates:
        if report_date > today:
            raise RecoveryError(f"future report date is forbidden: {report_date}")
        if report_date == today and local_now.time() < SAME_DAY_RECOVERY_START:
            raise RecoveryError(
                f"same-day recovery is forbidden before market close: {report_date}"
            )

    cache: dict[Path, bytes] = {}
    manifest_raw = _source_bytes(root_path, base_sha, MANIFEST_PATH, cache)
    manifest = _load_json_bytes(manifest_raw, MANIFEST_PATH.as_posix())
    if manifest.get("schema_version") != "decision_model_freeze_v2":
        raise RecoveryError("recovery requires decision_model_freeze_v2")
    if manifest.get("active") is not True:
        raise RecoveryError("recovery requires an active Decision freeze manifest")
    calendar_raw = _source_bytes(root_path, base_sha, CALENDAR_PATH, cache)
    calendar = _calendar_rows(calendar_raw)

    loaded = [
        _load_inputs(root_path, base_sha, report_date, calendar, cache)
        for report_date in dates
    ]
    generated_at = _base_generated_at(root_path, base_sha)
    output_payloads: dict[Path, dict[str, Any]] = {}
    for inputs in loaded:
        relative = Path(f"outputs/decision/action_plan_{inputs.report_date}.json")
        target = root_path / relative
        if (
            _git_target_exists(root_path, base_sha, relative)
            or target.exists()
            or target.is_symlink()
        ):
            raise RecoveryError(f"existing action plan conflict: {relative.as_posix()}")
        output_payloads[relative] = _build_plan(inputs, base_sha, generated_at)

    try:
        index = build_report_index(root_path)
    except Exception as exc:
        raise RecoveryError(
            f"report index candidate build failed: {type(exc).__name__}"
        ) from exc
    index["generated_at_utc"] = generated_at
    reports = [
        item
        for item in index.get("reports", [])
        if isinstance(item, dict)
    ]
    indexed_dates = [str(item.get("report_date") or "") for item in reports]
    base_report_dates, base_action_dates = _exact_base_index_inventory(
        root_path,
        base_sha,
    )
    builder_action_dates = {
        str(item.get("report_date") or "")
        for item in reports
        if item.get("action_available") is True
    }
    expected_action_dates = set(base_report_dates).intersection(base_action_dates)
    if indexed_dates != base_report_dates:
        raise RecoveryError("report index inventory differs from exact base")
    if builder_action_dates != expected_action_dates:
        raise RecoveryError("action availability inventory differs from exact base")
    if any(report_date not in indexed_dates for report_date in dates):
        raise RecoveryError("report index builder omitted a requested report date")
    # The shared index builder truthfully sees only files in the exact-base
    # checkout.  Project the isolated candidate files into the candidate index
    # without ever copying them into the checkout or touching the latest alias.
    requested = set(dates)
    for item in reports:
        report_date = str(item.get("report_date") or "")
        if report_date in requested:
            item["action_available"] = True
            item["action_url"] = (
                f"outputs/decision/action_plan_{report_date}.json"
            )
    available_dates = [
        str(item.get("report_date") or "")
        for item in reports
        if item.get("action_available") is True
    ]
    latest_action_date = available_dates[0] if available_dates else ""
    index["latest_action_report_date"] = latest_action_date
    index["latest_action_url"] = (
        f"outputs/decision/action_plan_{latest_action_date}.json"
        if latest_action_date
        else ""
    )
    all_source_hashes = {
        path: digest
        for inputs in loaded
        for path, digest in inputs.source_sha256.items()
    }
    index["recovery_candidate"] = {
        "schema_version": "decision_action_gap_recovery_index_v1",
        "mode": "RETROSPECTIVE_NO_TRADE_ONLY",
        "base_sha": base_sha,
        "report_dates": dates,
        "source_sha256": dict(sorted(all_source_hashes.items())),
        "action_plan_latest_changed": False,
    }
    index_relative = Path("outputs/decision/report_index.json")
    output_payloads[index_relative] = index

    output_files = {relative: _json_bytes(payload) for relative, payload in output_payloads.items()}
    _write_all_exclusive(candidate_root, output_files)
    changed_paths = [
        f"outputs/decision/action_plan_{report_date}.json" for report_date in dates
    ] + [index_relative.as_posix()]
    receipt = {
        "schema_version": "decision_action_gap_recovery_receipt_v1",
        "status": "candidate_generated",
        "base_sha": base_sha,
        "report_dates": dates,
        "output_root": str(candidate_root),
        "changed_paths": changed_paths,
        "source_sha256": dict(sorted(all_source_hashes.items())),
        "output_sha256": {
            path: _sha256(output_files[Path(path)])
            for path in changed_paths
        },
        "action_plan_latest_changed": False,
    }
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build isolated retrospective NO_TRADE action-plan gap candidates"
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--report-dates",
        nargs="+",
        required=True,
        help="1..5 unique report dates in strict ascending order; commas are accepted",
    )
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--output-root", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = recover(
            args.root,
            args.report_dates,
            args.base_sha,
            args.output_root,
        )
    except RecoveryError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "decision_action_gap_recovery_receipt_v1",
                    "status": "blocked",
                    "error": str(exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
