#!/usr/bin/env python3
"""Build the Pages-only, time-honest DC20 three-rank research archive.

The archive is a projection of the frozen walk-forward/holdout OOF Top10 file.
It never scores a historical row with the final fitted model.  Promotion OOF
values are exposed as historical research fields.  The two unpromoted heads
remain null in all official fields; their OOF values live only under an
explicit ``research_diagnostics`` namespace.

Only Python's standard library is used so GitHub Pages can rebuild and verify
the projection without installing the training runtime.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


OOF_RELATIVE_PATH = Path(
    "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
)
VALIDATION_RELATIVE_PATH = Path(
    "models/decision_three_engines/validation_latest.json"
)
SOURCES_MANIFEST_RELATIVE_PATH = Path(
    "models/decision_three_rank_history_sources.json"
)
CALENDAR_RELATIVE_PATH = Path("data/market/trade_cal_sse.csv")
REPORTS_RELATIVE_PATH = Path("outputs/decision")
LEDGER_RELATIVE_PATH = Path(
    "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
)
LEDGER_MANIFEST_RELATIVE_PATH = Path(
    "data/decision_three_engines/five_year_ledger_manifest.json"
)

ARCHIVE_SCHEMA = "dc20_three_rank_history_archive_v2"
EVIDENCE_SCHEMA = "dc20_three_rank_history_evidence_v2"
REPORT_MAP_SCHEMA = "dc20_three_rank_history_report_map_v1"
STATISTICS_SCHEMA = "dc20_three_rank_history_statistics_v2"
INDEX_SCHEMA = "dc20_three_rank_history_index_v2"
PFILL_SHADOW_SCHEMA = "dc20_p_fill_shadow_oof_cumulative_v1"
FORWARD_PFILL_SHADOW_SCHEMA = "dc20_p_fill_shadow_forward_top2_v1"

OFFICIAL_PROMOTION_STATUS = "TIME_HONEST_OOF_RESEARCH"
UNRELEASED_STATUS = "RESEARCH_NOT_RELEASED"
AVAILABLE_STATUS = "AVAILABLE_TIME_HONEST_OOF_RESEARCH"
ARCHIVED_STATUS = "ARCHIVED_NONCANONICAL_SOURCE_EXEC"
UNAVAILABLE_STATUS = "UNAVAILABLE_SOURCE_AFTER_OOF_CUTOFF"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^20\d{6}$")
FORWARD_SNAPSHOT_RE = re.compile(r"^three_rank_top10_(20\d{6})\.json$")

REQUIRED_OOF_COLUMNS = {
    "signal_date",
    "buy_date",
    "target_exit_date",
    "ts_code",
    "stage",
    "board",
    "promotion_hit",
    "market_fill",
    "big_loss_hit",
    "profit_hit",
    "net_return",
    "promotion_pool_size",
    "top10_selected",
    "top10_members_sha256",
    "promotion_rank",
    "predicted_promotion_probability",
    "promotion_rank_score",
    "promotion_oof_fold",
    "promotion_oof_fold_kind",
    "promotion_oof_train_end",
    "promotion_oof_model_kind",
    "promotion_oof_calibration",
    "promotion_oof_selection_eligible",
    "promotion_oof_selection_composite_lift",
    "big_loss_safety_rank",
    "predicted_big_loss_probability",
    "big_loss_rank_score",
    "big_loss_oof_fold",
    "big_loss_oof_fold_kind",
    "big_loss_oof_train_end",
    "big_loss_oof_model_kind",
    "big_loss_oof_calibration",
    "big_loss_oof_selection_eligible",
    "big_loss_oof_selection_composite_lift",
    "profit_rank",
    "predicted_profit_probability",
    "profit_rank_score",
    "profit_oof_fold",
    "profit_oof_fold_kind",
    "profit_oof_train_end",
    "profit_oof_model_kind",
    "profit_oof_calibration",
    "profit_oof_selection_eligible",
    "profit_oof_selection_composite_lift",
    "p_fill_shadow_rank",
    "p_fill_shadow_probability",
    "p_fill_shadow_score",
    "p_fill_shadow_oof_fold",
    "p_fill_shadow_oof_fold_kind",
    "p_fill_shadow_oof_train_end",
    "p_fill_shadow_oof_model_kind",
    "p_fill_shadow_oof_calibration",
    "p_fill_shadow_oof_selection_eligible",
    "p_fill_shadow_oof_selection_composite_lift",
}

CSV_FIELDS = (
    "signal_date",
    "exec_date",
    "exit_date",
    "ts_code",
    "stage_transition",
    "board",
    "promotion_rank",
    "predicted_promotion_probability",
    "big_loss_safety_rank",
    "predicted_big_loss_probability",
    "profit_rank",
    "predicted_profit_probability",
    "research_big_loss_safety_rank",
    "research_predicted_big_loss_probability",
    "research_profit_rank",
    "research_predicted_profit_probability",
    "research_p_fill_shadow_rank",
    "research_p_fill_shadow_probability",
    "research_p_fill_shadow_selected",
    "promotion_hit",
    "market_fill_proxy",
    "big_loss_hit",
    "profit_hit",
    "net_return",
    "promotion_oof_train_end",
    "big_loss_oof_train_end",
    "profit_oof_train_end",
    "p_fill_shadow_oof_train_end",
    "top10_members_sha256",
    "date_bundle_sha256",
    "source_report_dates",
    "actual_execution_claimed",
)


class HistoryProjectionError(RuntimeError):
    """Raised when a source or generated archive contract is unsafe."""


def _fail(message: str) -> None:
    raise HistoryProjectionError(message)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    if not path.is_file() or path.stat().st_size <= 0:
        _fail(f"missing or empty source: {path}")
    return _sha256_bytes(path.read_bytes())


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (text + "\n").encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_json_bytes(value, pretty=False).rstrip(b"\n"))


def _write_bytes(path: Path, raw: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": _sha256_bytes(raw)}


def _date(value: Any, *, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if not DATE_RE.fullmatch(text):
        _fail(f"invalid {field}: {value!r}")
    return text


def _optional_float(value: Any, *, field: str) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise HistoryProjectionError(f"invalid numeric {field}: {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        _fail(f"non-finite numeric {field}: {value!r}")
    return number


def _required_int(value: Any, *, field: str) -> int:
    number = _optional_float(value, field=field)
    if number is None or not number.is_integer():
        _fail(f"invalid integer {field}: {value!r}")
    return int(number)


def _optional_int(value: Any, *, field: str) -> int | None:
    if value is None or not str(value).strip():
        return None
    return _required_int(value, field=field)


def _optional_binary(value: Any, *, field: str) -> int | None:
    number = _optional_float(value, field=field)
    if number is None:
        return None
    if number not in (0.0, 1.0):
        _fail(f"invalid binary {field}: {value!r}")
    return int(number)


def _optional_bool(value: Any, *, field: str) -> bool | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    _fail(f"invalid boolean {field}: {value!r}")


def _top10_members_sha256(signal_date: str, codes: Iterable[str]) -> str:
    payload = {
        "schema": "dc20_three_rank_member_set_v1",
        "signal_date": _date(signal_date, field="member signal_date"),
        "members": sorted({str(code).strip().upper() for code in codes if str(code).strip()}),
    }
    return _canonical_sha256(payload)


def _binding(path: Path, source_root: Path) -> dict[str, Any]:
    try:
        relative = path.relative_to(source_root).as_posix()
    except ValueError as exc:
        raise HistoryProjectionError(f"source escaped source root: {path}") from exc
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryProjectionError(f"invalid JSON source: {path}") from exc
    if not isinstance(value, dict):
        _fail(f"JSON source must be an object: {path}")
    return value


def _read_calendar(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["exchange", "cal_date", "is_open", "pretrade_date"]:
            _fail("trading calendar fields are not exact")
        rows = list(reader)
    if not rows:
        _fail("trading calendar contains no rows")
    parsed_dates: list[str] = []
    previous_natural_date = None
    previous_open_date = None
    open_dates: list[str] = []
    for index, row in enumerate(rows):
        if set(row) != {"exchange", "cal_date", "is_open", "pretrade_date"}:
            _fail("trading calendar row fields are not exact")
        if row.get("exchange") != "SSE":
            _fail("trading calendar exchange is not exactly SSE")
        cal_date = _date(row.get("cal_date"), field="calendar cal_date")
        try:
            natural_date = datetime.strptime(cal_date, "%Y%m%d").date()
        except ValueError as exc:
            raise HistoryProjectionError(
                f"invalid calendar natural date: {cal_date}"
            ) from exc
        if previous_natural_date is not None and natural_date != previous_natural_date + timedelta(days=1):
            _fail("trading calendar natural dates are not complete and consecutive")
        if cal_date in parsed_dates:
            _fail("trading calendar dates are duplicated")
        parsed_dates.append(cal_date)
        is_open = str(row.get("is_open") or "").strip()
        if is_open not in {"0", "1"}:
            _fail("trading calendar is_open escaped 0/1")
        pretrade_date = _date(
            row.get("pretrade_date"), field="calendar pretrade_date"
        )
        if index == 0:
            previous_open_date = pretrade_date
        elif pretrade_date != previous_open_date:
            _fail("trading calendar pretrade_date chain is invalid")
        if is_open == "1":
            open_dates.append(cal_date)
            previous_open_date = cal_date
        previous_natural_date = natural_date
    if not open_dates:
        _fail("trading calendar contains no open dates")
    return {date: index for index, date in enumerate(open_dates)}


def _read_oof(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HistoryProjectionError(f"invalid OOF gzip CSV: {path}") from exc
    missing = sorted(REQUIRED_OOF_COLUMNS.difference(fields))
    if missing:
        _fail(f"OOF source missing columns: {missing}")
    if not rows:
        _fail("OOF source is empty")
    return rows, fields


def _validate_validation(
    validation: Mapping[str, Any], *, oof_path: Path, oof_sha256: str
) -> None:
    if validation.get("schema_version") != "decision_three_engine_validation_v2":
        _fail("unexpected three-engine validation schema")
    artifact = validation.get("artifacts", {}).get("oof_top10", {})
    if artifact.get("path") != OOF_RELATIVE_PATH.as_posix():
        _fail("validation does not bind the exact frozen OOF path")
    if artifact.get("sha256") != oof_sha256:
        _fail("validation OOF SHA256 does not match source bytes")
    if int(artifact.get("bytes") or -1) != oof_path.stat().st_size:
        _fail("validation OOF byte count does not match source bytes")
    if validation.get("release_contract", {}).get("actual_execution_claimed") is not False:
        _fail("validation unexpectedly claims actual execution")
    heads = validation.get("heads")
    if not isinstance(heads, dict):
        _fail("validation heads are missing")
    expected = {
        "promotion": ("READY", True),
        "big_loss": ("NOT_READY_VALIDATION_GATE", False),
        "profit": ("NOT_READY_VALIDATION_GATE", False),
    }
    for head, (status, promoted) in expected.items():
        value = heads.get(head)
        if not isinstance(value, dict):
            _fail(f"validation head is missing: {head}")
        if value.get("status") != status or value.get("promoted") is not promoted:
            _fail(f"validation release state changed for {head}")
    p_fill_shadow = heads.get("p_fill_shadow")
    if not isinstance(p_fill_shadow, dict):
        _fail("validation p_fill_shadow head is missing")
    execution_truth = p_fill_shadow.get("execution_truth_claim")
    if (
        p_fill_shadow.get("schema_version")
        != "decision_three_engine_head_validation_v1"
        or p_fill_shadow.get("head") != "p_fill_shadow"
        or p_fill_shadow.get("status") != "SHADOW_READY"
        or p_fill_shadow.get("promoted") is not False
        or p_fill_shadow.get("target") != "market_fill"
        or p_fill_shadow.get("training_scope")
        != "historical_promotion_oof_top10_shadow_only"
        or p_fill_shadow.get("cannot_change_core_members_or_ranks") is not True
        or not isinstance(execution_truth, dict)
        or execution_truth.get("actual_execution_claimed") is not False
        or execution_truth.get("actual_order_fill_observed") is not False
        or not isinstance(p_fill_shadow.get("gate_checks"), dict)
        or not p_fill_shadow["gate_checks"]
        or not all(value is True for value in p_fill_shadow["gate_checks"].values())
        or p_fill_shadow.get("gate_failures") != []
    ):
        _fail("validation p_fill_shadow release contract is invalid")


def _parse_report_binding(
    declared: Mapping[str, Any], source_root: Path
) -> dict[str, Any]:
    report_date = _date(declared.get("report_date"), field="source report_date")
    declared_signal_date = _date(
        declared.get("signal_date"), field="source signal_date"
    )
    declared_exec_date = _date(declared.get("exec_date"), field="source exec_date")
    expected_paths = {
        "report": (
            REPORTS_RELATIVE_PATH / f"decision_report_{report_date}.md"
        ).as_posix(),
        "evaluation": (
            REPORTS_RELATIVE_PATH / f"eval_{report_date}.json"
        ).as_posix(),
    }
    verified: dict[str, dict[str, Any]] = {}
    for kind, expected_path in expected_paths.items():
        value = declared.get(kind)
        if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
            _fail(f"source manifest {kind} binding is malformed: {report_date}")
        if value.get("path") != expected_path:
            _fail(f"source manifest {kind} path is not exact: {report_date}")
        if (
            not isinstance(value.get("bytes"), int)
            or value["bytes"] <= 0
            or not isinstance(value.get("sha256"), str)
            or not SHA256_RE.fullmatch(value["sha256"])
        ):
            _fail(f"source manifest {kind} size/hash is invalid: {report_date}")
        actual = _binding(source_root / expected_path, source_root)
        if actual != value:
            _fail(f"source manifest {kind} bytes drifted: {report_date}")
        verified[kind] = actual

    report_path = source_root / expected_paths["report"]
    eval_path = source_root / expected_paths["evaluation"]
    evaluation = _read_json(eval_path)
    signal_date = _date(evaluation.get("signal_date"), field="eval signal_date")
    exec_date = _date(evaluation.get("exec_date"), field="eval exec_date")
    if signal_date != declared_signal_date or exec_date != declared_exec_date:
        _fail(f"source manifest dates drifted: {report_date}")
    if exec_date != report_date:
        _fail(f"eval exec_date does not match filename: {eval_path}")
    text = report_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if lines[:1] != [f"# Decision Report ({report_date})"]:
        _fail(f"report heading does not match filename: {report_path}")
    for field, expected in (("signal_date", signal_date), ("exec_date", exec_date)):
        if lines.count(f"- {field}: **{expected}**") != 1:
            _fail(f"report {field} does not match evaluation: {report_path}")
    return {
        "report_date": report_date,
        "signal_date": signal_date,
        "source_exec_date": exec_date,
        "report": verified["report"],
        "evaluation": verified["evaluation"],
    }


def _load_report_pairs(
    source_root: Path, manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != "dc20_three_rank_history_sources_v1":
        _fail("unexpected history sources manifest schema")
    if manifest.get("inventory_kind") != "immutable_exact_report_eval_pairs":
        _fail("history sources manifest is not an immutable exact inventory")
    if (
        manifest.get("calendar_source") != "tushare:trade_cal:SSE"
        or manifest.get("strict_calendar") is not True
        or manifest.get("exchange") != "SSE"
    ):
        _fail("history sources strict SSE calendar contract is invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        _fail("history sources manifest entries are missing")
    if manifest.get("report_eval_pairs") != len(entries):
        _fail("history sources manifest count is inconsistent")
    expected_inventory_sha256 = _canonical_sha256(entries)
    if manifest.get("canonical_inventory_sha256") != expected_inventory_sha256:
        _fail("history sources canonical inventory SHA256 mismatch")
    report_dates = [str(entry.get("report_date") or "") for entry in entries if isinstance(entry, dict)]
    if len(report_dates) != len(entries) or len(report_dates) != len(set(report_dates)):
        _fail("history sources report dates are invalid or duplicated")
    if report_dates != sorted(report_dates):
        _fail("history sources inventory is not sorted by report date")
    return [
        _parse_report_binding(entry, source_root)
        for entry in entries
        if isinstance(entry, dict)
    ]


def _validate_rank(ranks: Sequence[int | None], expected_count: int, *, field: str) -> None:
    if all(rank is None for rank in ranks):
        return
    if any(rank is None for rank in ranks):
        _fail(f"partially missing {field}")
    actual = sorted(int(rank) for rank in ranks if rank is not None)
    if actual != list(range(1, expected_count + 1)):
        _fail(f"non-contiguous {field}: {actual}")


def _oof_metadata(row: Mapping[str, str], head: str) -> dict[str, Any]:
    rank_field = {
        "promotion": "promotion_rank",
        "big_loss": "big_loss_safety_rank",
        "profit": "profit_rank",
        "p_fill_shadow": "p_fill_shadow_rank",
    }[head]
    probability_field = {
        "promotion": "predicted_promotion_probability",
        "big_loss": "predicted_big_loss_probability",
        "profit": "predicted_profit_probability",
        "p_fill_shadow": "p_fill_shadow_probability",
    }[head]
    rank_score_field = {
        "promotion": "promotion_rank_score",
        "big_loss": "big_loss_rank_score",
        "profit": "profit_rank_score",
        "p_fill_shadow": "p_fill_shadow_score",
    }[head]
    metadata = {
        "rank": _optional_int(row.get(rank_field), field=rank_field),
        "predicted_probability": _optional_float(
            row.get(probability_field), field=probability_field
        ),
        "rank_score": _optional_float(
            row.get(rank_score_field), field=rank_score_field
        ),
        "fold": _optional_int(row.get(f"{head}_oof_fold"), field=f"{head}_oof_fold"),
        "fold_kind": str(row.get(f"{head}_oof_fold_kind") or "").strip() or None,
        "train_end": (
            _date(row.get(f"{head}_oof_train_end"), field=f"{head}_oof_train_end")
            if str(row.get(f"{head}_oof_train_end") or "").strip()
            else None
        ),
        "model_kind": str(row.get(f"{head}_oof_model_kind") or "").strip() or None,
        "calibration": str(row.get(f"{head}_oof_calibration") or "").strip() or None,
        "selection_eligible": _optional_bool(
            row.get(f"{head}_oof_selection_eligible"),
            field=f"{head}_oof_selection_eligible",
        ),
        "selection_composite_lift": _optional_float(
            row.get(f"{head}_oof_selection_composite_lift"),
            field=f"{head}_oof_selection_composite_lift",
        ),
    }
    presence = [value is not None for value in metadata.values()]
    if any(presence) and not all(presence):
        _fail(f"partially populated {head} OOF metadata")
    probability = metadata["predicted_probability"]
    if probability is not None and not 0.0 <= probability <= 1.0:
        _fail(f"{head} OOF probability escaped [0,1]")
    return metadata


def _validate_rank_probability_order(
    metadata: Sequence[Mapping[str, Any]], *, head: str
) -> None:
    populated = [value for value in metadata if value.get("rank") is not None]
    if not populated:
        return
    ordered = sorted(populated, key=lambda value: int(value["rank"]))
    probabilities = [float(value["predicted_probability"]) for value in ordered]
    if head == "big_loss":
        valid = all(
            probabilities[index] <= probabilities[index + 1]
            for index in range(len(probabilities) - 1)
        )
    else:
        valid = all(
            probabilities[index] >= probabilities[index + 1]
            for index in range(len(probabilities) - 1)
        )
    if not valid:
        _fail(f"{head} OOF probabilities are not monotonic by rank")


def _validate_oof_archive_contract(
    validation: Mapping[str, Any],
    oof_rows: Sequence[Mapping[str, str]],
    grouped: Mapping[str, Sequence[Mapping[str, str]]],
) -> str:
    actual_rows = len(oof_rows)
    actual_dates = len(grouped)
    if actual_rows <= 0 or actual_dates <= 0:
        _fail("OOF archive is empty")

    artifact = validation.get("artifacts", {}).get("oof_top10")
    integrity = validation.get("oof_top10")
    if not isinstance(artifact, dict):
        _fail("validation OOF artifact contract is missing")
    if not isinstance(integrity, dict):
        _fail("validation OOF integrity contract is missing")
    if (
        _required_int(artifact.get("rows"), field="validation artifact OOF rows")
        != actual_rows
        or _required_int(
            artifact.get("dates"), field="validation artifact OOF dates"
        )
        != actual_dates
    ):
        _fail("validation artifact OOF row/date counts do not match source")
    if integrity.get("valid") is not True or integrity.get("failures") != []:
        _fail("validation OOF integrity contract is not PASS")
    if (
        _required_int(integrity.get("rows"), field="validation OOF rows")
        != actual_rows
        or _required_int(integrity.get("dates"), field="validation OOF dates")
        != actual_dates
    ):
        _fail("validation OOF integrity row/date counts do not match source")

    ordered_dates = sorted(grouped)
    oof_cutoff = ordered_dates[-1]
    source = validation.get("source")
    if not isinstance(source, dict):
        _fail("validation source contract is missing")
    source_cutoff = _date(source.get("end"), field="validation source end")
    if source_cutoff != oof_cutoff:
        _fail("validation source cutoff does not match OOF cutoff")

    allowed_fold_kinds = {
        "development_walkforward",
        "final_independent_holdout",
    }
    fold_contracts: dict[str, dict[int, dict[str, Any]]] = {
        head: {}
        for head in ("promotion", "big_loss", "profit", "p_fill_shadow")
    }
    date_contracts: dict[str, dict[str, tuple[Any, ...]]] = {
        head: {} for head in fold_contracts
    }
    date_metadata_rows: dict[str, dict[str, int]] = {
        head: defaultdict(int) for head in fold_contracts
    }
    for row in oof_rows:
        signal_date = _date(row.get("signal_date"), field="OOF signal_date")
        for head in fold_contracts:
            metadata = _oof_metadata(row, head)
            fold = metadata["fold"]
            if fold is None:
                continue
            fold_kind = metadata["fold_kind"]
            if fold_kind not in allowed_fold_kinds:
                _fail(f"{head} OOF fold kind is invalid: {fold_kind}")
            train_end = metadata["train_end"]
            if not train_end or train_end >= signal_date:
                _fail(f"{head} OOF train_end is not before D: {signal_date}")
            signature = (
                fold_kind,
                train_end,
                metadata["model_kind"],
                metadata["calibration"],
                metadata["selection_eligible"],
                metadata["selection_composite_lift"],
            )
            existing = fold_contracts[head].setdefault(
                int(fold), {"signature": signature, "dates": set()}
            )
            if existing["signature"] != signature:
                _fail(f"{head} OOF fold metadata is inconsistent: {fold}")
            existing["dates"].add(signal_date)
            date_signature = (int(fold),) + signature
            existing_date = date_contracts[head].setdefault(
                signal_date, date_signature
            )
            if existing_date != date_signature:
                _fail(f"{head} OOF date has mixed fold metadata: {signal_date}")
            date_metadata_rows[head][signal_date] += 1

    for head, folds in fold_contracts.items():
        if not folds:
            _fail(f"{head} OOF fold inventory is empty")
        head_dates = sorted(date_contracts[head])
        first_source_index = ordered_dates.index(head_dates[0])
        if head_dates != ordered_dates[first_source_index:]:
            _fail(f"{head} OOF dates are not a contiguous source tail")
        for signal_date in head_dates:
            if date_metadata_rows[head][signal_date] != len(grouped[signal_date]):
                _fail(f"{head} OOF date is only partially populated: {signal_date}")

        fold_ids = sorted(folds)
        if fold_ids != list(range(1, len(fold_ids) + 1)):
            _fail(f"{head} OOF fold IDs are not contiguous")
        previous_end: str | None = None
        previous_train_end: str | None = None
        for fold in fold_ids:
            contract = folds[fold]
            fold_dates = sorted(contract["dates"])
            train_end = contract["signature"][1]
            if train_end >= fold_dates[0]:
                _fail(f"{head} OOF fold train_end is not before its first D: {fold}")
            if previous_end is not None and fold_dates[0] <= previous_end:
                _fail(f"{head} OOF folds are not chronologically ordered")
            if previous_train_end is not None and train_end <= previous_train_end:
                _fail(f"{head} OOF fold train_end is not strictly increasing")
            previous_train_end = train_end
            previous_end = fold_dates[-1]
        final_fold_ids = [
            fold
            for fold in fold_ids
            if folds[fold]["signature"][0] == "final_independent_holdout"
        ]
        if final_fold_ids != [fold_ids[-1]]:
            _fail(f"{head} OOF final holdout fold is not unique and highest")

    p_fill_head = validation.get("heads", {}).get("p_fill_shadow")
    if not isinstance(p_fill_head, dict):
        _fail("validation p_fill_shadow head is missing")
    p_fill_dates = sorted(date_contracts["p_fill_shadow"])
    p_fill_rows = [
        row
        for signal_date in p_fill_dates
        for row in grouped[signal_date]
    ]
    p_fill_daily_rates: list[float] = []
    p_fill_rank1_labels: list[int] = []
    for signal_date in p_fill_dates:
        labels: list[int] = []
        for row in grouped[signal_date]:
            label = _optional_binary(row.get("market_fill"), field="market_fill")
            if label is None:
                _fail(f"p_fill_shadow OOF truth is missing: {signal_date}")
            labels.append(label)
            if _required_int(
                row.get("p_fill_shadow_rank"), field="p_fill_shadow_rank"
            ) == 1:
                p_fill_rank1_labels.append(label)
        p_fill_daily_rates.append(sum(labels) / len(labels))
    probability_contract = p_fill_head.get("probability")
    ranking_contract = p_fill_head.get("ranking")
    if not isinstance(probability_contract, dict) or not isinstance(
        ranking_contract, dict
    ):
        _fail("validation p_fill_shadow probability/ranking contract is missing")
    date_balanced_pool_rate = sum(p_fill_daily_rates) / len(p_fill_daily_rates)
    rank1_rate = sum(p_fill_rank1_labels) / len(p_fill_rank1_labels)
    probability_positive_rate = _optional_float(
        probability_contract.get("positive_rate"),
        field="p_fill_shadow positive_rate",
    )
    ranking_pool_target_rate = _optional_float(
        ranking_contract.get("pool_target_rate"),
        field="p_fill_shadow pool_target_rate",
    )
    ranking_rank1_target_rate = _optional_float(
        ranking_contract.get("rank1_target_rate"),
        field="p_fill_shadow rank1_target_rate",
    )
    if (
        _required_int(
            probability_contract.get("rows"),
            field="p_fill_shadow probability rows",
        )
        != len(p_fill_rows)
        or _required_int(
            probability_contract.get("dates"),
            field="p_fill_shadow probability dates",
        )
        != len(p_fill_dates)
        or _required_int(
            ranking_contract.get("dates"),
            field="p_fill_shadow ranking dates",
        )
        != len(p_fill_dates)
        or probability_positive_rate is None
        or abs(probability_positive_rate - date_balanced_pool_rate) > 1e-12
        or ranking_pool_target_rate is None
        or abs(ranking_pool_target_rate - date_balanced_pool_rate) > 1e-12
        or ranking_rank1_target_rate is None
        or abs(ranking_rank1_target_rate - rank1_rate) > 1e-12
    ):
        _fail("validation p_fill_shadow probability/ranking contract drifted")

    p_fill_selection_audit = p_fill_head.get("outer_fold_selection_audit")
    if not isinstance(p_fill_selection_audit, list):
        _fail("validation p_fill_shadow fold selection audit is missing")
    expected_selection_audit = []
    for fold in sorted(fold_contracts["p_fill_shadow"]):
        signature = fold_contracts["p_fill_shadow"][fold]["signature"]
        expected_selection_audit.append(
            {
                "p_fill_shadow_oof_fold": fold,
                "p_fill_shadow_oof_model_kind": signature[2],
                "p_fill_shadow_oof_calibration": signature[3],
                "p_fill_shadow_oof_selection_eligible": signature[4],
                "p_fill_shadow_oof_selection_composite_lift": signature[5],
            }
        )
    if p_fill_selection_audit != expected_selection_audit:
        _fail("validation p_fill_shadow fold selection audit drifted")

    configuration = validation.get("configuration")
    promotion = validation.get("heads", {}).get("promotion")
    if not isinstance(configuration, dict) or not isinstance(promotion, dict):
        _fail("validation promotion holdout contract is missing")
    if configuration.get("release_mode") is not True:
        _fail("validation was not produced in release mode")
    holdout_count = _required_int(
        configuration.get("final_holdout_dates"),
        field="validation final_holdout_dates",
    )
    if holdout_count <= 0 or holdout_count >= actual_dates:
        _fail("validation final holdout size is invalid")
    holdout = promotion.get("final_independent_holdout")
    if not isinstance(holdout, dict):
        _fail("validation promotion final holdout is missing")
    if (
        holdout.get("model_refit_within_holdout") is not False
        or holdout.get("model_family_and_calibrator_locked_before_holdout") is not True
        or _required_int(
            holdout.get("minimum_dates"), field="promotion holdout minimum_dates"
        )
        != holdout_count
        or _required_int(
            holdout.get("calendar_dates"), field="promotion holdout calendar_dates"
        )
        != holdout_count
        or _required_int(
            holdout.get("labeled_dates"), field="promotion holdout labeled_dates"
        )
        != holdout_count
    ):
        _fail("validation promotion final holdout contract drifted")
    for head, folds in fold_contracts.items():
        head_dates = sorted(date_contracts[head])
        if len(head_dates) <= holdout_count:
            _fail(f"{head} OOF history is not longer than the final holdout")
        final_fold = max(folds)
        if sorted(folds[final_fold]["dates"]) != head_dates[-holdout_count:]:
            _fail(f"{head} final holdout is not the exact head OOF date tail")
    p_fill_holdout = p_fill_head.get("final_independent_holdout")
    if (
        not isinstance(p_fill_holdout, dict)
        or p_fill_holdout.get("model_refit_within_holdout") is not False
        or p_fill_holdout.get(
            "model_family_and_calibrator_locked_before_holdout"
        )
        is not True
        or _required_int(
            p_fill_holdout.get("minimum_dates"),
            field="p_fill_shadow holdout minimum_dates",
        )
        != holdout_count
        or _required_int(
            p_fill_holdout.get("calendar_dates"),
            field="p_fill_shadow holdout calendar_dates",
        )
        != holdout_count
        or _required_int(
            p_fill_holdout.get("labeled_dates"),
            field="p_fill_shadow holdout labeled_dates",
        )
        != holdout_count
    ):
        _fail("validation p_fill_shadow final holdout contract drifted")
    promotion_final_dates = sorted(fold_contracts["promotion"][max(
        fold_contracts["promotion"]
    )]["dates"])
    if promotion_final_dates != ordered_dates[-holdout_count:]:
        _fail("promotion final holdout is not the exact source OOF date tail")

    production = promotion.get("production")
    if not isinstance(production, dict) or production.get("bundle_present") is not True:
        _fail("validation promotion production bundle is missing")
    trained_start = _date(
        production.get("trained_signal_start"),
        field="promotion production trained_signal_start",
    )
    trained_end = _date(
        production.get("trained_signal_end"),
        field="promotion production trained_signal_end",
    )
    monotonicity = production.get("calibration_monotonicity")
    if not isinstance(monotonicity, dict):
        _fail("promotion production calibration contract is missing")
    audit = monotonicity.get(
        "independent_production_rank_audit"
    )
    if not isinstance(audit, dict):
        _fail("promotion production rank audit is missing")
    if production.get("independent_rank_audit") != audit:
        _fail("promotion production rank audit copies disagree")
    audit_start = _date(
        audit.get("start"), field="promotion production audit start"
    )
    audit_end = _date(audit.get("end"), field="promotion production audit end")
    minimum_audit_dates = _required_int(
        configuration.get("minimum_inner_selection_dates"),
        field="validation minimum_inner_selection_dates",
    )
    embargo_dates = _required_int(
        configuration.get("embargo_dates"), field="validation embargo_dates"
    )
    if minimum_audit_dates <= 0 or embargo_dates < 0:
        _fail("promotion production rank audit configuration is invalid")
    audit_rows = _required_int(
        audit.get("rows"), field="promotion production audit rows"
    )
    nonconstant_dates = _required_int(
        audit.get("nonconstant_dates"),
        field="promotion production audit nonconstant_dates",
    )
    nonconstant_fraction = _optional_float(
        audit.get("nonconstant_date_fraction"),
        field="promotion production audit nonconstant_date_fraction",
    )
    minimum_nonconstant_fraction = _optional_float(
        audit.get("minimum_nonconstant_date_fraction"),
        field="promotion production audit minimum_nonconstant_date_fraction",
    )
    if (
        production.get("independent_rank_audit_valid") is not True
        or production.get("calibration_monotonicity_valid") is not True
        or production.get("constant_rank_forbidden") is not True
        or audit_rows < 2 * minimum_audit_dates
        or not 0 <= nonconstant_dates <= minimum_audit_dates
        or nonconstant_fraction is None
        or not 0.0 <= nonconstant_fraction <= 1.0
        or minimum_nonconstant_fraction is None
        or not 0.0 <= minimum_nonconstant_fraction <= 1.0
        or minimum_nonconstant_fraction < 0.90
        or abs(
            nonconstant_fraction
            - (nonconstant_dates / minimum_audit_dates)
        )
        > 1e-12
        or nonconstant_fraction < minimum_nonconstant_fraction
        or _required_int(
            audit.get("calendar_dates"),
            field="promotion production audit calendar_dates",
        )
        != minimum_audit_dates
        or _required_int(
            audit.get("eligible_dates"),
            field="promotion production audit eligible_dates",
        )
        != minimum_audit_dates
        or _required_int(
            audit.get("minimum_eligible_dates"),
            field="promotion production audit minimum_eligible_dates",
        )
        != minimum_audit_dates
        or _required_int(
            audit.get("embargo_dates"),
            field="promotion production audit embargo_dates",
        )
        != embargo_dates
    ):
        _fail("promotion production rank audit contract is invalid")
    date_index = {date: index for index, date in enumerate(ordered_dates)}
    expected_audit_dates = ordered_dates[-minimum_audit_dates:]
    if (
        trained_end not in date_index
        or audit_start not in date_index
        or audit_end not in date_index
        or audit_start != expected_audit_dates[0]
        or audit_end != expected_audit_dates[-1]
        or date_index[audit_start] - date_index[trained_end] != embargo_dates + 1
    ):
        _fail("promotion production rank audit window is invalid")
    if (
        production.get("post_gate_locked_family_refit") is not True
        or not trained_start <= trained_end < audit_start <= audit_end
        or audit_end != source_cutoff
        or audit.get("valid") is not True
        or audit.get("truth_or_performance_used") is not False
        or audit.get("fit_or_calibration_rows_used") is not False
    ):
        _fail("promotion production refit/audit chronology is invalid")
    return oof_cutoff


def _project_date(
    signal_date: str,
    source_rows: Sequence[Mapping[str, str]],
    report_bindings: Sequence[Mapping[str, Any]],
    open_date_index: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ordered = sorted(
        source_rows,
        key=lambda row: _required_int(row.get("promotion_rank"), field="promotion_rank"),
    )
    if not ordered:
        _fail(f"empty OOF group: {signal_date}")
    exec_dates = {_date(row.get("buy_date"), field="buy_date") for row in ordered}
    exit_dates = {
        _date(row.get("target_exit_date"), field="target_exit_date") for row in ordered
    }
    if len(exec_dates) != 1 or len(exit_dates) != 1:
        _fail(f"OOF group has mixed T/T+1 dates: {signal_date}")
    exec_date = next(iter(exec_dates))
    exit_date = next(iter(exit_dates))
    if not signal_date < exec_date < exit_date:
        _fail(f"OOF D/T/T+1 order is invalid: {signal_date}/{exec_date}/{exit_date}")
    if any(date not in open_date_index for date in (signal_date, exec_date, exit_date)):
        _fail(f"OOF D/T/T+1 includes a closed session: {signal_date}/{exec_date}/{exit_date}")
    if not (
        open_date_index[exec_date] == open_date_index[signal_date] + 1
        and open_date_index[exit_date] == open_date_index[exec_date] + 1
    ):
        _fail(
            "OOF D/T/T+1 are not adjacent trading sessions: "
            f"{signal_date}/{exec_date}/{exit_date}"
        )

    pool_sizes = {
        _required_int(row.get("promotion_pool_size"), field="promotion_pool_size")
        for row in ordered
    }
    if len(pool_sizes) != 1:
        _fail(f"OOF group has mixed pool sizes: {signal_date}")
    pool_size = next(iter(pool_sizes))
    expected_count = min(10, pool_size)
    if len(ordered) != expected_count:
        _fail(
            f"OOF group count does not match min(10,pool): {signal_date} "
            f"{len(ordered)} != {expected_count}"
        )
    if any(_required_int(row.get("top10_selected"), field="top10_selected") != 1 for row in ordered):
        _fail(f"OOF group contains a non-selected row: {signal_date}")

    claims = {str(row.get("top10_members_sha256") or "").strip() for row in ordered}
    members_sha256 = _top10_members_sha256(
        signal_date, (str(row.get("ts_code") or "") for row in ordered)
    )
    if claims != {members_sha256}:
        _fail(f"OOF member hash mismatch: {signal_date}")
    codes = [str(row.get("ts_code") or "").strip().upper() for row in ordered]
    if not all(codes) or len(codes) != len(set(codes)):
        _fail(f"OOF group has empty or duplicate codes: {signal_date}")

    projection_rows: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    per_head_ranks: dict[str, list[int | None]] = defaultdict(list)
    raw_projection: list[dict[str, Any]] = []
    source_report_dates = sorted(str(binding["report_date"]) for binding in report_bindings)
    for source in ordered:
        head_meta = {
            head: _oof_metadata(source, head)
            for head in ("promotion", "big_loss", "profit", "p_fill_shadow")
        }
        for head, meta in head_meta.items():
            per_head_ranks[head].append(meta["rank"])
            if meta["rank"] is not None:
                if not meta["train_end"] or not meta["train_end"] < signal_date:
                    _fail(f"{head} OOF train_end is not before D: {signal_date}")
        promotion = head_meta["promotion"]
        p_fill_shadow = {
            **head_meta["p_fill_shadow"],
            "shadow_selected": (
                head_meta["p_fill_shadow"]["rank"] is not None
                and head_meta["p_fill_shadow"]["rank"] <= 2
            ),
        }
        if promotion["rank"] is None or promotion["predicted_probability"] is None:
            _fail(f"promotion OOF field is missing: {signal_date}")
        stage = _required_int(source.get("stage"), field="stage")
        if stage not in (2, 3):
            _fail(f"OOF stage escaped 2/3 hard range: {signal_date}")
        row = {
            "ts_code": str(source.get("ts_code") or "").strip().upper(),
            "stage_transition": f"{stage}→{stage + 1}",
            "board": str(source.get("board") or "").strip(),
            "top10_selected": 1,
            "promotion_rank": promotion["rank"],
            "predicted_promotion_probability": promotion["predicted_probability"],
            # Unpromoted heads never enter official historical fields.
            "big_loss_safety_rank": None,
            "predicted_big_loss_probability": None,
            "profit_rank": None,
            "predicted_profit_probability": None,
            "research_diagnostics": {
                "big_loss": head_meta["big_loss"],
                "profit": head_meta["profit"],
                "p_fill_shadow": p_fill_shadow,
            },
            "actual_outcomes": {
                "promotion_hit": _optional_binary(
                    source.get("promotion_hit"), field="promotion_hit"
                ),
                "market_fill_proxy": _optional_binary(
                    source.get("market_fill"), field="market_fill"
                ),
                "big_loss_hit": _optional_binary(
                    source.get("big_loss_hit"), field="big_loss_hit"
                ),
                "profit_hit": _optional_binary(
                    source.get("profit_hit"), field="profit_hit"
                ),
                "net_return": _optional_float(source.get("net_return"), field="net_return"),
            },
            "promotion_oof": promotion,
        }
        projection_rows.append(row)
        raw_projection.append(
            {
                key: source.get(key)
                for key in sorted(REQUIRED_OOF_COLUMNS)
            }
        )

    for head in ("promotion", "big_loss", "profit", "p_fill_shadow"):
        _validate_rank(per_head_ranks[head], expected_count, field=f"{head} rank")
        _validate_rank_probability_order(
            [
                (
                    row["promotion_oof"]
                    if head == "promotion"
                    else row["research_diagnostics"][head]
                )
                for row in projection_rows
            ],
            head=head,
        )

    source_bindings = [
        {
            "report_date": binding["report_date"],
            "source_exec_date": binding["source_exec_date"],
            "source_exec_matches_oof_t": binding["source_exec_date"] == exec_date,
            "report": binding["report"],
            "evaluation": binding["evaluation"],
        }
        for binding in report_bindings
    ]
    base_record = {
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "status": AVAILABLE_STATUS,
        "research_only": True,
        "actual_execution_claimed": False,
        "promotion_pool_size": pool_size,
        "top10_count": expected_count,
        "top10_members_sha256": members_sha256,
        "models": {
            "promotion": {
                "status": OFFICIAL_PROMOTION_STATUS,
                "official_historical_fields_populated": True,
            },
            "big_loss": {
                "status": UNRELEASED_STATUS,
                "official_historical_fields_populated": False,
                "diagnostics_only": True,
            },
            "profit": {
                "status": UNRELEASED_STATUS,
                "official_historical_fields_populated": False,
                "diagnostics_only": True,
            },
            "p_fill_shadow": {
                "status": (
                    "SHADOW_READY_TIME_HONEST_OOF_DIAGNOSTIC"
                    if per_head_ranks["p_fill_shadow"]
                    and per_head_ranks["p_fill_shadow"][0] is not None
                    else "SHADOW_OOF_NOT_YET_AVAILABLE"
                ),
                "official_historical_fields_populated": False,
                "diagnostics_only": True,
                "may_change_core_members_or_ranks": False,
                "may_create_trade_action": False,
            },
        },
        "source_report_dates": source_report_dates,
        "rows": projection_rows,
    }
    date_bundle_sha256 = _canonical_sha256(base_record)
    record = dict(base_record, date_bundle_sha256=date_bundle_sha256)
    evidence = {
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "date_bundle_sha256": date_bundle_sha256,
        "top10_members_sha256": members_sha256,
        "row_count": expected_count,
        "source_oof_rows_sha256": _canonical_sha256(raw_projection),
        "source_reports": source_bindings,
        "integrity": {
            "train_end_strictly_before_signal_date": True,
            "d_before_t_before_tplus1": True,
            "all_sessions_open": True,
            "adjacent_trading_sessions": True,
            "promotion_ranks_contiguous": True,
            "member_hash_verified": True,
            "official_big_loss_fields_all_null": True,
            "official_profit_fields_all_null": True,
            "p_fill_shadow_ranks_contiguous_or_unavailable": True,
            "p_fill_shadow_may_change_core_members_or_ranks": False,
            "actual_execution_claimed": False,
        },
    }

    for row in projection_rows:
        diagnostics = row["research_diagnostics"]
        actual = row["actual_outcomes"]
        csv_rows.append(
            {
                "signal_date": signal_date,
                "exec_date": exec_date,
                "exit_date": exit_date,
                "ts_code": row["ts_code"],
                "stage_transition": row["stage_transition"],
                "board": row["board"],
                "promotion_rank": row["promotion_rank"],
                "predicted_promotion_probability": row[
                    "predicted_promotion_probability"
                ],
                "big_loss_safety_rank": "",
                "predicted_big_loss_probability": "",
                "profit_rank": "",
                "predicted_profit_probability": "",
                "research_big_loss_safety_rank": diagnostics["big_loss"]["rank"],
                "research_predicted_big_loss_probability": diagnostics["big_loss"][
                    "predicted_probability"
                ],
                "research_profit_rank": diagnostics["profit"]["rank"],
                "research_predicted_profit_probability": diagnostics["profit"][
                    "predicted_probability"
                ],
                "research_p_fill_shadow_rank": diagnostics["p_fill_shadow"]["rank"],
                "research_p_fill_shadow_probability": diagnostics[
                    "p_fill_shadow"
                ]["predicted_probability"],
                "research_p_fill_shadow_selected": (
                    "true"
                    if diagnostics["p_fill_shadow"]["shadow_selected"]
                    else "false"
                ),
                "promotion_hit": actual["promotion_hit"],
                "market_fill_proxy": actual["market_fill_proxy"],
                "big_loss_hit": actual["big_loss_hit"],
                "profit_hit": actual["profit_hit"],
                "net_return": actual["net_return"],
                "promotion_oof_train_end": row["promotion_oof"]["train_end"],
                "big_loss_oof_train_end": diagnostics["big_loss"]["train_end"],
                "profit_oof_train_end": diagnostics["profit"]["train_end"],
                "p_fill_shadow_oof_train_end": diagnostics["p_fill_shadow"][
                    "train_end"
                ],
                "top10_members_sha256": members_sha256,
                "date_bundle_sha256": date_bundle_sha256,
                "source_report_dates": ";".join(source_report_dates),
                "actual_execution_claimed": "false",
            }
        )
    return record, evidence, csv_rows


def _csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    return buffer.getvalue().encode("utf-8")


def _deterministic_gzip(raw: bytes) -> bytes:
    """Return a reproducible gzip stream (no filename and mtime=0)."""

    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=buffer,
        mtime=0,
    ) as handle:
        handle.write(raw)
    return buffer.getvalue()


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _wilson_95(successes: int, trials: int) -> dict[str, float | int | None]:
    if trials <= 0:
        return {"successes": successes, "trials": trials, "low": None, "high": None}
    z = 1.959963984540054
    rate = successes / trials
    denominator = 1.0 + z * z / trials
    center = (rate + z * z / (2.0 * trials)) / denominator
    half_width = (
        z
        * math.sqrt(
            rate * (1.0 - rate) / trials + z * z / (4.0 * trials * trials)
        )
        / denominator
    )
    return {
        "successes": successes,
        "trials": trials,
        "low": max(0.0, center - half_width),
        "high": min(1.0, center + half_width),
    }


def _p_fill_shadow_oof_statistics(
    projected_by_signal: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a strict OOF-only Top2 diagnostic ledger.

    This is a counterfactual analysis of the public-market ``market_fill``
    proxy.  It does not consume forward snapshots or actual orders, and its
    return section is not a profit-model evaluation.
    """

    daily: list[dict[str, Any]] = []
    requested_slots = 0
    selected_slots = 0
    fill_hits = 0
    baseline_rows = 0
    baseline_hits = 0
    filled_slots = 0
    observed_filled_returns = 0
    conditional_return_sum = 0.0
    conditional_return_wins = 0
    resolved_selected_slots = 0
    dates_with_two_slots = 0
    dates_with_one_slot = 0
    fully_resolved_dates = 0
    top2_daily_rates: list[float] = []
    baseline_daily_rates: list[float] = []
    counterfactual_daily_returns: list[float] = []
    counterfactual_nav = 1.0
    return_incomplete_dates: list[str] = []
    per_rank = {
        1: {"selected_slots": 0, "fill_hits": 0},
        2: {"selected_slots": 0, "fill_hits": 0},
    }

    for signal_date in sorted(projected_by_signal):
        record = projected_by_signal[signal_date]
        rows = record.get("rows")
        if not isinstance(rows, list) or not rows:
            _fail(f"invalid projected rows for p_fill_shadow: {signal_date}")
        populated = [
            isinstance(row.get("research_diagnostics"), dict)
            and isinstance(row["research_diagnostics"].get("p_fill_shadow"), dict)
            and row["research_diagnostics"]["p_fill_shadow"].get("rank") is not None
            for row in rows
            if isinstance(row, dict)
        ]
        if len(populated) != len(rows):
            _fail(f"invalid p_fill_shadow projected row: {signal_date}")
        if not any(populated):
            continue
        if not all(populated):
            _fail(f"partially populated p_fill_shadow projected date: {signal_date}")

        ordered = sorted(
            rows,
            key=lambda row: int(
                row["research_diagnostics"]["p_fill_shadow"]["rank"]
            ),
        )
        ranks = [
            int(row["research_diagnostics"]["p_fill_shadow"]["rank"])
            for row in ordered
        ]
        if ranks != list(range(1, len(ordered) + 1)):
            _fail(f"p_fill_shadow OOF ranks are not strict 1..N: {signal_date}")
        selected = [
            row
            for row in ordered
            if int(row["research_diagnostics"]["p_fill_shadow"]["rank"]) <= 2
        ]
        expected_slots = min(2, len(ordered))
        if len(selected) != expected_slots:
            _fail(f"p_fill_shadow OOF strict Top2 selection drifted: {signal_date}")

        full_labels: list[int] = []
        for row in ordered:
            outcomes = row.get("actual_outcomes")
            if not isinstance(outcomes, dict):
                _fail(f"p_fill_shadow OOF outcomes are missing: {signal_date}")
            label = outcomes.get("market_fill_proxy")
            if type(label) is not int or label not in (0, 1):
                _fail(f"p_fill_shadow OOF market_fill proxy is missing: {signal_date}")
            full_labels.append(label)
        baseline_rows += len(full_labels)
        baseline_hits += sum(full_labels)
        baseline_daily_rate = sum(full_labels) / len(full_labels)
        baseline_daily_rates.append(baseline_daily_rate)

        requested_slots += 2
        selected_slots += len(selected)
        if len(selected) == 2:
            dates_with_two_slots += 1
        elif len(selected) == 1:
            dates_with_one_slot += 1

        selected_payload: list[dict[str, Any]] = []
        selected_labels: list[int] = []
        date_outcomes_resolved = True
        date_counterfactual_sum = 0.0
        for row in selected:
            diagnostics = row["research_diagnostics"]["p_fill_shadow"]
            outcomes = row["actual_outcomes"]
            rank = int(diagnostics["rank"])
            label = int(outcomes["market_fill_proxy"])
            net_return = outcomes.get("net_return")
            if net_return is not None and (
                isinstance(net_return, bool) or not isinstance(net_return, (int, float))
            ):
                _fail(f"p_fill_shadow OOF net_return is invalid: {signal_date}")
            if label == 0 and net_return is not None:
                _fail(f"p_fill_shadow nonfill return must be null: {signal_date}")
            return_observed = label == 1 and net_return is not None
            outcome_resolved = label == 0 or return_observed
            selected_labels.append(label)
            fill_hits += label
            per_rank[rank]["selected_slots"] += 1
            per_rank[rank]["fill_hits"] += label
            if label == 1:
                filled_slots += 1
            if return_observed:
                observed_filled_returns += 1
                conditional_return_sum += float(net_return)
                conditional_return_wins += int(float(net_return) > 0.0)
                date_counterfactual_sum += float(net_return)
            if outcome_resolved:
                resolved_selected_slots += 1
            else:
                date_outcomes_resolved = False
            selected_payload.append(
                {
                    "p_fill_shadow_rank": rank,
                    "ts_code": row["ts_code"],
                    "p_fill_shadow_probability": diagnostics[
                        "predicted_probability"
                    ],
                    "market_fill_proxy": label,
                    "net_return": net_return,
                    "return_observed": return_observed,
                    "outcome_resolved": outcome_resolved,
                }
            )

        top2_daily_rate = sum(selected_labels) / len(selected_labels)
        top2_daily_rates.append(top2_daily_rate)
        if date_outcomes_resolved:
            fully_resolved_dates += 1
            # Two requested slots are equally weighted.  A nonfill or a
            # missing slot caused by a one-name official pool remains cash.
            complete_case_return = date_counterfactual_sum / 2.0
            counterfactual_daily_returns.append(complete_case_return)
            counterfactual_nav *= 1.0 + complete_case_return
        else:
            complete_case_return = None
            return_incomplete_dates.append(signal_date)

        daily.append(
            {
                "signal_date": signal_date,
                "exec_date": record["exec_date"],
                "exit_date": record["exit_date"],
                "requested_slots": 2,
                "selected_slots": len(selected),
                "missing_slots": 2 - len(selected),
                "selected": selected_payload,
                "top2_market_fill_proxy_hit_rate": top2_daily_rate,
                "same_date_full_top10_rows": len(full_labels),
                "same_date_full_top10_market_fill_proxy_hit_rate": baseline_daily_rate,
                "all_selected_outcomes_resolved": date_outcomes_resolved,
                "fixed_two_slot_counterfactual_net_return": complete_case_return,
                "cumulative": {
                    "selection_dates": len(daily) + 1,
                    "requested_slots": requested_slots,
                    "selected_slots": selected_slots,
                    "selection_slot_coverage": _ratio(
                        selected_slots, requested_slots
                    ),
                    "market_fill_proxy_hits": fill_hits,
                    "market_fill_proxy_hit_rate": _ratio(fill_hits, selected_slots),
                    "market_fill_proxy_truth_covered_slots": selected_slots,
                    "market_fill_proxy_truth_coverage": 1.0,
                    "filled_slots": filled_slots,
                    "filled_return_observations": observed_filled_returns,
                    "conditional_filled_return_coverage": _ratio(
                        observed_filled_returns, filled_slots
                    ),
                    "conditional_filled_mean_net_return": _ratio(
                        conditional_return_sum, observed_filled_returns
                    ),
                    "conditional_filled_win_rate": _ratio(
                        conditional_return_wins, observed_filled_returns
                    ),
                    "resolved_selected_slots": resolved_selected_slots,
                    "selected_slot_outcome_coverage": _ratio(
                        resolved_selected_slots, selected_slots
                    ),
                    "complete_case_included_signal_dates": len(
                        counterfactual_daily_returns
                    ),
                    "complete_case_counterfactual_nav": counterfactual_nav,
                },
            }
        )

    if not daily:
        _fail("p_fill_shadow OOF diagnostic history is empty")
    top2_date_balanced_rate = sum(top2_daily_rates) / len(top2_daily_rates)
    baseline_date_balanced_rate = sum(baseline_daily_rates) / len(
        baseline_daily_rates
    )
    return {
        "schema_version": PFILL_SHADOW_SCHEMA,
        "status": "TIME_HONEST_OOF_COUNTERFACTUAL_DIAGNOSTIC",
        "selection_scope": (
            "strict_rank_lte_2_within_historical_promotion_oof_top10"
        ),
        "selection_rule": "p_fill_shadow_rank<=2",
        "requested_slots_per_signal_date": 2,
        "selection_dates": len(daily),
        "requested_slots": requested_slots,
        "selected_slots": selected_slots,
        "selection_slot_coverage": _ratio(selected_slots, requested_slots),
        "dates_with_two_slots": dates_with_two_slots,
        "dates_with_one_slot": dates_with_one_slot,
        "market_fill_proxy": {
            "definition": "T bar exists and is not a one-price 10% limit-up",
            "actual_order_fill_observed": False,
            "truth_covered_slots": selected_slots,
            "truth_coverage": 1.0,
            "hits": fill_hits,
            "hit_rate": _ratio(fill_hits, selected_slots),
            "date_balanced_hit_rate": top2_date_balanced_rate,
            "wilson_95": _wilson_95(fill_hits, selected_slots),
            "rank_breakdown": {
                str(rank): {
                    **values,
                    "hit_rate": _ratio(
                        values["fill_hits"], values["selected_slots"]
                    ),
                }
                for rank, values in per_rank.items()
            },
            "same_period_full_top10_baseline": {
                "rows": baseline_rows,
                "hits": baseline_hits,
                "hit_rate": _ratio(baseline_hits, baseline_rows),
                "date_balanced_hit_rate": baseline_date_balanced_rate,
            },
            "micro_hit_rate_lift_vs_full_top10": (
                fill_hits / selected_slots - baseline_hits / baseline_rows
            ),
            "date_balanced_hit_rate_lift_vs_full_top10": (
                top2_date_balanced_rate - baseline_date_balanced_rate
            ),
        },
        "returns": {
            "status": (
                "INCOMPLETE_FILLED_RETURN_TRUTH"
                if observed_filled_returns != filled_slots
                else "COMPLETE_FILLED_RETURN_TRUTH"
            ),
            "return_window": "T open proxy to T+1 open, net of 0.0045 round-trip cost",
            "not_profit_model_evaluation": True,
            "filled_slots": filled_slots,
            "observed_filled_return_slots": observed_filled_returns,
            "conditional_filled_return_coverage": _ratio(
                observed_filled_returns, filled_slots
            ),
            "conditional_filled_mean_net_return": _ratio(
                conditional_return_sum, observed_filled_returns
            ),
            "conditional_filled_win_count": conditional_return_wins,
            "conditional_filled_win_rate": _ratio(
                conditional_return_wins, observed_filled_returns
            ),
            "resolved_selected_slots": resolved_selected_slots,
            "selected_slot_outcome_coverage": _ratio(
                resolved_selected_slots, selected_slots
            ),
            "fully_resolved_signal_dates": fully_resolved_dates,
            "return_incomplete_signal_dates": return_incomplete_dates,
            "fixed_two_slot_complete_case_counterfactual": {
                "diagnostic_only": True,
                "actual_trading_result": False,
                "weight_per_requested_slot": 0.5,
                "nonfill_and_missing_pool_slot_return": 0.0,
                "excluded_when_filled_return_missing": True,
                "included_signal_dates": len(counterfactual_daily_returns),
                "excluded_signal_dates": return_incomplete_dates,
                "mean_daily_net_return": (
                    sum(counterfactual_daily_returns)
                    / len(counterfactual_daily_returns)
                ),
                "compounded_net_return": counterfactual_nav - 1.0,
            },
        },
        "separation_guards": {
            "historical_oof_rows_only": True,
            "forward_snapshot_rows_used": 0,
            "actual_order_rows_used": 0,
            "actual_execution_claimed": False,
            "final_model_historical_scoring_used": False,
            "may_change_core_members_or_ranks": False,
            "may_create_trade_action": False,
        },
        "daily": daily,
    }


