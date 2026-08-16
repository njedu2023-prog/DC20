from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_frame_fingerprint,
    canonical_float_token,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
    normalize_date,
)


LEGACY_FREEZE_SCHEMA_VERSION = "decision_model_freeze_v1"
FREEZE_SCHEMA_VERSION = "decision_model_freeze_v2"
CANONICAL_RUNTIME_SCHEMA_VERSION = "decision_runtime_canonical_contract_v2"
BEHAVIOR_SCHEMA_VERSION = "decision_frozen_behavior_v2"
DEFAULT_FREEZE_PATH = Path("models/decision_model_freeze.json")
DATE_PATTERN = re.compile(r"^20\d{6}$")
CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GITHUB_RUN_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")

KNOWN_HISTORY_PATH = "models/decision_v12_frozen_history_20260805.csv.gz"
KNOWN_HISTORY_SHA256 = (
    "77e48be6732a08698a6abf4a0da74cb02b3129c57d14be66fb94679816a5337e"
)
KNOWN_HISTORY_ROWS = 40355
KNOWN_TOP10_ROWS = 4467
KNOWN_TOP10_DATES = 543
KNOWN_OOS_ROWS = 3097
KNOWN_OOS_DATES = 363
KNOWN_NESTED_OOS_SIGNALS = 158
KNOWN_NESTED_OOS_SIGNAL_DATES = 119
KNOWN_NESTED_OOS_FILLED_TRADES = 158
KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES = 25
KNOWN_NESTED_OOS_TRADE_SELECTED = 158
KNOWN_ACTION_SHADOW_ROWS = 2
KNOWN_REFERENCE_EVIDENCE = {
    "baseline_commit": "c6de497aaab48c40e205aa7fe8401ad6ad9780ad",
    "top10_blob_sha1": "1bbebbbe4a3b94c0a95fd64f4e27b242ea5b0222",
    "trade_selector_oos_blob_sha1": "6afd29e31cf98c434ac6e67183f7005a89663a49",
    "backtest_blob_sha1": "e27511643fc5aa1ee5bdb60f1d3b15b7e90adef4",
    "model_meta_blob_sha1": "9fee4a2bc9904bf703a292b5df3c367c4c39712b",
}
REQUIRED_ACTIVE_PIN_PATHS = frozenset(
    {
        ".github/workflows/backfill_decision_v11_history.yml",
        ".github/workflows/diagnose_decision_fingerprint.yml",
        ".github/workflows/run_auction_v3.yml",
        ".github/workflows/run_decision_daily.yml",
        ".github/workflows/verify_decision_observations.yml",
        "requirements.lock",
        "models/decision_v12_frozen_history_20260805.csv.gz",
        "scripts/diagnose_decision_fingerprint.py",
        "scripts/publish_decision_action.py",
        "scripts/run_auction_v3.py",
        "scripts/validate_decision_model_freeze.py",
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/action_plan.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/model_freeze.py",
        "src/top10decision/decision/observation.py",
        "src/top10decision/decision/trade_selector.py",
    }
)

CANONICAL_CONTRACT_KEYS = frozenset(
    {
        "schema",
        "layer",
        "decimals",
        "rounding",
        "execution_mode",
        "raw_execution_preserved",
    }
)
FINGERPRINT_KEYS = frozenset(
    {
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
)

IDENTITY_COLUMNS = ("signal_date", "ts_code")
GATE_DISCRETE_BEHAVIOR_COLUMNS = (
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
TOP10_DISCRETE_BEHAVIOR_COLUMNS = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
OOS_DISCRETE_BEHAVIOR_COLUMNS = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "promotion_rank",
    "trade_rank",
    "trade_gate_pass",
    "trade_selected",
    "trade_shadow_selected",
    "trade_model_reason",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    "trade_selector_promoted",
    "trade_selector_globally_promoted",
    "trade_selector_policy_ready",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
TOP10_SCORE_COLUMNS = (
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
    "diagnostic_gap",
    "recommended_max_gap",
    "policy_max_big_loss_probability",
    "policy_min_mean_return_lcb",
    "policy_min_fill_probability",
    "policy_min_exit_probability",
    "policy_min_conservative_ev",
    "policy_min_selection_score",
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
    "trade_tail_risk_weight",
    "trade_base_score",
    "trade_score",
)
OOS_SCORE_COLUMNS = (*TOP10_SCORE_COLUMNS, *TRADE_SCORE_COLUMNS)

PREDICTION_FILL_RELATIONSHIP_COLUMNS = (
    "observation_selected",
    "predicted_fill_probability",
    "predicted_public_market_buyable_probability",
    "trade_predicted_fill_probability",
    "trade_predicted_public_market_buyable_probability",
    "actual_order_fill_probability_available",
    "predicted_actual_order_fill_probability",
)

MODEL_PREDICTION_CANONICAL_COLUMNS = (
    "model_canonical_v2_version",
    "model_artifact_v2_sha256",
    "model_canonical_schema",
    "model_canonical_decimals",
    "model_execution_numeric_mode",
    "model_raw_execution_preserved",
)
SELECTOR_PREDICTION_CANONICAL_COLUMNS = (
    "trade_selector_canonical_v2_version",
    "trade_selector_artifact_v2_sha256",
    "trade_selector_canonical_schema",
    "trade_selector_canonical_decimals",
    "trade_selector_execution_numeric_mode",
    "trade_selector_raw_execution_preserved",
)
SELECTOR_PREDICTION_GLOBAL_COLUMNS = tuple(
    column
    for column in SELECTOR_PREDICTION_CANONICAL_COLUMNS
    if column != "trade_selector_artifact_v2_sha256"
)
SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS = (
    "promotion_rank",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_rank",
    "trade_score",
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_big_loss_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_base_score",
    "trade_tail_risk_weight",
)
SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS = (
    "trade_gate_pass",
    "trade_shadow_selected",
    "trade_selected",
    "trade_selector_policy_ready",
)

INTEGER_BEHAVIOR_COLUMNS = frozenset(
    {
        "observation_rank",
        "promotion_rank",
        "trade_rank",
        "shadow_rank",
        "policy_max_positions",
        "observation_risk_tier",
    }
)
BOOLEAN_BEHAVIOR_COLUMNS = frozenset(
    {
        "trade_gate_pass",
        "trade_selected",
        "trade_shadow_selected",
        "shadow_selected",
        "selected",
        "risk_gate_pass",
        "trade_selector_globally_promoted",
        "trade_selector_policy_ready",
        "trade_selector_promoted",
        "stage_focus",
        "observation_selected",
        *GATE_DISCRETE_BEHAVIOR_COLUMNS,
    }
)
TEXT_BEHAVIOR_COLUMNS = frozenset(
    {
        "model_reason",
        "trade_model_reason",
        "observation_risk_label",
        "selection_policy_version",
    }
)

MODEL_POLICY_KEYS = frozenset(
    {"version", "ready", "reason", "max_positions", "thresholds"}
)
MODEL_POLICY_THRESHOLD_KEYS = frozenset(
    {
        "max_big_loss_probability",
        "min_mean_return_lcb",
        "min_fill_probability",
        "min_exit_probability",
        "min_conservative_ev",
        "min_selection_score",
    }
)
SELECTOR_POLICY_KEYS = frozenset(
    {
        "version",
        "ready",
        "reason",
        "max_positions",
        "tail_risk_weight",
        "thresholds",
    }
)
SELECTOR_POLICY_THRESHOLD_KEYS = frozenset(
    {
        "min_trade_score",
        "min_mean_return_lcb",
        "min_fill_probability",
        "max_big_loss_probability",
    }
)


class DecisionModelFreezeError(RuntimeError):
    """Raised when a frozen Decision production contract drifts."""


def _valid_date(value: str) -> bool:
    if not DATE_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _fail(message: str) -> None:
    raise DecisionModelFreezeError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DecisionModelFreezeError(f"JSON artifact missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionModelFreezeError(f"JSON artifact unreadable: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"JSON artifact must be an object: {path}")
    return payload


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{context} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], context: str
) -> None:
    actual = frozenset(str(key) for key in value)
    if actual != expected:
        _fail(
            f"{context} keys drift: missing={sorted(expected - actual)!r} "
            f"unexpected={sorted(actual - expected)!r}"
        )


def _require_bool(value: Any, context: str) -> bool:
    if type(value) is not bool:
        _fail(f"{context} must be boolean")
    return value


