#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.three_engine_models import (  # noqa: E402
    CORE_HEADS,
    RUNTIME_ALIGNED_MARKET_FEATURES,
    RUNTIME_FEATURE_CONTRACT_VERSION,
    model_artifact_payload,
    train_three_engine_models,
)


DEFAULT_LEDGER = ROOT / "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
DEFAULT_LEDGER_MANIFEST = (
    ROOT / "data/decision_three_engines/five_year_ledger_manifest.json"
)
DEFAULT_MODEL_DIR = ROOT / "models/decision_three_engines"
DEFAULT_VALIDATION = DEFAULT_MODEL_DIR / "validation_latest.json"
DEFAULT_OOF = ROOT / "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
HEADS = (*CORE_HEADS, "p_fill_shadow")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if pd.isna(value):
        return None
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_joblib(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        joblib.dump(payload, temporary, compress=3)
        loaded = joblib.load(temporary)
        if not isinstance(loaded, dict) or loaded.get("head") != payload.get("head"):
            raise RuntimeError(f"joblib round-trip validation failed for {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_csv_gzip(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                frame.to_csv(compressed, index=False, lineterminator="\n")
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_runtime_ledger_contract(
    ledger_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"cannot read three-engine ledger manifest: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("three-engine ledger manifest must be an object")
    if manifest.get("ledger_sha256") != _sha256(ledger_path):
        raise RuntimeError("three-engine ledger does not match its manifest SHA256")
    if manifest.get("owner") != "njedu2023-prog/DC20" or manifest.get(
        "runtime_dependency_on_top10_decision"
    ) is not False:
        raise RuntimeError("three-engine ledger ownership/isolation is invalid")
    contract = manifest.get("runtime_feature_contract")
    if not isinstance(contract, dict):
        raise RuntimeError("three-engine runtime feature contract is missing")
    if contract.get("version") != RUNTIME_FEATURE_CONTRACT_VERSION:
        raise RuntimeError("three-engine runtime feature contract version drifted")
    columns = contract.get("columns")
    if not isinstance(columns, list) or tuple(columns) != tuple(
        RUNTIME_ALIGNED_MARKET_FEATURES
    ):
        raise RuntimeError("three-engine runtime feature column inventory drifted")
    if contract.get("available_by_d_close") is not True:
        raise RuntimeError("three-engine runtime features are not D-close safe")
    if contract.get("future_columns_used") != []:
        raise RuntimeError("three-engine runtime feature contract uses future columns")
    return contract


def _oof_export(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = [
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
        "p_fill_shadow_rank",
        "p_fill_shadow_probability",
        "p_fill_shadow_score",
        "p_fill_shadow_oof_fold",
        "p_fill_shadow_oof_fold_kind",
        "p_fill_shadow_oof_train_end",
        "p_fill_shadow_oof_model_kind",
        "p_fill_shadow_oof_calibration",
        "p_fill_shadow_oof_selection_eligible",
        "p_fill_shadow_oof_selection_composite_lift",
    ]
    columns = [name for name in ordered if name in frame.columns]
    return frame[columns].sort_values(
        ["signal_date", "promotion_rank", "ts_code"], kind="stable"
    ).reset_index(drop=True)


def write_training_artifacts(
    result: Any,
    *,
    ledger_path: Path,
    model_dir: Path,
    validation_path: Path,
    oof_path: Path,
    ledger_manifest_path: Path | None = None,
    runtime_feature_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    model_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, Any]] = {}
    model_metadata: dict[str, dict[str, Any]] = {}
    for head in HEADS:
        payload = model_artifact_payload(result, head)
        path = model_dir / f"{head}.joblib"
        _atomic_joblib(path, payload)
        artifact_sha256 = _sha256(path)
        artifacts[head] = {
            "path": _relative(path),
            "sha256": artifact_sha256,
            "bytes": path.stat().st_size,
        }
        model_metadata[head] = {
            "status": payload.get("status"),
            "promoted": payload.get("promoted") is True,
            "model_version": payload.get("model_version", ""),
            "model_as_of_date": payload.get("model_as_of_date", ""),
            "artifact_sha256": artifact_sha256,
        }

    oof = _oof_export(result.oof_top10)
    _atomic_csv_gzip(oof_path, oof)
    artifacts["oof_top10"] = {
        "path": _relative(oof_path),
        "sha256": _sha256(oof_path),
        "bytes": oof_path.stat().st_size,
        "rows": int(len(oof)),
        "dates": int(oof["signal_date"].astype(str).nunique()) if not oof.empty else 0,
    }

    validation = copy.deepcopy(result.validation)
    validation["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    validation["source"]["ledger_path"] = _relative(ledger_path)
    validation["source"]["ledger_sha256"] = _sha256(ledger_path)
    if ledger_manifest_path is not None:
        validation["source"]["ledger_manifest_path"] = _relative(
            ledger_manifest_path
        )
        validation["source"]["ledger_manifest_sha256"] = _sha256(
            ledger_manifest_path
        )
    if runtime_feature_contract is not None:
        validation["source"]["runtime_feature_contract"] = dict(
            runtime_feature_contract
        )
    validation["artifacts"] = artifacts
    validation["model_metadata"] = model_metadata
    validation["release_contract"] = {
        "promoted_only_when_all_core_heads_and_top10_integrity_pass": True,
        "failed_or_constant_head_must_not_emit_official_rank": True,
        "p_fill_is_shadow_only": True,
        "actual_execution_claimed": False,
    }
    _atomic_json(validation_path, validation)
    return validation


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train DC20's independent promotion, downside-risk and T+1-profit "
            "probability engines with nested chronological walk-forward validation."
        )
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--ledger-manifest", type=Path, default=DEFAULT_LEDGER_MANIFEST
    )
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument(
        "--require-promoted",
        action="store_true",
        help="exit 2 unless every core model and release gate is READY",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    ledger_path = args.ledger.resolve()
    if not ledger_path.is_file():
        raise FileNotFoundError(f"three-engine supervised ledger not found: {ledger_path}")
    ledger_manifest_path = args.ledger_manifest.resolve()
    runtime_feature_contract = _load_runtime_ledger_contract(
        ledger_path,
        ledger_manifest_path,
    )
    ledger = pd.read_csv(
        ledger_path,
        dtype={
            "signal_date": str,
            "buy_date": str,
            "target_exit_date": str,
            "ts_code": str,
        },
    )
    result = train_three_engine_models(ledger)
    validation = write_training_artifacts(
        result,
        ledger_path=ledger_path,
        model_dir=args.model_dir.resolve(),
        validation_path=args.validation.resolve(),
        oof_path=args.oof.resolve(),
        ledger_manifest_path=ledger_manifest_path,
        runtime_feature_contract=runtime_feature_contract,
    )
    summary = {
        "status": validation.get("status"),
        "ready": validation.get("ready") is True,
        "ledger_rows": validation.get("source", {}).get("rows"),
        "ledger_dates": validation.get("source", {}).get("dates"),
        "oof_top10": validation.get("oof_top10"),
        "heads": {
            head: {
                "status": validation.get("heads", {}).get(head, {}).get("status"),
                "gate_failures": validation.get("heads", {})
                .get(head, {})
                .get("gate_failures", []),
            }
            for head in HEADS
        },
        "validation": _relative(args.validation.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2))
    if args.require_promoted and validation.get("ready") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