def _three_rank_core_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    row_fields = (
        "ts_code",
        "name",
        "industry",
        "stage_transition",
        "top10_selected",
        "promotion_rank",
        "predicted_promotion_probability",
        "big_loss_safety_rank",
        "predicted_big_loss_probability",
        "profit_rank",
        "predicted_profit_probability",
    )
    rows = contract.get("rows")
    return {
        "schema_version": contract.get("schema_version"),
        "artifact_kind": contract.get("artifact_kind"),
        "contract_version": contract.get("contract_version"),
        "signal_date": contract.get("signal_date"),
        "exec_date": contract.get("exec_date"),
        "exit_date": contract.get("exit_date"),
        "feature_as_of_date": contract.get("feature_as_of_date"),
        "feature_snapshot_sha256": contract.get("feature_snapshot_sha256"),
        "promotion_pool_size": contract.get("promotion_pool_size"),
        "top10_count": contract.get("top10_count"),
        "top10_members_sha256": contract.get("top10_members_sha256"),
        "models": contract.get("models"),
        "rows": [
            {field: row.get(field) for field in row_fields}
            for row in rows
            if isinstance(row, dict)
        ]
        if isinstance(rows, list)
        else rows,
    }


def _forward_shadow_top2_projection(
    rows: Sequence[Mapping[str, Any]], *, model_status: str
) -> dict[str, Any]:
    selected = (
        sorted(
            (
                {
                    "ts_code": str(row.get("ts_code") or "").strip().upper(),
                    "name": str(row.get("name") or "").strip(),
                    "p_fill_shadow_rank": _optional_int(
                        row.get("p_fill_shadow_rank"),
                        field="forward p_fill_shadow_rank",
                    ),
                    "p_fill_shadow_probability": _optional_float(
                        row.get("p_fill_shadow_probability"),
                        field="forward p_fill_shadow_probability",
                    ),
                }
                for row in rows
                if (
                    _optional_int(
                        row.get("p_fill_shadow_rank"),
                        field="forward p_fill_shadow_rank",
                    )
                    or 0
                )
                <= 2
                and _optional_int(
                    row.get("p_fill_shadow_rank"),
                    field="forward p_fill_shadow_rank",
                )
                is not None
            ),
            key=lambda row: (int(row["p_fill_shadow_rank"]), row["ts_code"]),
        )
        if model_status == "SHADOW_READY"
        else []
    )
    return {
        "status": "ANNOTATION_ONLY",
        "model_status": model_status,
        "selection_rule": "p_fill_shadow_rank_lte_requested_slots",
        "rank_field": "p_fill_shadow_rank",
        "probability_field": "p_fill_shadow_probability",
        "requested_slots": 2,
        "actual_slots": len(selected),
        "may_change_core_bundle": False,
        "may_override_core_ranks": False,
        "may_create_trade_action": False,
        "rows": selected,
    }


