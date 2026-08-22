#!/usr/bin/env python3
"""Print safe legacy components and canonical cross-platform freeze evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping
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
from top10decision.decision import model_freeze as freeze_contract  # noqa: E402
from top10decision.decision.model_freeze import (  # noqa: E402
    ACTION_WATCHLIST_COLUMNS,
    BEHAVIOR_SCHEMA_VERSION,
    CANONICAL_RUNTIME_SCHEMA_VERSION,
    DecisionModelFreezeError,
    IDENTITY_COLUMNS as FREEZE_IDENTITY_COLUMNS,
    KNOWN_REFERENCE_EVIDENCE,
    OOS_DISCRETE_BEHAVIOR_COLUMNS as FREEZE_OOS_DISCRETE_COLUMNS,
    OOS_SCORE_COLUMNS as FREEZE_OOS_SCORE_COLUMNS,
    TOP10_DISCRETE_BEHAVIOR_COLUMNS as FREEZE_TOP10_DISCRETE_COLUMNS,
    TOP10_SCORE_COLUMNS as FREEZE_TOP10_SCORE_COLUMNS,
    compute_action_watchlist_fingerprint,
    compute_behavior_fingerprints,
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
ACTIVATION_EVIDENCE_SCHEMA = "dc20_canonical_v2_activation_evidence_v1"
ACTIVATION_EVIDENCE_MAX_BYTES = 512 * 1024
ACTIVATION_SOURCE6_SCHEMA = "decision_canonical_v2_source6_v1"
EXPECTED_ACTIVATION_SOURCE6_SHA256 = (
    "1e00438a57b100e17637d016ee9c768accf78f04612bd00eef1c35131eedc467"
)
EXPECTED_FROZEN_BEHAVIOR_COUNTS = {
    "top10": {
        "rows": 4467,
        "signal_dates": 543,
        "observation_selected": 4467,
        "shadow_selected": 1069,
        "risk_gate_pass": 0,
        "selected": 0,
    },
    "trade_selector_oos": {
        "rows": 3097,
        "signal_dates": 363,
        "trade_selected": 158,
        "trade_shadow_selected": 523,
        "shadow_selected": 726,
        "trade_selector_promoted": 3097,
        "trade_selector_globally_promoted": 0,
        "trade_selector_policy_ready": 1083,
    },
    "nested_oos_research": {
        "signals": 158,
        "signal_dates": 119,
        "filled_trades": 158,
        "market_buyable_filled_trades": 25,
    },
    "production": {
        "promoted": False,
        "trade_selector_promoted": False,
        "signals": 0,
        "signal_dates": 0,
        "filled_trades": 0,
    },
}
LEGACY_DIAGNOSTIC_MANIFEST_SHA256 = (
    "87605814bce9f2180e151ed91d6c16e2c22b46c3dcd147e8bdab7895c3f0975a"
)
EXPECTED_HISTORY_EVIDENCE = {
    "freeze_id": "dc20_decision_v13_promotion_oos_d20260815_history20260805",
    "training_cutoff_signal_date": "20260805",
    "signal_dates": 715,
    "columns": 151,
    "columns_sha256": (
        "dbfd38f20f00cbd57460ac3a858f937fa560bcd221b0ed3a12b408bc6c313d49"
    ),
    "history_start": "20230822",
    "history_end": "20260805",
}
ACTIVATION_SOURCE_PATHS = (
    "src/top10decision/auction_v3/engine.py",
    "src/top10decision/decision/trade_selector.py",
    "src/top10decision/decision/canonical_fingerprint.py",
    "src/top10decision/decision/action_plan.py",
    "scripts/publish_decision_action.py",
    "scripts/replay_frozen_canonical_v2.py",
)
TOP10_EVIDENCE_PATH = "outputs/auction_v3/metrics/backtest_top10_latest.csv"
OOS_EVIDENCE_PATH = (
    "outputs/auction_v3/metrics/backtest_trade_selector_oos_latest.csv"
)
BACKTEST_EVIDENCE_PATH = "outputs/auction_v3/metrics/backtest_latest.json"
MODEL_META_EVIDENCE_PATH = "outputs/auction_v3/models/model_meta_latest.json"
PREDICTION_EVIDENCE_PATH = "outputs/auction_v3/predictions/pred_latest.csv"
ACTION_EVIDENCE_PATH = "outputs/decision/action_plan_latest.json"
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


def _legacy_main() -> int:
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


def _evidence_require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"activation evidence: {message}")


def _evidence_mapping(value: Any, context: str) -> dict[str, Any]:
    _evidence_require(isinstance(value, Mapping), f"{context} must be an object")
    return dict(value)


def _evidence_strict_int(value: Any, context: str) -> int:
    _evidence_require(type(value) is int, f"{context} must be a JSON integer")
    return int(value)


def _evidence_json(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    _evidence_require(path.is_file(), f"required JSON missing: {relative}")
    value = _read_json(path)
    _evidence_require(bool(value), f"required JSON invalid or empty: {relative}")
    return value


def _evidence_csv(root: Path, relative: str) -> pd.DataFrame:
    path = root / relative
    _evidence_require(path.is_file(), f"required CSV missing: {relative}")
    try:
        return pd.read_csv(
            path,
            low_memory=False,
            dtype={"signal_date": "string", "ts_code": "string"},
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise RuntimeError(f"activation evidence: unreadable CSV: {relative}") from exc


def _runtime_layer_projection(
    container: Mapping[str, Any],
    *,
    layer: str,
    context: str,
) -> dict[str, Any]:
    if layer == "model":
        output = {
            "canonical_v2_version": container.get("model_canonical_v2_version"),
            "artifact_v2_sha256": container.get("model_artifact_v2_sha256"),
            "fingerprint_v2": container.get("model_fingerprint_v2"),
            "canonical_contract": container.get("model_canonical_contract"),
        }
    else:
        output = {
            "canonical_v2_version": container.get("canonical_v2_version"),
            "artifact_v2_sha256": container.get("production_artifact_v2_sha256"),
            "fingerprint_v2": container.get("production_fingerprint_v2"),
            "canonical_contract": container.get("canonical_contract"),
        }
    # C3 owns the exact 4-key layer, 11-key fingerprint, 6-key contract,
    # strict types, policy re-hash, and artifact re-composition.  The adapter
    # deliberately calls that implementation instead of copying its schema.
    freeze_contract._validate_canonical_layer(  # noqa: SLF001
        output,
        layer=layer,
        context=context,
    )
    return output


def _activation_source6_sha256(file_sha256: Mapping[str, str]) -> str:
    return canonical_mapping_sha256(
        {
            "schema": ACTIVATION_SOURCE6_SCHEMA,
            "files": [
                {"path": relative, "sha256": file_sha256[relative]}
                for relative in ACTIVATION_SOURCE_PATHS
            ],
        },
        decimals=8,
        exact_strings=True,
    )


def _activation_source_evidence(
    root: Path,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    reported = _evidence_mapping(report.get("candidate_source"), "candidate_source")
    _evidence_require(
        set(reported) == {"candidate_commit", "file_sha256"},
        "candidate_source keys drifted",
    )
    candidate_commit = reported.get("candidate_commit")
    _evidence_require(
        isinstance(candidate_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", candidate_commit) is not None,
        "candidate_source.candidate_commit must be 40-hex",
    )
    reported_files = _evidence_mapping(
        reported.get("file_sha256"), "candidate_source.file_sha256"
    )
    _evidence_require(
        tuple(reported_files) == ACTIVATION_SOURCE_PATHS,
        "candidate_source six-file path/order drifted",
    )
    actual: dict[str, str] = {}
    for relative in ACTIVATION_SOURCE_PATHS:
        path = root / relative
        _evidence_require(path.is_file(), f"source file missing: {relative}")
        actual[relative] = _sha256(path)
    _evidence_require(
        reported_files == actual,
        "candidate_source six-file SHA map differs from runner files",
    )
    aggregate = _activation_source6_sha256(actual)
    _evidence_require(
        aggregate == EXPECTED_ACTIVATION_SOURCE6_SHA256,
        "candidate_source six-file aggregate differs from reviewed candidate",
    )
    return {
        "schema": ACTIVATION_SOURCE6_SCHEMA,
        "candidate_commit": candidate_commit,
        "paths": list(ACTIVATION_SOURCE_PATHS),
        "file_sha256": actual,
        "sha256": aggregate,
    }


def _activation_reference_evidence(report: Mapping[str, Any]) -> dict[str, str]:
    golden = _evidence_mapping(report.get("golden"), "golden")
    reference = _evidence_mapping(golden.get("reference"), "golden.reference")
    _evidence_require(
        reference.get("profile") == "persisted-c6",
        "reference profile must be persisted-c6",
    )
    _evidence_require(
        reference.get("persisted_trust_root_verified") is True,
        "persisted c6 trust root was not verified",
    )
    _evidence_require(
        reference.get("same_machine_reference_only") is False,
        "same-machine reference cannot activate production",
    )
    files = _evidence_mapping(reference.get("files"), "golden.reference.files")
    blob_fields = {
        "top10_blob_sha1": "backtest_top10_latest.csv",
        "trade_selector_oos_blob_sha1": "backtest_trade_selector_oos_latest.csv",
        "backtest_blob_sha1": "backtest_latest.json",
        "model_meta_blob_sha1": "model_meta_latest.json",
    }
    for evidence_key, filename in blob_fields.items():
        entry = _evidence_mapping(files.get(filename), f"reference file {filename}")
        _evidence_require(
            entry.get("git_blob_sha1") == KNOWN_REFERENCE_EVIDENCE[evidence_key],
            f"reference blob drifted: {filename}",
        )
    return dict(KNOWN_REFERENCE_EVIDENCE)


def _behavior_manifest_projection(
    frame: pd.DataFrame,
    *,
    relative_path: str,
    discrete_columns: tuple[str, ...],
    score_columns: tuple[str, ...],
    context: str,
    expected_rows: int,
    expected_dates: int,
) -> dict[str, Any]:
    calculation_contract = {
        "identity_columns": list(FREEZE_IDENTITY_COLUMNS),
        "discrete_columns": list(discrete_columns),
        "score_columns": list(score_columns),
        "score_decimals": 8,
    }
    computed = compute_behavior_fingerprints(
        frame,
        calculation_contract,
        context=context,
    )
    _evidence_require(
        computed.get("identity_unique_nonempty") is True,
        f"{context} identity is not unique/nonempty",
    )
    _evidence_require(
        computed.get("rows") == expected_rows,
        f"{context} row count differs from reviewed baseline",
    )
    _evidence_require(
        computed.get("signal_dates") == expected_dates,
        f"{context} signal-date count differs from reviewed baseline",
    )
    return {
        "path": relative_path,
        "rows": computed["rows"],
        "signal_dates": computed["signal_dates"],
        "score_decimals": computed["score_decimals"],
        "identity_columns": list(FREEZE_IDENTITY_COLUMNS),
        "discrete_columns": list(discrete_columns),
        "score_columns": list(score_columns),
        "identity_sha256": computed["identity_sha256"],
        "date_counts_sha256": computed["date_counts_sha256"],
        "discrete_sha256": computed["discrete_sha256"],
        "scores_sha256": computed["scores_sha256"],
    }


def _strict_binary_sum(frame: pd.DataFrame, column: str, context: str) -> int:
    _evidence_require(column in frame, f"{context} missing {column}")
    return sum(
        freeze_contract._behavior_boolean(  # noqa: SLF001
            value,
            f"{context}.{column}[{row_number}]",
        )
        for row_number, value in enumerate(frame[column])
    )


def _behavior_activation_evidence(
    root: Path,
    *,
    top10: pd.DataFrame,
    oos: pd.DataFrame,
    backtest: Mapping[str, Any],
    action: Mapping[str, Any],
    expected_action_rows: int,
) -> dict[str, Any]:
    _evidence_require(
        type(expected_action_rows) is int and expected_action_rows > 0,
        "prediction observation-domain row count is invalid",
    )
    top10_projection = _behavior_manifest_projection(
        top10,
        relative_path=TOP10_EVIDENCE_PATH,
        discrete_columns=tuple(FREEZE_TOP10_DISCRETE_COLUMNS),
        score_columns=tuple(FREEZE_TOP10_SCORE_COLUMNS),
        context="activation.top10",
        expected_rows=freeze_contract.KNOWN_TOP10_ROWS,
        expected_dates=freeze_contract.KNOWN_TOP10_DATES,
    )
    oos_projection = _behavior_manifest_projection(
        oos,
        relative_path=OOS_EVIDENCE_PATH,
        discrete_columns=tuple(FREEZE_OOS_DISCRETE_COLUMNS),
        score_columns=tuple(FREEZE_OOS_SCORE_COLUMNS),
        context="activation.trade_selector_oos",
        expected_rows=freeze_contract.KNOWN_OOS_ROWS,
        expected_dates=freeze_contract.KNOWN_OOS_DATES,
    )
    action_contract = {"columns": list(ACTION_WATCHLIST_COLUMNS)}
    action_computed = compute_action_watchlist_fingerprint(action, action_contract)
    _evidence_require(
        action_computed["rows"] == expected_action_rows,
        "action watchlist rows differ from prediction observation domain",
    )
    expected_shadow_rows = min(
        freeze_contract.KNOWN_ACTION_SHADOW_ROWS,
        expected_action_rows,
    )
    _evidence_require(
        action_computed["shadow_only_rows"] == expected_shadow_rows,
        "action watchlist must preserve the available relative-best shadows",
    )
    action_projection = {
        "path": ACTION_EVIDENCE_PATH,
        "rows": action_computed["rows"],
        "columns": list(ACTION_WATCHLIST_COLUMNS),
        "sha256": action_computed["sha256"],
        "unique_codes": action_computed["unique_codes"],
        "shadow_only_rows": action_computed["shadow_only_rows"],
    }

    selector = _evidence_mapping(backtest.get("trade_selector"), "backtest.trade_selector")
    formal = _evidence_mapping(
        selector.get("formal_policy_oos"),
        "backtest.trade_selector.formal_policy_oos",
    )
    all_candidates = _evidence_mapping(
        formal.get("all_candidates"),
        "backtest.trade_selector.formal_policy_oos.all_candidates",
    )
    market_buyable = _evidence_mapping(
        formal.get("market_buyable_only"),
        "backtest.trade_selector.formal_policy_oos.market_buyable_only",
    )
    nested_oos = {
        "all_candidates_path": "trade_selector.formal_policy_oos.all_candidates",
        "signals": _evidence_strict_int(all_candidates.get("signals"), "nested signals"),
        "signal_dates": _evidence_strict_int(
            all_candidates.get("signal_dates"), "nested signal_dates"
        ),
        "filled_trades": _evidence_strict_int(
            all_candidates.get("filled_trades"), "nested filled_trades"
        ),
        "market_buyable_path": (
            "trade_selector.formal_policy_oos.market_buyable_only"
        ),
        "market_buyable_filled_trades": _evidence_strict_int(
            market_buyable.get("filled_trades"),
            "nested market_buyable filled_trades",
        ),
    }
    expected_nested = {
        "signals": freeze_contract.KNOWN_NESTED_OOS_SIGNALS,
        "signal_dates": freeze_contract.KNOWN_NESTED_OOS_SIGNAL_DATES,
        "filled_trades": freeze_contract.KNOWN_NESTED_OOS_FILLED_TRADES,
        "market_buyable_filled_trades": (
            freeze_contract.KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES
        ),
    }
    for key, expected in expected_nested.items():
        _evidence_require(nested_oos[key] == expected, f"nested OOS {key} drifted")

    action_status = action.get("status_code")
    _evidence_require(
        action_status == "NO_TRADE_MODEL_NOT_PROMOTED",
        "action status is not frozen NO_TRADE",
    )
    formal_buy_count = _evidence_strict_int(
        action.get("formal_buy_count"), "action formal_buy_count"
    )
    watchlist = action.get("stage_watchlist")
    _evidence_require(isinstance(watchlist, list), "action stage_watchlist must be a list")
    for row_number, row in enumerate(watchlist):
        _evidence_require(isinstance(row, Mapping), f"action row {row_number} invalid")
        weight = row.get("target_weight")
        _evidence_require(
            type(weight) in (int, float)
            and math.isfinite(float(weight))
            and float(weight) == 0.0,
            f"action target_weight[{row_number}] must be zero",
        )
    reason_values = sorted(set(top10["model_reason"].tolist()))
    _evidence_require(
        reason_values == ["selection_policy_not_ready"],
        "Top10 model_reason set drifted",
    )
    decision = {
        "status_code": action_status,
        "formal_buy_count": formal_buy_count,
        "top10_selected_count": _strict_binary_sum(
            top10, "selected", "activation.top10"
        ),
        "selector_globally_promoted_count": _strict_binary_sum(
            oos,
            "trade_selector_globally_promoted",
            "activation.trade_selector_oos",
        ),
        "nested_oos_trade_selected_count": _strict_binary_sum(
            oos, "trade_selected", "activation.trade_selector_oos"
        ),
        "nested_oos_trade_selector_promoted_count": _strict_binary_sum(
            oos,
            "trade_selector_promoted",
            "activation.trade_selector_oos",
        ),
        "production_backtest_signals": _evidence_strict_int(
            backtest.get("signals"), "backtest root signals"
        ),
        "production_backtest_signal_dates": _evidence_strict_int(
            backtest.get("signal_dates"), "backtest root signal_dates"
        ),
        "production_backtest_fills": _evidence_strict_int(
            backtest.get("filled_trades"), "backtest root filled_trades"
        ),
        "reason_values": reason_values,
    }
    expected_zero = (
        "formal_buy_count",
        "top10_selected_count",
        "selector_globally_promoted_count",
        "production_backtest_signals",
        "production_backtest_signal_dates",
        "production_backtest_fills",
    )
    for key in expected_zero:
        _evidence_require(decision[key] == 0, f"decision {key} is nonzero")
    _evidence_require(
        decision["nested_oos_trade_selected_count"]
        == freeze_contract.KNOWN_NESTED_OOS_TRADE_SELECTED,
        "nested OOS trade_selected count drifted",
    )
    _evidence_require(
        decision["nested_oos_trade_selector_promoted_count"]
        == freeze_contract.KNOWN_OOS_ROWS,
        "nested OOS selector promoted count drifted",
    )
    promoted = backtest.get("promoted")
    selector_promoted = selector.get("promoted")
    _evidence_require(type(promoted) is bool, "backtest promoted must be boolean")
    _evidence_require(
        type(selector_promoted) is bool,
        "backtest selector promoted must be boolean",
    )
    reject_rows = sum(
        1
        for row in watchlist
        if isinstance(row, Mapping) and row.get("action") == "REJECT"
    )
    _evidence_require(
        reject_rows + action_computed["shadow_only_rows"]
        == action_computed["rows"],
        "action watchlist contains an unexpected executable action",
    )
    persisted_counts = {
        "top10": {
            "rows": top10_projection["rows"],
            "signal_dates": top10_projection["signal_dates"],
            "observation_selected": _strict_binary_sum(
                top10, "observation_selected", "activation.top10"
            ),
            "shadow_selected": _strict_binary_sum(
                top10, "shadow_selected", "activation.top10"
            ),
            "risk_gate_pass": _strict_binary_sum(
                top10, "risk_gate_pass", "activation.top10"
            ),
            "selected": decision["top10_selected_count"],
        },
        "trade_selector_oos": {
            "rows": oos_projection["rows"],
            "signal_dates": oos_projection["signal_dates"],
            "trade_selected": decision["nested_oos_trade_selected_count"],
            "trade_shadow_selected": _strict_binary_sum(
                oos,
                "trade_shadow_selected",
                "activation.trade_selector_oos",
            ),
            "shadow_selected": _strict_binary_sum(
                oos, "shadow_selected", "activation.trade_selector_oos"
            ),
            "trade_selector_promoted": decision[
                "nested_oos_trade_selector_promoted_count"
            ],
            "trade_selector_globally_promoted": decision[
                "selector_globally_promoted_count"
            ],
            "trade_selector_policy_ready": _strict_binary_sum(
                oos,
                "trade_selector_policy_ready",
                "activation.trade_selector_oos",
            ),
        },
        "nested_oos_research": {
            key: nested_oos[key]
            for key in (
                "signals",
                "signal_dates",
                "filled_trades",
                "market_buyable_filled_trades",
            )
        },
        "production": {
            "promoted": promoted,
            "trade_selector_promoted": selector_promoted,
            "signals": decision["production_backtest_signals"],
            "signal_dates": decision["production_backtest_signal_dates"],
            "filled_trades": decision["production_backtest_fills"],
        },
        "action_watchlist": {
            "rows": action_projection["rows"],
            "shadow_only_rows": action_projection["shadow_only_rows"],
            "reject_rows": reject_rows,
            "formal_buy_count": decision["formal_buy_count"],
        },
    }
    frozen_counts = {
        key: persisted_counts[key] for key in EXPECTED_FROZEN_BEHAVIOR_COUNTS
    }
    _evidence_require(
        frozen_counts == EXPECTED_FROZEN_BEHAVIOR_COUNTS,
        "persisted C6 frozen behavior counts drifted",
    )
    return {
        "schema_version": BEHAVIOR_SCHEMA_VERSION,
        "canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
        "top10": top10_projection,
        "trade_selector_oos": oos_projection,
        "action_watchlist": action_projection,
        "reference_evidence": dict(KNOWN_REFERENCE_EVIDENCE),
        "nested_oos_research": nested_oos,
        "decision": decision,
        "persisted_counts": persisted_counts,
    }


def _runtime_surface_evidence(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    model_meta = _evidence_json(root, MODEL_META_EVIDENCE_PATH)
    backtest = _evidence_json(root, BACKTEST_EVIDENCE_PATH)
    action = _evidence_json(root, ACTION_EVIDENCE_PATH)
    prediction = _evidence_csv(root, PREDICTION_EVIDENCE_PATH)

    meta_model = _runtime_layer_projection(
        model_meta,
        layer="model",
        context="evidence.model_meta.model",
    )
    backtest_model = _runtime_layer_projection(
        backtest,
        layer="model",
        context="evidence.backtest.model",
    )
    _evidence_require(meta_model == backtest_model, "model meta/backtest layers differ")
    meta_selector_container = _evidence_mapping(
        model_meta.get("trade_selector"), "model_meta.trade_selector"
    )
    backtest_selector_container = _evidence_mapping(
        backtest.get("trade_selector"), "backtest.trade_selector"
    )
    meta_selector = _runtime_layer_projection(
        meta_selector_container,
        layer="trade_selector",
        context="evidence.model_meta.trade_selector",
    )
    backtest_selector = _runtime_layer_projection(
        backtest_selector_container,
        layer="trade_selector",
        context="evidence.backtest.trade_selector",
    )
    _evidence_require(
        meta_selector == backtest_selector,
        "selector meta/backtest layers differ",
    )

    action_model = _evidence_mapping(action.get("model"), "action_plan.model")
    action_model_layer = freeze_contract._action_layer_values(  # noqa: SLF001
        action_model,
        layer="model",
        expected=meta_model,
    )
    action_selector_layer = freeze_contract._action_layer_values(  # noqa: SLF001
        action_model,
        layer="trade_selector",
        expected=meta_selector,
    )
    _evidence_require(
        action_model_layer == meta_model,
        "action model V2 layer differs from meta/backtest",
    )
    _evidence_require(
        action_selector_layer == meta_selector,
        "action selector V2 layer differs from meta/backtest",
    )
    _evidence_require(
        action_model.get("v2_integrity_match") is True,
        "action v2_integrity_match is not true",
    )
    _evidence_require(
        action_model.get("v2_eligibility_match") is False,
        "action v2_eligibility_match must remain false while policies are not ready",
    )

    prediction_model = freeze_contract._prediction_layer_values(  # noqa: SLF001
        prediction,
        layer="model",
        expected=meta_model,
    )
    # Historical Top10/OOS rows retain their fold-specific policy values in
    # the behavior hashes.  Only the latest prediction is the final production
    # policy surface, so all nine executable version/ready/position/threshold
    # fields are checked here against the model fingerprint projection.
    freeze_contract._validate_model_policy_columns(  # noqa: SLF001
        prediction,
        meta_model,
        context="evidence.prediction.final_model_policy",
    )
    selector_v1_artifact = backtest_selector_container.get(
        "production_artifact_sha256"
    )
    selector_version = backtest_selector_container.get("version")
    _evidence_require(
        isinstance(selector_v1_artifact, str)
        and re.fullmatch(r"[0-9a-f]{64}", selector_v1_artifact) is not None,
        "selector V1 same-run artifact is invalid",
    )
    _evidence_require(
        isinstance(selector_version, str) and selector_version != "",
        "selector version is invalid",
    )
    prediction_selector, prediction_domain = (
        freeze_contract._prediction_selector_domain_values(  # noqa: SLF001
            prediction,
            expected=meta_selector,
            expected_runtime_v1_artifact_sha256=selector_v1_artifact,
            expected_selector_version=selector_version,
        )
    )
    fill_relationships = freeze_contract._validate_prediction_fill_relationships(  # noqa: SLF001
        prediction
    )
    return (
        {
            "schema_version": CANONICAL_RUNTIME_SCHEMA_VERSION,
            "model": meta_model,
            "trade_selector": meta_selector,
            "surface_consistency": {
                "model_meta_backtest_exact": True,
                "selector_meta_backtest_exact": True,
                "action_model_exact": True,
                "action_selector_exact": True,
                "prediction_model": prediction_model,
                "prediction_trade_selector": prediction_selector,
                "prediction_trade_selector_domain": prediction_domain,
                "prediction_fill_relationships": fill_relationships,
            },
        },
        {"backtest": backtest, "action": action, "prediction": prediction},
    )


def _canonical_precision_evidence(report: Mapping[str, Any]) -> dict[str, Any]:
    golden = _evidence_mapping(report.get("golden"), "golden")
    canonical_scores = _evidence_mapping(
        golden.get("canonical_scores"), "golden.canonical_scores"
    )
    output: dict[str, Any] = {}
    for precision in ("6", "8", "10", "12"):
        top10 = _evidence_mapping(
            _evidence_mapping(canonical_scores.get("top10"), "canonical top10").get(
                precision
            ),
            f"canonical top10 q{precision}",
        )
        selector = _evidence_mapping(
            _evidence_mapping(
                canonical_scores.get("selector_oos"), "canonical selector OOS"
            ).get(precision),
            f"canonical selector OOS q{precision}",
        )
        expected_gate = "hard" if precision == "8" else "audit_only"
        _evidence_require(top10.get("gate") == expected_gate, f"Top10 q{precision} gate drift")
        _evidence_require(
            selector.get("gate") == expected_gate,
            f"selector OOS q{precision} gate drift",
        )
        for surface_name, surface in (("Top10", top10), ("selector OOS", selector)):
            _evidence_require(
                type(surface.get("equal")) is bool,
                f"{surface_name} q{precision} equal must be boolean",
            )
            for hash_name in ("reference_sha256", "candidate_sha256"):
                hash_value = surface.get(hash_name)
                _evidence_require(
                    isinstance(hash_value, str)
                    and re.fullmatch(r"[0-9a-f]{64}", hash_value) is not None,
                    f"{surface_name} q{precision} {hash_name} must be 64-hex",
                )
        if precision == "8":
            _evidence_require(top10.get("equal") is True, "Top10 q8 hard gate failed")
            _evidence_require(
                selector.get("equal") is True,
                "selector OOS q8 hard gate failed",
            )
        output[precision] = {
            "gate": expected_gate,
            "top10_equal": top10.get("equal"),
            "top10_reference_sha256": top10.get("reference_sha256"),
            "top10_candidate_sha256": top10.get("candidate_sha256"),
            "selector_oos_equal": selector.get("equal"),
            "selector_oos_reference_sha256": selector.get("reference_sha256"),
            "selector_oos_candidate_sha256": selector.get("candidate_sha256"),
        }
    return output


def _ci_activation_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    candidate_sha = os.environ.get("GITHUB_SHA", "")
    _evidence_require(
        isinstance(source.get("candidate_commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", source["candidate_commit"]) is not None,
        "candidate_source.candidate_commit must be 40-hex",
    )
    _evidence_require(
        re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is not None
        and candidate_sha == source["candidate_commit"],
        "GITHUB_SHA differs from candidate_source.candidate_commit",
    )
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    _evidence_require(
        os.environ.get("GITHUB_ACTIONS") == "true",
        "activation evidence must be generated by GitHub Actions",
    )
    _evidence_require(
        re.fullmatch(r"[1-9][0-9]*", run_id) is not None,
        "GITHUB_RUN_ID must be canonical positive ASCII digits",
    )
    _evidence_require(
        run_attempt == "1",
        "GITHUB_RUN_ATTEMPT must be 1 for fresh activation evidence",
    )
    _evidence_require(
        os.environ.get("RUNNER_OS") == "Linux",
        "activation evidence must be generated on the Linux runner",
    )
    _evidence_require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "activation evidence event must be workflow_dispatch",
    )
    _evidence_require(
        os.environ.get("GITHUB_REPOSITORY") == "njedu2023-prog/DC20"
        and os.environ.get("GITHUB_REF") == "refs/heads/main",
        "activation evidence repository/ref drifted",
    )
    return {
        "github_actions": True,
        "candidate_sha": candidate_sha,
        "github_run_id": run_id,
        "github_run_attempt": run_attempt,
        "runner_os": "Linux",
        "event_name": "workflow_dispatch",
        "repository": "njedu2023-prog/DC20",
        "ref": "refs/heads/main",
    }


def _history_activation_evidence(report: Mapping[str, Any]) -> dict[str, Any]:
    history = _evidence_mapping(report.get("history"), "history")
    schema = history.get("manifest_schema_version")
    active = history.get("manifest_active_on_disk")
    _evidence_require(type(active) is bool, "manifest active state must be boolean")
    _evidence_require(
        history.get("active") is active and history.get("manifest_active") is active,
        "history active-state surfaces differ",
    )
    _evidence_require(
        history.get("sha256") == freeze_contract.KNOWN_HISTORY_SHA256,
        "history snapshot SHA drifted",
    )
    _evidence_require(
        history.get("rows") == freeze_contract.KNOWN_HISTORY_ROWS,
        "history snapshot row count drifted",
    )
    _evidence_require(
        history.get("path") == freeze_contract.KNOWN_HISTORY_PATH,
        "history snapshot path drifted",
    )
    for field, expected in EXPECTED_HISTORY_EVIDENCE.items():
        _evidence_require(
            history.get(field) == expected,
            f"history {field} drifted",
        )
    _evidence_require(
        history.get("bootstrap_mode") is False,
        "history bootstrap mode must remain false",
    )
    _evidence_require(
        history.get("forced_frozen_replay") is True
        and history.get("manifest_mutated_on_disk") is False
        and history.get("live_history_fallback") is False,
        "forced replay isolation contract drifted",
    )
    manifest_content_sha256 = history.get("manifest_content_sha256")
    _evidence_require(
        isinstance(manifest_content_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_content_sha256) is not None,
        "freeze manifest content SHA is invalid",
    )

    pin_projection: dict[str, Any] | None = None
    if schema == "decision_model_freeze_v1":
        _evidence_require(active is False, "legacy V1 diagnostic manifest must be inactive")
        _evidence_require(
            history.get("source") == "legacy_v1_exact_diagnostic_bootstrap"
            and history.get("loader_contract")
            == "one_time_exact_v1_no_live_fallback"
            and manifest_content_sha256 == LEGACY_DIAGNOSTIC_MANIFEST_SHA256,
            "legacy V1 exact diagnostic loader contract drifted",
        )
    elif schema == "decision_model_freeze_v2":
        _evidence_require(
            history.get("source") == "forced_frozen_snapshot"
            and history.get("loader_contract")
            == "v2_complete_contract_and_pins_no_live_fallback",
            "V2 verified diagnostic loader contract drifted",
        )
        pins = _evidence_mapping(history.get("pinned_files"), "history.pinned_files")
        _evidence_require(
            pins.get("active") is active
            and pins.get("validated") is True
            and pins.get("enforced") is True
            and pins.get("forced_enforcement") is (not active),
            "V2 pinned-file enforcement state drifted",
        )
        _evidence_require(
            type(pins.get("pinned_files")) is int and pins["pinned_files"] > 0,
            "V2 pinned-file count is invalid",
        )
        pin_projection = {
            "count": pins["pinned_files"],
            "validated": True,
            "enforced": True,
            "forced_enforcement": pins["forced_enforcement"],
        }
    else:
        raise RuntimeError("activation evidence: unsupported freeze manifest schema")

    return {
        "manifest_schema_version": schema,
        "manifest_active_on_disk": active,
        "manifest_content_sha256": manifest_content_sha256,
        "freeze_id": history.get("freeze_id"),
        "training_cutoff_signal_date": history.get(
            "training_cutoff_signal_date"
        ),
        "rows": history.get("rows"),
        "signal_dates": history.get("signal_dates"),
        "columns": history.get("columns"),
        "columns_sha256": history.get("columns_sha256"),
        "history_start": history.get("history_start"),
        "history_end": history.get("history_end"),
        "source": history.get("source"),
        "loader_contract": history.get("loader_contract"),
        "path": history.get("path"),
        "sha256": history.get("sha256"),
        "forced_frozen_replay": True,
        "manifest_mutated_on_disk": False,
        "live_history_fallback": False,
        "pinned_files": pin_projection,
    }


def _build_activation_evidence(
    report: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    _evidence_require(report.get("status") == "pass", "replay report did not pass")
    golden = _evidence_mapping(report.get("golden"), "golden")
    _evidence_require(golden.get("status") == "pass", "golden comparison did not pass")
    source = _activation_source_evidence(root, report)
    reference = _activation_reference_evidence(report)
    canonical, runtime = _runtime_surface_evidence(root)
    prediction_domain = canonical["surface_consistency"][
        "prediction_trade_selector_domain"
    ]
    expected_action_rows = _evidence_strict_int(
        prediction_domain.get("observation_domain_rows"),
        "prediction observation-domain rows",
    )
    top10 = _evidence_csv(root, TOP10_EVIDENCE_PATH)
    oos = _evidence_csv(root, OOS_EVIDENCE_PATH)
    behavior = _behavior_activation_evidence(
        root,
        top10=top10,
        oos=oos,
        backtest=runtime["backtest"],
        action=runtime["action"],
        expected_action_rows=expected_action_rows,
    )
    _evidence_require(
        behavior["reference_evidence"] == reference,
        "behavior reference evidence differs from replay trust root",
    )
    history_projection = _history_activation_evidence(report)
    return {
        "schema_version": ACTIVATION_EVIDENCE_SCHEMA,
        "system": "DC2.0",
        "read_only": True,
        "ci": _ci_activation_evidence(source),
        "candidate_source": source,
        "history_snapshot": history_projection,
        "reference_evidence": reference,
        "canonical_v2": canonical,
        "behavior_contract": behavior,
        "canonical_precision": _canonical_precision_evidence(report),
    }


def _render_compact_activation_evidence(evidence: Mapping[str, Any]) -> str:
    def string_values(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for nested in value.values():
                yield from string_values(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                yield from string_values(nested)

    for value in string_values(evidence):
        lowered = value.lower()
        _evidence_require(
            "\\" not in value,
            "rooted or escaped Windows path is forbidden in compact evidence",
        )
        _evidence_require(
            "//" not in value,
            "double-root or URL path is forbidden in compact evidence",
        )
        _evidence_require(
            not any(ord(character) < 32 for character in value),
            "control characters are forbidden in compact evidence",
        )
        _evidence_require(
            re.search(r"(?:^|[^a-z0-9._/])/(?!/)[^\s]*", lowered) is None,
            "absolute POSIX path is forbidden in compact evidence",
        )
        _evidence_require(
            re.search(r"[a-z]:[\\\\/]", lowered) is None,
            "absolute Windows path is forbidden in compact evidence",
        )
        _evidence_require(
            re.search(r"\b[0-9]{6}\.(?:sh|sz|bj)\b", lowered) is None,
            "stock-level records are forbidden in compact evidence",
        )
    rendered = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    size = len(rendered.encode("utf-8"))
    _evidence_require(size <= ACTIVATION_EVIDENCE_MAX_BYTES, "compact JSON is too large")
    lower = rendered.lower()
    for forbidden in (
        "tushare",
        "api_key",
        "secret",
        "authorization",
        "token",
        "ghp_",
        "github_pat_",
        "bearer ",
    ):
        _evidence_require(forbidden not in lower, f"forbidden sensitive key {forbidden!r}")
    return rendered


def _public_exact_keys(
    value: Any,
    expected: set[str] | frozenset[str],
    context: str,
) -> dict[str, Any]:
    mapping = _evidence_mapping(value, context)
    _evidence_require(
        set(mapping) == set(expected),
        f"{context} public allowlist keys drifted",
    )
    return mapping


def _public_sha256(value: Any, context: str) -> str:
    _evidence_require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{context} must be 64-hex",
    )
    return value


def _public_git_sha(value: Any, context: str) -> str:
    _evidence_require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
        f"{context} must be 40-hex",
    )
    return value


def _public_int(value: Any, context: str, *, expected: int | None = None) -> int:
    _evidence_require(type(value) is int, f"{context} must be a JSON integer")
    if expected is not None:
        _evidence_require(value == expected, f"{context} drifted")
    return value


def _public_bool(value: Any, context: str, *, expected: bool | None = None) -> bool:
    _evidence_require(type(value) is bool, f"{context} must be a JSON boolean")
    if expected is not None:
        _evidence_require(value is expected, f"{context} drifted")
    return value


def _public_exact_json(value: Any, expected: Any, context: str) -> None:
    """Compare JSON values without Python's bool/int/float aliasing."""

    _evidence_require(type(value) is type(expected), f"{context} JSON type drifted")
    if isinstance(expected, dict):
        _evidence_require(set(value) == set(expected), f"{context} keys drifted")
        for key in expected:
            _public_exact_json(value[key], expected[key], f"{context}.{key}")
    elif isinstance(expected, list):
        _evidence_require(len(value) == len(expected), f"{context} length drifted")
        for index, (actual_item, expected_item) in enumerate(zip(value, expected)):
            _public_exact_json(
                actual_item,
                expected_item,
                f"{context}[{index}]",
            )
    elif isinstance(expected, float):
        _evidence_require(
            math.isfinite(value) and value == expected,
            f"{context} numeric value drifted",
        )
    else:
        _evidence_require(value == expected, f"{context} value drifted")


