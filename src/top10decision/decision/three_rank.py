from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .d_close_features import (
    D_CLOSE_FEATURE_COLUMNS,
    D_CLOSE_MAX_HISTORY_BARS,
    compute_d_close_features,
    empty_d_close_feature_values,
)
from .three_engine_models import (
    CORE_HEADS,
    PROMOTION_SOURCE_FEATURES,
    RUNTIME_ALIGNED_D_FEATURES,
    RUNTIME_ALIGNED_MARKET_FEATURES,
    RUNTIME_ALIGNED_POOL_FEATURES,
    THREE_ENGINE_VALIDATION_GATE_NAMES,
    ThreeEngineArtifactError,
    load_three_engine_artifacts,
    score_three_engine_snapshot,
)

THREE_RANK_CONTRACT_VERSION = "decision_three_rank_v1"
THREE_RANK_ARTIFACT_SCHEMA = "decision_three_rank_top10_v1"
THREE_RANK_ARTIFACT_KIND = "d_close_independent_three_rank_top10"
THREE_RANK_INDEX_SCHEMA = "decision_three_rank_index_v1"
THREE_RANK_INDEX_KIND = "dated_three_rank_pointer_only"
THREE_RANK_TOP_N = 10
SHADOW_TOP2_SLOTS = 2