def _forward_shadow_snapshot_sha256(
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    members_sha256: str,
    shadow: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    shadow_top2: Mapping[str, Any],
) -> str:
    top2_rows = shadow_top2.get("rows")
    payload = {
        "schema": "dc20_p_fill_shadow_snapshot_v1",
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "top10_members_sha256": members_sha256,
        "model": {
            "status": shadow.get("model_status"),
            "version": shadow.get("model_version"),
            "as_of_date": shadow.get("model_as_of_date"),
            "artifact_sha256": shadow.get("model_artifact_sha256"),
        },
        "rows": sorted(
            (
                {
                    "ts_code": str(row.get("ts_code") or "").strip().upper(),
                    "p_fill_shadow_rank": _optional_int(
                        row.get("p_fill_shadow_rank"),
                        field="forward p_fill_shadow_rank",
                    ),
                    "p_fill_shadow_probability": _optional_float(
                        row.get("p_fill_shadow_probability"),
                        field="forward p_fill_shadow_probability",
                    ),
                    "p_fill_shadow_status": str(
                        row.get("p_fill_shadow_status") or ""
                    )
                    .strip()
                    .upper(),
                }
                for row in rows
            ),
            key=lambda row: row["ts_code"],
        ),
        "shadow_top2": {
            "requested_slots": shadow_top2.get("requested_slots"),
            "actual_slots": shadow_top2.get("actual_slots"),
            "members": [
                {
                    "ts_code": str(row.get("ts_code") or "").strip().upper(),
                    "p_fill_shadow_rank": _optional_int(
                        row.get("p_fill_shadow_rank"),
                        field="forward p_fill_shadow_rank",
                    ),
                }
                for row in top2_rows
                if isinstance(row, dict)
            ]
            if isinstance(top2_rows, list)
            else top2_rows,
        },
    }
    return _canonical_sha256(payload)


