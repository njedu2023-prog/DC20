from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


FREEZE_SCHEMA_VERSION = "decision_model_freeze_v1"
DEFAULT_FREEZE_PATH = Path("models/decision_model_freeze.json")
DATE_PATTERN = re.compile(r"^20\d{6}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DecisionModelFreezeError(RuntimeError):
    """Raised when a frozen Decision production contract drifts."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DecisionModelFreezeError(f"model freeze manifest missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionModelFreezeError(f"model freeze manifest unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise DecisionModelFreezeError("model freeze manifest must be a JSON object")
    return payload


def load_model_freeze(
    root: Path | str = Path("."),
    *,
    required: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = root_path / DEFAULT_FREEZE_PATH
    if not path.exists() and not required:
        return {}
    payload = _read_json(path)
    if payload.get("schema_version") != FREEZE_SCHEMA_VERSION:
        raise DecisionModelFreezeError(
            f"unsupported model freeze schema: {payload.get('schema_version')}"
        )
    if not isinstance(payload.get("active"), bool):
        raise DecisionModelFreezeError("model freeze active must be boolean")
    cutoff = str(payload.get("training_cutoff_signal_date") or "")
    if payload["active"] and not DATE_PATTERN.fullmatch(cutoff):
        raise DecisionModelFreezeError(
            "active model freeze requires training_cutoff_signal_date=YYYYMMDD"
        )
    production = payload.get("production")
    if payload["active"] and not isinstance(production, dict):
        raise DecisionModelFreezeError("active model freeze requires production metadata")
    pinned = payload.get("pinned_files", {})
    if payload["active"] and not isinstance(pinned, dict):
        raise DecisionModelFreezeError("active model freeze requires pinned_files")
    snapshot = payload.get("history_snapshot")
    if payload["active"] and not isinstance(snapshot, dict):
        raise DecisionModelFreezeError(
            "active model freeze requires history_snapshot metadata"
        )
    if isinstance(snapshot, dict):
        relative_path = str(snapshot.get("path") or "").strip()
        path = Path(relative_path)
        if (
            not relative_path
            or path.is_absolute()
            or ".." in path.parts
            or not relative_path.endswith(".csv.gz")
        ):
            raise DecisionModelFreezeError(
                "history_snapshot.path must be a repository-relative .csv.gz path"
            )
        if not isinstance(snapshot.get("bootstrap_mode"), bool):
            raise DecisionModelFreezeError(
                "history_snapshot.bootstrap_mode must be boolean"
            )
        snapshot_sha = str(snapshot.get("sha256") or "")
        if (
            payload["active"]
            and not snapshot["bootstrap_mode"]
            and not SHA256_PATTERN.fullmatch(snapshot_sha)
        ):
            raise DecisionModelFreezeError(
                "finalized history snapshot requires sha256"
            )
    return payload


def model_freeze_active(manifest: dict[str, Any]) -> bool:
    return manifest.get("active") is True


def apply_frozen_history_cutoff(
    frame: pd.DataFrame,
    manifest: dict[str, Any],
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
        raise DecisionModelFreezeError("training history has no signal_date column")

    cutoff = str(manifest["training_cutoff_signal_date"])
    signal_dates = (
        frame["signal_date"]
        .astype(str)
        .str.replace(r"[^0-9]", "", regex=True)
        .str.slice(0, 8)
    )
    valid = signal_dates.str.fullmatch(r"20\d{6}", na=False)
    eligible = valid & signal_dates.le(cutoff)
    filtered = frame.loc[eligible].copy().reset_index(drop=True)
    if filtered.empty:
        raise DecisionModelFreezeError(
            f"model freeze removed all training rows at cutoff {cutoff}"
        )
    kept_dates = sorted(filtered["signal_date"].astype(str).unique())
    audit = {
        "active": True,
        "freeze_id": str(manifest.get("freeze_id") or ""),
        "training_cutoff_signal_date": cutoff,
        "rows_before": int(len(frame)),
        "rows_after": int(len(filtered)),
        "rows_removed": int(len(frame) - len(filtered)),
        "history_start": kept_dates[0] if kept_dates else "",
        "history_end": kept_dates[-1] if kept_dates else "",
    }
    return filtered, audit


def history_snapshot_bootstrap_mode(manifest: dict[str, Any]) -> bool:
    snapshot = manifest.get("history_snapshot", {}) or {}
    return bool(model_freeze_active(manifest) and snapshot.get("bootstrap_mode"))


def _history_snapshot_path(
    root: Path | str,
    manifest: dict[str, Any],
) -> Path:
    snapshot = manifest.get("history_snapshot", {}) or {}
    relative_path = str(snapshot.get("path") or "").strip()
    if not relative_path:
        raise DecisionModelFreezeError("history snapshot path is missing")
    return Path(root).resolve() / relative_path


def load_frozen_history_snapshot(
    root: Path | str,
    manifest: dict[str, Any],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    if not model_freeze_active(manifest):
        return None, {"active": False, "source": "live_history"}
    path = _history_snapshot_path(root, manifest)
    bootstrap = history_snapshot_bootstrap_mode(manifest)
    if not path.is_file():
        if bootstrap:
            return None, {
                "active": True,
                "source": "bootstrap_required",
                "path": str(path.relative_to(Path(root).resolve())),
            }
        raise DecisionModelFreezeError(f"frozen history snapshot missing: {path}")

    actual_sha = _sha256(path)
    snapshot = manifest.get("history_snapshot", {}) or {}
    expected_sha = str(snapshot.get("sha256") or "")
    if expected_sha and actual_sha != expected_sha:
        raise DecisionModelFreezeError(
            "frozen history snapshot drift detected: "
            f"expected={expected_sha} actual={actual_sha}"
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
    filtered, cutoff_audit = apply_frozen_history_cutoff(frame, manifest)
    if int(cutoff_audit.get("rows_removed", 0)) != 0:
        raise DecisionModelFreezeError(
            "frozen history snapshot contains rows beyond its cutoff"
        )
    return filtered, {
        **cutoff_audit,
        "source": "frozen_snapshot",
        "path": str(path.relative_to(Path(root).resolve())),
        "sha256": actual_sha,
        "bootstrap_mode": bootstrap,
    }


def capture_frozen_history_snapshot(
    root: Path | str,
    manifest: dict[str, Any],
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not history_snapshot_bootstrap_mode(manifest):
        raise DecisionModelFreezeError(
            "history snapshot capture is disabled outside bootstrap mode"
        )
    existing, audit = load_frozen_history_snapshot(root, manifest)
    if existing is not None:
        return existing, audit

    filtered, _ = apply_frozen_history_cutoff(frame, manifest)
    path = _history_snapshot_path(root, manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    filtered.to_csv(
        temporary,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    temporary.replace(path)
    captured, captured_audit = load_frozen_history_snapshot(root, manifest)
    if captured is None:
        raise DecisionModelFreezeError("history snapshot capture did not persist")
    captured_audit["source"] = "bootstrap_snapshot_created"
    return captured, captured_audit


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_pinned_files(
    root: Path | str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    pinned = manifest.get("pinned_files", {}) or {}
    if not model_freeze_active(manifest):
        return {
            "active": False,
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "pinned_files": int(len(pinned)),
            "validated": True,
            "enforced": False,
        }
    mismatches: list[dict[str, str]] = []
    for relative_path, expected in sorted(pinned.items()):
        path = root_path / str(relative_path)
        if not path.is_file():
            mismatches.append(
                {
                    "path": str(relative_path),
                    "expected": str(expected),
                    "actual": "MISSING",
                }
            )
            continue
        actual = _sha256(path)
        if actual != str(expected):
            mismatches.append(
                {
                    "path": str(relative_path),
                    "expected": str(expected),
                    "actual": actual,
                }
            )
    if mismatches:
        detail = "; ".join(
            f"{item['path']} expected={item['expected']} actual={item['actual']}"
            for item in mismatches
        )
        raise DecisionModelFreezeError(f"frozen file drift detected: {detail}")
    return {
        "active": model_freeze_active(manifest),
        "freeze_id": str(manifest.get("freeze_id") or ""),
        "pinned_files": int(len(pinned)),
        "validated": True,
        "enforced": True,
    }


def validate_runtime_artifacts(
    root: Path | str,
    manifest: dict[str, Any],
    *,
    check_action_plan: bool = True,
) -> dict[str, Any]:
    if not model_freeze_active(manifest):
        return {"active": False, "validated": True}
    root_path = Path(root).resolve()
    model_meta = _read_json(
        root_path / "outputs/auction_v3/models/model_meta_latest.json"
    )
    backtest = _read_json(
        root_path / "outputs/auction_v3/metrics/backtest_latest.json"
    )
    expected = manifest.get("production", {}) or {}
    selector = backtest.get("trade_selector", {}) or {}
    meta_selector = model_meta.get("trade_selector", {}) or {}
    bootstrap = history_snapshot_bootstrap_mode(manifest)
    runtime_values = {
        "model_version": str(model_meta.get("model_version") or ""),
        "model_artifact_sha256": str(
            model_meta.get("model_artifact_sha256") or ""
        ),
        "model_promoted": model_meta.get("promoted") is True,
        "trade_selector_version": str(selector.get("version") or ""),
        "trade_selector_artifact_sha256": str(
            selector.get("production_artifact_sha256") or ""
        ),
        "trade_selector_promoted": selector.get("promoted") is True,
        "meta_trade_selector_version": str(meta_selector.get("version") or ""),
        "meta_trade_selector_artifact_sha256": str(
            meta_selector.get("production_artifact_sha256") or ""
        ),
        "meta_trade_selector_promoted": meta_selector.get("promoted") is True,
    }
    checks = {
        "model_version": (
            str(model_meta.get("model_version") or "")
            == str(expected.get("model_version") or "")
        ),
        "model_artifact_sha256": (
            bootstrap
            or runtime_values["model_artifact_sha256"]
            == str(expected.get("model_artifact_sha256") or "")
        ),
        "model_promoted": (
            (model_meta.get("promoted") is True)
            == (expected.get("promoted") is True)
        ),
        "trade_selector_version": (
            str(selector.get("version") or "")
            == str(expected.get("trade_selector_version") or "")
        ),
        "trade_selector_artifact_sha256": (
            bootstrap
            or runtime_values["trade_selector_artifact_sha256"]
            == str(expected.get("trade_selector_artifact_sha256") or "")
        ),
        "trade_selector_promoted": (
            (selector.get("promoted") is True)
            == (expected.get("trade_selector_promoted") is True)
        ),
        "meta_trade_selector_version": (
            str(meta_selector.get("version") or "")
            == str(expected.get("trade_selector_version") or "")
        ),
        "meta_trade_selector_artifact_sha256": (
            bootstrap
            or str(meta_selector.get("production_artifact_sha256") or "")
            == str(expected.get("trade_selector_artifact_sha256") or "")
        ),
        "meta_trade_selector_promoted": (
            (meta_selector.get("promoted") is True)
            == (expected.get("trade_selector_promoted") is True)
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        expected_values = {
            "model_version": str(expected.get("model_version") or ""),
            "model_artifact_sha256": str(
                expected.get("model_artifact_sha256") or ""
            ),
            "model_promoted": expected.get("promoted") is True,
            "trade_selector_version": str(
                expected.get("trade_selector_version") or ""
            ),
            "trade_selector_artifact_sha256": str(
                expected.get("trade_selector_artifact_sha256") or ""
            ),
            "trade_selector_promoted": (
                expected.get("trade_selector_promoted") is True
            ),
        }
        raise DecisionModelFreezeError(
            "frozen runtime artifact drift detected: "
            + ", ".join(failed)
            + "; expected="
            + json.dumps(expected_values, ensure_ascii=True, sort_keys=True)
            + "; actual="
            + json.dumps(runtime_values, ensure_ascii=True, sort_keys=True)
        )

    action_plan_path = root_path / "outputs/decision/action_plan_latest.json"
    action_plan_checks: dict[str, bool] = {}
    if check_action_plan and action_plan_path.is_file():
        action_plan = _read_json(action_plan_path)
        action_model = action_plan.get("model", {}) or {}
        action_selector = action_model.get("trade_selector", {}) or {}
        action_plan_checks = {
            "formal_status": (
                str(action_plan.get("status_code") or "")
                == str(expected.get("formal_status") or "")
            ),
            "formal_buy_count": (
                int(action_plan.get("formal_buy_count") or 0)
                == int(expected.get("formal_buy_count") or 0)
            ),
            "action_model_version": (
                str(action_model.get("version") or "")
                == str(expected.get("model_version") or "")
            ),
            "action_model_artifact_sha256": (
                bootstrap
                or str(action_model.get("artifact_sha256") or "")
                == str(expected.get("model_artifact_sha256") or "")
            ),
            "action_model_promoted": (
                (action_model.get("promoted") is True)
                == (expected.get("promoted") is True)
            ),
            "action_selector_version": (
                str(action_selector.get("version") or "")
                == str(expected.get("trade_selector_version") or "")
            ),
            "action_selector_artifact_sha256": (
                bootstrap
                or str(action_model.get("trade_selector_artifact_sha256") or "")
                == str(expected.get("trade_selector_artifact_sha256") or "")
            ),
            "action_selector_promoted": (
                (action_selector.get("promoted") is True)
                == (expected.get("trade_selector_promoted") is True)
            ),
            "action_artifact_versions_match": (
                action_model.get("artifact_versions_match") is True
            ),
            "action_artifact_fingerprints_match": (
                action_model.get("artifact_fingerprints_match") is True
            ),
            "action_selector_artifacts_match": (
                action_model.get("trade_selector_artifacts_match") is True
            ),
        }
        failed_action_plan = [
            name for name, passed in action_plan_checks.items() if not passed
        ]
        if failed_action_plan:
            raise DecisionModelFreezeError(
                "frozen action plan drift detected: "
                + ", ".join(failed_action_plan)
            )
    history_end = str(
        ((model_meta.get("data_coverage") or {}).get("history_end")) or ""
    )
    cutoff = str(manifest.get("training_cutoff_signal_date") or "")
    if history_end and history_end > cutoff:
        raise DecisionModelFreezeError(
            f"frozen history_end {history_end} exceeds cutoff {cutoff}"
        )
    return {
        "active": True,
        "freeze_id": str(manifest.get("freeze_id") or ""),
        "validated": True,
        "bootstrap_mode": bootstrap,
        "fingerprints_enforced": not bootstrap,
        "runtime_values": runtime_values,
        "checks": checks,
        "action_plan_checks": action_plan_checks,
        "history_end": history_end,
        "training_cutoff_signal_date": cutoff,
    }


__all__ = [
    "DecisionModelFreezeError",
    "apply_frozen_history_cutoff",
    "capture_frozen_history_snapshot",
    "history_snapshot_bootstrap_mode",
    "load_model_freeze",
    "load_frozen_history_snapshot",
    "model_freeze_active",
    "validate_pinned_files",
    "validate_runtime_artifacts",
]