HEADS = ("promotion", "big_loss", "profit")
HEAD_FIELDS = {
    "promotion": ("promotion_rank", "predicted_promotion_probability"),
    "big_loss": ("big_loss_safety_rank", "predicted_big_loss_probability"),
    "profit": ("profit_rank", "predicted_profit_probability"),
}
HEAD_LABELS = {
    "promotion": "晋级",
    "big_loss": "大跌安全",
    "profit": "盈利",
}
VALIDATION_GATE_FIELDS = (
    "validation_gate_pass_count",
    "validation_gate_total_count",
    "validation_gate_score_pct",
)
DATE_RE = re.compile(r"^20\d{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ThreeRankContractError(ValueError):
    """Raised when a three-rank artifact can no longer prove its frozen set."""


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _date(value: Any) -> str:
    text = _text(value)
    return text if DATE_RE.fullmatch(text) else ""


def _uniform_text(rows: Iterable[Mapping[str, Any]], field: str) -> str:
    values = {_text(row.get(field)) for row in rows if _text(row.get(field))}
    return next(iter(values)) if len(values) == 1 else ""


def _gate_value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip().lower() in {
        "",
        "nan",
        "none",
        "null",
    }:
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def _uniform_gate_value(
    rows: Iterable[Mapping[str, Any]],
    field: str,
) -> Any:
    values: list[Any] = []
    missing = 0
    total = 0
    for row in rows:
        total += 1
        raw = row.get(field)
        if not _gate_value_is_present(raw):
            missing += 1
            continue
        values.append(raw)
    if not values:
        return None
    if missing and missing != total:
        raise ThreeRankContractError(
            f"three-rank {field} is only present on part of the pool"
        )
    numbers = [_number(value) for value in values]
    if any(number is None for number in numbers) or len(set(numbers)) != 1:
        raise ThreeRankContractError(
            f"three-rank {field} is malformed or inconsistent"
        )
    return numbers[0]


def _validation_gate_summary(
    pass_value: Any,
    total_value: Any,
    score_value: Any,
    *,
    context: str,
) -> dict[str, int | float | None]:
    values = (pass_value, total_value, score_value)
    present = tuple(_gate_value_is_present(value) for value in values)
    if not any(present):
        return {
            "validation_gate_pass_count": None,
            "validation_gate_total_count": None,
            "validation_gate_score_pct": None,
        }
    if not all(present) or any(type(value) is bool for value in values):
        raise ThreeRankContractError(
            f"three-rank {context} validation gate summary is incomplete"
        )
    pass_count = _integer(pass_value)
    total_count = _integer(total_value)
    score = _number(score_value)
    if (
        pass_count is None
        or total_count is None
        or total_count <= 0
        or pass_count < 0
        or pass_count > total_count
        or score is None
    ):
        raise ThreeRankContractError(
            f"three-rank {context} validation gate summary is invalid"
        )
    expected_score = round(100.0 * pass_count / total_count, 1)
    if not math.isclose(score, expected_score, abs_tol=1e-9):
        raise ThreeRankContractError(
            f"three-rank {context} validation gate score is inconsistent"
        )
    return {
        "validation_gate_pass_count": pass_count,
        "validation_gate_total_count": total_count,
        "validation_gate_score_pct": expected_score,
    }


def _gate_summary_from_rows(
    rows: list[dict[str, Any]],
    prefix: str,
    *,
    explicit: Mapping[str, Any] | None = None,
) -> dict[str, int | float | None]:
    explicit = explicit or {}
    values: list[Any] = []
    for field in VALIDATION_GATE_FIELDS:
        claimed = explicit.get(field)
        values.append(
            claimed
            if _gate_value_is_present(claimed)
            else _uniform_gate_value(rows, f"{prefix}_{field}")
        )
    return _validation_gate_summary(
        *values,
        context=prefix,
    )


def _validate_gate_summary_meta(
    meta: Mapping[str, Any],
    *,
    context: str,
) -> None:
    if any(field not in meta for field in VALIDATION_GATE_FIELDS):
        raise ThreeRankContractError(
            f"three-rank {context} validation gate metadata is missing"
        )
    pass_count = meta["validation_gate_pass_count"]
    total_count = meta["validation_gate_total_count"]
    score = meta["validation_gate_score_pct"]
    if not (pass_count is None and total_count is None and score is None) and (
        type(pass_count) is not int
        or type(total_count) is not int
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
    ):
        raise ThreeRankContractError(
            f"three-rank {context} validation gate types are invalid"
        )
    _validation_gate_summary(
        pass_count,
        total_count,
        score,
        context=context,
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def top10_members_sha256(signal_date: str, codes: Iterable[str]) -> str:
    """Hash set identity, not a presentation order.

    The two downstream heads and the shadow selector must bind to this exact
    value.  Sorting here makes a UI sort incapable of changing membership.
    """

    normalized = sorted({_text(code) for code in codes if _text(code)})
    payload = {
        "schema": "dc20_three_rank_member_set_v1",
        "signal_date": _date(signal_date),
        "members": normalized,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _normalize_status(value: Any) -> str:
    status = _text(value).upper()
    if status == "READY" or status.startswith("NOT_READY_"):
        return status
    return "NOT_READY_MISSING_STATUS"


def _head_meta(
    name: str,
    rows: list[dict[str, Any]],
    model: Mapping[str, Any],
    signal_date: str,
) -> dict[str, Any]:
    explicit_heads = model.get("three_rank_models")
    explicit = (
        explicit_heads.get(name)
        if isinstance(explicit_heads, Mapping)
        and isinstance(explicit_heads.get(name), Mapping)
        else {}
    )
    prefix = name

    def value(field: str) -> str:
        explicit_value = _text(explicit.get(field))
        if explicit_value:
            return explicit_value
        return _uniform_text(rows, f"{prefix}_model_{field}")

    status = _normalize_status(value("status"))
    version = value("version")
    as_of_date = _date(value("as_of_date"))
    artifact_sha256 = value("artifact_sha256")
    if status == "READY" and (
        not version
        or not as_of_date
        or as_of_date >= signal_date
        or SHA256_RE.fullmatch(artifact_sha256) is None
    ):
        status = "NOT_READY_MISSING_PROVENANCE"
    return {
        "label": HEAD_LABELS[name],
        "status": status,
        "ranking_ready": status == "READY",
        "probability_ready": status == "READY",
        "version": version,
        "model_as_of_date": as_of_date,
        "artifact_sha256": artifact_sha256,
        "rank_field": HEAD_FIELDS[name][0],
        "probability_field": HEAD_FIELDS[name][1],
        **_gate_summary_from_rows(rows, prefix, explicit=explicit),
    }


def _shadow_meta(rows: list[dict[str, Any]], signal_date: str) -> dict[str, Any]:
    status = _uniform_text(rows, "p_fill_shadow_status").upper()
    if not (
        status.startswith("SHADOW_") or status.startswith("NOT_READY_")
    ):
        status = "SHADOW_NOT_READY_MISSING_STATUS"
    version = _uniform_text(rows, "p_fill_shadow_model_version")
    as_of_date = _date(
        _uniform_text(rows, "p_fill_shadow_model_as_of_date")
    )
    artifact_sha256 = _uniform_text(
        rows, "p_fill_shadow_model_artifact_sha256"
    )
    if status == "SHADOW_READY" and (
        not version
        or not as_of_date
        or as_of_date >= signal_date
        or SHA256_RE.fullmatch(artifact_sha256) is None
    ):
        status = "SHADOW_NOT_READY_MISSING_PROVENANCE"
    return {
        "model_status": status,
        "model_version": version,
        "model_as_of_date": as_of_date,
        "model_artifact_sha256": artifact_sha256,
        **_gate_summary_from_rows(rows, "p_fill_shadow"),
    }


def _source_rows(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    stage = plan.get("stage_watchlist")
    candidates = plan.get("candidates")
    # Candidates retain the complete engine-A pool.  stage_watchlist is only a
    # presentation projection and may already be limited to ten rows, so it
    # must never hide an over-selected or under-selected engine output.
    source = candidates if isinstance(candidates, list) and candidates else stage
    return [dict(row) for row in source or [] if isinstance(row, Mapping)]


def _selected_source_rows(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _source_rows(plan)
    has_explicit_membership = any("top10_selected" in row for row in rows)
    if has_explicit_membership:
        rows = [row for row in rows if _integer(row.get("top10_selected")) == 1]
    return rows


def _probability(row: Mapping[str, Any], head: str) -> float | None:
    field = HEAD_FIELDS[head][1]
    # Once a row declares the new contract, only the six official engine
    # outputs may populate it.  Legacy observation/trade fields remain useful
    # for old historical plans, but must never backfill a missing READY output
    # and thereby let a shadow model impersonate an official head.
    if _text(row.get("three_rank_contract_version")):
        aliases = (field,)
    else:
        aliases = {
            "promotion": (field, "promotion_probability"),
            "big_loss": (
                field,
                "big_loss_probability",
                "trade_predicted_big_loss_probability",
            ),
            "profit": (field, "profit_probability"),
        }[head]
    for alias in aliases:
        number = _number(row.get(alias))
        if number is not None:
            return number
    return None


def _rank(row: Mapping[str, Any], head: str) -> int | None:
    field = HEAD_FIELDS[head][0]
    aliases = (
        (field,)
        if _text(row.get("three_rank_contract_version")) or head != "big_loss"
        else (field, "big_loss_rank")
    )
    for alias in aliases:
        value = _integer(row.get(alias))
        if value is not None:
            return value
    return None


def _head_output_is_valid(
    rows: list[dict[str, Any]],
    head: str,
) -> bool:
    count = len(rows)
    ranks = [_rank(row, head) for row in rows]
    probabilities = [_probability(row, head) for row in rows]
    return (
        sorted(rank for rank in ranks if rank is not None)
        == list(range(1, count + 1))
        and len([rank for rank in ranks if rank is not None]) == count
        and all(
            probability is not None and 0.0 <= probability <= 1.0
            for probability in probabilities
        )
    )


def _shadow_output_is_valid(rows: list[dict[str, Any]]) -> bool:
    ranks = [_integer(row.get("p_fill_shadow_rank")) for row in rows]
    probabilities = [
        _number(row.get("p_fill_shadow_probability")) for row in rows
    ]
    statuses = [
        _text(row.get("p_fill_shadow_status")).upper() for row in rows
    ]
    return (
        sorted(rank for rank in ranks if rank is not None)
        == list(range(1, len(rows) + 1))
        and len([rank for rank in ranks if rank is not None]) == len(rows)
        and all(
            probability is not None and 0.0 <= probability <= 1.0
            for probability in probabilities
        )
        and all(status == "SHADOW_READY" for status in statuses)
    )


def _shadow_top2_projection(
    rows: list[dict[str, Any]],
    *,
    model_status: str,
) -> dict[str, Any]:
    selected = (
        sorted(
            (
                {
                    "ts_code": _text(row.get("ts_code")),
                    "name": _text(row.get("name")),
                    "p_fill_shadow_rank": _integer(
                        row.get("p_fill_shadow_rank")
                    ),
                    "p_fill_shadow_probability": _number(
                        row.get("p_fill_shadow_probability")
                    ),
                }
                for row in rows
                if (_integer(row.get("p_fill_shadow_rank")) or 0)
                <= SHADOW_TOP2_SLOTS
                and _integer(row.get("p_fill_shadow_rank")) is not None
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
        "requested_slots": SHADOW_TOP2_SLOTS,
        "actual_slots": len(selected),
        "may_change_core_bundle": False,
        "may_override_core_ranks": False,
        "may_create_trade_action": False,
        "rows": selected,
    }


def _shadow_snapshot_sha256(
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    members_sha256: str,
    shadow: Mapping[str, Any],
    rows: list[dict[str, Any]],
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
                    "ts_code": _text(row.get("ts_code")),
                    "p_fill_shadow_rank": _integer(
                        row.get("p_fill_shadow_rank")
                    ),
                    "p_fill_shadow_probability": _number(
                        row.get("p_fill_shadow_probability")
                    ),
                    "p_fill_shadow_status": _text(
                        row.get("p_fill_shadow_status")
                    ).upper(),
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
                    "ts_code": _text(row.get("ts_code")),
                    "p_fill_shadow_rank": _integer(
                        row.get("p_fill_shadow_rank")
                    ),
                }
                for row in top2_rows
                if isinstance(row, Mapping)
            ]
            if isinstance(top2_rows, list)
            else top2_rows,
        },
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _core_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    core_row_fields = (
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
            {field: row.get(field) for field in core_row_fields}
            for row in rows
            if isinstance(row, Mapping)
        ]
        if isinstance(rows, list)
        else rows,
    }


def build_three_rank_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Project one plan into a frozen, non-composite three-rank contract.

    This function never manufactures a rank from a probability.  A head may
    publish ranks only when the engine itself emitted READY provenance and a
    complete 1..N permutation.  Constant fallbacks therefore remain visible
    as NOT_READY_* instead of becoming a deterministic but meaningless rank.
    """

    signal_date = _date(plan.get("signal_date"))
    exec_date = _date(plan.get("exec_date"))
    exit_date = _date(plan.get("exit_date"))
    model = plan.get("model") if isinstance(plan.get("model"), Mapping) else {}
    all_source_rows = _source_rows(plan)
    source_rows = _selected_source_rows(plan)
    source_rows.sort(
        key=lambda row: (
            _rank(row, "promotion") or 999999,
            _text(row.get("ts_code")),
        )
    )
    models = {
        # Read readiness/provenance from the complete current-D pool.  When A
        # correctly selects no members because it is NOT_READY, filtering to
        # selected rows first would erase the reason/version shown to users.
        head: _head_meta(head, all_source_rows, model, signal_date)
        for head in HEADS
    }

    pool_values = {
        _integer(row.get("promotion_pool_size"))
        for row in all_source_rows
        if _integer(row.get("promotion_pool_size")) is not None
    }
    scoped_pool_size = sum(
        _text(row.get("stage_transition") or row.get("stage"))
        in {"2→3", "3→4"}
        for row in all_source_rows
    )
    promotion_pool_size = (
        next(iter(pool_values))
        if len(pool_values) == 1
        else scoped_pool_size
    )
    snapshots = {
        _text(row.get("feature_snapshot_sha256"))
        for row in all_source_rows
        if _text(row.get("feature_snapshot_sha256"))
    }
    top_level_snapshot = _text(plan.get("feature_snapshot_sha256"))
    if top_level_snapshot:
        snapshots.add(top_level_snapshot)
    feature_snapshot_sha256 = (
        next(iter(snapshots)) if len(snapshots) == 1 else ""
    )

    # Promotion is the sole membership authority.  Without a trustworthy A
    # head there is no official Top10 for B/C to rank.
    if not signal_date or not exec_date or not exit_date or not (
        signal_date < exec_date < exit_date
    ):
        models["promotion"]["status"] = "NOT_READY_DATE_BINDING"
        models["promotion"]["ranking_ready"] = False
        models["promotion"]["probability_ready"] = False
    if models["promotion"]["status"] == "READY" and (
        len(pool_values) > 1
        or promotion_pool_size < 0
        or len(source_rows) != min(THREE_RANK_TOP_N, promotion_pool_size)
        or len(source_rows) > THREE_RANK_TOP_N
    ):
        models["promotion"]["status"] = "NOT_READY_INVALID_MEMBERSHIP"
        models["promotion"]["ranking_ready"] = False
        models["promotion"]["probability_ready"] = False
    if (
        models["promotion"]["status"] == "READY"
        and source_rows
        and SHA256_RE.fullmatch(feature_snapshot_sha256) is None
    ):
        models["promotion"]["status"] = "NOT_READY_MISSING_FEATURE_SNAPSHOT"
        models["promotion"]["ranking_ready"] = False
        models["promotion"]["probability_ready"] = False
    if models["promotion"]["status"] == "READY" and not _head_output_is_valid(
        source_rows, "promotion"
    ):
        models["promotion"]["status"] = "NOT_READY_INVALID_OUTPUT"
        models["promotion"]["ranking_ready"] = False
        models["promotion"]["probability_ready"] = False

    official_rows = source_rows if models["promotion"]["status"] == "READY" else []
    members_sha256 = top10_members_sha256(
        signal_date,
        (_text(row.get("ts_code")) for row in official_rows),
    )
    claims = {
        _text(row.get("top10_members_sha256"))
        for row in official_rows
        if _text(row.get("top10_members_sha256"))
    }
    top_level_claim = _text(plan.get("top10_members_sha256"))
    if top_level_claim:
        claims.add(top_level_claim)
    if claims and claims != {members_sha256}:
        models["promotion"]["status"] = "NOT_READY_SET_HASH_MISMATCH"
        models["promotion"]["ranking_ready"] = False
        models["promotion"]["probability_ready"] = False
        official_rows = []
        members_sha256 = top10_members_sha256(signal_date, [])

    for head in ("big_loss", "profit"):
        if models["promotion"]["status"] != "READY":
            # B/C are conditional engines over A's exact frozen Top10.  A
            # missing or invalid membership/date/hash binding means that input
            # set does not exist, regardless of whether a standalone B/C
            # artifact happens to be loadable.
            models[head]["status"] = "NOT_READY_NO_FROZEN_TOP10"
            models[head]["ranking_ready"] = False
            models[head]["probability_ready"] = False
        elif models[head]["status"] == "READY" and not _head_output_is_valid(
            official_rows, head
        ):
            models[head]["status"] = "NOT_READY_INVALID_OUTPUT"
            models[head]["ranking_ready"] = False
            models[head]["probability_ready"] = False

    shadow_meta = _shadow_meta(all_source_rows, signal_date)
    if models["promotion"]["status"] != "READY":
        shadow_meta["model_status"] = (
            "SHADOW_NOT_READY_NO_FROZEN_TOP10"
        )
    elif (
        shadow_meta["model_status"] == "SHADOW_READY"
        and not _shadow_output_is_valid(official_rows)
    ):
        shadow_meta["model_status"] = "SHADOW_NOT_READY_INVALID_OUTPUT"
    shadow_ready = shadow_meta["model_status"] == "SHADOW_READY"

    rows: list[dict[str, Any]] = []
    for source in official_rows:
        row = {
            "ts_code": _text(source.get("ts_code")),
            "name": _text(source.get("name")),
            "industry": _text(source.get("industry")) or "未分类",
            "stage_transition": _text(
                source.get("stage_transition") or source.get("stage")
            ),
            "top10_selected": 1,
            "promotion_rank": _rank(source, "promotion"),
            "predicted_promotion_probability": _probability(
                source, "promotion"
            ),
            "big_loss_safety_rank": (
                _rank(source, "big_loss")
                if models["big_loss"]["status"] == "READY"
                else None
            ),
            "predicted_big_loss_probability": (
                _probability(source, "big_loss")
                if models["big_loss"]["status"] == "READY"
                else None
            ),
            "profit_rank": (
                _rank(source, "profit")
                if models["profit"]["status"] == "READY"
                else None
            ),
            "predicted_profit_probability": (
                _probability(source, "profit")
                if models["profit"]["status"] == "READY"
                else None
            ),
            # Shadow output is namespaced and cannot replace a core field.
            "p_fill_shadow_rank": (
                _integer(source.get("p_fill_shadow_rank"))
                if shadow_ready
                else None
            ),
            "p_fill_shadow_probability": (
                _number(source.get("p_fill_shadow_probability"))
                if shadow_ready
                else None
            ),
            "p_fill_shadow_status": shadow_meta["model_status"],
        }
        rows.append(row)

    for head in HEADS:
        models[head]["input_members_sha256"] = members_sha256

    ready_heads = sum(models[head]["status"] == "READY" for head in HEADS)
    status = (
        "READY"
        if ready_heads == 3
        else "PARTIAL_MODELS_NOT_READY"
        if models["promotion"]["status"] == "READY"
        else "NOT_READY_PROMOTION"
    )
    shadow_contract = {
        "status": "ANNOTATION_ONLY",
        "input_members_sha256": members_sha256,
        "may_change_membership": False,
        "may_override_core_ranks": False,
        **shadow_meta,
    }
    shadow_top2 = _shadow_top2_projection(
        rows,
        model_status=shadow_meta["model_status"],
    )
    shadow_contract["shadow_snapshot_sha256"] = _shadow_snapshot_sha256(
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        members_sha256=members_sha256,
        shadow=shadow_contract,
        rows=rows,
        shadow_top2=shadow_top2,
    )
    contract: dict[str, Any] = {
        "schema_version": THREE_RANK_ARTIFACT_SCHEMA,
        "artifact_kind": THREE_RANK_ARTIFACT_KIND,
        "contract_version": THREE_RANK_CONTRACT_VERSION,
        "status": status,
        "generated_at_utc": _text(plan.get("generated_at_utc")),
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "feature_as_of_date": signal_date,
        "feature_snapshot_sha256": feature_snapshot_sha256,
        "membership_authority": "promotion_probability_engine_only",
        "downstream_scope": "exact_frozen_promotion_top10",
        "promotion_pool_size": int(promotion_pool_size or 0),
        "top10_count": len(rows),
        "top10_members_sha256": members_sha256,
        "models": models,
        "rows": rows,
        "shadow_contract": shadow_contract,
        "shadow_top2": shadow_top2,
    }
    contract["bundle_sha256"] = hashlib.sha256(
        _canonical_json_bytes(_core_projection(contract))
    ).hexdigest()
    validate_three_rank_contract(contract)
    return contract


def validate_three_rank_contract(
    contract: Mapping[str, Any],
    *,
    require_all_models_ready: bool = False,
) -> None:
    if not isinstance(contract, Mapping):
        raise ThreeRankContractError("three-rank contract must be an object")
    if contract.get("schema_version") != THREE_RANK_ARTIFACT_SCHEMA:
        raise ThreeRankContractError("three-rank schema_version is invalid")
    if contract.get("artifact_kind") != THREE_RANK_ARTIFACT_KIND:
        raise ThreeRankContractError("three-rank artifact_kind is invalid")
    if contract.get("contract_version") != THREE_RANK_CONTRACT_VERSION:
        raise ThreeRankContractError("three-rank contract_version is invalid")
    signal_date = _date(contract.get("signal_date"))
    exec_date = _date(contract.get("exec_date"))
    exit_date = _date(contract.get("exit_date"))
    if not signal_date or not exec_date or not exit_date or not (
        signal_date < exec_date < exit_date
    ):
        raise ThreeRankContractError("three-rank date binding is invalid")
    rows = contract.get("rows")
    if not isinstance(rows, list) or len(rows) > THREE_RANK_TOP_N:
        raise ThreeRankContractError("three-rank rows are invalid")
    if contract.get("feature_as_of_date") != signal_date:
        raise ThreeRankContractError("three-rank feature date escaped D close")
    pool_size = contract.get("promotion_pool_size")
    if type(pool_size) is not int or pool_size < 0 or pool_size < len(rows):
        raise ThreeRankContractError("three-rank promotion pool size is invalid")
    codes = [_text(row.get("ts_code")) for row in rows if isinstance(row, Mapping)]
    if len(codes) != len(rows) or any(not code for code in codes):
        raise ThreeRankContractError("three-rank row code is invalid")
    if len(set(codes)) != len(codes):
        raise ThreeRankContractError("three-rank rows contain duplicate codes")
    if any(row.get("top10_selected") != 1 for row in rows):
        raise ThreeRankContractError("three-rank row is not in the frozen Top10")
    if any(
        _text(row.get("stage_transition")) not in {"2→3", "3→4"}
        for row in rows
    ):
        raise ThreeRankContractError("three-rank row escaped the 2-to-3/3-to-4 scope")
    expected_members = top10_members_sha256(signal_date, codes)
    if contract.get("top10_members_sha256") != expected_members:
        raise ThreeRankContractError("three-rank frozen member hash is invalid")
    if contract.get("top10_count") != len(rows):
        raise ThreeRankContractError("three-rank row count is invalid")
    models = contract.get("models")
    if not isinstance(models, Mapping) or set(models) != set(HEADS):
        raise ThreeRankContractError("three-rank model inventory is invalid")
    for head in HEADS:
        meta = models[head]
        if not isinstance(meta, Mapping):
            raise ThreeRankContractError(f"three-rank {head} model is invalid")
        raw_status = meta.get("status")
        if type(raw_status) is not str or not (
            raw_status == "READY" or raw_status.startswith("NOT_READY_")
        ):
            raise ThreeRankContractError(
                f"three-rank {head} model status is invalid"
            )
        status = raw_status
        ready = status == "READY"
        if meta.get("ranking_ready") is not ready or meta.get(
            "probability_ready"
        ) is not ready:
            raise ThreeRankContractError(
                f"three-rank {head} readiness flags disagree with status"
            )
        if meta.get("rank_field") != HEAD_FIELDS[head][0] or meta.get(
            "probability_field"
        ) != HEAD_FIELDS[head][1]:
            raise ThreeRankContractError(
                f"three-rank {head} field declaration is invalid"
            )
        if meta.get("input_members_sha256") != expected_members:
            raise ThreeRankContractError(
                f"three-rank {head} model is not bound to the frozen set"
            )
        _validate_gate_summary_meta(meta, context=head)
        ranks = [_integer(row.get(HEAD_FIELDS[head][0])) for row in rows]
        probabilities = [
            _number(row.get(HEAD_FIELDS[head][1])) for row in rows
        ]
        if status == "READY":
            as_of_date = _date(meta.get("model_as_of_date"))
            if (
                not _text(meta.get("version"))
                or not as_of_date
                or as_of_date >= signal_date
                or SHA256_RE.fullmatch(_text(meta.get("artifact_sha256")))
                is None
            ):
                raise ThreeRankContractError(
                    f"three-rank {head} READY provenance is invalid"
                )
            if sorted(rank for rank in ranks if rank is not None) != list(
                range(1, len(rows) + 1)
            ) or len([rank for rank in ranks if rank is not None]) != len(rows):
                raise ThreeRankContractError(
                    f"three-rank {head} ranks are not a 1..N permutation"
                )
            if any(
                probability is None or not 0.0 <= probability <= 1.0
                for probability in probabilities
            ):
                raise ThreeRankContractError(
                    f"three-rank {head} probabilities are invalid"
                )
        elif any(rank is not None or probability is not None for rank, probability in zip(ranks, probabilities)):
            raise ThreeRankContractError(
                f"three-rank {head} emitted a fake rank while not ready"
            )
    promotion_ready = models["promotion"].get("status") == "READY"
    if not promotion_ready and any(
        models[head].get("status") != "NOT_READY_NO_FROZEN_TOP10"
        for head in ("big_loss", "profit")
    ):
        raise ThreeRankContractError(
            "three-rank downstream model is READY without a frozen Top10"
        )
    if not promotion_ready and rows:
        raise ThreeRankContractError(
            "three-rank official Top10 exists without a ready promotion model"
        )
    if promotion_ready and len(rows) != min(THREE_RANK_TOP_N, pool_size):
        raise ThreeRankContractError(
            "three-rank official Top10 count disagrees with the promotion pool"
        )
    feature_snapshot_sha256 = _text(
        contract.get("feature_snapshot_sha256")
    )
    if promotion_ready and rows and SHA256_RE.fullmatch(
        feature_snapshot_sha256
    ) is None:
        raise ThreeRankContractError(
            "three-rank READY output lacks a frozen feature snapshot"
        )
    ready_heads = sum(models[head].get("status") == "READY" for head in HEADS)
    expected_status = (
        "READY"
        if ready_heads == 3
        else "PARTIAL_MODELS_NOT_READY"
        if promotion_ready
        else "NOT_READY_PROMOTION"
    )
    if contract.get("status") != expected_status:
        raise ThreeRankContractError("three-rank aggregate status is invalid")
    if require_all_models_ready and any(
        models[head].get("status") != "READY" for head in HEADS
    ):
        raise ThreeRankContractError("three-rank production models are not all ready")
    shadow = contract.get("shadow_contract")
    if (
        not isinstance(shadow, Mapping)
        or shadow.get("status") != "ANNOTATION_ONLY"
        or shadow.get("input_members_sha256") != expected_members
        or shadow.get("may_change_membership") is not False
        or shadow.get("may_override_core_ranks") is not False
    ):
        raise ThreeRankContractError("three-rank shadow contract is invalid")
    shadow_model_status = shadow.get("model_status")
    if type(shadow_model_status) is not str or not (
        shadow_model_status.startswith("SHADOW_")
        or shadow_model_status.startswith("NOT_READY_")
    ):
        raise ThreeRankContractError(
            "three-rank shadow model status is invalid"
        )
    if shadow_model_status == "SHADOW_READY":
        shadow_as_of_date = _date(shadow.get("model_as_of_date"))
        if (
            not _text(shadow.get("model_version"))
            or not shadow_as_of_date
            or shadow_as_of_date >= signal_date
            or SHA256_RE.fullmatch(
                _text(shadow.get("model_artifact_sha256"))
            )
            is None
        ):
            raise ThreeRankContractError(
                "three-rank SHADOW_READY provenance is invalid"
            )
    shadow_ranks = [
        _integer(row.get("p_fill_shadow_rank")) for row in rows
    ]
    shadow_probabilities = [
        _number(row.get("p_fill_shadow_probability")) for row in rows
    ]
    shadow_statuses = [
        _text(row.get("p_fill_shadow_status")).upper() for row in rows
    ]
    if any(status != shadow_model_status for status in shadow_statuses):
        raise ThreeRankContractError(
            "three-rank shadow row statuses disagree with the model"
        )
    if shadow_model_status == "SHADOW_READY":
        if sorted(rank for rank in shadow_ranks if rank is not None) != list(
            range(1, len(rows) + 1)
        ) or len([rank for rank in shadow_ranks if rank is not None]) != len(
            rows
        ):
            raise ThreeRankContractError(
                "three-rank shadow ranks are not a 1..N permutation"
            )
        if any(
            probability is None or not 0.0 <= probability <= 1.0
            for probability in shadow_probabilities
        ):
            raise ThreeRankContractError(
                "three-rank shadow probabilities are invalid"
            )
    elif any(
        rank is not None or probability is not None
        for rank, probability in zip(shadow_ranks, shadow_probabilities)
    ):
        raise ThreeRankContractError(
            "three-rank shadow emitted output while not ready"
        )
    _validate_gate_summary_meta(shadow, context="p_fill_shadow")
    expected_shadow_top2 = _shadow_top2_projection(
        rows,
        model_status=shadow_model_status,
    )
    if contract.get("shadow_top2") != expected_shadow_top2:
        raise ThreeRankContractError(
            "three-rank shadow Top2 contract is invalid"
        )
    expected_shadow_snapshot_sha256 = _shadow_snapshot_sha256(
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        members_sha256=expected_members,
        shadow=shadow,
        rows=rows,
        shadow_top2=expected_shadow_top2,
    )
    if (
        SHA256_RE.fullmatch(
            _text(shadow.get("shadow_snapshot_sha256"))
        )
        is None
        or shadow.get("shadow_snapshot_sha256")
        != expected_shadow_snapshot_sha256
    ):
        raise ThreeRankContractError(
            "three-rank shadow snapshot hash is invalid"
        )
    bundle_sha256 = contract.get("bundle_sha256")
    expected_bundle = hashlib.sha256(
        _canonical_json_bytes(_core_projection(contract))
    ).hexdigest()
    if bundle_sha256 != expected_bundle:
        raise ThreeRankContractError("three-rank bundle hash is invalid")
    downloads = contract.get("downloads")
    if downloads is not None:
        expected_prefix = (
            f"outputs/decision/three_rank_top10_{signal_date}"
        )
        if (
            not isinstance(downloads, Mapping)
            or downloads.get("json_url") != f"{expected_prefix}.json"
            or downloads.get("csv_url") != f"{expected_prefix}.csv"
            or SHA256_RE.fullmatch(_text(downloads.get("csv_sha256")))
            is None
            or downloads.get("row_count") != len(rows)
        ):
            raise ThreeRankContractError(
                "three-rank download binding is invalid"
            )


def _artifact_json_bytes(contract: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            contract,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _three_rank_index_payload(
    contract: Mapping[str, Any],
    *,
    contract_sha256: str,
) -> dict[str, Any]:
    validate_three_rank_contract(contract)
    downloads = contract.get("downloads")
    if not isinstance(downloads, Mapping):
        raise ThreeRankContractError(
            "three-rank index requires dated download bindings"
        )
    signal_date = _date(contract.get("signal_date"))
    return {
        "schema_version": THREE_RANK_INDEX_SCHEMA,
        "index_kind": THREE_RANK_INDEX_KIND,
        "data_alias": False,
        "latest_signal_date": signal_date,
        "latest_exec_date": _date(contract.get("exec_date")),
        "latest_exit_date": _date(contract.get("exit_date")),
        "latest_status": _text(contract.get("status")),
        "latest_contract_url": downloads["json_url"],
        "latest_csv_url": downloads["csv_url"],
        "latest_contract_sha256": contract_sha256,
        "latest_csv_sha256": downloads["csv_sha256"],
        "latest_bundle_sha256": contract["bundle_sha256"],
        "latest_top10_members_sha256": contract[
            "top10_members_sha256"
        ],
    }


def validate_three_rank_index(index: Mapping[str, Any]) -> None:
    if not isinstance(index, Mapping):
        raise ThreeRankContractError("three-rank index must be an object")
    if index.get("schema_version") != THREE_RANK_INDEX_SCHEMA:
        raise ThreeRankContractError("three-rank index schema is invalid")
    if index.get("index_kind") != THREE_RANK_INDEX_KIND:
        raise ThreeRankContractError("three-rank index kind is invalid")
    if index.get("data_alias") is not False:
        raise ThreeRankContractError("three-rank index cannot be a data alias")
    signal_date = _date(index.get("latest_signal_date"))
    exec_date = _date(index.get("latest_exec_date"))
    exit_date = _date(index.get("latest_exit_date"))
    if not signal_date or not exec_date or not exit_date or not (
        signal_date < exec_date < exit_date
    ):
        raise ThreeRankContractError("three-rank index dates are invalid")
    expected_prefix = f"outputs/decision/three_rank_top10_{signal_date}"
    if (
        index.get("latest_contract_url") != f"{expected_prefix}.json"
        or index.get("latest_csv_url") != f"{expected_prefix}.csv"
    ):
        raise ThreeRankContractError("three-rank index URL is not dated")
    for field in (
        "latest_contract_sha256",
        "latest_csv_sha256",
        "latest_bundle_sha256",
        "latest_top10_members_sha256",
    ):
        if SHA256_RE.fullmatch(_text(index.get(field))) is None:
            raise ThreeRankContractError(
                f"three-rank index {field} is invalid"
            )
    status = _text(index.get("latest_status"))
    if status not in {
        "READY",
        "PARTIAL_MODELS_NOT_READY",
        "NOT_READY_PROMOTION",
    }:
        raise ThreeRankContractError("three-rank index status is invalid")


def materialize_three_rank_index(
    output_root: Path,
    contract: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Point to the newest immutable dated bundle without copying its data."""

    output = output_root.resolve() / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    signal_date = _date(contract.get("signal_date"))
    contract_path = output / f"three_rank_top10_{signal_date}.json"
    if (
        not contract_path.is_file()
        or contract_path.is_symlink()
        or contract_path.stat().st_size <= 0
    ):
        raise ThreeRankContractError(
            "three-rank index target is missing, empty, or unsafe"
        )
    contract_sha256 = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    candidate = _three_rank_index_payload(
        contract,
        contract_sha256=contract_sha256,
    )
    path = output / "three_rank_index.json"
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise ThreeRankContractError("existing three-rank index is unsafe")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ThreeRankContractError(
                "existing three-rank index is unreadable"
            ) from exc
        validate_three_rank_index(existing)
        existing_date = str(existing["latest_signal_date"])
        candidate_date = str(candidate["latest_signal_date"])
        if existing_date > candidate_date:
            return path, dict(existing)
        if existing_date == candidate_date:
            if existing != candidate:
                raise ThreeRankContractError(
                    "dated three-rank index target cannot be rewritten"
                )
            return path, dict(existing)
    path.write_text(
        json.dumps(candidate, ensure_ascii=False, allow_nan=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path, candidate


def _csv_bytes(contract: Mapping[str, Any]) -> bytes:
    fields = [
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
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
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
    for head in HEADS:
        common[f"{head}_model_status"] = models[head]["status"]
        common[f"{head}_model_version"] = models[head]["version"]
        common[f"{head}_model_as_of_date"] = models[head][
            "model_as_of_date"
        ]
        common[f"{head}_model_artifact_sha256"] = models[head][
            "artifact_sha256"
        ]
        for field in VALIDATION_GATE_FIELDS:
            common[f"{head}_{field}"] = models[head][field]
    shadow = contract["shadow_contract"]
    common["p_fill_shadow_model_version"] = shadow["model_version"]
    common["p_fill_shadow_model_as_of_date"] = shadow["model_as_of_date"]
    common["p_fill_shadow_model_artifact_sha256"] = shadow[
        "model_artifact_sha256"
    ]
    common["p_fill_shadow_snapshot_sha256"] = shadow[
        "shadow_snapshot_sha256"
    ]
    for field in VALIDATION_GATE_FIELDS:
        common[f"p_fill_shadow_{field}"] = shadow[field]
    for row in contract["rows"]:
        writer.writerow({**common, **row})
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def materialize_three_rank_artifacts(
    output_root: Path,
    contract: Mapping[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    """Write exact dated JSON/CSV files, preserving an already frozen D set."""

    validate_three_rank_contract(contract)
    enriched = copy.deepcopy(dict(contract))
    signal_date = str(enriched["signal_date"])
    output = output_root.resolve() / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"three_rank_top10_{signal_date}.json"
    csv_path = output / f"three_rank_top10_{signal_date}.csv"
    csv_payload = _csv_bytes(enriched)
    enriched["downloads"] = {
        "json_url": f"outputs/decision/{json_path.name}",
        "csv_url": f"outputs/decision/{csv_path.name}",
        "csv_sha256": hashlib.sha256(csv_payload).hexdigest(),
        "row_count": len(enriched["rows"]),
    }
    json_payload = _artifact_json_bytes(enriched)

    if json_path.exists() or csv_path.exists():
        if not json_path.is_file() or json_path.is_symlink():
            raise ThreeRankContractError("existing three-rank JSON is unsafe")
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ThreeRankContractError(
                "existing three-rank JSON is unreadable"
            ) from exc
        validate_three_rank_contract(existing)
        if existing.get("bundle_sha256") != enriched.get("bundle_sha256"):
            raise ThreeRankContractError(
                "frozen D three-rank artifact cannot be overwritten"
            )
        if not csv_path.is_file() or csv_path.is_symlink():
            raise ThreeRankContractError("existing three-rank CSV is unsafe")
        expected_csv_sha = (existing.get("downloads") or {}).get("csv_sha256")
        if hashlib.sha256(csv_path.read_bytes()).hexdigest() != expected_csv_sha:
            raise ThreeRankContractError("existing three-rank CSV hash drifted")
        materialize_three_rank_index(output_root, existing)
        return json_path, csv_path, existing

    json_path.write_bytes(json_payload)
    csv_path.write_bytes(csv_payload)
    materialize_three_rank_index(output_root, enriched)
    return json_path, csv_path, enriched


PROMOTION_D_CONTEXT_FEATURES = tuple(PROMOTION_SOURCE_FEATURES[8:16])

# This projection lives outside the hash-bound Auction V3 canonical engine.
# It may enrich a newly generated D prediction, but it must never alter the
# frozen model fingerprint, selection policy, historical action plan, or
# official candidate membership.
THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS = (
    "promotion_pool_size",
    "three_rank_contract_version",
    "feature_snapshot_sha256",
    "top10_selected",
    "promotion_rank",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "big_loss_safety_rank",
    "big_loss_rank_score",
    "predicted_big_loss_probability",
    "profit_rank",
    "profit_rank_score",
    "predicted_profit_probability",
    "p_fill_shadow_probability",
    "p_fill_shadow_score",
    "p_fill_shadow_rank",
    "top10_members_sha256",
    "p_fill_shadow_status",
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
    "p_fill_shadow_model_version",
    "p_fill_shadow_model_as_of_date",
    "p_fill_shadow_model_artifact_sha256",
    "p_fill_shadow_validation_gate_pass_count",
    "p_fill_shadow_validation_gate_total_count",
    "p_fill_shadow_validation_gate_score_pct",
)
THREE_ENGINE_RUNTIME_AUDIT_COLUMNS = (
    "three_engine_runtime_status",
    "three_engine_runtime_feature_gate_passed",
    "three_engine_runtime_artifacts_hash_bound",
    "three_engine_runtime_input_pool_complete",
    "three_engine_runtime_failure",
)


def build_promotion_context_features(
    stage: Any,
    closes: Sequence[Any],
    limit_up_flags: Sequence[Any],
) -> dict[str, float]:
    """Compute the eight D-only promotion context fields from six sessions."""

    defaults = {
        feature: float("nan") for feature in PROMOTION_D_CONTEXT_FEATURES
    }
    close_items = list(closes)
    flag_items = list(limit_up_flags)
    try:
        stage_value = float(stage)
    except (TypeError, ValueError):
        return defaults
    if (
        not math.isfinite(stage_value)
        or stage_value not in (2.0, 3.0)
        or len(close_items) != 6
        or len(flag_items) != 6
    ):
        return defaults

    def numeric(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return result if math.isfinite(result) else float("nan")

    def limit_up(value: Any) -> bool:
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            return False
        try:
            return bool(value)
        except (TypeError, ValueError):
            return False

    close_values = np.asarray(
        [numeric(value) for value in close_items], dtype=float
    )
    states = [limit_up(value) for value in flag_items]
    stage_count = int(stage_value)
    pre_end = 5 - stage_count
    pre_closes = close_values[: pre_end + 1]
    valid_returns = np.asarray([], dtype=float)
    if len(pre_closes) >= 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            returns = pre_closes[1:] / pre_closes[:-1] - 1.0
        valid_returns = returns[np.isfinite(returns)]
    pre_1d = (
        pre_closes[-1] / pre_closes[-2] - 1.0
        if len(pre_closes) >= 2
        and np.isfinite(pre_closes[-2:]).all()
        and pre_closes[-2] > 0
        else float("nan")
    )
    anchor_index = max(0, pre_end - 3)
    pre_3d = (
        close_values[pre_end] / close_values[anchor_index] - 1.0
        if np.isfinite(close_values[[pre_end, anchor_index]]).all()
        and close_values[anchor_index] > 0
        else float("nan")
    )
    prior_positions = [
        index
        for index, is_limit_up in enumerate(states[: pre_end + 1])
        if is_limit_up
    ]
    return {
        "five_year_pre_streak_1d_return": pre_1d,
        "five_year_pre_streak_3d_return": pre_3d,
        "five_year_pre_streak_volatility": (
            float(np.std(valid_returns, ddof=0))
            if len(valid_returns)
            else float("nan")
        ),
        "five_year_pre_streak_limit_up_count": float(len(prior_positions)),
        "five_year_recent_limit_up_count": float(sum(states)),
        "five_year_days_since_prior_limit_up": (
            float(pre_end - prior_positions[-1] + 1)
            if prior_positions
            else 6.0
        ),
        "five_year_streak_runup": (
            close_values[-1] / close_values[pre_end] - 1.0
            if np.isfinite(close_values[[-1, pre_end]]).all()
            and close_values[pre_end] > 0
            else float("nan")
        ),
        "five_year_price_log": (
            float(np.log1p(close_values[-1]))
            if math.isfinite(close_values[-1]) and close_values[-1] > 0
            else float("nan")
        ),
    }

def _normal_date(value: Any) -> str:
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        missing = False
    digits = "".join(
        character for character in str("" if missing else value) if character.isdigit()
    )
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        missing = False
    text = str("" if missing else value).strip().upper()
    if "." in text:
        digits, suffix = text.split(".", 1)
        digits = "".join(character for character in digits if character.isdigit())[:6]
        if len(digits) == 6 and suffix in {"SH", "SZ"}:
            return f"{digits}.{suffix}"
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"

def _runtime_numeric(row: Any, names: Sequence[str]) -> float:
    if row is None:
        return float("nan")
    for name in names:
        try:
            value = row.get(name)
        except AttributeError:
            value = getattr(row, name, None)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return float("nan")


def _runtime_pre_close(row: Any) -> float:
    """Mirror the canonical Auction pre-close fallback outside its source pin."""

    if row is None:
        return float("nan")
    direct = _runtime_numeric(row, ("pre_close", "pre_close_est"))
    if direct > 0:
        return direct
    close = _runtime_numeric(row, ("close",))
    pct_chg = _runtime_numeric(row, ("pct_chg",))
    denominator = 1.0 + pct_chg / 100.0
    if close > 0 and math.isfinite(pct_chg) and denominator > 0:
        return close / denominator
    return float("nan")


def three_engine_d_close_market_features(
    engine: Any,
    signal_date: str,
    code: str,
    dates: Sequence[str],
) -> dict[str, Any]:
    """Reproduce the five-year ledger's stock-local D-close feature surface."""

    result: dict[str, Any] = empty_d_close_feature_values()
    target_date = str(signal_date or "").strip()
    if len(target_date) != 8 or not target_date.isdigit():
        return result
    history: list[dict[str, float | str]] = []
    eligible_dates = [
        str(date or "").strip()
        for date in dates
        if str(date or "").strip() <= target_date
        and len(str(date or "").strip()) == 8
        and str(date or "").strip().isdigit()
    ]
    seen_dates: set[str] = set()
    for trade_date in reversed(eligible_dates):
        if trade_date in seen_dates:
            continue
        seen_dates.add(trade_date)
        daily = engine._row(engine.market_table(trade_date, "daily"), code)
        if daily is None:
            continue
        close = _runtime_numeric(daily, ("close",))
        if not math.isfinite(close) or close <= 0:
            continue
        history.append(
            {
                "trade_date": trade_date,
                "open": _runtime_numeric(daily, ("open",)),
                "close": close,
                "high": _runtime_numeric(daily, ("high",)),
                "low": _runtime_numeric(daily, ("low",)),
                "volume": _runtime_numeric(daily, ("vol", "volume")),
            }
        )
        if len(history) >= D_CLOSE_MAX_HISTORY_BARS:
            break
    history.reverse()
    canonical = compute_d_close_features(
        pd.DataFrame(history),
        cutoff_date=target_date,
    )
    if canonical.empty or str(canonical.iloc[-1]["trade_date"]) != target_date:
        return result
    latest = canonical.iloc[-1]
    return {name: latest[name] for name in D_CLOSE_FEATURE_COLUMNS}


def augment_three_engine_runtime_base(
    engine: Any,
    signal_date: str,
    base: pd.DataFrame,
) -> pd.DataFrame:
    """Enrich only the separate three-engine inference surface."""

    if base.empty:
        return base.copy()
    output = base.copy()
    codes = output["ts_code"].astype(str).str.strip().str.upper()
    stages = pd.to_numeric(output["limit_times"], errors="coerce").round()
    eligible = stages.isin((2.0, 3.0)) & codes.str.endswith((".SH", ".SZ"))
    output = output.loc[eligible].copy()
    if output.empty:
        return output
    output["ts_code"] = codes.loc[output.index]
    output = engine._attach_cohort_features(output)
    stages = pd.to_numeric(output["limit_times"], errors="coerce").round()
    output["stage2_pool_size"] = float(stages.eq(2.0).sum())
    output["stage3_pool_size"] = float(stages.eq(3.0).sum())
    output["board"] = output["ts_code"].map(
        lambda code: "SH_MAIN" if str(code).endswith(".SH") else "SZ_MAIN"
    )
    dates = engine.market_dates()
    daily_table = engine.market_table(signal_date, "daily")
    basic_table = engine.market_table(signal_date, "daily_basic")
    for index, row in output.iterrows():
        code = str(row["ts_code"])
        daily = engine._row(daily_table, code)
        basic = engine._row(basic_table, code)
        d_close = _runtime_numeric(daily, ("close",))
        pre_close = _runtime_pre_close(daily)
        output.at[index, "d_open"] = _runtime_numeric(daily, ("open",))
        output.at[index, "d_close"] = d_close
        output.at[index, "d_high"] = _runtime_numeric(daily, ("high",))
        output.at[index, "d_low"] = _runtime_numeric(daily, ("low",))
        output.at[index, "d_volume"] = _runtime_numeric(
            daily, ("vol", "volume")
        )
        output.at[index, "d_amount"] = _runtime_numeric(daily, ("amount",))
        output.at[index, "d_pct_change"] = (
            100.0 * (d_close / pre_close - 1.0)
            if math.isfinite(d_close)
            and math.isfinite(pre_close)
            and pre_close > 0
            else _runtime_numeric(daily, ("pct_chg", "pct_change"))
        )
        output.at[index, "d_turnover_pct"] = _runtime_numeric(
            basic, ("turnover_rate", "turnover_rate_f")
        )
        for name, value in three_engine_d_close_market_features(
            engine,
            signal_date,
            code,
            dates,
        ).items():
            output.at[index, name] = value
    return output


def apply_three_engine_runtime(
    engine: Any,
    scored: pd.DataFrame,
    inference_pool: pd.DataFrame,
    signal_date: str,
) -> pd.DataFrame:
    """Overlay hash-bound relative ranks without changing official membership."""

    output = scored.copy()
    legacy_shadow_fields = {
        "promotion_rank": "legacy_shadow_promotion_rank",
        "promotion_rank_score": "legacy_shadow_promotion_rank_score",
        "predicted_promotion_probability": (
            "legacy_shadow_predicted_promotion_probability"
        ),
        "predicted_big_loss_probability": (
            "legacy_shadow_predicted_big_loss_probability"
        ),
        "predicted_profit_probability": (
            "legacy_shadow_predicted_profit_probability"
        ),
    }
    for source, shadow in legacy_shadow_fields.items():
        output[shadow] = (
            output[source]
            if source in output.columns
            else pd.Series(np.nan, index=output.index, dtype=float)
        )

    validation_path = (
        engine.config.root
        / "models"
        / "decision_three_engines"
        / "validation_latest.json"
    )

    def fail_closed(reason: str) -> pd.DataFrame:
        failed = output.copy()
        failed["promotion_pool_size"] = int(len(inference_pool))
        failed["three_rank_contract_version"] = THREE_RANK_CONTRACT_VERSION
        failed["feature_snapshot_sha256"] = ""
        failed["top10_selected"] = 0
        for column in (
            "promotion_rank",
            "big_loss_safety_rank",
            "profit_rank",
            "p_fill_shadow_rank",
        ):
            failed[column] = pd.Series(
                pd.NA, index=failed.index, dtype="Int64"
            )
        for column in (
            "promotion_rank_score",
            "predicted_promotion_probability",
            "big_loss_rank_score",
            "predicted_big_loss_probability",
            "profit_rank_score",
            "predicted_profit_probability",
            "p_fill_shadow_probability",
            "p_fill_shadow_score",
        ):
            failed[column] = np.nan
        failed["top10_members_sha256"] = top10_members_sha256(
            signal_date, []
        )
        failed["p_fill_shadow_status"] = (
            "SHADOW_NOT_READY_ARTIFACT_PROVENANCE"
        )
        failed["p_fill_shadow_model_version"] = ""
        failed["p_fill_shadow_model_as_of_date"] = ""
        failed["p_fill_shadow_model_artifact_sha256"] = ""
        for field in (
            "validation_gate_pass_count",
            "validation_gate_total_count",
            "validation_gate_score_pct",
        ):
            failed[f"p_fill_shadow_{field}"] = np.nan
        for head in CORE_HEADS:
            failed[f"{head}_model_status"] = reason
            failed[f"{head}_model_version"] = ""
            failed[f"{head}_model_as_of_date"] = ""
            failed[f"{head}_model_artifact_sha256"] = ""
            for field in (
                "validation_gate_pass_count",
                "validation_gate_total_count",
                "validation_gate_score_pct",
            ):
                failed[f"{head}_{field}"] = np.nan
        failed["three_engine_runtime_status"] = "NOT_READY_PROMOTION"
        failed["three_engine_runtime_feature_gate_passed"] = 0
        failed["three_engine_runtime_artifacts_hash_bound"] = 0
        failed["three_engine_runtime_input_pool_complete"] = 1
        failed["three_engine_runtime_failure"] = reason
        return failed

    try:
        loaded = load_three_engine_artifacts(
            validation_path,
            root=engine.config.root,
        )
        snapshot = score_three_engine_snapshot(
            inference_pool,
            loaded,
            signal_date=signal_date,
        )
        official = snapshot.rows.copy()
        if (
            official["ts_code"].duplicated().any()
            or output["ts_code"].duplicated().any()
            or set(official["ts_code"].astype(str))
            != set(output["ts_code"].astype(str))
            or len(official) != len(output)
        ):
            raise ValueError(
                "three-engine scorer changed the complete D candidate pool"
            )
        official = official.set_index("ts_code", drop=False)
        keys = output["ts_code"].astype(str)
        for column in THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS:
            if column not in official.columns:
                raise ValueError(
                    f"three-engine scorer omitted required output: {column}"
                )
            output[column] = keys.map(official[column])
        output["three_engine_runtime_status"] = snapshot.status
        output["three_engine_runtime_feature_gate_passed"] = int(
            snapshot.diagnostics.get("runtime_feature_gate_passed") is True
        )
        output["three_engine_runtime_artifacts_hash_bound"] = 1
        output["three_engine_runtime_input_pool_complete"] = 1
        output["three_engine_runtime_failure"] = ""
        return output
    except (ThreeEngineArtifactError, OSError, ValueError, KeyError, TypeError):
        reason = (
            "NOT_READY_ARTIFACT_PROVENANCE"
            if validation_path.exists()
            else "NOT_READY_ARTIFACT_MISSING"
        )
        return fail_closed(reason)


def _three_engine_projection_is_complete(
    frame: pd.DataFrame,
    signal_date: str,
) -> bool:
    required = {
        "signal_date",
        "ts_code",
        "stage",
        *THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS,
        *THREE_ENGINE_RUNTIME_AUDIT_COLUMNS,
    }
    if not required.issubset(frame.columns):
        return False
    if frame.empty:
        return True
    dates = frame["signal_date"].map(_normal_date)
    codes = frame["ts_code"].map(_normal_code)
    if (
        not dates.eq(_normal_date(signal_date)).all()
        or codes.eq("").any()
        or codes.duplicated().any()
    ):
        return False
    if not frame["three_rank_contract_version"].astype(str).eq(
        THREE_RANK_CONTRACT_VERSION
    ).all():
        return False

    pool_size = pd.to_numeric(frame["promotion_pool_size"], errors="coerce")
    if (
        pool_size.isna().any()
        or not np.isfinite(pool_size.to_numpy(dtype=float)).all()
        or not pool_size.eq(np.floor(pool_size)).all()
        or not pool_size.eq(len(frame)).all()
    ):
        return False
    stages = (
        frame["stage"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace("->", "→", regex=False)
    )
    if not stages.isin(("2→3", "3→4")).all():
        return False

    def binary(name: str) -> Optional[pd.Series]:
        values = pd.to_numeric(frame[name], errors="coerce")
        return (
            values
            if values.notna().all() and values.isin((0, 1)).all()
            else None
        )

    top10_selected = binary("top10_selected")
    feature_gate = binary("three_engine_runtime_feature_gate_passed")
    artifacts_bound = binary("three_engine_runtime_artifacts_hash_bound")
    input_complete = binary("three_engine_runtime_input_pool_complete")
    if (
        top10_selected is None
        or feature_gate is None
        or artifacts_bound is None
        or input_complete is None
        or not input_complete.eq(1).all()
    ):
        return False
    selected = top10_selected.eq(1)
    cap = min(THREE_RANK_TOP_N, len(frame))
    if int(selected.sum()) > cap:
        return False

    promotion_rank = pd.to_numeric(frame["promotion_rank"], errors="coerce")
    ranked = promotion_rank.notna()
    if ranked.any():
        ranked_values = promotion_rank.loc[ranked]
        if (
            not ranked.all()
            or not np.isfinite(ranked_values.to_numpy(dtype=float)).all()
            or not ranked_values.eq(np.floor(ranked_values)).all()
            or ranked_values.lt(1).any()
            or ranked_values.gt(len(frame)).any()
            or ranked_values.duplicated().any()
            or set(ranked_values.astype(int)) != set(range(1, len(frame) + 1))
        ):
            return False
        expected_selected = promotion_rank.le(cap)
        if not selected.equals(expected_selected):
            return False
    elif selected.any():
        return False

    member_hashes = (
        frame["top10_members_sha256"].fillna("").astype(str).str.strip()
    )
    expected_member_hash = top10_members_sha256(
        signal_date,
        frame.loc[selected, "ts_code"].astype(str),
    )
    if (
        member_hashes.nunique(dropna=False) != 1
        or not member_hashes.eq(expected_member_hash).all()
    ):
        return False
    statuses = (
        frame["three_engine_runtime_status"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    if statuses.eq("").any() or statuses.nunique(dropna=False) != 1:
        return False
    failures = (
        frame["three_engine_runtime_failure"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    snapshots = (
        frame["feature_snapshot_sha256"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    bound = artifacts_bound.eq(1)
    if (
        feature_gate.nunique(dropna=False) != 1
        or artifacts_bound.nunique(dropna=False) != 1
        or input_complete.nunique(dropna=False) != 1
        or failures.nunique(dropna=False) != 1
        or snapshots.nunique(dropna=False) != 1
        or (bound & failures.ne("")).any()
        or (bound & ~snapshots.str.fullmatch(r"[0-9a-f]{64}")).any()
        or (~bound & failures.eq("")).any()
    ):
        return False
    return True


def _restore_projection_bytes(path: Path, payload: Optional[bytes]) -> None:
    """Atomically restore one projection path after an overlay failure."""

    if payload is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restore_projection_pair(
    dated_path: Path,
    dated_payload: Optional[bytes],
    latest_path: Path,
    latest_payload: Optional[bytes],
) -> None:
    """Attempt both rollback legs even if one filesystem operation fails."""

    first_error: Optional[OSError] = None
    for path, payload in (
        (dated_path, dated_payload),
        (latest_path, latest_payload),
    ):
        try:
            _restore_projection_bytes(path, payload)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise RuntimeError(
            "failed to restore three-engine projection bytes"
        ) from first_error


class ThreeEngineRuntimeMixin:
    """Keep the independent three-engine projector outside canonical Auction."""

    def build_three_engine_inference_pool(
        self,
        signal_date: str,
        candidates: pd.DataFrame,
    ) -> pd.DataFrame:
        """Build the hard-range overlay pool after canonical scoring is sealed."""

        base = super()._current_base(signal_date, candidates)
        return augment_three_engine_runtime_base(self, signal_date, base)

    def build_prediction(
        self,
        signal_date: str,
        candidates: pd.DataFrame,
        bundle: Any,
        backtest_metrics: dict[str, Any],
        *,
        force: bool = False,
    ) -> pd.DataFrame:
        dated_path = self.config.prediction_root / f"pred_{signal_date}.csv"
        latest_path = self.config.prediction_root / "pred_latest.csv"
        before_dated = dated_path.read_bytes() if dated_path.is_file() else None
        before_latest = latest_path.read_bytes() if latest_path.is_file() else None
        expected_buy, _ = self._prediction_dates(signal_date, candidates)
        revision_allowed = self._prediction_revision_allowed(expected_buy)
        from top10decision.auction_v3.engine import _read_csv, _write_csv

        frozen = _read_csv(dated_path) if before_dated is not None else None
        frozen_complete = (
            frozen is not None
            and _three_engine_projection_is_complete(frozen, signal_date)
        )
        if not revision_allowed:
            if before_dated is None:
                raise RuntimeError(
                    "three-engine projection cannot create a historical D prediction"
                )
            if not frozen_complete:
                raise RuntimeError(
                    "three-engine historical D prediction is incomplete"
                )
            try:
                _write_csv(frozen, latest_path)
                return frozen
            except Exception:
                _restore_projection_bytes(latest_path, before_latest)
                raise
        try:
            scored = super().build_prediction(
                signal_date,
                candidates,
                bundle,
                backtest_metrics,
                force=force,
            )
            after_dated = dated_path.read_bytes() if dated_path.is_file() else None
            if (
                before_dated is not None
                and after_dated == before_dated
                and frozen_complete
            ):
                _write_csv(scored, latest_path)
                return scored

            inference_pool = self.build_three_engine_inference_pool(
                signal_date,
                candidates,
            )
            if inference_pool.empty:
                raise RuntimeError(
                    "three-engine hard-range inference pool is empty "
                    f"for signal_date={signal_date}"
                )
            official_codes = set(inference_pool["ts_code"].astype(str))
            scored = scored.loc[
                scored["ts_code"].astype(str).isin(official_codes)
            ].copy()
            if official_codes != set(scored["ts_code"].astype(str)):
                raise RuntimeError(
                    "three-engine hard-range pool is not a subset of canonical D prediction"
                )
            pool = inference_pool.set_index("ts_code", drop=False)
            keys = scored["ts_code"].astype(str)
            surface_columns = (
                "board",
                *RUNTIME_ALIGNED_D_FEATURES,
                *RUNTIME_ALIGNED_MARKET_FEATURES,
                *RUNTIME_ALIGNED_POOL_FEATURES,
                *PROMOTION_SOURCE_FEATURES,
            )
            for column in surface_columns:
                if column in pool.columns:
                    scored[column] = keys.map(pool[column])
            scored = apply_three_engine_runtime(
                self,
                scored,
                inference_pool,
                signal_date,
            )
            _write_csv(scored, dated_path)
            _write_csv(scored, latest_path)
            return scored
        except Exception:
            _restore_projection_pair(
                dated_path,
                before_dated,
                latest_path,
                before_latest,
            )
            raise

__all__ = [
    "HEADS",
    "PROMOTION_D_CONTEXT_FEATURES",
    "PROMOTION_SOURCE_FEATURES",
    "RUNTIME_ALIGNED_D_FEATURES",
    "RUNTIME_ALIGNED_MARKET_FEATURES",
    "RUNTIME_ALIGNED_POOL_FEATURES",
    "THREE_RANK_ARTIFACT_KIND",
    "THREE_RANK_ARTIFACT_SCHEMA",
    "THREE_RANK_CONTRACT_VERSION",
    "THREE_RANK_INDEX_KIND",
    "THREE_RANK_INDEX_SCHEMA",
    "THREE_RANK_TOP_N",
    "THREE_ENGINE_RUNTIME_OUTPUT_COLUMNS",
    "THREE_ENGINE_VALIDATION_GATE_NAMES",
    "ThreeEngineArtifactError",
    "ThreeEngineRuntimeMixin",
    "ThreeRankContractError",
    "apply_three_engine_runtime",
    "augment_three_engine_runtime_base",
    "build_promotion_context_features",
    "build_three_rank_contract",
    "load_three_engine_artifacts",
    "materialize_three_rank_artifacts",
    "materialize_three_rank_index",
    "top10_members_sha256",
    "three_engine_d_close_market_features",
    "validate_three_rank_contract",
    "validate_three_rank_index",
]
