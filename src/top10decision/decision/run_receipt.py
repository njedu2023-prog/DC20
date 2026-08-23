from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


DECISION_RUN_RECEIPT_SCHEMA = "decision_run_receipt_v1"
DATE_RE = re.compile(r"20\d{6}")


class DecisionRunReceiptError(ValueError):
    """Raised when a Daily run receipt is missing or not exactly date-bound."""


def _strict_date(value: Any, field: str) -> tuple[str, date]:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        raise DecisionRunReceiptError(f"{field} must be an exact YYYYMMDD string")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise DecisionRunReceiptError(f"{field} is not a calendar date") from exc
    if parsed.strftime("%Y%m%d") != value:
        raise DecisionRunReceiptError(f"{field} is not canonical")
    return value, parsed


def _strict_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DecisionRunReceiptError(f"{label} is missing, empty, or a symlink")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DecisionRunReceiptError(f"{label} has duplicate JSON key: {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except DecisionRunReceiptError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DecisionRunReceiptError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise DecisionRunReceiptError(f"{label} must be one JSON object")
    return payload


def write_decision_run_receipt(
    *,
    requested_trade_date: str,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    report_path: str,
    eval_path: str,
    stop_trading: bool,
    receipt_path: str = "",
) -> Path | None:
    """Write the exact outputs selected by this run when a receipt was requested."""

    configured_path = receipt_path or os.environ.get("DECISION_RUN_RECEIPT_PATH", "")
    if not configured_path:
        return None
    signal_date, signal_day = _strict_date(signal_date, "receipt.signal_date")
    exec_date, exec_day = _strict_date(exec_date, "receipt.exec_date")
    exit_date, exit_day = _strict_date(exit_date, "receipt.exit_date")
    if not signal_day < exec_day < exit_day:
        raise DecisionRunReceiptError("receipt date order must be signal < exec < exit")
    if requested_trade_date:
        requested_trade_date, _ = _strict_date(
            requested_trade_date,
            "receipt.requested_trade_date",
        )
        if requested_trade_date != signal_date:
            raise DecisionRunReceiptError(
                "receipt requested_trade_date must equal signal_date"
            )
    expected_report_path = f"outputs/decision/decision_report_{exec_date}.md"
    expected_eval_path = f"outputs/decision/eval_{exec_date}.json"
    if report_path != expected_report_path:
        raise DecisionRunReceiptError("receipt report_path is not exact-date canonical")
    if eval_path != expected_eval_path:
        raise DecisionRunReceiptError("receipt eval_path is not exact-date canonical")
    if type(stop_trading) is not bool:
        raise DecisionRunReceiptError("receipt stop_trading must be boolean")
    payload = {
        "schema_version": DECISION_RUN_RECEIPT_SCHEMA,
        "requested_trade_date": requested_trade_date,
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "report_path": report_path,
        "eval_path": eval_path,
        "stop_trading": stop_trading,
    }
    path = Path(configured_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DecisionRunReceiptError("receipt output path cannot be a symlink")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def validate_decision_run_receipt(
    *,
    root: Path,
    receipt_path: Path,
    requested_trade_date: str = "",
) -> dict[str, Any]:
    """Validate receipt, exact dated report, and exact dated evaluation together."""

    receipt = _strict_json_object(receipt_path, "Daily run receipt")
    if receipt.get("schema_version") != DECISION_RUN_RECEIPT_SCHEMA:
        raise DecisionRunReceiptError("Daily run receipt schema_version is invalid")
    signal_date, signal_day = _strict_date(
        receipt.get("signal_date"), "receipt.signal_date"
    )
    exec_date, exec_day = _strict_date(receipt.get("exec_date"), "receipt.exec_date")
    exit_date, exit_day = _strict_date(receipt.get("exit_date"), "receipt.exit_date")
    if not signal_day < exec_day < exit_day:
        raise DecisionRunReceiptError("receipt date order must be signal < exec < exit")
    receipt_requested = receipt.get("requested_trade_date")
    if type(receipt_requested) is not str:
        raise DecisionRunReceiptError("receipt.requested_trade_date must be a string")
    if requested_trade_date:
        requested_trade_date, _ = _strict_date(
            requested_trade_date,
            "requested TRADE_DATE",
        )
        if receipt_requested != requested_trade_date or signal_date != requested_trade_date:
            raise DecisionRunReceiptError(
                "requested TRADE_DATE does not match receipt signal date"
            )
    elif receipt_requested:
        raise DecisionRunReceiptError(
            "receipt unexpectedly claims a requested trade date"
        )
    if type(receipt.get("stop_trading")) is not bool:
        raise DecisionRunReceiptError("receipt.stop_trading must be boolean")

    expected_report = f"outputs/decision/decision_report_{exec_date}.md"
    expected_eval = f"outputs/decision/eval_{exec_date}.json"
    if receipt.get("report_path") != expected_report:
        raise DecisionRunReceiptError("receipt.report_path is not exact-date canonical")
    if receipt.get("eval_path") != expected_eval:
        raise DecisionRunReceiptError("receipt.eval_path is not exact-date canonical")
    root = root.resolve()
    report_path = root / expected_report
    eval_path = root / expected_eval
    if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size <= 0:
        raise DecisionRunReceiptError("receipt report is missing, empty, or a symlink")
    try:
        report = report_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DecisionRunReceiptError("receipt report is not UTF-8 text") from exc
    report_lines = report.splitlines()
    if report_lines[:1] != [f"# Decision Report ({exec_date})"]:
        raise DecisionRunReceiptError("receipt report heading does not match exec_date")
    for field, value in (
        ("signal_date", signal_date),
        ("exec_date", exec_date),
        ("exit_date", exit_date),
    ):
        if report_lines.count(f"- {field}: **{value}**") != 1:
            raise DecisionRunReceiptError(
                f"receipt report {field} binding is not exact and unique"
            )

    evaluation = _strict_json_object(eval_path, "receipt evaluation")
    for field, value in (
        ("signal_date", signal_date),
        ("exec_date", exec_date),
        ("exit_date", exit_date),
        ("requested_trade_date", receipt_requested),
        ("stop_trading", receipt["stop_trading"]),
    ):
        if evaluation.get(field) != value:
            raise DecisionRunReceiptError(
                f"receipt evaluation {field} binding is invalid"
            )
    return receipt


__all__ = [
    "DECISION_RUN_RECEIPT_SCHEMA",
    "DecisionRunReceiptError",
    "validate_decision_run_receipt",
    "write_decision_run_receipt",
]