def _require_int(
    value: Any,
    context: str,
    *,
    minimum: int | None = None,
    exact: int | None = None,
) -> int:
    if type(value) is not int:
        _fail(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        _fail(f"{context} must be >= {minimum}")
    if exact is not None and value != exact:
        _fail(f"{context} must equal {exact}")
    return value


def _require_binary_int(value: Any, context: str) -> int:
    number = _require_int(value, context)
    if number not in (0, 1):
        _fail(f"{context} must equal 0 or 1")
    return number


def _require_text(value: Any, context: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context} must be a nonempty string")
    if value != value.strip():
        _fail(f"{context} must not have surrounding whitespace")
    if exact is not None and value != exact:
        _fail(f"{context} must equal {exact!r}")
    return value


def _require_sha256(value: Any, context: str) -> str:
    text = _require_text(value, context)
    if not SHA256_PATTERN.fullmatch(text):
        _fail(f"{context} must be a lowercase 64-hex SHA-256")
    return text


def _require_string_list(
    value: Any,
    context: str,
    *,
    exact: Sequence[str] | None = None,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        _fail(f"{context} must be a list of nonempty strings")
    if not allow_empty and not value:
        _fail(f"{context} must not be empty")
    if len(set(value)) != len(value):
        _fail(f"{context} contains duplicate names")
    if exact is not None and value != list(exact):
        _fail(f"{context} must equal the reviewed exact column list")
    return list(value)


def _safe_repository_path(
    root: Path | str,
    value: Any,
    context: str,
    *,
    suffix: str | None = None,
) -> Path:
    text = _require_text(value, context)
    relative = Path(text)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or "\\" in text
        or "\x00" in text
        or (suffix is not None and not text.endswith(suffix))
    ):
        _fail(f"{context} must be a safe repository-relative path")
    root_path = Path(root).resolve()
    candidate = root_path / relative
    probe = root_path
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            _fail(f"{context} must not traverse a symlink: {text}")
    try:
        candidate.resolve(strict=False).relative_to(root_path)
    except ValueError:
        _fail(f"{context} escapes repository root: {text}")
    return candidate


def frame_columns_sha256(columns: Sequence[str]) -> str:
    payload = json.dumps(
        list(columns), ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_canonical_contract(
    value: Any, *, layer: str, context: str
) -> dict[str, Any]:
    contract = _require_mapping(value, context)
    _require_exact_keys(contract, CANONICAL_CONTRACT_KEYS, context)
    _require_text(
        contract["schema"], f"{context}.schema", exact=CANONICAL_FINGERPRINT_SCHEMA
    )
    _require_text(contract["layer"], f"{context}.layer", exact=layer)
    _require_int(contract["decimals"], f"{context}.decimals", exact=8)
    _require_text(
        contract["rounding"],
        f"{context}.rounding",
        exact="decimal_string_half_even",
    )
    _require_text(
        contract["execution_mode"],
        f"{context}.execution_mode",
        exact="raw_float64",
    )
    if not _require_bool(
        contract["raw_execution_preserved"],
        f"{context}.raw_execution_preserved",
    ):
        _fail(f"{context}.raw_execution_preserved must be true")
    return contract


def _validate_policy_projection(
    value: Any, *, layer: str, context: str
) -> dict[str, Any]:
    projection = _require_mapping(value, context)
    expected_keys = MODEL_POLICY_KEYS if layer == "model" else SELECTOR_POLICY_KEYS
    threshold_keys = (
        MODEL_POLICY_THRESHOLD_KEYS
        if layer == "model"
        else SELECTOR_POLICY_THRESHOLD_KEYS
    )
    _require_exact_keys(projection, expected_keys, context)
    thresholds = _require_mapping(projection["thresholds"], f"{context}.thresholds")
    _require_exact_keys(thresholds, threshold_keys, f"{context}.thresholds")
    if not isinstance(projection["version"], str) or projection["version"] == "":
        _fail(f"{context}.version must be an exact nonempty string")
    if not isinstance(projection["reason"], str) or projection["reason"] == "":
        _fail(f"{context}.reason must be an exact nonempty string")
    _require_bool(projection["ready"], f"{context}.ready")
    _require_int(
        projection["max_positions"],
        f"{context}.max_positions",
        minimum=0 if layer == "model" else 1,
    )
    numeric_values = list(thresholds.items())
    if layer == "trade_selector":
        numeric_values.append(("tail_risk_weight", projection["tail_risk_weight"]))
    for name, raw in numeric_values:
        if type(raw) not in (int, float):
            _fail(f"{context}.{name} must be finite numeric")
        number = float(raw)
        if not math.isfinite(number):
            _fail(f"{context}.{name} must be finite numeric")
    return projection


def _validate_fingerprint(
    value: Any,
    *,
    layer: str,
    canonical_version: str,
    contract: dict[str, Any],
    artifact_sha256: str,
    context: str,
) -> dict[str, Any]:
    fingerprint = _require_mapping(value, context)
    _require_exact_keys(fingerprint, FINGERPRINT_KEYS, context)
    _require_text(
        fingerprint["schema"],
        f"{context}.schema",
        exact=CANONICAL_FINGERPRINT_SCHEMA,
    )
    _require_text(
        fingerprint["canonical_version"],
        f"{context}.canonical_version",
        exact=canonical_version,
    )
    if fingerprint["canonical_contract"] != contract:
        _fail(f"{context}.canonical_contract differs from its outer contract")
    provenance = _require_sha256(
        fingerprint["provenance_sha256"], f"{context}.provenance_sha256"
    )
    semantic = _require_sha256(
        fingerprint["semantic_sha256"], f"{context}.semantic_sha256"
    )
    policy_sha = _require_sha256(
        fingerprint["policy_sha256"], f"{context}.policy_sha256"
    )
    projection = _validate_policy_projection(
        fingerprint["policy_projection"],
        layer=layer,
        context=f"{context}.policy_projection",
    )
    recomputed_policy = (
        canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=contract["decimals"],
            exact_strings=True,
        )
        if layer == "model"
        else canonical_policy_fingerprint(
            projection, decimals=contract["decimals"]
        )["sha256"]
    )
    if recomputed_policy != policy_sha:
        _fail(f"{context}.policy_sha256 does not match policy_projection")
    actual_artifact = _require_sha256(
        fingerprint["artifact_sha256"], f"{context}.artifact_sha256"
    )
    if actual_artifact != artifact_sha256:
        _fail(f"{context}.artifact_sha256 differs from outer artifact pin")
    artifact_kind = (
        "decision_model_canonical_runtime_v2"
        if layer == "model"
        else "decision_trade_selector_canonical_runtime_v2"
    )
    if compose_artifact_fingerprint(
        artifact_kind=artifact_kind,
        provenance_sha256=provenance,
        semantic_sha256=semantic,
        policy_sha256=policy_sha,
        decimals=contract["decimals"],
    ) != actual_artifact:
        _fail(f"{context}.artifact_sha256 does not match V2 components")
    if not _require_bool(fingerprint["schema_valid"], f"{context}.schema_valid"):
        _fail(f"{context}.schema_valid must be true")
    _require_string_list(
        fingerprint["missing_columns"],
        f"{context}.missing_columns",
        exact=(),
        allow_empty=True,
    )
    _require_int(
        fingerprint["invalid_cell_count"],
        f"{context}.invalid_cell_count",
        exact=0,
    )
    return fingerprint


def _validate_canonical_layer(
    value: Any, *, layer: str, context: str
) -> dict[str, Any]:
    layer_contract = _require_mapping(value, context)
    _require_exact_keys(
        layer_contract,
        frozenset(
            {
                "canonical_v2_version",
                "artifact_v2_sha256",
                "fingerprint_v2",
                "canonical_contract",
            }
        ),
        context,
    )
    version = _require_text(
        layer_contract["canonical_v2_version"], f"{context}.canonical_v2_version"
    )
    artifact = _require_sha256(
        layer_contract["artifact_v2_sha256"], f"{context}.artifact_v2_sha256"
    )
    contract = _validate_canonical_contract(
        layer_contract["canonical_contract"],
        layer=layer,
        context=f"{context}.canonical_contract",
    )
    _validate_fingerprint(
        layer_contract["fingerprint_v2"],
        layer=layer,
        canonical_version=version,
        contract=contract,
        artifact_sha256=artifact,
        context=f"{context}.fingerprint_v2",
    )
    return layer_contract


def _validate_history_manifest(
    root: Path, snapshot_value: Any, *, active: bool
) -> dict[str, Any]:
    snapshot = _require_mapping(snapshot_value, "history_snapshot")
    path = _safe_repository_path(
        root, snapshot.get("path"), "history_snapshot.path", suffix=".csv.gz"
    )
    sha = _require_sha256(snapshot.get("sha256"), "history_snapshot.sha256")
    rows = _require_int(snapshot.get("rows"), "history_snapshot.rows", minimum=1)
    if _require_bool(
        snapshot.get("bootstrap_mode"), "history_snapshot.bootstrap_mode"
    ):
        _fail("history_snapshot.bootstrap_mode must be false in schema V2")
    schema = _require_mapping(snapshot.get("schema"), "history_snapshot.schema")
    _require_exact_keys(
        schema,
        frozenset({"required_columns", "columns_sha256"}),
        "history_snapshot.schema",
    )
    required_columns = _require_string_list(
        schema["required_columns"], "history_snapshot.schema.required_columns"
    )
    for required in IDENTITY_COLUMNS:
        if required not in required_columns:
            _fail(f"history_snapshot requires column {required!r}")
    _require_sha256(
        schema["columns_sha256"], "history_snapshot.schema.columns_sha256"
    )
    if active and (
        path.relative_to(root).as_posix() != KNOWN_HISTORY_PATH
        or sha != KNOWN_HISTORY_SHA256
        or rows != KNOWN_HISTORY_ROWS
    ):
        _fail("active V2 freeze must pin the reviewed 40,355-row SHA77e snapshot")
    return snapshot


def _validate_behavior_dataset_contract(
    root: Path,
    value: Any,
    *,
    name: str,
    discrete_columns: Sequence[str],
    score_columns: Sequence[str],
    expected_decimals: int,
) -> dict[str, Any]:
    context = f"behavior_contract.{name}"
    contract = _require_mapping(value, context)
    _require_exact_keys(
        contract,
        frozenset(
            {
                "path",
                "rows",
                "signal_dates",
                "score_decimals",
                "identity_columns",
                "discrete_columns",
                "score_columns",
                "identity_sha256",
                "date_counts_sha256",
                "discrete_sha256",
                "scores_sha256",
            }
        ),
        context,
    )
    _safe_repository_path(root, contract["path"], f"{context}.path", suffix=".csv")
    _require_int(contract["rows"], f"{context}.rows", minimum=1)
    _require_int(contract["signal_dates"], f"{context}.signal_dates", minimum=1)
    _require_int(
        contract["score_decimals"],
        f"{context}.score_decimals",
        exact=expected_decimals,
    )
    _require_string_list(
        contract["identity_columns"],
        f"{context}.identity_columns",
        exact=IDENTITY_COLUMNS,
    )
    _require_string_list(
        contract["discrete_columns"],
        f"{context}.discrete_columns",
        exact=discrete_columns,
    )
    _require_string_list(
        contract["score_columns"],
        f"{context}.score_columns",
        exact=score_columns,
    )
    for key in (
        "identity_sha256",
        "date_counts_sha256",
        "discrete_sha256",
        "scores_sha256",
    ):
        _require_sha256(contract[key], f"{context}.{key}")
    return contract


ACTION_WATCHLIST_COLUMNS = (
    "ts_code",
    "action",
    "stage_watch_rank",
    "watch_label",
    "target_weight",
)


def _validate_action_watchlist_contract(root: Path, value: Any) -> dict[str, Any]:
    context = "behavior_contract.action_watchlist"
    contract = _require_mapping(value, context)
    _require_exact_keys(
        contract,
        frozenset(
            {
                "path",
                "rows",
                "columns",
                "sha256",
                "unique_codes",
                "shadow_only_rows",
            }
        ),
        context,
    )
    _safe_repository_path(root, contract["path"], f"{context}.path", suffix=".json")
    _require_int(contract["rows"], f"{context}.rows", minimum=0)
    _require_string_list(
        contract["columns"],
        f"{context}.columns",
        exact=ACTION_WATCHLIST_COLUMNS,
    )
    _require_sha256(contract["sha256"], f"{context}.sha256")
    if not _require_bool(contract["unique_codes"], f"{context}.unique_codes"):
        _fail(f"{context}.unique_codes must be true")
    _require_int(
        contract["shadow_only_rows"],
        f"{context}.shadow_only_rows",
        minimum=0,
    )
    return contract


def _validate_precision_evidence(value: Any) -> None:
    context = "production.canonical_v2.precision_evidence"
    evidence = _require_mapping(value, context)
    _require_exact_keys(
        evidence,
        frozenset(
            {
                "baseline_commit",
                "candidate_commit",
                "github_run_ids",
                "probes",
                "identity_and_discrete_changed",
                "formal_no_trade_preserved",
                "material_mutation_probe_passed",
            }
        ),
        context,
    )
    for key in ("baseline_commit", "candidate_commit"):
        commit = _require_text(evidence[key], f"{context}.{key}")
        if not GIT_SHA_PATTERN.fullmatch(commit):
            _fail(f"{context}.{key} must be a 40-hex commit")
    run_ids = evidence["github_run_ids"]
    if (
        not isinstance(run_ids, list)
        or len(run_ids) != 2
        or any(
            type(run_id) is not str
            or GITHUB_RUN_ID_PATTERN.fullmatch(run_id) is None
            for run_id in run_ids
        )
        or len(set(run_ids)) != 2
    ):
        _fail(
            f"{context}.github_run_ids must be a native list of exactly two "
            "distinct canonical positive ASCII decimal strings"
        )
    if evidence["probes"] != [6, 8, 10, 12]:
        _fail(f"{context}.probes must equal [6, 8, 10, 12]")
    _require_int(
        evidence["identity_and_discrete_changed"],
        f"{context}.identity_and_discrete_changed",
        exact=0,
    )
    for key in ("formal_no_trade_preserved", "material_mutation_probe_passed"):
        if not _require_bool(evidence[key], f"{context}.{key}"):
            _fail(f"{context}.{key} must be true")


def _validate_v2_manifest(
    root: Path,
    payload: dict[str, Any],
    *,
    require_complete: bool = False,
) -> None:
    active = _require_bool(payload.get("active"), "model freeze active")
    complete = active or require_complete
    cutoff = _require_text(
        payload.get("training_cutoff_signal_date"),
        "training_cutoff_signal_date",
    )
    if not _valid_date(cutoff):
        _fail("training_cutoff_signal_date must be YYYYMMDD")
    _require_text(payload.get("freeze_id"), "freeze_id")
    _validate_history_manifest(root, payload.get("history_snapshot"), active=complete)

    production = _require_mapping(payload.get("production"), "production")
    _require_text(production.get("model_version"), "production.model_version")
    if _require_bool(production.get("promoted"), "production.promoted"):
        _fail("production.promoted must remain false")
    _require_text(
        production.get("trade_selector_version"),
        "production.trade_selector_version",
    )
    if _require_bool(
        production.get("trade_selector_promoted"),
        "production.trade_selector_promoted",
    ):
        _fail("production.trade_selector_promoted must remain false")
    _require_text(
        production.get("formal_status"),
        "production.formal_status",
        exact="NO_TRADE_MODEL_NOT_PROMOTED",
    )
    _require_int(
        production.get("formal_buy_count"),
        "production.formal_buy_count",
        exact=0,
    )

    legacy = _require_mapping(
        production.get("legacy_v1_audit"), "production.legacy_v1_audit"
    )
    _require_exact_keys(
        legacy,
        frozenset(
            {
                "enforcement",
                "model_artifact_sha256",
                "trade_selector_artifact_sha256",
            }
        ),
        "production.legacy_v1_audit",
    )
    _require_text(
        legacy["enforcement"],
        "production.legacy_v1_audit.enforcement",
        exact="audit_only",
    )
    _require_sha256(
        legacy["model_artifact_sha256"],
        "production.legacy_v1_audit.model_artifact_sha256",
    )
    _require_sha256(
        legacy["trade_selector_artifact_sha256"],
        "production.legacy_v1_audit.trade_selector_artifact_sha256",
    )

    canonical = _require_mapping(
        production.get("canonical_v2"), "production.canonical_v2"
    )
    required_canonical = {
        "schema_version",
        "enforcement",
        "model",
        "trade_selector",
    }
    allowed_canonical = {*required_canonical, "precision_evidence"}
    if complete:
        required_canonical.add("precision_evidence")
    actual_canonical = set(canonical)
    if not required_canonical.issubset(actual_canonical) or not actual_canonical.issubset(
        allowed_canonical
    ):
        _fail(
            "production.canonical_v2 keys drift: "
            f"missing={sorted(required_canonical - actual_canonical)!r} "
            f"unexpected={sorted(actual_canonical - allowed_canonical)!r}"
        )
    _require_text(
        canonical["schema_version"],
        "production.canonical_v2.schema_version",
        exact=CANONICAL_RUNTIME_SCHEMA_VERSION,
    )
    _require_text(
        canonical["enforcement"],
        "production.canonical_v2.enforcement",
        exact="hard",
    )
    model_layer = _validate_canonical_layer(
        canonical["model"], layer="model", context="production.canonical_v2.model"
    )
    selector_layer = _validate_canonical_layer(
        canonical["trade_selector"],
        layer="trade_selector",
        context="production.canonical_v2.trade_selector",
    )
    model_decimals = model_layer["canonical_contract"]["decimals"]
    if model_decimals != selector_layer["canonical_contract"]["decimals"]:
        _fail("model and selector canonical decimals must match")
    if "precision_evidence" in canonical:
        _validate_precision_evidence(canonical["precision_evidence"])

    behavior = _require_mapping(payload.get("behavior_contract"), "behavior_contract")
    _require_exact_keys(
        behavior,
        frozenset(
            {
                "schema_version",
                "canonical_schema",
                "top10",
                "trade_selector_oos",
                "action_watchlist",
                "reference_evidence",
                "nested_oos_research",
                "decision",
            }
        ),
        "behavior_contract",
    )
    reference_evidence = _require_mapping(
        behavior.get("reference_evidence"), "behavior_contract.reference_evidence"
    )
    _require_exact_keys(
        reference_evidence,
        frozenset(KNOWN_REFERENCE_EVIDENCE),
        "behavior_contract.reference_evidence",
    )
    for key, value in reference_evidence.items():
        text = _require_text(value, f"behavior_contract.reference_evidence.{key}")
        if not GIT_SHA_PATTERN.fullmatch(text):
            _fail(f"behavior_contract.reference_evidence.{key} must be 40-hex")
    if reference_evidence != KNOWN_REFERENCE_EVIDENCE:
        _fail("V2 freeze must use the reviewed remote c6 reference blobs")
    nested_oos = _require_mapping(
        behavior.get("nested_oos_research"),
        "behavior_contract.nested_oos_research",
    )
    _require_exact_keys(
        nested_oos,
        frozenset(
            {
                "all_candidates_path",
                "signals",
                "signal_dates",
                "filled_trades",
                "market_buyable_path",
                "market_buyable_filled_trades",
            }
        ),
        "behavior_contract.nested_oos_research",
    )
    _require_text(
        nested_oos["all_candidates_path"],
        "behavior_contract.nested_oos_research.all_candidates_path",
        exact="trade_selector.formal_policy_oos.all_candidates",
    )
    _require_text(
        nested_oos["market_buyable_path"],
        "behavior_contract.nested_oos_research.market_buyable_path",
        exact="trade_selector.formal_policy_oos.market_buyable_only",
    )
    for key in (
        "signals",
        "signal_dates",
        "filled_trades",
        "market_buyable_filled_trades",
    ):
        _require_int(
            nested_oos[key],
            f"behavior_contract.nested_oos_research.{key}",
            minimum=0,
        )
    if complete and (
        nested_oos["signals"] != KNOWN_NESTED_OOS_SIGNALS
        or nested_oos["signal_dates"] != KNOWN_NESTED_OOS_SIGNAL_DATES
        or nested_oos["filled_trades"] != KNOWN_NESTED_OOS_FILLED_TRADES
        or nested_oos["market_buyable_filled_trades"]
        != KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES
    ):
        _fail("complete nested-OOS research metrics must pin 158/119/158 and 25")
    _require_text(
        behavior.get("schema_version"),
        "behavior_contract.schema_version",
        exact=BEHAVIOR_SCHEMA_VERSION,
    )
    _require_text(
        behavior.get("canonical_schema"),
        "behavior_contract.canonical_schema",
        exact=CANONICAL_FINGERPRINT_SCHEMA,
    )
    top10 = _validate_behavior_dataset_contract(
        root,
        behavior.get("top10"),
        name="top10",
        discrete_columns=TOP10_DISCRETE_BEHAVIOR_COLUMNS,
        score_columns=TOP10_SCORE_COLUMNS,
        expected_decimals=model_decimals,
    )
    oos = _validate_behavior_dataset_contract(
        root,
        behavior.get("trade_selector_oos"),
        name="trade_selector_oos",
        discrete_columns=OOS_DISCRETE_BEHAVIOR_COLUMNS,
        score_columns=OOS_SCORE_COLUMNS,
        expected_decimals=model_decimals,
    )
    action_watchlist = _validate_action_watchlist_contract(
        root, behavior.get("action_watchlist")
    )
    if complete and action_watchlist["shadow_only_rows"] != KNOWN_ACTION_SHADOW_ROWS:
        _fail("complete action watchlist must pin two relative-best shadows")
    if complete and (
        top10["rows"] != KNOWN_TOP10_ROWS
        or top10["signal_dates"] != KNOWN_TOP10_DATES
        or oos["rows"] != KNOWN_OOS_ROWS
        or oos["signal_dates"] != KNOWN_OOS_DATES
    ):
        _fail("complete behavior contract must pin 4467/543 and 3097/363")

    decision = _require_mapping(behavior.get("decision"), "behavior_contract.decision")
    _require_exact_keys(
        decision,
        frozenset(
            {
                "status_code",
                "formal_buy_count",
                "top10_selected_count",
                "selector_globally_promoted_count",
                "nested_oos_trade_selected_count",
                "nested_oos_trade_selector_promoted_count",
                "production_backtest_signals",
                "production_backtest_signal_dates",
                "production_backtest_fills",
                "reason_values",
            }
        ),
        "behavior_contract.decision",
    )
    _require_text(
        decision["status_code"],
        "behavior_contract.decision.status_code",
        exact="NO_TRADE_MODEL_NOT_PROMOTED",
    )
    for key in (
        "formal_buy_count",
        "top10_selected_count",
        "selector_globally_promoted_count",
        "production_backtest_signals",
        "production_backtest_signal_dates",
        "production_backtest_fills",
    ):
        _require_int(decision[key], f"behavior_contract.decision.{key}", exact=0)
    _require_int(
        decision["nested_oos_trade_selected_count"],
        "behavior_contract.decision.nested_oos_trade_selected_count",
        minimum=0,
    )
    _require_int(
        decision["nested_oos_trade_selector_promoted_count"],
        "behavior_contract.decision.nested_oos_trade_selector_promoted_count",
        minimum=0,
    )
    if complete and (
        decision["nested_oos_trade_selected_count"]
        != KNOWN_NESTED_OOS_TRADE_SELECTED
        or decision["nested_oos_trade_selector_promoted_count"] != KNOWN_OOS_ROWS
    ):
        _fail(
            "complete nested-OOS research counts must pin "
            "158 selected / 3097 promoted"
        )
    _require_string_list(
        decision["reason_values"],
        "behavior_contract.decision.reason_values",
        exact=("selection_policy_not_ready",),
    )

    pinned = _require_mapping(payload.get("pinned_files"), "pinned_files")
    if complete and not pinned:
        _fail("complete V2 freeze requires nonempty pinned_files")
    if complete:
        missing_pins = sorted(REQUIRED_ACTIVE_PIN_PATHS.difference(pinned))
        if missing_pins:
            _fail(
                "complete V2 freeze is missing execution-critical pins: "
                f"{missing_pins!r}"
            )
    for relative, expected_sha in pinned.items():
        _safe_repository_path(root, relative, f"pinned_files[{relative!r}]")
        _require_sha256(expected_sha, f"pinned_files[{relative!r}]")


def _validate_legacy_inactive_manifest(root: Path, payload: dict[str, Any]) -> None:
    active = _require_bool(payload.get("active"), "model freeze active")
    if active:
        _fail("active Decision freeze requires decision_model_freeze_v2")
    snapshot = payload.get("history_snapshot")
    if isinstance(snapshot, dict):
        _safe_repository_path(
            root,
            snapshot.get("path"),
            "history_snapshot.path",
            suffix=".csv.gz",
        )
        _require_bool(snapshot.get("bootstrap_mode"), "history_snapshot.bootstrap_mode")


def load_model_freeze(
    root: Path | str = Path("."), *, required: bool = False
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = root_path / DEFAULT_FREEZE_PATH
    if not path.exists() and not required:
        return {}
    payload = _read_json(path)
    schema = payload.get("schema_version")
    if schema == FREEZE_SCHEMA_VERSION:
        _validate_v2_manifest(root_path, payload)
    elif schema == LEGACY_FREEZE_SCHEMA_VERSION:
        _validate_legacy_inactive_manifest(root_path, payload)
    else:
        _fail(f"unsupported model freeze schema: {schema}")
    return payload


def model_freeze_active(manifest: dict[str, Any]) -> bool:
    return manifest.get("active") is True


def apply_frozen_history_cutoff(
    frame: pd.DataFrame, manifest: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not model_freeze_active(manifest) or frame.empty:
        return frame.copy(), {
            "active": model_freeze_active(manifest),
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "rows_before": int(len(frame)),
            "rows_after": int(len(frame)),
            "rows_removed": 0,
        }
    if "signal_date" not in frame.columns:
        _fail("training history has no signal_date column")
    cutoff = str(manifest.get("training_cutoff_signal_date") or "")
    signal_dates = frame["signal_date"].map(normalize_date)
    valid = signal_dates.map(_valid_date)
    filtered = frame.loc[valid & signal_dates.le(cutoff)].copy().reset_index(drop=True)
    if filtered.empty:
        _fail(f"model freeze removed all training rows at cutoff {cutoff}")
    kept = sorted(filtered["signal_date"].map(normalize_date).unique())
    return filtered, {
        "active": True,
        "freeze_id": str(manifest.get("freeze_id") or ""),
        "training_cutoff_signal_date": cutoff,
        "rows_before": int(len(frame)),
        "rows_after": int(len(filtered)),
        "rows_removed": int(len(frame) - len(filtered)),
        "history_start": kept[0] if kept else "",
        "history_end": kept[-1] if kept else "",
    }


def history_snapshot_bootstrap_mode(manifest: dict[str, Any]) -> bool:
    snapshot = manifest.get("history_snapshot") or {}
    return bool(model_freeze_active(manifest) and snapshot.get("bootstrap_mode"))


def _read_verified_history(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    source: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root_path = Path(root).resolve()
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("verified frozen replay requires decision_model_freeze_v2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=source == "forced_frozen_snapshot",
    )
    snapshot = manifest["history_snapshot"]
    path = _safe_repository_path(
        root_path,
        snapshot["path"],
        "history_snapshot.path",
        suffix=".csv.gz",
    )
    if not path.is_file():
        _fail(f"frozen history snapshot missing: {path}")
    actual_sha = _sha256(path)
    if actual_sha != snapshot["sha256"]:
        _fail(
            "frozen history snapshot drift detected: "
            f"expected={snapshot['sha256']} actual={actual_sha}"
        )
    try:
        frame = pd.read_csv(
            path,
            compression="gzip",
            dtype={
                "signal_date": "string",
                "buy_date": "string",
                "target_exit_date": "string",
                "actual_exit_date": "string",
                "ts_code": "string",
            },
        )
    except (OSError, ValueError) as exc:
        raise DecisionModelFreezeError(
            f"frozen history snapshot unreadable: {path}"
        ) from exc
    if len(frame) != snapshot["rows"]:
        _fail(
            "frozen history snapshot row-count drift: "
            f"expected={snapshot['rows']} actual={len(frame)}"
        )
    schema = snapshot["schema"]
    missing = [name for name in schema["required_columns"] if name not in frame]
    if missing:
        _fail(f"frozen history snapshot missing required columns: {missing!r}")
    actual_columns_sha = frame_columns_sha256(list(frame.columns))
    if actual_columns_sha != schema["columns_sha256"]:
        _fail(
            "frozen history snapshot column-order drift: "
            f"expected={schema['columns_sha256']} actual={actual_columns_sha}"
        )
    dates = frame["signal_date"].astype("string")
    if dates.isna().any() or (~dates.map(lambda value: _valid_date(str(value)))).any():
        _fail("frozen history snapshot contains invalid signal_date values")
    if "ts_code" in schema["required_columns"]:
        codes = frame["ts_code"].astype("string")
        if codes.isna().any() or (
            ~codes.map(lambda value: bool(CODE_PATTERN.fullmatch(str(value))))
        ).any():
            _fail("frozen history snapshot contains noncanonical ts_code values")
    cutoff = manifest["training_cutoff_signal_date"]
    if dates.gt(cutoff).any():
        _fail("frozen history snapshot contains rows beyond its cutoff")
    audit = {
        "active": model_freeze_active(manifest),
        "manifest_active": model_freeze_active(manifest),
        "freeze_id": manifest["freeze_id"],
        "source": source,
        "path": path.relative_to(root_path).as_posix(),
        "sha256": actual_sha,
        "rows": int(len(frame)),
        "columns_sha256": actual_columns_sha,
        "bootstrap_mode": False,
        "training_cutoff_signal_date": cutoff,
        "history_start": min(str(value) for value in dates) if len(dates) else "",
        "history_end": max(str(value) for value in dates) if len(dates) else "",
    }
    return frame, audit


def load_verified_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the manifest-pinned snapshot even while inactive; never use live data."""
    snapshot = manifest.get("history_snapshot") or {}
    if (
        snapshot.get("path") != KNOWN_HISTORY_PATH
        or snapshot.get("sha256") != KNOWN_HISTORY_SHA256
        or snapshot.get("rows") != KNOWN_HISTORY_ROWS
    ):
        _fail(
            "forced frozen replay requires the reviewed 40,355-row SHA77e snapshot"
        )
    return _read_verified_history(root, manifest, source="forced_frozen_snapshot")


def load_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any]
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    if not model_freeze_active(manifest):
        return None, {"active": False, "source": "live_history"}
    frame, audit = _read_verified_history(root, manifest, source="frozen_snapshot")
    return frame, audit


def capture_frozen_history_snapshot(
    root: Path | str, manifest: dict[str, Any], frame: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    del root, manifest, frame
    _fail("history snapshot capture is permanently disabled in freeze schema V2")


def validate_pinned_files(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    force_enforcement: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    pinned = manifest.get("pinned_files") or {}
    active = model_freeze_active(manifest)
    enforce = active or force_enforcement
    if not enforce:
        return {
            "active": False,
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "pinned_files": int(len(pinned)),
            "validated": True,
            "enforced": False,
            "forced_enforcement": False,
        }
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("pinned-file enforcement requires freeze schema V2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=force_enforcement,
    )
    missing_pins = sorted(REQUIRED_ACTIVE_PIN_PATHS.difference(pinned))
    if missing_pins:
        _fail(f"enforced V2 freeze is missing execution-critical pins: {missing_pins!r}")
    mismatches: list[dict[str, str]] = []
    for relative_path, expected in sorted(pinned.items()):
        path = _safe_repository_path(
            root_path, relative_path, f"pinned_files[{relative_path!r}]"
        )
        actual = _sha256(path) if path.is_file() else "MISSING"
        if actual != expected:
            mismatches.append(
                {"path": relative_path, "expected": expected, "actual": actual}
            )
    if mismatches:
        detail = "; ".join(
            f"{item['path']} expected={item['expected']} actual={item['actual']}"
            for item in mismatches
        )
        _fail(f"frozen file drift detected: {detail}")
    return {
        "active": active,
        "freeze_id": manifest["freeze_id"],
        "pinned_files": int(len(pinned)),
        "validated": True,
        "enforced": True,
        "forced_enforcement": force_enforcement and not active,
    }


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {
            "na",
            "nan",
            "null",
            "none",
            "<na>",
        }
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _behavior_boolean(value: Any, context: str) -> int:
    if _is_missing(value):
        _fail(f"{context} must not be missing")
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (str, bytes)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = math.nan
    else:
        number = math.nan
    if math.isfinite(number):
        if number in (0.0, 1.0):
            return int(number)
    _fail(f"{context} must be a strict binary value")


def _behavior_integer(value: Any, context: str) -> int:
    if _is_missing(value) or isinstance(value, bool):
        _fail(f"{context} must be a nonmissing integer")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{context} must be an integer")
    if not math.isfinite(number) or not number.is_integer():
        _fail(f"{context} must be an integer")
    return int(number)


def _exact_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or value == "":
        _fail(f"{context} must be an exact nonempty string")
    return value


def _normalized_identity_frame(frame: pd.DataFrame, context: str) -> pd.DataFrame:
    missing = [column for column in IDENTITY_COLUMNS if column not in frame]
    if missing:
        _fail(f"{context} missing identity columns: {missing!r}")
    rows: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for row_number, row in frame.loc[:, IDENTITY_COLUMNS].iterrows():
        if _is_missing(row["signal_date"]) or _is_missing(row["ts_code"]):
            _fail(f"{context} has empty identity at row {row_number}")
        if not isinstance(row["signal_date"], str) or not isinstance(
            row["ts_code"], str
        ):
            _fail(f"{context} identity must be exact strings at row {row_number}")
        signal_date = row["signal_date"]
        ts_code = row["ts_code"]
        if not _valid_date(signal_date) or not CODE_PATTERN.fullmatch(ts_code):
            _fail(f"{context} has invalid identity at row {row_number}")
        identity = (signal_date, ts_code)
        if identity in identities:
            _fail(f"{context} has duplicate identity {identity!r}")
        identities.add(identity)
        rows.append({"signal_date": signal_date, "ts_code": ts_code})
    if not rows:
        _fail(f"{context} must not be empty")
    return pd.DataFrame(rows)


def compute_behavior_fingerprints(
    frame: pd.DataFrame,
    contract: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    identity_columns = tuple(contract["identity_columns"])
    discrete_columns = tuple(contract["discrete_columns"])
    score_columns = tuple(contract["score_columns"])
    decimals = int(contract["score_decimals"])
    required = list(dict.fromkeys((*identity_columns, *discrete_columns, *score_columns)))
    missing = [column for column in required if column not in frame]
    if missing:
        _fail(f"{context} missing exact contract columns: {missing!r}")
    identities = _normalized_identity_frame(frame, context)

    identity_sha = canonical_frame_fingerprint(
        identities,
        identity_columns,
        decimals=decimals,
        kinds={"signal_date": "date", "ts_code": "code"},
    )["sha256"]
    date_counts = Counter(identities["signal_date"].tolist())
    date_counts_sha = canonical_mapping_sha256(
        [
            {"signal_date": date, "rows": int(rows)}
            for date, rows in sorted(date_counts.items())
        ],
        decimals=decimals,
    )

    discrete = identities.copy()
    discrete_kinds: dict[str, str] = {
        "signal_date": "date",
        "ts_code": "code",
    }
    for column in discrete_columns:
        values: list[Any] = []
        for row_number, value in enumerate(frame[column]):
            cell = f"{context}.{column}[{row_number}]"
            if column == "stage":
                values.append(_exact_text(value, cell))
                discrete_kinds[column] = "exact_text"
            elif column in BOOLEAN_BEHAVIOR_COLUMNS:
                values.append(_behavior_boolean(value, cell))
                discrete_kinds[column] = "integer"
            elif column in INTEGER_BEHAVIOR_COLUMNS:
                values.append(_behavior_integer(value, cell))
                discrete_kinds[column] = "integer"
            elif column in TEXT_BEHAVIOR_COLUMNS:
                values.append(_exact_text(value, cell))
                discrete_kinds[column] = "exact_text"
            else:
                _fail(f"{context} has unclassified discrete column {column!r}")
        discrete[column] = values
    discrete_sha = canonical_frame_fingerprint(
        discrete,
        (*identity_columns, *discrete_columns),
        decimals=decimals,
        kinds=discrete_kinds,
    )["sha256"]

    scores = identities.copy()
    score_kinds: dict[str, str] = {
        "signal_date": "date",
        "ts_code": "code",
        **{column: "float" for column in score_columns},
    }
    for column in score_columns:
        normalized: list[float | None] = []
        for row_number, value in enumerate(frame[column]):
            if _is_missing(value):
                normalized.append(None)
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                _fail(f"{context}.{column}[{row_number}] is not numeric")
            if not math.isfinite(number):
                _fail(f"{context}.{column}[{row_number}] must be finite or missing")
            normalized.append(number)
        scores[column] = normalized
    if {
        "risk_gate_pass",
        "diagnostic_gap",
        "recommended_max_gap",
    }.issubset(frame.columns):
        for row_number, row in frame.iterrows():
            risk_gate = _behavior_boolean(
                row["risk_gate_pass"], f"{context}.risk_gate_pass[{row_number}]"
            )
            recommended = row["recommended_max_gap"]
            diagnostic = row["diagnostic_gap"]
            if risk_gate == 1:
                if _is_missing(recommended) or _is_missing(diagnostic):
                    _fail(
                        f"{context}.recommended_max_gap must be present when "
                        f"risk_gate_pass=1 at row {row_number}"
                    )
                try:
                    recommended_number = float(recommended)
                    diagnostic_number = float(diagnostic)
                except (TypeError, ValueError, OverflowError):
                    _fail(f"{context} gap relation is nonnumeric at row {row_number}")
                if (
                    not math.isfinite(recommended_number)
                    or not math.isfinite(diagnostic_number)
                    or recommended_number != diagnostic_number
                ):
                    _fail(
                        f"{context}.recommended_max_gap must equal diagnostic_gap "
                        f"when risk_gate_pass=1 at row {row_number}"
                    )
            elif not _is_missing(recommended):
                _fail(
                    f"{context}.recommended_max_gap must be missing when "
                    f"risk_gate_pass=0 at row {row_number}"
                )
    scores_sha = canonical_frame_fingerprint(
        scores,
        (*identity_columns, *score_columns),
        decimals=decimals,
        kinds=score_kinds,
    )["sha256"]
    return {
        "rows": int(len(frame)),
        "signal_dates": int(len(date_counts)),
        "identity_sha256": identity_sha,
        "date_counts_sha256": date_counts_sha,
        "discrete_sha256": discrete_sha,
        "scores_sha256": scores_sha,
        "score_decimals": decimals,
        "identity_unique_nonempty": True,
    }


def _read_csv(path: Path, context: str) -> pd.DataFrame:
    if not path.is_file():
        _fail(f"{context} missing: {path}")
    try:
        return pd.read_csv(
            path,
            low_memory=False,
            dtype={"signal_date": "string", "ts_code": "string"},
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise DecisionModelFreezeError(f"{context} unreadable: {path}") from exc


def _validate_model_policy_columns(
    frame: pd.DataFrame,
    expected_model: Mapping[str, Any],
    *,
    context: str,
) -> None:
    projection = expected_model["fingerprint_v2"]["policy_projection"]
    decimals = expected_model["canonical_contract"]["decimals"]
    required = {
        "selection_policy_version",
        "gate_policy_ready",
        "policy_max_positions",
        *(
            f"policy_{name}"
            for name in MODEL_POLICY_THRESHOLD_KEYS
        ),
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        _fail(f"{context} missing frozen policy columns: {missing!r}")
    expected_ready = bool(projection["ready"])
    expected_version = str(projection["version"])
    expected_positions = projection["max_positions"]
    for row_number, row in frame.iterrows():
        if _exact_text(
            row["selection_policy_version"],
            f"{context}.selection_policy_version[{row_number}]",
        ) != expected_version:
            _fail(f"{context} selection policy version drift at row {row_number}")
        if bool(
            _behavior_boolean(
                row["gate_policy_ready"],
                f"{context}.gate_policy_ready[{row_number}]",
            )
        ) != expected_ready:
            _fail(f"{context} policy ready drift at row {row_number}")
        if _behavior_integer(
            row["policy_max_positions"],
            f"{context}.policy_max_positions[{row_number}]",
        ) != expected_positions:
            _fail(f"{context} max positions drift at row {row_number}")
        for threshold_name in MODEL_POLICY_THRESHOLD_KEYS:
            column = f"policy_{threshold_name}"
            actual = canonical_float_token(row[column], decimals=decimals)
            expected = canonical_float_token(
                projection["thresholds"][threshold_name], decimals=decimals
            )
            if actual != expected:
                _fail(
                    f"{context} threshold {threshold_name} drift at row {row_number}"
                )


def compute_action_watchlist_fingerprint(
    action: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    watchlist = action.get("stage_watchlist")
    if not isinstance(watchlist, list):
        _fail("action_plan.stage_watchlist must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    shadow_only_rows = 0
    for row_number, item in enumerate(watchlist):
        if not isinstance(item, dict):
            _fail(f"action_plan.stage_watchlist[{row_number}] must be an object")
        missing = [column for column in contract["columns"] if column not in item]
        if missing:
            _fail(
                f"action_plan.stage_watchlist[{row_number}] missing columns: {missing!r}"
            )
        code = item["ts_code"]
        if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code) or code in seen:
            _fail(f"action watchlist has invalid/duplicate code at row {row_number}")
        seen.add(code)
        target_weight = item["target_weight"]
        if _is_missing(target_weight):
            _fail(f"action watchlist target_weight missing at row {row_number}")
        if type(target_weight) not in (int, float):
            _fail(f"action watchlist target_weight invalid at row {row_number}")
        try:
            target_weight_number = float(target_weight)
        except (TypeError, ValueError, OverflowError):
            _fail(f"action watchlist target_weight invalid at row {row_number}")
        if not math.isfinite(target_weight_number):
            _fail(f"action watchlist target_weight nonfinite at row {row_number}")
        normalized.append(
            {
                "ts_code": code,
                "action": _exact_text(
                    item["action"], f"action watchlist action[{row_number}]"
                ),
                "stage_watch_rank": _require_int(
                    item["stage_watch_rank"],
                    f"action watchlist stage_watch_rank[{row_number}]",
                    minimum=1,
                ),
                "watch_label": _exact_text(
                    item["watch_label"],
                    f"action watchlist watch_label[{row_number}]",
                ),
                "target_weight": target_weight_number,
            }
        )
        if normalized[-1]["action"] not in {"REJECT", "SHADOW_ONLY"}:
            _fail("NO_TRADE action watchlist contains an unauthorized action")
        if "trade_shadow_selected" not in item:
            _fail(
                f"action_plan.stage_watchlist[{row_number}] missing "
                "trade_shadow_selected"
            )
        shadow_selected = _require_binary_int(
            item["trade_shadow_selected"],
            f"action watchlist trade_shadow_selected[{row_number}]",
        )
        expected_action = "SHADOW_ONLY" if shadow_selected == 1 else "REJECT"
        expected_label = "二筛影子" if shadow_selected == 1 else "仅观察"
        if normalized[-1]["action"] != expected_action:
            _fail("action watchlist SHADOW_ONLY must match relative-best-two flag")
        if normalized[-1]["watch_label"] != expected_label:
            _fail("action watchlist label must match relative-best-two flag")
        shadow_only_rows += shadow_selected
    frame = pd.DataFrame(normalized, columns=ACTION_WATCHLIST_COLUMNS)
    digest = canonical_frame_fingerprint(
        frame,
        ACTION_WATCHLIST_COLUMNS,
        decimals=8,
        kinds={
            "ts_code": "code",
            "action": "exact_text",
            "stage_watch_rank": "integer",
            "watch_label": "exact_text",
            "target_weight": "float",
        },
    )["sha256"]
    return {
        "rows": len(normalized),
        "sha256": digest,
        "unique_codes": True,
        "shadow_only_rows": shadow_only_rows,
    }


def _prediction_unique(
    frame: pd.DataFrame,
    column: str,
    *,
    kind: str,
    context: str,
) -> Any:
    if frame.empty:
        _fail(f"{context} must not be empty")
    if column not in frame:
        _fail(f"{context} missing column {column!r}")
    normalized: list[Any] = []
    for row_number, value in enumerate(frame[column]):
        cell = f"{context}.{column}[{row_number}]"
        if kind == "text":
            normalized.append(_exact_text(value, cell))
        elif kind == "int":
            normalized.append(_behavior_integer(value, cell))
        elif kind == "bool":
            normalized.append(bool(_behavior_boolean(value, cell)))
        else:
            _fail(f"unsupported prediction value kind: {kind}")
    unique = set(normalized)
    if len(unique) != 1:
        _fail(f"{context}.{column} has mixed row values")
    return normalized[0]


def _prediction_probability(value: Any, context: str) -> float:
    if _is_missing(value) or isinstance(value, bool):
        _fail(f"{context} must be a finite probability")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{context} must be a finite probability")
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        _fail(f"{context} must be finite and within [0,1]")
    return number


def _prediction_observation_flags(prediction: pd.DataFrame) -> list[int]:
    if "observation_selected" not in prediction:
        _fail("prediction missing column 'observation_selected'")
    return [
        _behavior_boolean(
            value,
            f"prediction.observation_selected[{row_number}]",
        )
        for row_number, value in enumerate(prediction["observation_selected"])
    ]


def _validate_prediction_fill_relationships(
    prediction: pd.DataFrame,
) -> dict[str, Any]:
    missing = [
        column
        for column in PREDICTION_FILL_RELATIONSHIP_COLUMNS
        if column not in prediction
    ]
    if missing:
        _fail(f"prediction missing fill relationship columns: {missing!r}")
    if prediction.empty:
        _fail("prediction must not be empty")
    observation_flags = _prediction_observation_flags(prediction)
    available_rows = 0
    missing_actual_rows = 0
    for position, (row_number, row) in enumerate(prediction.iterrows()):
        fill = _prediction_probability(
            row["predicted_fill_probability"],
            f"prediction.predicted_fill_probability[{row_number}]",
        )
        public_fill = _prediction_probability(
            row["predicted_public_market_buyable_probability"],
            f"prediction.predicted_public_market_buyable_probability[{row_number}]",
        )
        if public_fill != fill:
            _fail(
                "prediction.predicted_public_market_buyable_probability must "
                f"equal predicted_fill_probability at row {row_number}"
            )
        trade_fill_value = row["trade_predicted_fill_probability"]
        trade_public_fill_value = row[
            "trade_predicted_public_market_buyable_probability"
        ]
        if observation_flags[position] == 1:
            trade_fill = _prediction_probability(
                trade_fill_value,
                f"prediction.trade_predicted_fill_probability[{row_number}]",
            )
            trade_public_fill = _prediction_probability(
                trade_public_fill_value,
                "prediction.trade_predicted_public_market_buyable_probability"
                f"[{row_number}]",
            )
            if trade_public_fill != trade_fill:
                _fail(
                    "prediction.trade_predicted_public_market_buyable_probability "
                    f"must equal trade_predicted_fill_probability at row {row_number}"
                )
        elif not _is_missing(trade_fill_value) or not _is_missing(
            trade_public_fill_value
        ):
            _fail(
                "prediction trade fill probabilities must be missing outside "
                f"the observation domain at row {row_number}"
            )
        availability = _behavior_integer(
            row["actual_order_fill_probability_available"],
            f"prediction.actual_order_fill_probability_available[{row_number}]",
        )
        if availability not in (0, 1):
            _fail(
                "prediction.actual_order_fill_probability_available must be binary"
            )
        actual_value = row["predicted_actual_order_fill_probability"]
        actual_missing = _is_missing(actual_value)
        if (availability == 0) != actual_missing:
            _fail(
                "prediction actual fill availability must be 0 iff "
                f"predicted_actual_order_fill_probability is missing at row {row_number}"
            )
        if availability == 1:
            _prediction_probability(
                actual_value,
                f"prediction.predicted_actual_order_fill_probability[{row_number}]",
            )
            available_rows += 1
        else:
            missing_actual_rows += 1
    return {
        "rows": int(len(prediction)),
        "public_fill_equals_fill": True,
        "trade_public_fill_equals_trade_fill": True,
        "trade_fill_observation_domain_rows": int(sum(observation_flags)),
        "trade_fill_outside_domain_rows": int(
            len(observation_flags) - sum(observation_flags)
        ),
        "actual_fill_available_rows": available_rows,
        "actual_fill_missing_rows": missing_actual_rows,
    }


def _runtime_layer_values(
    container: Mapping[str, Any], *, layer: str, context: str
) -> dict[str, Any]:
    if layer == "model":
        values = {
            "canonical_v2_version": container.get("model_canonical_v2_version"),
            "artifact_v2_sha256": container.get("model_artifact_v2_sha256"),
            "fingerprint_v2": container.get("model_fingerprint_v2"),
            "canonical_contract": container.get("model_canonical_contract"),
        }
    else:
        values = {
            "canonical_v2_version": container.get("canonical_v2_version"),
            "artifact_v2_sha256": container.get("production_artifact_v2_sha256"),
            "fingerprint_v2": container.get("production_fingerprint_v2"),
            "canonical_contract": container.get("canonical_contract"),
        }
    _validate_canonical_layer(values, layer=layer, context=context)
    return values


def _action_layer_values(
    action_model: Mapping[str, Any], *, layer: str, expected: Mapping[str, Any]
) -> dict[str, Any]:
    if layer == "model":
        values = {
            "canonical_v2_version": action_model.get("canonical_v2_version"),
            "artifact_v2_sha256": action_model.get("artifact_v2_sha256"),
            "fingerprint_v2": action_model.get("fingerprint_v2"),
            "canonical_contract": action_model.get("canonical_contract"),
        }
        match_fields = (
            "artifact_v2_fingerprints_match",
            "fingerprint_v2_valid",
            "canonical_v2_versions_match",
            "canonical_contracts_match",
            "canonical_decimals_match",
        )
        ready_field = "canonical_policy_ready"
        decimals_field = "canonical_decimals"
        mode_field = "execution_numeric_mode"
        raw_field = "raw_execution_preserved"
    else:
        values = {
            "canonical_v2_version": action_model.get(
                "trade_selector_canonical_v2_version"
            ),
            "artifact_v2_sha256": action_model.get(
                "trade_selector_artifact_v2_sha256"
            ),
            "fingerprint_v2": action_model.get("trade_selector_fingerprint_v2"),
            "canonical_contract": action_model.get(
                "trade_selector_canonical_contract"
            ),
        }
        match_fields = (
            "trade_selector_artifacts_v2_match",
            "trade_selector_fingerprint_v2_valid",
            "trade_selector_canonical_v2_versions_match",
            "trade_selector_canonical_contracts_match",
            "trade_selector_canonical_decimals_match",
        )
        ready_field = "trade_selector_canonical_policy_ready"
        decimals_field = "trade_selector_canonical_decimals"
        mode_field = "trade_selector_execution_numeric_mode"
        raw_field = "trade_selector_raw_execution_preserved"
    _validate_canonical_layer(
        values, layer=layer, context=f"action_plan.model.{layer}_canonical_v2"
    )
    for field in match_fields:
        if not _require_bool(action_model.get(field), f"action_plan.model.{field}"):
            _fail(f"action_plan.model.{field} must be true")
    expected_ready = bool(expected["fingerprint_v2"]["policy_projection"]["ready"])
    if _require_bool(
        action_model.get(ready_field), f"action_plan.model.{ready_field}"
    ) != expected_ready:
        _fail(f"action_plan.model.{ready_field} differs from frozen policy")
    if _require_int(
        action_model.get(decimals_field), f"action_plan.model.{decimals_field}"
    ) != expected["canonical_contract"]["decimals"]:
        _fail(f"action_plan.model.{decimals_field} differs from frozen contract")
    if _require_text(
        action_model.get(mode_field), f"action_plan.model.{mode_field}"
    ) != expected["canonical_contract"]["execution_mode"]:
        _fail(f"action_plan.model.{mode_field} differs from frozen contract")
    if _require_bool(
        action_model.get(raw_field), f"action_plan.model.{raw_field}"
    ) != expected["canonical_contract"]["raw_execution_preserved"]:
        _fail(f"action_plan.model.{raw_field} differs from frozen contract")
    return values


def _prediction_layer_values(
    prediction: pd.DataFrame, *, layer: str, expected: Mapping[str, Any]
) -> dict[str, Any]:
    prefix = "model_" if layer == "model" else "trade_selector_"
    values = {
        "canonical_v2_version": _prediction_unique(
            prediction, f"{prefix}canonical_v2_version", kind="text", context="prediction"
        ),
        "artifact_v2_sha256": _prediction_unique(
            prediction, f"{prefix}artifact_v2_sha256", kind="text", context="prediction"
        ),
        "canonical_schema": _prediction_unique(
            prediction, f"{prefix}canonical_schema", kind="text", context="prediction"
        ),
        "canonical_decimals": _prediction_unique(
            prediction, f"{prefix}canonical_decimals", kind="int", context="prediction"
        ),
        "execution_numeric_mode": _prediction_unique(
            prediction, f"{prefix}execution_numeric_mode", kind="text", context="prediction"
        ),
        "raw_execution_preserved": _prediction_unique(
            prediction, f"{prefix}raw_execution_preserved", kind="bool", context="prediction"
        ),
    }
    contract = expected["canonical_contract"]
    expected_projection = {
        "canonical_v2_version": expected["canonical_v2_version"],
        "artifact_v2_sha256": expected["artifact_v2_sha256"],
        "canonical_schema": contract["schema"],
        "canonical_decimals": contract["decimals"],
        "execution_numeric_mode": contract["execution_mode"],
        "raw_execution_preserved": contract["raw_execution_preserved"],
    }
    if values != expected_projection:
        _fail(f"prediction {layer} canonical V2 fields differ from manifest")
    return values


def _prediction_selector_domain_values(
    prediction: pd.DataFrame,
    *,
    expected: Mapping[str, Any],
    expected_runtime_v1_artifact_sha256: str,
    expected_selector_version: str,
    expected_shadow_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {
        *SELECTOR_PREDICTION_CANONICAL_COLUMNS,
        *SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS,
        *SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS,
        "trade_predicted_public_market_buyable_probability",
        "trade_selector_artifact_sha256",
        "trade_selector_promoted",
        "trade_selector_version",
        "trade_model_reason",
    }
    missing = sorted(required.difference(prediction.columns))
    if missing:
        _fail(f"prediction missing selector domain columns: {missing!r}")
    flags = _prediction_observation_flags(prediction)
    observation_positions = [index for index, value in enumerate(flags) if value == 1]
    outside_positions = [index for index, value in enumerate(flags) if value == 0]
    if not observation_positions:
        _fail("prediction selector observation domain must not be empty")
    expected_contract = expected["canonical_contract"]
    global_expected = {
        "trade_selector_canonical_v2_version": expected["canonical_v2_version"],
        "trade_selector_canonical_schema": expected_contract["schema"],
        "trade_selector_canonical_decimals": expected_contract["decimals"],
        "trade_selector_execution_numeric_mode": expected_contract[
            "execution_mode"
        ],
        "trade_selector_raw_execution_preserved": expected_contract[
            "raw_execution_preserved"
        ],
    }
    global_kinds = {
        "trade_selector_canonical_v2_version": "text",
        "trade_selector_canonical_schema": "text",
        "trade_selector_canonical_decimals": "int",
        "trade_selector_execution_numeric_mode": "text",
        "trade_selector_raw_execution_preserved": "bool",
    }
    for column in SELECTOR_PREDICTION_GLOBAL_COLUMNS:
        actual = _prediction_unique(
            prediction,
            column,
            kind=global_kinds[column],
            context="prediction",
        )
        if actual != global_expected[column]:
            _fail(f"prediction {column} differs from the frozen selector contract")
    if _prediction_unique(
        prediction,
        "trade_selector_version",
        kind="text",
        context="prediction",
    ) != expected_selector_version:
        _fail("prediction trade_selector_version drift detected")
    formal_selected = 0
    shadow_selected_count = 0
    promoted_count = 0
    for position, row in prediction.iterrows():
        formal_selected += _behavior_boolean(
            row["trade_selected"], f"prediction.trade_selected[{position}]"
        )
        shadow_selected_count += _behavior_boolean(
            row["trade_shadow_selected"],
            f"prediction.trade_shadow_selected[{position}]",
        )
        promoted_count += _behavior_boolean(
            row["trade_selector_promoted"],
            f"prediction.trade_selector_promoted[{position}]",
        )
    if formal_selected != 0:
        _fail("prediction must preserve zero formal trade_selected rows")
    if promoted_count != 0:
        _fail("prediction must preserve zero trade_selector_promoted rows")
    if shadow_selected_count != expected_shadow_count:
        _fail("prediction relative-best-two shadow count drift detected")
    for position in outside_positions:
        row = prediction.iloc[position]
        for column in (
            "trade_selector_artifact_sha256",
            "trade_selector_artifact_v2_sha256",
        ):
            if not _is_missing(row[column]):
                _fail(
                    f"prediction.{column}[{position}] must be missing outside "
                    "the observation domain"
                )
        for column in SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS:
            if not _is_missing(row[column]):
                _fail(
                    f"prediction.{column}[{position}] must be missing outside "
                    "the observation domain"
                )
        for column in SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS:
            if _behavior_boolean(
                row[column], f"prediction.{column}[{position}]"
            ) != 0:
                _fail(
                    f"prediction.{column}[{position}] must be zero outside "
                    "the observation domain"
                )
        if _behavior_boolean(
            row["trade_selector_promoted"],
            f"prediction.trade_selector_promoted[{position}]",
        ) != 0:
            _fail("prediction trade_selector_promoted must remain zero")
        if _exact_text(
            row["trade_model_reason"],
            f"prediction.trade_model_reason[{position}]",
        ) != "outside_observation_top10":
            _fail(
                "prediction trade_model_reason must be exactly "
                "outside_observation_top10 outside the observation domain"
            )
    domain = prediction.iloc[observation_positions]
    values = _prediction_layer_values(
        domain,
        layer="trade_selector",
        expected=expected,
    )
    domain_v1_artifact = _prediction_unique(
        domain,
        "trade_selector_artifact_sha256",
        kind="text",
        context="prediction selector observation domain",
    )
    _require_sha256(
        domain_v1_artifact,
        "prediction selector observation domain V1 artifact",
    )
    if domain_v1_artifact != expected_runtime_v1_artifact_sha256:
        _fail("selector V1 artifact differs across same-run runtime surfaces")
    return values, {
        "observation_domain_rows": len(observation_positions),
        "outside_domain_rows": len(outside_positions),
        "global_selector_v2_declarations_match": True,
        "domain_v2_artifact_manifest_match": True,
        "domain_v1_artifact_same_run_match": True,
        "domain_v1_artifact_sha256": domain_v1_artifact,
        "outside_selector_artifacts_empty": True,
        "outside_trade_semantics_valid": True,
        "formal_trade_selected_count": formal_selected,
        "trade_selector_promoted_count": promoted_count,
        "shadow_selected_count": shadow_selected_count,
    }


def validate_behavior_artifacts(
    root: Path | str, manifest: dict[str, Any]
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    behavior = manifest["behavior_contract"]
    audits: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    for name in ("top10", "trade_selector_oos"):
        contract = behavior[name]
        path = _safe_repository_path(
            root_path,
            contract["path"],
            f"behavior_contract.{name}.path",
            suffix=".csv",
        )
        frame = _read_csv(path, f"behavior artifact {name}")
        actual = compute_behavior_fingerprints(
            frame, contract, context=f"behavior.{name}"
        )
        expected = {
            key: contract[key]
            for key in (
                "rows",
                "signal_dates",
                "score_decimals",
                "identity_sha256",
                "date_counts_sha256",
                "discrete_sha256",
                "scores_sha256",
            )
        }
        mismatches = [key for key, value in expected.items() if actual[key] != value]
        if mismatches:
            _fail(f"frozen {name} behavior drift detected: {', '.join(mismatches)}")
        audits[name] = {**actual, "path": contract["path"], "validated": True}
        frames[name] = frame

    decision = behavior["decision"]
    top10_selected = sum(
        _behavior_boolean(value, f"top10.selected[{row}]")
        for row, value in enumerate(frames["top10"]["selected"])
    )
    oos = frames["trade_selector_oos"]
    selector_globally_promoted = sum(
        _behavior_boolean(value, f"oos.trade_selector_globally_promoted[{row}]")
        for row, value in enumerate(oos["trade_selector_globally_promoted"])
    )
    nested_trade_selected = sum(
        _behavior_boolean(value, f"oos.trade_selected[{row}]")
        for row, value in enumerate(oos["trade_selected"])
    )
    nested_selector_promoted = sum(
        _behavior_boolean(value, f"oos.trade_selector_promoted[{row}]")
        for row, value in enumerate(oos["trade_selector_promoted"])
    )
    reason_values = sorted(
        {
            _exact_text(value, f"top10.model_reason[{row}]")
            for row, value in enumerate(frames["top10"]["model_reason"])
        }
    )
    count_actual = {
        "top10_selected_count": top10_selected,
        "selector_globally_promoted_count": selector_globally_promoted,
        "nested_oos_trade_selected_count": nested_trade_selected,
        "nested_oos_trade_selector_promoted_count": nested_selector_promoted,
    }
    mismatches = [
        key for key, actual in count_actual.items() if actual != decision[key]
    ]
    if mismatches:
        _fail("frozen decision/research count drift: " + ", ".join(mismatches))
    if reason_values != decision["reason_values"]:
        _fail("NO_TRADE reason values drift detected")
    audits["decision_frame_counts"] = {
        **count_actual,
        "reason_values": reason_values,
        "formal_no_trade_note": (
            "nested OOS trade_selected/promoted counts are research evidence; "
            "only top-level selected/global promotion and production backtest fields "
            "define formal NO_TRADE"
        ),
    }
    return audits


def validate_runtime_artifacts(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    check_action_plan: bool = True,
    force_enforcement: bool = False,
) -> dict[str, Any]:
    enforce = model_freeze_active(manifest) or force_enforcement
    if not enforce:
        return {
            "active": False,
            "validated": True,
            "canonical_v2_enforced": False,
            "legacy_v1_enforced": False,
        }
    root_path = Path(root).resolve()
    if manifest.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail("canonical V2 runtime enforcement requires freeze schema V2")
    _validate_v2_manifest(
        root_path,
        manifest,
        require_complete=force_enforcement,
    )
    pinned_files_audit = validate_pinned_files(
        root_path,
        manifest,
        force_enforcement=force_enforcement,
    )
    if model_freeze_active(manifest):
        _, snapshot_audit = load_frozen_history_snapshot(root_path, manifest)
    else:
        _, snapshot_audit = load_verified_frozen_history_snapshot(
            root_path, manifest
        )
    model_meta = _read_json(
        root_path / "outputs/auction_v3/models/model_meta_latest.json"
    )
    backtest = _read_json(root_path / "outputs/auction_v3/metrics/backtest_latest.json")
    prediction = _read_csv(
        root_path / "outputs/auction_v3/predictions/pred_latest.csv",
        "prediction artifact",
    )
    production = manifest["production"]
    expected_canonical = production["canonical_v2"]
    expected_model = expected_canonical["model"]
    expected_selector = expected_canonical["trade_selector"]

    meta_model = _runtime_layer_values(
        model_meta, layer="model", context="model_meta.model_canonical_v2"
    )
    backtest_model = _runtime_layer_values(
        backtest, layer="model", context="backtest.model_canonical_v2"
    )
    meta_selector_raw = _require_mapping(
        model_meta.get("trade_selector"), "model_meta.trade_selector"
    )
    backtest_selector_raw = _require_mapping(
        backtest.get("trade_selector"), "backtest.trade_selector"
    )
    meta_selector = _runtime_layer_values(
        meta_selector_raw,
        layer="trade_selector",
        context="model_meta.trade_selector.canonical_v2",
    )
    backtest_selector = _runtime_layer_values(
        backtest_selector_raw,
        layer="trade_selector",
        context="backtest.trade_selector.canonical_v2",
    )
    if meta_model != expected_model or backtest_model != expected_model:
        _fail("model canonical V2 differs across manifest/meta/backtest")
    if meta_selector != expected_selector or backtest_selector != expected_selector:
        _fail("selector canonical V2 differs across manifest/meta/backtest")
    prediction_model = _prediction_layer_values(
        prediction, layer="model", expected=expected_model
    )
    # Top10/OOS artifacts are historical fold behavior. Their policy columns
    # are frozen by the exact discrete/q8 behavior hashes and must not be
    # compared to the final production policy. The current prediction is the
    # final-policy execution surface, so equality is enforced here instead.
    _validate_model_policy_columns(
        prediction, expected_model, context="prediction.final_model_policy"
    )
    selector_v1_meta = _require_sha256(
        meta_selector_raw.get("production_artifact_sha256"),
        "model_meta.trade_selector.production_artifact_sha256",
    )
    selector_v1_backtest = _require_sha256(
        backtest_selector_raw.get("production_artifact_sha256"),
        "backtest.trade_selector.production_artifact_sha256",
    )
    if selector_v1_meta != selector_v1_backtest:
        _fail("selector V1 artifact differs across same-run meta/backtest surfaces")
    prediction_selector, prediction_selector_domain = (
        _prediction_selector_domain_values(
            prediction,
            expected=expected_selector,
            expected_runtime_v1_artifact_sha256=selector_v1_meta,
            expected_selector_version=production["trade_selector_version"],
            expected_shadow_count=manifest["behavior_contract"][
                "action_watchlist"
            ]["shadow_only_rows"],
        )
    )
    prediction_fill_relationships = _validate_prediction_fill_relationships(
        prediction
    )

    expected_model_version = production["model_version"]
    expected_selector_version = production["trade_selector_version"]
    version_checks = {
        "meta_model_version": model_meta.get("model_version") == expected_model_version,
        "backtest_model_version": backtest.get("model_version") == expected_model_version,
        "meta_selector_version": meta_selector_raw.get("version")
        == expected_selector_version,
        "backtest_selector_version": backtest_selector_raw.get("version")
        == expected_selector_version,
        "meta_not_promoted": model_meta.get("promoted") is False,
        "backtest_not_promoted": backtest.get("promoted") is False,
        "meta_selector_not_promoted": meta_selector_raw.get("promoted") is False,
        "backtest_selector_not_promoted": backtest_selector_raw.get("promoted")
        is False,
    }
    failed_versions = [name for name, passed in version_checks.items() if not passed]
    if failed_versions:
        _fail("frozen runtime version/promotion drift: " + ", ".join(failed_versions))

    legacy_expected = production["legacy_v1_audit"]
    legacy_actual = {
        "model_meta": model_meta.get("model_artifact_sha256"),
        "model_backtest": backtest.get("model_artifact_sha256"),
        "selector_meta": meta_selector_raw.get("production_artifact_sha256"),
        "selector_backtest": backtest_selector_raw.get(
            "production_artifact_sha256"
        ),
        "selector_prediction": prediction_selector_domain[
            "domain_v1_artifact_sha256"
        ],
    }
    for name, value in legacy_actual.items():
        _require_sha256(value, f"legacy_v1_audit.actual.{name}")
    legacy_matches = {
        "model_meta": legacy_actual["model_meta"]
        == legacy_expected["model_artifact_sha256"],
        "model_backtest": legacy_actual["model_backtest"]
        == legacy_expected["model_artifact_sha256"],
        "selector_meta": legacy_actual["selector_meta"]
        == legacy_expected["trade_selector_artifact_sha256"],
        "selector_backtest": legacy_actual["selector_backtest"]
        == legacy_expected["trade_selector_artifact_sha256"],
        "selector_prediction": legacy_actual["selector_prediction"]
        == legacy_expected["trade_selector_artifact_sha256"],
    }

    action_checks: dict[str, Any] = {}
    if check_action_plan:
        action_path = root_path / "outputs/decision/action_plan_latest.json"
        if not action_path.is_file():
            _fail("frozen action plan is required but missing")
        action = _read_json(action_path)
        action_model = _require_mapping(action.get("model"), "action_plan.model")
        action_model_v2 = _action_layer_values(
            action_model, layer="model", expected=expected_model
        )
        action_selector_v2 = _action_layer_values(
            action_model, layer="trade_selector", expected=expected_selector
        )
        if action_model_v2 != expected_model or action_selector_v2 != expected_selector:
            _fail("action plan canonical V2 differs from manifest")
        if action.get("status_code") != production["formal_status"]:
            _fail("action plan formal status drift detected")
        formal_buy_count = _require_int(
            action.get("formal_buy_count"), "action_plan.formal_buy_count"
        )
        if formal_buy_count != production["formal_buy_count"]:
            _fail("action plan formal buy count drift detected")
        candidates = action.get("candidates")
        if not isinstance(candidates, list):
            _fail("action_plan.candidates must be a list")
        buy_count = 0
        shadow_count = 0
        candidate_projection: dict[str, tuple[str, int, float]] = {}
        for row_number, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                _fail(f"action_plan.candidates[{row_number}] must be an object")
            candidate_action = _exact_text(
                candidate.get("action"),
                f"action_plan.candidates[{row_number}].action",
            )
            candidate_code = candidate.get("ts_code")
            if (
                not isinstance(candidate_code, str)
                or not CODE_PATTERN.fullmatch(candidate_code)
                or candidate_code in candidate_projection
            ):
                _fail(
                    f"action_plan.candidates[{row_number}] has invalid/duplicate ts_code"
                )
            if candidate_action == "BUY":
                buy_count += 1
            elif candidate_action not in {"REJECT", "SHADOW_ONLY"}:
                _fail("NO_TRADE action plan contains an unauthorized action")
            if "trade_shadow_selected" not in candidate:
                _fail(
                    f"action_plan.candidates[{row_number}] missing "
                    "trade_shadow_selected"
                )
            shadow_selected = _require_binary_int(
                candidate["trade_shadow_selected"],
                f"action_plan.candidates[{row_number}].trade_shadow_selected",
            )
            expected_action = "SHADOW_ONLY" if shadow_selected == 1 else "REJECT"
            if candidate_action != expected_action:
                _fail(
                    "NO_TRADE candidate SHADOW_ONLY must match the "
                    "relative-best-two flag"
                )
            shadow_count += shadow_selected
            if "target_weight" not in candidate:
                _fail(
                    f"action_plan.candidates[{row_number}] missing target_weight"
                )
            if type(candidate["target_weight"]) not in (int, float):
                _fail(
                    f"action_plan.candidates[{row_number}].target_weight invalid"
                )
            try:
                target_weight = float(candidate["target_weight"])
            except (TypeError, ValueError, OverflowError):
                _fail(
                    f"action_plan.candidates[{row_number}].target_weight invalid"
                )
            if not math.isfinite(target_weight) or target_weight != 0.0:
                _fail("NO_TRADE action candidates require zero target_weight")
            candidate_projection[candidate_code] = (
                candidate_action,
                shadow_selected,
                target_weight,
            )
        if buy_count != 0:
            _fail("NO_TRADE action plan contains BUY candidates")
        if _require_int(action.get("shadow_count"), "action_plan.shadow_count") != (
            shadow_count
        ):
            _fail("action plan shadow count drift detected")
        if model_freeze_active(manifest) and shadow_count != 2:
            _fail("active frozen action plan must preserve relative-best-two")
        if action_model.get("version") != production["model_version"]:
            _fail("action plan model version drift detected")
        if action_model.get("promoted") is not False:
            _fail("action plan model must remain not promoted")
        nested_selector = _require_mapping(
            action_model.get("trade_selector"), "action_plan.model.trade_selector"
        )
        if nested_selector.get("version") != production["trade_selector_version"]:
            _fail("action plan selector version drift detected")
        if nested_selector.get("promoted") is not False:
            _fail("action plan selector must remain not promoted")
        watch_contract = manifest["behavior_contract"]["action_watchlist"]
        if action_path.relative_to(root_path).as_posix() != watch_contract["path"]:
            _fail("action watchlist contract path does not name action_plan_latest.json")
        watch_actual = compute_action_watchlist_fingerprint(action, watch_contract)
        if (
            watch_actual["rows"] != watch_contract["rows"]
            or watch_actual["sha256"] != watch_contract["sha256"]
            or watch_actual["shadow_only_rows"]
            != watch_contract["shadow_only_rows"]
        ):
            _fail("frozen action watchlist behavior drift detected")
        if watch_actual["shadow_only_rows"] != shadow_count:
            _fail("action watchlist/candidate relative-best-two drift detected")
        for row_number, item in enumerate(action.get("stage_watchlist", [])):
            code = item["ts_code"]
            candidate_values = candidate_projection.get(code)
            if candidate_values is None:
                _fail(
                    f"action watchlist row {row_number} has no matching candidate"
                )
            watch_values = (
                item["action"],
                _require_binary_int(
                    item["trade_shadow_selected"],
                    f"action watchlist trade_shadow_selected[{row_number}]",
                ),
                float(item["target_weight"]),
            )
            if watch_values != candidate_values:
                _fail(
                    f"action watchlist row {row_number} differs from its candidate"
                )
        if any(
            float(item.get("target_weight", 0.0)) != 0.0
            for item in action.get("stage_watchlist", [])
            if isinstance(item, dict)
        ):
            _fail("NO_TRADE action watchlist contains nonzero target weight")
        action_checks = {
            "present": True,
            "status_code": action["status_code"],
            "formal_buy_count": formal_buy_count,
            "buy_candidate_count": buy_count,
            "shadow_candidate_count": shadow_count,
            "model_v2_match": True,
            "selector_v2_match": True,
            "watchlist": watch_actual,
        }

    behavior_audit = validate_behavior_artifacts(root_path, manifest)
    decision = manifest["behavior_contract"]["decision"]
    nested_contract = manifest["behavior_contract"]["nested_oos_research"]
    formal_policy_oos = _require_mapping(
        backtest_selector_raw.get("formal_policy_oos"),
        "backtest.trade_selector.formal_policy_oos",
    )
    all_candidates = _require_mapping(
        formal_policy_oos.get("all_candidates"),
        "backtest.trade_selector.formal_policy_oos.all_candidates",
    )
    market_buyable_only = _require_mapping(
        formal_policy_oos.get("market_buyable_only"),
        "backtest.trade_selector.formal_policy_oos.market_buyable_only",
    )
    nested_oos_actual = {
        "signals": _require_int(
            all_candidates.get("signals"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.signals",
        ),
        "signal_dates": _require_int(
            all_candidates.get("signal_dates"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.signal_dates",
        ),
        "filled_trades": _require_int(
            all_candidates.get("filled_trades"),
            "backtest.trade_selector.formal_policy_oos.all_candidates.filled_trades",
        ),
        "market_buyable_filled_trades": _require_int(
            market_buyable_only.get("filled_trades"),
            "backtest.trade_selector.formal_policy_oos.market_buyable_only.filled_trades",
        ),
    }
    for key, actual in nested_oos_actual.items():
        if actual != nested_contract[key]:
            _fail(f"nested OOS research metric drift: {key}")
    # These are deliberately the top-level production backtest fields. Nested
    # selector.formal_policy_oos.all_candidates is research evidence (158/119/158),
    # not a formal trade authorization.
    production_backtest_values = {
        "production_backtest_signals": _require_int(
            backtest.get("signals"), "backtest.<root>.signals"
        ),
        "production_backtest_signal_dates": _require_int(
            backtest.get("signal_dates"), "backtest.<root>.signal_dates"
        ),
        "production_backtest_fills": _require_int(
            backtest.get("filled_trades"), "backtest.<root>.filled_trades"
        ),
    }
    for key, actual in production_backtest_values.items():
        if actual != decision[key]:
            _fail(f"{key} drift detected")

    history_end = str(((model_meta.get("data_coverage") or {}).get("history_end")) or "")
    if history_end and (
        not _valid_date(history_end)
        or history_end > manifest["training_cutoff_signal_date"]
    ):
        _fail("frozen runtime history_end exceeds or violates cutoff")
    return {
        "active": model_freeze_active(manifest),
        "forced_enforcement": force_enforcement and not model_freeze_active(manifest),
        "freeze_id": manifest["freeze_id"],
        "validated": True,
        "canonical_v2_enforced": True,
        "legacy_v1_enforced": False,
        "raw_execution_preserved": True,
        "pinned_files": pinned_files_audit,
        "snapshot": snapshot_audit,
        "model": {
            "manifest": expected_model,
            "meta_match": True,
            "backtest_match": True,
            "prediction": prediction_model,
        },
        "trade_selector": {
            "manifest": expected_selector,
            "meta_match": True,
            "backtest_match": True,
            "prediction": prediction_selector,
            "prediction_domain": prediction_selector_domain,
        },
        "prediction_fill_relationships": prediction_fill_relationships,
        "legacy_v1_audit": {
            "enforcement": "audit_only",
            "expected": legacy_expected,
            "actual": legacy_actual,
            "matches": legacy_matches,
            "all_match": all(legacy_matches.values()),
        },
        "version_checks": version_checks,
        "action_plan": action_checks,
        "behavior": behavior_audit,
        "nested_oos_research": {
            **nested_oos_actual,
            "formal_authorization": False,
        },
        "production_backtest_zero_values": production_backtest_values,
        "history_end": history_end,
        "training_cutoff_signal_date": manifest["training_cutoff_signal_date"],
    }


__all__ = [
    "ACTION_WATCHLIST_COLUMNS",
    "BEHAVIOR_SCHEMA_VERSION",
    "CANONICAL_RUNTIME_SCHEMA_VERSION",
    "DecisionModelFreezeError",
    "FREEZE_SCHEMA_VERSION",
    "GATE_DISCRETE_BEHAVIOR_COLUMNS",
    "IDENTITY_COLUMNS",
    "KNOWN_HISTORY_PATH",
    "KNOWN_HISTORY_ROWS",
    "KNOWN_HISTORY_SHA256",
    "KNOWN_REFERENCE_EVIDENCE",
    "OOS_DISCRETE_BEHAVIOR_COLUMNS",
    "OOS_SCORE_COLUMNS",
    "MODEL_PREDICTION_CANONICAL_COLUMNS",
    "PREDICTION_FILL_RELATIONSHIP_COLUMNS",
    "SELECTOR_PREDICTION_CANONICAL_COLUMNS",
    "SELECTOR_PREDICTION_GLOBAL_COLUMNS",
    "SELECTOR_OUTSIDE_BINARY_ZERO_COLUMNS",
    "SELECTOR_OUTSIDE_NUMERIC_MISSING_COLUMNS",
    "REQUIRED_ACTIVE_PIN_PATHS",
    "TOP10_DISCRETE_BEHAVIOR_COLUMNS",
    "TOP10_SCORE_COLUMNS",
    "apply_frozen_history_cutoff",
    "capture_frozen_history_snapshot",
    "compute_action_watchlist_fingerprint",
    "compute_behavior_fingerprints",
    "frame_columns_sha256",
    "history_snapshot_bootstrap_mode",
    "load_frozen_history_snapshot",
    "load_model_freeze",
    "load_verified_frozen_history_snapshot",
    "model_freeze_active",
    "validate_behavior_artifacts",
    "validate_pinned_files",
    "validate_runtime_artifacts",
]
