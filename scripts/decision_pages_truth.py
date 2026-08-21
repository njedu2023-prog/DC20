#!/usr/bin/env python3
"""Strict, dependency-free freshness truth for the Decision Pages build."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATE_RE = re.compile(r"\d{8}")
DATED_REPORT_RE = re.compile(r"decision_report_(\d{8})\.md")
DATED_EVALUATION_RE = re.compile(r"eval_(\d{8})\.json")
DATED_ACTION_RE = re.compile(r"action_plan_(\d{8})\.json")
UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)"
)
REQUIRED_CALENDAR_COLUMNS = frozenset({"exchange", "cal_date", "is_open"})


class DecisionPagesTruthError(ValueError):
    """Raised when Pages freshness evidence is missing or internally inconsistent."""


@dataclass(frozen=True)
class DecisionPagesTruth:
    signal_date: str
    exec_date: str
    report_date: str
    next_open_date: str
    report_age_days: int
    prospective: bool
    stale: bool
    stale_reasons: tuple[str, ...]
    stale_reason: str
    freshness_state: str


@dataclass(frozen=True)
class DecisionActionIndexTruth:
    report_dates: tuple[str, ...]
    action_dates: tuple[str, ...]
    latest_action_report_date: str
    latest_action_url: str


def _strict_date(value: Any, field: str) -> tuple[str, date]:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        raise DecisionPagesTruthError(f"{field} must be an exact YYYYMMDD string")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise DecisionPagesTruthError(f"{field} is not a calendar date: {value!r}") from exc
    if parsed.strftime("%Y%m%d") != value:
        raise DecisionPagesTruthError(f"{field} is not canonical: {value!r}")
    return value, parsed


def _strict_utc_timestamp(value: Any, field: str) -> str:
    if type(value) is not str or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise DecisionPagesTruthError(
            f"{field} must be an exact ISO-8601 UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionPagesTruthError(
            f"{field} is not a real UTC timestamp"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DecisionPagesTruthError(f"{field} is not UTC")
    return value


def _without_symlink_components(path: Path, label: str) -> Path:
    """Return an absolute lexical path after rejecting every symlink component."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise DecisionPagesTruthError(
                f"{label} contains a symlink path component: {current}"
            )
    return absolute


def _site_child(site_root: Path, parts: tuple[str, ...], label: str) -> Path:
    if not parts or any(
        type(part) is not str or not part or part in {".", ".."} or "/" in part
        for part in parts
    ):
        raise DecisionPagesTruthError(f"{label} has an unsafe relative path")
    root = _without_symlink_components(site_root, "site_root")
    candidate = _without_symlink_components(root.joinpath(*parts), label)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DecisionPagesTruthError(f"{label} escapes site_root") from exc
    return candidate