def _validate_public_activation_evidence_shape(evidence: Mapping[str, Any]) -> None:
    """Fail closed unless the public payload matches the reviewed recursive shape."""

    top = _public_exact_keys(
        evidence,
        {
            "schema_version",
            "system",
            "read_only",
            "ci",
            "candidate_source",
            "history_snapshot",
            "reference_evidence",
            "canonical_v2",
            "behavior_contract",
            "canonical_precision",
        },
        "public evidence",
    )
    _evidence_require(
        top["schema_version"] == ACTIVATION_EVIDENCE_SCHEMA
        and top["system"] == "DC2.0"
        and top["read_only"] is True,
        "public evidence identity drifted",
    )
    ci = _public_exact_keys(
        top["ci"],
        {
            "github_actions",
            "candidate_sha",
            "github_run_id",
            "github_run_attempt",
            "runner_os",
            "event_name",
            "repository",
            "ref",
        },
        "public evidence.ci",
    )
    source = _public_exact_keys(
        top["candidate_source"],
        {"schema", "candidate_commit", "paths", "file_sha256", "sha256"},
        "public evidence.candidate_source",
    )
    _evidence_require(
        source["schema"] == ACTIVATION_SOURCE6_SCHEMA
        and source["paths"] == list(ACTIVATION_SOURCE_PATHS),
        "public evidence source path list drifted",
    )
    _public_git_sha(source["candidate_commit"], "public evidence candidate commit")
    source_files = _public_exact_keys(
        source["file_sha256"],
        set(ACTIVATION_SOURCE_PATHS),
        "public evidence.candidate_source.file_sha256",
    )
    for path in ACTIVATION_SOURCE_PATHS:
        _public_sha256(source_files[path], f"public evidence source {path}")
    _evidence_require(
        _public_sha256(source["sha256"], "public evidence source aggregate")
        == EXPECTED_ACTIVATION_SOURCE6_SHA256
        == _activation_source6_sha256(source_files),
        "public evidence source aggregate drifted",
    )
    _public_bool(ci["github_actions"], "public evidence github_actions", expected=True)
    _evidence_require(
        _public_git_sha(ci["candidate_sha"], "public evidence candidate SHA")
        == source["candidate_commit"]
        and isinstance(ci["github_run_id"], str)
        and re.fullmatch(r"[1-9][0-9]*", ci["github_run_id"]) is not None
        and ci["github_run_attempt"] == "1"
        and ci["runner_os"] == "Linux"
        and ci["event_name"] == "workflow_dispatch"
        and ci["repository"] == "njedu2023-prog/DC20"
        and ci["ref"] == "refs/heads/main",
        "public evidence CI values drifted",
    )
    history = _public_exact_keys(
        top["history_snapshot"],
        {
            "manifest_schema_version",
            "manifest_active_on_disk",
            "manifest_content_sha256",
            "freeze_id",
            "training_cutoff_signal_date",
            "rows",
            "signal_dates",
            "columns",
            "columns_sha256",
            "history_start",
            "history_end",
            "source",
            "loader_contract",
            "path",
            "sha256",
            "forced_frozen_replay",
            "manifest_mutated_on_disk",
            "live_history_fallback",
            "pinned_files",
        },
        "public evidence.history_snapshot",
    )
    if history["pinned_files"] is not None:
        pins = _public_exact_keys(
            history["pinned_files"],
            {"count", "validated", "enforced", "forced_enforcement"},
            "public evidence.history_snapshot.pinned_files",
        )
        _public_int(
            pins["count"],
            "public evidence pinned file count",
            expected=len(freeze_contract.REQUIRED_ACTIVE_PIN_PATHS),
        )
        _public_bool(
            pins["validated"], "public evidence pinned_files.validated", expected=True
        )
        _public_bool(
            pins["enforced"], "public evidence pinned_files.enforced", expected=True
        )
        _public_bool(
            pins["forced_enforcement"],
            "public evidence pinned_files.forced_enforcement",
            expected=not history["manifest_active_on_disk"],
        )
    _public_bool(
        history["manifest_active_on_disk"],
        "public evidence manifest active state",
    )
    _public_sha256(
        history["manifest_content_sha256"],
        "public evidence manifest content",
    )
    _evidence_require(
        history["freeze_id"] == EXPECTED_HISTORY_EVIDENCE["freeze_id"]
        and history["training_cutoff_signal_date"]
        == EXPECTED_HISTORY_EVIDENCE["training_cutoff_signal_date"]
        and history["path"] == freeze_contract.KNOWN_HISTORY_PATH
        and history["sha256"] == freeze_contract.KNOWN_HISTORY_SHA256,
        "public evidence history identity drifted",
    )
    _public_int(
        history["rows"],
        "public evidence history rows",
        expected=freeze_contract.KNOWN_HISTORY_ROWS,
    )
    for key in ("signal_dates", "columns"):
        _public_int(
            history[key],
            f"public evidence history {key}",
            expected=EXPECTED_HISTORY_EVIDENCE[key],
        )
    _evidence_require(
        _public_sha256(
            history["columns_sha256"], "public evidence history columns"
        )
        == EXPECTED_HISTORY_EVIDENCE["columns_sha256"],
        "public evidence history columns hash drifted",
    )
    for key in ("history_start", "history_end"):
        _evidence_require(
            history[key] == EXPECTED_HISTORY_EVIDENCE[key],
            f"public evidence history {key} drifted",
        )
    for key, expected in (
        ("forced_frozen_replay", True),
        ("manifest_mutated_on_disk", False),
        ("live_history_fallback", False),
    ):
        _public_bool(history[key], f"public evidence history {key}", expected=expected)
    if history["manifest_schema_version"] == "decision_model_freeze_v1":
        _evidence_require(
            history["manifest_active_on_disk"] is False
            and history["manifest_content_sha256"]
            == LEGACY_DIAGNOSTIC_MANIFEST_SHA256
            and history["source"] == "legacy_v1_exact_diagnostic_bootstrap"
            and history["loader_contract"] == "one_time_exact_v1_no_live_fallback"
            and history["pinned_files"] is None,
            "public legacy history contract drifted",
        )
    elif history["manifest_schema_version"] == "decision_model_freeze_v2":
        _evidence_require(
            history["source"] == "forced_frozen_snapshot"
            and history["loader_contract"]
            == "v2_complete_contract_and_pins_no_live_fallback"
            and isinstance(history["pinned_files"], Mapping),
            "public V2 history contract drifted",
        )
    else:
        raise RuntimeError("activation evidence: public history schema drifted")
    reference_keys = set(KNOWN_REFERENCE_EVIDENCE)
    public_reference = _public_exact_keys(
        top["reference_evidence"],
        reference_keys,
        "public evidence.reference_evidence",
    )
    _evidence_require(
        public_reference == dict(KNOWN_REFERENCE_EVIDENCE),
        "public reference evidence drifted",
    )
    canonical = _public_exact_keys(
        top["canonical_v2"],
        {"schema_version", "model", "trade_selector", "surface_consistency"},
        "public evidence.canonical_v2",
    )
    freeze_contract._validate_canonical_layer(  # noqa: SLF001
        canonical["model"], layer="model", context="public evidence canonical model"
    )
    _evidence_require(
        canonical["schema_version"] == CANONICAL_RUNTIME_SCHEMA_VERSION,
        "public canonical runtime schema drifted",
    )
    freeze_contract._validate_canonical_layer(  # noqa: SLF001
        canonical["trade_selector"],
        layer="trade_selector",
        context="public evidence canonical selector",
    )
    surfaces = _public_exact_keys(
        canonical["surface_consistency"],
        {
            "model_meta_backtest_exact",
            "selector_meta_backtest_exact",
            "action_model_exact",
            "action_selector_exact",
            "prediction_model",
            "prediction_trade_selector",
            "prediction_trade_selector_domain",
            "prediction_fill_relationships",
        },
        "public evidence.canonical_v2.surface_consistency",
    )
    prediction_projection_keys = {
        "canonical_v2_version",
        "artifact_v2_sha256",
        "canonical_schema",
        "canonical_decimals",
        "execution_numeric_mode",
        "raw_execution_preserved",
    }
    for key, layer_key in (
        ("prediction_model", "model"),
        ("prediction_trade_selector", "trade_selector"),
    ):
        projection = _public_exact_keys(
            surfaces[key],
            prediction_projection_keys,
            f"public evidence.canonical_v2.surface_consistency.{key}",
        )
        layer = canonical[layer_key]
        contract = layer["canonical_contract"]
        _public_exact_json(
            projection,
            {
                "canonical_v2_version": layer["canonical_v2_version"],
                "artifact_v2_sha256": layer["artifact_v2_sha256"],
                "canonical_schema": contract["schema"],
                "canonical_decimals": contract["decimals"],
                "execution_numeric_mode": contract["execution_mode"],
                "raw_execution_preserved": contract["raw_execution_preserved"],
            },
            f"public evidence {key}",
        )
    for key in (
        "model_meta_backtest_exact",
        "selector_meta_backtest_exact",
        "action_model_exact",
        "action_selector_exact",
    ):
        _public_bool(surfaces[key], f"public evidence surface {key}", expected=True)
    domain = _public_exact_keys(
        surfaces["prediction_trade_selector_domain"],
        {
            "observation_domain_rows",
            "outside_domain_rows",
            "global_selector_v2_declarations_match",
            "domain_v2_artifact_manifest_match",
            "domain_v1_artifact_same_run_match",
            "domain_v1_artifact_sha256",
            "outside_selector_artifacts_empty",
            "outside_trade_semantics_valid",
            "formal_trade_selected_count",
            "trade_selector_promoted_count",
            "shadow_selected_count",
        },
        "public evidence selector domain",
    )
    observation_domain_rows = _public_int(
        domain["observation_domain_rows"],
        "public evidence selector domain observation rows",
    )
    outside_domain_rows = _public_int(
        domain["outside_domain_rows"],
        "public evidence selector domain outside rows",
    )
    _evidence_require(
        observation_domain_rows > 0 and outside_domain_rows >= 0,
        "public selector domain row counts are invalid",
    )
    expected_domain = {
        "global_selector_v2_declarations_match": True,
        "domain_v2_artifact_manifest_match": True,
        "domain_v1_artifact_same_run_match": True,
        "outside_selector_artifacts_empty": True,
        "outside_trade_semantics_valid": True,
        "formal_trade_selected_count": 0,
        "trade_selector_promoted_count": 0,
        "shadow_selected_count": min(
            freeze_contract.KNOWN_ACTION_SHADOW_ROWS,
            observation_domain_rows,
        ),
    }
    for key, expected in expected_domain.items():
        if type(expected) is bool:
            _public_bool(domain[key], f"public evidence selector domain {key}", expected=expected)
        else:
            _public_int(domain[key], f"public evidence selector domain {key}", expected=expected)
    _public_sha256(
        domain["domain_v1_artifact_sha256"],
        "public evidence selector domain V1 artifact",
    )
    fill = _public_exact_keys(
        surfaces["prediction_fill_relationships"],
        {
            "rows",
            "public_fill_equals_fill",
            "trade_public_fill_equals_trade_fill",
            "trade_fill_observation_domain_rows",
            "trade_fill_outside_domain_rows",
            "actual_fill_available_rows",
            "actual_fill_missing_rows",
        },
        "public evidence fill relationships",
    )
    for key in ("public_fill_equals_fill", "trade_public_fill_equals_trade_fill"):
        _public_bool(fill[key], f"public evidence fill {key}", expected=True)
    fill_rows = _public_int(fill["rows"], "public evidence fill rows")
    fill_observation_rows = _public_int(
        fill["trade_fill_observation_domain_rows"],
        "public evidence fill observation rows",
    )
    fill_outside_rows = _public_int(
        fill["trade_fill_outside_domain_rows"],
        "public evidence fill outside rows",
    )
    actual_fill_available_rows = _public_int(
        fill["actual_fill_available_rows"],
        "public evidence available fill rows",
    )
    actual_fill_missing_rows = _public_int(
        fill["actual_fill_missing_rows"],
        "public evidence missing fill rows",
    )
    _evidence_require(
        fill_rows == observation_domain_rows + outside_domain_rows
        and fill_observation_rows == observation_domain_rows
        and fill_outside_rows == outside_domain_rows,
        "public prediction/fill domain partition drifted",
    )
    _evidence_require(
        actual_fill_available_rows >= 0
        and actual_fill_missing_rows >= 0
        and actual_fill_available_rows + actual_fill_missing_rows == fill_rows,
        "public available/missing fill partition drifted",
    )
    behavior = _public_exact_keys(
        top["behavior_contract"],
        {
            "schema_version",
            "canonical_schema",
            "top10",
            "trade_selector_oos",
            "action_watchlist",
            "reference_evidence",
            "nested_oos_research",
            "decision",
            "persisted_counts",
        },
        "public evidence.behavior_contract",
    )
    _evidence_require(
        behavior["schema_version"] == BEHAVIOR_SCHEMA_VERSION
        and behavior["canonical_schema"] == CANONICAL_FINGERPRINT_SCHEMA,
        "public behavior schema drifted",
    )
    ledger_keys = {
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
    ledger_specs = {
        "top10": (
            TOP10_EVIDENCE_PATH,
            freeze_contract.KNOWN_TOP10_ROWS,
            freeze_contract.KNOWN_TOP10_DATES,
            list(FREEZE_TOP10_DISCRETE_COLUMNS),
            list(FREEZE_TOP10_SCORE_COLUMNS),
        ),
        "trade_selector_oos": (
            OOS_EVIDENCE_PATH,
            freeze_contract.KNOWN_OOS_ROWS,
            freeze_contract.KNOWN_OOS_DATES,
            list(FREEZE_OOS_DISCRETE_COLUMNS),
            list(FREEZE_OOS_SCORE_COLUMNS),
        ),
    }
    for key, (path, rows, dates, discrete_columns, score_columns) in ledger_specs.items():
        ledger = _public_exact_keys(
            behavior[key], ledger_keys, f"public evidence.behavior_contract.{key}"
        )
        _public_exact_json(
            {
                "path": ledger["path"],
                "rows": ledger["rows"],
                "signal_dates": ledger["signal_dates"],
                "score_decimals": ledger["score_decimals"],
                "identity_columns": ledger["identity_columns"],
                "discrete_columns": ledger["discrete_columns"],
                "score_columns": ledger["score_columns"],
            },
            {
                "path": path,
                "rows": rows,
                "signal_dates": dates,
                "score_decimals": 8,
                "identity_columns": list(FREEZE_IDENTITY_COLUMNS),
                "discrete_columns": discrete_columns,
                "score_columns": score_columns,
            },
            f"public evidence.behavior_contract.{key} contract",
        )
        for hash_key in (
            "identity_sha256",
            "date_counts_sha256",
            "discrete_sha256",
            "scores_sha256",
        ):
            _public_sha256(
                ledger[hash_key],
                f"public evidence.behavior_contract.{key}.{hash_key}",
            )
    action = _public_exact_keys(
        behavior["action_watchlist"],
        {"path", "rows", "columns", "sha256", "unique_codes", "shadow_only_rows"},
        "public evidence.behavior_contract.action_watchlist",
    )
    _public_exact_json(
        {key: action[key] for key in ("path", "columns", "unique_codes")},
        {
            "path": ACTION_EVIDENCE_PATH,
            "columns": list(ACTION_WATCHLIST_COLUMNS),
            "unique_codes": True,
        },
        "public evidence action watchlist contract",
    )
    action_rows = _public_int(
        action["rows"], "public evidence action watchlist rows"
    )
    action_shadow_rows = _public_int(
        action["shadow_only_rows"],
        "public evidence action watchlist shadow rows",
    )
    _evidence_require(
        action_rows == observation_domain_rows
        and action_shadow_rows == domain["shadow_selected_count"],
        "public action watchlist differs from prediction observation domain",
    )
    _public_sha256(action["sha256"], "public evidence action watchlist hash")
    behavior_reference = _public_exact_keys(
        behavior["reference_evidence"],
        reference_keys,
        "public evidence.behavior_contract.reference_evidence",
    )
    _evidence_require(
        behavior_reference == dict(KNOWN_REFERENCE_EVIDENCE),
        "public behavior reference evidence drifted",
    )
    nested = _public_exact_keys(
        behavior["nested_oos_research"],
        {
            "all_candidates_path",
            "signals",
            "signal_dates",
            "filled_trades",
            "market_buyable_path",
            "market_buyable_filled_trades",
        },
        "public evidence.behavior_contract.nested_oos_research",
    )
    _public_exact_json(
        nested,
        {
            "all_candidates_path": "trade_selector.formal_policy_oos.all_candidates",
            "signals": freeze_contract.KNOWN_NESTED_OOS_SIGNALS,
            "signal_dates": freeze_contract.KNOWN_NESTED_OOS_SIGNAL_DATES,
            "filled_trades": freeze_contract.KNOWN_NESTED_OOS_FILLED_TRADES,
            "market_buyable_path": "trade_selector.formal_policy_oos.market_buyable_only",
            "market_buyable_filled_trades": (
                freeze_contract.KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES
            ),
        },
        "public nested OOS evidence",
    )
    decision = _public_exact_keys(
        behavior["decision"],
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
        },
        "public evidence.behavior_contract.decision",
    )
    _public_exact_json(
        decision,
        {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "top10_selected_count": 0,
            "selector_globally_promoted_count": 0,
            "nested_oos_trade_selected_count": (
                freeze_contract.KNOWN_NESTED_OOS_TRADE_SELECTED
            ),
            "nested_oos_trade_selector_promoted_count": freeze_contract.KNOWN_OOS_ROWS,
            "production_backtest_signals": 0,
            "production_backtest_signal_dates": 0,
            "production_backtest_fills": 0,
            "reason_values": ["selection_policy_not_ready"],
        },
        "public decision evidence",
    )
    counts = _public_exact_keys(
        behavior["persisted_counts"],
        set(EXPECTED_FROZEN_BEHAVIOR_COUNTS) | {"action_watchlist"},
        "public evidence.behavior_contract.persisted_counts",
    )
    for key, expected in EXPECTED_FROZEN_BEHAVIOR_COUNTS.items():
        _public_exact_keys(
            counts[key],
            set(expected),
            f"public evidence.behavior_contract.persisted_counts.{key}",
        )
    _public_exact_json(
        {key: counts[key] for key in EXPECTED_FROZEN_BEHAVIOR_COUNTS},
        EXPECTED_FROZEN_BEHAVIOR_COUNTS,
        "public persisted frozen behavior counts",
    )
    action_counts = _public_exact_keys(
        counts["action_watchlist"],
        {"rows", "shadow_only_rows", "reject_rows", "formal_buy_count"},
        "public evidence.behavior_contract.persisted_counts.action_watchlist",
    )
    for key in action_counts:
        _public_int(action_counts[key], f"public persisted action {key}")
    _evidence_require(
        action_counts["rows"] == action_rows
        and action_counts["shadow_only_rows"] == action_shadow_rows
        and action_counts["reject_rows"] == action_rows - action_shadow_rows
        and action_counts["formal_buy_count"] == decision["formal_buy_count"] == 0,
        "public persisted action counts drifted",
    )
    precision = _public_exact_keys(
        top["canonical_precision"],
        {"6", "8", "10", "12"},
        "public evidence.canonical_precision",
    )
    precision_keys = {
        "gate",
        "top10_equal",
        "top10_reference_sha256",
        "top10_candidate_sha256",
        "selector_oos_equal",
        "selector_oos_reference_sha256",
        "selector_oos_candidate_sha256",
    }
    for key in ("6", "8", "10", "12"):
        entry = _public_exact_keys(
            precision[key],
            precision_keys,
            f"public evidence.canonical_precision.{key}",
        )
        expected_gate = "hard" if key == "8" else "audit_only"
        _evidence_require(
            entry["gate"] == expected_gate,
            f"public canonical precision q{key} gate drifted",
        )
        for bool_key in ("top10_equal", "selector_oos_equal"):
            _public_bool(entry[bool_key], f"public q{key} {bool_key}")
        if key == "8":
            _evidence_require(
                entry["top10_equal"] is True
                and entry["selector_oos_equal"] is True,
                "public q8 hard equality failed",
            )
        for hash_key in (
            "top10_reference_sha256",
            "top10_candidate_sha256",
            "selector_oos_reference_sha256",
            "selector_oos_candidate_sha256",
        ):
            _public_sha256(entry[hash_key], f"public q{key} {hash_key}")
        _evidence_require(
            entry["top10_equal"]
            is (
                entry["top10_reference_sha256"]
                == entry["top10_candidate_sha256"]
            )
            and entry["selector_oos_equal"]
            is (
                entry["selector_oos_reference_sha256"]
                == entry["selector_oos_candidate_sha256"]
            ),
            f"public q{key} equality/hash relationship drifted",
        )


