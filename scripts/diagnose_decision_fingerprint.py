#!/usr/bin/env python3
"""Print safe legacy components and canonical cross-platform freeze evidence."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3.config import AuctionV3Config  # noqa: E402
from top10decision.auction_v3.engine import (  # noqa: E402
    MARKET_SENTIMENT_FEATURES,
    MODEL_FEATURES,
    _hash_frame,
)
from top10decision.auction_v3.promotion_model import (  # noqa: E402
    PROMOTION_SOURCE_FEATURES,
    attach_promotion_source_features,
)
from top10decision.decision.canonical_fingerprint import (  # noqa: E402
    CANONICAL_DECIMAL_PROBES,
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_float_token,
    canonical_frame_fingerprint,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    canonical_value,
    compose_artifact_fingerprint,
    normalize_code,
    normalize_date,
    normalize_stage,
)
from top10decision.decision.model_freeze import (  # noqa: E402
    load_frozen_history_snapshot,
    load_model_freeze,
)
from top10decision.decision.trade_selector import (  # noqa: E402
    TRADE_SELECTOR_FEATURE_CONTRACT,
    TRADE_SELECTOR_FEATURES,
    TRADE_SELECTOR_VERSION,
    TradeSelectorConfig,
    _bundle_hash,
    _features as _trade_selector_feature_frame,
)


MODEL_INPUT_PATHS = (
    "models/decision_v12_frozen_history_20260805.csv.gz",
    "data/auction_v3/promotion_prior/five_year_daily_stage_board.csv",
    "data/auction_v3/promotion_prior/five_year_event_features.csv.gz",
    "models/decision_promotion_v13_validation.json",
)
MODEL_SOURCE_PATHS = (
    "src/top10decision/auction_v3/engine.py",
    "src/top10decision/auction_v3/calibration.py",
    "src/top10decision/auction_v3/config.py",
    "src/top10decision/auction_v3/promotion_model.py",
    "src/top10decision/decision/contracts.py",
    "src/top10decision/decision/observation.py",
    "src/top10decision/decision/canonical_fingerprint.py",
)
INPUT_PATHS = (
    *MODEL_INPUT_PATHS,
    *MODEL_SOURCE_PATHS,
    "src/top10decision/decision/trade_selector.py",
)
MODEL_INTEGER_KINDS = {
    name: "integer"
    for name in (
        "profit_hit",
        "big_loss_hit",
        "continuation_limit_up_hit",
        "exit_on_time",
        "market_fill",
    )
}
SELECTOR_INTEGER_KINDS = {
    "observation_rank": "integer",
    "market_fill": "integer",
}
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
    "observation_rank",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
OOS_DISCRETE_BEHAVIOR_COLUMNS = (
    "stage",
    "observation_rank",
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
    "trade_selector_globally_promoted",
    "trade_selector_policy_ready",
    *GATE_DISCRETE_BEHAVIOR_COLUMNS,
)
INTEGER_BEHAVIOR_COLUMNS = frozenset(
    {
        "observation_rank",
        "promotion_rank",
        "trade_rank",
        "shadow_rank",
        "stage_watch_rank",
    }
)
BOOLEAN_BEHAVIOR_COLUMNS = frozenset(
    {
        "trade_gate_pass",
        "trade_selected",
        "trade_shadow_selected",
        "shadow_selected",
        "selected",
        "observation_selected",
        "risk_gate_pass",
        "trade_selector_globally_promoted",
        "trade_selector_policy_ready",
        *GATE_DISCRETE_BEHAVIOR_COLUMNS,
    }
)
EXPECTED_TOP10_ROWS = 4467
EXPECTED_TOP10_DATES = 543
EXPECTED_OOS_ROWS = 3097
EXPECTED_OOS_DATES = 363
BASE_SCORE_COLUMNS = (
    "predicted_net_return",
    "predicted_mean_return_lcb",
    "predicted_profit_probability",
    "predicted_big_loss_probability",
    "predicted_continuation_limit_up_probability",
    "predicted_fill_probability",
    "predicted_exit_probability",
    "conservative_ev",
    "selection_score",
    "diagnostic_gap",
    "recommended_max_gap",
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
SCORE_COLUMNS = (*BASE_SCORE_COLUMNS, *TRADE_SCORE_COLUMNS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any, *, default: Any | None = None) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _packages() -> dict[str, str]:
    output: dict[str, str] = {}
    for name in (
        "numpy",
        "pandas",
        "scikit-learn",
        "scipy",
        "joblib",
        "lightgbm",
    ):
        try:
            output[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            output[name] = "missing"
    return output


def _file_hashes(paths: tuple[str, ...]) -> dict[str, str]:
    return {
        relative: _sha256(ROOT / relative)
        for relative in paths
        if (ROOT / relative).is_file()
    }


def _model_columns() -> list[str]:
    return list(
        dict.fromkeys(
            [
                "signal_date",
                "buy_date",
                "target_exit_date",
                "ts_code",
                "net_return",
                "profit_hit",
                "big_loss_hit",
                "continuation_limit_up_hit",
                "exit_on_time",
                "market_fill",
                *MODEL_FEATURES,
                *MARKET_SENTIMENT_FEATURES,
                *PROMOTION_SOURCE_FEATURES,
            ]
        )
    )


def _selector_columns() -> list[str]:
    return list(
        dict.fromkeys(
            [
                "signal_date",
                "ts_code",
                "stage",
                "observation_rank",
                "market_fill",
                "net_return",
                *TRADE_SELECTOR_FEATURES,
            ]
        )
    )


def _model_components(packages: dict[str, str]) -> dict[str, Any]:
    manifest = load_model_freeze(ROOT, required=True)
    history, history_audit = load_frozen_history_snapshot(ROOT, manifest)
    if history is None:
        raise RuntimeError("active freeze did not return its frozen history")
    enriched = attach_promotion_source_features(history, ROOT)
    training = enriched.dropna(
        subset=["net_return", "proposed_gap", "market_fill"]
    ).copy()
    columns = _model_columns()
    available = [column for column in columns if column in training.columns]
    training = training[available].copy()
    sort_columns = [
        column
        for column in ("signal_date", "ts_code", "proposed_gap")
        if column in training.columns
    ]
    if sort_columns:
        training = training.sort_values(sort_columns, kind="stable")
    training = training.reset_index(drop=True)

    source_hasher = hashlib.sha256()
    engine_path = ROOT / "src/top10decision/auction_v3/engine.py"
    for name in ("engine.py", "calibration.py", "config.py", "promotion_model.py"):
        source_hasher.update(engine_path.with_name(name).read_bytes())
    validation = ROOT / "models/decision_promotion_v13_validation.json"
    if validation.exists():
        source_hasher.update(validation.read_bytes())
    config = AuctionV3Config(root=ROOT)
    config_payload = {
        key: value for key, value in asdict(config).items() if key != "root"
    }
    legacy_training_sha = _hash_frame(training)
    legacy_source_sha = source_hasher.hexdigest()
    legacy_payload = {
        "model_version": config.model_version,
        "training_sha256": legacy_training_sha,
        "source_sha256": legacy_source_sha,
        "config": config_payload,
    }
    provenance_payload = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "artifact_kind": "decision_model_provenance",
        "model_version": config.model_version,
        "frozen_inputs": _file_hashes(MODEL_INPUT_PATHS),
        "source_files": _file_hashes(MODEL_SOURCE_PATHS),
        "config": config_payload,
        "runtime_packages": packages,
    }
    model_kinds = {column: "float" for column in columns}
    model_kinds.update(
        {
            "signal_date": "date",
            "buy_date": "date",
            "target_exit_date": "date",
            "ts_code": "code",
            **MODEL_INTEGER_KINDS,
        }
    )
    canonical: dict[str, Any] = {}
    for decimals in CANONICAL_DECIMAL_PROBES:
        semantic = canonical_frame_fingerprint(
            training,
            columns,
            decimals=decimals,
            kinds=model_kinds,
            strict=False,
        )
        provenance_sha = canonical_mapping_sha256(
            provenance_payload,
            decimals=decimals,
        )
        canonical[str(decimals)] = {
            "provenance_sha256": provenance_sha,
            "semantic_sha256": semantic["sha256"],
            "schema_valid": semantic["valid"],
            "missing_columns": semantic["missing_columns"],
            "invalid_cell_count": semantic["invalid_cell_count"],
            "invalid_cell_sample": semantic["invalid_cell_sample"],
            "artifact_sha256": compose_artifact_fingerprint(
                artifact_kind="decision_model",
                provenance_sha256=provenance_sha,
                semantic_sha256=semantic["sha256"],
                decimals=decimals,
            ),
        }
    return {
        "legacy": {
            "recomputed_model_artifact_sha256": _json_sha256(legacy_payload),
            "training_sha256": legacy_training_sha,
            "source_sha256": legacy_source_sha,
            "config_sha256": _json_sha256(config_payload, default=str),
        },
        "canonical_v2": canonical,
        "history_audit": history_audit,
        "history_rows": int(len(history)),
        "training_rows": int(len(training)),
        "training_columns": list(training.columns),
        "training_dtypes": {
            name: str(dtype) for name, dtype in training.dtypes.items()
        },
        "legacy_training_column_sha256": {
            column: _hash_frame(training[[column]]) for column in training.columns
        },
    }


def _selector_snapshot(
    top10_path: Path,
    model_meta_path: Path,
    packages: dict[str, str],
) -> dict[str, Any]:
    if not top10_path.is_file():
        return {"status": "missing", "top10_path": str(top10_path)}
    frame = pd.read_csv(top10_path, low_memory=False)
    semantic_frame = frame.copy()
    derived_features = _trade_selector_feature_frame(frame)
    for column in ("stage_2to3", "stage_3to4"):
        semantic_frame[column] = derived_features[column]
    model_meta = _read_json(model_meta_path)
    policy = ((model_meta.get("trade_selector") or {}).get("production_policy") or {})
    provenance = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "artifact_kind": "decision_trade_selector_provenance",
        "selector_version": TRADE_SELECTOR_VERSION,
        "feature_contract": TRADE_SELECTOR_FEATURE_CONTRACT,
        "features": list(TRADE_SELECTOR_FEATURES),
        "config": asdict(TradeSelectorConfig()),
        "algorithm_source_sha256": _file_hashes(
            (
                "src/top10decision/decision/trade_selector.py",
                "src/top10decision/decision/canonical_fingerprint.py",
            )
        ),
        "runtime_packages": packages,
    }
    selector_columns = _selector_columns()
    selector_kinds = {column: "float" for column in selector_columns}
    selector_kinds.update(
        {
            "signal_date": "date",
            "ts_code": "code",
            "stage": "stage",
            **SELECTOR_INTEGER_KINDS,
        }
    )
    canonical: dict[str, Any] = {}
    for decimals in CANONICAL_DECIMAL_PROBES:
        semantic = canonical_frame_fingerprint(
            semantic_frame,
            selector_columns,
            decimals=decimals,
            kinds=selector_kinds,
            strict=False,
        )
        policy_hash = canonical_policy_fingerprint(policy, decimals=decimals)
        provenance_sha = canonical_mapping_sha256(provenance, decimals=decimals)
        canonical[str(decimals)] = {
            "provenance_sha256": provenance_sha,
            "semantic_sha256": semantic["sha256"],
            "schema_valid": semantic["valid"],
            "missing_columns": semantic["missing_columns"],
            "invalid_cell_count": semantic["invalid_cell_count"],
            "invalid_cell_sample": semantic["invalid_cell_sample"],
            "policy_sha256": policy_hash["sha256"],
            "artifact_sha256": compose_artifact_fingerprint(
                artifact_kind="decision_trade_selector",
                provenance_sha256=provenance_sha,
                semantic_sha256=semantic["sha256"],
                policy_sha256=policy_hash["sha256"],
                decimals=decimals,
            ),
            "policy_projection": policy_hash["projection"],
        }
    return {
        "status": "loaded",
        "top10_path": str(top10_path),
        "rows": int(len(frame)),
        "dates": int(
            frame["signal_date"].astype(str).nunique()
            if "signal_date" in frame.columns
            else 0
        ),
        "legacy_roundtrip_bundle_sha256": _bundle_hash(frame, policy),
        "canonical_v2": canonical,
    }


def _is_missing_scalar(value: Any) -> bool:
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
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _identity(row: pd.Series) -> tuple[str, str] | None:
    date_value = row.get("signal_date")
    code_value = row.get("ts_code")
    if _is_missing_scalar(date_value) or _is_missing_scalar(code_value):
        return None
    date = normalize_date(date_value)
    code = normalize_code(code_value)
    if not re.fullmatch(r"[0-9]{8}", date):
        return None
    if not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", code):
        return None
    return date, code


def _identity_object(identity: tuple[str, str]) -> dict[str, str]:
    return dict(zip(IDENTITY_COLUMNS, identity))


def _identity_audit(
    frame: pd.DataFrame,
) -> tuple[dict[str, Any], Counter[tuple[str, str]]]:
    missing_columns = [
        column for column in IDENTITY_COLUMNS if column not in frame.columns
    ]
    if missing_columns:
        return (
            {
                "status": "schema_error",
                "missing_identity_columns": missing_columns,
                "rows": int(len(frame)),
                "unique_nonempty": False,
            },
            Counter(),
        )
    counter: Counter[tuple[str, str]] = Counter()
    invalid_rows: list[dict[str, int]] = []
    for row_number, (_, row) in enumerate(frame.iterrows()):
        identity = _identity(row)
        if identity is None:
            invalid_rows.append({"row": int(row_number)})
        else:
            counter[identity] += 1
    duplicates = sorted(
        (identity, count) for identity, count in counter.items() if count > 1
    )
    identity_objects = [
        _identity_object(identity)
        for identity, count in sorted(counter.items())
        for _ in range(count)
    ]
    return (
        {
            "status": "valid" if not invalid_rows and not duplicates else "invalid",
            "rows": int(len(frame)),
            "valid_identity_rows": int(sum(counter.values())),
            "invalid_identity_count": int(len(invalid_rows)),
            "invalid_identity_sample": invalid_rows[:20],
            "duplicate_identity_count": int(len(duplicates)),
            "duplicate_identity_row_count": int(
                sum(count for _, count in duplicates)
            ),
            "duplicate_identity_sample": [
                {**_identity_object(identity), "count": int(count)}
                for identity, count in duplicates[:20]
            ],
            "identity_multiset_sha256": canonical_mapping_sha256(
                identity_objects, decimals=12
            ),
            "unique_nonempty": not invalid_rows and not duplicates,
        },
        counter,
    )


def _counter_difference(
    left: Counter[tuple[str, str]],
    right: Counter[tuple[str, str]],
) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for key in sorted(set(left).union(right)):
        output.extend([key] * max(0, left[key] - right[key]))
    return output


def _behavior_token(value: Any, column: str) -> Any:
    if column == "stage":
        return canonical_value(value, decimals=12, kind="stage")
    if column in INTEGER_BEHAVIOR_COLUMNS:
        return canonical_value(value, decimals=12, kind="integer")
    if column in BOOLEAN_BEHAVIOR_COLUMNS:
        if _is_missing_scalar(value):
            return {"$special": "missing"}
        if isinstance(value, bool):
            return value
        text = unicodedata.normalize("NFKC", str(value)).strip().lower()
        if text in {"1", "1.0", "true", "yes", "y"}:
            return True
        if text in {"0", "0.0", "false", "no", "n"}:
            return False
        return {"$special": "invalid"}
    return canonical_value(value, decimals=12, kind="text")


def _is_invalid_token(value: Any) -> bool:
    return isinstance(value, dict) and value.get("$special") == "invalid"


def _behavior_comparison(
    reference: pd.DataFrame,
    fresh: pd.DataFrame,
    required_columns: tuple[str, ...],
) -> dict[str, Any]:
    missing_reference = [
        column for column in required_columns if column not in reference.columns
    ]
    missing_fresh = [
        column for column in required_columns if column not in fresh.columns
    ]
    left_audit, left_counter = _identity_audit(reference)
    right_audit, right_counter = _identity_audit(fresh)
    base: dict[str, Any] = {
        "required_columns": list(required_columns),
        "missing_reference_columns": missing_reference,
        "missing_fresh_columns": missing_fresh,
        "reference_identity": left_audit,
        "fresh_identity": right_audit,
    }
    if (
        left_audit.get("status") == "schema_error"
        or right_audit.get("status") == "schema_error"
    ):
        return {**base, "status": "schema_error", "comparison_performed": False}
    if missing_reference or missing_fresh:
        return {**base, "status": "schema_error", "comparison_performed": False}
    if (
        left_audit.get("invalid_identity_count", 0)
        or right_audit.get("invalid_identity_count", 0)
    ):
        return {**base, "status": "invalid_identity", "comparison_performed": False}
    if (
        left_audit.get("duplicate_identity_count", 0)
        or right_audit.get("duplicate_identity_count", 0)
    ):
        return {
            **base,
            "status": "ambiguous_duplicate_identity",
            "comparison_performed": False,
        }

    removed = _counter_difference(left_counter, right_counter)
    added = _counter_difference(right_counter, left_counter)
    left_rows = {
        _identity(row): row for _, row in reference.iterrows()
    }
    right_rows = {_identity(row): row for _, row in fresh.iterrows()}
    common = sorted(set(left_rows).intersection(right_rows))
    changes: list[dict[str, Any]] = []
    invalid_cells: list[dict[str, Any]] = []
    changed_by_column = {column: 0 for column in required_columns}
    for identity in common:
        left_row, right_row = left_rows[identity], right_rows[identity]
        for column in required_columns:
            left_value = _behavior_token(left_row[column], column)
            right_value = _behavior_token(right_row[column], column)
            if _is_invalid_token(left_value) or _is_invalid_token(right_value):
                invalid_cells.append(
                    {**_identity_object(identity), "column": column}
                )
                continue
            if left_value != right_value:
                changed_by_column[column] += 1
                changes.append(
                    {
                        **_identity_object(identity),
                        "column": column,
                        "reference": left_value,
                        "fresh": right_value,
                    }
                )
    identity_equal = left_counter == right_counter
    status = "compared"
    if invalid_cells:
        status = "schema_error"
    elif not identity_equal:
        status = "identity_mismatch"
    return {
        **base,
        "status": status,
        "comparison_performed": True,
        "identity_multiset_equal": identity_equal,
        "matched_identity_count": int(len(common)),
        "removed_identity_count": int(len(removed)),
        "added_identity_count": int(len(added)),
        "removed_identity_sha256": canonical_mapping_sha256(
            [_identity_object(identity) for identity in removed], decimals=12
        ),
        "added_identity_sha256": canonical_mapping_sha256(
            [_identity_object(identity) for identity in added], decimals=12
        ),
        "removed_identity_sample": [
            _identity_object(identity) for identity in removed[:20]
        ],
        "added_identity_sample": [
            _identity_object(identity) for identity in added[:20]
        ],
        "invalid_behavior_cell_count": int(len(invalid_cells)),
        "invalid_behavior_cell_sample": invalid_cells[:20],
        "changed_count": int(len(changes)),
        "changed_by_column": changed_by_column,
        "changed_sha256": canonical_mapping_sha256(changes, decimals=12),
        "changed_sample": changes[:20],
        "all_equal": identity_equal and not invalid_cells and not changes,
    }


def _date_counts(frame: pd.DataFrame) -> Counter[str]:
    if "signal_date" not in frame.columns:
        return Counter()
    return Counter(normalize_date(value) for value in frame["signal_date"])


def _score_comparison(
    reference: pd.DataFrame,
    fresh: pd.DataFrame,
    required_columns: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    left_audit, left_counter = _identity_audit(reference)
    right_audit, right_counter = _identity_audit(fresh)
    base = {
        "reference_identity": left_audit,
        "fresh_identity": right_audit,
    }
    if (
        left_audit.get("status") == "schema_error"
        or right_audit.get("status") == "schema_error"
    ):
        return {**base, "status": "schema_error", "comparison_performed": False}
    missing_reference = [
        column
        for column in (required_columns or ())
        if column not in reference.columns
    ]
    missing_fresh = [
        column for column in (required_columns or ()) if column not in fresh.columns
    ]
    base.update(
        {
            "missing_reference_numeric_columns": missing_reference,
            "missing_fresh_numeric_columns": missing_fresh,
        }
    )
    if missing_reference or missing_fresh:
        return {**base, "status": "schema_error", "comparison_performed": False}
    if (
        left_audit.get("invalid_identity_count", 0)
        or right_audit.get("invalid_identity_count", 0)
    ):
        return {**base, "status": "invalid_identity", "comparison_performed": False}
    if (
        left_audit.get("duplicate_identity_count", 0)
        or right_audit.get("duplicate_identity_count", 0)
    ):
        return {
            **base,
            "status": "ambiguous_duplicate_identity",
            "comparison_performed": False,
        }
    left_rows = {_identity(row): row for _, row in reference.iterrows()}
    right_rows = {_identity(row): row for _, row in fresh.iterrows()}
    common = sorted(set(left_rows).intersection(right_rows))
    columns = (
        list(required_columns)
        if required_columns is not None
        else [
            column
            for column in SCORE_COLUMNS
            if column in reference.columns and column in fresh.columns
        ]
    )
    numeric: dict[str, Any] = {}
    converted: dict[str, tuple[pd.Series, pd.Series]] = {}
    invalid_numeric_columns: list[str] = []
    for column in columns:
        left_values = [left_rows[identity][column] for identity in common]
        right_values = [right_rows[identity][column] for identity in common]
        left_values = [None if _is_missing_scalar(value) else value for value in left_values]
        right_values = [
            None if _is_missing_scalar(value) else value for value in right_values
        ]
        try:
            a = pd.to_numeric(pd.Series(left_values), errors="raise")
            b = pd.to_numeric(pd.Series(right_values), errors="raise")
        except (TypeError, ValueError):
            invalid_numeric_columns.append(column)
            continue
        converted[column] = (a, b)
        finite = a.notna() & b.notna()
        delta = (a[finite] - b[finite]).abs()
        numeric[column] = {
            "finite_cells": int(finite.sum()),
            "missing_state_mismatches": int((a.isna() != b.isna()).sum()),
            "max_abs_delta": float(delta.max()) if not delta.empty else None,
            "mean_abs_delta": float(delta.mean()) if not delta.empty else None,
        }
    precision: dict[str, Any] = {}
    for decimals in CANONICAL_DECIMAL_PROBES:
        changed_rows: set[str] = set()
        by_column: dict[str, int] = {}
        for column, (a, b) in converted.items():
            changed = [
                canonical_float_token(left_value, decimals=decimals)
                != canonical_float_token(right_value, decimals=decimals)
                for left_value, right_value in zip(a, b)
            ]
            by_column[column] = int(sum(changed))
            changed_rows.update(
                "|".join(identity)
                for identity, differs in zip(common, changed)
                if differs
            )
        precision[str(decimals)] = {
            "changed_rows": int(len(changed_rows)),
            "changed_cells": int(sum(by_column.values())),
            "changed_cells_by_column": by_column,
        }
    return {
        **base,
        "status": (
            "schema_error"
            if invalid_numeric_columns
            else "compared"
            if left_counter == right_counter
            else "identity_mismatch"
        ),
        "comparison_performed": True,
        "identity_multiset_equal": left_counter == right_counter,
        "matched_identity_count": int(len(common)),
        "invalid_numeric_columns": invalid_numeric_columns,
        "numeric_delta": numeric,
        "precision": precision,
    }


def _dataset_comparison(
    reference: pd.DataFrame,
    fresh: pd.DataFrame,
    required_behavior_columns: tuple[str, ...],
    required_score_columns: tuple[str, ...],
) -> dict[str, Any]:
    left_dates, right_dates = _date_counts(reference), _date_counts(fresh)
    date_diff = [
        {
            "signal_date": date,
            "reference_rows": int(left_dates[date]),
            "fresh_rows": int(right_dates[date]),
        }
        for date in sorted(set(left_dates).union(right_dates))
        if left_dates[date] != right_dates[date]
    ]
    return {
        "reference_rows": int(len(reference)),
        "fresh_rows": int(len(fresh)),
        "reference_dates": int(len(left_dates)),
        "fresh_dates": int(len(right_dates)),
        "per_date_counts_equal": left_dates == right_dates,
        "per_date_difference_count": int(len(date_diff)),
        "per_date_difference_sha256": canonical_mapping_sha256(
            date_diff, decimals=12
        ),
        "per_date_difference_sample": date_diff[:50],
        "discrete_behavior": _behavior_comparison(
            reference, fresh, required_behavior_columns
        ),
        "score_comparison": _score_comparison(
            reference, fresh, required_score_columns
        ),
    }


def _reference_vs_fresh(
    reference_dir: Path | None,
    fresh_top10: Path,
    fresh_oos: Path,
    fresh_meta: Path,
    packages: dict[str, str],
) -> dict[str, Any]:
    if reference_dir is None:
        return {"status": "reference_not_configured"}
    reference_top10 = reference_dir / "backtest_top10_latest.csv"
    reference_oos = reference_dir / "backtest_trade_selector_oos_latest.csv"
    reference_meta = reference_dir / "model_meta_latest.json"
    required_paths = {
        "reference_top10": reference_top10,
        "fresh_top10": fresh_top10,
        "reference_oos": reference_oos,
        "fresh_oos": fresh_oos,
        "reference_meta": reference_meta,
        "fresh_meta": fresh_meta,
    }
    missing_paths = [name for name, path in required_paths.items() if not path.is_file()]
    if missing_paths:
        return {
            "status": "snapshot_missing",
            "missing_paths": missing_paths,
            "path_exists": {
                name: path.is_file() for name, path in required_paths.items()
            },
        }
    left = pd.read_csv(reference_top10, low_memory=False)
    right = pd.read_csv(fresh_top10, low_memory=False)
    left_oos = pd.read_csv(reference_oos, low_memory=False)
    right_oos = pd.read_csv(fresh_oos, low_memory=False)
    left_snapshot = _selector_snapshot(reference_top10, reference_meta, packages)
    right_snapshot = _selector_snapshot(fresh_top10, fresh_meta, packages)
    precision: dict[str, Any] = {}
    for decimals in CANONICAL_DECIMAL_PROBES:
        key = str(decimals)
        a = (left_snapshot.get("canonical_v2") or {}).get(key, {})
        b = (right_snapshot.get("canonical_v2") or {}).get(key, {})
        precision[key] = {
            "semantic_equal": a.get("semantic_sha256") == b.get("semantic_sha256"),
            "policy_equal": a.get("policy_sha256") == b.get("policy_sha256"),
            "artifact_equal": a.get("artifact_sha256") == b.get("artifact_sha256"),
            "reference_semantic_sha256": a.get("semantic_sha256", ""),
            "fresh_semantic_sha256": b.get("semantic_sha256", ""),
            "reference_policy_sha256": a.get("policy_sha256", ""),
            "fresh_policy_sha256": b.get("policy_sha256", ""),
        }
    return {
        "status": "compared",
        "top10": {
            **_dataset_comparison(
                left,
                right,
                TOP10_DISCRETE_BEHAVIOR_COLUMNS,
                BASE_SCORE_COLUMNS,
            ),
            "precision": precision,
        },
        "trade_selector_oos": _dataset_comparison(
            left_oos,
            right_oos,
            OOS_DISCRETE_BEHAVIOR_COLUMNS,
            SCORE_COLUMNS,
        ),
    }


def _strict_frame_schema(
    frame: pd.DataFrame,
    *,
    required_behavior_columns: tuple[str, ...],
    required_numeric_columns: tuple[str, ...],
) -> dict[str, Any]:
    missing_columns = [
        column
        for column in (
            *IDENTITY_COLUMNS,
            *required_behavior_columns,
            *required_numeric_columns,
        )
        if column not in frame.columns
    ]
    identity, _ = _identity_audit(frame)
    invalid_behavior_cells: list[dict[str, Any]] = []
    for column in required_behavior_columns:
        if column not in frame.columns:
            continue
        for row_number, value in enumerate(frame[column]):
            token = _behavior_token(value, column)
            if isinstance(token, dict) and token.get("$special") in {
                "missing",
                "invalid",
            }:
                invalid_behavior_cells.append(
                    {"row": int(row_number), "column": column}
                )
    invalid_numeric_columns: list[str] = []
    nonintegral_columns: list[str] = []
    for column in required_numeric_columns:
        if column not in frame.columns:
            continue
        values = frame[column]
        nonempty = values[
            [not _is_missing_scalar(value) for value in values]
        ]
        try:
            numbers = pd.to_numeric(nonempty, errors="raise")
        except (TypeError, ValueError):
            invalid_numeric_columns.append(column)
            continue
        if column in INTEGER_BEHAVIOR_COLUMNS:
            finite = numbers[pd.notna(numbers)]
            if any(
                not math.isfinite(float(number))
                or not math.isclose(
                    float(number), round(float(number)), rel_tol=0.0, abs_tol=1e-9
                )
                for number in finite
            ):
                nonintegral_columns.append(column)
    valid = not any(
        (
            missing_columns,
            invalid_behavior_cells,
            invalid_numeric_columns,
            nonintegral_columns,
            not identity.get("unique_nonempty", False),
        )
    )
    return {
        "valid": valid,
        "rows": int(len(frame)),
        "dates": int(len(_date_counts(frame))),
        "missing_columns": sorted(set(missing_columns)),
        "invalid_behavior_cell_count": int(len(invalid_behavior_cells)),
        "invalid_behavior_cell_sample": invalid_behavior_cells[:20],
        "invalid_numeric_columns": invalid_numeric_columns,
        "nonintegral_columns": nonintegral_columns,
        "identity": identity,
    }


def _strict_integer_column(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if column not in frame.columns:
        return {"valid": False, "missing_column": True, "sum": None}
    tokens = [_behavior_token(value, column) for value in frame[column]]
    invalid = [
        row
        for row, token in enumerate(tokens)
        if isinstance(token, dict)
    ]
    values = [int(token) for token in tokens if not isinstance(token, dict)]
    return {
        "valid": not invalid,
        "missing_column": False,
        "invalid_count": int(len(invalid)),
        "invalid_row_sample": invalid[:20],
        "sum": int(sum(values)) if not invalid else None,
    }


def _hard_invariant_report(
    *,
    model_components: dict[str, Any],
    selector_components: dict[str, Any],
    reference_comparison: dict[str, Any],
    fresh_top10: Path,
    fresh_oos: Path,
    model_meta: dict[str, Any],
    backtest: dict[str, Any],
) -> dict[str, Any]:
    top10 = (
        pd.read_csv(fresh_top10, low_memory=False)
        if fresh_top10.is_file()
        else pd.DataFrame()
    )
    oos = (
        pd.read_csv(fresh_oos, low_memory=False)
        if fresh_oos.is_file()
        else pd.DataFrame()
    )
    top10_schema = _strict_frame_schema(
        top10,
        required_behavior_columns=TOP10_DISCRETE_BEHAVIOR_COLUMNS,
        required_numeric_columns=BASE_SCORE_COLUMNS,
    )
    oos_schema = _strict_frame_schema(
        oos,
        required_behavior_columns=OOS_DISCRETE_BEHAVIOR_COLUMNS,
        required_numeric_columns=SCORE_COLUMNS,
    )
    formal_selected = _strict_integer_column(top10, "selected")
    globally_promoted = _strict_integer_column(
        oos, "trade_selector_globally_promoted"
    )
    selector_meta = model_meta.get("trade_selector") or {}
    selector_backtest = backtest.get("trade_selector") or {}
    production_policy = selector_meta.get("production_policy") or {}
    backtest_policy = selector_backtest.get("production_policy") or {}
    top10_comparison = reference_comparison.get("top10") or {}
    oos_comparison = reference_comparison.get("trade_selector_oos") or {}
    top10_behavior = top10_comparison.get("discrete_behavior") or {}
    oos_behavior = oos_comparison.get("discrete_behavior") or {}
    top10_precision_12 = (top10_comparison.get("precision") or {}).get("12") or {}
    top10_score_comparison = top10_comparison.get("score_comparison") or {}
    oos_score_comparison = oos_comparison.get("score_comparison") or {}
    top10_score_12 = (
        (top10_score_comparison.get("precision") or {}).get("12")
        or {}
    )
    oos_score_12 = (
        (oos_score_comparison.get("precision") or {}).get("12")
        or {}
    )
    top10_numeric_delta = top10_score_comparison.get("numeric_delta") or {}
    oos_numeric_delta = oos_score_comparison.get("numeric_delta") or {}
    model_schema = (
        (model_components.get("canonical_v2") or {}).get("12") or {}
    )
    selector_schema = (
        (selector_components.get("canonical_v2") or {}).get("12") or {}
    )
    reason_values = (
        sorted(
            unicodedata.normalize("NFKC", str(value)).strip()
            for value in top10.get("model_reason", pd.Series(dtype=object)).dropna()
        )
        if "model_reason" in top10.columns
        else []
    )
    reason_contract = bool(reason_values) and set(reason_values) == {
        "selection_policy_not_ready"
    }
    checks = {
        "fresh_top10_exists": fresh_top10.is_file(),
        "fresh_oos_exists": fresh_oos.is_file(),
        "model_canonical_schema_valid": model_schema.get("schema_valid") is True,
        "selector_canonical_schema_valid": selector_schema.get("schema_valid")
        is True,
        "top10_strict_schema_valid": top10_schema["valid"],
        "oos_strict_schema_valid": oos_schema["valid"],
        "top10_rows_4467": len(top10) == EXPECTED_TOP10_ROWS,
        "top10_dates_543": len(_date_counts(top10)) == EXPECTED_TOP10_DATES,
        "oos_rows_3097": len(oos) == EXPECTED_OOS_ROWS,
        "oos_dates_363": len(_date_counts(oos)) == EXPECTED_OOS_DATES,
        "meta_history_rows_4467": selector_meta.get("history_rows")
        == EXPECTED_TOP10_ROWS,
        "meta_history_dates_543": selector_meta.get("history_dates")
        == EXPECTED_TOP10_DATES,
        "meta_oos_rows_3097": selector_meta.get("oos_rows") == EXPECTED_OOS_ROWS,
        "meta_oos_dates_363": selector_meta.get("oos_dates")
        == EXPECTED_OOS_DATES,
        "backtest_history_rows_4467": selector_backtest.get("history_rows")
        == EXPECTED_TOP10_ROWS,
        "backtest_history_dates_543": selector_backtest.get("history_dates")
        == EXPECTED_TOP10_DATES,
        "backtest_oos_rows_3097": selector_backtest.get("oos_rows")
        == EXPECTED_OOS_ROWS,
        "backtest_oos_dates_363": selector_backtest.get("oos_dates")
        == EXPECTED_OOS_DATES,
        "selector_not_promoted": selector_meta.get("promoted") is False
        and selector_backtest.get("promoted") is False,
        "production_policy_not_ready": production_policy.get("ready") is False
        and backtest_policy.get("ready") is False,
        "formal_selected_count_zero": formal_selected.get("valid") is True
        and formal_selected.get("sum") == 0,
        "globally_promoted_count_zero": globally_promoted.get("valid") is True
        and globally_promoted.get("sum") == 0,
        "no_trade_reason_contract": reason_contract,
        "no_trade_backtest_signals_zero": backtest.get("signals") == 0,
        "no_trade_backtest_signal_dates_zero": backtest.get("signal_dates") == 0,
        "no_trade_backtest_fills_zero": backtest.get("filled_trades") == 0,
        "reference_comparison_completed": reference_comparison.get("status")
        == "compared",
        "top10_per_date_counts_equal": top10_comparison.get(
            "per_date_counts_equal"
        )
        is True,
        "oos_per_date_counts_equal": oos_comparison.get("per_date_counts_equal")
        is True,
        "top10_identity_and_discrete_equal": top10_behavior.get("all_equal")
        is True,
        "oos_identity_and_discrete_equal": oos_behavior.get("all_equal") is True,
        "top10_semantic_equal_at_12_decimals": top10_precision_12.get(
            "semantic_equal"
        )
        is True,
        "top10_policy_equal_at_12_decimals": top10_precision_12.get(
            "policy_equal"
        )
        is True,
        "top10_scores_equal_at_12_decimals": top10_score_12.get("changed_rows")
        == 0,
        "oos_scores_equal_at_12_decimals": oos_score_12.get("changed_rows") == 0,
        "top10_score_comparison_valid": top10_score_comparison.get("status")
        == "compared"
        and top10_score_comparison.get("invalid_numeric_columns") == [],
        "oos_score_comparison_valid": oos_score_comparison.get("status")
        == "compared"
        and oos_score_comparison.get("invalid_numeric_columns") == [],
        "top10_score_missing_states_equal": bool(top10_numeric_delta)
        and all(
            values.get("missing_state_mismatches") == 0
            for values in top10_numeric_delta.values()
        ),
        "oos_score_missing_states_equal": bool(oos_numeric_delta)
        and all(
            values.get("missing_state_mismatches") == 0
            for values in oos_numeric_delta.values()
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "top10_schema": top10_schema,
        "oos_schema": oos_schema,
        "formal_selected": formal_selected,
        "globally_promoted": globally_promoted,
        "expected_contract": {
            "promoted": False,
            "production_policy_ready": False,
            "top10_rows": EXPECTED_TOP10_ROWS,
            "top10_dates": EXPECTED_TOP10_DATES,
            "oos_rows": EXPECTED_OOS_ROWS,
            "oos_dates": EXPECTED_OOS_DATES,
            "formal_selected_count": 0,
            "global_promotion_count": 0,
            "decision": "NO_TRADE",
        },
    }


def main() -> int:
    manifest = load_model_freeze(ROOT, required=True)
    packages = _packages()
    fresh_top10 = Path(
        os.environ.get(
            "FINGERPRINT_FRESH_TOP10",
            str(ROOT / "outputs/auction_v3/metrics/backtest_top10_latest.csv"),
        )
    )
    fresh_meta = Path(
        os.environ.get(
            "FINGERPRINT_FRESH_MODEL_META",
            str(ROOT / "outputs/auction_v3/models/model_meta_latest.json"),
        )
    )
    fresh_backtest = Path(
        os.environ.get(
            "FINGERPRINT_FRESH_BACKTEST",
            str(ROOT / "outputs/auction_v3/metrics/backtest_latest.json"),
        )
    )
    fresh_oos = Path(
        os.environ.get(
            "FINGERPRINT_FRESH_OOS",
            str(
                ROOT
                / "outputs/auction_v3/metrics/backtest_trade_selector_oos_latest.csv"
            ),
        )
    )
    reference_text = os.environ.get("FINGERPRINT_REFERENCE_DIR", "").strip()
    reference_dir = Path(reference_text) if reference_text else None
    model_meta = _read_json(fresh_meta)
    backtest = _read_json(fresh_backtest)
    production = manifest.get("production") or {}
    selector_meta = model_meta.get("trade_selector") or {}
    selector_backtest = backtest.get("trade_selector") or {}
    model_components = _model_components(packages)
    selector_components = _selector_snapshot(fresh_top10, fresh_meta, packages)
    reference_comparison = _reference_vs_fresh(
        reference_dir, fresh_top10, fresh_oos, fresh_meta, packages
    )
    hard_invariants = _hard_invariant_report(
        model_components=model_components,
        selector_components=selector_components,
        reference_comparison=reference_comparison,
        fresh_top10=fresh_top10,
        fresh_oos=fresh_oos,
        model_meta=model_meta,
        backtest=backtest,
    )
    summary = {
        "schema_version": "dc20_decision_fingerprint_diagnostic_v2",
        "canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
        "system": "DC2.0",
        "read_only": True,
        "expected": {
            "model_artifact_sha256": production.get("model_artifact_sha256", ""),
            "trade_selector_artifact_sha256": production.get(
                "trade_selector_artifact_sha256", ""
            ),
        },
        "generated": {
            "model_artifact_sha256": model_meta.get("model_artifact_sha256", ""),
            "meta_trade_selector_artifact_sha256": selector_meta.get(
                "production_artifact_sha256", ""
            ),
            "backtest_trade_selector_artifact_sha256": selector_backtest.get(
                "production_artifact_sha256", ""
            ),
        },
        "model_components": model_components,
        "fresh_selector_components": selector_components,
        "reference_vs_fresh": reference_comparison,
        "hard_invariants": hard_invariants,
        "input_sha256": _file_hashes(INPUT_PATHS),
        "packages": packages,
        "python": sys.version,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if hard_invariants["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
