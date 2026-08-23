#!/usr/bin/env python3
"""Atomically bind validated three-rank assets into the active V2 freeze.

Initial migration and autonomous publication may record either strictly
validated release mode: all three core heads READY, or promotion READY with
both secondary heads explicitly NOT_READY and therefore null.  The optional
``--require-all-core-ready`` flag is a stricter operator/audit mode.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.model_freeze import (  # noqa: E402
    FREEZE_SCHEMA_VERSION,
    LEGACY_PRE_THREE_RANK_FREEZE_ID,
    REQUIRED_ACTIVE_PIN_PATHS,
    THREE_RANK_ALL_HEADS,
    THREE_RANK_CONTRACT_VERSION,
    THREE_RANK_CONTEXT_MISSINGNESS_POLICY,
    THREE_RANK_CORE_HEADS,
    THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION,
    THREE_RANK_DATE_BINDING_RULE,
    THREE_RANK_DYNAMIC_ASSET_PATHS,
    THREE_RANK_FEATURE_CONTRACT,
    THREE_RANK_FREEZE_SCHEMA_VERSION,
    THREE_RANK_LEDGER_SCHEMA_VERSION,
    THREE_RANK_PROMOTION_BAR_CONTEXT_COLUMNS,
    THREE_RANK_RUNTIME_FEATURE_COLUMNS,
    THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION,
    THREE_RANK_REQUIRED_DATA_GATES,
    THREE_RANK_RELEASE_MODES,
    THREE_RANK_STOCK_PRIOR_RULE,
    THREE_RANK_TRAINING_CALENDAR_PATH,
    THREE_RANK_TRAINING_CALENDAR_SOURCE,
    THREE_RANK_TRAINING_EVENT_SEED_PATH,
    THREE_RANK_TOP_N,
    THREE_RANK_VALIDATION_SCHEMA_VERSION,
    frame_columns_sha256,
    validate_pinned_files,
    validate_production_three_rank_contract,
)


class ThreeRankRefreezeError(RuntimeError):
    """Raised before an invalid candidate can change the freeze manifest."""


def _fail(message: str) -> None:
    raise ThreeRankRefreezeError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    if not path.is_file() or path.is_symlink():
        _fail(f"{label} is not a regular file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThreeRankRefreezeError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"{label} must be a JSON object")
    return payload


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _safe_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label} must be a nonempty repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value:
        _fail(f"{label} is unsafe: {value!r}")
    candidate = root / relative
    probe = root
    for part in relative.parts:
        probe /= part
        if probe.is_symlink():
            _fail(f"{label} traverses a symlink: {value!r}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ThreeRankRefreezeError(f"{label} escapes repository root") from exc
    if not candidate.is_file() or candidate.is_symlink():
        _fail(f"{label} is not a regular file: {value!r}")
    return candidate


def _require_hash_bound_file(
    root: Path,
    record: Mapping[str, Any],
    *,
    label: str,
    expected_path: str,
) -> tuple[str, str]:
    path = record.get("path")
    digest = record.get("sha256")
    if path != expected_path:
        _fail(f"{label}.path must equal {expected_path!r}")
    if not isinstance(digest, str) or len(digest) != 64:
        _fail(f"{label}.sha256 is invalid")
    target = _safe_file(root, path, f"{label}.path")
    if _sha256(target) != digest:
        _fail(f"{label} bytes differ from claimed SHA-256")
    return path, digest


def _validate_prior_non_dynamic_pins(
    root: Path,
    pinned_files: Mapping[str, Any],
) -> None:
    """Prevent an autonomous retrain from re-signing unrelated drift."""

    for relative, expected in pinned_files.items():
        if not isinstance(relative, str) or not relative:
            _fail("existing Decision freeze contains an invalid pinned path")
        if not isinstance(expected, str) or len(expected) != 64:
            _fail(f"existing Decision freeze contains an invalid pin: {relative}")
        target = _safe_file(root, relative, f"existing pinned_files[{relative!r}]")
        if relative in THREE_RANK_DYNAMIC_ASSET_PATHS:
            continue
        if _sha256(target) != expected:
            _fail(
                "existing non-dynamic Decision pin drifted and cannot be "
                f"reblessed: {relative}"
            )


def build_three_rank_contract(
    root: Path | str,
    *,
    require_all_core_ready: bool = False,
    expected_release_mode: str | None = None,
) -> dict[str, Any]:
    """Build a strict production overlay from current, hash-bound evidence."""

    root_path = Path(root).resolve()
    validation_path = root_path / "models/decision_three_engines/validation_latest.json"
    ledger_manifest_path = root_path / "data/decision_three_engines/five_year_ledger_manifest.json"
    data_validation_path = root_path / "models/decision_three_engine_data_validation.json"
    validation = _load_json(validation_path, "three-engine validation")
    ledger_manifest = _load_json(ledger_manifest_path, "five-year ledger manifest")
    data_validation = _load_json(data_validation_path, "five-year data validation")

    if validation.get("schema_version") != THREE_RANK_VALIDATION_SCHEMA_VERSION:
        _fail("three-engine validation schema is invalid")
    if validation.get("contract_version") != THREE_RANK_CONTRACT_VERSION:
        _fail("three-engine contract version is invalid")
    if validation.get("feature_contract") != THREE_RANK_FEATURE_CONTRACT:
        _fail("three-engine feature contract is invalid")
    if validation.get("runtime_feature_contract_version") != (
        THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION
    ):
        _fail("three-engine runtime feature contract version is invalid")
    configuration = _mapping(validation.get("configuration"), "validation.configuration")
    if configuration.get("top_n") != THREE_RANK_TOP_N:
        _fail("three-engine Top-N contract must remain 10")
    release = _mapping(validation.get("release_contract"), "validation.release_contract")
    expected_release = {
        "actual_execution_claimed": False,
        "failed_or_constant_head_must_not_emit_official_rank": True,
        "p_fill_is_shadow_only": True,
        "promoted_only_when_all_core_heads_and_top10_integrity_pass": True,
    }
    if release != expected_release:
        _fail("three-engine release/fail-closed contract drifted")

    source = _mapping(validation.get("source"), "validation.source")
    runtime_contract = _mapping(
        source.get("runtime_feature_contract"),
        "validation.source.runtime_feature_contract",
    )
    if runtime_contract != {
        "version": THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION,
        "columns": list(THREE_RANK_RUNTIME_FEATURE_COLUMNS),
        "available_by_d_close": True,
        "future_columns_used": [],
    }:
        _fail("runtime feature inventory is not the exact D-close allowlist")
    ledger_path, ledger_sha = _require_hash_bound_file(
        root_path,
        {
            "path": source.get("ledger_path"),
            "sha256": source.get("ledger_sha256"),
        },
        label="validation.source.ledger",
        expected_path="data/decision_three_engines/five_year_supervised_ledger.csv.gz",
    )
    if source.get("ledger_manifest_path") != (
        "data/decision_three_engines/five_year_ledger_manifest.json"
    ):
        _fail("validation source ledger manifest path drifted")
    if source.get("ledger_manifest_sha256") != _sha256(ledger_manifest_path):
        _fail("validation source ledger manifest hash drifted")
    if int(source.get("rows") or 0) < 10_000 or int(source.get("dates") or 0) < 1_100:
        _fail("validation source is below the five-year coverage gate")

    if ledger_manifest.get("schema_version") != THREE_RANK_LEDGER_SCHEMA_VERSION:
        _fail("five-year ledger manifest schema is invalid")
    if ledger_manifest.get("owner") != "njedu2023-prog/DC20":
        _fail("five-year ledger is not DC20-owned")
    if ledger_manifest.get("runtime_dependency_on_top10_decision") is not False:
        _fail("five-year ledger has a top10-decision runtime dependency")
    if ledger_manifest.get("ledger_path") != ledger_path or ledger_manifest.get("ledger_sha256") != ledger_sha:
        _fail("five-year ledger manifest is not bound to validation ledger bytes")
    ledger_source = _mapping(ledger_manifest.get("source"), "ledger_manifest.source")
    if ledger_source.get("prior_grid_truth_cutoff_rule") != "strictly_before_signal_date":
        _fail("promotion prior truth is not strictly lagged")
    calendar_path = root_path / THREE_RANK_TRAINING_CALENDAR_PATH
    event_seed_path = root_path / THREE_RANK_TRAINING_EVENT_SEED_PATH
    calendar = _mapping(ledger_source.get("calendar"), "ledger_manifest.source.calendar")
    if (
        calendar.get("path") != THREE_RANK_TRAINING_CALENDAR_PATH
        or calendar.get("sha256") != _sha256(calendar_path)
        or calendar.get("source") != THREE_RANK_TRAINING_CALENDAR_SOURCE
        or calendar.get("exchange") != "SSE"
        or calendar.get("strict") is not True
        or calendar.get("pretrade_chain_validated") is not True
        or int(calendar.get("natural_day_rows") or 0) < 1
        or int(calendar.get("open_sessions") or 0) < 3
    ):
        _fail("five-year ledger is not bound to the exact strict SSE calendar")
    if ledger_source.get("date_binding_rule") != THREE_RANK_DATE_BINDING_RULE:
        _fail("five-year ledger D/T/T+1 binding is not strict SSE adjacency")
    if ledger_source.get("context_source_used") is not False:
        _fail("five-year ledger consumed untrusted seed context")
    if ledger_source.get("bar_context_rebuild_columns") != list(
        THREE_RANK_PROMOTION_BAR_CONTEXT_COLUMNS
    ):
        _fail("five-year rebuilt promotion context inventory drifted")
    if (
        ledger_source.get("context_missingness_policy")
        != THREE_RANK_CONTEXT_MISSINGNESS_POLICY
    ):
        _fail("five-year context missingness policy drifted")
    if ledger_source.get("stock_prior_rule") != THREE_RANK_STOCK_PRIOR_RULE:
        _fail("five-year stock prior is not strictly point-in-time")
    inventory = _mapping(
        ledger_source.get("event_source_inventory"),
        "ledger_manifest.source.event_source_inventory",
    )
    if (
        ledger_source.get("event_artifact") != THREE_RANK_TRAINING_EVENT_SEED_PATH
        or ledger_source.get("event_sha256") != _sha256(event_seed_path)
        or inventory.get("seed_path") != THREE_RANK_TRAINING_EVENT_SEED_PATH
        or inventory.get("seed_sha256") != _sha256(event_seed_path)
        or inventory.get("seed_raw_sha256") != _sha256(event_seed_path)
        or inventory.get("seed_context_source_used") is not False
        or inventory.get("seed_partial_identity_rows") != 0
        or inventory.get("seed_invalid_identity_rows") != 0
        or inventory.get("seed_duplicate_identity_rows") != 0
    ):
        _fail("five-year event seed evidence is invalid or not hash-bound")
    files = inventory.get("canonical_prediction_files")
    if not isinstance(files, list) or inventory.get("canonical_prediction_file_count") != len(files):
        _fail("canonical prediction source inventory is inconsistent")
    coverage = _mapping(ledger_manifest.get("coverage"), "ledger_manifest.coverage")
    expected_source_counts = {
        "rows": source.get("rows"),
        "signal_dates": source.get("dates"),
        "start_signal_date": source.get("start"),
        "end_signal_date": source.get("end"),
    }
    for key, expected in expected_source_counts.items():
        if coverage.get(key) != expected:
            _fail(f"ledger coverage {key} differs from model validation")

    if data_validation.get("schema_version") != THREE_RANK_DATA_VALIDATION_SCHEMA_VERSION:
        _fail("five-year data validation schema is invalid")
    if (
        data_validation.get("valid") is not True
        or data_validation.get("status") != "PASS"
        or data_validation.get("failed_gates") != []
    ):
        _fail("five-year data validation is not PASS")
    independence = _mapping(data_validation.get("independence"), "data_validation.independence")
    if independence != {
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
    }:
        _fail("five-year data validation does not prove DC20 isolation")
    inputs = _mapping(data_validation.get("inputs"), "data_validation.inputs")
    if _mapping(inputs.get("ledger"), "data_validation.inputs.ledger") != {
        "path": ledger_path,
        "sha256": ledger_sha,
    }:
        _fail("data validation ledger binding differs from source ledger")
    if _mapping(inputs.get("manifest"), "data_validation.inputs.manifest") != {
        "path": "data/decision_three_engines/five_year_ledger_manifest.json",
        "sha256": _sha256(ledger_manifest_path),
    }:
        _fail("data validation ledger manifest binding is invalid")
    if _mapping(
        inputs.get("sse_trade_calendar"),
        "data_validation.inputs.sse_trade_calendar",
    ) != {
        "path": THREE_RANK_TRAINING_CALENDAR_PATH,
        "sha256": _sha256(calendar_path),
    }:
        _fail("data validation strict SSE calendar binding is invalid")
    if _mapping(inputs.get("event_seed"), "data_validation.inputs.event_seed") != {
        "path": THREE_RANK_TRAINING_EVENT_SEED_PATH,
        "sha256": _sha256(event_seed_path),
    }:
        _fail("data validation event seed binding is invalid")
    hard_gates = _mapping(data_validation.get("hard_gates"), "data_validation.hard_gates")
    missing_gates = sorted(THREE_RANK_REQUIRED_DATA_GATES.difference(hard_gates))
    if missing_gates:
        _fail(f"five-year data validation omits required gates: {missing_gates}")
    for gate_name in sorted(THREE_RANK_REQUIRED_DATA_GATES):
        gate = _mapping(
            hard_gates[gate_name], f"data_validation.hard_gates.{gate_name}"
        )
        if gate.get("passed") is not True:
            _fail(f"five-year data validation gate is not PASS: {gate_name}")
    calendar_report = _mapping(
        data_validation.get("strict_sse_calendar"),
        "data_validation.strict_sse_calendar",
    )
    if (
        calendar_report.get("valid") is not True
        or calendar_report.get("path") != THREE_RANK_TRAINING_CALENDAR_PATH
        or calendar_report.get("sha256") != _sha256(calendar_path)
        or calendar_report.get("pretrade_chain_validated") is not True
    ):
        _fail("data validation strict SSE calendar audit is invalid")
    date_binding = _mapping(
        data_validation.get("date_binding"), "data_validation.date_binding"
    )
    if (
        date_binding.get("rule") != THREE_RANK_DATE_BINDING_RULE
        or date_binding.get("rows") != source.get("rows")
        or date_binding.get("violations") != 0
    ):
        _fail("data validation D/T/T+1 adjacency audit is invalid")
    seed_audit = _mapping(
        data_validation.get("event_seed_audit"),
        "data_validation.event_seed_audit",
    )
    if (
        seed_audit.get("valid") is not True
        or seed_audit.get("path") != THREE_RANK_TRAINING_EVENT_SEED_PATH
        or seed_audit.get("sha256") != _sha256(event_seed_path)
    ):
        _fail("data validation event seed audit is invalid")
    stock_prior_audit = _mapping(
        data_validation.get("stock_prior_audit"),
        "data_validation.stock_prior_audit",
    )
    if (
        stock_prior_audit.get("valid") is not True
        or stock_prior_audit.get("rule") != THREE_RANK_STOCK_PRIOR_RULE
        or stock_prior_audit.get("rows_checked") != source.get("rows")
    ):
        _fail("data validation stock prior audit is invalid")

    head_validations = _mapping(validation.get("heads"), "validation.heads")
    metadata = _mapping(validation.get("model_metadata"), "validation.model_metadata")
    artifacts = _mapping(validation.get("artifacts"), "validation.artifacts")
    heads: dict[str, Any] = {}
    core_ready: list[bool] = []
    for head in THREE_RANK_ALL_HEADS:
        head_validation = _mapping(head_validations.get(head), f"validation.heads.{head}")
        identity = _mapping(metadata.get(head), f"validation.model_metadata.{head}")
        artifact = _mapping(artifacts.get(head), f"validation.artifacts.{head}")
        expected_path = f"models/decision_three_engines/{head}.joblib"
        artifact_path, artifact_sha = _require_hash_bound_file(
            root_path,
            artifact,
            label=f"validation.artifacts.{head}",
            expected_path=expected_path,
        )
        for key in ("status", "promoted"):
            if identity.get(key) != head_validation.get(key):
                _fail(f"{head} model metadata {key} differs from head validation")
        if identity.get("artifact_sha256") != artifact_sha:
            _fail(f"{head} model metadata hash differs from artifact inventory")
        status = identity.get("status")
        promoted = identity.get("promoted")
        if not isinstance(status, str) or type(promoted) is not bool:
            _fail(f"{head} status/promotion state is invalid")
        if head in THREE_RANK_CORE_HEADS:
            if promoted != (status == "READY"):
                _fail(f"{head} READY/promotion state is inconsistent")
            if status != "READY" and not status.startswith("NOT_READY_"):
                _fail(f"{head} has an unsupported core status")
            gate_failures = head_validation.get("gate_failures")
            if status == "READY" and gate_failures != []:
                _fail(f"{head} READY head still has failed validation gates")
            if status != "READY" and (
                not isinstance(gate_failures, list) or not gate_failures
            ):
                _fail(f"{head} NOT_READY head must record failed validation gates")
            core_ready.append(promoted)
        else:
            if promoted is not False:
                _fail("p_fill_shadow must never be promoted")
            if head_validation.get("cannot_change_core_members_or_ranks") is not True:
                _fail("p_fill_shadow may not change core membership or ranks")
            if status != "SHADOW_READY" and not status.startswith("NOT_READY_"):
                _fail("p_fill_shadow has an unsupported status")
        version = identity.get("model_version")
        as_of = identity.get("model_as_of_date")
        if not isinstance(version, str) or not version or not isinstance(as_of, str) or not as_of:
            _fail(f"{head} version/as-of provenance is missing")
        heads[head] = {
            "role": "core" if head in THREE_RANK_CORE_HEADS else "shadow_only",
            "status": status,
            "promoted": promoted,
            "model_version": version,
            "model_as_of_date": as_of,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha,
        }

    all_core = all(core_ready)
    if heads["promotion"]["promoted"] is not True:
        _fail("initial/production freeze requires a READY promotion authority")
    secondary_promoted = [
        heads[head]["promoted"] for head in ("big_loss", "profit")
    ]
    if all(secondary_promoted):
        release_mode = "ALL_CORE_READY"
    elif not any(secondary_promoted):
        release_mode = "PROMOTION_READY_PARTIAL"
    else:
        _fail("release cannot mix one READY and one NOT_READY secondary head")
    if validation.get("all_core_heads_promoted") is not all_core:
        _fail("validation all_core_heads_promoted is inconsistent")
    if validation.get("ready") is not all_core:
        _fail("validation ready state is inconsistent")
    expected_status = "READY" if all_core else "NOT_READY_VALIDATION_GATE"
    if validation.get("status") != expected_status:
        _fail("validation overall status is inconsistent")
    if require_all_core_ready and not all_core:
        _fail("automated production refreeze requires all three core heads READY")
    if expected_release_mode is not None:
        if expected_release_mode not in THREE_RANK_RELEASE_MODES:
            _fail(f"unsupported expected release mode: {expected_release_mode}")
        if release_mode != expected_release_mode:
            _fail(
                "validated release mode differs from publisher authorization: "
                f"expected={expected_release_mode} actual={release_mode}"
            )

    oof_artifact = _mapping(artifacts.get("oof_top10"), "validation.artifacts.oof_top10")
    oof_path, oof_sha = _require_hash_bound_file(
        root_path,
        oof_artifact,
        label="validation.artifacts.oof_top10",
        expected_path="outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
    )
    oof_validation = _mapping(validation.get("oof_top10"), "validation.oof_top10")
    if oof_validation.get("valid") is not True or oof_validation.get("failures") != []:
        _fail("OOF Top10 integrity validation is not clean")
    if (
        oof_artifact.get("rows") != oof_validation.get("rows")
        or oof_artifact.get("dates") != oof_validation.get("dates")
    ):
        _fail("OOF artifact row/date inventory differs from validation")

    contract = {
        "schema_version": THREE_RANK_FREEZE_SCHEMA_VERSION,
        "contract_version": THREE_RANK_CONTRACT_VERSION,
        "validation_schema_version": THREE_RANK_VALIDATION_SCHEMA_VERSION,
        "feature_contract": THREE_RANK_FEATURE_CONTRACT,
        "runtime_feature_contract_version": THREE_RANK_RUNTIME_FEATURE_CONTRACT_VERSION,
        "runtime_feature_columns": list(THREE_RANK_RUNTIME_FEATURE_COLUMNS),
        "runtime_feature_columns_sha256": frame_columns_sha256(THREE_RANK_RUNTIME_FEATURE_COLUMNS),
        "feature_columns_sha256": source.get("feature_columns_sha256"),
        "top_n": THREE_RANK_TOP_N,
        "eligible_pool": "hard_stage_2_to_3_and_3_to_4_pool",
        "membership_authority": "promotion_probability_engine_only",
        "downstream_scope": "exact_frozen_promotion_top10",
        "fail_closed": {
            "artifact_or_ledger_drift": "ZERO_OFFICIAL_CORE_RANKS",
            "missing_or_invalid_runtime_feature": "ZERO_OFFICIAL_CORE_RANKS",
            "promotion_not_ready": "EMPTY_OFFICIAL_TOP10",
            "unready_secondary_head": "NULL_HEAD_FIELDS",
            "shadow_may_change_membership": False,
            "shadow_may_override_core_ranks": False,
            "formal_trade_status": "NO_TRADE_MODEL_NOT_PROMOTED",
        },
        "source_ledger": {
            "owner": ledger_manifest["owner"],
            "runtime_dependency_on_top10_decision": ledger_manifest[
                "runtime_dependency_on_top10_decision"
            ],
            "schema_version": ledger_manifest["schema_version"],
            "ledger_path": ledger_path,
            "ledger_sha256": ledger_sha,
            "ledger_manifest_path": "data/decision_three_engines/five_year_ledger_manifest.json",
            "ledger_manifest_sha256": _sha256(ledger_manifest_path),
            "data_validation_path": "models/decision_three_engine_data_validation.json",
            "data_validation_sha256": _sha256(data_validation_path),
            "data_validation_schema_version": data_validation["schema_version"],
            "data_validation_status": data_validation["status"],
            "data_validation_valid": data_validation["valid"],
            "rows": source["rows"],
            "signal_dates": source["dates"],
            "start_signal_date": source["start"],
            "end_signal_date": source["end"],
            "prior_truth_cutoff_rule": ledger_source["prior_grid_truth_cutoff_rule"],
            "event_source_inventory_sha256": _canonical_sha256(inventory),
            "canonical_prediction_file_count": inventory[
                "canonical_prediction_file_count"
            ],
            "calendar_path": THREE_RANK_TRAINING_CALENDAR_PATH,
            "calendar_sha256": calendar["sha256"],
            "calendar_source": calendar["source"],
            "calendar_exchange": calendar["exchange"],
            "strict_calendar": calendar["strict"],
            "event_seed_path": THREE_RANK_TRAINING_EVENT_SEED_PATH,
            "event_seed_sha256": inventory["seed_sha256"],
            "date_binding_rule": ledger_source["date_binding_rule"],
            "context_source_used": ledger_source["context_source_used"],
            "bar_context_rebuild_columns": ledger_source[
                "bar_context_rebuild_columns"
            ],
            "context_missingness_policy": ledger_source[
                "context_missingness_policy"
            ],
            "stock_prior_rule": ledger_source["stock_prior_rule"],
        },
        "validation": {
            "path": "models/decision_three_engines/validation_latest.json",
            "sha256": _sha256(validation_path),
            "schema_version": validation["schema_version"],
            "status": validation["status"],
            "ready": validation["ready"],
            "generated_at_utc": validation["generated_at_utc"],
        },
        "heads": heads,
        "oof_top10": {
            "path": oof_path,
            "sha256": oof_sha,
            "dataset_sha256": oof_validation["dataset_sha256"],
            "rows": oof_validation["rows"],
            "dates": oof_validation["dates"],
            "valid": oof_validation["valid"],
        },
        "all_core_heads_promoted": all_core,
        "release_mode": release_mode,
    }
    probe_pins = {
        source["ledger_path"]: source["ledger_sha256"],
        "data/decision_three_engines/five_year_ledger_manifest.json": _sha256(ledger_manifest_path),
        "models/decision_three_engine_data_validation.json": _sha256(data_validation_path),
        "models/decision_three_engines/validation_latest.json": _sha256(validation_path),
        oof_path: oof_sha,
        THREE_RANK_TRAINING_CALENDAR_PATH: calendar["sha256"],
        THREE_RANK_TRAINING_EVENT_SEED_PATH: inventory["seed_sha256"],
        **{item["artifact_path"]: item["artifact_sha256"] for item in heads.values()},
    }
    validate_production_three_rank_contract(
        root_path,
        {"freeze_id": "candidate", "production": {"three_rank": contract}, "pinned_files": probe_pins},
    )
    return contract


def build_refrozen_manifest(
    root: Path | str,
    manifest: Mapping[str, Any],
    *,
    require_all_core_ready: bool = False,
    expected_release_mode: str | None = None,
) -> dict[str, Any]:
    """Return a new freeze without touching legacy history/canonical facts."""

    root_path = Path(root).resolve()
    if not isinstance(manifest, Mapping):
        _fail("Decision freeze manifest must be an object")
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("three-rank refreeze requires decision_model_freeze_v2")
    if manifest.get("active") is not True:
        _fail("three-rank refreeze requires the active Decision freeze")
    required_legacy = (
        "training_cutoff_signal_date",
        "history_snapshot",
        "behavior_contract",
    )
    for key in required_legacy:
        if key not in manifest:
            _fail(f"Decision freeze is missing legacy field {key}")
    production = _mapping(manifest.get("production"), "production")
    for key in ("legacy_v1_audit", "canonical_v2"):
        if key not in production:
            _fail(f"Decision freeze is missing production.{key}")

    existing_pins = manifest.get("pinned_files")
    if not isinstance(existing_pins, dict):
        _fail("Decision freeze pinned_files must be an object")
    if "three_rank" in production:
        _validate_prior_non_dynamic_pins(root_path, existing_pins)
    elif manifest.get("freeze_id") != LEGACY_PRE_THREE_RANK_FREEZE_ID:
        _fail(
            "only the exact reviewed pre-three-rank freeze may use the "
            "one-time migration exception"
        )

    candidate = copy.deepcopy(dict(manifest))
    candidate_production = _mapping(candidate["production"], "production")
    contract = build_three_rank_contract(
        root_path,
        require_all_core_ready=require_all_core_ready,
        expected_release_mode=expected_release_mode,
    )
    candidate_production["three_rank"] = contract
    state = "ready" if contract["all_core_heads_promoted"] else "partial"
    candidate["freeze_id"] = (
        f"dc20_decision_three_rank_v2_{state}_d"
        f"{contract['source_ledger']['end_signal_date']}_"
        f"{contract['validation']['sha256'][:16]}"
    )

    pin_paths = set(existing_pins) | set(REQUIRED_ACTIVE_PIN_PATHS)
    pins: dict[str, str] = {}
    for relative in sorted(pin_paths):
        target = _safe_file(root_path, relative, f"pinned_files[{relative!r}]")
        pins[relative] = _sha256(target)
    candidate["pinned_files"] = pins

    # These historical facts are deliberately immutable across the overlay.
    for key in required_legacy:
        if candidate[key] != manifest[key]:
            _fail(f"refreeze attempted to rewrite legacy {key}")
    for key in (
        "model_version",
        "promoted",
        "trade_selector_version",
        "trade_selector_promoted",
        "formal_status",
        "formal_buy_count",
        "legacy_v1_audit",
        "canonical_v2",
    ):
        if candidate_production.get(key) != production.get(key):
            _fail(f"refreeze attempted to rewrite legacy production.{key}")

    validate_production_three_rank_contract(
        root_path,
        candidate,
        require_complete=True,
    )
    validate_pinned_files(root_path, candidate)
    return candidate


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        _fail(f"freeze manifest must not be a symlink: {path}")
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("models/decision_model_freeze.json"),
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-all-core-ready", action="store_true")
    parser.add_argument(
        "--expected-release-mode",
        choices=THREE_RANK_RELEASE_MODES,
        help="bind this refreeze to the release mode authorized by the gate",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    try:
        manifest_path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ThreeRankRefreezeError("manifest path escapes repository root") from exc
    current = _load_json(manifest_path, "Decision freeze manifest")
    candidate = build_refrozen_manifest(
        root,
        current,
        require_all_core_ready=args.require_all_core_ready,
        expected_release_mode=args.expected_release_mode,
    )
    if not args.check:
        _atomic_write_json(manifest_path, candidate)
    print(
        json.dumps(
            {
                "check_only": args.check,
                "freeze_id": candidate["freeze_id"],
                "all_core_heads_promoted": candidate["production"]["three_rank"][
                    "all_core_heads_promoted"
                ],
                "release_mode": candidate["production"]["three_rank"][
                    "release_mode"
                ],
                "pinned_files": len(candidate["pinned_files"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