def _validate_forward_core_snapshot(
    *,
    path: Path,
    payload: Mapping[str, Any],
    source_root: Path,
    open_date_index: Mapping[str, int],
) -> tuple[str, str, str, list[Mapping[str, Any]], str]:
    filename_match = FORWARD_SNAPSHOT_RE.fullmatch(path.name)
    if filename_match is None:
        _fail(f"invalid forward snapshot filename: {path.name}")
    signal_date = _date(payload.get("signal_date"), field="forward signal_date")
    exec_date = _date(payload.get("exec_date"), field="forward exec_date")
    exit_date = _date(payload.get("exit_date"), field="forward exit_date")
    if signal_date != filename_match.group(1):
        _fail(f"forward snapshot filename/date mismatch: {path.name}")
    if (
        not signal_date < exec_date < exit_date
        or any(date not in open_date_index for date in (signal_date, exec_date, exit_date))
        or open_date_index[exec_date] != open_date_index[signal_date] + 1
        or open_date_index[exit_date] != open_date_index[exec_date] + 1
    ):
        _fail(f"forward snapshot D/T/T+1 contract is invalid: {signal_date}")
    rows = payload.get("rows")
    if (
        payload.get("schema_version") != "decision_three_rank_top10_v1"
        or payload.get("artifact_kind") != "d_close_independent_three_rank_top10"
        or payload.get("contract_version") != "decision_three_rank_v1"
        or payload.get("feature_as_of_date") != signal_date
        or not isinstance(rows, list)
        or not rows
        or len(rows) > 10
    ):
        _fail(f"forward snapshot core contract is invalid: {signal_date}")
    pool_size = _required_int(
        payload.get("promotion_pool_size"), field="forward promotion_pool_size"
    )
    if (
        _required_int(payload.get("top10_count"), field="forward top10_count")
        != len(rows)
        or len(rows) != min(10, pool_size)
    ):
        _fail(f"forward snapshot Top10 count is invalid: {signal_date}")
    codes = [str(row.get("ts_code") or "").strip().upper() for row in rows]
    if not all(codes) or len(codes) != len(set(codes)):
        _fail(f"forward snapshot codes are invalid: {signal_date}")
    members_sha256 = _top10_members_sha256(signal_date, codes)
    if payload.get("top10_members_sha256") != members_sha256:
        _fail(f"forward snapshot member hash is invalid: {signal_date}")
    promotion_ranks = [
        _required_int(row.get("promotion_rank"), field="forward promotion_rank")
        for row in rows
    ]
    if sorted(promotion_ranks) != list(range(1, len(rows) + 1)) or any(
        _required_int(row.get("top10_selected"), field="forward top10_selected")
        != 1
        for row in rows
    ):
        _fail(f"forward snapshot promotion ranks are invalid: {signal_date}")
    models = payload.get("models")
    if not isinstance(models, dict) or set(models) != {
        "promotion",
        "big_loss",
        "profit",
    }:
        _fail(f"forward snapshot model inventory is invalid: {signal_date}")
    if models["promotion"].get("status") != "READY":
        _fail(f"forward snapshot promotion is not READY: {signal_date}")
    head_fields = {
        "promotion": ("promotion_rank", "predicted_promotion_probability"),
        "big_loss": ("big_loss_safety_rank", "predicted_big_loss_probability"),
        "profit": ("profit_rank", "predicted_profit_probability"),
    }
    ready_heads = 0
    for head in ("promotion", "big_loss", "profit"):
        model = models[head]
        if not isinstance(model, dict) or model.get("input_members_sha256") != members_sha256:
            _fail(f"forward snapshot {head} set binding is invalid: {signal_date}")
        status = model.get("status")
        if not isinstance(status, str) or not (
            status == "READY" or status.startswith("NOT_READY_")
        ):
            _fail(f"forward snapshot {head} status is invalid: {signal_date}")
        ready = status == "READY"
        ready_heads += int(ready)
        rank_field, probability_field = head_fields[head]
        if (
            model.get("ranking_ready") is not ready
            or model.get("probability_ready") is not ready
            or model.get("rank_field") != rank_field
            or model.get("probability_field") != probability_field
        ):
            _fail(f"forward snapshot {head} readiness contract is invalid: {signal_date}")
        ranks = [
            _optional_int(row.get(rank_field), field=f"forward {rank_field}")
            for row in rows
        ]
        probabilities = [
            _optional_float(
                row.get(probability_field), field=f"forward {probability_field}"
            )
            for row in rows
        ]
        if ready:
            model_as_of_date = _date(
                model.get("model_as_of_date"),
                field=f"forward {head} model_as_of_date",
            )
            if (
                not str(model.get("version") or "").strip()
                or model_as_of_date >= signal_date
                or not SHA256_RE.fullmatch(str(model.get("artifact_sha256") or ""))
                or sorted(int(rank) for rank in ranks if rank is not None)
                != list(range(1, len(rows) + 1))
                or any(rank is None for rank in ranks)
                or any(
                    probability is None or not 0.0 <= probability <= 1.0
                    for probability in probabilities
                )
            ):
                _fail(f"forward snapshot {head} READY output is invalid: {signal_date}")
        elif any(
            rank is not None or probability is not None
            for rank, probability in zip(ranks, probabilities)
        ):
            _fail(f"forward snapshot {head} unready output is not null: {signal_date}")
    expected_status = "READY" if ready_heads == 3 else "PARTIAL_MODELS_NOT_READY"
    if (
        payload.get("status") != expected_status
        or payload.get("membership_authority")
        != "promotion_probability_engine_only"
        or payload.get("downstream_scope") != "exact_frozen_promotion_top10"
    ):
        _fail(f"forward snapshot aggregate contract is invalid: {signal_date}")
    execution_summary = payload.get("execution_summary")
    if (
        execution_summary is not None
        and (
            not isinstance(execution_summary, dict)
            or execution_summary.get("actual_execution_claimed") is not False
        )
    ):
        _fail(f"forward snapshot execution claim is invalid: {signal_date}")
    feature_snapshot_sha256 = str(
        payload.get("feature_snapshot_sha256") or ""
    ).strip()
    if not SHA256_RE.fullmatch(feature_snapshot_sha256):
        _fail(f"forward snapshot feature hash is invalid: {signal_date}")
    if payload.get("bundle_sha256") != _canonical_sha256(
        _three_rank_core_projection(payload)
    ):
        _fail(f"forward snapshot core bundle hash is invalid: {signal_date}")

    downloads = payload.get("downloads")
    expected_prefix = f"outputs/decision/three_rank_top10_{signal_date}"
    if (
        not isinstance(downloads, dict)
        or downloads.get("json_url") != f"{expected_prefix}.json"
        or downloads.get("csv_url") != f"{expected_prefix}.csv"
        or downloads.get("row_count") != len(rows)
        or not SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or ""))
    ):
        _fail(f"forward snapshot download binding is invalid: {signal_date}")
    csv_path = source_root / f"{expected_prefix}.csv"
    if _sha256_file(csv_path) != downloads["csv_sha256"]:
        _fail(f"forward snapshot CSV hash is invalid: {signal_date}")
    return signal_date, exec_date, exit_date, rows, members_sha256