def _site_decision_root(site_root: Path) -> tuple[Path, Path]:
    root = _without_symlink_components(site_root, "site_root")
    if not root.is_dir():
        raise DecisionPagesTruthError(
            f"site_root is missing or not a directory: {root}"
        )
    outputs_root = _site_child(root, ("outputs",), "site outputs directory")
    if not outputs_root.is_dir():
        raise DecisionPagesTruthError(
            f"site outputs is missing or not a directory: {outputs_root}"
        )
    decision_root = _site_child(
        root, ("outputs", "decision"), "site Decision output directory"
    )
    if not decision_root.is_dir():
        raise DecisionPagesTruthError(
            f"site Decision output is missing or not a directory: {decision_root}"
        )
    return root, decision_root


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DecisionPagesTruthError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DecisionPagesTruthError(
            f"{label} is missing, empty, or a symlink: {path}"
        )
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except DecisionPagesTruthError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DecisionPagesTruthError(
            f"{label} is not strict UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise DecisionPagesTruthError(f"{label} must be one JSON object")
    return payload


def _load_evaluation(path: Path) -> dict[str, Any]:
    return _load_json_object(path, "evaluation")


def _load_sse_calendar(path: Path) -> dict[str, bool]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DecisionPagesTruthError(f"SSE calendar is missing, empty, or a symlink: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                raise DecisionPagesTruthError("SSE calendar has duplicate columns")
            missing = REQUIRED_CALENDAR_COLUMNS.difference(fieldnames)
            if missing:
                raise DecisionPagesTruthError(
                    f"SSE calendar is missing columns: {sorted(missing)!r}"
                )
            calendar: dict[str, bool] = {}
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise DecisionPagesTruthError(
                        f"SSE calendar row {row_number} has extra unnamed values"
                    )
                if row.get("exchange") != "SSE":
                    raise DecisionPagesTruthError(
                        f"SSE calendar row {row_number} has invalid exchange"
                    )
                cal_date, _ = _strict_date(
                    row.get("cal_date"), f"SSE calendar row {row_number} cal_date"
                )
                is_open = row.get("is_open")
                if is_open not in {"0", "1"}:
                    raise DecisionPagesTruthError(
                        f"SSE calendar row {row_number} is_open must be 0 or 1"
                    )
                if cal_date in calendar:
                    raise DecisionPagesTruthError(
                        f"SSE calendar contains duplicate cal_date: {cal_date}"
                    )
                calendar[cal_date] = is_open == "1"
    except DecisionPagesTruthError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DecisionPagesTruthError(f"cannot read strict SSE calendar: {path}") from exc
    if not calendar:
        raise DecisionPagesTruthError("SSE calendar has no rows")
    return calendar


def _load_report_binding(path: Path, report_date: str) -> tuple[str, str]:
    """Return the unique signal/exec dates carried by one dated report."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DecisionPagesTruthError(
            f"dated report is missing, empty, or a symlink: {path}"
        )
    try:
        body = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DecisionPagesTruthError(
            f"dated report is not strict UTF-8 text: {path}"
        ) from exc
    expected_heading = f"# Decision Report ({report_date})"
    if body.splitlines()[:1] != [expected_heading]:
        raise DecisionPagesTruthError(
            f"dated report heading does not match its filename: {path.name}"
        )

    bindings: dict[str, str] = {}
    for field in ("signal_date", "exec_date"):
        matches = re.findall(
            rf"^- {field}: \*\*(\d{{8}})\*\*$",
            body,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            raise DecisionPagesTruthError(
                f"dated report must contain exactly one canonical {field}: {path.name}"
            )
        value, _ = _strict_date(matches[0], f"dated report {path.name}.{field}")
        bindings[field] = value
    if bindings["exec_date"] != report_date:
        raise DecisionPagesTruthError(
            f"dated report exec_date does not match its filename: {path.name}"
        )
    return bindings["signal_date"], bindings["exec_date"]


def project_report_index_action_truth(
    *,
    source_report_index_path: Path,
    site_root: Path,
) -> DecisionActionIndexTruth:
    """Rebuild the public index from isolated site inventory, then validate it.

    The checked-in index is used only as a v2 contract anchor. Its inventory is
    deliberately not trusted because a Daily writer may add a report/evaluation
    pair without rewriting that Auction-owned file.
    """

    source_index_path = _without_symlink_components(
        Path(source_report_index_path), "source report_index"
    )
    source_index = _load_json_object(source_index_path, "source report_index")
    if source_index.get("schema_version") != "decision_report_index_v2_action_truth":
        raise DecisionPagesTruthError(
            "source report_index.schema_version is not decision_report_index_v2_action_truth"
        )
    source_latest_report_date, _ = _strict_date(
        source_index.get("latest_report_date"),
        "source report_index.latest_report_date",
    )

    site_root, decision_root = _site_decision_root(Path(site_root))

    report_paths: dict[str, Path] = {}
    evaluation_paths: dict[str, Path] = {}
    action_paths: dict[str, Path] = {}
    for path in decision_root.iterdir():
        for pattern, inventory, label in (
            (DATED_REPORT_RE, report_paths, "dated report"),
            (DATED_EVALUATION_RE, evaluation_paths, "dated evaluation"),
            (DATED_ACTION_RE, action_paths, "dated action"),
        ):
            match = pattern.fullmatch(path.name)
            if match is None:
                continue
            report_date, _ = _strict_date(match.group(1), f"{label} filename date")
            if report_date in inventory:
                raise DecisionPagesTruthError(
                    f"duplicate {label} date in site inventory: {report_date}"
                )
            safe_path = _site_child(
                site_root,
                ("outputs", "decision", path.name),
                f"{label} path",
            )
            if safe_path != _without_symlink_components(path, f"{label} path"):
                raise DecisionPagesTruthError(f"{label} escapes site Decision output")
            if not safe_path.is_file() or safe_path.stat().st_size <= 0:
                raise DecisionPagesTruthError(
                    f"{label} is missing or empty: {safe_path}"
                )
            inventory[report_date] = safe_path

    if not report_paths:
        raise DecisionPagesTruthError("site Decision output has no dated reports")
    if set(report_paths) != set(evaluation_paths):
        missing_evaluations = sorted(set(report_paths).difference(evaluation_paths))
        orphan_evaluations = sorted(set(evaluation_paths).difference(report_paths))
        raise DecisionPagesTruthError(
            "dated report/evaluation inventory mismatch: "
            f"missing_evaluations={missing_evaluations!r}, "
            f"orphan_evaluations={orphan_evaluations!r}"
        )
    orphan_actions = sorted(set(action_paths).difference(report_paths))
    if orphan_actions:
        raise DecisionPagesTruthError(
            f"dated actions have no matching report: {orphan_actions!r}"
        )

    reports: list[dict[str, Any]] = []
    action_dates: list[str] = []
    for report_date in sorted(report_paths, reverse=True):
        report_signal_date, report_exec_date = _load_report_binding(
            report_paths[report_date], report_date
        )
        evaluation = _load_json_object(
            evaluation_paths[report_date], f"dated evaluation for {report_date}"
        )
        evaluation_signal_date, signal_day = _strict_date(
            evaluation.get("signal_date"),
            f"dated evaluation for {report_date}.signal_date",
        )
        evaluation_exec_date, exec_day = _strict_date(
            evaluation.get("exec_date"),
            f"dated evaluation for {report_date}.exec_date",
        )
        if evaluation_exec_date != report_date:
            raise DecisionPagesTruthError(
                f"dated evaluation exec_date does not match its filename: {report_date}"
            )
        if signal_day >= exec_day:
            raise DecisionPagesTruthError(
                f"dated evaluation signal_date must precede exec_date: {report_date}"
            )
        if (report_signal_date, report_exec_date) != (
            evaluation_signal_date,
            evaluation_exec_date,
        ):
            raise DecisionPagesTruthError(
                f"dated report/evaluation date binding mismatch: {report_date}"
            )

        row: dict[str, Any] = {
            "report_date": report_date,
            "report_file": f"decision_report_{report_date}.md",
            "report_url": f"outputs/decision/decision_report_{report_date}.md",
            "eval_url": f"outputs/decision/eval_{report_date}.json",
            "action_available": report_date in action_paths,
        }
        if report_date in action_paths:
            action = _load_json_object(
                action_paths[report_date], f"dated action for {report_date}"
            )
            action_report_date, _ = _strict_date(
                action.get("report_date"),
                f"dated action for {report_date}.report_date",
            )
            if action_report_date != report_date:
                raise DecisionPagesTruthError(
                    f"dated action for {report_date} contains a different report_date"
                )
            row["action_url"] = f"outputs/decision/action_plan_{report_date}.json"
            action_dates.append(report_date)
        reports.append(row)

    latest_report_date = str(reports[0]["report_date"])
    latest_action_date = action_dates[0] if action_dates else ""
    inventory_projected = latest_report_date != source_latest_report_date
    generated_at_utc = None
    if not inventory_projected:
        generated_at_utc = _strict_utc_timestamp(
            source_index.get("generated_at_utc"),
            "source report_index.generated_at_utc",
        )
    projected_index = {
        "schema_version": "decision_report_index_v2_action_truth",
        "generated_at_utc": generated_at_utc,
        "inventory_projected": inventory_projected,
        "source_latest_report_date": source_latest_report_date,
        "latest_report_date": latest_report_date,
        "latest_report_file": f"decision_report_{latest_report_date}.md",
        "latest_action_report_date": latest_action_date,
        "latest_action_url": (
            f"outputs/decision/action_plan_{latest_action_date}.json"
            if latest_action_date
            else ""
        ),
        "reports": reports,
    }
    index_path = _site_child(
        site_root,
        ("outputs", "decision", "report_index.json"),
        "projected report_index",
    )
    if index_path.exists() and not index_path.is_file():
        raise DecisionPagesTruthError(
            f"projected report_index exists but is not a regular file: {index_path}"
        )
    index_path.write_text(
        json.dumps(projected_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return validate_report_index_action_truth(
        report_index_path=index_path,
        site_root=site_root,
    )


def validate_report_index_action_truth(
    *,
    report_index_path: Path,
    site_root: Path,
) -> DecisionActionIndexTruth:
    """Prove that every advertised action URL matches one valid dated plan."""

    site_root, _decision_root = _site_decision_root(Path(site_root))
    expected_index_path = _site_child(
        site_root,
        ("outputs", "decision", "report_index.json"),
        "report_index path",
    )
    supplied_index_path = _without_symlink_components(
        Path(report_index_path), "report_index path"
    )
    if supplied_index_path != expected_index_path:
        raise DecisionPagesTruthError(
            "report_index path escapes or is not the exact site Decision index"
        )
    report_index = _load_json_object(supplied_index_path, "report_index")
    if report_index.get("schema_version") != "decision_report_index_v2_action_truth":
        raise DecisionPagesTruthError(
            "report_index.schema_version is not decision_report_index_v2_action_truth"
        )
    reports = report_index.get("reports")
    if not isinstance(reports, list) or not reports:
        raise DecisionPagesTruthError("report_index.reports must be a nonempty list")

    report_dates: list[str] = []
    action_dates: list[str] = []
    for row_number, report in enumerate(reports, start=1):
        if not isinstance(report, dict):
            raise DecisionPagesTruthError(
                f"report_index.reports[{row_number}] must be an object"
            )
        report_date, _ = _strict_date(
            report.get("report_date"),
            f"report_index.reports[{row_number}].report_date",
        )
        if report_date in report_dates:
            raise DecisionPagesTruthError(
                f"report_index contains duplicate report_date: {report_date}"
            )
        expected_report_file = f"decision_report_{report_date}.md"
        expected_report_url = (
            f"outputs/decision/decision_report_{report_date}.md"
        )
        expected_eval_url = f"outputs/decision/eval_{report_date}.json"
        for field, expected in (
            ("report_file", expected_report_file),
            ("report_url", expected_report_url),
            ("eval_url", expected_eval_url),
        ):
            if report.get(field) != expected:
                raise DecisionPagesTruthError(
                    f"report_index.reports[{row_number}].{field} is not exact"
                )

        action_available = report.get("action_available")
        if type(action_available) is not bool:
            raise DecisionPagesTruthError(
                f"report_index.reports[{row_number}].action_available must be a bool"
            )
        expected_action_url = (
            f"outputs/decision/action_plan_{report_date}.json"
        )
        action_path = _site_child(
            site_root,
            ("outputs", "decision", f"action_plan_{report_date}.json"),
            f"dated action for {report_date}",
        )

        action_payload: dict[str, Any] | None = None
        action_error: DecisionPagesTruthError | None = None
        try:
            action_payload = _load_json_object(
                action_path, f"dated action for {report_date}"
            )
            action_report_date, _ = _strict_date(
                action_payload.get("report_date"),
                f"dated action for {report_date}.report_date",
            )
            if action_report_date != report_date:
                raise DecisionPagesTruthError(
                    f"dated action for {report_date} contains a different report_date"
                )
        except DecisionPagesTruthError as exc:
            action_error = exc

        valid_dated_action = action_payload is not None and action_error is None
        if action_available:
            if report.get("action_url") != expected_action_url:
                raise DecisionPagesTruthError(
                    f"report_index.reports[{row_number}].action_url is not exact"
                )
            if not valid_dated_action:
                raise DecisionPagesTruthError(
                    f"advertised dated action is invalid for {report_date}: {action_error}"
                )
            action_dates.append(report_date)
        else:
            if "action_url" in report:
                raise DecisionPagesTruthError(
                    f"unavailable action for {report_date} must not have action_url"
                )
            if valid_dated_action:
                raise DecisionPagesTruthError(
                    f"valid dated action for {report_date} is hidden by action_available=false"
                )
        report_dates.append(report_date)

    if tuple(report_dates) != tuple(sorted(report_dates, reverse=True)):
        raise DecisionPagesTruthError(
            "report_index.reports must be in strictly descending report_date order"
        )
    latest_report_date = report_dates[0]
    if report_index.get("latest_report_date") != latest_report_date:
        raise DecisionPagesTruthError(
            "report_index.latest_report_date does not match reports[0]"
        )
    if report_index.get("latest_report_file") != (
        f"decision_report_{latest_report_date}.md"
    ):
        raise DecisionPagesTruthError(
            "report_index.latest_report_file does not match latest_report_date"
        )

    expected_latest_action_date = action_dates[0] if action_dates else ""
    expected_latest_action_url = (
        f"outputs/decision/action_plan_{expected_latest_action_date}.json"
        if expected_latest_action_date
        else ""
    )
    if type(report_index.get("latest_action_report_date")) is not str:
        raise DecisionPagesTruthError(
            "report_index.latest_action_report_date must be a string"
        )
    if type(report_index.get("latest_action_url")) is not str:
        raise DecisionPagesTruthError(
            "report_index.latest_action_url must be a string"
        )
    if report_index.get("latest_action_report_date") != expected_latest_action_date:
        raise DecisionPagesTruthError(
            "report_index.latest_action_report_date is not the newest valid dated action"
        )
    if report_index.get("latest_action_url") != expected_latest_action_url:
        raise DecisionPagesTruthError(
            "report_index.latest_action_url is not the newest valid dated action URL"
        )

    return DecisionActionIndexTruth(
        report_dates=tuple(report_dates),
        action_dates=tuple(action_dates),
        latest_action_report_date=expected_latest_action_date,
        latest_action_url=expected_latest_action_url,
    )


def assess_decision_pages_truth(
    *,
    evaluation_path: Path,
    calendar_path: Path,
    report_date: str,
    today: date,
    freeze_active: bool,
    max_report_age_days: int,
) -> DecisionPagesTruth:
    """Validate report timing and classify current/prospective/stale Pages truth."""

    if type(today) is not date:
        raise DecisionPagesTruthError("today must be a datetime.date")
    if type(freeze_active) is not bool:
        raise DecisionPagesTruthError("freeze_active must be a bool")
    if type(max_report_age_days) is not int or max_report_age_days < 0:
        raise DecisionPagesTruthError("max_report_age_days must be a non-negative int")

    canonical_report_date, report_day = _strict_date(report_date, "report_date")
    evaluation = _load_evaluation(Path(evaluation_path))
    signal_date, signal_day = _strict_date(
        evaluation.get("signal_date"), "evaluation.signal_date"
    )
    exec_date, exec_day = _strict_date(
        evaluation.get("exec_date"), "evaluation.exec_date"
    )
    if exec_date != canonical_report_date:
        raise DecisionPagesTruthError(
            "evaluation.exec_date does not match report_index.latest_report_date"
        )
    if signal_day >= exec_day:
        raise DecisionPagesTruthError("evaluation.signal_date must precede exec_date")

    calendar = _load_sse_calendar(Path(calendar_path))
    cursor = signal_day
    while cursor <= exec_day:
        key = cursor.strftime("%Y%m%d")
        if key not in calendar:
            raise DecisionPagesTruthError(
                f"SSE calendar does not cover report interval date: {key}"
            )
        cursor += timedelta(days=1)
    if calendar.get(signal_date) is not True:
        raise DecisionPagesTruthError("evaluation.signal_date is not an open SSE session")
    if calendar.get(exec_date) is not True:
        raise DecisionPagesTruthError("evaluation.exec_date is not an open SSE session")

    next_open_date = ""
    cursor = signal_day + timedelta(days=1)
    while cursor <= exec_day:
        key = cursor.strftime("%Y%m%d")
        if calendar[key]:
            next_open_date = key
            break
        cursor += timedelta(days=1)
    if not next_open_date:
        raise DecisionPagesTruthError("SSE calendar has no open session after signal_date")

    report_age_days = (today - report_day).days
    prospective = bool(
        report_day > today
        and signal_day <= today
        and exec_date == next_open_date
    )
    stale_reasons: list[str] = []
    if not freeze_active:
        stale_reasons.append("freeze_inactive")
    if report_age_days < 0 and not prospective:
        stale_reasons.append("report_date_in_future")
    elif report_age_days > max_report_age_days:
        stale_reasons.append("report_expired")
    stale = bool(stale_reasons)
    stale_reason = "+".join(stale_reasons) if stale_reasons else "none"
    freshness_state = (
        "STALE"
        if stale
        else "PROSPECTIVE_NEXT_SESSION"
        if prospective
        else "CURRENT"
    )
    return DecisionPagesTruth(
        signal_date=signal_date,
        exec_date=exec_date,
        report_date=canonical_report_date,
        next_open_date=next_open_date,
        report_age_days=report_age_days,
        prospective=prospective,
        stale=stale,
        stale_reasons=tuple(stale_reasons),
        stale_reason=stale_reason,
        freshness_state=freshness_state,
    )


__all__ = [
    "DecisionActionIndexTruth",
    "DecisionPagesTruth",
    "DecisionPagesTruthError",
    "assess_decision_pages_truth",
    "project_report_index_action_truth",
    "validate_report_index_action_truth",
]
