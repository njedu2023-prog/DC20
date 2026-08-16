#!/usr/bin/env python3
"""Diagnostic-only frozen replay for canonical runtime V2 metadata.

This command is intentionally separate from the production runner.  While the
repository still contains the one reviewed inactive V1 manifest, the replay
accepts only its exact bytes and manually reads only the reviewed SHA77e frozen
snapshot.  Once the manifest is V2, the replay delegates to the production C3
verified-snapshot loader with its complete-contract and pin checks.  Neither
branch can fall back to live history, edit the manifest, or weaken the
production freeze loader.
"""

from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import io
import json
import math
import re
import subprocess
import sys
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine  # noqa: E402
from top10decision.auction_v3.engine import (  # noqa: E402
    MODEL_CANONICAL_V2,
    _model_executable_policy_projection,
)
from top10decision.decision.canonical_fingerprint import (  # noqa: E402
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_frame_fingerprint,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
)
from top10decision.decision.model_freeze import (  # noqa: E402
    FREEZE_SCHEMA_VERSION,
    LEGACY_FREEZE_SCHEMA_VERSION,
    DecisionModelFreezeError,
    frame_columns_sha256,
    load_model_freeze,
    load_verified_frozen_history_snapshot,
    validate_pinned_files,
)
from top10decision.decision.trade_selector import (  # noqa: E402
    TRADE_SELECTOR_CANONICAL_V2,
    _selector_executable_policy_projection,
)