def _primary_only_non_shadow_receipt_binding(
    *,
    source_root: Path,
    snapshot_path: Path,
    payload: Mapping[str, Any],
    signal_date: str,
    exec_date: str,
    exit_date: str,
    rows: Sequence[Mapping[str, Any]],
    members_sha256: str,
) -> tuple[str, dict[str, Any]] | None:
    """Return the exact P0 receipt which proves this is not p_fill Shadow.

    Receipt presence alone is deliberately insufficient.  Every date, output hash,
    primary-only flag and the null Shadow payload must agree before the snapshot is
    excluded from p_fill forward statistics.  Both P0 modes are promotion-only:
    ``NATURAL`` may be forward-eligible as a D list, but it is not a p_fill Shadow
    observation because its receipt explicitly makes the Shadow ledger ineligible.
    An incomplete receipt or any secondary output continues through the strict
    forward-Shadow validator below and fails closed when its Shadow contract is not
    audit grade.
    """

    receipt_path = (
        source_root
        / REPORTS_RELATIVE_PATH
        / f"primary_d_receipt_{signal_date}.json"
    )
    if not receipt_path.is_file():
        return None
    receipt = _read_json(receipt_path)
    mode = str(receipt.get("generation_mode") or "").strip().upper()
    mode_flags = {
        "NATURAL": {
            "prospective": True,
            "forward_eligible": True,
            "not_forward_generated": False,
        },
        "RETROSPECTIVE_RECOVERY": {
            "prospective": False,
            "forward_eligible": False,
            "not_forward_generated": True,
        },
    }.get(mode)
    if mode_flags is None:
        return None
    secondary = receipt.get("secondary_outputs_generated")
    outputs = receipt.get("outputs")
    shadow = payload.get("shadow_contract")
    expected_secondary = {
        "action_plan": False,
        "big_loss": False,
        "profit": False,
        "p_fill_shadow": False,
        "executable_profit": False,
    }
    expected_shadow_top2 = _forward_shadow_top2_projection(
        rows, model_status="SHADOW_NOT_READY_PRIMARY_ONLY"
    )
    snapshot_relative = snapshot_path.relative_to(source_root).as_posix()
    downloads = payload.get("downloads")
    models = payload.get("models")
    primary_only_rows = all(
        row.get("p_fill_shadow_rank") is None
        and row.get("p_fill_shadow_probability") is None
        and row.get("p_fill_shadow_status") == "SHADOW_NOT_READY_PRIMARY_ONLY"
        for row in rows
    )
    primary_only_shadow = (
        isinstance(shadow, dict)
        and shadow.get("status") == "ANNOTATION_ONLY"
        and shadow.get("input_members_sha256") == members_sha256
        and shadow.get("may_change_membership") is False
        and shadow.get("may_override_core_ranks") is False
        and shadow.get("model_status") == "SHADOW_NOT_READY_PRIMARY_ONLY"
        and shadow.get("model_version") == ""
        and shadow.get("model_as_of_date") == ""
        and shadow.get("model_artifact_sha256") == ""
        and shadow.get("validation_gate_pass_count") is None
        and shadow.get("validation_gate_total_count") is None
        and shadow.get("validation_gate_score_pct") is None
        and payload.get("shadow_top2") == expected_shadow_top2
        and shadow.get("shadow_snapshot_sha256")
        == _forward_shadow_snapshot_sha256(
            signal_date=signal_date,
            exec_date=exec_date,
            exit_date=exit_date,
            members_sha256=members_sha256,
            shadow=shadow,
            rows=rows,
            shadow_top2=expected_shadow_top2,
        )
    )
    if not (
        receipt.get("schema_version") == "dc20_primary_d_receipt_v1"
        and receipt.get("artifact_kind") == "p0_promotion_only_d_list_receipt"
        and receipt.get("owner") == "njedu2023-prog/DC20"
        and receipt.get("runtime_dependency_on_top10_decision") is False
        and receipt.get("generation_mode") == mode
        and receipt.get("prospective") is mode_flags["prospective"]
        and receipt.get("forward_eligible") is mode_flags["forward_eligible"]
        and receipt.get("not_forward_generated")
        is mode_flags["not_forward_generated"]
        and (
            (mode == "NATURAL" and receipt.get("recovered_at_utc") == "")
            or (
                mode == "RETROSPECTIVE_RECOVERY"
                and bool(str(receipt.get("recovered_at_utc") or "").strip())
            )
        )
        and receipt.get("nominal_source_cutoff_bj")
        == (
            f"{signal_date[:4]}-{signal_date[4:6]}-{signal_date[6:]}"
            "T21:15:00+08:00"
        )
        and receipt.get("signal_date") == signal_date
        and receipt.get("exec_date") == exec_date
        and receipt.get("exit_date") == exit_date
        and receipt.get("primary_status") == "READY"
        and receipt.get("action_authorized") is False
        and receipt.get("action_input_consumed") is False
        and receipt.get("formal_trade_count") == 0
        and receipt.get("shadow_forward_ledger_eligible") is False
        and receipt.get("future_market_data_consumed") is False
        and receipt.get("latest_fallback_used") is False
        and secondary == expected_secondary
        and isinstance(outputs, dict)
        and outputs.get("json_path") == snapshot_relative
        and outputs.get("json_sha256") == _sha256_file(snapshot_path)
        and isinstance(downloads, dict)
        and outputs.get("csv_path") == downloads.get("csv_url")
        and outputs.get("csv_sha256") == downloads.get("csv_sha256")
        and outputs.get("bundle_sha256") == payload.get("bundle_sha256")
        and outputs.get("feature_snapshot_sha256")
        == payload.get("feature_snapshot_sha256")
        and outputs.get("top10_members_sha256") == members_sha256
        and outputs.get("promotion_pool_size") == payload.get("promotion_pool_size")
        and outputs.get("top10_count") == len(rows)
        and isinstance(models, dict)
        and models.get("big_loss", {}).get("status")
        == "NOT_READY_PRIMARY_ONLY"
        and models.get("profit", {}).get("status")
        == "NOT_READY_PRIMARY_ONLY"
        and primary_only_rows
        and primary_only_shadow
    ):
        return None
    return mode, _binding(receipt_path, source_root)


