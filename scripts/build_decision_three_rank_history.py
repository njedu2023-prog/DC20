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

ARCHIVE_SCHEMA = "dc20_three_rank_history_archive_v1"
EVIDENCE_SCHEMA = "dc20_three_rank_history_evidence_v1"
REPORT_MAP_SCHEMA = "dc20_three_rank_history_report_map_v1"
STATISTICS_SCHEMA = "dc20_three_rank_history_statistics_v1"
INDEX_SCHEMA = "dc20_three_rank_history_index_v1"

OFFICIAL_PROMOTION_STATUS = "TIME_HONEST_OOF_RESEARCH"
UNRELEASED_STATUS = "RESEARCH_NOT_RELEASED"
AVAILABLE_STATUS = "AVAILABLE_TIME_HONEST_OOF_RESEARCH"
ARCHIVED_STATUS = "ARCHIVED_NONCANONICAL_SOURCE_EXEC"
UNAVAILABLE_STATUS = "UNAVAILABLE_SOURCE_AFTER_OOF_CUTOFF"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^20\d{6}$")

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
    "promotion_hit",
    "market_fill_proxy",
    "big_loss_hit",
    "profit_hit",
    "net_return",
    "promotion_oof_train_end",
    "big_loss_oof_train_end",
    "profit_oof_train_end",
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
    text = str(value or "").strip()
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
    if not str(value or "").strip():
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
    }[head]
    probability_field = {
        "promotion": "predicted_promotion_probability",
        "big_loss": "predicted_big_loss_probability",
        "profit": "predicted_profit_probability",
    }[head]
    rank_score_field = {
        "promotion": "promotion_rank_score",
        "big_loss": "big_loss_rank_score",
        "profit": "profit_rank_score",
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
            for head in ("promotion", "big_loss", "profit")
        }
        for head, meta in head_meta.items():
            per_head_ranks[head].append(meta["rank"])
            if meta["rank"] is not None:
                if not meta["train_end"] or not meta["train_end"] < signal_date:
                    _fail(f"{head} OOF train_end is not before D: {signal_date}")
        promotion = head_meta["promotion"]
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

    for head in ("promotion", "big_loss", "profit"):
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
                "promotion_hit": actual["promotion_hit"],
                "market_fill_proxy": actual["market_fill_proxy"],
                "big_loss_hit": actual["big_loss_hit"],
                "profit_hit": actual["profit_hit"],
                "net_return": actual["net_return"],
                "promotion_oof_train_end": row["promotion_oof"]["train_end"],
                "big_loss_oof_train_end": diagnostics["big_loss"]["train_end"],
                "profit_oof_train_end": diagnostics["profit"]["train_end"],
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
    oof_cutoff = max(grouped)
    validation_cutoff = _date(
        validation.get("heads", {}).get("promotion", {}).get("production", {}).get(
            "trained_signal_end"
        ),
        field="validation promotion trained_signal_end",
    )
    if validation_cutoff != oof_cutoff:
        _fail("validation trained cutoff does not match OOF cutoff")

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
    for head in ("big_loss", "profit"):
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