EXPECTED_SNAPSHOT_SHA256 = (
    "77e48be6732a08698a6abf4a0da74cb02b3129c57d14be66fb94679816a5337e"
)
EXPECTED_SNAPSHOT_PATH = "models/decision_v12_frozen_history_20260805.csv.gz"
EXPECTED_HISTORY_ROWS = 40_355
EXPECTED_HISTORY_DATES = 715
EXPECTED_HISTORY_COLUMNS = 151
EXPECTED_HISTORY_COLUMNS_SHA256 = (
    "dbfd38f20f00cbd57460ac3a858f937fa560bcd221b0ed3a12b408bc6c313d49"
)
EXPECTED_FREEZE_ID = "dc20_decision_v13_promotion_oos_d20260815_history20260805"
EXPECTED_TRAINING_CUTOFF = "20260805"
LEGACY_BOOTSTRAP_MANIFEST_SHA256 = (
    "87605814bce9f2180e151ed91d6c16e2c22b46c3dcd147e8bdab7895c3f0975a"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^20\d{6}$")
TS_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
REFERENCE_C6_GIT_BLOBS = {
    "backtest_top10_latest.csv": "1bbebbbe4a3b94c0a95fd64f4e27b242ea5b0222",
    "backtest_trade_selector_oos_latest.csv": "6afd29e31cf98c434ac6e67183f7005a89663a49",
    "backtest_latest.json": "e27511643fc5aa1ee5bdb60f1d3b15b7e90adef4",
    "model_meta_latest.json": "9fee4a2bc9904bf703a292b5df3c367c4c39712b",
}

IDENTITY_COLUMNS = ("signal_date", "ts_code")
TOP10_DISCRETE_COLUMNS = (
    "stage",
    "stage_focus",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "policy_max_positions",
    "gate_policy_ready",
    "gate_stage_focus",
    "gate_exit_probability",
    "gate_fill_probability",
    "gate_big_loss_probability",
    "gate_mean_return_lcb",
    "gate_conservative_ev",
    "gate_selection_score",
    "risk_gate_pass",
)
OOS_DISCRETE_COLUMNS = (
    *TOP10_DISCRETE_COLUMNS,
    "promotion_rank",
    "trade_rank",
    "trade_gate_pass",
    "trade_shadow_selected",
    "trade_selected",
    "trade_model_reason",
    "trade_selector_promoted",
    "trade_selector_globally_promoted",
    "trade_selector_policy_ready",
)
GAP_COLUMNS = ("diagnostic_gap", "recommended_max_gap")
POLICY_SCORE_COLUMNS = (
    "policy_max_big_loss_probability",
    "policy_min_mean_return_lcb",
    "policy_min_fill_probability",
    "policy_min_exit_probability",
    "policy_min_conservative_ev",
    "policy_min_selection_score",
)
BASE_SCORE_COLUMNS = (
    "predicted_net_return",
    "predicted_return_lcb",
    "predicted_return_ucb",
    "predicted_mean_return_lcb",
    "predicted_mean_return_ucb",
    "predicted_outcome_q10",
    "predicted_outcome_q90",
    "predicted_profit_probability",
    "predicted_big_loss_probability",
    "predicted_continuation_limit_up_probability",
    "predicted_fill_probability",
    "predicted_exit_probability",
    "conservative_ev",
    "selection_score",
    *POLICY_SCORE_COLUMNS,
    *GAP_COLUMNS,
)
TRADE_SCORE_COLUMNS = (
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_big_loss_probability",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_base_score",
    "trade_score",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _safe_exact_repository_file(root: Path, relative: str, *, label: str) -> Path:
    """Resolve one already-exact relative path without following symlinks."""

    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        _require(not candidate.is_symlink(), f"{label} must not traverse a symlink")
    _require(candidate.is_file(), f"{label} missing: {candidate}")
    try:
        candidate.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes repository root") from exc
    return candidate


def _parse_manifest_bytes(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("freeze manifest is not valid UTF-8 JSON") from exc
    _require(isinstance(decoded, dict), "freeze manifest must be an object")
    return decoded


def _validate_loaded_history(
    history: pd.DataFrame,
    *,
    expected_columns_sha256: str,
) -> dict[str, Any]:
    _require(len(history) == EXPECTED_HISTORY_ROWS, "frozen history row count drifted")
    _require(
        len(history.columns) == EXPECTED_HISTORY_COLUMNS,
        "frozen history column count drifted",
    )
    columns_sha = frame_columns_sha256(list(history.columns))
    _require(
        columns_sha == expected_columns_sha256,
        "frozen history column order/schema drifted",
    )
    for column in ("signal_date", "buy_date", "target_exit_date", "actual_exit_date"):
        _require(column in history.columns, f"frozen history missing {column}")
        values = history[column].astype("string")
        _require(
            values.notna().all()
            and values.map(lambda value: DATE_RE.fullmatch(str(value)) is not None).all(),
            f"frozen history contains noncanonical {column}",
        )
    _require("ts_code" in history.columns, "frozen history missing ts_code")
    codes = history["ts_code"].astype("string")
    _require(
        codes.notna().all()
        and codes.map(lambda value: TS_CODE_RE.fullmatch(str(value)) is not None).all(),
        "frozen history contains noncanonical ts_code",
    )
    signal_dates = history["signal_date"].astype("string")
    _require(
        signal_dates.nunique() == EXPECTED_HISTORY_DATES,
        "frozen history date count drifted",
    )
    _require(
        signal_dates.le(EXPECTED_TRAINING_CUTOFF).all(),
        "frozen history contains rows beyond its cutoff",
    )
    _require(
        str(signal_dates.max()) == EXPECTED_TRAINING_CUTOFF,
        "frozen history does not end at the reviewed cutoff",
    )
    _require(
        not history.duplicated(list(IDENTITY_COLUMNS)).any(),
        "frozen history contains duplicate signal_date/ts_code identities",
    )
    return {
        "rows": int(len(history)),
        "signal_dates": int(signal_dates.nunique()),
        "columns": int(len(history.columns)),
        "columns_sha256": columns_sha,
        "history_start": str(signal_dates.min()),
        "history_end": str(signal_dates.max()),
    }


def _load_exact_legacy_bootstrap(
    root: Path,
    manifest_bytes: bytes,
    manifest: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read the single reviewed inactive V1 snapshot without a live fallback."""

    _require(
        manifest.get("schema_version") == LEGACY_FREEZE_SCHEMA_VERSION,
        "legacy bootstrap schema_version drifted",
    )
    _require(manifest.get("active") is False, "legacy bootstrap must remain inactive")
    _require(
        manifest.get("freeze_id") == EXPECTED_FREEZE_ID,
        "legacy bootstrap freeze_id drifted",
    )
    _require(
        manifest.get("training_cutoff_signal_date") == EXPECTED_TRAINING_CUTOFF,
        "legacy bootstrap cutoff drifted",
    )
    snapshot = manifest.get("history_snapshot")
    _require(isinstance(snapshot, dict), "legacy history_snapshot must be an object")
    _require(
        snapshot.get("path") == EXPECTED_SNAPSHOT_PATH,
        "legacy history_snapshot.path drifted",
    )
    _require(
        snapshot.get("sha256") == EXPECTED_SNAPSHOT_SHA256,
        "legacy history_snapshot.sha256 drifted",
    )
    _require(
        snapshot.get("bootstrap_mode") is False,
        "legacy history_snapshot.bootstrap_mode must be false",
    )
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    _require(
        manifest_sha == LEGACY_BOOTSTRAP_MANIFEST_SHA256,
        "legacy bootstrap manifest content SHA drifted",
    )

    snapshot_path = _safe_exact_repository_file(
        root,
        EXPECTED_SNAPSHOT_PATH,
        label="legacy frozen history snapshot",
    )
    snapshot_bytes = snapshot_path.read_bytes()
    actual_sha = hashlib.sha256(snapshot_bytes).hexdigest()
    _require(actual_sha == EXPECTED_SNAPSHOT_SHA256, "legacy snapshot bytes drifted")
    try:
        history = pd.read_csv(
            io.BytesIO(snapshot_bytes),
            compression="gzip",
            low_memory=False,
            dtype={
                "signal_date": "string",
                "buy_date": "string",
                "target_exit_date": "string",
                "actual_exit_date": "string",
                "ts_code": "string",
            },
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("legacy frozen history snapshot is unreadable") from exc
    facts = _validate_loaded_history(
        history,
        expected_columns_sha256=EXPECTED_HISTORY_COLUMNS_SHA256,
    )
    return history, {
        "active": False,
        "manifest_active": False,
        "freeze_id": EXPECTED_FREEZE_ID,
        "source": "legacy_v1_exact_diagnostic_bootstrap",
        "path": EXPECTED_SNAPSHOT_PATH,
        "sha256": actual_sha,
        "bootstrap_mode": False,
        "training_cutoff_signal_date": EXPECTED_TRAINING_CUTOFF,
        "manifest_content_sha256": manifest_sha,
        **facts,
    }


def load_forced_frozen_history(
    root: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Load only the reviewed frozen snapshot; never consult live history."""

    root = root.resolve()
    manifest_path = _safe_exact_repository_file(
        root,
        "models/decision_model_freeze.json",
        label="freeze manifest",
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest_content_sha = hashlib.sha256(manifest_bytes).hexdigest()
    raw_manifest = _parse_manifest_bytes(manifest_bytes)
    schema = raw_manifest.get("schema_version")

    if schema == LEGACY_FREEZE_SCHEMA_VERSION:
        manifest = raw_manifest
        history, audit = _load_exact_legacy_bootstrap(
            root, manifest_bytes, manifest
        )
        loader_contract = "one_time_exact_v1_no_live_fallback"
    elif schema == FREEZE_SCHEMA_VERSION:
        manifest = load_model_freeze(root, required=True)
        _require(
            _sha256(manifest_path) == manifest_content_sha,
            "V2 freeze manifest changed while loading",
        )
        pinned_files_audit = validate_pinned_files(
            root,
            manifest,
            force_enforcement=True,
        )
        _require(
            pinned_files_audit.get("enforced") is True
            and (
                pinned_files_audit.get("active") is True
                or pinned_files_audit.get("forced_enforcement") is True
            ),
            "V2 replay did not enforce pinned-file bytes",
        )
        history, audit = load_verified_frozen_history_snapshot(root, manifest)
        _require(
            audit.get("source") == "forced_frozen_snapshot",
            "V2 replay did not use the verified forced snapshot loader",
        )
        facts = _validate_loaded_history(
            history,
            expected_columns_sha256=EXPECTED_HISTORY_COLUMNS_SHA256,
        )
        audit = {
            **audit,
            **facts,
            "manifest_content_sha256": manifest_content_sha,
            "pinned_files": pinned_files_audit,
        }
        loader_contract = "v2_complete_contract_and_pins_no_live_fallback"
    else:
        raise RuntimeError(f"unsupported diagnostic freeze schema: {schema}")

    _require(
        audit.get("sha256") == EXPECTED_SNAPSHOT_SHA256,
        "loaded snapshot SHA drifted",
    )
    _require(
        _sha256(manifest_path) == manifest_content_sha,
        "freeze manifest changed during diagnostic load",
    )
    return history, copy.deepcopy(manifest), {
        **audit,
        "manifest_schema_version": schema,
        "manifest_active_on_disk": manifest.get("active") is True,
        "forced_frozen_replay": True,
        "manifest_mutated_on_disk": False,
        "live_history_fallback": False,
        "loader_contract": loader_contract,
    }


class DiagnosticFrozenEngine(AuctionV3Engine):
    def __init__(
        self,
        config: AuctionV3Config,
        history: pd.DataFrame,
        audit: dict[str, Any],
    ) -> None:
        super().__init__(config)
        self._diagnostic_history = history.copy()
        self.diagnostic_history_audit = dict(audit)

    def build_history(self) -> pd.DataFrame:
        return self._diagnostic_history.copy()


def _read_csv(path: Path) -> pd.DataFrame:
    _require(path.is_file(), f"required CSV missing: {path}")
    return pd.read_csv(path, low_memory=False)


def _read_csv_exact_text(path: Path) -> pd.DataFrame:
    """Read CSV cells as their decoded text after rejecting header ambiguity."""

    _require(path.is_file(), f"required CSV missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise RuntimeError(f"required CSV is empty: {path}") from exc
        _require(bool(header), f"required CSV has empty header: {path}")
        _require(
            len(header) == len(set(header)),
            f"required CSV has duplicate header: {path}",
        )
        rows: list[list[str]] = []
        for line_number, row in enumerate(reader, start=2):
            _require(
                len(row) == len(header),
                f"required CSV row width mismatch at line {line_number}: {path}",
            )
            rows.append(row)
    return pd.DataFrame(rows, columns=header, dtype=object)


def _read_json(path: Path) -> dict[str, Any]:
    _require(path.is_file(), f"required JSON missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), f"JSON must be an object: {path}")
    return payload


def _identity_index(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    for column in IDENTITY_COLUMNS:
        _require(column in frame.columns, f"{label} missing identity column {column}")
        values = frame[column].astype("string")
        stripped = values.str.strip()
        _require(stripped.notna().all() and stripped.ne("").all(), f"{label} has empty {column}")
        _require(values.equals(stripped), f"{label} has whitespace in {column}")
        pattern = r"20\d{6}" if column == "signal_date" else r"\d{6}\.(?:SH|SZ|BJ)"
        _require(
            values.str.fullmatch(pattern).all(),
            f"{label} has invalid exact-format {column}",
        )
    duplicates = frame.duplicated(list(IDENTITY_COLUMNS), keep=False)
    _require(not duplicates.any(), f"{label} has ambiguous duplicate identities")
    return frame.set_index(list(IDENTITY_COLUMNS), drop=False).sort_index()


def _token_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column]
    return values.astype("string").fillna("<MISSING>")


TEXT_DISCRETE_COLUMNS = frozenset(
    {"stage", "model_reason", "trade_model_reason", "observation_risk_label"}
)
BINARY_DISCRETE_COLUMNS = frozenset(
    {
        "stage_focus",
        "observation_selected",
        "shadow_selected",
        "selected",
        "gate_policy_ready",
        "gate_stage_focus",
        "gate_exit_probability",
        "gate_fill_probability",
        "gate_big_loss_probability",
        "gate_mean_return_lcb",
        "gate_conservative_ev",
        "gate_selection_score",
        "risk_gate_pass",
        "trade_gate_pass",
        "trade_shadow_selected",
        "trade_selected",
        "trade_selector_promoted",
        "trade_selector_globally_promoted",
        "trade_selector_policy_ready",
    }
)
SELECTOR_PREDICTION_SCORE_COLUMNS = (
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_score",
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_public_market_buyable_probability",
    "trade_predicted_big_loss_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_base_score",
    "trade_tail_risk_weight",
)
SELECTOR_PREDICTION_RANK_COLUMNS = (
    "observation_rank",
    "promotion_rank",
    "trade_rank",
)
SELECTOR_PREDICTION_BINARY_COLUMNS = (
    "trade_gate_pass",
    "trade_shadow_selected",
    "trade_selected",
    "trade_selector_policy_ready",
)
SELECTOR_PREDICTION_ARTIFACT_COLUMNS = (
    "trade_selector_artifact_sha256",
    "trade_selector_artifact_v2_sha256",
)


def _validated_numeric(
    frame: pd.DataFrame,
    column: str,
    *,
    label: str,
    allow_missing: bool,
    integral: bool = False,
) -> pd.Series:
    _require(column in frame.columns, f"{label} missing {column}")
    raw = frame[column]
    numeric = pd.to_numeric(raw, errors="coerce")
    invalid = raw.notna() & numeric.isna()
    _require(not invalid.any(), f"{label} has invalid numeric {column}")
    present = numeric.notna()
    _require(
        bool(np.isfinite(numeric.loc[present].to_numpy(dtype=float)).all()),
        f"{label} has non-finite {column}",
    )
    if not allow_missing:
        _require(present.all(), f"{label} has missing {column}")
    if integral and present.any():
        values = numeric.loc[present].to_numpy(dtype=float)
        _require(
            bool(np.equal(values, np.rint(values)).all()),
            f"{label} has non-integral {column}",
        )
    return numeric


def _validated_binary(
    frame: pd.DataFrame,
    column: str,
    *,
    label: str,
    allow_missing: bool = False,
) -> pd.Series:
    values = _validated_numeric(
        frame,
        column,
        label=label,
        allow_missing=allow_missing,
        integral=True,
    )
    present = values.notna()
    _require(
        values.loc[present].isin((0.0, 1.0)).all(),
        f"{label} has non-binary {column}",
    )
    return values


def _validated_positive_unique_rank(
    frame: pd.DataFrame,
    column: str,
    *,
    label: str,
) -> pd.Series:
    values = _validated_numeric(
        frame,
        column,
        label=label,
        allow_missing=False,
        integral=True,
    )
    _require(values.gt(0).all(), f"{label} has non-positive {column}")
    _require(
        values.nunique(dropna=False) == len(values),
        f"{label} has non-unique {column}",
    )
    return values


def _selector_prediction_domain_contract(
    prediction: pd.DataFrame,
) -> dict[str, Any]:
    label = "prediction selector domain"
    observation_selected = _validated_binary(
        prediction,
        "observation_selected",
        label=label,
    )
    domain_mask = observation_selected.eq(1.0)
    domain = prediction.loc[domain_mask].copy()
    outside = prediction.loc[~domain_mask].copy()
    _require(not domain.empty, f"{label} is empty")

    numeric: dict[str, pd.Series] = {}
    for column in SELECTOR_PREDICTION_SCORE_COLUMNS:
        numeric[column] = _validated_numeric(
            domain,
            column,
            label=label,
            allow_missing=False,
        )
        outside_values = _validated_numeric(
            outside,
            column,
            label=f"{label} outside",
            allow_missing=True,
        )
        _require(
            outside_values.isna().all(),
            f"{label} outside has present {column}",
        )
    for column in SELECTOR_PREDICTION_RANK_COLUMNS:
        numeric[column] = _validated_positive_unique_rank(
            domain,
            column,
            label=label,
        )
        outside_values = _validated_numeric(
            outside,
            column,
            label=f"{label} outside",
            allow_missing=True,
            integral=True,
        )
        _require(
            outside_values.isna().all(),
            f"{label} outside has present {column}",
        )
    binary: dict[str, pd.Series] = {}
    for column in SELECTOR_PREDICTION_BINARY_COLUMNS:
        binary[column] = _validated_binary(
            domain,
            column,
            label=label,
        )
        outside_values = _validated_binary(
            outside,
            column,
            label=f"{label} outside",
        )
        _require(
            outside_values.eq(0.0).all(),
            f"{label} outside has nonzero {column}",
        )

    promoted = _validated_binary(
        prediction,
        "trade_selector_promoted",
        label=label,
    )
    _require(
        promoted.nunique(dropna=False) == 1,
        f"{label} has mixed trade_selector_promoted",
    )
    _require(
        "trade_model_reason" in prediction.columns,
        f"{label} missing trade_model_reason",
    )
    domain_reasons = domain["trade_model_reason"].tolist()
    _require(
        all(isinstance(value, str) and value != "" for value in domain_reasons),
        f"{label} has empty trade_model_reason",
    )
    _require(
        all(
            isinstance(value, str) and value == "outside_observation_top10"
            for value in outside["trade_model_reason"].tolist()
        ),
        f"{label} outside reason drifted",
    )
    artifacts: dict[str, str] = {}
    for column in SELECTOR_PREDICTION_ARTIFACT_COLUMNS:
        _require(column in prediction.columns, f"{label} missing {column}")
        domain_values = domain[column].tolist()
        _require(
            all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in domain_values),
            f"{label} has invalid {column}",
        )
        unique = set(domain_values)
        _require(len(unique) == 1, f"{label} has mixed {column}")
        artifacts[column] = next(iter(unique))
        _require(
            all(
                value == "" if isinstance(value, str) else bool(pd.isna(value))
                for value in outside[column].tolist()
            ),
            f"{label} outside has present {column}",
        )
    return {
        "domain": domain,
        "outside": outside,
        "domain_mask": domain_mask,
        "numeric": numeric,
        "binary": binary,
        "promoted": promoted,
        "artifacts": artifacts,
        "rows": int(len(domain)),
        "outside_rows": int(len(outside)),
    }


def _decimal_policy_surface_contract(
    prediction_text: pd.DataFrame,
    *,
    parsed_rows: int,
    threshold_columns: dict[str, str],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    label = "prediction policy text surface"
    _require(
        len(prediction_text) == parsed_rows,
        f"{label} row count differs from parsed prediction",
    )
    for name, column in threshold_columns.items():
        _require(
            list(prediction_text.columns).count(column) == 1,
            f"{label} requires exactly one {column} header",
        )
        expected = Decimal(str(thresholds[name]))
        _require(expected.is_finite(), f"{label} expected {column} is non-finite")
        for row_number, raw in enumerate(prediction_text[column].tolist(), start=2):
            _require(
                isinstance(raw, str) and raw != "",
                f"{label} has blank {column} at row {row_number}",
            )
            try:
                actual = Decimal(raw)
            except (InvalidOperation, ValueError) as exc:
                raise RuntimeError(
                    f"{label} has malformed {column} at row {row_number}"
                ) from exc
            _require(
                actual.is_finite(),
                f"{label} has non-finite {column} at row {row_number}",
            )
            _require(
                actual == expected,
                f"prediction {column} differs from model V2 policy projection",
            )
    return {
        "rows": int(parsed_rows),
        "columns": sorted(threshold_columns.values()),
        "exact_decimal_match": True,
    }


def validate_prediction_fill_contract(
    prediction: pd.DataFrame,
    *,
    selector_domain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = "prediction fill contract"
    first_fill = _validated_numeric(
        prediction,
        "predicted_fill_probability",
        label=label,
        allow_missing=False,
    )
    first_public = _validated_numeric(
        prediction,
        "predicted_public_market_buyable_probability",
        label=label,
        allow_missing=False,
    )
    for column, values in (
        ("predicted_fill_probability", first_fill),
        ("predicted_public_market_buyable_probability", first_public),
    ):
        _require(values.between(0.0, 1.0).all(), f"{label} out-of-range {column}")
    _require(
        first_fill.equals(first_public),
        f"{label} first-layer fill aliases differ",
    )

    selector = selector_domain or _selector_prediction_domain_contract(prediction)
    domain = selector["domain"]
    outside = selector["outside"]
    trade_fill = _validated_numeric(
        domain,
        "trade_predicted_fill_probability",
        label=label,
        allow_missing=False,
    )
    trade_public = _validated_numeric(
        domain,
        "trade_predicted_public_market_buyable_probability",
        label=label,
        allow_missing=False,
    )
    _require(trade_fill.between(0.0, 1.0).all(), f"{label} trade fill out of range")
    _require(trade_public.between(0.0, 1.0).all(), f"{label} trade public fill out of range")
    _require(trade_fill.equals(trade_public), f"{label} trade fill aliases differ")
    for column in (
        "trade_predicted_fill_probability",
        "trade_predicted_public_market_buyable_probability",
    ):
        outside_values = _validated_numeric(
            outside,
            column,
            label=label,
            allow_missing=True,
        )
        _require(outside_values.isna().all(), f"{label} outside has present {column}")

    actual = _validated_numeric(
        prediction,
        "predicted_actual_order_fill_probability",
        label=label,
        allow_missing=True,
    )
    available = _validated_binary(
        prediction,
        "actual_order_fill_probability_available",
        label=label,
    )
    present = actual.notna()
    _require(
        actual.loc[present].between(0.0, 1.0).all(),
        f"{label} actual fill probability out of range",
    )
    _require(
        available.eq(1.0).equals(present),
        f"{label} actual fill availability disagrees with missing-state",
    )
    return {
        "rows": int(len(prediction)),
        "selector_domain_rows": int(len(domain)),
        "selector_outside_rows": int(len(outside)),
        "first_layer_alias_equal_rows": int(len(prediction)),
        "selector_alias_equal_rows": int(len(domain)),
        "actual_probability_available_rows": int(present.sum()),
        "actual_probability_missing_rows": int((~present).sum()),
    }


def _validate_behavior_schema(
    frame: pd.DataFrame,
    *,
    label: str,
    discrete_columns: tuple[str, ...],
) -> dict[str, Any]:
    for column in discrete_columns:
        _require(column in frame.columns, f"{label} missing {column}")
        if column in TEXT_DISCRETE_COLUMNS:
            values = frame[column].astype("string").str.strip()
            _require(
                values.notna().all() and values.ne("").all(),
                f"{label} has missing/empty {column}",
            )
            continue
        values = _validated_numeric(
            frame,
            column,
            label=label,
            allow_missing=False,
            integral=True,
        )
        if column in BINARY_DISCRETE_COLUMNS:
            _require(
                values.isin((0, 1)).all(),
                f"{label} has non-boolean {column}",
            )

    diagnostic = _validated_numeric(
        frame,
        "diagnostic_gap",
        label=label,
        allow_missing=False,
    )
    recommended = _validated_numeric(
        frame,
        "recommended_max_gap",
        label=label,
        allow_missing=True,
    )
    risk_gate = _validated_numeric(
        frame,
        "risk_gate_pass",
        label=label,
        allow_missing=False,
        integral=True,
    )
    expected_present = risk_gate.eq(1)
    _require(
        recommended.notna().equals(expected_present),
        f"{label} recommended_max_gap missing-state disagrees with risk_gate_pass",
    )
    if expected_present.any():
        _require(
            recommended.loc[expected_present].equals(
                diagnostic.loc[expected_present]
            ),
            f"{label} recommended_max_gap differs from diagnostic_gap on passed rows",
        )
    return {
        "mandatory_discrete_missing": 0,
        "diagnostic_gap_missing": 0,
        "recommended_max_gap_present": int(recommended.notna().sum()),
        "recommended_max_gap_missing": int(recommended.isna().sum()),
        "recommended_presence_matches_risk_gate": True,
    }


def _validate_score_columns(
    frame: pd.DataFrame,
    *,
    label: str,
    columns: tuple[str, ...],
) -> dict[str, pd.Series]:
    return {
        column: _validated_numeric(
            frame,
            column,
            label=label,
            allow_missing=column == "recommended_max_gap",
        )
        for column in columns
    }


def _compare_behavior(
    reference: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    label: str,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    left = _identity_index(reference, label=f"reference {label}")
    right = _identity_index(candidate, label=f"candidate {label}")
    _require(left.index.equals(right.index), f"{label} identity set/order changed")
    left_schema = _validate_behavior_schema(
        left,
        label=f"reference {label}",
        discrete_columns=columns,
    )
    right_schema = _validate_behavior_schema(
        right,
        label=f"candidate {label}",
        discrete_columns=columns,
    )
    changed: dict[str, int] = {}
    for column in (*columns, *GAP_COLUMNS):
        _require(column in left.columns, f"reference {label} missing {column}")
        _require(column in right.columns, f"candidate {label} missing {column}")
        count = int((_token_series(left, column) != _token_series(right, column)).sum())
        changed[column] = count
        _require(count == 0, f"{label} behavior changed in {column}: {count} rows")
    return {
        "rows": int(len(right)),
        "dates": int(right["signal_date"].astype(str).nunique()),
        "identity_equal": True,
        "changed_rows": changed,
        "reference_schema": left_schema,
        "candidate_schema": right_schema,
    }


def _canonical_score_comparison(
    reference: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    label: str,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    left = _identity_index(reference, label=f"reference {label}").reset_index(drop=True)
    right = _identity_index(candidate, label=f"candidate {label}").reset_index(drop=True)
    _require(
        left[list(IDENTITY_COLUMNS)].equals(right[list(IDENTITY_COLUMNS)]),
        f"{label} identity changed before canonical comparison",
    )
    left_numeric = _validate_score_columns(
        left,
        label=f"reference {label}",
        columns=columns,
    )
    right_numeric = _validate_score_columns(
        right,
        label=f"candidate {label}",
        columns=columns,
    )
    for column in columns:
        _require(
            left_numeric[column].isna().equals(right_numeric[column].isna()),
            f"{label} missing-state drifted in {column}",
        )
    requested = (*IDENTITY_COLUMNS, *columns)
    kinds = {
        "signal_date": "date",
        "ts_code": "code",
        **{name: "float" for name in columns},
    }
    probes: dict[str, Any] = {}
    for decimals in (6, 8, 10, 12):
        left_fingerprint = canonical_frame_fingerprint(
            left,
            requested,
            decimals=decimals,
            kinds=kinds,
            strict=True,
        )
        right_fingerprint = canonical_frame_fingerprint(
            right,
            requested,
            decimals=decimals,
            kinds=kinds,
            strict=True,
        )
        equal = left_fingerprint["sha256"] == right_fingerprint["sha256"]
        probes[str(decimals)] = {
            "equal": equal,
            "reference_sha256": left_fingerprint["sha256"],
            "candidate_sha256": right_fingerprint["sha256"],
            "gate": "hard" if decimals == 8 else "audit_only",
        }
        if decimals == 8:
            _require(equal, f"{label} canonical 8-decimal scores drifted")
    return probes


def _candidate_frame_contract(
    frame: pd.DataFrame,
    *,
    label: str,
    discrete_columns: tuple[str, ...],
    score_columns: tuple[str, ...],
) -> dict[str, Any]:
    stable = _identity_index(frame, label=label).reset_index(drop=True)
    behavior_schema = _validate_behavior_schema(
        stable,
        label=label,
        discrete_columns=discrete_columns,
    )
    _validate_score_columns(
        stable,
        label=label,
        columns=score_columns,
    )
    identity_kinds = {"signal_date": "date", "ts_code": "code"}
    identity = canonical_frame_fingerprint(
        stable,
        IDENTITY_COLUMNS,
        decimals=8,
        kinds=identity_kinds,
        strict=True,
    )
    date_counts = (
        stable.groupby("signal_date", sort=True)
        .size()
        .rename("row_count")
        .reset_index()
    )
    date_counts_fingerprint = canonical_frame_fingerprint(
        date_counts,
        ("signal_date", "row_count"),
        decimals=8,
        kinds={"signal_date": "date", "row_count": "integer"},
        strict=True,
    )
    discrete_kinds = dict(identity_kinds)
    for column in discrete_columns:
        if column in TEXT_DISCRETE_COLUMNS:
            discrete_kinds[column] = "exact_text"
        else:
            discrete_kinds[column] = "integer"
    discrete = canonical_frame_fingerprint(
        stable,
        (*IDENTITY_COLUMNS, *discrete_columns),
        decimals=8,
        kinds=discrete_kinds,
        strict=True,
    )
    score_kinds = {
        **identity_kinds,
        **{column: "float" for column in score_columns},
    }
    scores = canonical_frame_fingerprint(
        stable,
        (*IDENTITY_COLUMNS, *score_columns),
        decimals=8,
        kinds=score_kinds,
        strict=True,
    )
    return {
        "label": label,
        "rows": int(len(stable)),
        "dates": int(stable["signal_date"].astype(str).nunique()),
        "identity_columns": list(IDENTITY_COLUMNS),
        "discrete_columns": list(discrete_columns),
        "score_columns": list(score_columns),
        "identity_sha256": identity["sha256"],
        "date_counts_sha256": date_counts_fingerprint["sha256"],
        "discrete_sha256": discrete["sha256"],
        "scores_sha256_q8": scores["sha256"],
        "schema": {
            "identity_valid": identity["valid"],
            "date_counts_valid": date_counts_fingerprint["valid"],
            "discrete_valid": discrete["valid"],
            "scores_valid": scores["valid"],
            "missing_columns": sorted(
                set(
                    identity["missing_columns"]
                    + date_counts_fingerprint["missing_columns"]
                    + discrete["missing_columns"]
                    + scores["missing_columns"]
                )
            ),
            "invalid_cell_count": int(
                identity["invalid_cell_count"]
                + date_counts_fingerprint["invalid_cell_count"]
                + discrete["invalid_cell_count"]
                + scores["invalid_cell_count"]
            ),
            "score_missing_counts": {
                column: int(stable[column].isna().sum())
                for column in score_columns
            },
            "behavior_missing_state": behavior_schema,
        },
    }


def _reference_path(reference_dir: Path, name: str) -> Path:
    flat = reference_dir / name
    if flat.is_file():
        return flat
    metric = reference_dir / "outputs" / "auction_v3" / "metrics" / name
    if metric.is_file():
        return metric
    return reference_dir / "outputs" / "auction_v3" / "models" / name


def _action_candidate_contract(action_plan: dict[str, Any]) -> dict[str, Any]:
    signal_date = str(action_plan.get("signal_date") or "").strip()
    _require(signal_date != "", "action plan has empty signal_date")
    rows = action_plan.get("stage_watchlist")
    _require(isinstance(rows, list) and rows, "action plan stage_watchlist missing")
    frame = pd.DataFrame(rows).copy()
    frame.insert(0, "signal_date", signal_date)
    required = (
        "signal_date",
        "ts_code",
        "stage_watch_rank",
        "trade_rank",
        "trade_shadow_selected",
        "watch_label",
        "action",
        "target_weight",
    )
    indexed = _identity_index(frame, label="action plan stage watchlist").reset_index(
        drop=True
    )
    rank = _validated_numeric(
        indexed,
        "stage_watch_rank",
        label="action plan stage watchlist",
        allow_missing=False,
        integral=True,
    )
    _require(
        rank.gt(0).all() and rank.nunique(dropna=False) == len(rank),
        "action plan stage_watch_rank must be positive and unique",
    )
    trade_rank = _validated_numeric(
        indexed,
        "trade_rank",
        label="action plan stage watchlist",
        allow_missing=False,
        integral=True,
    )
    _require(
        trade_rank.gt(0).all()
        and trade_rank.nunique(dropna=False) == len(trade_rank),
        "action plan trade_rank must be positive and unique",
    )
    trade_shadow = _validated_numeric(
        indexed,
        "trade_shadow_selected",
        label="action plan stage watchlist",
        allow_missing=False,
        integral=True,
    )
    _require(
        trade_shadow.isin((0.0, 1.0)).all(),
        "action plan trade_shadow_selected must be binary",
    )
    weight = _validated_numeric(
        indexed,
        "target_weight",
        label="action plan stage watchlist",
        allow_missing=False,
    )
    for column in ("watch_label", "action"):
        _require(column in indexed.columns, f"action plan stage watchlist missing {column}")
        values = indexed[column].astype("string").str.strip()
        _require(
            values.notna().all() and values.ne("").all(),
            f"action plan stage watchlist has empty {column}",
        )
    fingerprint = canonical_frame_fingerprint(
        indexed,
        required,
        decimals=8,
        kinds={
            "signal_date": "date",
            "ts_code": "code",
            "stage_watch_rank": "integer",
            "trade_rank": "integer",
            "trade_shadow_selected": "integer",
            "watch_label": "exact_text",
            "action": "exact_text",
            "target_weight": "float",
        },
        strict=True,
    )
    formal_buy_count = int(action_plan.get("formal_buy_count") or 0)
    status = str(action_plan.get("status_code") or "")
    _require(formal_buy_count == 0, "action plan contains formal buys")
    _require(status == "NO_TRADE_MODEL_NOT_PROMOTED", "action plan status is not NO_TRADE")
    actions = indexed["action"].astype(str)
    _require(
        actions.isin(("REJECT", "SHADOW_ONLY")).all(),
        "watchlist action is invalid for NO_TRADE",
    )
    expected_shadow = set(
        indexed.assign(_trade_rank=trade_rank)
        .sort_values(
            ["_trade_rank", "ts_code"],
            ascending=[True, True],
            kind="mergesort",
        )
        .head(2)["ts_code"]
        .astype(str)
    )
    actual_shadow = set(indexed.loc[actions.eq("SHADOW_ONLY"), "ts_code"].astype(str))
    flagged_shadow = set(indexed.loc[trade_shadow.eq(1), "ts_code"].astype(str))
    shadow_labels = indexed.loc[actions.eq("SHADOW_ONLY"), "watch_label"].tolist()
    reject_labels = indexed.loc[actions.eq("REJECT"), "watch_label"].tolist()
    _require(
        len(expected_shadow) == 2
        and actual_shadow == expected_shadow
        and flagged_shadow == expected_shadow
        and all(value == "二筛影子" for value in shadow_labels)
        and all(value == "仅观察" for value in reject_labels),
        "relative-best-two shadow contract drifted",
    )
    _require(weight.eq(0.0).all(), "watchlist target_weight is nonzero")
    return {
        "columns": list(required),
        "rows": int(len(indexed)),
        "signal_date": signal_date,
        "action_plan_candidates_sha256_q8": fingerprint["sha256"],
        "formal_buy_count": formal_buy_count,
        "status_code": status,
        "shadow_only_count": int(actions.eq("SHADOW_ONLY").sum()),
        "reject_count": int(actions.eq("REJECT").sum()),
        "relative_best_two_exact": True,
        "all_target_weights_zero": True,
    }


def validate_reference_snapshot(
    reference_dir: Path,
    *,
    profile: str,
) -> dict[str, Any]:
    _require(
        profile in {"persisted-c6", "same-machine-c6"},
        f"unsupported reference profile: {profile}",
    )
    files: dict[str, Any] = {}
    for name, expected_blob in REFERENCE_C6_GIT_BLOBS.items():
        path = _reference_path(reference_dir, name)
        _require(path.is_file(), f"reference snapshot missing {name}")
        blob_sha = _git_blob_sha1(path)
        if profile == "persisted-c6":
            _require(
                blob_sha == expected_blob,
                f"untrusted persisted-c6 reference blob for {name}: {blob_sha}",
            )
        files[name] = {
            "path": str(path),
            "git_blob_sha1": blob_sha,
            "expected_git_blob_sha1": (
                expected_blob if profile == "persisted-c6" else None
            ),
            "content_sha256": _sha256(path),
        }
    return {
        "profile": profile,
        "files": files,
        "persisted_trust_root_verified": profile == "persisted-c6",
        "same_machine_reference_only": profile == "same-machine-c6",
    }


def candidate_source_evidence(root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=resolved,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip().lower()
    _require(
        completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
        "cannot resolve candidate base commit",
    )
    relative_paths = (
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/trade_selector.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/action_plan.py",
        "scripts/publish_decision_action.py",
        "scripts/replay_frozen_canonical_v2.py",
    )
    files: dict[str, str] = {}
    for relative in relative_paths:
        path = resolved / relative
        _require(path.is_file(), f"candidate source missing: {relative}")
        files[relative] = _sha256(path)
    return {"candidate_commit": commit, "file_sha256": files}


def _finite_policy_projection(
    fingerprint: dict[str, Any],
    *,
    label: str,
    threshold_names: tuple[str, ...],
) -> dict[str, Any]:
    projection = fingerprint.get("policy_projection")
    _require(isinstance(projection, dict), f"{label} policy projection missing")
    thresholds = projection.get("thresholds")
    _require(isinstance(thresholds, dict), f"{label} policy thresholds missing")
    for name in threshold_names:
        _require(name in thresholds, f"{label} policy missing threshold {name}")
        try:
            number = float(thresholds[name])
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"{label} policy threshold invalid: {name}") from exc
        _require(math.isfinite(number), f"{label} policy threshold non-finite: {name}")
    return projection


def _validate_fingerprint_envelope(
    fingerprint: dict[str, Any],
    *,
    layer: str,
    canonical_version: str,
) -> None:
    expected_keys = {
        "schema",
        "canonical_version",
        "canonical_contract",
        "provenance_sha256",
        "semantic_sha256",
        "policy_sha256",
        "policy_projection",
        "artifact_sha256",
        "schema_valid",
        "missing_columns",
        "invalid_cell_count",
    }
    _require(
        set(fingerprint) == expected_keys,
        f"{layer} V2 fingerprint envelope keys drifted",
    )
    _require(
        fingerprint.get("schema") == CANONICAL_FINGERPRINT_SCHEMA,
        f"{layer} V2 fingerprint schema drifted",
    )
    _require(
        fingerprint.get("canonical_version") == canonical_version,
        f"{layer} V2 canonical version drifted",
    )
    expected_contract = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "layer": layer,
        "decimals": 8,
        "rounding": "decimal_string_half_even",
        "execution_mode": "raw_float64",
        "raw_execution_preserved": True,
    }
    _require(
        fingerprint.get("canonical_contract") == expected_contract,
        f"{layer} V2 canonical contract drifted",
    )
    for name in (
        "provenance_sha256",
        "semantic_sha256",
        "policy_sha256",
        "artifact_sha256",
    ):
        _require(
            SHA256_RE.fullmatch(str(fingerprint.get(name) or "")) is not None,
            f"{layer} V2 {name} invalid",
        )
    _require(
        fingerprint.get("schema_valid") is True,
        f"{layer} V2 semantic schema is not valid",
    )
    _require(
        fingerprint.get("missing_columns") == [],
        f"{layer} V2 semantic columns missing",
    )
    _require(
        fingerprint.get("invalid_cell_count") == 0,
        f"{layer} V2 semantic invalid cells present",
    )


def validate_fingerprint_integrity(
    model_fingerprint: dict[str, Any],
    selector_fingerprint: dict[str, Any],
    *,
    live_model_policy: dict[str, Any],
    live_selector_policy: dict[str, Any],
) -> dict[str, Any]:
    _validate_fingerprint_envelope(
        model_fingerprint,
        layer="model",
        canonical_version=MODEL_CANONICAL_V2,
    )
    _validate_fingerprint_envelope(
        selector_fingerprint,
        layer="trade_selector",
        canonical_version=TRADE_SELECTOR_CANONICAL_V2,
    )
    model_projection = _finite_policy_projection(
        model_fingerprint,
        label="model V2",
        threshold_names=(
            "max_big_loss_probability",
            "min_mean_return_lcb",
            "min_fill_probability",
            "min_exit_probability",
            "min_conservative_ev",
            "min_selection_score",
        ),
    )
    selector_projection = _finite_policy_projection(
        selector_fingerprint,
        label="selector V2",
        threshold_names=(
            "min_trade_score",
            "min_mean_return_lcb",
            "min_fill_probability",
            "max_big_loss_probability",
        ),
    )
    live_model_projection = _model_executable_policy_projection(
        live_model_policy
    )
    live_selector_projection = _selector_executable_policy_projection(
        live_selector_policy
    )
    _require(
        live_model_projection == model_projection,
        "live model selection_policy differs from V2 policy projection",
    )
    _require(
        live_selector_projection == selector_projection,
        "live selector production_policy differs from V2 policy projection",
    )
    model_policy_sha = canonical_mapping_sha256(
        {
            "schema": CANONICAL_FINGERPRINT_SCHEMA,
            "artifact_kind": "decision_model_executable_policy",
            "projection": model_projection,
        },
        decimals=8,
        exact_strings=True,
    )
    selector_policy_sha = canonical_policy_fingerprint(
        selector_projection,
        decimals=8,
    )["sha256"]
    _require(
        model_fingerprint.get("policy_sha256") == model_policy_sha,
        "model V2 policy SHA does not recompute",
    )
    _require(
        selector_fingerprint.get("policy_sha256") == selector_policy_sha,
        "selector V2 policy SHA does not recompute",
    )
    model_artifact = compose_artifact_fingerprint(
        artifact_kind="decision_model_canonical_runtime_v2",
        provenance_sha256=str(model_fingerprint.get("provenance_sha256") or ""),
        semantic_sha256=str(model_fingerprint.get("semantic_sha256") or ""),
        policy_sha256=model_policy_sha,
        decimals=8,
    )
    selector_artifact = compose_artifact_fingerprint(
        artifact_kind="decision_trade_selector_canonical_runtime_v2",
        provenance_sha256=str(selector_fingerprint.get("provenance_sha256") or ""),
        semantic_sha256=str(selector_fingerprint.get("semantic_sha256") or ""),
        policy_sha256=selector_policy_sha,
        decimals=8,
    )
    _require(
        model_fingerprint.get("artifact_sha256") == model_artifact,
        "model V2 artifact does not recompose",
    )
    _require(
        selector_fingerprint.get("artifact_sha256") == selector_artifact,
        "selector V2 artifact does not recompose",
    )
    return {
        "model_policy_sha256": model_policy_sha,
        "model_artifact_sha256": model_artifact,
        "selector_policy_sha256": selector_policy_sha,
        "selector_artifact_sha256": selector_artifact,
        "model_projection": model_projection,
        "selector_projection": selector_projection,
        "live_policies_match_fingerprint_projection": True,
    }


def validate_prediction_policy_execution(
    prediction: pd.DataFrame,
    *,
    prediction_text: pd.DataFrame,
    model_projection: dict[str, Any],
    selector_projection: dict[str, Any],
) -> dict[str, Any]:
    model_thresholds = dict(model_projection["thresholds"])
    threshold_columns = {
        "max_big_loss_probability": "policy_max_big_loss_probability",
        "min_mean_return_lcb": "policy_min_mean_return_lcb",
        "min_fill_probability": "policy_min_fill_probability",
        "min_exit_probability": "policy_min_exit_probability",
        "min_conservative_ev": "policy_min_conservative_ev",
        "min_selection_score": "policy_min_selection_score",
    }
    policy_surface = _decimal_policy_surface_contract(
        prediction_text,
        parsed_rows=len(prediction),
        threshold_columns=threshold_columns,
        thresholds=model_thresholds,
    )
    numeric: dict[str, pd.Series] = {}
    for column in (
        "predicted_big_loss_probability",
        "predicted_mean_return_lcb",
        "predicted_fill_probability",
        "predicted_exit_probability",
        "conservative_ev",
        "selection_score",
    ):
        numeric[column] = _validated_numeric(
            prediction,
            column,
            label="prediction policy execution",
            allow_missing=False,
        )
    model_binary: dict[str, pd.Series] = {}
    for column in (
        "stage_focus",
        "gate_policy_ready",
        "gate_stage_focus",
        "gate_exit_probability",
        "gate_fill_probability",
        "gate_big_loss_probability",
        "gate_mean_return_lcb",
        "gate_conservative_ev",
        "gate_selection_score",
        "risk_gate_pass",
    ):
        model_binary[column] = _validated_binary(
            prediction,
            column,
            label="prediction policy execution",
        )
    model_expected = {
        "gate_policy_ready": pd.Series(
            int(model_projection["ready"] is True),
            index=prediction.index,
        ),
        "gate_stage_focus": model_binary["stage_focus"],
        "gate_exit_probability": numeric["predicted_exit_probability"].ge(
            float(model_thresholds["min_exit_probability"])
        ).astype(int),
        "gate_fill_probability": numeric["predicted_fill_probability"].ge(
            float(model_thresholds["min_fill_probability"])
        ).astype(int),
        "gate_big_loss_probability": numeric[
            "predicted_big_loss_probability"
        ].le(float(model_thresholds["max_big_loss_probability"])).astype(int),
        "gate_mean_return_lcb": numeric["predicted_mean_return_lcb"].ge(
            float(model_thresholds["min_mean_return_lcb"])
        ).astype(int),
        "gate_conservative_ev": numeric["conservative_ev"].ge(
            float(model_thresholds["min_conservative_ev"])
        ).astype(int),
        "gate_selection_score": numeric["selection_score"].ge(
            float(model_thresholds["min_selection_score"])
        ).astype(int),
    }
    for column, expected in model_expected.items():
        _require(
            model_binary[column].eq(expected).all(),
            f"prediction {column} disagrees with model V2 policy",
        )
    expected_risk = pd.Series(1, index=prediction.index, dtype=int)
    for column in (
        "gate_policy_ready",
        "gate_stage_focus",
        "gate_exit_probability",
        "gate_fill_probability",
        "gate_big_loss_probability",
        "gate_mean_return_lcb",
        "gate_conservative_ev",
        "gate_selection_score",
    ):
        expected_risk &= model_expected[column].astype(int)
    _require(
        model_binary["risk_gate_pass"].eq(expected_risk).all(),
        "prediction risk_gate_pass disagrees with six-gate policy",
    )

    selector_thresholds = dict(selector_projection["thresholds"])
    selector = _selector_prediction_domain_contract(prediction)
    domain = selector["domain"]
    selector_numeric = selector["numeric"]
    selector_binary = selector["binary"]
    qualifies = (
        selector_numeric["trade_score"].ge(
            float(selector_thresholds["min_trade_score"])
        )
        & selector_numeric["trade_predicted_mean_return_lcb"].ge(
            float(selector_thresholds["min_mean_return_lcb"])
        )
        & selector_numeric["trade_predicted_fill_probability"].ge(
            float(selector_thresholds["min_fill_probability"])
        )
        & selector_numeric["trade_predicted_big_loss_probability"].le(
            float(selector_thresholds["max_big_loss_probability"])
        )
    )
    ordered = domain.loc[qualifies].assign(
        _signal_date=domain.loc[qualifies, "signal_date"].astype(str),
        _score=selector_numeric["trade_score"].loc[qualifies],
        _big_loss=selector_numeric["trade_predicted_big_loss_probability"].loc[
            qualifies
        ],
        _promotion_rank=selector_numeric["promotion_rank"].loc[qualifies],
        _observation_rank=selector_numeric["observation_rank"].loc[qualifies],
    ).sort_values(
        [
            "_signal_date",
            "_score",
            "_big_loss",
            "_promotion_rank",
            "_observation_rank",
            "ts_code",
        ],
        ascending=[True, False, True, True, True, True],
        kind="stable",
    )
    max_positions = max(1, min(2, int(selector_projection["max_positions"])))
    eligible = ordered.loc[
        ordered.groupby("_signal_date", sort=False).cumcount() < max_positions
    ]
    expected_trade_gate = pd.Series(0, index=domain.index, dtype=int)
    expected_trade_gate.loc[eligible.index] = 1
    _require(
        selector_binary["trade_gate_pass"].eq(expected_trade_gate).all(),
        "prediction trade_gate_pass disagrees with selector V2 policy",
    )
    _require(
        selector_binary["trade_selector_policy_ready"].eq(
            int(selector_projection["ready"] is True)
        ).all(),
        "prediction selector policy-ready flag disagrees with V2 policy",
    )
    fill_contract = validate_prediction_fill_contract(
        prediction,
        selector_domain=selector,
    )
    return {
        "rows": int(len(prediction)),
        "selector_domain_rows": selector["rows"],
        "selector_outside_rows": selector["outside_rows"],
        "model_gate_columns_recomputed": sorted(model_expected),
        "model_risk_gate_pass_rows": int(expected_risk.sum()),
        "selector_trade_gate_pass_rows": int(expected_trade_gate.sum()),
        "policy_threshold_columns_match": True,
        "policy_threshold_text_surface": policy_surface,
        "fill_contract": fill_contract,
    }


def build_candidate_behavior_contract(root: Path) -> dict[str, Any]:
    metrics_root = root / "outputs" / "auction_v3" / "metrics"
    top10 = _read_csv(metrics_root / "backtest_top10_latest.csv")
    oos = _read_csv(metrics_root / "backtest_trade_selector_oos_latest.csv")
    backtest = _read_json(metrics_root / "backtest_latest.json")
    action_plan = _read_json(
        root / "outputs" / "decision" / "action_plan_latest.json"
    )
    return {
        "top10": _candidate_frame_contract(
            top10,
            label="Top10",
            discrete_columns=TOP10_DISCRETE_COLUMNS,
            score_columns=BASE_SCORE_COLUMNS,
        ),
        "selector_oos": _candidate_frame_contract(
            oos,
            label="selector OOS",
            discrete_columns=OOS_DISCRETE_COLUMNS,
            score_columns=(*BASE_SCORE_COLUMNS, *TRADE_SCORE_COLUMNS),
        ),
        "no_trade": {
            "promoted": backtest.get("promoted") is True,
            "signals": int(backtest.get("signals") or 0),
            "signal_dates": int(backtest.get("signal_dates") or 0),
            "filled_trades": int(backtest.get("filled_trades") or 0),
            "top10_selected": int(
                pd.to_numeric(top10["selected"], errors="raise").sum()
            ),
            "selector_global_promotion_rows": int(
                pd.to_numeric(
                    oos["trade_selector_globally_promoted"],
                    errors="raise",
                ).sum()
            ),
        },
        "research_oos": {
            "trade_selected": int(
                pd.to_numeric(oos["trade_selected"], errors="raise").sum()
            ),
            "trade_shadow_selected": int(
                pd.to_numeric(
                    oos["trade_shadow_selected"], errors="raise"
                ).sum()
            ),
            "selector_promoted_rows": int(
                pd.to_numeric(
                    oos["trade_selector_promoted"], errors="raise"
                ).sum()
            ),
            "selector_policy_ready_rows": int(
                pd.to_numeric(
                    oos["trade_selector_policy_ready"], errors="raise"
                ).sum()
            ),
        },
        "action_plan_candidates": _action_candidate_contract(action_plan),
    }


def compare_frozen_golden(
    root: Path,
    reference_dir: Path,
    *,
    reference_profile: str = "persisted-c6",
) -> dict[str, Any]:
    reference_evidence = validate_reference_snapshot(
        reference_dir,
        profile=reference_profile,
    )
    metrics_root = root / "outputs" / "auction_v3" / "metrics"
    top10 = _read_csv(metrics_root / "backtest_top10_latest.csv")
    oos = _read_csv(metrics_root / "backtest_trade_selector_oos_latest.csv")
    reference_top10 = _read_csv(_reference_path(reference_dir, "backtest_top10_latest.csv"))
    reference_oos = _read_csv(
        _reference_path(reference_dir, "backtest_trade_selector_oos_latest.csv")
    )
    reference_backtest = _read_json(
        _reference_path(reference_dir, "backtest_latest.json")
    )
    top10_behavior = _compare_behavior(
        reference_top10,
        top10,
        label="Top10",
        columns=TOP10_DISCRETE_COLUMNS,
    )
    oos_behavior = _compare_behavior(
        reference_oos,
        oos,
        label="selector OOS",
        columns=OOS_DISCRETE_COLUMNS,
    )
    _require(top10_behavior["rows"] == 4467, "Top10 row count changed")
    _require(top10_behavior["dates"] == 543, "Top10 date count changed")
    _require(oos_behavior["rows"] == 3097, "selector OOS row count changed")
    _require(oos_behavior["dates"] == 363, "selector OOS date count changed")
    candidate_top10_summary = {
        "shadow_selected": int(
            pd.to_numeric(top10["shadow_selected"], errors="raise").sum()
        ),
        "risk_gate_pass": int(
            pd.to_numeric(top10["risk_gate_pass"], errors="raise").sum()
        ),
        "observation_selected": int(
            pd.to_numeric(top10["observation_selected"], errors="raise").sum()
        ),
    }
    if reference_profile == "persisted-c6":
        _require(
            candidate_top10_summary
            == {
                "shadow_selected": 1069,
                "risk_gate_pass": 0,
                "observation_selected": 4467,
            },
            "persisted-c6 Top10 behavior summary drift",
        )

    backtest = _read_json(metrics_root / "backtest_latest.json")
    _require(backtest.get("promoted") is False, "candidate unexpectedly promoted")
    _require(int(backtest.get("signals") or 0) == 0, "formal signals are not NO_TRADE")
    _require(int(backtest.get("signal_dates") or 0) == 0, "formal signal dates are nonzero")
    _require(int(backtest.get("filled_trades") or 0) == 0, "formal fills are nonzero")
    _require(
        int(pd.to_numeric(top10["selected"], errors="raise").sum()) == 0,
        "Top10 contains a formal selection",
    )
    candidate_oos_summary = {
        "trade_selected": int(
            pd.to_numeric(oos["trade_selected"], errors="raise").sum()
        ),
        "trade_shadow_selected": int(
            pd.to_numeric(oos["trade_shadow_selected"], errors="raise").sum()
        ),
        "shadow_selected": int(
            pd.to_numeric(oos["shadow_selected"], errors="raise").sum()
        ),
        "selector_promoted_rows": int(
            pd.to_numeric(oos["trade_selector_promoted"], errors="raise").sum()
        ),
        "selector_global_promotion_rows": int(
            pd.to_numeric(
                oos["trade_selector_globally_promoted"],
                errors="raise",
            ).sum()
        ),
        "selector_policy_ready_rows": int(
            pd.to_numeric(
                oos["trade_selector_policy_ready"],
                errors="raise",
            ).sum()
        ),
    }
    reference_oos_summary = {
        key: int(
            pd.to_numeric(reference_oos[column], errors="raise").sum()
        )
        for key, column in {
            "trade_selected": "trade_selected",
            "trade_shadow_selected": "trade_shadow_selected",
            "shadow_selected": "shadow_selected",
            "selector_promoted_rows": "trade_selector_promoted",
            "selector_global_promotion_rows": "trade_selector_globally_promoted",
            "selector_policy_ready_rows": "trade_selector_policy_ready",
        }.items()
    }
    _require(
        candidate_oos_summary == reference_oos_summary,
        "selector research-OOS count summary changed",
    )
    _require(
        candidate_oos_summary["selector_global_promotion_rows"] == 0,
        "selector OOS was globally promoted",
    )
    selector = dict(backtest.get("trade_selector") or {})
    _require(selector.get("promoted") is False, "selector unexpectedly promoted")
    _require(
        (selector.get("production_policy") or {}).get("ready") is False,
        "selector production policy unexpectedly ready",
    )
    formal = (selector.get("formal_policy_oos") or {}).get("all_candidates") or {}
    reference_selector = dict(reference_backtest.get("trade_selector") or {})
    reference_formal = (
        (reference_selector.get("formal_policy_oos") or {}).get("all_candidates")
        or {}
    )
    candidate_formal_summary = {
        field: int(formal.get(field) or 0)
        for field in ("signals", "signal_dates", "filled_trades")
    }
    reference_formal_summary = {
        field: int(reference_formal.get(field) or 0)
        for field in ("signals", "signal_dates", "filled_trades")
    }
    candidate_market_buyable_fills = int(
        (
            (selector.get("formal_policy_oos") or {}).get("market_buyable_only")
            or {}
        ).get("filled_trades")
        or 0
    )
    reference_market_buyable_fills = int(
        (
            (reference_selector.get("formal_policy_oos") or {}).get(
                "market_buyable_only"
            )
            or {}
        ).get("filled_trades")
        or 0
    )
    _require(
        candidate_formal_summary == reference_formal_summary,
        "selector research-OOS metric summary changed",
    )
    _require(
        candidate_market_buyable_fills == reference_market_buyable_fills,
        "selector research-OOS buyable-fill count changed",
    )
    if reference_profile == "persisted-c6":
        _require(
            candidate_oos_summary
            == {
                "trade_selected": 158,
                "trade_shadow_selected": 523,
                "shadow_selected": 726,
                "selector_promoted_rows": 3097,
                "selector_global_promotion_rows": 0,
                "selector_policy_ready_rows": 1083,
            },
            "persisted-c6 selector research-OOS summary drift",
        )
        _require(
            candidate_formal_summary
            == {"signals": 158, "signal_dates": 119, "filled_trades": 158},
            "persisted-c6 selector research-OOS metrics drift",
        )
        _require(
            candidate_market_buyable_fills == 25,
            "persisted-c6 selector buyable-fill count drift",
        )

    expected_model_contract = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "layer": "model",
        "decimals": 8,
        "rounding": "decimal_string_half_even",
        "execution_mode": "raw_float64",
        "raw_execution_preserved": True,
    }
    expected_selector_contract = {
        **expected_model_contract,
        "layer": "trade_selector",
    }
    _require(
        backtest.get("model_canonical_contract") == expected_model_contract,
        "backtest model canonical contract drift",
    )
    model_artifact = str(backtest.get("model_artifact_v2_sha256") or "")
    _require(SHA256_RE.fullmatch(model_artifact) is not None, "model V2 artifact invalid")
    model_fingerprint = dict(backtest.get("model_fingerprint_v2") or {})
    model_v2_version = str(model_fingerprint.get("canonical_version") or "")
    _require(model_v2_version != "", "model V2 canonical version missing")
    _require(
        backtest.get("model_canonical_v2_version") == model_v2_version,
        "backtest model V2 version drift",
    )
    _require(
        model_fingerprint.get("artifact_sha256") == model_artifact,
        "backtest model V2 fingerprint mismatch",
    )
    _require(
        selector.get("canonical_contract") == expected_selector_contract,
        "selector canonical contract drift",
    )
    selector_artifact = str(selector.get("production_artifact_v2_sha256") or "")
    _require(
        SHA256_RE.fullmatch(selector_artifact) is not None,
        "selector V2 artifact invalid",
    )
    selector_fingerprint = dict(selector.get("production_fingerprint_v2") or {})
    selector_v2_version = str(
        selector_fingerprint.get("canonical_version") or ""
    )
    _require(selector_v2_version != "", "selector V2 canonical version missing")
    _require(
        selector.get("canonical_v2_version") == selector_v2_version,
        "backtest selector V2 version drift",
    )
    _require(
        selector_fingerprint.get("artifact_sha256") == selector_artifact,
        "selector V2 fingerprint mismatch",
    )

    model_meta = _read_json(
        root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
    )
    _require(model_meta.get("model_canonical_contract") == expected_model_contract, "meta model contract drift")
    _require(
        model_meta.get("model_canonical_v2_version") == model_v2_version,
        "meta model V2 version drift",
    )
    _require(model_meta.get("model_artifact_v2_sha256") == model_artifact, "meta model artifact drift")
    _require(
        model_meta.get("model_fingerprint_v2") == model_fingerprint,
        "meta model V2 fingerprint object drift",
    )
    _require(
        ((model_meta.get("trade_selector") or {}).get("production_artifact_v2_sha256"))
        == selector_artifact,
        "meta selector artifact drift",
    )
    _require(
        ((model_meta.get("trade_selector") or {}).get("canonical_v2_version"))
        == selector_v2_version,
        "meta selector V2 version drift",
    )
    _require(
        ((model_meta.get("trade_selector") or {}).get("production_fingerprint_v2"))
        == selector_fingerprint,
        "meta selector V2 fingerprint object drift",
    )
    fingerprint_integrity = validate_fingerprint_integrity(
        model_fingerprint,
        selector_fingerprint,
        live_model_policy=dict(model_meta.get("selection_policy") or {}),
        live_selector_policy=dict(selector.get("production_policy") or {}),
    )
    prediction_path = (
        root / "outputs" / "auction_v3" / "predictions" / "pred_latest.csv"
    )
    prediction = _read_csv(prediction_path)
    prediction_text = _read_csv_exact_text(prediction_path)
    for column, expected in (
        ("model_artifact_v2_sha256", model_artifact),
        ("trade_selector_artifact_v2_sha256", selector_artifact),
        ("model_canonical_v2_version", model_v2_version),
        ("trade_selector_canonical_v2_version", selector_v2_version),
        ("model_canonical_schema", CANONICAL_FINGERPRINT_SCHEMA),
        ("trade_selector_canonical_schema", CANONICAL_FINGERPRINT_SCHEMA),
        ("model_execution_numeric_mode", "raw_float64"),
        ("trade_selector_execution_numeric_mode", "raw_float64"),
    ):
        values = set(prediction[column].dropna().astype(str))
        _require(values == {expected}, f"prediction {column} drift")
    for column, expected in (
        ("model_canonical_decimals", 8),
        ("trade_selector_canonical_decimals", 8),
        ("model_raw_execution_preserved", 1),
        ("trade_selector_raw_execution_preserved", 1),
    ):
        values = _validated_numeric(
            prediction,
            column,
            label="prediction V2 contract",
            allow_missing=False,
            integral=True,
        )
        _require(values.eq(expected).all(), f"prediction {column} drift")
    policy_execution = validate_prediction_policy_execution(
        prediction,
        prediction_text=prediction_text,
        model_projection=fingerprint_integrity["model_projection"],
        selector_projection=fingerprint_integrity["selector_projection"],
    )
    action_plan = _read_json(
        root / "outputs" / "decision" / "action_plan_latest.json"
    )
    action_model = dict(action_plan.get("model") or {})
    action_candidate_contract = _action_candidate_contract(action_plan)
    _require(
        action_plan.get("status_code") == "NO_TRADE_MODEL_NOT_PROMOTED",
        "action plan is not frozen NO_TRADE",
    )
    _require(int(action_plan.get("formal_buy_count") or 0) == 0, "action plan has formal buys")
    for field in (
        "canonical_v2_versions_match",
        "artifact_v2_fingerprints_match",
        "fingerprint_v2_valid",
        "canonical_contracts_match",
        "canonical_decimals_match",
        "raw_execution_preserved",
        "trade_selector_canonical_v2_versions_match",
        "trade_selector_artifacts_v2_match",
        "trade_selector_fingerprint_v2_valid",
        "trade_selector_canonical_contracts_match",
        "trade_selector_canonical_decimals_match",
        "trade_selector_raw_execution_preserved",
    ):
        _require(action_model.get(field) is True, f"action plan V2 integrity failed: {field}")
    _require(action_model.get("artifact_v2_sha256") == model_artifact, "action model artifact drift")
    _require(
        action_model.get("canonical_v2_version") == model_v2_version,
        "action model V2 version drift",
    )
    _require(
        action_model.get("fingerprint_v2") == model_fingerprint,
        "action model V2 fingerprint object drift",
    )
    _require(
        action_model.get("trade_selector_artifact_v2_sha256") == selector_artifact,
        "action selector artifact drift",
    )
    _require(
        action_model.get("trade_selector_canonical_v2_version")
        == selector_v2_version,
        "action selector V2 version drift",
    )
    _require(
        action_model.get("trade_selector_fingerprint_v2")
        == selector_fingerprint,
        "action selector V2 fingerprint object drift",
    )
    _require(action_model.get("canonical_contract") == expected_model_contract, "action model contract drift")
    _require(
        action_model.get("trade_selector_canonical_contract")
        == expected_selector_contract,
        "action selector contract drift",
    )

    return {
        "status": "pass",
        "reference": reference_evidence,
        "behavior_contract_candidate": build_candidate_behavior_contract(root),
        "top10": top10_behavior,
        "selector_oos": oos_behavior,
        "top10_summary": candidate_top10_summary,
        "research_oos_summary": candidate_oos_summary,
        "research_oos_metrics": {
            **candidate_formal_summary,
            "market_buyable_filled_trades": candidate_market_buyable_fills,
        },
        "fingerprint_integrity": fingerprint_integrity,
        "prediction_policy_execution": policy_execution,
        "action_plan_candidates": action_candidate_contract,
        "no_trade": {
            "promoted": False,
            "formal_signals": 0,
            "selector_promoted": False,
            "selector_global_promotion_rows": 0,
            "action_formal_buy_count": 0,
        },
        "canonical_scores": {
            "top10": _canonical_score_comparison(
                reference_top10,
                top10,
                label="Top10",
                columns=BASE_SCORE_COLUMNS,
            ),
            "selector_oos": _canonical_score_comparison(
                reference_oos,
                oos,
                label="selector OOS",
                columns=(*BASE_SCORE_COLUMNS, *TRADE_SCORE_COLUMNS),
            ),
        },
        "execution_numeric": {
            "mode": "raw_float64",
            "quantized_execution_fields": [],
            "raw_execution_preserved": True,
        },
    }


def run_forced_replay(
    root: Path,
    *,
    signal_date: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    history, _forced_manifest, history_audit = load_forced_frozen_history(root)
    config = AuctionV3Config(root=root.resolve())
    engine = DiagnosticFrozenEngine(config, history, history_audit)
    # Mandatory in diagnostic mode: a pre-existing dated prediction may be V1
    # or from another replay.  Replacement is confined to this runner checkout.
    result = engine.run(signal_date, force_prediction=True)
    published = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "publish_decision_action.py"),
            "--root",
            str(root),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    _require(
        published.returncode == 0,
        "workspace action-plan publish failed: "
        + (published.stderr.strip() or published.stdout.strip()),
    )
    try:
        publish_payload = json.loads(published.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("action-plan publish did not emit JSON") from exc
    return asdict(result), history_audit, publish_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Force SHA-locked history through raw-execution canonical V2 in a disposable checkout"
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--signal-date", default="")
    parser.add_argument("--reference-dir", default="")
    parser.add_argument(
        "--reference-profile",
        choices=("persisted-c6", "same-machine-c6"),
        default="persisted-c6",
        help=(
            "persisted-c6 requires the four immutable remote blobs; "
            "same-machine-c6 is only for local raw-execution equivalence"
        ),
    )
    parser.add_argument("--report", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    manifest_path = root / "models" / "decision_model_freeze.json"
    manifest_before = _sha256(manifest_path)
    try:
        source_evidence = candidate_source_evidence(root)
        result, history_audit, action_publish = run_forced_replay(
            root,
            signal_date=args.signal_date,
        )
        _require(_sha256(manifest_path) == manifest_before, "freeze manifest was mutated")
        behavior_contract_candidate = build_candidate_behavior_contract(root)
        golden = (
            compare_frozen_golden(
                root,
                Path(args.reference_dir).resolve(),
                reference_profile=args.reference_profile,
            )
            if args.reference_dir
            else {"status": "not_requested"}
        )
        payload = {
            "status": "pass",
            "diagnostic_mode": "workspace_only_forced_frozen_canonical_v2",
            "force_prediction": True,
            "candidate_source": source_evidence,
            "history": history_audit,
            "run_result": result,
            "action_plan_publish": action_publish,
            "behavior_contract_candidate": behavior_contract_candidate,
            "golden": golden,
        }
    except (DecisionModelFreezeError, RuntimeError, ValueError) as exc:
        payload = {
            "status": "fail",
            "diagnostic_mode": "workspace_only_forced_frozen_canonical_v2",
            "force_prediction": True,
            "error": str(exc),
        }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