def _validate_forward_shadow_snapshot(
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    rows: Sequence[Mapping[str, Any]],
    members_sha256: str,
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    shadow = payload.get("shadow_contract")
    if (
        not isinstance(shadow, dict)
        or shadow.get("status") != "ANNOTATION_ONLY"
        or shadow.get("input_members_sha256") != members_sha256
        or shadow.get("may_change_membership") is not False
        or shadow.get("may_override_core_ranks") is not False
        or shadow.get("model_status") != "SHADOW_READY"
        or not str(shadow.get("model_version") or "").strip()
        or _date(
            shadow.get("model_as_of_date"),
            field="forward p_fill_shadow model_as_of_date",
        )
        >= signal_date
        or not SHA256_RE.fullmatch(
            str(shadow.get("model_artifact_sha256") or "")
        )
        or _required_int(
            shadow.get("validation_gate_pass_count"),
            field="forward p_fill_shadow gate pass count",
        )
        != _required_int(
            shadow.get("validation_gate_total_count"),
            field="forward p_fill_shadow gate total count",
        )
        or _required_int(
            shadow.get("validation_gate_total_count"),
            field="forward p_fill_shadow gate total count",
        )
        <= 0
        or _optional_float(
            shadow.get("validation_gate_score_pct"),
            field="forward p_fill_shadow gate score",
        )
        != 100.0
    ):
        _fail(f"forward p_fill_shadow contract is invalid: {signal_date}")
    probabilities = [
        _optional_float(
            row.get("p_fill_shadow_probability"),
            field="forward p_fill_shadow_probability",
        )
        for row in rows
    ]
    if any(
        probability is None or not 0.0 <= probability <= 1.0
        for probability in probabilities
    ) or any(
        str(row.get("p_fill_shadow_status") or "").strip().upper()
        != "SHADOW_READY"
        for row in rows
    ):
        _fail(f"forward p_fill_shadow rows are invalid: {signal_date}")
    ranks = [
        _optional_int(
            row.get("p_fill_shadow_rank"), field="forward p_fill_shadow_rank"
        )
        for row in rows
    ]
    shadow_top2 = payload.get("shadow_top2")
    shadow_snapshot_sha256 = shadow.get("shadow_snapshot_sha256")
    legacy_fields_absent = (
        all(rank is None for rank in ranks)
        and shadow_top2 is None
        and shadow_snapshot_sha256 is None
    )
    if legacy_fields_absent:
        provisional = sorted(
            (
                {
                    "candidate_order": index,
                    "ts_code": str(row.get("ts_code") or "").strip().upper(),
                    "name": str(row.get("name") or "").strip(),
                    "p_fill_shadow_probability": float(probability),
                    "frozen_rank": None,
                    "status": "NOT_FROZEN_EXCLUDED_FROM_FORWARD_STATISTICS",
                }
                for index, (row, probability) in enumerate(
                    sorted(
                        zip(rows, probabilities),
                        key=lambda value: (
                            -float(value[1]),
                            str(value[0].get("ts_code") or "").strip().upper(),
                        ),
                    )[:2],
                    start=1,
                )
            ),
            key=lambda row: row["candidate_order"],
        )
        return "legacy_provisional", {
            "reason": "missing_frozen_p_fill_rank_top2_and_shadow_snapshot_hash",
            "probability_order_candidates_not_frozen": provisional,
        }
    if any(rank is None for rank in ranks):
        _fail(f"forward p_fill_shadow ranks are partially populated: {signal_date}")
    if sorted(int(rank) for rank in ranks if rank is not None) != list(
        range(1, len(rows) + 1)
    ):
        _fail(f"forward p_fill_shadow ranks are invalid: {signal_date}")
    ordered_probabilities = [
        float(probability)
        for _, probability in sorted(
            zip((int(rank) for rank in ranks if rank is not None), probabilities),
            key=lambda value: value[0],
        )
    ]
    if any(
        ordered_probabilities[index] < ordered_probabilities[index + 1]
        for index in range(len(ordered_probabilities) - 1)
    ):
        _fail(f"forward p_fill_shadow probability/rank order drifted: {signal_date}")
    expected_top2 = _forward_shadow_top2_projection(
        rows, model_status="SHADOW_READY"
    )
    if shadow_top2 != expected_top2:
        _fail(f"forward p_fill_shadow Top2 contract is invalid: {signal_date}")
    expected_shadow_sha256 = _forward_shadow_snapshot_sha256(
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        members_sha256=members_sha256,
        shadow=shadow,
        rows=rows,
        shadow_top2=expected_top2,
    )
    if (
        not SHA256_RE.fullmatch(str(shadow_snapshot_sha256 or ""))
        or shadow_snapshot_sha256 != expected_shadow_sha256
    ):
        _fail(f"forward p_fill_shadow snapshot hash is invalid: {signal_date}")
    return "accepted", expected_top2


def _read_forward_settlement_ledger(
    source_root: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    manifest_path = source_root / LEDGER_MANIFEST_RELATIVE_PATH
    ledger_path = source_root / LEDGER_RELATIVE_PATH
    manifest = _read_json(manifest_path)
    ledger_binding = _binding(ledger_path, source_root)
    manifest_binding = _binding(manifest_path, source_root)
    target_contract = manifest.get("target_contract")
    if (
        manifest.get("schema_version") != "dc20_three_engine_five_year_ledger_v2"
        or manifest.get("owner") != "njedu2023-prog/DC20"
        or manifest.get("runtime_dependency_on_top10_decision") is not False
        or manifest.get("ledger_path") != LEDGER_RELATIVE_PATH.as_posix()
        or manifest.get("ledger_sha256") != ledger_binding["sha256"]
        or not isinstance(target_contract, dict)
        or target_contract.get("market_fill")
        != "T bar exists and is not a one-price 10% limit-up"
        or target_contract.get("nonfill_return_targets") != "null"
        or target_contract.get("return_window") != "T open proxy to T+1 open"
        or _optional_float(
            target_contract.get("round_trip_cost_rate"),
            field="ledger round_trip_cost_rate",
        )
        != 0.0045
    ):
        _fail("forward settlement ledger manifest contract is invalid")
    try:
        with gzip.open(ledger_path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HistoryProjectionError("invalid forward settlement ledger") from exc
    required = {
        "signal_date",
        "buy_date",
        "target_exit_date",
        "ts_code",
        "market_fill",
        "net_return",
    }
    if not required <= fields or not rows:
        _fail("forward settlement ledger fields/rows are invalid")
    coverage = manifest.get("coverage")
    if (
        not isinstance(coverage, dict)
        or _required_int(coverage.get("rows"), field="ledger coverage rows")
        != len(rows)
        or _required_int(
            coverage.get("signal_dates"), field="ledger coverage signal_dates"
        )
        != len({str(row.get("signal_date") or "") for row in rows})
    ):
        _fail("forward settlement ledger coverage contract drifted")
    ledger: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        signal_date = _date(row.get("signal_date"), field="ledger signal_date")
        code = str(row.get("ts_code") or "").strip().upper()
        if not code:
            _fail(f"forward settlement ledger code is empty: {signal_date}")
        key = (signal_date, code)
        if key in ledger:
            _fail(f"forward settlement ledger identity is duplicated: {key}")
        market_fill = _optional_binary(row.get("market_fill"), field="ledger market_fill")
        net_return = _optional_float(row.get("net_return"), field="ledger net_return")
        if market_fill == 0 and net_return is not None:
            _fail(f"forward settlement ledger nonfill return is not null: {key}")
        ledger[key] = {
            "buy_date": _date(row.get("buy_date"), field="ledger buy_date"),
            "target_exit_date": _date(
                row.get("target_exit_date"), field="ledger target_exit_date"
            ),
            "market_fill_proxy": market_fill,
            "net_return": net_return,
        }
    return ledger, {
        "ledger": ledger_binding,
        "manifest": manifest_binding,
        "ledger_signal_date_start": min(key[0] for key in ledger),
        "ledger_signal_date_end": max(key[0] for key in ledger),
        "target_contract": target_contract,
    }


def _forward_p_fill_shadow_top2(
    *, source_root: Path, open_date_index: Mapping[str, int]
) -> dict[str, Any]:
    ledger, ledger_sources = _read_forward_settlement_ledger(source_root)
    reports_root = source_root / REPORTS_RELATIVE_PATH
    if not reports_root.is_dir():
        _fail("forward snapshot directory is missing")
    snapshot_paths = sorted(
        (
            path
            for path in reports_root.iterdir()
            if path.is_file() and FORWARD_SNAPSHOT_RE.fullmatch(path.name)
        ),
        key=lambda path: path.name,
    )
    accepted_records: list[dict[str, Any]] = []
    provisional_records: list[dict[str, Any]] = []
    primary_only_non_shadow_records: list[dict[str, Any]] = []
    selected_entries = 0
    fill_truth_entries = 0
    fill_hits = 0
    filled_entries = 0
    filled_return_entries = 0
    return_sum = 0.0
    return_wins = 0
    resolved_entries = 0

    for path in snapshot_paths:
        payload = _read_json(path)
        signal_date, exec_date, exit_date, rows, members_sha256 = (
            _validate_forward_core_snapshot(
                path=path,
                payload=payload,
                source_root=source_root,
                open_date_index=open_date_index,
            )
        )
        snapshot_binding = _binding(path, source_root)
        primary_only_receipt = _primary_only_non_shadow_receipt_binding(
            source_root=source_root,
            snapshot_path=path,
            payload=payload,
            signal_date=signal_date,
            exec_date=exec_date,
            exit_date=exit_date,
            rows=rows,
            members_sha256=members_sha256,
        )
        if primary_only_receipt is not None:
            generation_mode, receipt_binding = primary_only_receipt
            primary_only_non_shadow_records.append(
                {
                    "signal_date": signal_date,
                    "exec_date": exec_date,
                    "exit_date": exit_date,
                    "generation_mode": generation_mode,
                    "status": (
                        "EXCLUDED_NATURAL_PRIMARY_ONLY_NO_PFILL_SHADOW"
                        if generation_mode == "NATURAL"
                        else "EXCLUDED_RETROSPECTIVE_PRIMARY_ONLY"
                    ),
                    "excluded_from_forward_statistics": True,
                    "exclusion_reason": (
                        "same_day_hash_bound_p0_primary_only_receipt_"
                        "with_shadow_ledger_ineligible"
                    ),
                    "snapshot": snapshot_binding,
                    "receipt": receipt_binding,
                    "top10_members_sha256": members_sha256,
                }
            )
            continue
        mode, shadow_projection = _validate_forward_shadow_snapshot(
            signal_date=signal_date,
            exec_date=exec_date,
            exit_date=exit_date,
            rows=rows,
            members_sha256=members_sha256,
            payload=payload,
        )
        if mode == "legacy_provisional":
            provisional_records.append(
                {
                    "signal_date": signal_date,
                    "exec_date": exec_date,
                    "exit_date": exit_date,
                    "status": "PENDING_SNAPSHOT_CONTRACT_UPGRADE",
                    "settlement_status": "PENDING",
                    "excluded_from_forward_statistics": True,
                    "snapshot": snapshot_binding,
                    **shadow_projection,
                }
            )
            continue

        selected_rows = shadow_projection["rows"]
        settled_rows: list[dict[str, Any]] = []
        for selected in selected_rows:
            code = selected["ts_code"]
            truth = ledger.get((signal_date, code))
            selected_entries += 1
            if truth is None:
                settlement_status = "PENDING"
                market_fill = None
                net_return = None
                return_matured = False
                outcome_resolved = False
            else:
                if (
                    truth["buy_date"] != exec_date
                    or truth["target_exit_date"] != exit_date
                ):
                    _fail(f"forward settlement D/T/T+1 mismatch: {signal_date}/{code}")
                market_fill = truth["market_fill_proxy"]
                net_return = truth["net_return"]
                if market_fill is None:
                    settlement_status = "PENDING_FILL_TRUTH"
                    return_matured = False
                    outcome_resolved = False
                elif market_fill == 0:
                    settlement_status = "SETTLED_NONFILL"
                    fill_truth_entries += 1
                    return_matured = True
                    outcome_resolved = True
                else:
                    fill_truth_entries += 1
                    fill_hits += 1
                    filled_entries += 1
                    if net_return is None:
                        settlement_status = "PENDING_RETURN_TRUTH"
                        return_matured = False
                        outcome_resolved = False
                    else:
                        settlement_status = "SETTLED_FILLED_RETURN"
                        filled_return_entries += 1
                        return_sum += net_return
                        return_wins += int(net_return > 0.0)
                        return_matured = True
                        outcome_resolved = True
                resolved_entries += int(outcome_resolved)
            settled_rows.append(
                {
                    **selected,
                    "settlement_status": settlement_status,
                    "market_fill_proxy": market_fill,
                    "net_return": net_return,
                    "return_matured": return_matured,
                    "outcome_resolved": outcome_resolved,
                    "actual_order_fill_observed": False,
                }
            )
        accepted_records.append(
            {
                "signal_date": signal_date,
                "exec_date": exec_date,
                "exit_date": exit_date,
                "status": "FROZEN_FORWARD_SHADOW_TOP2",
                "snapshot": snapshot_binding,
                "top10_members_sha256": members_sha256,
                "shadow_snapshot_sha256": payload["shadow_contract"][
                    "shadow_snapshot_sha256"
                ],
                "requested_slots": 2,
                "selected_slots": len(settled_rows),
                "rows": settled_rows,
            }
        )

    return {
        "schema_version": FORWARD_PFILL_SHADOW_SCHEMA,
        "status": (
            "FROZEN_FORWARD_RECORDS_PRESENT"
            if accepted_records
            else "NO_AUDIT_GRADE_FROZEN_SNAPSHOTS"
        ),
        "selection_scope": "forward_dated_snapshots_only_never_oof",
        "selection_rule": "frozen_p_fill_shadow_rank<=2",
        "discovered_snapshot_dates": [
            FORWARD_SNAPSHOT_RE.fullmatch(path.name).group(1)
            for path in snapshot_paths
        ],
        "accepted_snapshot_dates": [
            record["signal_date"] for record in accepted_records
        ],
        "provisional_snapshot_dates": [
            record["signal_date"] for record in provisional_records
        ],
        "primary_only_non_shadow_snapshot_dates": [
            record["signal_date"]
            for record in primary_only_non_shadow_records
        ],
        "natural_primary_only_snapshot_dates": [
            record["signal_date"]
            for record in primary_only_non_shadow_records
            if record["generation_mode"] == "NATURAL"
        ],
        "retrospective_primary_only_snapshot_dates": [
            record["signal_date"]
            for record in primary_only_non_shadow_records
            if record["generation_mode"] == "RETROSPECTIVE_RECOVERY"
        ],
        "selection_dates": len(accepted_records),
        "selected_entries": selected_entries,
        "fill_truth": {
            "covered_entries": fill_truth_entries,
            "coverage": _ratio(fill_truth_entries, selected_entries),
            "hits": fill_hits,
            "hit_rate": _ratio(fill_hits, fill_truth_entries),
            "actual_order_fill_observed": False,
        },
        "returns": {
            "filled_entries": filled_entries,
            "matured_filled_return_entries": filled_return_entries,
            "filled_return_coverage": _ratio(
                filled_return_entries, filled_entries
            ),
            "conditional_filled_mean_net_return": _ratio(
                return_sum, filled_return_entries
            ),
            "conditional_filled_win_count": return_wins,
            "conditional_filled_win_rate": _ratio(
                return_wins, filled_return_entries
            ),
            "resolved_selected_entries": resolved_entries,
            "selected_outcome_coverage": _ratio(
                resolved_entries, selected_entries
            ),
            "actual_trading_result": False,
        },
        "separation_guards": {
            "forward_snapshot_rows_only": True,
            "historical_oof_rows_used": 0,
            "actual_order_rows_used": 0,
            "actual_execution_claimed": False,
            "primary_only_non_shadow_rows_used": 0,
            "may_change_core_members_or_ranks": False,
            "may_create_trade_action": False,
        },
        "source_bindings": ledger_sources,
        "records": accepted_records,
        "provisional_pre_freeze_records": provisional_records,
        "primary_only_non_shadow_records": primary_only_non_shadow_records,
    }


def build_history_archive(source_root: Path, output_root: Path) -> Mapping[str, Any]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists() and (
        not output_root.is_dir() or any(output_root.iterdir())
    ):
        _fail("output root must not exist or must be empty")
    oof_path = source_root / OOF_RELATIVE_PATH
    validation_path = source_root / VALIDATION_RELATIVE_PATH
    sources_manifest_path = source_root / SOURCES_MANIFEST_RELATIVE_PATH
    calendar_path = source_root / CALENDAR_RELATIVE_PATH

    oof_binding = _binding(oof_path, source_root)
    validation_binding = _binding(validation_path, source_root)
    sources_manifest_binding = _binding(sources_manifest_path, source_root)
    calendar_binding = _binding(calendar_path, source_root)
    validation = _read_json(validation_path)
    sources_manifest = _read_json(sources_manifest_path)
    _validate_validation(validation, oof_path=oof_path, oof_sha256=oof_binding["sha256"])
    open_date_index = _read_calendar(calendar_path)
    oof_rows, _ = _read_oof(oof_path)
    report_pairs = _load_report_pairs(source_root, sources_manifest)
    sources_manifest_binding = {
        **sources_manifest_binding,
        "calendar_source": sources_manifest["calendar_source"],
        "canonical_inventory_sha256": sources_manifest[
            "canonical_inventory_sha256"
        ],
        "exchange": sources_manifest["exchange"],
        "report_eval_pairs": len(report_pairs),
        "strict_calendar": sources_manifest["strict_calendar"],
    }

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in oof_rows:
        signal_date = _date(row.get("signal_date"), field="OOF signal_date")
        grouped[signal_date].append(row)
    oof_cutoff = _validate_oof_archive_contract(
        validation,
        oof_rows,
        grouped,
    )

    reports_by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in report_pairs:
        reports_by_signal[str(binding["signal_date"])].append(binding)

    annual_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    annual_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    annual_csv: dict[str, list[dict[str, Any]]] = defaultdict(list)
    report_map_rows: list[dict[str, Any]] = []

    # Project the complete frozen OOF archive, not merely the dates which have
    # a legacy Decision report.  Report bindings are additive provenance for
    # the 121-report window; they are never a prerequisite or a fallback data
    # source for an OOF date.
    for signal_date in sorted(grouped):
        record, evidence, csv_rows = _project_date(
            signal_date,
            grouped[signal_date],
            reports_by_signal.get(signal_date, ()),
            open_date_index,
        )
        year = signal_date[:4]
        annual_records[year].append(record)
        annual_evidence[year].append(evidence)
        annual_csv[year].extend(csv_rows)

    projected_by_signal = {
        record["signal_date"]: record
        for records in annual_records.values()
        for record in records
    }
    p_fill_shadow_oof_top2 = _p_fill_shadow_oof_statistics(projected_by_signal)
    forward_p_fill_shadow_top2 = _forward_p_fill_shadow_top2(
        source_root=source_root,
        open_date_index=open_date_index,
    )
    for binding in report_pairs:
        signal_date = str(binding["signal_date"])
        projected = projected_by_signal.get(signal_date)
        if projected is None:
            if signal_date <= oof_cutoff:
                _fail(f"report D is missing from OOF before cutoff: {signal_date}")
            status = UNAVAILABLE_STATUS
            reason = "source_after_oof_cutoff"
            canonical_exec_date = None
            canonical_exit_date = None
            archive_year = None
            date_bundle_sha256 = None
            source_exec_matches_oof_t = None
        else:
            canonical_exec_date = projected["exec_date"]
            canonical_exit_date = projected["exit_date"]
            source_exec_matches_oof_t = (
                binding["source_exec_date"] == canonical_exec_date
            )
            if (
                source_exec_matches_oof_t
                and binding["source_exec_date"] in open_date_index
            ):
                status = AVAILABLE_STATUS
                reason = "exact_report_exec_to_oof_t_binding"
            else:
                status = ARCHIVED_STATUS
                reason = "source_report_exec_differs_from_canonical_oof_t"
            archive_year = signal_date[:4]
            date_bundle_sha256 = projected["date_bundle_sha256"]
        report_map_rows.append(
            {
                "report_date": binding["report_date"],
                "signal_date": signal_date,
                "source_exec_date": binding["source_exec_date"],
                "canonical_oof_exec_date": canonical_exec_date,
                "canonical_oof_exit_date": canonical_exit_date,
                "source_exec_is_open_session": binding["source_exec_date"] in open_date_index,
                "source_exec_matches_oof_t": source_exec_matches_oof_t,
                "status": status,
                "reason": reason,
                "archive_year": archive_year,
                "date_bundle_sha256": date_bundle_sha256,
                "report": binding["report"],
                "evaluation": binding["evaluation"],
                "actual_execution_claimed": False,
            }
        )

    shard_entries: list[dict[str, Any]] = []
    total_rows = 0
    for year in sorted(annual_records):
        records = sorted(annual_records[year], key=lambda item: item["signal_date"])
        evidences = sorted(
            annual_evidence[year], key=lambda item: item["signal_date"]
        )
        csv_rows = sorted(
            annual_csv[year],
            key=lambda item: (item["signal_date"], int(item["promotion_rank"])),
        )
        archive = {
            "schema_version": ARCHIVE_SCHEMA,
            "year": year,
            "research_only": True,
            "actual_execution_claimed": False,
            "records": records,
        }
        evidence = {
            "schema_version": EVIDENCE_SCHEMA,
            "year": year,
            "source_bindings": {
                "oof_top10": oof_binding,
                "validation": validation_binding,
                "history_sources_manifest": sources_manifest_binding,
                "trading_calendar": calendar_binding,
            },
            "release_contract": {
                "promotion": OFFICIAL_PROMOTION_STATUS,
                "big_loss": UNRELEASED_STATUS,
                "profit": UNRELEASED_STATUS,
                "p_fill_shadow": "TIME_HONEST_OOF_DIAGNOSTIC_ONLY",
                "actual_execution_claimed": False,
                "final_model_historical_scoring_used": False,
            },
            "dates": evidences,
        }
        json_name = f"three_rank_history_{year}.json"
        csv_name = f"three_rank_history_{year}.csv.gz"
        evidence_name = f"three_rank_history_{year}.evidence.json"
        # Annual shards are compact JSON to keep the Pages artifact bounded;
        # the small index/statistics/report-map documents remain pretty JSON.
        json_meta = _write_bytes(output_root / json_name, _json_bytes(archive, pretty=False))
        csv_raw = _csv_bytes(csv_rows)
        csv_meta = _write_bytes(
            output_root / csv_name, _deterministic_gzip(csv_raw)
        )
        evidence_meta = _write_bytes(
            output_root / evidence_name, _json_bytes(evidence, pretty=False)
        )
        total_rows += len(csv_rows)
        shard_entries.append(
            {
                "year": year,
                "signal_date_start": records[0]["signal_date"],
                "signal_date_end": records[-1]["signal_date"],
                "signal_dates": len(records),
                "rows": len(csv_rows),
                "json_url": f"outputs/decision/three_rank_history/{json_name}",
                "json_sha256": json_meta["sha256"],
                "json_bytes": json_meta["bytes"],
                "csv_url": f"outputs/decision/three_rank_history/{csv_name}",
                "csv_sha256": csv_meta["sha256"],
                "csv_bytes": csv_meta["bytes"],
                "csv_content_encoding": "gzip_mtime_0",
                "csv_uncompressed_sha256": _sha256_bytes(csv_raw),
                "csv_uncompressed_bytes": len(csv_raw),
                "evidence_url": (
                    f"outputs/decision/three_rank_history/{evidence_name}"
                ),
                "evidence_sha256": evidence_meta["sha256"],
                "evidence_bytes": evidence_meta["bytes"],
            }
        )

    unavailable = [row for row in report_map_rows if row["status"] == UNAVAILABLE_STATUS]
    available = [row for row in report_map_rows if row["status"] == AVAILABLE_STATUS]
    archived = [row for row in report_map_rows if row["status"] == ARCHIVED_STATUS]
    oof_corresponding = [
        row for row in report_map_rows if row["signal_date"] in projected_by_signal
    ]
    report_covered_signal_dates = sorted({row["signal_date"] for row in available})
    report_covered_rows = sum(
        len(projected_by_signal[signal_date]["rows"])
        for signal_date in report_covered_signal_dates
    )
    diagnostic_coverage: dict[str, dict[str, int]] = {}
    for head in ("big_loss", "profit", "p_fill_shadow"):
        diagnostic_dates = 0
        diagnostic_rows = 0
        for record in projected_by_signal.values():
            values = [
                row["research_diagnostics"][head]["rank"]
                for row in record["rows"]
            ]
            if any(value is not None for value in values):
                diagnostic_dates += 1
                diagnostic_rows += len(values)
        diagnostic_coverage[head] = {
            "signal_dates": diagnostic_dates,
            "rows": diagnostic_rows,
        }
    duplicate_signal_dates = sorted(
        signal_date
        for signal_date, bindings in reports_by_signal.items()
        if len(bindings) > 1
    )
    nontrading_source_report_dates = sorted(
        row["report_date"] for row in report_map_rows if not row["source_exec_is_open_session"]
    )
    report_map = {
        "schema_version": REPORT_MAP_SCHEMA,
        "mapping_kind": "exact_report_to_signal_date_no_latest_fallback",
        "data_alias": False,
        "oof_cutoff_signal_date": oof_cutoff,
        "reports": report_map_rows,
    }
    report_map_meta = _write_bytes(
        output_root / "report_map.json", _json_bytes(report_map, pretty=True)
    )
    statistics = {
        "schema_version": STATISTICS_SCHEMA,
        "status": "PARTIAL_OOF_COVERAGE",
        "research_only": True,
        "actual_execution_claimed": False,
        "final_model_historical_scoring_used": False,
        "official_model_status": {
            "promotion": OFFICIAL_PROMOTION_STATUS,
            "big_loss": UNRELEASED_STATUS,
            "profit": UNRELEASED_STATUS,
        },
        "diagnostic_model_status": {
            "p_fill_shadow": "TIME_HONEST_OOF_SHADOW_DIAGNOSTIC_ONLY",
        },
        "calendar_source": "tushare:trade_cal:SSE",
        "strict_calendar": True,
        "exchange": "SSE",
        "coverage": {
            "report_eval_pairs": len(report_pairs),
            "oof_corresponding_report_mappings": len(oof_corresponding),
            "canonical_report_mappings": len(available),
            "canonical_report_unique_signal_dates": len(report_covered_signal_dates),
            "canonical_report_rows": report_covered_rows,
            "archived_noncanonical_report_mappings": len(archived),
            "archive_unique_signal_dates": len(projected_by_signal),
            "archive_rows": total_rows,
            "unavailable_report_mappings": len(unavailable),
            "unavailable_signal_dates": sorted(
                {row["signal_date"] for row in unavailable}
            ),
            "duplicate_signal_dates": duplicate_signal_dates,
            "nontrading_source_report_dates": nontrading_source_report_dates,
            "oof_cutoff_signal_date": oof_cutoff,
        },
        "research_diagnostic_coverage": diagnostic_coverage,
        "p_fill_shadow_top2_oof": p_fill_shadow_oof_top2,
        "p_fill_shadow_top2_forward": forward_p_fill_shadow_top2,
        "source_bindings": {
            "oof_top10": oof_binding,
            "validation": validation_binding,
            "history_sources_manifest": sources_manifest_binding,
            "trading_calendar": calendar_binding,
        },
        "shards": shard_entries,
        "report_map": {
            "url": "outputs/decision/three_rank_history/report_map.json",
            "sha256": report_map_meta["sha256"],
            "bytes": report_map_meta["bytes"],
        },
    }
    statistics_meta = _write_bytes(
        output_root / "statistics.json", _json_bytes(statistics, pretty=True)
    )
    index = {
        "schema_version": INDEX_SCHEMA,
        "index_kind": "dated_annual_shards_pointer_only",
        "data_alias": False,
        "latest_fallback_allowed": False,
        "research_only": True,
        "actual_execution_claimed": False,
        "statistics_url": "outputs/decision/three_rank_history/statistics.json",
        "statistics_sha256": statistics_meta["sha256"],
        "report_map_url": "outputs/decision/three_rank_history/report_map.json",
        "report_map_sha256": report_map_meta["sha256"],
        "shards": shard_entries,
    }
    index_meta = _write_bytes(output_root / "index.json", _json_bytes(index, pretty=True))
    return {
        "index": index,
        "index_sha256": index_meta["sha256"],
        "statistics": statistics,
        "output_root": str(output_root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="DC20 checkout root",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Pages-only output directory",
    )
    args = parser.parse_args(argv)
    result = build_history_archive(args.source_root, args.output_root)
    print(
        json.dumps(
            {
                "status": "ok",
                "index_sha256": result["index_sha256"],
                "coverage": result["statistics"]["coverage"],
                "output_root": result["output_root"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