def _activation_evidence_contract_self_test() -> dict[str, Any]:
    def frame_for(
        discrete_columns: tuple[str, ...],
        score_columns: tuple[str, ...],
    ) -> pd.DataFrame:
        row: dict[str, Any] = {
            "signal_date": "20260805",
            "ts_code": "000001.SZ",
        }
        exact_values = {
            "stage": "2→3",
            "model_reason": "selection_policy_not_ready",
            "trade_model_reason": "below_learned_policy",
            "selection_policy_version": "nested_temporal_utility_v1",
            "observation_risk_label": "HIGH_RISK",
        }
        for column in discrete_columns:
            if column in freeze_contract.BOOLEAN_BEHAVIOR_COLUMNS:
                row[column] = 0
            elif column in freeze_contract.INTEGER_BEHAVIOR_COLUMNS:
                row[column] = 1
            elif column in exact_values:
                row[column] = exact_values[column]
            else:
                raise RuntimeError(f"unclassified self-test discrete column: {column}")
        for column in score_columns:
            row[column] = None if column == "recommended_max_gap" else 0.125
        row["risk_gate_pass"] = 0
        return pd.DataFrame([row])

    top_contract = {
        "identity_columns": list(FREEZE_IDENTITY_COLUMNS),
        "discrete_columns": list(FREEZE_TOP10_DISCRETE_COLUMNS),
        "score_columns": list(FREEZE_TOP10_SCORE_COLUMNS),
        "score_decimals": 8,
    }
    oos_contract = {
        "identity_columns": list(FREEZE_IDENTITY_COLUMNS),
        "discrete_columns": list(FREEZE_OOS_DISCRETE_COLUMNS),
        "score_columns": list(FREEZE_OOS_SCORE_COLUMNS),
        "score_decimals": 8,
    }
    top = compute_behavior_fingerprints(
        frame_for(
            tuple(FREEZE_TOP10_DISCRETE_COLUMNS),
            tuple(FREEZE_TOP10_SCORE_COLUMNS),
        ),
        top_contract,
        context="self_test.top10",
    )
    oos = compute_behavior_fingerprints(
        frame_for(
            tuple(FREEZE_OOS_DISCRETE_COLUMNS),
            tuple(FREEZE_OOS_SCORE_COLUMNS),
        ),
        oos_contract,
        context="self_test.selector_oos",
    )
    action = {
        "stage_watchlist": [
            {
                "ts_code": f"00000{index}.SZ",
                "action": "SHADOW_ONLY" if index < 3 else "REJECT",
                "stage_watch_rank": index,
                "watch_label": "二筛影子" if index < 3 else "仅观察",
                "target_weight": 0.0,
                "trade_shadow_selected": 1 if index < 3 else 0,
            }
            for index in (1, 2, 3)
        ]
    }
    action_result = compute_action_watchlist_fingerprint(
        action,
        {"columns": list(ACTION_WATCHLIST_COLUMNS)},
    )
    for result in (top, oos):
        for key in (
            "identity_sha256",
            "date_counts_sha256",
            "discrete_sha256",
            "scores_sha256",
        ):
            _evidence_require(
                re.fullmatch(r"[0-9a-f]{64}", str(result.get(key) or ""))
                is not None,
                f"self-test {key} is not 64-hex",
            )
    _evidence_require(action_result["shadow_only_rows"] == 2, "self-test shadow count")
    _evidence_require(
        len(freeze_contract.FINGERPRINT_KEYS) == 11,
        "C3 fingerprint envelope is not 11 keys",
    )
    return {
        "schema_version": ACTIVATION_EVIDENCE_SCHEMA,
        "passed": True,
        "checks": {
            "shared_c3_contract": True,
            "fingerprint_envelope_11_keys": True,
            "top10_behavior_contract": True,
            "selector_oos_behavior_contract": True,
            "action_watchlist_contract": True,
        },
    }


