#!/usr/bin/env python3
"""Publish P1 single-profit and mixed-profit research for the exact P0 TopN.

The only dated candidate input is ``primary_d_runtime_features_<D>.csv`` and
its P0 receipt/three-rank bundle.  This command never reads Auction predictions,
Action plans, forward Shadow selections, settlements, or statistics.

Every complete mixed-profit projection from the public cutover date is also
catalogued in a daily Top1/Top2 audit ledger.  ``RETROSPECTIVE_RECOVERY`` stays
visibly non-forward and is excluded from the separate forward performance
ledger; recording a ranking fact must never masquerade as a prospective freeze.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.executable_profit_shadow import (  # noqa: E402
    ARTIFACT_STATUS,
    DEFAULT_HISTORY_LEDGER_PATH,
    EXPECTED_ALL_FEATURES_SHA256,
    EXPECTED_MODEL_SHA256,
    INTERNAL_STATUS,
    ExecutableProfitShadowError,
    _feature_snapshot_sha256 as _mixed_feature_snapshot_sha256,
    _promotion_feature_snapshot_sha256,
    _read_pinned_sse_open_dates,
    _restore_hash_bound_runtime_promotion_priors,
    _strict_frozen_stage_numbers,
    _strict_top10_targets,
    build_strict_lagged_priors,
    load_internal_challenger,
)
from top10decision.decision.legacy_profit_relative_research import (  # noqa: E402
    SCORE_SEMANTICS,
    SEALED_PROFIT_ARTIFACT_PATH,
    SEALED_PROFIT_ARTIFACT_SHA256,
    SEALED_PROFIT_GATE_PASS_COUNT,
    SEALED_PROFIT_GATE_SCORE_PCT,
    SEALED_PROFIT_GATE_TOTAL_COUNT,
    SEALED_PROFIT_MODEL_AS_OF_DATE,
    SEALED_PROFIT_MODEL_VERSION,
    SEALED_PROFIT_OFFICIAL_STATUS,
    SEALED_VALIDATION_PATH,
    SEALED_VALIDATION_SHA256,
    score_legacy_profit_relative_rows,
)
from top10decision.decision.three_engine_models import (  # noqa: E402
    load_research_only_legacy_three_engine_snapshot,
)
from top10decision.decision.three_rank import (  # noqa: E402
    top10_members_sha256,
    validate_three_rank_contract,
)


DATE_RE = re.compile(r"20\d{6}")
CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MODES = ("NATURAL", "RETROSPECTIVE_RECOVERY")
MODE_STATUS = {
    "NATURAL": "PROSPECTIVE_RESEARCH",
    "RETROSPECTIVE_RECOVERY": "RETROSPECTIVE_NON_FORWARD_RESEARCH",
}

CONTRACT_PATH = Path("models/decision_primary_profit_research_contract.json")
CONTRACT_SCHEMA = "dc20_primary_profit_research_contract_v1"
CONTRACT_ID = "dc20_primary_profit_research_20260827_v1"

SINGLE_SCHEMA = "dc20_primary_single_profit_research_v1"
SINGLE_KIND = "immutable_d_frozen_primary_single_profit_research"
SINGLE_INDEX_SCHEMA = "dc20_primary_single_profit_research_index_v1"
SINGLE_ROOT = Path("outputs/decision/legacy_profit_relative_research")

MIXED_SCHEMA = "dc20_primary_mixed_profit_research_projection_v1"
MIXED_KIND = "immutable_d_frozen_primary_mixed_profit_research_projection"
MIXED_INDEX_SCHEMA = "dc20_primary_mixed_profit_research_index_v1"
MIXED_ROOT = Path("outputs/decision/executable_profit_research")

DAILY_MIXED_TOP2_SCHEMA = "dc20_primary_mixed_daily_top2_index_v1"
DAILY_MIXED_TOP2_KIND = "primary_mixed_top2_daily_audit_index"
DAILY_MIXED_TOP2_START_DATE = "20260828"
DAILY_MIXED_TOP2_PATH = MIXED_ROOT / "daily_mixed_top2_index.json"
DAILY_MIXED_TOP2_DISPLAY_NAME = "混合盈利排序 Top1 / Top2 每日影子记录"
DAILY_MIXED_TOP2_BOUNDARIES = {
    "research_only": True,
    "every_complete_mixed_projection_must_be_recorded": True,
    "retrospective_records_enter_forward_statistics": False,
    "forward_statistics_require_separate_prospective_freeze": True,
    "may_change_promotion_membership_or_rank": False,
    "may_create_trade_action": False,
    "broker_or_order_integration_allowed": False,
    "actual_execution_claimed": False,
}

DAILY_MIXED_TOP2_ROW_KEYS = frozenset(
    {
        "slot",
        "ts_code",
        "name",
        "industry",
        "stage_transition",
        "promotion_rank",
        "mixed_profit_rank",
    }
)

DAILY_MIXED_TOP2_ENTRY_KEYS = frozenset(
    {
        "signal_date",
        "exec_date",
        "exit_date",
        "generation_mode",
        "prospective",
        "retrospective_non_forward",
        "record_class",
        "forward_statistics_policy",
        "candidate_count",
        "recorded_slots",
        "projection_json_url",
        "projection_json_sha256",
        "projection_csv_url",
        "projection_csv_sha256",
        "projection_snapshot_sha256",
        "top10_members_sha256",
        "source_bundle_sha256",
        "source_feature_snapshot_sha256",
        "rows",
        "entry_sha256",
    }
)

DAILY_MIXED_TOP2_INDEX_KEYS = frozenset(
    {
        "schema_version",
        "index_kind",
        "data_alias",
        "display_name",
        "public_start_signal_date",
        "latest_signal_date",
        "recorded_signal_dates",
        "recorded_days",
        "recorded_slots",
        "entries",
        "boundaries",
        "snapshot_sha256",
    }
)

PRIMARY_INDEX_KEYS = frozenset(
    {
        "schema_version",
        "index_kind",
        "data_alias",
        "display_name",
        "status",
        "generation_mode",
        "prospective",
        "retrospective_non_forward",
        "latest_signal_date",
        "latest_exec_date",
        "latest_exit_date",
        "latest_projection_json_url",
        "latest_projection_json_sha256",
        "latest_projection_csv_url",
        "latest_projection_csv_sha256",
        "latest_projection_snapshot_sha256",
        "latest_top10_members_sha256",
        "latest_source_bundle_sha256",
        "latest_source_feature_snapshot_sha256",
        "candidate_count",
        "source_bindings",
        "boundaries",
    }
)

BOUNDARIES = {
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

SINGLE_ROW_FIELDS = (
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "legacy_profit_relative_rank",
    "legacy_profit_raw_score",
    "legacy_profit_relative_percentile",
    "rank_tied",
    "rank_group_size",
)

MIXED_ROW_FIELDS = (
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "predicted_promotion_probability",
    "executable_profit_research_rank",
    "estimated_executable_profit_probability",
    "research_joint_proxy_score",
    "research_fill_proxy_score",
    "research_conditional_profit_score",
    "rank_tied",
    "rank_group_size",
)


class PrimaryProfitRankingError(RuntimeError):
    """Raised when P1 cannot prove its exact P0 lineage or research boundary."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PrimaryProfitRankingError(message)


def _normal_date(value: Any) -> str:
    text = "".join(character for character in str(value or "") if character.isdigit())
    return text[:8] if len(text) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if CODE_RE.fullmatch(text) else ""


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    return str(value)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_snapshot(payload: Mapping[str, Any]) -> str:
    copied = copy.deepcopy(dict(payload))
    copied.pop("snapshot_sha256", None)
    copied.pop("downloads", None)
    return _canonical_sha256(copied)


