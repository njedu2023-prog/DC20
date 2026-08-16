#!/usr/bin/env python3
"""Print non-sensitive component hashes for the frozen Decision runtime."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from dataclasses import asdict
from pathlib import Path
import sys
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
from top10decision.decision.model_freeze import (  # noqa: E402
    load_frozen_history_snapshot,
    load_model_freeze,
)
from top10decision.decision.trade_selector import _bundle_hash  # noqa: E402


INPUT_PATHS = (
    "models/decision_v12_frozen_history_20260805.csv.gz",
    "data/auction_v3/promotion_prior/five_year_daily_stage_board.csv",
    "data/auction_v3/promotion_prior/five_year_event_features.csv.gz",
    "models/decision_promotion_v13_validation.json",
    "src/top10decision/auction_v3/engine.py",
    "src/top10decision/auction_v3/calibration.py",
    "src/top10decision/auction_v3/config.py",
    "src/top10decision/auction_v3/promotion_model.py",
    "src/top10decision/decision/trade_selector.py",
)


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


def _model_components() -> dict[str, Any]:
    manifest = load_model_freeze(ROOT, required=True)
    history, history_audit = load_frozen_history_snapshot(ROOT, manifest)
    if history is None:
        raise RuntimeError("active freeze did not return its frozen history")
    enriched = attach_promotion_source_features(history, ROOT)
    all_clean = enriched.dropna(
        subset=["net_return", "proposed_gap", "market_fill"]
    ).copy()
    columns = list(
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
    available = [name for name in columns if name in all_clean.columns]
    training = all_clean[available].copy()
    sort_columns = [
        name
        for name in ("signal_date", "ts_code", "proposed_gap")
        if name in training.columns
    ]
    if sort_columns:
        training = training.sort_values(sort_columns, kind="stable")
    training = training.reset_index(drop=True)

    source_hasher = hashlib.sha256()
    engine_path = ROOT / "src/top10decision/auction_v3/engine.py"
    for name in ("engine.py", "calibration.py", "config.py", "promotion_model.py"):
        source_hasher.update(engine_path.with_name(name).read_bytes())
    validation_path = ROOT / "models/decision_promotion_v13_validation.json"
    if validation_path.exists():
        source_hasher.update(validation_path.read_bytes())

    config = AuctionV3Config(root=ROOT)
    config_payload = {
        key: value for key, value in asdict(config).items() if key != "root"
    }
    training_sha = _hash_frame(training)
    source_sha = source_hasher.hexdigest()
    artifact_payload = {
        "model_version": config.model_version,
        "training_sha256": training_sha,
        "source_sha256": source_sha,
        "config": config_payload,
    }
    column_hashes = {
        column: _hash_frame(training[[column]]) for column in training.columns
    }
    return {
        "recomputed_model_artifact_sha256": _json_sha256(artifact_payload),
        "training_sha256": training_sha,
        "source_sha256": source_sha,
        "config_sha256": _json_sha256(config_payload, default=str),
        "history_audit": history_audit,
        "history_rows": int(len(history)),
        "training_rows": int(len(training)),
        "training_columns": list(training.columns),
        "training_dtypes": {
            name: str(dtype) for name, dtype in training.dtypes.items()
        },
        "training_column_sha256": column_hashes,
    }


def _selector_components(model_meta: dict[str, Any]) -> dict[str, Any]:
    top10_path = ROOT / "outputs/auction_v3/metrics/backtest_top10_latest.csv"
    selector = model_meta.get("trade_selector") or {}
    policy = selector.get("production_policy") or {}
    result: dict[str, Any] = {
        "policy_sha256": _json_sha256(policy, default=str),
        "policy": policy,
        "persisted_top10_path": str(top10_path.relative_to(ROOT)),
    }
    if not top10_path.is_file():
        result["persisted_top10_status"] = "missing"
        return result
    frame = pd.read_csv(top10_path, low_memory=False)
    result.update(
        {
            "persisted_top10_status": "loaded",
            "persisted_top10_rows": int(len(frame)),
            "persisted_top10_dates": int(
                frame["signal_date"].astype(str).nunique()
                if "signal_date" in frame.columns
                else 0
            ),
            "persisted_roundtrip_bundle_sha256": _bundle_hash(frame, policy),
        }
    )
    return result


def main() -> int:
    manifest = load_model_freeze(ROOT, required=True)
    model_meta = _read_json(
        ROOT / "outputs/auction_v3/models/model_meta_latest.json"
    )
    backtest = _read_json(
        ROOT / "outputs/auction_v3/metrics/backtest_latest.json"
    )
    production = manifest.get("production") or {}
    selector_meta = model_meta.get("trade_selector") or {}
    selector_backtest = backtest.get("trade_selector") or {}
    packages = {}
    for name in (
        "numpy",
        "pandas",
        "scikit-learn",
        "scipy",
        "joblib",
        "lightgbm",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"

    summary = {
        "schema_version": "dc20_decision_fingerprint_diagnostic_v1",
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
        "model_components": _model_components(),
        "selector_components": _selector_components(model_meta),
        "input_sha256": {
            relative: _sha256(ROOT / relative)
            for relative in INPUT_PATHS
            if (ROOT / relative).is_file()
        },
        "packages": packages,
        "python": sys.version,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
