#!/usr/bin/env python3
"""Strict, dependency-free freshness truth for the Decision Pages build."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
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
DATED_RESEARCH_RE = re.compile(r"research_context_(\d{8})\.json")
DATED_DC20_RESEARCH_RE = re.compile(r"research_context_dc20_(\d{8})\.json")
DATED_THREE_RANK_JSON_RE = re.compile(r"three_rank_top10_(\d{8})\.json")
DATED_THREE_RANK_CSV_RE = re.compile(r"three_rank_top10_(\d{8})\.csv")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PRIMARY_CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ)")
GIT_OBJECT_RE = re.compile(r"[0-9a-f]{40}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)"
)
REQUIRED_CALENDAR_COLUMNS = frozenset({"exchange", "cal_date", "is_open"})
THREE_RANK_SCHEMA = "decision_three_rank_top10_v1"
THREE_RANK_CONTRACT_VERSION = "decision_three_rank_v1"
THREE_RANK_INDEX_SCHEMA = "decision_three_rank_index_v1"
THREE_RANK_INDEX_KIND = "dated_three_rank_pointer_only"
PRIMARY_SINGLE_PROFIT_INDEX_SCHEMA = (
    "dc20_primary_single_profit_research_index_v1"
)
PRIMARY_MIXED_PROFIT_INDEX_SCHEMA = (
    "dc20_primary_mixed_profit_research_index_v1"
)
PRIMARY_SINGLE_PROFIT_SCHEMA = "dc20_primary_single_profit_research_v1"
PRIMARY_MIXED_PROFIT_SCHEMA = (
    "dc20_primary_mixed_profit_research_projection_v1"
)
PRIMARY_PROFIT_INDEX_KIND = "dated_primary_profit_research_pointer_only"
PRIMARY_PROFIT_CONTRACT_ID = "dc20_primary_profit_research_20260827_v1"
PRIMARY_PROFIT_BOUNDARIES = {
    "research_only": True,
    "public_research_projection_allowed": True,
    "estimated_probability_calibrated": False,
    "formal_probability_allowed": False,
    "formal_rank_allowed": False,
    "official_trade_action_allowed": False,
    "may_create_trade_action": False,
    "broker_or_order_integration_allowed": False,
    "actual_execution_claimed": False,
    "human_decision_support_only": True,
    "proxy_scores_uncalibrated": True,
    "may_change_promotion_membership_or_rank": False,
    "forward_selection_created": False,
    "forward_statistics_updated": False,
    "action_input_consumed": False,
}
P_FILL_SHADOW_TOP2_SLOTS = 2
INDEPENDENCE_CUTOVER_SIGNAL_DATE = "20260821"
THREE_RANK_HEAD_FIELDS = {
    "promotion": ("promotion_rank", "predicted_promotion_probability"),
    "big_loss": ("big_loss_safety_rank", "predicted_big_loss_probability"),
    "profit": ("profit_rank", "predicted_profit_probability"),
}
THREE_RANK_CSV_FIELDS = (
    "contract_version",
    "schema_version",
    "contract_status",
    "bundle_sha256",
    "top10_members_sha256",
    "signal_date",
    "exec_date",
    "exit_date",
    "feature_as_of_date",
    "feature_snapshot_sha256",
    "promotion_pool_size",
    "top10_count",
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
    "promotion_model_status",
    "promotion_model_version",
    "promotion_model_as_of_date",
    "promotion_model_artifact_sha256",
    "promotion_validation_gate_pass_count",
    "promotion_validation_gate_total_count",
    "promotion_validation_gate_score_pct",
    "big_loss_model_status",
    "big_loss_model_version",
    "big_loss_model_as_of_date",
    "big_loss_model_artifact_sha256",
    "big_loss_validation_gate_pass_count",
    "big_loss_validation_gate_total_count",
    "big_loss_validation_gate_score_pct",
    "profit_model_status",
    "profit_model_version",
    "profit_model_as_of_date",
    "profit_model_artifact_sha256",
    "profit_validation_gate_pass_count",
    "profit_validation_gate_total_count",
    "profit_validation_gate_score_pct",
    "p_fill_shadow_rank",
    "p_fill_shadow_probability",
    "p_fill_shadow_status",
    "p_fill_shadow_model_version",
    "p_fill_shadow_model_as_of_date",
    "p_fill_shadow_model_artifact_sha256",
    "p_fill_shadow_snapshot_sha256",
    "p_fill_shadow_validation_gate_pass_count",
    "p_fill_shadow_validation_gate_total_count",
    "p_fill_shadow_validation_gate_score_pct",
)


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
    research_dates: tuple[str, ...]
    latest_action_report_date: str
    latest_action_url: str


@dataclass(frozen=True)
class PrimaryProfitResearchTruth:
    signal_date: str
    exec_date: str
    exit_date: str
    generation_mode: str
    candidate_count: int
    top10_members_sha256: str


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


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _three_rank_member_hash(signal_date: str, codes: list[str]) -> str:
    payload = {
        "schema": "dc20_three_rank_member_set_v1",
        "signal_date": signal_date,
        "members": sorted(set(codes)),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _three_rank_core_projection(contract: dict[str, Any]) -> dict[str, Any]:
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
            for row in contract.get("rows", [])
        ],
    }


def _p_fill_shadow_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _p_fill_shadow_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _p_fill_shadow_integer(value: Any) -> int | None:
    number = _p_fill_shadow_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _p_fill_shadow_top2_projection(
    rows: list[dict[str, Any]],
    *,
    model_status: str,
) -> dict[str, Any]:
    selected = (
        sorted(
            (
                {
                    "ts_code": _p_fill_shadow_text(row.get("ts_code")),
                    "name": _p_fill_shadow_text(row.get("name")),
                    "p_fill_shadow_rank": _p_fill_shadow_integer(
                        row.get("p_fill_shadow_rank")
                    ),
                    "p_fill_shadow_probability": _p_fill_shadow_number(
                        row.get("p_fill_shadow_probability")
                    ),
                }
                for row in rows
                if (_p_fill_shadow_integer(row.get("p_fill_shadow_rank")) or 0)
                <= P_FILL_SHADOW_TOP2_SLOTS
                and _p_fill_shadow_integer(row.get("p_fill_shadow_rank"))
                is not None
            ),
            key=lambda row: (row["p_fill_shadow_rank"], row["ts_code"]),
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
        "requested_slots": P_FILL_SHADOW_TOP2_SLOTS,
        "actual_slots": len(selected),
        "may_change_core_bundle": False,
        "may_override_core_ranks": False,
        "may_create_trade_action": False,
        "rows": selected,
    }


def _p_fill_shadow_snapshot_sha256(
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    members_sha256: str,
    shadow: dict[str, Any],
    rows: list[dict[str, Any]],
    shadow_top2: dict[str, Any],
) -> str:
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
                    "ts_code": _p_fill_shadow_text(row.get("ts_code")),
                    "p_fill_shadow_rank": _p_fill_shadow_integer(
                        row.get("p_fill_shadow_rank")
                    ),
                    "p_fill_shadow_probability": _p_fill_shadow_number(
                        row.get("p_fill_shadow_probability")
                    ),
                    "p_fill_shadow_status": _p_fill_shadow_text(
                        row.get("p_fill_shadow_status") or ""
                    )
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
                    "ts_code": _p_fill_shadow_text(row.get("ts_code")),
                    "p_fill_shadow_rank": _p_fill_shadow_integer(
                        row.get("p_fill_shadow_rank")
                    ),
                }
                for row in shadow_top2.get("rows", [])
            ],
        },
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _strict_probability(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionPagesTruthError(f"{field} must be a numeric probability")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise DecisionPagesTruthError(f"{field} is outside [0,1]")
    return number


def _validate_validation_gate_summary(
    meta: dict[str, Any],
    *,
    label: str,
) -> None:
    for field in (
        "validation_gate_pass_count",
        "validation_gate_total_count",
        "validation_gate_score_pct",
    ):
        if field not in meta:
            raise DecisionPagesTruthError(
                f"{label} validation gate metadata is missing"
            )
    pass_count = meta.get("validation_gate_pass_count")
    total_count = meta.get("validation_gate_total_count")
    score = meta.get("validation_gate_score_pct")
    if pass_count is None and total_count is None and score is None:
        return
    if (
        type(pass_count) is not int
        or type(total_count) is not int
        or total_count <= 0
        or pass_count < 0
        or pass_count > total_count
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        raise DecisionPagesTruthError(
            f"{label} validation gate summary is invalid"
        )
    expected = round(100.0 * pass_count / total_count, 1)
    if not math.isclose(float(score), expected, abs_tol=1e-9):
        raise DecisionPagesTruthError(
            f"{label} validation gate score is inconsistent"
        )


def _validate_three_rank_contract_payload(
    payload: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any] | None:
    contract = payload.get("three_rank")
    if contract is None:
        return None
    if not isinstance(contract, dict):
        raise DecisionPagesTruthError(f"{label}.three_rank must be an object")
    if contract.get("schema_version") != THREE_RANK_SCHEMA:
        raise DecisionPagesTruthError(f"{label}.three_rank schema is invalid")
    if contract.get("contract_version") != THREE_RANK_CONTRACT_VERSION:
        raise DecisionPagesTruthError(
            f"{label}.three_rank contract version is invalid"
        )
    if contract.get("artifact_kind") != "d_close_independent_three_rank_top10":
        raise DecisionPagesTruthError(
            f"{label}.three_rank artifact kind is invalid"
        )
    signal_date, signal_day = _strict_date(
        contract.get("signal_date"), f"{label}.three_rank.signal_date"
    )
    exec_date, exec_day = _strict_date(
        contract.get("exec_date"), f"{label}.three_rank.exec_date"
    )
    exit_date, exit_day = _strict_date(
        contract.get("exit_date"), f"{label}.three_rank.exit_date"
    )
    if not signal_day < exec_day < exit_day:
        raise DecisionPagesTruthError(f"{label}.three_rank dates are invalid")
    if (
        signal_date != payload.get("signal_date")
        or exec_date != payload.get("exec_date")
        or exit_date != payload.get("exit_date")
        or contract.get("feature_as_of_date") != signal_date
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank is not bound to its parent dates"
        )
    rows = contract.get("rows")
    if not isinstance(rows, list) or len(rows) > 10 or any(
        not isinstance(row, dict) for row in rows
    ):
        raise DecisionPagesTruthError(f"{label}.three_rank rows are invalid")
    pool_size = contract.get("promotion_pool_size")
    if (
        type(pool_size) is not int
        or pool_size < 0
        or pool_size < len(rows)
        or contract.get("top10_count") != len(rows)
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank pool or row count is invalid"
        )
    codes: list[str] = []
    required_row_fields = {
        "ts_code",
        "name",
        "industry",
        "stage_transition",
        "top10_selected",
        "p_fill_shadow_rank",
        "p_fill_shadow_probability",
        "p_fill_shadow_status",
        *(field for fields in THREE_RANK_HEAD_FIELDS.values() for field in fields),
    }
    for position, row in enumerate(rows, start=1):
        if not required_row_fields.issubset(row):
            raise DecisionPagesTruthError(
                f"{label}.three_rank row {position} lacks required fields"
            )
        code = row.get("ts_code")
        if type(code) is not str or not code.strip():
            raise DecisionPagesTruthError(
                f"{label}.three_rank row {position} code is invalid"
            )
        if row.get("top10_selected") != 1:
            raise DecisionPagesTruthError(
                f"{label}.three_rank row {position} escaped the frozen set"
            )
        if row.get("stage_transition") not in {"2→3", "3→4"}:
            raise DecisionPagesTruthError(
                f"{label}.three_rank row {position} escaped the hard stages"
            )
        codes.append(code.strip())
    if len(set(codes)) != len(codes):
        raise DecisionPagesTruthError(f"{label}.three_rank has duplicate codes")
    expected_members = _three_rank_member_hash(signal_date, codes)
    if contract.get("top10_members_sha256") != expected_members:
        raise DecisionPagesTruthError(
            f"{label}.three_rank frozen member hash is invalid"
        )
    models = contract.get("models")
    if not isinstance(models, dict) or set(models) != set(
        THREE_RANK_HEAD_FIELDS
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank model inventory is invalid"
        )
    ready_heads = 0
    for head, (rank_field, probability_field) in THREE_RANK_HEAD_FIELDS.items():
        meta = models[head]
        if not isinstance(meta, dict):
            raise DecisionPagesTruthError(
                f"{label}.three_rank {head} model metadata is invalid"
            )
        status = meta.get("status")
        if type(status) is not str or not (
            status == "READY" or status.startswith("NOT_READY_")
        ):
            raise DecisionPagesTruthError(
                f"{label}.three_rank {head} status is invalid"
            )
        ready = status == "READY"
        ready_heads += int(ready)
        if (
            meta.get("ranking_ready") is not ready
            or meta.get("probability_ready") is not ready
            or meta.get("rank_field") != rank_field
            or meta.get("probability_field") != probability_field
            or meta.get("input_members_sha256") != expected_members
        ):
            raise DecisionPagesTruthError(
                f"{label}.three_rank {head} binding is invalid"
            )
        _validate_validation_gate_summary(
            meta,
            label=f"{label}.three_rank.{head}",
        )
        ranks = [row.get(rank_field) for row in rows]
        probabilities = [row.get(probability_field) for row in rows]
        if ready:
            version = meta.get("version")
            model_as_of, model_as_of_day = _strict_date(
                meta.get("model_as_of_date"),
                f"{label}.three_rank.{head}.model_as_of_date",
            )
            if (
                type(version) is not str
                or not version.strip()
                or model_as_of_day >= signal_day
                or type(meta.get("artifact_sha256")) is not str
                or SHA256_RE.fullmatch(meta["artifact_sha256"]) is None
            ):
                raise DecisionPagesTruthError(
                    f"{label}.three_rank {head} READY provenance is invalid"
                )
            if any(type(rank) is not int or isinstance(rank, bool) for rank in ranks):
                raise DecisionPagesTruthError(
                    f"{label}.three_rank {head} ranks are invalid"
                )
            if sorted(ranks) != list(range(1, len(rows) + 1)):
                raise DecisionPagesTruthError(
                    f"{label}.three_rank {head} ranks are not 1..N"
                )
            for position, probability in enumerate(probabilities, start=1):
                _strict_probability(
                    probability,
                    f"{label}.three_rank row {position}.{probability_field}",
                )
        elif any(
            rank is not None or probability is not None
            for rank, probability in zip(ranks, probabilities)
        ):
            raise DecisionPagesTruthError(
                f"{label}.three_rank {head} emitted a fake unready rank"
            )
    promotion_ready = models["promotion"]["status"] == "READY"
    if not promotion_ready and any(
        models[head]["status"] != "NOT_READY_NO_FROZEN_TOP10"
        for head in ("big_loss", "profit")
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank downstream READY without engine A"
        )
    if (not promotion_ready and rows) or (
        promotion_ready and len(rows) != min(10, pool_size)
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank official Top10 membership is invalid"
        )
    snapshot = contract.get("feature_snapshot_sha256")
    if (
        promotion_ready
        and rows
        and (type(snapshot) is not str or SHA256_RE.fullmatch(snapshot) is None)
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank feature snapshot is invalid"
        )
    expected_status = (
        "READY"
        if ready_heads == 3
        else "PARTIAL_MODELS_NOT_READY"
        if promotion_ready
        else "NOT_READY_PROMOTION"
    )
    if contract.get("status") != expected_status:
        raise DecisionPagesTruthError(
            f"{label}.three_rank aggregate status is invalid"
        )
    shadow = contract.get("shadow_contract")
    if (
        not isinstance(shadow, dict)
        or shadow.get("status") != "ANNOTATION_ONLY"
        or shadow.get("input_members_sha256") != expected_members
        or shadow.get("may_change_membership") is not False
        or shadow.get("may_override_core_ranks") is not False
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow override contract is invalid"
        )
    shadow_model_status = shadow.get("model_status")
    if type(shadow_model_status) is not str or not (
        shadow_model_status.startswith("SHADOW_")
        or shadow_model_status.startswith("NOT_READY_")
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow model status is invalid"
        )
    if shadow_model_status == "SHADOW_READY":
        shadow_as_of, shadow_as_of_day = _strict_date(
            shadow.get("model_as_of_date"),
            f"{label}.three_rank.p_fill_shadow.model_as_of_date",
        )
        if (
            type(shadow.get("model_version")) is not str
            or not shadow["model_version"].strip()
            or shadow_as_of_day >= signal_day
            or type(shadow.get("model_artifact_sha256")) is not str
            or SHA256_RE.fullmatch(shadow["model_artifact_sha256"]) is None
        ):
            raise DecisionPagesTruthError(
                f"{label}.three_rank SHADOW_READY provenance is invalid"
            )
    if not promotion_ready and shadow_model_status != (
        "SHADOW_NOT_READY_NO_FROZEN_TOP10"
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow exists without a frozen Top10"
        )
    shadow_ranks = [row.get("p_fill_shadow_rank") for row in rows]
    shadow_probabilities = [
        row.get("p_fill_shadow_probability") for row in rows
    ]
    shadow_statuses = [row.get("p_fill_shadow_status") for row in rows]
    if any(status != shadow_model_status for status in shadow_statuses):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow row statuses disagree with the model"
        )
    if shadow_model_status == "SHADOW_READY":
        if any(type(rank) is not int for rank in shadow_ranks):
            raise DecisionPagesTruthError(
                f"{label}.three_rank shadow ranks are invalid"
            )
        if sorted(shadow_ranks) != list(range(1, len(rows) + 1)):
            raise DecisionPagesTruthError(
                f"{label}.three_rank shadow ranks are not 1..N"
            )
        for position, probability in enumerate(
            shadow_probabilities, start=1
        ):
            _strict_probability(
                probability,
                (
                    f"{label}.three_rank row {position}."
                    "p_fill_shadow_probability"
                ),
            )
    elif any(
        rank is not None or probability is not None
        for rank, probability in zip(shadow_ranks, shadow_probabilities)
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow emitted output while not ready"
        )
    _validate_validation_gate_summary(
        shadow,
        label=f"{label}.three_rank.p_fill_shadow",
    )
    expected_shadow_top2 = _p_fill_shadow_top2_projection(
        rows,
        model_status=shadow_model_status,
    )
    if contract.get("shadow_top2") != expected_shadow_top2:
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow Top2 contract is invalid"
        )
    expected_shadow_snapshot_sha256 = _p_fill_shadow_snapshot_sha256(
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        members_sha256=expected_members,
        shadow=shadow,
        rows=rows,
        shadow_top2=expected_shadow_top2,
    )
    shadow_snapshot_sha256 = shadow.get("shadow_snapshot_sha256")
    if (
        type(shadow_snapshot_sha256) is not str
        or SHA256_RE.fullmatch(shadow_snapshot_sha256) is None
        or shadow_snapshot_sha256 != expected_shadow_snapshot_sha256
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank shadow snapshot hash is invalid"
        )
    expected_bundle = hashlib.sha256(
        _canonical_json_bytes(_three_rank_core_projection(contract))
    ).hexdigest()
    if contract.get("bundle_sha256") != expected_bundle:
        raise DecisionPagesTruthError(
            f"{label}.three_rank bundle hash is invalid"
        )
    downloads = contract.get("downloads")
    expected_prefix = f"outputs/decision/three_rank_top10_{signal_date}"
    if (
        not isinstance(downloads, dict)
        or downloads.get("json_url") != f"{expected_prefix}.json"
        or downloads.get("csv_url") != f"{expected_prefix}.csv"
        or type(downloads.get("csv_sha256")) is not str
        or SHA256_RE.fullmatch(downloads["csv_sha256"]) is None
        or downloads.get("row_count") != len(rows)
    ):
        raise DecisionPagesTruthError(
            f"{label}.three_rank download binding is invalid"
        )
    return contract


def _csv_scalar(value: Any) -> str:
    return "" if value is None else str(value)


def _validate_three_rank_downloads(
    *,
    payload: dict[str, Any],
    site_root: Path,
    label: str,
) -> None:
    contract = _validate_three_rank_contract_payload(payload, label=label)
    if contract is None:
        return
    signal_date = contract["signal_date"]
    json_path = _site_child(
        site_root,
        ("outputs", "decision", f"three_rank_top10_{signal_date}.json"),
        f"{label}.three_rank JSON download",
    )
    artifact = _load_json_object(
        json_path, f"{label}.three_rank JSON download"
    )
    artifact_wrapper = {
        "signal_date": contract["signal_date"],
        "exec_date": contract["exec_date"],
        "exit_date": contract["exit_date"],
        "three_rank": artifact,
    }
    _validate_three_rank_contract_payload(
        artifact_wrapper,
        label=f"{label}.three_rank JSON download",
    )
    if artifact != contract:
        raise DecisionPagesTruthError(
            f"{label}.three_rank JSON download differs from the embedded contract"
        )
    csv_path = _site_child(
        site_root,
        ("outputs", "decision", f"three_rank_top10_{signal_date}.csv"),
        f"{label}.three_rank CSV download",
    )
    if csv_path.is_symlink() or not csv_path.is_file() or csv_path.stat().st_size <= 0:
        raise DecisionPagesTruthError(
            f"{label}.three_rank CSV download is missing, empty, or a symlink"
        )
    csv_bytes = csv_path.read_bytes()
    if hashlib.sha256(csv_bytes).hexdigest() != contract["downloads"][
        "csv_sha256"
    ]:
        raise DecisionPagesTruthError(
            f"{label}.three_rank CSV download hash drifted"
        )
    try:
        reader = csv.DictReader(csv_bytes.decode("utf-8-sig").splitlines())
        csv_rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise DecisionPagesTruthError(
            f"{label}.three_rank CSV download is invalid"
        ) from exc
    if tuple(reader.fieldnames or ()) != THREE_RANK_CSV_FIELDS:
        raise DecisionPagesTruthError(
            f"{label}.three_rank CSV header is invalid"
        )
    if len(csv_rows) != len(contract["rows"]):
        raise DecisionPagesTruthError(
            f"{label}.three_rank CSV row count is invalid"
        )
    models = contract["models"]
    common = {
        "contract_version": contract["contract_version"],
        "schema_version": contract["schema_version"],
        "contract_status": contract["status"],
        "bundle_sha256": contract["bundle_sha256"],
        "top10_members_sha256": contract["top10_members_sha256"],
        "signal_date": contract["signal_date"],
        "exec_date": contract["exec_date"],
        "exit_date": contract["exit_date"],
        "feature_as_of_date": contract["feature_as_of_date"],
        "feature_snapshot_sha256": contract["feature_snapshot_sha256"],
        "promotion_pool_size": contract["promotion_pool_size"],
        "top10_count": contract["top10_count"],
    }
    for head in THREE_RANK_HEAD_FIELDS:
        common[f"{head}_model_status"] = models[head]["status"]
        common[f"{head}_model_version"] = models[head]["version"]
        common[f"{head}_model_as_of_date"] = models[head][
            "model_as_of_date"
        ]
        common[f"{head}_model_artifact_sha256"] = models[head][
            "artifact_sha256"
        ]
        common[f"{head}_validation_gate_pass_count"] = models[head][
            "validation_gate_pass_count"
        ]
        common[f"{head}_validation_gate_total_count"] = models[head][
            "validation_gate_total_count"
        ]
        common[f"{head}_validation_gate_score_pct"] = models[head][
            "validation_gate_score_pct"
        ]
    shadow = contract["shadow_contract"]
    common["p_fill_shadow_model_version"] = shadow["model_version"]
    common["p_fill_shadow_model_as_of_date"] = shadow[
        "model_as_of_date"
    ]
    common["p_fill_shadow_model_artifact_sha256"] = shadow[
        "model_artifact_sha256"
    ]
    common["p_fill_shadow_snapshot_sha256"] = shadow[
        "shadow_snapshot_sha256"
    ]
    common["p_fill_shadow_validation_gate_pass_count"] = shadow[
        "validation_gate_pass_count"
    ]
    common["p_fill_shadow_validation_gate_total_count"] = shadow[
        "validation_gate_total_count"
    ]
    common["p_fill_shadow_validation_gate_score_pct"] = shadow[
        "validation_gate_score_pct"
    ]
    for position, (csv_row, contract_row) in enumerate(
        zip(csv_rows, contract["rows"]), start=1
    ):
        expected = {**common, **contract_row}
        if any(
            csv_row[field] != _csv_scalar(expected.get(field))
            for field in THREE_RANK_CSV_FIELDS
        ):
            raise DecisionPagesTruthError(
                f"{label}.three_rank CSV row {position} differs from JSON"
            )


def _three_rank_index_payload(
    contract: dict[str, Any],
    *,
    contract_sha256: str,
) -> dict[str, Any]:
    downloads = contract["downloads"]
    return {
        "schema_version": THREE_RANK_INDEX_SCHEMA,
        "index_kind": THREE_RANK_INDEX_KIND,
        "data_alias": False,
        "latest_signal_date": contract["signal_date"],
        "latest_exec_date": contract["exec_date"],
        "latest_exit_date": contract["exit_date"],
        "latest_status": contract["status"],
        "latest_contract_url": downloads["json_url"],
        "latest_csv_url": downloads["csv_url"],
        "latest_contract_sha256": contract_sha256,
        "latest_csv_sha256": downloads["csv_sha256"],
        "latest_bundle_sha256": contract["bundle_sha256"],
        "latest_top10_members_sha256": contract[
            "top10_members_sha256"
        ],
    }


def validate_three_rank_index_truth(
    *,
    index_path: Path,
    site_root: Path,
) -> dict[str, Any]:
    site_root, _decision_root = _site_decision_root(Path(site_root))
    expected_path = _site_child(
        site_root,
        ("outputs", "decision", "three_rank_index.json"),
        "three-rank index",
    )
    supplied = _without_symlink_components(Path(index_path), "three-rank index")
    if supplied != expected_path:
        raise DecisionPagesTruthError("three-rank index path is not exact")
    index = _load_json_object(supplied, "three-rank index")
    if (
        index.get("schema_version") != THREE_RANK_INDEX_SCHEMA
        or index.get("index_kind") != THREE_RANK_INDEX_KIND
        or index.get("data_alias") is not False
    ):
        raise DecisionPagesTruthError("three-rank index contract is invalid")
    signal_date, _ = _strict_date(
        index.get("latest_signal_date"),
        "three-rank index.latest_signal_date",
    )
    exec_date, signal_exec_day = _strict_date(
        index.get("latest_exec_date"),
        "three-rank index.latest_exec_date",
    )
    exit_date, exit_day = _strict_date(
        index.get("latest_exit_date"),
        "three-rank index.latest_exit_date",
    )
    _, signal_day = _strict_date(signal_date, "three-rank index signal date")
    if not signal_day < signal_exec_day < exit_day:
        raise DecisionPagesTruthError("three-rank index date order is invalid")
    expected_prefix = f"outputs/decision/three_rank_top10_{signal_date}"
    if (
        index.get("latest_contract_url") != f"{expected_prefix}.json"
        or index.get("latest_csv_url") != f"{expected_prefix}.csv"
    ):
        raise DecisionPagesTruthError("three-rank index URL is not dated")
    contract_path = _site_child(
        site_root,
        ("outputs", "decision", f"three_rank_top10_{signal_date}.json"),
        "three-rank index contract",
    )
    contract = _load_json_object(contract_path, "three-rank index contract")
    contract_sha256 = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    wrapper = {
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "three_rank": contract,
    }
    _validate_three_rank_downloads(
        payload=wrapper,
        site_root=site_root,
        label="three-rank index contract",
    )
    expected = _three_rank_index_payload(
        contract,
        contract_sha256=contract_sha256,
    )
    if index != expected:
        raise DecisionPagesTruthError(
            "three-rank index differs from its immutable dated contract"
        )
    return contract


def _primary_profit_file_sha256(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DecisionPagesTruthError(f"{label} is missing, empty, or a symlink")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _primary_profit_bound_path(
    site_root: Path,
    value: Any,
    expected: str,
    label: str,
) -> Path:
    if value != expected:
        raise DecisionPagesTruthError(f"{label} path is not the exact dated path")
    return _site_child(site_root, tuple(expected.split("/")), label)


def _validate_primary_profit_index_projection(
    *,
    site_root: Path,
    index: dict[str, Any],
    output_root: str,
    index_schema: str,
    projection_schema: str,
    display_name: str,
) -> dict[str, Any]:
    if (
        index.get("schema_version") != index_schema
        or index.get("index_kind") != PRIMARY_PROFIT_INDEX_KIND
        or index.get("data_alias") is not False
        or index.get("display_name") != display_name
    ):
        raise DecisionPagesTruthError(
            f"{display_name} primary-profit index contract is invalid"
        )
    signal_date, signal_day = _strict_date(
        index.get("latest_signal_date"), f"{display_name} index signal date"
    )
    exec_date, exec_day = _strict_date(
        index.get("latest_exec_date"), f"{display_name} index exec date"
    )
    exit_date, exit_day = _strict_date(
        index.get("latest_exit_date"), f"{display_name} index exit date"
    )
    if not signal_day < exec_day < exit_day:
        raise DecisionPagesTruthError(f"{display_name} index dates are invalid")
    mode = index.get("generation_mode")
    expected_status = {
        "NATURAL": "PROSPECTIVE_RESEARCH",
        "RETROSPECTIVE_RECOVERY": "RETROSPECTIVE_NON_FORWARD_RESEARCH",
    }.get(mode)
    if (
        expected_status is None
        or index.get("status") != expected_status
        or index.get("prospective") is not (mode == "NATURAL")
        or index.get("retrospective_non_forward")
        is not (mode == "RETROSPECTIVE_RECOVERY")
        or index.get("boundaries") != PRIMARY_PROFIT_BOUNDARIES
    ):
        raise DecisionPagesTruthError(
            f"{display_name} index mode or safety boundary is invalid"
        )
    candidate_count = index.get("candidate_count")
    if type(candidate_count) is not int or not 0 <= candidate_count <= 10:
        raise DecisionPagesTruthError(
            f"{display_name} index candidate count is invalid"
        )
    prefix = f"{output_root}/projection_{signal_date}"
    json_path = _primary_profit_bound_path(
        site_root,
        index.get("latest_projection_json_url"),
        f"{prefix}.json",
        f"{display_name} projection JSON",
    )
    csv_path = _primary_profit_bound_path(
        site_root,
        index.get("latest_projection_csv_url"),
        f"{prefix}.csv",
        f"{display_name} projection CSV",
    )
    if (
        index.get("latest_projection_json_sha256")
        != _primary_profit_file_sha256(json_path, f"{display_name} projection JSON")
        or index.get("latest_projection_csv_sha256")
        != _primary_profit_file_sha256(csv_path, f"{display_name} projection CSV")
    ):
        raise DecisionPagesTruthError(
            f"{display_name} projection download hash drifted"
        )
    projection = _load_json_object(json_path, f"{display_name} projection")
    if (
        projection.get("schema_version") != projection_schema
        or projection.get("contract_id") != PRIMARY_PROFIT_CONTRACT_ID
        or projection.get("display_name") != display_name
        or projection.get("research_only") is not True
        or projection.get("generation_mode") != mode
        or projection.get("status") != expected_status
        or projection.get("prospective") is not (mode == "NATURAL")
        or projection.get("retrospective_non_forward")
        is not (mode == "RETROSPECTIVE_RECOVERY")
        or projection.get("signal_date") != signal_date
        or projection.get("exec_date") != exec_date
        or projection.get("exit_date") != exit_date
        or projection.get("candidate_count") != candidate_count
        or projection.get("boundaries") != PRIMARY_PROFIT_BOUNDARIES
    ):
        raise DecisionPagesTruthError(
            f"{display_name} projection identity or boundary is invalid"
        )
    rows = projection.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) != candidate_count
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise DecisionPagesTruthError(f"{display_name} projection rows are invalid")
    codes: list[str] = []
    promotion_ranks: list[int] = []
    for position, row in enumerate(rows, start=1):
        code = row.get("ts_code")
        rank = row.get("promotion_rank")
        if (
            type(code) is not str
            or PRIMARY_CODE_RE.fullmatch(code) is None
            or type(rank) is not int
            or isinstance(rank, bool)
            or row.get("stage_transition") not in {"2→3", "3→4"}
        ):
            raise DecisionPagesTruthError(
                f"{display_name} projection row {position} identity is invalid"
            )
        if projection_schema == PRIMARY_SINGLE_PROFIT_SCHEMA:
            relative_rank = row.get("legacy_profit_relative_rank")
            raw_score = row.get("legacy_profit_raw_score")
            if (
                type(relative_rank) is not int
                or isinstance(relative_rank, bool)
                or type(raw_score) not in {int, float}
                or isinstance(raw_score, bool)
                or not math.isfinite(float(raw_score))
                or not 0 <= float(raw_score) <= 1
            ):
                raise DecisionPagesTruthError(
                    f"{display_name} projection row {position} score is invalid"
                )
        else:
            if row.get("executable_profit_research_rank") != position:
                raise DecisionPagesTruthError(
                    f"{display_name} projection rank order is invalid"
                )
            fill = row.get("research_fill_proxy_score")
            conditional = row.get("research_conditional_profit_score")
            joint = row.get("research_joint_proxy_score")
            alias = row.get("estimated_executable_profit_probability")
            if any(
                type(value) not in {int, float}
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
                for value in (fill, conditional, joint, alias)
            ) or not (
                math.isclose(float(joint), float(fill) * float(conditional), abs_tol=1e-15)
                and math.isclose(float(alias), float(joint), abs_tol=1e-15)
            ):
                raise DecisionPagesTruthError(
                    f"{display_name} projection row {position} proxy identity is invalid"
                )
        codes.append(code)
        promotion_ranks.append(rank)
    if (
        len(set(codes)) != len(codes)
        or sorted(promotion_ranks) != list(range(1, candidate_count + 1))
        or projection.get("top10_members_sha256")
        != _three_rank_member_hash(signal_date, codes)
    ):
        raise DecisionPagesTruthError(
            f"{display_name} changed frozen membership or promotion ranks"
        )
    for index_field, projection_field in (
        ("latest_projection_snapshot_sha256", "snapshot_sha256"),
        ("latest_top10_members_sha256", "top10_members_sha256"),
        ("latest_source_bundle_sha256", "source_bundle_sha256"),
        (
            "latest_source_feature_snapshot_sha256",
            "source_feature_snapshot_sha256",
        ),
    ):
        value = projection.get(projection_field)
        if (
            type(value) is not str
            or SHA256_RE.fullmatch(value) is None
            or index.get(index_field) != value
        ):
            raise DecisionPagesTruthError(
                f"{display_name} projection/index fingerprint drifted"
            )
    snapshot_payload = dict(projection)
    snapshot_payload.pop("snapshot_sha256", None)
    snapshot_payload.pop("downloads", None)
    expected_snapshot = hashlib.sha256(
        _canonical_json_bytes(snapshot_payload)
    ).hexdigest()
    if projection.get("snapshot_sha256") != expected_snapshot:
        raise DecisionPagesTruthError(
            f"{display_name} projection snapshot hash drifted"
        )
    downloads = projection.get("downloads")
    if (
        not isinstance(downloads, dict)
        or downloads.get("json_url") != f"{prefix}.json"
        or downloads.get("csv_url") != f"{prefix}.csv"
        or downloads.get("csv_sha256")
        != index.get("latest_projection_csv_sha256")
        or downloads.get("row_count") != candidate_count
    ):
        raise DecisionPagesTruthError(
            f"{display_name} projection download binding is invalid"
        )
    try:
        csv_rows = list(
            csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines())
        )
    except (UnicodeError, csv.Error) as exc:
        raise DecisionPagesTruthError(
            f"{display_name} projection CSV is invalid"
        ) from exc
    if len(csv_rows) != candidate_count or {
        row.get("ts_code") for row in csv_rows
    } != set(codes):
        raise DecisionPagesTruthError(
            f"{display_name} projection CSV membership drifted"
        )
    if projection.get("source_bindings") != index.get("source_bindings"):
        raise DecisionPagesTruthError(
            f"{display_name} projection/index source bindings drifted"
        )
    return projection


def validate_primary_profit_research_truth(
    *, site_root: Path
) -> PrimaryProfitResearchTruth | None:
    """Validate the new P0-authority single/mixed pair without Action inputs.

    Archived forward-v2/legacy-v1 contracts remain governed by their existing
    validators.  Once either primary index is advertised, both rankings must
    be present and bind the same exact P0 receipt/runtime/three-rank snapshot.
    """

    site_root, _decision_root = _site_decision_root(Path(site_root))
    single_index_path = _site_child(
        site_root,
        ("outputs", "decision", "legacy_profit_relative_research", "index.json"),
        "single-profit research index",
    )
    mixed_index_path = _site_child(
        site_root,
        ("outputs", "decision", "executable_profit_research", "index.json"),
        "mixed-profit research index",
    )
    present = (single_index_path.exists(), mixed_index_path.exists())
    if not any(present):
        return None
    single_index = (
        _load_json_object(single_index_path, "single-profit research index")
        if present[0]
        else None
    )
    mixed_index = (
        _load_json_object(mixed_index_path, "mixed-profit research index")
        if present[1]
        else None
    )
    new_single = (
        isinstance(single_index, dict)
        and single_index.get("schema_version")
        == PRIMARY_SINGLE_PROFIT_INDEX_SCHEMA
    )
    new_mixed = (
        isinstance(mixed_index, dict)
        and mixed_index.get("schema_version")
        == PRIMARY_MIXED_PROFIT_INDEX_SCHEMA
    )
    if not new_single and not new_mixed:
        return None
    if not new_single or not new_mixed:
        raise DecisionPagesTruthError(
            "primary-profit publication is a partial single/mixed chain"
        )
    single = _validate_primary_profit_index_projection(
        site_root=site_root,
        index=single_index,
        output_root="outputs/decision/legacy_profit_relative_research",
        index_schema=PRIMARY_SINGLE_PROFIT_INDEX_SCHEMA,
        projection_schema=PRIMARY_SINGLE_PROFIT_SCHEMA,
        display_name="单一盈利排序",
    )
    mixed = _validate_primary_profit_index_projection(
        site_root=site_root,
        index=mixed_index,
        output_root="outputs/decision/executable_profit_research",
        index_schema=PRIMARY_MIXED_PROFIT_INDEX_SCHEMA,
        projection_schema=PRIMARY_MIXED_PROFIT_SCHEMA,
        display_name="混合盈利排序",
    )
    shared_fields = (
        "signal_date",
        "exec_date",
        "exit_date",
        "generation_mode",
        "candidate_count",
        "top10_members_sha256",
        "source_bundle_sha256",
        "source_feature_snapshot_sha256",
        "source_bindings",
        "boundaries",
    )
    if any(single.get(field) != mixed.get(field) for field in shared_fields):
        raise DecisionPagesTruthError(
            "primary-profit single/mixed projections do not share one P0 source"
        )
    bindings = mixed.get("source_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        "contract",
        "primary_receipt",
        "runtime_features",
        "three_rank",
    }:
        raise DecisionPagesTruthError("primary-profit source inventory is invalid")
    date_value = str(mixed["signal_date"])
    expected_sources = {
        "contract": "models/decision_primary_profit_research_contract.json",
        "primary_receipt": f"outputs/decision/primary_d_receipt_{date_value}.json",
        "runtime_features": f"outputs/decision/primary_d_runtime_features_{date_value}.csv",
    }
    source_paths: dict[str, Path] = {}
    for name, expected in expected_sources.items():
        binding = bindings.get(name)
        if not isinstance(binding, dict):
            raise DecisionPagesTruthError(f"primary-profit {name} binding is invalid")
        source_paths[name] = _primary_profit_bound_path(
            site_root, binding.get("path"), expected, f"primary-profit {name}"
        )
        if binding.get("sha256") != _primary_profit_file_sha256(
            source_paths[name], f"primary-profit {name}"
        ):
            raise DecisionPagesTruthError(f"primary-profit {name} SHA drifted")
    contract = _load_json_object(source_paths["contract"], "primary-profit contract")
    if (
        contract.get("schema_version")
        != "dc20_primary_profit_research_contract_v1"
        or contract.get("contract_id") != PRIMARY_PROFIT_CONTRACT_ID
        or contract.get("status") != "PUBLIC_CORE_RESEARCH_ALLOWED_NOT_FORMAL"
        or contract.get("boundaries") != PRIMARY_PROFIT_BOUNDARIES
        or contract.get("inputs", {}).get("action_input_allowed") is not False
        or contract.get("outputs", {}).get("forward_selection_output_allowed")
        is not False
        or contract.get("outputs", {}).get("forward_statistics_output_allowed")
        is not False
        or contract.get("outputs", {}).get("action_output_allowed") is not False
    ):
        raise DecisionPagesTruthError("primary-profit contract grants forbidden authority")
    receipt = _load_json_object(source_paths["primary_receipt"], "P0 receipt")
    if (
        receipt.get("schema_version") != "dc20_primary_d_receipt_v1"
        or receipt.get("signal_date") != date_value
        or receipt.get("exec_date") != mixed.get("exec_date")
        or receipt.get("exit_date") != mixed.get("exit_date")
        or receipt.get("generation_mode") != mixed.get("generation_mode")
        or receipt.get("action_authorized") is not False
        or receipt.get("action_input_consumed") is not False
        or receipt.get("formal_trade_count") != 0
    ):
        raise DecisionPagesTruthError("primary-profit P0 receipt authority drifted")
    three_binding = bindings.get("three_rank")
    if not isinstance(three_binding, dict):
        raise DecisionPagesTruthError("primary-profit three-rank binding is invalid")
    three_json_expected = f"outputs/decision/three_rank_top10_{date_value}.json"
    three_csv_expected = f"outputs/decision/three_rank_top10_{date_value}.csv"
    three_json_path = _primary_profit_bound_path(
        site_root,
        three_binding.get("json_path"),
        three_json_expected,
        "primary-profit three-rank JSON",
    )
    three_csv_path = _primary_profit_bound_path(
        site_root,
        three_binding.get("csv_path"),
        three_csv_expected,
        "primary-profit three-rank CSV",
    )
    if (
        three_binding.get("json_sha256")
        != _primary_profit_file_sha256(three_json_path, "primary-profit three-rank JSON")
        or three_binding.get("csv_sha256")
        != _primary_profit_file_sha256(three_csv_path, "primary-profit three-rank CSV")
    ):
        raise DecisionPagesTruthError("primary-profit three-rank SHA drifted")
    three_rank = _load_json_object(three_json_path, "primary-profit three-rank JSON")
    validated_three = _validate_three_rank_contract_payload(
        {
            "signal_date": date_value,
            "exec_date": mixed["exec_date"],
            "exit_date": mixed["exit_date"],
            "three_rank": three_rank,
        },
        label="primary-profit source",
    )
    if (
        validated_three is None
        or validated_three.get("bundle_sha256") != mixed.get("source_bundle_sha256")
        or validated_three.get("feature_snapshot_sha256")
        != mixed.get("source_feature_snapshot_sha256")
        or validated_three.get("top10_members_sha256")
        != mixed.get("top10_members_sha256")
    ):
        raise DecisionPagesTruthError("primary-profit three-rank fingerprint drifted")
    outputs = receipt.get("outputs")
    runtime_binding = bindings.get("runtime_features")
    if not isinstance(outputs, dict) or not isinstance(runtime_binding, dict):
        raise DecisionPagesTruthError("primary-profit P0 output bindings are invalid")
    if any(
        (
            outputs.get(receipt_field) != expected
            or source_binding.get(binding_field) != expected
        )
        for receipt_field, source_binding, binding_field, expected in (
            ("runtime_features_path", runtime_binding, "path", expected_sources["runtime_features"]),
            ("runtime_features_sha256", runtime_binding, "sha256", _primary_profit_file_sha256(source_paths["runtime_features"], "P0 runtime")),
            ("json_path", three_binding, "json_path", three_json_expected),
            ("json_sha256", three_binding, "json_sha256", _primary_profit_file_sha256(three_json_path, "P0 three-rank JSON")),
            ("csv_path", three_binding, "csv_path", three_csv_expected),
            ("csv_sha256", three_binding, "csv_sha256", _primary_profit_file_sha256(three_csv_path, "P0 three-rank CSV")),
        )
    ):
        raise DecisionPagesTruthError("primary-profit receipt/source file binding drifted")
    try:
        runtime_rows = list(
            csv.DictReader(
                source_paths["runtime_features"]
                .read_text(encoding="utf-8-sig")
                .splitlines()
            )
        )
    except (UnicodeError, csv.Error) as exc:
        raise DecisionPagesTruthError("primary-profit P0 runtime CSV is invalid") from exc
    selected_rows = [row for row in runtime_rows if row.get("top10_selected") == "1"]
    try:
        selected_rows.sort(key=lambda row: int(row["promotion_rank"]))
        identity_rows = [
            {
                "identity": row["identity"],
                "ts_code": row["ts_code"],
                "stage_transition": row["stage_transition"],
                "top10_selected": int(row["top10_selected"]),
                "promotion_rank": int(row["promotion_rank"]),
            }
            for row in sorted(runtime_rows, key=lambda row: row["ts_code"])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionPagesTruthError("primary-profit P0 runtime identity is invalid") from exc
    selected_codes = [row.get("ts_code", "") for row in selected_rows]
    three_codes = [row.get("ts_code", "") for row in validated_three["rows"]]
    expected_identity = hashlib.sha256(
        _canonical_json_bytes(
            {
                "schema": "dc20_primary_d_runtime_identity_v1",
                "signal_date": date_value,
                "rows": identity_rows,
            }
        )
    ).hexdigest()
    if (
        len(runtime_rows) != runtime_binding.get("row_count")
        or len(selected_rows) != runtime_binding.get("selected_count")
        or len(selected_rows) != mixed.get("candidate_count")
        or selected_codes != three_codes
        or runtime_binding.get("identity_sha256") != expected_identity
        or outputs.get("runtime_identity_sha256") != expected_identity
        or outputs.get("runtime_feature_row_count") != len(runtime_rows)
        or outputs.get("runtime_selected_count") != len(selected_rows)
        or runtime_binding.get("feature_snapshot_sha256")
        != mixed.get("source_feature_snapshot_sha256")
    ):
        raise DecisionPagesTruthError("primary-profit P0 runtime/TopN identity drifted")
    return PrimaryProfitResearchTruth(
        signal_date=date_value,
        exec_date=str(mixed["exec_date"]),
        exit_date=str(mixed["exit_date"]),
        generation_mode=str(mixed["generation_mode"]),
        candidate_count=int(mixed["candidate_count"]),
        top10_members_sha256=str(mixed["top10_members_sha256"]),
    )


def _project_three_rank_index(site_root: Path, decision_root: Path) -> None:
    json_dates: set[str] = set()
    csv_dates: set[str] = set()
    contracts: dict[str, tuple[dict[str, Any], str]] = {}
    for path in decision_root.iterdir():
        json_match = DATED_THREE_RANK_JSON_RE.fullmatch(path.name)
        csv_match = DATED_THREE_RANK_CSV_RE.fullmatch(path.name)
        if json_match:
            signal_date, _ = _strict_date(
                json_match.group(1), "three-rank JSON filename date"
            )
            json_dates.add(signal_date)
            contract = _load_json_object(
                path, f"dated three-rank contract {signal_date}"
            )
            if contract.get("signal_date") != signal_date:
                raise DecisionPagesTruthError(
                    "dated three-rank contract filename is date-inconsistent"
                )
            wrapper = {
                "signal_date": contract.get("signal_date"),
                "exec_date": contract.get("exec_date"),
                "exit_date": contract.get("exit_date"),
                "three_rank": contract,
            }
            _validate_three_rank_downloads(
                payload=wrapper,
                site_root=site_root,
                label=f"dated three-rank contract {signal_date}",
            )
            contracts[signal_date] = (
                contract,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        elif csv_match:
            signal_date, _ = _strict_date(
                csv_match.group(1), "three-rank CSV filename date"
            )
            csv_dates.add(signal_date)
    index_path = _site_child(
        site_root,
        ("outputs", "decision", "three_rank_index.json"),
        "three-rank index",
    )
    if json_dates != csv_dates:
        raise DecisionPagesTruthError(
            "dated three-rank JSON/CSV inventory mismatch"
        )
    if not contracts:
        if index_path.exists():
            raise DecisionPagesTruthError(
                "three-rank index exists without a dated contract"
            )
        return
    latest, latest_contract_sha256 = contracts[max(contracts)]
    if index_path.exists() and (
        not index_path.is_file() or index_path.is_symlink()
    ):
        raise DecisionPagesTruthError("three-rank index is not a regular file")
    index_path.write_text(
        json.dumps(
            _three_rank_index_payload(
                latest,
                contract_sha256=latest_contract_sha256,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    validate_three_rank_index_truth(index_path=index_path, site_root=site_root)


def _load_evaluation(path: Path) -> dict[str, Any]:
    return _load_json_object(path, "evaluation")


def _validate_action_payload(
    payload: dict[str, Any],
    *,
    report_date: str,
    label: str,
) -> None:
    schema = payload.get("schema_version")
    if type(schema) is not str or not schema.startswith("decision_action_plan_v"):
        raise DecisionPagesTruthError(f"{label}.schema_version is invalid")
    bound_report_date, report_day = _strict_date(
        payload.get("report_date"), f"{label}.report_date"
    )
    signal_date, signal_day = _strict_date(
        payload.get("signal_date"), f"{label}.signal_date"
    )
    exec_date, exec_day = _strict_date(
        payload.get("exec_date"), f"{label}.exec_date"
    )
    _exit_date, exit_day = _strict_date(
        payload.get("exit_date"), f"{label}.exit_date"
    )
    if bound_report_date != report_date:
        raise DecisionPagesTruthError(
            f"{label} contains a different report_date"
        )
    if exec_date != bound_report_date or report_day != exec_day:
        raise DecisionPagesTruthError(f"{label} exec_date must equal report_date")
    if not signal_day < exec_day < exit_day:
        raise DecisionPagesTruthError(f"{label} date order must be D < T < T+1")
    if payload.get("broker_connected") is not False:
        raise DecisionPagesTruthError(f"{label} cannot connect a broker")
    # Older reviewed V12 plans predate this explicit marker.  Absence means
    # no execution claim; an affirmative claim is always rejected.
    if payload.get("execution_or_fill_claimed", False) is not False:
        raise DecisionPagesTruthError(f"{label} cannot claim execution or fill")
    _validate_three_rank_contract_payload(payload, label=label)


def _validate_research_context_payload(
    payload: dict[str, Any],
    *,
    report_date: str,
    label: str,
    site_root: Path,
    independent_dc20_path: bool = False,
) -> None:
    """Validate a public research artifact without importing model code."""

    if payload.get("schema_version") == "decision_research_context_v1_historical_parity":
        if independent_dc20_path:
            raise DecisionPagesTruthError(
                f"{label} cannot place historical parity in the DC20 path"
            )
        if payload.get("artifact_kind") != "historical_parity_research_context":
            raise DecisionPagesTruthError(f"{label}.artifact_kind is invalid")
        if payload.get("historical_parity") is not True or payload.get("research_only") is not True:
            raise DecisionPagesTruthError(f"{label} must be historical research-only")
        if payload.get("action_authorized") is not False:
            raise DecisionPagesTruthError(f"{label} cannot authorize action")
        if payload.get("runtime_network_dependency") is not False:
            raise DecisionPagesTruthError(f"{label} cannot have a runtime dependency")
        binding = payload.get("source_binding")
        if not isinstance(binding, dict) or binding.get("scope") != "vendored_immutable_legacy_snapshot":
            raise DecisionPagesTruthError(f"{label}.source_binding is invalid")
        repository = binding.get("repository")
        if type(repository) is not str or REPOSITORY_RE.fullmatch(repository) is None:
            raise DecisionPagesTruthError(f"{label} vendored repository is invalid")
        commit_sha = binding.get("commit_sha")
        if type(commit_sha) is not str or GIT_OBJECT_RE.fullmatch(commit_sha) is None:
            raise DecisionPagesTruthError(f"{label} vendored Git identity is invalid")
        if binding.get("import_mode") != "one_time_vendored_snapshot":
            raise DecisionPagesTruthError(f"{label} vendored import mode is invalid")
        if binding.get("runtime_network_dependency") is not False:
            raise DecisionPagesTruthError(f"{label} vendored snapshot has a runtime dependency")

        payloads = payload.get("payloads_base64")
        artifact_bindings = binding.get("artifacts")
        artifact_keys = {"action_plan", "decision_report", "evaluation"}
        if payloads is None and artifact_bindings is None:
            payloads = {"action_plan": payload.get("payload_base64")}
            artifact_bindings = {
                "action_plan": {
                    "path": binding.get("path"),
                    "blob_sha": binding.get("blob_sha"),
                    "raw_sha256": binding.get("raw_sha256"),
                }
            }
        elif (
            not isinstance(payloads, dict)
            or set(payloads) != artifact_keys
            or not isinstance(artifact_bindings, dict)
            or set(artifact_bindings) != artifact_keys
        ):
            raise DecisionPagesTruthError(f"{label} vendored artifact set is invalid")

        decoded: dict[str, bytes] = {}
        for artifact, encoded in payloads.items():
            artifact_binding = artifact_bindings.get(artifact)
            if not isinstance(artifact_binding, dict):
                raise DecisionPagesTruthError(f"{label} {artifact} binding is invalid")
            source_path = artifact_binding.get("path")
            if (
                type(source_path) is not str
                or source_path.startswith("/")
                or ".." in Path(source_path).parts
                or "://" in source_path
            ):
                raise DecisionPagesTruthError(f"{label} {artifact} source path is invalid")
            blob_sha = artifact_binding.get("blob_sha")
            raw_sha256 = artifact_binding.get("raw_sha256")
            if type(blob_sha) is not str or GIT_OBJECT_RE.fullmatch(blob_sha) is None:
                raise DecisionPagesTruthError(f"{label} {artifact} blob SHA is invalid")
            if type(raw_sha256) is not str or SHA256_RE.fullmatch(raw_sha256) is None:
                raise DecisionPagesTruthError(f"{label} {artifact} raw digest is invalid")
            if type(encoded) is not str or not encoded:
                raise DecisionPagesTruthError(f"{label} {artifact} payload is missing")
            try:
                raw = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeError, ValueError) as exc:
                raise DecisionPagesTruthError(f"{label} {artifact} payload is invalid") from exc
            if hashlib.sha256(raw).hexdigest() != raw_sha256:
                raise DecisionPagesTruthError(f"{label} {artifact} raw SHA256 does not match payload")
            git_header = f"blob {len(raw)}\0".encode("ascii")
            if hashlib.sha1(git_header + raw).hexdigest() != blob_sha:
                raise DecisionPagesTruthError(f"{label} {artifact} Git blob SHA does not match payload")
            decoded[artifact] = raw

        raw = decoded["action_plan"]
        try:
            legacy = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except DecisionPagesTruthError:
            raise
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DecisionPagesTruthError(f"{label} payload is not strict UTF-8 JSON") from exc
        if not isinstance(legacy, dict):
            raise DecisionPagesTruthError(f"{label} payload must be one JSON object")
        bound_report_date, report_day = _strict_date(
            legacy.get("report_date"), f"{label}.payload.report_date"
        )
        signal_date, signal_day = _strict_date(
            legacy.get("signal_date"), f"{label}.payload.signal_date"
        )
        exec_date, exec_day = _strict_date(
            legacy.get("exec_date"), f"{label}.payload.exec_date"
        )
        _exit_date, exit_day = _strict_date(
            legacy.get("exit_date"), f"{label}.payload.exit_date"
        )
        if bound_report_date != report_date or exec_date != report_date:
            raise DecisionPagesTruthError(f"{label} payload date does not match its filename")
        if not signal_day < report_day == exec_day < exit_day:
            raise DecisionPagesTruthError(f"{label} payload date order is invalid")
        for field, value in (
            ("report_date", bound_report_date),
            ("signal_date", signal_date),
            ("exec_date", exec_date),
            ("exit_date", _exit_date),
        ):
            if payload.get(field) != value:
                raise DecisionPagesTruthError(f"{label} wrapper {field} mismatch")

        if set(decoded) == artifact_keys:
            try:
                legacy_report = decoded["decision_report"].decode("utf-8-sig")
            except UnicodeError as exc:
                raise DecisionPagesTruthError(
                    f"{label} decision_report is not UTF-8 text"
                ) from exc
            report_lines = legacy_report.splitlines()
            if report_lines[:1] != [f"# Decision Report ({report_date})"]:
                raise DecisionPagesTruthError(
                    f"{label} decision_report heading is date-inconsistent"
                )
            for field, value in (
                ("signal_date", signal_date),
                ("exec_date", exec_date),
                ("exit_date", _exit_date),
            ):
                if report_lines.count(f"- {field}: **{value}**") != 1:
                    raise DecisionPagesTruthError(
                        f"{label} decision_report {field} binding is invalid"
                    )
            try:
                legacy_evaluation = json.loads(
                    decoded["evaluation"].decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_json_keys,
                )
            except DecisionPagesTruthError:
                raise
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise DecisionPagesTruthError(
                    f"{label} evaluation is not strict UTF-8 JSON"
                ) from exc
            if not isinstance(legacy_evaluation, dict):
                raise DecisionPagesTruthError(f"{label} evaluation must be one JSON object")
            for field, value in (
                ("signal_date", signal_date),
                ("exec_date", exec_date),
                ("exit_date", _exit_date),
            ):
                if legacy_evaluation.get(field) != value:
                    raise DecisionPagesTruthError(
                        f"{label} evaluation {field} binding is invalid"
                    )
        return

    if payload.get("schema_version") != "decision_research_context_v1_daily":
        raise DecisionPagesTruthError(f"{label}.schema_version is invalid")
    if payload.get("artifact_kind") != "daily_research_context":
        raise DecisionPagesTruthError(f"{label}.artifact_kind is invalid")
    for field in ("research_only", "daily_research_only"):
        if payload.get(field) is not True:
            raise DecisionPagesTruthError(f"{label}.{field} must be true")
    if payload.get("action_authorized") is not False:
        raise DecisionPagesTruthError(f"{label} cannot authorize action")
    if payload.get("formal_buy_count") != 0:
        raise DecisionPagesTruthError(f"{label}.formal_buy_count must be zero")
    if payload.get("broker_connected") is not False:
        raise DecisionPagesTruthError(f"{label} cannot connect a broker")
    if payload.get("execution_or_fill_claimed") is not False:
        raise DecisionPagesTruthError(f"{label} cannot claim execution or fill")

    bound_report_date, report_day = _strict_date(
        payload.get("report_date"), f"{label}.report_date"
    )
    signal_date, signal_day = _strict_date(
        payload.get("signal_date"), f"{label}.signal_date"
    )
    exec_date, exec_day = _strict_date(
        payload.get("exec_date"), f"{label}.exec_date"
    )
    _exit_date, exit_day = _strict_date(
        payload.get("exit_date"), f"{label}.exit_date"
    )
    if bound_report_date != report_date or exec_date != report_date:
        raise DecisionPagesTruthError(f"{label} date does not match its filename")
    if not signal_day < report_day == exec_day < exit_day:
        raise DecisionPagesTruthError(f"{label} date order is invalid")

    if independent_dc20_path:
        if (
            signal_date < INDEPENDENCE_CUTOVER_SIGNAL_DATE
            or payload.get("independent_dc20_context") is not True
            or payload.get("independence_cutover_signal_date")
            != INDEPENDENCE_CUTOVER_SIGNAL_DATE
            or payload.get("active_evidence_scope")
            != "dc20_owned_dated_three_rank_bundle_only"
            or payload.get("historical_parity") is not False
        ):
            raise DecisionPagesTruthError(
                f"{label} independent DC20 cutover binding is invalid"
            )
    elif payload.get("independent_dc20_context") is True:
        raise DecisionPagesTruthError(
            f"{label} independent DC20 context is in the legacy path"
        )

    model = payload.get("model")
    if not isinstance(model, dict) or (
        model.get("prediction_matches_report") is not True
        and not independent_dc20_path
    ):
        raise DecisionPagesTruthError(f"{label}.model is not date-bound")
    if model.get("action_authorized") is not False:
        raise DecisionPagesTruthError(f"{label}.model cannot authorize action")

    binding = payload.get("source_binding")
    if not isinstance(binding, dict):
        raise DecisionPagesTruthError(f"{label}.source_binding is invalid")
    scope = binding.get("scope")
    expected_scope = (
        "same_repository_dc20_three_rank_artifacts_only"
        if independent_dc20_path
        else "same_repository_local_artifacts_only"
    )
    if scope != expected_scope:
        raise DecisionPagesTruthError(f"{label}.source_binding is invalid")
    files = binding.get("files")
    if not isinstance(files, dict) or not files:
        raise DecisionPagesTruthError(f"{label}.source_binding is invalid")
    for relative, digest in files.items():
        if (
            type(relative) is not str
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or "://" in relative
        ):
            raise DecisionPagesTruthError(f"{label} source path is not repository-local")
        if type(digest) is not str or SHA256_RE.fullmatch(digest) is None:
            raise DecisionPagesTruthError(f"{label} source digest is invalid")

    for collection in ("candidates", "stage_watchlist"):
        rows = payload.get(collection)
        if not isinstance(rows, list):
            raise DecisionPagesTruthError(f"{label}.{collection} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise DecisionPagesTruthError(f"{label}.{collection} row must be an object")
            if row.get("action") != "WATCH":
                raise DecisionPagesTruthError(f"{label}.{collection} contains a non-WATCH action")
            if row.get("target_weight") not in (0, 0.0):
                raise DecisionPagesTruthError(f"{label}.{collection} contains nonzero weight")
            if row.get("trade_selected") not in (0, False):
                raise DecisionPagesTruthError(f"{label}.{collection} contains selected action")
            if row.get("market_order_allowed") is not False:
                raise DecisionPagesTruthError(f"{label}.{collection} permits a market order")
    three_rank = _validate_three_rank_contract_payload(payload, label=label)
    if independent_dc20_path and (
        not isinstance(three_rank, dict)
        or three_rank.get("models", {})
        .get("promotion", {})
        .get("status")
        != "READY"
        or not three_rank.get("rows")
        or not isinstance(three_rank.get("downloads"), dict)
    ):
        raise DecisionPagesTruthError(
            f"{label} lacks a complete engine-A three-rank download contract"
        )
    if independent_dc20_path:
        assert isinstance(three_rank, dict)
        downloads = three_rank["downloads"]
        expected_sources = {
            downloads["json_url"],
            downloads["csv_url"],
        }
        if (
            set(files) != expected_sources
            or files.get(downloads["csv_url"]) != downloads["csv_sha256"]
        ):
            raise DecisionPagesTruthError(
                f"{label} source inventory is not the exact dated bundle"
            )
        for relative, claimed_digest in files.items():
            source_path = _site_child(
                site_root,
                tuple(relative.split("/")),
                f"{label} source bundle file",
            )
            if (
                source_path.is_symlink()
                or not source_path.is_file()
                or source_path.stat().st_size <= 0
                or hashlib.sha256(source_path.read_bytes()).hexdigest()
                != claimed_digest
            ):
                raise DecisionPagesTruthError(
                    f"{label} source bundle SHA256 drifted"
                )


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
    research_paths: dict[str, Path] = {}
    dc20_research_paths: dict[str, Path] = {}
    for path in decision_root.iterdir():
        for pattern, inventory, label in (
            (DATED_REPORT_RE, report_paths, "dated report"),
            (DATED_EVALUATION_RE, evaluation_paths, "dated evaluation"),
            (DATED_ACTION_RE, action_paths, "dated action"),
            (DATED_RESEARCH_RE, research_paths, "dated research context"),
            (
                DATED_DC20_RESEARCH_RE,
                dc20_research_paths,
                "dated independent DC20 research context",
            ),
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
    orphan_research = sorted(set(research_paths).difference(report_paths))
    if orphan_research:
        raise DecisionPagesTruthError(
            f"dated research contexts have no matching report: {orphan_research!r}"
        )
    orphan_dc20_research = sorted(
        set(dc20_research_paths).difference(report_paths)
    )
    if orphan_dc20_research:
        raise DecisionPagesTruthError(
            "dated independent DC20 research contexts have no matching report: "
            f"{orphan_dc20_research!r}"
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
            _validate_action_payload(
                action,
                report_date=report_date,
                label=f"dated action for {report_date}",
            )
            _validate_three_rank_downloads(
                payload=action,
                site_root=site_root,
                label=f"dated action for {report_date}",
            )
            row["action_url"] = f"outputs/decision/action_plan_{report_date}.json"
            action_dates.append(report_date)
        historical_archive = None
        if report_date in research_paths:
            historical_archive = _load_json_object(
                research_paths[report_date],
                f"dated research context for {report_date}",
            )
            _validate_research_context_payload(
                historical_archive,
                report_date=report_date,
                label=f"dated research context for {report_date}",
                site_root=site_root,
            )
            _validate_three_rank_downloads(
                payload=historical_archive,
                site_root=site_root,
                label=f"dated research context for {report_date}",
            )
        if report_date in dc20_research_paths:
            research = _load_json_object(
                dc20_research_paths[report_date],
                f"dated independent DC20 research context for {report_date}",
            )
            _validate_research_context_payload(
                research,
                report_date=report_date,
                label=(
                    f"dated independent DC20 research context for {report_date}"
                ),
                site_root=site_root,
                independent_dc20_path=True,
            )
            _validate_three_rank_downloads(
                payload=research,
                site_root=site_root,
                label=(
                    f"dated independent DC20 research context for {report_date}"
                ),
            )
            row["research_url"] = (
                f"outputs/decision/research_context_dc20_{report_date}.json"
            )
            row["research_available"] = True
            row["research_kind"] = "dc20_independent"
            if historical_archive is not None:
                if historical_archive.get("schema_version") != (
                    "decision_research_context_v1_historical_parity"
                ):
                    raise DecisionPagesTruthError(
                        "only historical parity may coexist as a DC20 archive"
                    )
                row["research_archive_available"] = True
                row["research_archive_url"] = (
                    f"outputs/decision/research_context_{report_date}.json"
                )
        elif historical_archive is not None:
            row["research_url"] = (
                f"outputs/decision/research_context_{report_date}.json"
            )
            row["research_available"] = True
            row["research_kind"] = (
                "historical_archive"
                if historical_archive.get("schema_version")
                == "decision_research_context_v1_historical_parity"
                else "legacy_daily"
            )
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
    _project_three_rank_index(site_root, decision_root)
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
    research_dates: list[str] = []
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
            _validate_action_payload(
                action_payload,
                report_date=report_date,
                label=f"dated action for {report_date}",
            )
            _validate_three_rank_downloads(
                payload=action_payload,
                site_root=site_root,
                label=f"dated action for {report_date}",
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

        research_available = report.get("research_available", False)
        if type(research_available) is not bool:
            raise DecisionPagesTruthError(
                f"report_index.reports[{row_number}].research_available must be a bool"
            )
        legacy_research_path = _site_child(
            site_root,
            ("outputs", "decision", f"research_context_{report_date}.json"),
            f"dated research context for {report_date}",
        )
        dc20_research_path = _site_child(
            site_root,
            (
                "outputs",
                "decision",
                f"research_context_dc20_{report_date}.json",
            ),
            f"dated independent DC20 research context for {report_date}",
        )
        legacy_research: dict[str, Any] | None = None
        dc20_research: dict[str, Any] | None = None
        if legacy_research_path.exists():
            legacy_research = _load_json_object(
                legacy_research_path,
                f"dated research context for {report_date}",
            )
            _validate_research_context_payload(
                legacy_research,
                report_date=report_date,
                label=f"dated research context for {report_date}",
                site_root=site_root,
            )
            _validate_three_rank_downloads(
                payload=legacy_research,
                site_root=site_root,
                label=f"dated research context for {report_date}",
            )
        if dc20_research_path.exists():
            dc20_research = _load_json_object(
                dc20_research_path,
                f"dated independent DC20 research context for {report_date}",
            )
            _validate_research_context_payload(
                dc20_research,
                report_date=report_date,
                label=(
                    f"dated independent DC20 research context for {report_date}"
                ),
                site_root=site_root,
                independent_dc20_path=True,
            )
            _validate_three_rank_downloads(
                payload=dc20_research,
                site_root=site_root,
                label=(
                    f"dated independent DC20 research context for {report_date}"
                ),
            )
        if dc20_research is not None:
            expected_research_url = (
                f"outputs/decision/research_context_dc20_{report_date}.json"
            )
            expected_research_kind = "dc20_independent"
            if legacy_research is not None and legacy_research.get(
                "schema_version"
            ) != "decision_research_context_v1_historical_parity":
                raise DecisionPagesTruthError(
                    "only historical parity may coexist as a DC20 archive"
                )
        elif legacy_research is not None:
            expected_research_url = (
                f"outputs/decision/research_context_{report_date}.json"
            )
            expected_research_kind = (
                "historical_archive"
                if legacy_research.get("schema_version")
                == "decision_research_context_v1_historical_parity"
                else "legacy_daily"
            )
        else:
            expected_research_url = ""
            expected_research_kind = ""
        valid_dated_research = bool(expected_research_url)
        if research_available:
            if report.get("research_url") != expected_research_url:
                raise DecisionPagesTruthError(
                    f"report_index.reports[{row_number}].research_url is not the preferred exact path"
                )
            if report.get("research_kind") != expected_research_kind:
                raise DecisionPagesTruthError(
                    f"report_index.reports[{row_number}].research_kind is invalid"
                )
            if not valid_dated_research:
                raise DecisionPagesTruthError(
                    f"advertised dated research context is invalid for {report_date}"
                )
            has_archive = dc20_research is not None and legacy_research is not None
            if has_archive:
                if (
                    report.get("research_archive_available") is not True
                    or report.get("research_archive_url")
                    != f"outputs/decision/research_context_{report_date}.json"
                ):
                    raise DecisionPagesTruthError(
                        "historical parity archive is not explicitly separated"
                    )
            elif "research_archive_available" in report or "research_archive_url" in report:
                raise DecisionPagesTruthError(
                    "report_index advertises a nonexistent research archive"
                )
            research_dates.append(report_date)
        else:
            if any(
                field in report
                for field in (
                    "research_url",
                    "research_kind",
                    "research_archive_available",
                    "research_archive_url",
                )
            ):
                raise DecisionPagesTruthError(
                    f"unavailable research context for {report_date} must not have research_url"
                )
            if valid_dated_research:
                raise DecisionPagesTruthError(
                    f"valid dated research context for {report_date} is hidden by research_available=false"
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

    three_rank_index_path = _site_child(
        site_root,
        ("outputs", "decision", "three_rank_index.json"),
        "three-rank index",
    )
    dated_three_rank_exists = any(
        DATED_THREE_RANK_JSON_RE.fullmatch(path.name)
        for path in _decision_root.iterdir()
    )
    if three_rank_index_path.exists():
        validate_three_rank_index_truth(
            index_path=three_rank_index_path,
            site_root=site_root,
        )
    elif dated_three_rank_exists:
        raise DecisionPagesTruthError(
            "dated three-rank contract is hidden without three_rank_index.json"
        )

    return DecisionActionIndexTruth(
        report_dates=tuple(report_dates),
        action_dates=tuple(action_dates),
        research_dates=tuple(research_dates),
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
    "INDEPENDENCE_CUTOVER_SIGNAL_DATE",
    "assess_decision_pages_truth",
    "project_report_index_action_truth",
    "validate_report_index_action_truth",
    "validate_three_rank_index_truth",
]