def _safe_file(root: Path, relative: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    _expect(not relative.is_absolute() and ".." not in relative.parts, f"unsafe {label} path")
    current = root
    for part in relative.parts:
        current = current / part
        _expect(not current.is_symlink(), f"{label} has a symlink ancestor")
    _expect(current.is_file() and current.stat().st_size > 0, f"{label} is missing or empty")
    _expect(current.resolve(strict=True).is_relative_to(root), f"{label} escaped repository")
    return current


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrimaryProfitRankingError(f"{label} is invalid") from exc
    _expect(isinstance(value, dict), f"{label} must be an object")
    return value


def _ensure_directory(root: Path, relative: Path) -> Path:
    root = root.resolve(strict=True)
    _expect(not relative.is_absolute() and ".." not in relative.parts, "unsafe P1 output path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists():
            _expect(current.is_dir() and not current.is_symlink(), "P1 output has a symlink ancestor")
        else:
            current.mkdir()
    return current


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        _expect(path.is_file() and not path.is_symlink(), "immutable P1 path is unsafe")
        _expect(path.read_bytes() == content, f"immutable P1 artifact rewrite rejected: {path.name}")
        return
    _atomic_write(path, content)


def _load_contract(root: Path) -> tuple[Path, dict[str, Any]]:
    path = _safe_file(root, CONTRACT_PATH, label="P1 core-research contract")
    contract = _read_json(path, label="P1 core-research contract")
    _expect(
        contract.get("schema_version") == CONTRACT_SCHEMA
        and contract.get("contract_id") == CONTRACT_ID
        and contract.get("status") == "PUBLIC_CORE_RESEARCH_ALLOWED_NOT_FORMAL",
        "P1 core-research contract identity drifted",
    )
    _expect(contract.get("boundaries") == BOUNDARIES, "P1 contract boundaries drifted")
    inputs = contract.get("inputs")
    outputs = contract.get("outputs")
    modes = contract.get("generation_modes")
    _expect(
        isinstance(inputs, Mapping)
        and inputs.get("action_input_allowed") is False
        and inputs.get("auction_prediction_input_allowed") is False
        and inputs.get("network_input_allowed") is False,
        "P1 contract enabled a forbidden input",
    )
    _expect(
        isinstance(outputs, Mapping)
        and outputs.get("forward_selection_output_allowed") is False
        and outputs.get("forward_statistics_output_allowed") is False
        and outputs.get("action_output_allowed") is False,
        "P1 contract enabled a forbidden output",
    )
    _expect(
        isinstance(modes, Mapping)
        and set(modes) == set(MODES)
        and modes["NATURAL"].get("publication_status") == MODE_STATUS["NATURAL"]
        and modes["RETROSPECTIVE_RECOVERY"].get("publication_status")
        == MODE_STATUS["RETROSPECTIVE_RECOVERY"]
        and all(
            item.get("may_write_forward_selection") is False
            and item.get("may_write_forward_statistics") is False
            for item in modes.values()
        ),
        "P1 generation-mode contract drifted",
    )
    return path, contract


@dataclass(frozen=True)
class PrimaryInputs:
    root: Path
    signal_date: str
    generation_mode: str
    contract_path: Path
    receipt_path: Path
    runtime_path: Path
    three_json_path: Path
    three_csv_path: Path
    receipt: Mapping[str, Any]
    three_rank: Mapping[str, Any]
    full_runtime: pd.DataFrame
    selected_runtime: pd.DataFrame
    source_bindings: Mapping[str, Any]


def _receipt_output(outputs: Mapping[str, Any], primary: str, *aliases: str) -> Any:
    for name in (primary, *aliases):
        if name in outputs:
            return outputs[name]
    raise PrimaryProfitRankingError(f"P0 receipt output missing: {primary}")


def _parse_aware(value: Any, *, label: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PrimaryProfitRankingError(f"{label} is invalid") from exc
    _expect(parsed.tzinfo is not None, f"{label} is not timezone-aware")
    return parsed


def load_primary_inputs(root: Path, signal_date: str, generation_mode: str) -> PrimaryInputs:
    root = root.resolve(strict=True)
    date = _normal_date(signal_date)
    mode = str(generation_mode or "").strip().upper()
    _expect(DATE_RE.fullmatch(date) is not None and date == signal_date, "P1 signal date must be YYYYMMDD")
    _expect(mode in MODES, f"P1 generation mode must be one of {MODES}")
    contract_path, _contract = _load_contract(root)

    receipt_relative = Path(f"outputs/decision/primary_d_receipt_{date}.json")
    runtime_relative = Path(f"outputs/decision/primary_d_runtime_features_{date}.csv")
    three_json_relative = Path(f"outputs/decision/three_rank_top10_{date}.json")
    three_csv_relative = Path(f"outputs/decision/three_rank_top10_{date}.csv")
    receipt_path = _safe_file(root, receipt_relative, label="exact P0 receipt")
    runtime_path = _safe_file(root, runtime_relative, label="exact P0 runtime features")
    three_json_path = _safe_file(root, three_json_relative, label="exact P0 three-rank JSON")
    three_csv_path = _safe_file(root, three_csv_relative, label="exact P0 three-rank CSV")

    receipt = _read_json(receipt_path, label="exact P0 receipt")
    three_rank = _read_json(three_json_path, label="exact P0 three-rank JSON")
    try:
        validate_three_rank_contract(three_rank)
    except Exception as exc:
        raise PrimaryProfitRankingError("exact P0 three-rank contract is invalid") from exc
    _expect(
        receipt.get("schema_version") == "dc20_primary_d_receipt_v1"
        and receipt.get("artifact_kind") == "p0_promotion_only_d_list_receipt"
        and receipt.get("primary_status") == "READY",
        "P0 receipt identity/status drifted",
    )
    _expect(
        receipt.get("signal_date") == three_rank.get("signal_date") == date
        and receipt.get("exec_date") == three_rank.get("exec_date")
        and receipt.get("exit_date") == three_rank.get("exit_date"),
        "P0 receipt/three-rank D/T/T+1 drifted",
    )
    _expect(receipt.get("generation_mode") == mode, "P0/P1 generation mode drifted")
    if mode == "NATURAL":
        _expect(
            receipt.get("prospective") is True
            and receipt.get("forward_eligible") is True
            and receipt.get("not_forward_generated") is False,
            "NATURAL P1 requires a prospective P0 receipt",
        )
    else:
        _expect(
            receipt.get("prospective") is False
            and receipt.get("not_forward_generated") is True,
            "retrospective P1 requires a non-forward P0 receipt",
        )
    _expect(
        receipt.get("action_authorized") is False
        and receipt.get("action_input_consumed") is False
        and int(receipt.get("formal_trade_count") or 0) == 0,
        "P0 receipt crossed the no-Action/no-trade boundary",
    )

    outputs = receipt.get("outputs")
    _expect(isinstance(outputs, Mapping), "P0 receipt outputs missing")
    runtime_bound_path = str(
        _receipt_output(outputs, "runtime_features_path", "runtime_feature_path")
    )
    runtime_bound_sha = str(
        _receipt_output(outputs, "runtime_features_sha256", "runtime_feature_sha256")
    )
    runtime_row_count = int(
        _receipt_output(outputs, "runtime_feature_row_count", "runtime_features_row_count")
    )
    runtime_selected_count = int(
        _receipt_output(outputs, "runtime_selected_count", "runtime_feature_selected_count")
    )
    runtime_identity_sha = str(
        _receipt_output(outputs, "runtime_identity_sha256", "runtime_features_identity_sha256")
    )
    _expect(runtime_bound_path == runtime_relative.as_posix(), "P0 receipt runtime path drifted")
    _expect(
        SHA256_RE.fullmatch(runtime_bound_sha) is not None
        and _sha256(runtime_path) == runtime_bound_sha,
        "P0 receipt runtime SHA drifted",
    )
    _expect(
        outputs.get("json_path") == three_json_relative.as_posix()
        and outputs.get("csv_path") == three_csv_relative.as_posix()
        and outputs.get("json_sha256") == _sha256(three_json_path)
        and outputs.get("csv_sha256") == _sha256(three_csv_path),
        "P0 receipt three-rank file binding drifted",
    )
    _expect(
        outputs.get("feature_snapshot_sha256") == three_rank.get("feature_snapshot_sha256")
        and outputs.get("top10_members_sha256") == three_rank.get("top10_members_sha256")
        and outputs.get("bundle_sha256") == three_rank.get("bundle_sha256"),
        "P0 receipt three-rank fingerprint drifted",
    )

    try:
        full_runtime = pd.read_csv(
            runtime_path,
            low_memory=False,
            float_precision="round_trip",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise PrimaryProfitRankingError("P0 runtime feature CSV is invalid") from exc
    required = {
        "signal_date",
        "ts_code",
        "name",
        "industry",
        "stage",
        "stage_transition",
        "board",
        "generated_at_utc",
        "feature_snapshot_sha256",
        "top10_selected",
        "promotion_rank",
        "predicted_promotion_probability",
    }
    _expect(required.issubset(full_runtime.columns), "P0 runtime feature columns are incomplete")
    _expect(len(full_runtime) == runtime_row_count, "P0 runtime row count differs from receipt")
    _expect(
        len(full_runtime) == int(three_rank.get("promotion_pool_size") or 0),
        "P0 runtime is not the complete hard-range promotion pool",
    )
    if full_runtime.empty:
        selected = full_runtime.copy()
    else:
        _expect(full_runtime["signal_date"].map(_normal_date).eq(date).all(), "P0 runtime mixes signal dates")
        codes = full_runtime["ts_code"].map(_normal_code)
        _expect(not codes.eq("").any() and not codes.duplicated().any(), "P0 runtime codes are invalid or duplicated")
        full_runtime = full_runtime.copy()
        full_runtime["ts_code"] = codes
        selected_flag = pd.to_numeric(full_runtime["top10_selected"], errors="coerce")
        _expect(
            selected_flag.notna().all() and selected_flag.isin((0, 1)).all(),
            "P0 runtime selected flags are invalid",
        )
        selected = full_runtime.loc[selected_flag.eq(1)].copy()
    _expect(
        len(selected) == runtime_selected_count == int(three_rank.get("top10_count") or 0)
        and len(selected) == min(10, len(full_runtime)),
        "P0 runtime selected count violates exact real TopN",
    )
    frozen_rows = three_rank.get("rows")
    _expect(isinstance(frozen_rows, list) and len(frozen_rows) == len(selected), "P0 frozen TopN rows drifted")
    selected = selected.sort_values("promotion_rank", kind="stable").reset_index(drop=True)
    frozen_codes = [_normal_code(row.get("ts_code")) for row in frozen_rows]
    selected_codes = selected["ts_code"].astype(str).tolist()
    _expect(selected_codes == frozen_codes, "P0 runtime selected order/membership differs from three-rank")
    for position, (runtime_row, frozen) in enumerate(zip(selected.to_dict("records"), frozen_rows), start=1):
        _expect(
            int(runtime_row["promotion_rank"]) == frozen.get("promotion_rank") == position
            and str(runtime_row.get("stage_transition") or "") == frozen.get("stage_transition")
            and math.isclose(
                float(runtime_row["predicted_promotion_probability"]),
                float(frozen["predicted_promotion_probability"]),
                rel_tol=0.0,
                abs_tol=1e-15,
            ),
            "P0 runtime changed a frozen promotion row",
        )
    expected_members = top10_members_sha256(date, selected_codes)
    _expect(
        expected_members == three_rank.get("top10_members_sha256"),
        "P0 runtime selected membership fingerprint drifted",
    )
    if full_runtime.empty:
        generated_values: list[str] = []
        feature_values: list[str] = []
    else:
        generated_values = full_runtime["generated_at_utc"].fillna("").astype(str).unique().tolist()
        feature_values = full_runtime["feature_snapshot_sha256"].fillna("").astype(str).unique().tolist()
        _expect(len(generated_values) == 1 and bool(generated_values[0]), "P0 runtime generation timestamp is not uniform")
        _expect(
            feature_values == [str(three_rank.get("feature_snapshot_sha256") or "")]
            and SHA256_RE.fullmatch(feature_values[0]) is not None,
            "P0 runtime feature snapshot is not uniformly frozen",
        )
        if mode == "NATURAL":
            generated = _parse_aware(generated_values[0], label="P0 runtime generated_at_utc").astimezone(ZoneInfo("Asia/Shanghai"))
            start = datetime.strptime(date + " 15:00:00 +0800", "%Y%m%d %H:%M:%S %z")
            end = datetime.strptime(str(three_rank["exec_date"]) + " 09:20:00 +0800", "%Y%m%d %H:%M:%S %z")
            _expect(start < generated < end, "NATURAL P1 runtime was not frozen before T 09:20")

    _expect("identity" in full_runtime.columns, "P0 runtime identity column is missing")
    identity_rows: list[dict[str, Any]] = []
    for row in full_runtime.sort_values("ts_code", kind="stable").to_dict("records"):
        expected_row_identity = (
            f"{date}|{row['ts_code']}|{row['stage_transition']}"
        )
        _expect(
            str(row.get("identity") or "") == expected_row_identity,
            "P0 runtime row identity drifted",
        )
        selected_value = int(float(row["top10_selected"]))
        rank_value = int(float(row["promotion_rank"]))
        identity_rows.append(
            {
                "identity": expected_row_identity,
                "ts_code": str(row["ts_code"]),
                "stage_transition": str(row["stage_transition"]),
                "top10_selected": selected_value,
                "promotion_rank": rank_value,
            }
        )
    expected_identity = _canonical_sha256(
        {
            "schema": "dc20_primary_d_runtime_identity_v1",
            "signal_date": date,
            "rows": identity_rows,
        }
    )
    _expect(expected_identity == runtime_identity_sha, "P0 runtime identity digest drifted")

    source_bindings = {
        "contract": {
            "path": CONTRACT_PATH.as_posix(),
            "sha256": _sha256(contract_path),
            "contract_id": CONTRACT_ID,
        },
        "primary_receipt": {
            "path": receipt_relative.as_posix(),
            "sha256": _sha256(receipt_path),
            "generation_mode": mode,
        },
        "runtime_features": {
            "path": runtime_relative.as_posix(),
            "sha256": runtime_bound_sha,
            "row_count": len(full_runtime),
            "selected_count": len(selected),
            "identity_sha256": runtime_identity_sha,
            "feature_snapshot_sha256": str(three_rank.get("feature_snapshot_sha256") or ""),
        },
        "three_rank": {
            "json_path": three_json_relative.as_posix(),
            "json_sha256": _sha256(three_json_path),
            "csv_path": three_csv_relative.as_posix(),
            "csv_sha256": _sha256(three_csv_path),
            "bundle_sha256": str(three_rank.get("bundle_sha256") or ""),
            "feature_snapshot_sha256": str(three_rank.get("feature_snapshot_sha256") or ""),
            "top10_members_sha256": expected_members,
        },
    }
    return PrimaryInputs(
        root=root,
        signal_date=date,
        generation_mode=mode,
        contract_path=contract_path,
        receipt_path=receipt_path,
        runtime_path=runtime_path,
        three_json_path=three_json_path,
        three_csv_path=three_csv_path,
        receipt=receipt,
        three_rank=three_rank,
        full_runtime=full_runtime,
        selected_runtime=selected,
        source_bindings=source_bindings,
    )


def score_single_profit(inputs: PrimaryInputs) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    loaded = load_research_only_legacy_three_engine_snapshot(
        inputs.root / SEALED_VALIDATION_PATH,
        root=inputs.root,
    )
    try:
        rows, feature_snapshot = score_legacy_profit_relative_rows(
            inputs.root,
            signal_date=inputs.signal_date,
            runtime_candidates=inputs.full_runtime,
            three_rank=inputs.three_rank,
            loaded=loaded,
        )
    except Exception as exc:
        raise PrimaryProfitRankingError("sealed single-profit research scoring failed") from exc
    _expect(feature_snapshot == inputs.three_rank.get("feature_snapshot_sha256"), "single-profit feature snapshot drifted")
    return rows, {
        "head": "profit",
        "display_name": "单一盈利排序",
        "official_status": SEALED_PROFIT_OFFICIAL_STATUS,
        "formal_ranking_ready": False,
        "formal_probability_ready": False,
        "version": SEALED_PROFIT_MODEL_VERSION,
        "model_as_of_date": SEALED_PROFIT_MODEL_AS_OF_DATE,
        "validation_gate_pass_count": SEALED_PROFIT_GATE_PASS_COUNT,
        "validation_gate_total_count": SEALED_PROFIT_GATE_TOTAL_COUNT,
        "validation_gate_score_pct": SEALED_PROFIT_GATE_SCORE_PCT,
        "score_semantics": SCORE_SEMANTICS,
        "probability_claimed": False,
        "sealed_validation_path": SEALED_VALIDATION_PATH.as_posix(),
        "sealed_validation_sha256": SEALED_VALIDATION_SHA256,
        "sealed_artifact_path": SEALED_PROFIT_ARTIFACT_PATH.as_posix(),
        "sealed_artifact_sha256": SEALED_PROFIT_ARTIFACT_SHA256,
    }


def _prepare_mixed_base(inputs: PrimaryInputs, loaded: Any, targets: pd.DataFrame) -> pd.DataFrame:
    base = inputs.selected_runtime.copy()
    _expect(len(base) == len(targets), "mixed-profit input is not the exact frozen TopN")
    required = {"signal_date", "ts_code", "stage", "board", *loaded.raw_base_features}
    _expect(required.issubset(base.columns), "mixed-profit base feature inventory is incomplete")
    base["signal_date"] = base["signal_date"].map(_normal_date)
    base["ts_code"] = base["ts_code"].map(_normal_code)
    base["stage"] = _strict_frozen_stage_numbers(base["stage"])
    base["board"] = base["board"].fillna("").astype(str).str.upper()
    _expect(base["stage"].notna().all(), "mixed-profit runtime escaped hard stage scope")
    base = _restore_hash_bound_runtime_promotion_priors(base, loaded, signal_date=inputs.signal_date)
    joined = targets.merge(
        base,
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_input"),
    )
    supplied_stage = _strict_frozen_stage_numbers(joined["stage_input"])
    _expect(supplied_stage.notna().all() and supplied_stage.eq(joined["stage"]).all(), "mixed-profit stage identity drifted")
    supplied_board = joined["board_input"].fillna("").astype(str).str.upper()
    _expect(supplied_board.eq(joined["board"]).all(), "mixed-profit board identity drifted")
    for column in loaded.raw_base_features:
        original = joined[column]
        numeric = pd.to_numeric(original, errors="coerce").replace([np.inf, -np.inf], np.nan)
        invalid = original.notna() & original.astype(str).str.strip().ne("") & numeric.isna()
        _expect(not invalid.any(), f"mixed-profit feature is nonnumeric: {column}")
        _expect(numeric.notna().any(), f"mixed-profit feature is entirely missing: {column}")
        joined[column] = numeric
    joined["stage_2"] = joined["stage"].eq(2).astype(float)
    joined["stage_3"] = joined["stage"].eq(3).astype(float)
    joined["board_sh_main"] = joined["board"].eq("SH_MAIN").astype(float)
    joined["board_sz_main"] = joined["board"].eq("SZ_MAIN").astype(float)
    return joined.sort_values("promotion_rank", kind="stable").reset_index(drop=True)


def score_mixed_profit(inputs: PrimaryInputs) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if inputs.selected_runtime.empty:
        return [], {
            "status": INTERNAL_STATUS,
            "artifact_status": ARTIFACT_STATUS,
            "artifact_sha256": EXPECTED_MODEL_SHA256,
            "feature_columns_sha256": EXPECTED_ALL_FEATURES_SHA256,
            "feature_count": 156,
            "model_loaded": False,
            "inference_performed": False,
            "empty_event_reason": "P0_FROZEN_TOPN_EMPTY",
        }
    try:
        loaded = load_internal_challenger(inputs.root)
        open_dates = _read_pinned_sse_open_dates(inputs.root)
        targets = _strict_top10_targets(inputs.three_rank, open_dates)
        full_snapshot = _promotion_feature_snapshot_sha256(
            inputs.full_runtime,
            loaded,
            signal_date=inputs.signal_date,
        )
        _expect(full_snapshot == inputs.three_rank.get("feature_snapshot_sha256"), "mixed-profit P0 feature snapshot cannot be reproduced")
        prepared = _prepare_mixed_base(inputs, loaded, targets)
        history = pd.read_csv(
            inputs.root / DEFAULT_HISTORY_LEDGER_PATH,
            low_memory=False,
        )
        priors = build_strict_lagged_priors(
            history=history,
            targets=targets,
            open_dates=open_dates,
            lagged_module=loaded.lagged_priors,
        )
    except (OSError, ValueError, KeyError, ExecutableProfitShadowError) as exc:
        raise PrimaryProfitRankingError("mixed-profit research feature preparation failed") from exc
    prior_columns = [
        "signal_date",
        "ts_code",
        "lagged_prior_max_history_exit_date",
        "lagged_prior_snapshot_sha256",
        *loaded.lagged_features,
    ]
    frame = prepared.merge(
        priors[prior_columns],
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    ).sort_values("promotion_rank", kind="stable").reset_index(drop=True)
    _expect(
        len(frame) == len(targets)
        and frame[list(loaded.lagged_features)].notna().all().all(),
        "mixed-profit lagged-prior join is incomplete",
    )
    availability = frame["lagged_prior_max_history_exit_date"].fillna("").astype(str)
    _expect(
        availability.str.fullmatch(r"20\d{6}").all()
        and availability.isin(open_dates).all()
        and availability.lt(inputs.signal_date).all(),
        "mixed-profit priors include same-day or future outcome truth",
    )
    feature_columns = list(loaded.feature_columns)
    _expect(len(feature_columns) == 156, "mixed-profit challenger feature count drifted")
    x = frame[feature_columns]
    fill = np.asarray(loaded.bundle["fill_model"].predict_proba(x)[:, 1], dtype=float)
    conditional = np.asarray(
        loaded.bundle["conditional_profit_model"].predict_proba(x)[:, 1],
        dtype=float,
    )
    _expect(
        fill.shape == conditional.shape == (len(frame),)
        and np.isfinite(fill).all()
        and np.isfinite(conditional).all(),
        "mixed-profit challenger emitted invalid scores",
    )
    fill = np.clip(fill, 0.0, 1.0)
    conditional = np.clip(conditional, 0.0, 1.0)
    joint = fill * conditional
    order = sorted(
        range(len(frame)),
        key=lambda index: (
            -float(joint[index]),
            -float(conditional[index]),
            -float(fill[index]),
            str(frame.iloc[index]["ts_code"]),
        ),
    )
    order_by_source = {source: rank for rank, source in enumerate(order, start=1)}
    group_sizes: dict[float, int] = {}
    for value in joint:
        group_sizes[float(value)] = group_sizes.get(float(value), 0) + 1
    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        value = float(joint[index])
        rows.append(
            {
                "ts_code": str(row["ts_code"]),
                "name": str(row["name"]),
                "industry": str(row["industry"]),
                "stage_transition": str(row["stage_transition"]),
                "promotion_rank": int(row["promotion_rank"]),
                "predicted_promotion_probability": float(row["predicted_promotion_probability"]),
                "executable_profit_research_rank": int(order_by_source[index]),
                "estimated_executable_profit_probability": value,
                "research_joint_proxy_score": value,
                "research_fill_proxy_score": float(fill[index]),
                "research_conditional_profit_score": float(conditional[index]),
                "rank_tied": group_sizes[value] > 1,
                "rank_group_size": group_sizes[value],
            }
        )
    rows.sort(key=lambda row: int(row["executable_profit_research_rank"]))
    generated_at = str(inputs.full_runtime["generated_at_utc"].iloc[0])
    mixed_snapshot = _mixed_feature_snapshot_sha256(
        frame,
        feature_columns,
        source_file_sha256=_sha256(inputs.runtime_path),
        generated_at_utc=generated_at,
    )
    training = loaded.bundle.get("training_audit") or {}
    return rows, {
        "status": INTERNAL_STATUS,
        "artifact_status": ARTIFACT_STATUS,
        "artifact_sha256": EXPECTED_MODEL_SHA256,
        "feature_columns_sha256": EXPECTED_ALL_FEATURES_SHA256,
        "feature_count": 156,
        "model_loaded": True,
        "inference_performed": True,
        "calibrated_probability_output": False,
        "retrospective_confirmation_window_was_viewed": True,
        "front_end_formal_rank_allowed": False,
        "maximum_used_scheduled_exit_date": str(training.get("maximum_used_scheduled_exit_date") or ""),
        "lagged_prior_max_history_exit_date": max(availability.tolist()),
        "mixed_feature_snapshot_sha256": mixed_snapshot,
        "empty_event_reason": None,
    }


def _base_payload(inputs: PrimaryInputs, *, display_name: str) -> dict[str, Any]:
    mode = inputs.generation_mode
    return {
        "contract_id": CONTRACT_ID,
        "contract_file_sha256": _sha256(inputs.contract_path),
        "status": MODE_STATUS[mode],
        "display_name": display_name,
        "generation_mode": mode,
        "prospective": mode == "NATURAL",
        "retrospective_non_forward": mode == "RETROSPECTIVE_RECOVERY",
        "research_only": True,
        "signal_date": inputs.signal_date,
        "exec_date": str(inputs.three_rank["exec_date"]),
        "exit_date": str(inputs.three_rank["exit_date"]),
        "candidate_count": len(inputs.selected_runtime),
        "top10_members_sha256": str(inputs.three_rank["top10_members_sha256"]),
        "source_bundle_sha256": str(inputs.three_rank["bundle_sha256"]),
        "source_feature_snapshot_sha256": str(inputs.three_rank["feature_snapshot_sha256"]),
        "source_bindings": copy.deepcopy(dict(inputs.source_bindings)),
        "boundaries": dict(BOUNDARIES),
    }


def build_single_projection(
    inputs: PrimaryInputs,
    *,
    scorer: Callable[[PrimaryInputs], tuple[list[dict[str, Any]], dict[str, Any]]] = score_single_profit,
) -> dict[str, Any]:
    rows, model = scorer(inputs)
    payload = _base_payload(inputs, display_name="单一盈利排序")
    payload.update(
        {
            "schema_version": SINGLE_SCHEMA,
            "artifact_kind": SINGLE_KIND,
            "score_semantics": "sealed raw model score; relative order only; not probability",
            "model": model,
            "ranking_contract": {
                "candidate_scope": "exact P0 frozen promotion TopN only",
                "candidate_count_rule": "show exactly N for 0<=N<=10; never pad",
                "score_direction": "higher raw score is better relative order",
                "tie_policy": "equal raw score equal dense rank; promotion rank only orders display inside ties",
                "membership_or_promotion_rank_may_change": False,
                "formal_profit_fields_may_change": False,
                "trade_or_action_may_change": False,
            },
            "rows": rows,
        }
    )
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_single_projection(payload)
    return payload


def build_mixed_projection(
    inputs: PrimaryInputs,
    *,
    scorer: Callable[[PrimaryInputs], tuple[list[dict[str, Any]], dict[str, Any]]] = score_mixed_profit,
) -> dict[str, Any]:
    rows, model = scorer(inputs)
    payload = _base_payload(inputs, display_name="混合盈利排序")
    payload.update(
        {
            "schema_version": MIXED_SCHEMA,
            "artifact_kind": MIXED_KIND,
            "score_semantics": {
                "research_fill_proxy_score": "historical daily-bar buyability proxy; not actual fill probability",
                "research_conditional_profit_score": "uncalibrated conditional profit research score",
                "research_joint_proxy_score": "exact product of the two uncalibrated proxy scores",
                "estimated_executable_profit_probability": "display alias for the uncalibrated joint proxy; not formal probability",
            },
            "model": model,
            "ranking_contract": {
                "candidate_scope": "exact P0 frozen promotion TopN only",
                "candidate_count_rule": "show exactly N for 0<=N<=10; never pad",
                "primary_sort": "research_joint_proxy_score descending",
                "tie_breakers": [
                    "research_conditional_profit_score descending",
                    "research_fill_proxy_score descending",
                    "ts_code ascending",
                ],
                "membership_or_promotion_rank_may_change": False,
                "forward_selection_or_statistics_may_change": False,
                "trade_or_action_may_change": False,
            },
            "rows": rows,
        }
    )
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_mixed_projection(payload)
    return payload


def _validate_common(payload: Mapping[str, Any], *, schema: str, kind: str) -> list[Mapping[str, Any]]:
    _expect(payload.get("schema_version") == schema and payload.get("artifact_kind") == kind, "P1 projection identity drifted")
    mode = str(payload.get("generation_mode") or "")
    _expect(mode in MODES and payload.get("status") == MODE_STATUS[mode], "P1 projection generation status drifted")
    _expect(
        payload.get("prospective") is (mode == "NATURAL")
        and payload.get("retrospective_non_forward") is (mode == "RETROSPECTIVE_RECOVERY")
        and payload.get("research_only") is True,
        "P1 projection prospective/retrospective disclosure drifted",
    )
    _expect(payload.get("contract_id") == CONTRACT_ID and SHA256_RE.fullmatch(str(payload.get("contract_file_sha256") or "")) is not None, "P1 projection contract binding invalid")
    date = str(payload.get("signal_date") or "")
    _expect(DATE_RE.fullmatch(date) is not None and date < str(payload.get("exec_date") or "") < str(payload.get("exit_date") or ""), "P1 projection dates invalid")
    rows = payload.get("rows")
    _expect(isinstance(rows, list) and 0 <= len(rows) <= 10 and payload.get("candidate_count") == len(rows), "P1 projection row count invalid")
    _expect(payload.get("boundaries") == BOUNDARIES, "P1 projection safety boundaries drifted")
    for field in ("top10_members_sha256", "source_bundle_sha256", "source_feature_snapshot_sha256", "snapshot_sha256"):
        _expect(SHA256_RE.fullmatch(str(payload.get(field) or "")) is not None, f"P1 projection {field} invalid")
    bindings = payload.get("source_bindings")
    _expect(isinstance(bindings, Mapping) and set(bindings) == {"contract", "primary_receipt", "runtime_features", "three_rank"}, "P1 source bindings drifted")
    runtime = bindings.get("runtime_features")
    three = bindings.get("three_rank")
    receipt = bindings.get("primary_receipt")
    _expect(
        isinstance(runtime, Mapping)
        and runtime.get("path") == f"outputs/decision/primary_d_runtime_features_{date}.csv"
        and runtime.get("selected_count") == len(rows)
        and runtime.get("feature_snapshot_sha256") == payload.get("source_feature_snapshot_sha256")
        and isinstance(three, Mapping)
        and three.get("json_path") == f"outputs/decision/three_rank_top10_{date}.json"
        and three.get("csv_path") == f"outputs/decision/three_rank_top10_{date}.csv"
        and three.get("bundle_sha256") == payload.get("source_bundle_sha256")
        and three.get("feature_snapshot_sha256") == payload.get("source_feature_snapshot_sha256")
        and three.get("top10_members_sha256") == payload.get("top10_members_sha256")
        and isinstance(receipt, Mapping)
        and receipt.get("path") == f"outputs/decision/primary_d_receipt_{date}.json"
        and receipt.get("generation_mode") == mode,
        "P1 source exact-D/fingerprint binding drifted",
    )
    for binding in (runtime, three, receipt, bindings.get("contract")):
        _expect(
            isinstance(binding, Mapping)
            and all(
                SHA256_RE.fullmatch(str(value or "")) is not None
                for key, value in binding.items()
                if key.endswith("sha256")
            ),
            "P1 source binding SHA invalid",
        )
    _expect(payload.get("snapshot_sha256") == _payload_snapshot(payload), "P1 projection snapshot SHA drifted")
    downloads = payload.get("downloads")
    if downloads is not None:
        _expect(
            isinstance(downloads, Mapping)
            and downloads.get("row_count") == len(rows)
            and SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or "")) is not None,
            "P1 projection downloads invalid",
        )
    return rows


def validate_single_projection(payload: Mapping[str, Any]) -> None:
    rows = _validate_common(payload, schema=SINGLE_SCHEMA, kind=SINGLE_KIND)
    codes: list[str] = []
    scores: list[float] = []
    for row in rows:
        _expect(isinstance(row, Mapping) and set(row) == set(SINGLE_ROW_FIELDS), "single-profit row surface drifted")
        code = _normal_code(row.get("ts_code"))
        score = _finite(row.get("legacy_profit_raw_score"))
        _expect(code and score is not None and 0 <= score <= 1, "single-profit row score invalid")
        _expect(type(row.get("promotion_rank")) is int and type(row.get("legacy_profit_relative_rank")) is int, "single-profit ranks invalid")
        codes.append(code)
        scores.append(score)
    _expect(len(codes) == len(set(codes)) and top10_members_sha256(str(payload["signal_date"]), codes) == payload.get("top10_members_sha256"), "single-profit membership drifted")
    expected = sorted(rows, key=lambda row: (int(row["legacy_profit_relative_rank"]), int(row["promotion_rank"])))
    _expect(rows == expected, "single-profit rows are not in relative-rank order")


def validate_mixed_projection(payload: Mapping[str, Any]) -> None:
    rows = _validate_common(payload, schema=MIXED_SCHEMA, kind=MIXED_KIND)
    codes: list[str] = []
    promotion_ranks: list[int] = []
    for position, row in enumerate(rows, start=1):
        _expect(isinstance(row, Mapping) and set(row) == set(MIXED_ROW_FIELDS), "mixed-profit row surface drifted")
        code = _normal_code(row.get("ts_code"))
        _expect(code and row.get("stage_transition") in {"2→3", "3→4"}, "mixed-profit row identity invalid")
        _expect(type(row.get("promotion_rank")) is int and row.get("executable_profit_research_rank") == position, "mixed-profit ranks invalid")
        for field in ("predicted_promotion_probability", "estimated_executable_profit_probability", "research_joint_proxy_score", "research_fill_proxy_score", "research_conditional_profit_score"):
            value = _finite(row.get(field))
            _expect(value is not None and 0 <= value <= 1, f"mixed-profit {field} invalid")
        _expect(math.isclose(float(row["research_joint_proxy_score"]), float(row["research_fill_proxy_score"]) * float(row["research_conditional_profit_score"]), rel_tol=0.0, abs_tol=1e-15), "mixed-profit joint proxy identity drifted")
        _expect(math.isclose(float(row["estimated_executable_profit_probability"]), float(row["research_joint_proxy_score"]), rel_tol=0.0, abs_tol=1e-15), "mixed-profit display alias drifted")
        _expect(isinstance(row.get("rank_tied"), bool) and type(row.get("rank_group_size")) is int and row["rank_group_size"] >= 1, "mixed-profit tie disclosure invalid")
        codes.append(code)
        promotion_ranks.append(int(row["promotion_rank"]))
    _expect(len(codes) == len(set(codes)) and top10_members_sha256(str(payload["signal_date"]), codes) == payload.get("top10_members_sha256"), "mixed-profit membership drifted")
    _expect(sorted(promotion_ranks) == list(range(1, len(rows) + 1)), "mixed-profit changed independent promotion ranks")


def _csv_bytes(payload: Mapping[str, Any], fields: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows(payload.get("rows") or [])
    return buffer.getvalue().encode("utf-8")


def _daily_mixed_top2_entry_snapshot(entry: Mapping[str, Any]) -> str:
    copied = copy.deepcopy(dict(entry))
    copied.pop("entry_sha256", None)
    return _canonical_sha256(copied)


def _daily_mixed_top2_mode_fields(mode: str) -> tuple[str, str]:
    if mode == "NATURAL":
        return "NATURAL_RANK_SNAPSHOT", "REQUIRES_EXACT_NATURAL_FORWARD_FREEZE"
    _expect(mode == "RETROSPECTIVE_RECOVERY", "daily mixed Top2 mode is invalid")
    return "RETROSPECTIVE_RECOVERY_AUDIT_ONLY", "EXCLUDED_RETROSPECTIVE_RECOVERY"


def _build_daily_mixed_top2_entry(
    root: Path,
    projection: Mapping[str, Any],
    *,
    json_path: Path,
    csv_path: Path,
) -> dict[str, Any]:
    validate_mixed_projection(projection)
    date = str(projection["signal_date"])
    mode = str(projection["generation_mode"])
    record_class, statistics_policy = _daily_mixed_top2_mode_fields(mode)
    candidate_count = int(projection["candidate_count"])
    selected = list(projection.get("rows") or [])[: min(2, candidate_count)]
    rows = [
        {
            "slot": position,
            "ts_code": str(row["ts_code"]),
            "name": str(row["name"]),
            "industry": str(row["industry"]),
            "stage_transition": str(row["stage_transition"]),
            "promotion_rank": int(row["promotion_rank"]),
            "mixed_profit_rank": int(row["executable_profit_research_rank"]),
        }
        for position, row in enumerate(selected, start=1)
    ]
    entry = {
        "signal_date": date,
        "exec_date": str(projection["exec_date"]),
        "exit_date": str(projection["exit_date"]),
        "generation_mode": mode,
        "prospective": projection["prospective"],
        "retrospective_non_forward": projection["retrospective_non_forward"],
        "record_class": record_class,
        "forward_statistics_policy": statistics_policy,
        "candidate_count": candidate_count,
        "recorded_slots": len(rows),
        "projection_json_url": json_path.relative_to(root).as_posix(),
        "projection_json_sha256": _sha256(json_path),
        "projection_csv_url": csv_path.relative_to(root).as_posix(),
        "projection_csv_sha256": _sha256(csv_path),
        "projection_snapshot_sha256": str(projection["snapshot_sha256"]),
        "top10_members_sha256": str(projection["top10_members_sha256"]),
        "source_bundle_sha256": str(projection["source_bundle_sha256"]),
        "source_feature_snapshot_sha256": str(
            projection["source_feature_snapshot_sha256"]
        ),
        "rows": rows,
    }
    entry["entry_sha256"] = _daily_mixed_top2_entry_snapshot(entry)
    validate_daily_mixed_top2_entry(entry)
    return entry


def validate_daily_mixed_top2_entry(entry: Mapping[str, Any]) -> None:
    _expect(
        isinstance(entry, Mapping) and set(entry) == DAILY_MIXED_TOP2_ENTRY_KEYS,
        "daily mixed Top2 entry surface drifted",
    )
    date = str(entry.get("signal_date") or "")
    exec_date = str(entry.get("exec_date") or "")
    exit_date = str(entry.get("exit_date") or "")
    _expect(
        DATE_RE.fullmatch(date) is not None
        and date >= DAILY_MIXED_TOP2_START_DATE
        and DATE_RE.fullmatch(exec_date) is not None
        and DATE_RE.fullmatch(exit_date) is not None
        and date < exec_date < exit_date,
        "daily mixed Top2 D/T/T+1 is invalid",
    )
    mode = str(entry.get("generation_mode") or "")
    record_class, statistics_policy = _daily_mixed_top2_mode_fields(mode)
    _expect(
        entry.get("prospective") is (mode == "NATURAL")
        and entry.get("retrospective_non_forward")
        is (mode == "RETROSPECTIVE_RECOVERY")
        and entry.get("record_class") == record_class
        and entry.get("forward_statistics_policy") == statistics_policy,
        "daily mixed Top2 mode disclosure drifted",
    )
    candidate_count = entry.get("candidate_count")
    recorded_slots = entry.get("recorded_slots")
    rows = entry.get("rows")
    _expect(
        type(candidate_count) is int
        and 0 <= candidate_count <= 10
        and type(recorded_slots) is int
        and recorded_slots == min(2, candidate_count)
        and isinstance(rows, list)
        and len(rows) == recorded_slots,
        "daily mixed Top2 row count or no-padding rule drifted",
    )
    _expect(
        entry.get("projection_json_url")
        == f"{MIXED_ROOT.as_posix()}/projection_{date}.json"
        and entry.get("projection_csv_url")
        == f"{MIXED_ROOT.as_posix()}/projection_{date}.csv",
        "daily mixed Top2 exact-D projection path drifted",
    )
    for field in (
        "projection_json_sha256",
        "projection_csv_sha256",
        "projection_snapshot_sha256",
        "top10_members_sha256",
        "source_bundle_sha256",
        "source_feature_snapshot_sha256",
        "entry_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(entry.get(field) or "")) is not None,
            f"daily mixed Top2 {field} is invalid",
        )
    seen: set[str] = set()
    for position, row in enumerate(rows, start=1):
        _expect(
            isinstance(row, Mapping) and set(row) == DAILY_MIXED_TOP2_ROW_KEYS,
            "daily mixed Top2 row surface drifted",
        )
        code = _normal_code(row.get("ts_code"))
        _expect(
            row.get("slot") == position
            and row.get("mixed_profit_rank") == position
            and code
            and code not in seen
            and isinstance(row.get("name"), str)
            and bool(str(row.get("name") or "").strip())
            and isinstance(row.get("industry"), str)
            and row.get("stage_transition") in {"2→3", "3→4"}
            and type(row.get("promotion_rank")) is int
            and 1 <= int(row["promotion_rank"]) <= candidate_count,
            "daily mixed Top2 row identity or rank drifted",
        )
        seen.add(code)
    _expect(
        entry.get("entry_sha256") == _daily_mixed_top2_entry_snapshot(entry),
        "daily mixed Top2 entry SHA drifted",
    )


def _validate_daily_mixed_top2_index_shape(index: Mapping[str, Any]) -> None:
    _expect(
        isinstance(index, Mapping) and set(index) == DAILY_MIXED_TOP2_INDEX_KEYS,
        "daily mixed Top2 index surface drifted",
    )
    entries = index.get("entries")
    dates = index.get("recorded_signal_dates")
    _expect(
        index.get("schema_version") == DAILY_MIXED_TOP2_SCHEMA
        and index.get("index_kind") == DAILY_MIXED_TOP2_KIND
        and index.get("data_alias") is False
        and index.get("display_name") == DAILY_MIXED_TOP2_DISPLAY_NAME
        and index.get("public_start_signal_date") == DAILY_MIXED_TOP2_START_DATE
        and index.get("boundaries") == DAILY_MIXED_TOP2_BOUNDARIES
        and isinstance(entries, list)
        and isinstance(dates, list),
        "daily mixed Top2 index identity or boundaries drifted",
    )
    for entry in entries:
        validate_daily_mixed_top2_entry(entry)
    expected_dates = [str(entry["signal_date"]) for entry in entries]
    _expect(
        expected_dates == sorted(set(expected_dates))
        and dates == expected_dates
        and index.get("recorded_days") == len(entries)
        and index.get("recorded_slots")
        == sum(int(entry["recorded_slots"]) for entry in entries)
        and index.get("latest_signal_date")
        == (expected_dates[-1] if expected_dates else None),
        "daily mixed Top2 index ordering or totals drifted",
    )
    _expect(
        SHA256_RE.fullmatch(str(index.get("snapshot_sha256") or "")) is not None
        and index.get("snapshot_sha256") == _payload_snapshot(index),
        "daily mixed Top2 index snapshot SHA drifted",
    )


def _validate_daily_mixed_top2_source_bytes(
    root: Path,
    projection: Mapping[str, Any],
) -> None:
    bindings = projection["source_bindings"]
    date = str(projection["signal_date"])
    receipt_binding = bindings["primary_receipt"]
    runtime_binding = bindings["runtime_features"]
    three_binding = bindings["three_rank"]
    receipt_path = _safe_file(
        root,
        Path(str(receipt_binding["path"])),
        label=f"daily mixed Top2 P0 receipt {date}",
    )
    runtime_path = _safe_file(
        root,
        Path(str(runtime_binding["path"])),
        label=f"daily mixed Top2 P0 runtime {date}",
    )
    three_json_path = _safe_file(
        root,
        Path(str(three_binding["json_path"])),
        label=f"daily mixed Top2 three-rank JSON {date}",
    )
    three_csv_path = _safe_file(
        root,
        Path(str(three_binding["csv_path"])),
        label=f"daily mixed Top2 three-rank CSV {date}",
    )
    _expect(
        _sha256(receipt_path) == receipt_binding["sha256"]
        and _sha256(runtime_path) == runtime_binding["sha256"]
        and _sha256(three_json_path) == three_binding["json_sha256"]
        and _sha256(three_csv_path) == three_binding["csv_sha256"],
        "daily mixed Top2 source bytes drifted",
    )
    receipt = _read_json(receipt_path, label=f"daily mixed Top2 P0 receipt {date}")
    outputs = receipt.get("outputs")
    _expect(
        receipt.get("schema_version") == "dc20_primary_d_receipt_v1"
        and receipt.get("primary_status") == "READY"
        and receipt.get("signal_date") == date
        and receipt.get("exec_date") == projection.get("exec_date")
        and receipt.get("exit_date") == projection.get("exit_date")
        and receipt.get("generation_mode") == projection.get("generation_mode")
        and receipt.get("action_authorized") is False
        and receipt.get("action_input_consumed") is False
        and int(receipt.get("formal_trade_count") or 0) == 0
        and isinstance(outputs, Mapping)
        and outputs.get("runtime_features_path") == runtime_binding["path"]
        and outputs.get("runtime_features_sha256") == runtime_binding["sha256"]
        and outputs.get("runtime_feature_row_count") == runtime_binding["row_count"]
        and outputs.get("runtime_selected_count") == runtime_binding["selected_count"]
        and outputs.get("runtime_identity_sha256") == runtime_binding["identity_sha256"]
        and outputs.get("json_sha256") == three_binding["json_sha256"]
        and outputs.get("csv_sha256") == three_binding["csv_sha256"]
        and outputs.get("bundle_sha256") == projection.get("source_bundle_sha256")
        and outputs.get("top10_members_sha256") == projection.get("top10_members_sha256"),
        "daily mixed Top2 P0 receipt binding drifted",
    )
    try:
        runtime_rows = list(
            csv.DictReader(io.StringIO(runtime_path.read_text(encoding="utf-8-sig")))
        )
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PrimaryProfitRankingError("daily mixed Top2 P0 runtime is invalid") from exc
    selected_runtime = sorted(
        (row for row in runtime_rows if row.get("top10_selected") == "1"),
        key=lambda row: int(row.get("promotion_rank") or 0),
    )
    _expect(
        len(runtime_rows) == runtime_binding["row_count"]
        and len(selected_runtime) == runtime_binding["selected_count"]
        == projection.get("candidate_count")
        and all(
            row.get("signal_date") == date
            and row.get("identity")
            == f"{date}|{row.get('ts_code')}|{row.get('stage_transition')}"
            and row.get("feature_snapshot_sha256")
            == projection.get("source_feature_snapshot_sha256")
            for row in runtime_rows
        ),
        "daily mixed Top2 P0 runtime identity drifted",
    )
    three_rank = _read_json(
        three_json_path,
        label=f"daily mixed Top2 three-rank JSON {date}",
    )
    try:
        validate_three_rank_contract(three_rank)
    except Exception as exc:
        raise PrimaryProfitRankingError(
            "daily mixed Top2 three-rank contract is invalid"
        ) from exc
    _expect(
        three_rank.get("signal_date") == date
        and three_rank.get("exec_date") == projection.get("exec_date")
        and three_rank.get("exit_date") == projection.get("exit_date")
        and three_rank.get("bundle_sha256") == projection.get("source_bundle_sha256")
        and three_rank.get("feature_snapshot_sha256")
        == projection.get("source_feature_snapshot_sha256")
        and three_rank.get("top10_members_sha256")
        == projection.get("top10_members_sha256"),
        "daily mixed Top2 three-rank binding drifted",
    )
    frozen_by_code = {str(row["ts_code"]): row for row in three_rank["rows"]}
    _expect(
        len(frozen_by_code) == projection.get("candidate_count")
        and set(frozen_by_code) == {str(row["ts_code"]) for row in projection["rows"]}
        and all(
            frozen_by_code[str(row["ts_code"])].get("promotion_rank")
            == row.get("promotion_rank")
            and frozen_by_code[str(row["ts_code"])].get("stage_transition")
            == row.get("stage_transition")
            for row in projection["rows"]
        ),
        "daily mixed Top2 projection changed the frozen promotion membership or rank",
    )


def _discover_daily_mixed_top2_projection_paths(
    root: Path,
) -> dict[str, tuple[Path, Path]]:
    root = root.resolve(strict=True)
    output = root / MIXED_ROOT
    if not output.exists():
        return {}
    _expect(output.is_dir() and not output.is_symlink(), "daily mixed Top2 root is unsafe")
    json_paths = {
        match.group(1): path
        for path in sorted(output.glob("projection_20??????.json"))
        if (match := re.fullmatch(r"projection_(20\d{6})\.json", path.name))
        and match.group(1) >= DAILY_MIXED_TOP2_START_DATE
    }
    csv_paths = {
        match.group(1): path
        for path in sorted(output.glob("projection_20??????.csv"))
        if (match := re.fullmatch(r"projection_(20\d{6})\.csv", path.name))
        and match.group(1) >= DAILY_MIXED_TOP2_START_DATE
    }
    _expect(
        set(json_paths) == set(csv_paths),
        "daily mixed Top2 projection JSON/CSV inventory is partial",
    )
    pairs: dict[str, tuple[Path, Path]] = {}
    for date in sorted(json_paths):
        json_path = _safe_file(
            root,
            MIXED_ROOT / f"projection_{date}.json",
            label=f"daily mixed Top2 projection JSON {date}",
        )
        csv_path = _safe_file(
            root,
            MIXED_ROOT / f"projection_{date}.csv",
            label=f"daily mixed Top2 projection CSV {date}",
        )
        _expect(
            json_path == json_paths[date] and csv_path == csv_paths[date],
            "daily mixed Top2 projection path identity drifted",
        )
        pairs[date] = (json_path, csv_path)
    return pairs


def _build_daily_mixed_top2_entry_from_paths(
    root: Path,
    date: str,
    json_path: Path,
    csv_path: Path,
    *,
    validate_source_chain: bool = True,
) -> dict[str, Any]:
    projection = _read_json(json_path, label=f"daily mixed Top2 projection {date}")
    validate_mixed_projection(projection)
    _expect(
        projection.get("signal_date") == date,
        "daily mixed Top2 projection filename date drifted",
    )
    _expect(
        csv_path.read_bytes() == _csv_bytes(projection, MIXED_ROW_FIELDS),
        "daily mixed Top2 projection CSV bytes drifted",
    )
    if validate_source_chain:
        _validate_daily_mixed_top2_source_bytes(root, projection)
    return _build_daily_mixed_top2_entry(
        root,
        projection,
        json_path=json_path,
        csv_path=csv_path,
    )


def _build_daily_mixed_top2_index(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    copied_entries = [copy.deepcopy(dict(entry)) for entry in entries]
    dates = [str(entry["signal_date"]) for entry in copied_entries]
    index = {
        "schema_version": DAILY_MIXED_TOP2_SCHEMA,
        "index_kind": DAILY_MIXED_TOP2_KIND,
        "data_alias": False,
        "display_name": DAILY_MIXED_TOP2_DISPLAY_NAME,
        "public_start_signal_date": DAILY_MIXED_TOP2_START_DATE,
        "latest_signal_date": dates[-1] if dates else None,
        "recorded_signal_dates": dates,
        "recorded_days": len(copied_entries),
        "recorded_slots": sum(int(entry["recorded_slots"]) for entry in copied_entries),
        "entries": copied_entries,
        "boundaries": copy.deepcopy(DAILY_MIXED_TOP2_BOUNDARIES),
    }
    index["snapshot_sha256"] = _payload_snapshot(index)
    _validate_daily_mixed_top2_index_shape(index)
    return index


def validate_primary_profit_daily_mixed_top2_index(
    root: Path,
    *,
    expected_signal_date: str | None = None,
    require_all_projection_sources: bool = True,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    index_path = _safe_file(root, DAILY_MIXED_TOP2_PATH, label="daily mixed Top2 index")
    index = _read_json(index_path, label="daily mixed Top2 index")
    _validate_daily_mixed_top2_index_shape(index)
    entries_by_date = {str(entry["signal_date"]): entry for entry in index["entries"]}
    projection_paths = _discover_daily_mixed_top2_projection_paths(root)
    if require_all_projection_sources:
        _expect(
            set(entries_by_date) == set(projection_paths),
            "daily mixed Top2 ledger/source projection inventory drifted",
        )
    for date, (json_path, csv_path) in projection_paths.items():
        entry = entries_by_date.get(date)
        _expect(entry is not None, "daily mixed Top2 index missed a dated projection")
        _expect(
            entry
            == _build_daily_mixed_top2_entry_from_paths(
                root,
                date,
                json_path,
                csv_path,
                validate_source_chain=False,
            ),
            "daily mixed Top2 historical entry drifted from its dated projection",
        )
    if expected_signal_date is not None and expected_signal_date >= DAILY_MIXED_TOP2_START_DATE:
        _expect(
            index.get("latest_signal_date") == expected_signal_date,
            "daily mixed Top2 index latest D drifted",
        )
        _expect(
            expected_signal_date in projection_paths,
            "daily mixed Top2 current D projection is missing",
        )
        _expect(
            entries_by_date[expected_signal_date]
            == _build_daily_mixed_top2_entry_from_paths(
                root,
                expected_signal_date,
                *projection_paths[expected_signal_date],
            ),
            "daily mixed Top2 current D entry is not bound to the complete P0/P1 chain",
        )
    return index


def materialize_primary_profit_daily_mixed_top2_index(root: Path) -> tuple[Path, dict[str, Any]]:
    root = root.resolve(strict=True)
    _ensure_directory(root, MIXED_ROOT)
    path = root / DAILY_MIXED_TOP2_PATH
    old_entries: list[dict[str, Any]] = []
    if path.exists():
        _expect(path.is_file() and not path.is_symlink(), "daily mixed Top2 index path is unsafe")
        existing = _read_json(path, label="existing daily mixed Top2 index")
        _validate_daily_mixed_top2_index_shape(existing)
        old_entries = copy.deepcopy(list(existing.get("entries") or []))
    entries_by_date = {str(entry["signal_date"]): entry for entry in old_entries}
    projection_paths = _discover_daily_mixed_top2_projection_paths(root)
    for date, paths in projection_paths.items():
        if date in entries_by_date:
            entry = entries_by_date[date]
            _expect(
                entry["projection_json_sha256"] == _sha256(paths[0])
                and entry["projection_csv_sha256"] == _sha256(paths[1]),
                "daily mixed Top2 history rewrite rejected",
            )
            continue
        entries_by_date[date] = _build_daily_mixed_top2_entry_from_paths(
            root,
            date,
            *paths,
        )
    entries = [entries_by_date[date] for date in sorted(entries_by_date)]
    index = _build_daily_mixed_top2_index(entries)
    rebuilt_by_date = {str(entry["signal_date"]): entry for entry in index["entries"]}
    _expect(
        all(rebuilt_by_date.get(str(entry["signal_date"])) == entry for entry in old_entries),
        "daily mixed Top2 history rewrite rejected",
    )
    _atomic_write(path, _pretty_json_bytes(index))
    validate_primary_profit_daily_mixed_top2_index(
        root,
        expected_signal_date=str(index["latest_signal_date"] or ""),
    )
    return path, index


def validate_primary_profit_index_chain(
    root: Path,
    output_root: Path,
    *,
    index_schema: str,
    row_fields: Sequence[str],
    validator: Callable[[Mapping[str, Any]], None],
    expected_signal_date: str | None = None,
    expected_generation_mode: str | None = None,
) -> dict[str, Any]:
    """Validate one complete P1 pointer/projection/CSV/P0-lineage chain.

    This is the shared acceptance contract used by both the P1 owner workflow
    and the generic Pages deployer.  It deliberately re-runs
    :func:`load_primary_inputs` against the supplied root so a copied/public
    projection cannot pass without its exact P0 receipt, runtime CSV,
    three-rank bundle, contract, and hashes.
    """

    root = root.resolve(strict=True)
    index_relative = output_root / "index.json"
    index_path = _safe_file(root, index_relative, label="P1 research index")
    index = _read_json(index_path, label="P1 research index")
    _expect(set(index) == PRIMARY_INDEX_KEYS, "public P1 index surface drifted")

    signal_date = str(index.get("latest_signal_date") or "")
    generation_mode = str(index.get("generation_mode") or "")
    if expected_signal_date is not None:
        _expect(signal_date == expected_signal_date, "public P1 signal date drifted")
    if expected_generation_mode is not None:
        _expect(
            generation_mode == expected_generation_mode,
            "public P1 generation mode drifted",
        )
    inputs = load_primary_inputs(root, signal_date, generation_mode)

    json_relative = output_root / f"projection_{signal_date}.json"
    csv_relative = output_root / f"projection_{signal_date}.csv"
    json_path = _safe_file(root, json_relative, label="P1 projection JSON")
    csv_path = _safe_file(root, csv_relative, label="P1 projection CSV")
    projection = _read_json(json_path, label="P1 projection JSON")
    validator(projection)

    expected_display_name = {
        SINGLE_INDEX_SCHEMA: "单一盈利排序",
        MIXED_INDEX_SCHEMA: "混合盈利排序",
    }.get(index_schema)
    _expect(expected_display_name is not None, "unsupported P1 index schema")
    _expect(
        index["schema_version"] == index_schema
        and index["index_kind"] == "dated_primary_profit_research_pointer_only"
        and index["data_alias"] is False
        and index["display_name"] == projection.get("display_name") == expected_display_name
        and index["status"] == MODE_STATUS[generation_mode]
        and index["generation_mode"] == generation_mode
        and index["prospective"] is (generation_mode == "NATURAL")
        and index["retrospective_non_forward"]
        is (generation_mode == "RETROSPECTIVE_RECOVERY")
        and index["latest_signal_date"] == signal_date
        and index["latest_exec_date"] == inputs.three_rank["exec_date"]
        and index["latest_exit_date"] == inputs.three_rank["exit_date"]
        and index["latest_projection_json_url"] == json_relative.as_posix()
        and index["latest_projection_csv_url"] == csv_relative.as_posix()
        and index["latest_projection_json_sha256"] == _sha256(json_path)
        and index["latest_projection_csv_sha256"] == _sha256(csv_path)
        and index["latest_projection_snapshot_sha256"]
        == projection["snapshot_sha256"]
        and index["latest_top10_members_sha256"]
        == projection["top10_members_sha256"]
        and index["latest_source_bundle_sha256"]
        == projection["source_bundle_sha256"]
        and index["latest_source_feature_snapshot_sha256"]
        == projection["source_feature_snapshot_sha256"]
        and index["candidate_count"] == projection["candidate_count"]
        and index["source_bindings"]
        == projection["source_bindings"]
        == inputs.source_bindings
        and index["boundaries"] == projection["boundaries"] == BOUNDARIES
        and csv_path.read_bytes() == _csv_bytes(projection, row_fields),
        f"public P1 index/projection/CSV binding failed: {output_root}",
    )
    return {
        "inputs": inputs,
        "index": index,
        "projection": projection,
        "index_path": index_path,
        "json_path": json_path,
        "csv_path": csv_path,
        "projection_json_sha256": _sha256(json_path),
    }


def validate_primary_profit_bundle(
    root: Path,
    *,
    expected_signal_date: str | None = None,
    expected_generation_mode: str | None = None,
) -> dict[str, Any]:
    """Validate the atomic P1 single+mixed pair over one exact frozen TopN."""

    single = validate_primary_profit_index_chain(
        root,
        SINGLE_ROOT,
        index_schema=SINGLE_INDEX_SCHEMA,
        row_fields=SINGLE_ROW_FIELDS,
        validator=validate_single_projection,
        expected_signal_date=expected_signal_date,
        expected_generation_mode=expected_generation_mode,
    )
    mixed = validate_primary_profit_index_chain(
        root,
        MIXED_ROOT,
        index_schema=MIXED_INDEX_SCHEMA,
        row_fields=MIXED_ROW_FIELDS,
        validator=validate_mixed_projection,
        expected_signal_date=expected_signal_date,
        expected_generation_mode=expected_generation_mode,
    )
    single_projection = single["projection"]
    mixed_projection = mixed["projection"]
    single_inputs = single["inputs"]
    mixed_inputs = mixed["inputs"]
    _expect(
        single_projection["source_bindings"]
        == mixed_projection["source_bindings"]
        == single_inputs.source_bindings
        == mixed_inputs.source_bindings
        and single_projection["top10_members_sha256"]
        == mixed_projection["top10_members_sha256"]
        and single_projection["source_bundle_sha256"]
        == mixed_projection["source_bundle_sha256"]
        and single_projection["source_feature_snapshot_sha256"]
        == mixed_projection["source_feature_snapshot_sha256"]
        and single_projection["candidate_count"]
        == mixed_projection["candidate_count"]
        == len(single_inputs.selected_runtime)
        == len(mixed_inputs.selected_runtime),
        "public P1 single/mixed exact TopN binding failed",
    )
    return {"inputs": single_inputs, "single": single, "mixed": mixed}


def _validate_existing_index(existing: Mapping[str, Any], *, expected_schema: str) -> str:
    # Permit a one-way migration from the two existing public research index
    # schemas. Their dated artifacts remain immutable; only the latest pointer
    # moves to the stricter P0-authority schema.
    schema = str(existing.get("schema_version") or "")
    allowed = {
        expected_schema,
        "dc20_legacy_profit_relative_research_index_v1",
        "dc20_executable_profit_public_research_index_v1",
    }
    _expect(schema in allowed, "existing research index schema is unknown")
    date = str(existing.get("latest_signal_date") or "")
    _expect(DATE_RE.fullmatch(date) is not None, "existing research index date invalid")
    return date


def _materialize_projection(
    root: Path,
    payload: Mapping[str, Any],
    *,
    output_root: Path,
    row_fields: Sequence[str],
    index_schema: str,
    validator: Callable[[Mapping[str, Any]], None],
) -> tuple[Path, Path, Path, dict[str, Any]]:
    root = root.resolve(strict=True)
    payload = copy.deepcopy(dict(payload))
    validator(payload)
    output = _ensure_directory(root, output_root)
    date = str(payload["signal_date"])
    json_path = output / f"projection_{date}.json"
    csv_path = output / f"projection_{date}.csv"
    index_path = output / "index.json"
    csv_bytes = _csv_bytes(payload, row_fields)
    payload["downloads"] = {
        "json_url": json_path.relative_to(root).as_posix(),
        "csv_url": csv_path.relative_to(root).as_posix(),
        "csv_sha256": _sha256_bytes(csv_bytes),
        "row_count": len(payload.get("rows") or []),
    }
    validator(payload)
    json_bytes = _pretty_json_bytes(payload)
    _write_immutable(json_path, json_bytes)
    _write_immutable(csv_path, csv_bytes)
    index = {
        "schema_version": index_schema,
        "index_kind": "dated_primary_profit_research_pointer_only",
        "data_alias": False,
        "display_name": payload["display_name"],
        "status": payload["status"],
        "generation_mode": payload["generation_mode"],
        "prospective": payload["prospective"],
        "retrospective_non_forward": payload["retrospective_non_forward"],
        "latest_signal_date": date,
        "latest_exec_date": payload["exec_date"],
        "latest_exit_date": payload["exit_date"],
        "latest_projection_json_url": json_path.relative_to(root).as_posix(),
        "latest_projection_json_sha256": _sha256(json_path),
        "latest_projection_csv_url": csv_path.relative_to(root).as_posix(),
        "latest_projection_csv_sha256": _sha256(csv_path),
        "latest_projection_snapshot_sha256": payload["snapshot_sha256"],
        "latest_top10_members_sha256": payload["top10_members_sha256"],
        "latest_source_bundle_sha256": payload["source_bundle_sha256"],
        "latest_source_feature_snapshot_sha256": payload["source_feature_snapshot_sha256"],
        "candidate_count": payload["candidate_count"],
        "source_bindings": payload["source_bindings"],
        "boundaries": payload["boundaries"],
    }
    if index_path.exists():
        _expect(index_path.is_file() and not index_path.is_symlink(), "research index path is unsafe")
        existing = _read_json(index_path, label="existing research index")
        existing_date = _validate_existing_index(existing, expected_schema=index_schema)
        _expect(date >= existing_date, "out-of-order P1 research pointer update rejected")
        if date == existing_date:
            _expect(existing == index, "same-date P1 research pointer rewrite rejected")
            return json_path, csv_path, index_path, payload
    _atomic_write(index_path, _pretty_json_bytes(index))
    return json_path, csv_path, index_path, payload


def publish_primary_profit_rankings(
    root: Path,
    signal_date: str,
    *,
    generation_mode: str,
    single_scorer: Callable[[PrimaryInputs], tuple[list[dict[str, Any]], dict[str, Any]]] = score_single_profit,
    mixed_scorer: Callable[[PrimaryInputs], tuple[list[dict[str, Any]], dict[str, Any]]] = score_mixed_profit,
) -> dict[str, Any]:
    inputs = load_primary_inputs(root, signal_date, generation_mode)
    single = build_single_projection(inputs, scorer=single_scorer)
    mixed = build_mixed_projection(inputs, scorer=mixed_scorer)
    single_paths = _materialize_projection(
        inputs.root,
        single,
        output_root=SINGLE_ROOT,
        row_fields=SINGLE_ROW_FIELDS,
        index_schema=SINGLE_INDEX_SCHEMA,
        validator=validate_single_projection,
    )
    mixed_paths = _materialize_projection(
        inputs.root,
        mixed,
        output_root=MIXED_ROOT,
        row_fields=MIXED_ROW_FIELDS,
        index_schema=MIXED_INDEX_SCHEMA,
        validator=validate_mixed_projection,
    )
    daily_top2_path, daily_top2_index = (
        materialize_primary_profit_daily_mixed_top2_index(inputs.root)
    )
    return {
        "signal_date": inputs.signal_date,
        "generation_mode": inputs.generation_mode,
        "status": MODE_STATUS[inputs.generation_mode],
        "candidate_count": len(inputs.selected_runtime),
        "top10_members_sha256": inputs.three_rank["top10_members_sha256"],
        "single": {
            "json": single_paths[0],
            "csv": single_paths[1],
            "index": single_paths[2],
            "snapshot_sha256": single["snapshot_sha256"],
        },
        "mixed": {
            "json": mixed_paths[0],
            "csv": mixed_paths[1],
            "index": mixed_paths[2],
            "snapshot_sha256": mixed["snapshot_sha256"],
        },
        "daily_mixed_top2": {
            "index": daily_top2_path,
            "snapshot_sha256": daily_top2_index["snapshot_sha256"],
            "recorded_days": daily_top2_index["recorded_days"],
            "recorded_slots": daily_top2_index["recorded_slots"],
        },
        "forward_selection_created": False,
        "forward_statistics_updated": False,
        "action_input_consumed": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--generation-mode", choices=MODES, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = publish_primary_profit_rankings(
            args.root,
            args.signal_date,
            generation_mode=args.generation_mode,
        )
    except (PrimaryProfitRankingError, ExecutableProfitShadowError) as exc:
        print(f"[P1 BLOCK] {exc}", file=sys.stderr)
        return 2
    printable = copy.deepcopy(result)
    for section in ("single", "mixed"):
        for field in ("json", "csv", "index"):
            printable[section][field] = str(printable[section][field])
    printable["daily_mixed_top2"]["index"] = str(
        printable["daily_mixed_top2"]["index"]
    )
    print(json.dumps(printable, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