def _safe_probe_stdout(
    summary: Mapping[str, Any],
    *,
    evidence_written: bool,
) -> dict[str, Any]:
    """Return only boolean/check-name status suitable for the public job log."""

    checks = summary.get("checks")
    safe_checks = {
        str(name): value is True
        for name, value in checks.items()
    } if isinstance(checks, Mapping) else {}
    failed = [name for name, passed in safe_checks.items() if not passed]
    if summary.get("passed") is not True and not failed:
        failed = ["activation_evidence_generation"]
    return {
        "schema_version": "dc20_forced_frozen_canonical_v2_probe_log_v1",
        "system": "DC2.0",
        "read_only": True,
        "passed": summary.get("passed") is True and evidence_written,
        "checks": safe_checks,
        "failed_checks": failed,
        "runner_temp_evidence": {
            "written": evidence_written,
            "whitelist_only": evidence_written,
            "printed": False,
            "uploaded": False,
            "persisted": False,
        },
    }


def _public_activation_evidence_line(compact: str) -> str:
    """Return the single allowlisted JSON line authorized for the public job log."""

    _evidence_require("\n" not in compact and "\r" not in compact, "evidence must be one line")
    decoded = json.loads(compact)
    _evidence_require(isinstance(decoded, Mapping), "public evidence must be an object")
    _evidence_require(
        decoded.get("schema_version") == ACTIVATION_EVIDENCE_SCHEMA,
        "public evidence schema drifted",
    )
    _validate_public_activation_evidence_shape(decoded)
    # Re-render through the same allowlist/sensitive-data guard immediately
    # before stdout so no alternate serialization path can disclose more.
    return _render_compact_activation_evidence(decoded)


def _canonical_replay_report_probe(
    report_path: Path,
    *,
    root: Path = ROOT,
) -> tuple[dict[str, Any], bool]:
    report = _read_json(report_path)
    golden = report.get("golden") or {}
    history = report.get("history") or {}
    reference = golden.get("reference") or {}
    top10 = golden.get("top10") or {}
    selector_oos = golden.get("selector_oos") or {}
    canonical_scores = golden.get("canonical_scores") or {}
    top10_scores = canonical_scores.get("top10") or {}
    selector_scores = canonical_scores.get("selector_oos") or {}
    fingerprint_integrity = golden.get("fingerprint_integrity") or {}
    prediction_policy = golden.get("prediction_policy_execution") or {}
    action_candidates = golden.get("action_plan_candidates") or {}
    no_trade = golden.get("no_trade") or {}
    try:
        _history_activation_evidence(report)
        exact_history_loader = True
    except (RuntimeError, ValueError):
        exact_history_loader = False

    checks = {
        "forced_replay_report_passed": report.get("status") == "pass",
        "diagnostic_mode_exact": report.get("diagnostic_mode")
        == "workspace_only_forced_frozen_canonical_v2",
        "force_prediction_enabled": report.get("force_prediction") is True,
        "frozen_snapshot_source": exact_history_loader,
        "no_live_history_fallback": history.get("live_history_fallback") is False,
        "frozen_snapshot_sha_locked": history.get("sha256")
        == "77e48be6732a08698a6abf4a0da74cb02b3129c57d14be66fb94679816a5337e",
        "manifest_activity_schema_valid": exact_history_loader,
        "manifest_not_mutated": history.get("manifest_mutated_on_disk") is False,
        "persisted_c6_reference_verified": reference.get(
            "persisted_trust_root_verified"
        )
        is True,
        "golden_passed": golden.get("status") == "pass",
        "top10_identity_equal": top10.get("identity_equal") is True,
        "top10_discrete_and_gap_exact": bool(top10.get("changed_rows"))
        and all(value == 0 for value in top10.get("changed_rows", {}).values()),
        "selector_oos_identity_equal": selector_oos.get("identity_equal") is True,
        "selector_oos_discrete_and_gap_exact": bool(
            selector_oos.get("changed_rows")
        )
        and all(
            value == 0
            for value in selector_oos.get("changed_rows", {}).values()
        ),
        "top10_q8_hard_equal": (top10_scores.get("8") or {}).get("equal")
        is True,
        "selector_oos_q8_hard_equal": (
            selector_scores.get("8") or {}
        ).get("equal")
        is True,
        "live_policies_match_v2_projection": fingerprint_integrity.get(
            "live_policies_match_fingerprint_projection"
        )
        is True,
        "prediction_policy_thresholds_and_gates_recomputed": prediction_policy.get(
            "policy_threshold_columns_match"
        )
        is True,
        "action_plan_no_trade": action_candidates.get("status_code")
        == "NO_TRADE_MODEL_NOT_PROMOTED"
        and action_candidates.get("formal_buy_count") == 0,
        "action_candidate_contract_present": bool(
            action_candidates.get("action_plan_candidates_sha256_q8")
        ),
        "formal_runtime_no_trade": no_trade.get("formal_signals") == 0
        and no_trade.get("selector_global_promotion_rows") == 0
        and no_trade.get("action_formal_buy_count") == 0,
        "raw_float64_execution_preserved": (
            golden.get("execution_numeric") or {}
        ).get("raw_execution_preserved")
        is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    probes = {
        precision: {
            "gate": "hard" if precision == "8" else "audit_only",
            "top10_equal": (top10_scores.get(precision) or {}).get("equal"),
            "top10_reference_sha256": (
                top10_scores.get(precision) or {}
            ).get("reference_sha256", ""),
            "top10_candidate_sha256": (
                top10_scores.get(precision) or {}
            ).get("candidate_sha256", ""),
            "selector_oos_equal": (
                selector_scores.get(precision) or {}
            ).get("equal"),
            "selector_oos_reference_sha256": (
                selector_scores.get(precision) or {}
            ).get("reference_sha256", ""),
            "selector_oos_candidate_sha256": (
                selector_scores.get(precision) or {}
            ).get("candidate_sha256", ""),
        }
        for precision in ("6", "8", "10", "12")
    }
    summary = {
        "schema_version": "dc20_forced_frozen_canonical_v2_probe_v1",
        "system": "DC2.0",
        "read_only": True,
        "report_file": report_path.name,
        "passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "canonical_precision": probes,
        "behavior_contract_candidate": golden.get(
            "behavior_contract_candidate", {}
        ),
        "research_oos_summary": golden.get("research_oos_summary", {}),
        "research_oos_metrics": golden.get("research_oos_metrics", {}),
        "fingerprint_integrity": fingerprint_integrity,
        "prediction_policy_execution": prediction_policy,
        "action_plan_candidates": action_candidates,
        "candidate_source": report.get("candidate_source", {}),
        "reference": {
            "profile": reference.get("profile"),
            "persisted_trust_root_verified": reference.get(
                "persisted_trust_root_verified"
            ),
            "same_machine_reference_only": reference.get(
                "same_machine_reference_only"
            ),
        },
    }
    if not failed:
        summary["activation_evidence"] = _build_activation_evidence(
            report,
            root=root,
        )
    return summary, not failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print read-only frozen canonical V2 evidence"
    )
    parser.add_argument(
        "--self-test-evidence-contract",
        action="store_true",
        help="exercise the shared C3 behavior/action evidence schema without files",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.self_test_evidence_contract:
        try:
            payload = _activation_evidence_contract_self_test()
        except Exception:
            payload = {
                "schema_version": ACTIVATION_EVIDENCE_SCHEMA,
                "passed": False,
                "checks": {"shared_c3_contract": False},
                "failed_checks": ["shared_c3_contract"],
            }
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0 if payload["passed"] else 1

    report_text = os.environ.get("FINGERPRINT_REPLAY_REPORT", "").strip()
    if not report_text:
        print(
            json.dumps(
                {
                    "schema_version": "dc20_forced_frozen_canonical_v2_probe_log_v1",
                    "system": "DC2.0",
                    "read_only": True,
                    "passed": False,
                    "checks": {"replay_report_available": False},
                    "failed_checks": ["replay_report_available"],
                    "runner_temp_evidence": {
                        "written": False,
                        "whitelist_only": False,
                        "printed": False,
                        "uploaded": False,
                        "persisted": False,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    evidence_written = False
    public_evidence_line = ""
    try:
        summary, passed = _canonical_replay_report_probe(Path(report_text))
        if passed:
            evidence = _evidence_mapping(
                summary.pop("activation_evidence", None), "activation_evidence"
            )
            compact = _render_compact_activation_evidence(evidence)
            output_text = os.environ.get("FINGERPRINT_EVIDENCE_OUTPUT", "").strip()
            _evidence_require(
                output_text != "",
                "FINGERPRINT_EVIDENCE_OUTPUT is required for a passing probe",
            )
            output = Path(output_text)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(compact + "\n", encoding="utf-8")
            evidence_written = True
            public_evidence_line = _public_activation_evidence_line(compact)
    except Exception:
        summary = {
            "schema_version": "dc20_forced_frozen_canonical_v2_probe_v1",
            "system": "DC2.0",
            "read_only": True,
            "passed": False,
            "checks": {"activation_evidence_generation": False},
            "failed_checks": ["activation_evidence_generation"],
        }
        passed = False
    if passed and evidence_written:
        print(public_evidence_line)
    else:
        safe_summary = _safe_probe_stdout(
            summary,
            evidence_written=evidence_written,
        )
        print(
            json.dumps(
                safe_summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0 if passed and evidence_written else 1


if __name__ == "__main__":
    raise SystemExit(main())
