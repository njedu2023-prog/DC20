"""Independent inference rehearsal commands; no production, ledger or Git writer.

Promotion and profit are deliberately separate processes/artifacts. Failure of
the latter cannot delete or prevent the former. Historical comparison is a
separate acceptance operation, never a dependency of promotion inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .storage import encoded, compare_and_swap


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("JSON input must not be a symlink")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, text=True, capture_output=True).stdout.strip()


def _protected_snapshot(root: Path) -> dict:
    """Include ignored/untracked files and empty directories in protected data."""
    result = {}
    for relative in ("data", "models", "outputs", "work"):
        base = root / relative
        for path in [base, *sorted(base.rglob("*"))] if base.exists() else [base]:
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("protected repository tree contains a symlink")
            result[name] = (_sha(path.read_bytes()) if path.is_file()
                            else "DIRECTORY" if path.is_dir() else "MISSING")
    return result


def _audit_unchanged(root: Path, revision: str, snapshot: dict) -> None:
    if _head(root) != revision or _protected_snapshot(root) != snapshot:
        raise ValueError("inference changed HEAD or protected repository data")


def _guard(root: Path, output: Path) -> tuple[Path, Path]:
    root = root.resolve()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("rehearsal output must not follow symlinks")
    output = output.resolve()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError("rehearsal output must be outside the source repository")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("use a fresh empty rehearsal output directory")
    config = _json(root / "forward/config.json")
    if (config.get("production_enabled") is not False
            or config.get("activated_at_utc") is not None
            or config.get("legacy_statistics_import_allowed") is not False
            or config.get("formal_trade_actions_allowed") is not False):
        raise ValueError("rehearsal requires an inactive, non-trading migration config")
    return root, output


def _publish(output: Path, files: dict[str, dict], *, kind: str,
             source_revision: str, signal_date: str, inference_performed: bool) -> dict:
    # The receipt is last: interrupted writes are not accepted as a bundle.
    hashes = {}
    for name, content in files.items():
        hashes[name] = compare_and_swap(output / name, content, None)
    receipt = {"schema_version": "dc20_forward_rehearsal_receipt_v1",
               "kind": kind, "signal_date": signal_date,
               "source_revision": source_revision,
               "generation_mode": "REPLAY", "computation_completed": True,
               "inference_performed": inference_performed,
               "production_enabled": False, "forward_ledger_eligible": False,
               "formal_trade_count": 0, "files": hashes}
    compare_and_swap(output / "receipt.json", receipt, None)
    return receipt


def compute_promotion(root: Path, output: Path, signal_date: str) -> dict:
    root, output = _guard(root, output)
    revision, snapshot = _head(root), _protected_snapshot(root)
    # Lazy and separate: P0 never imports the profit engine or any daybook.
    from .promotion import compute_promotion_bundle
    bundle = compute_promotion_bundle(
        root, signal_date, generated_at_utc=datetime.now(timezone.utc).isoformat(),
        generation_mode="REPLAY")
    _audit_unchanged(root, revision, snapshot)
    return _publish(output, {"promotion.json": bundle["day"], "primary.json": bundle},
                    kind="PROMOTION_INFERENCE", source_revision=revision,
                    signal_date=signal_date, inference_performed=bool(bundle["day"]["rows"]))


def read_primary(directory: Path) -> tuple[dict, dict]:
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("primary bundle directory must not be a symlink")
    receipt = _json(directory / "receipt.json")
    if (receipt.get("kind") != "PROMOTION_INFERENCE"
            or receipt.get("generation_mode") != "REPLAY"
            or receipt.get("forward_ledger_eligible") is not False
            or receipt.get("production_enabled") is not False
            or set(receipt.get("files", {})) != {"promotion.json", "primary.json"}):
        raise ValueError("not a complete rehearsal promotion receipt")
    for filename, expected in receipt["files"].items():
        path = directory / filename
        if path.is_symlink() or _sha(path.read_bytes()) != expected:
            raise ValueError("primary bundle file hash mismatch")
    bundle = _json(directory / "primary.json")
    if bundle["day"] != _json(directory / "promotion.json"):
        raise ValueError("promotion day does not match runtime bundle")
    if bundle["day"]["signal_date"] != receipt["signal_date"]:
        raise ValueError("promotion receipt date mismatch")
    return bundle, receipt


def compute_profit(root: Path, primary: Path, output: Path) -> dict:
    root, output = _guard(root, output)
    bundle, promotion_receipt = read_primary(primary)
    revision, snapshot = _head(root), _protected_snapshot(root)
    if promotion_receipt["source_revision"] != revision:
        raise ValueError("profit checkout does not match promotion source revision")
    from .profit import infer_profit
    profit = infer_profit(root, bundle)
    _audit_unchanged(root, revision, snapshot)
    profit["rehearsal_promotion_receipt_sha256"] = _sha(encoded(promotion_receipt))
    return _publish(output, {"profit.json": profit}, kind="PROFIT_INFERENCE",
                    source_revision=revision, signal_date=bundle["day"]["signal_date"],
                    inference_performed=bool(profit["rows"]))


def compare_replay(root: Path, primary: Path, profit_directory: Path) -> dict:
    """Post-compute audit only: legacy outputs are never inference inputs."""
    from .engine import load_frozen_day
    bundle, p0_receipt = read_primary(primary)
    if _head(root) != p0_receipt["source_revision"]:
        raise ValueError("comparison checkout does not match inference revision")
    p1_receipt = _json(profit_directory / "receipt.json")
    if (p1_receipt.get("kind") != "PROFIT_INFERENCE"
            or p1_receipt.get("source_revision") != p0_receipt["source_revision"]
            or p1_receipt.get("signal_date") != p0_receipt["signal_date"]
            or set(p1_receipt.get("files", {})) != {"profit.json"}):
        raise ValueError("profit receipt identity mismatch")
    profit_file = profit_directory / "profit.json"
    profit = _json(profit_file)
    if (_sha(profit_file.read_bytes()) != p1_receipt["files"]["profit.json"]
            or profit.get("rehearsal_promotion_receipt_sha256") != _sha(encoded(p0_receipt))):
        raise ValueError("profit receipt binding mismatch")
    original = load_frozen_day(root, p0_receipt["signal_date"], generation_mode="REPLAY")
    actual = bundle["day"]
    if any(actual[key] != original[key] for key in ("signal_date", "exec_date", "exit_date")):
        raise ValueError("recomputed D/T/T1 differs from frozen source")
    if len(actual["rows"]) != len(original["rows"]) or len(profit["rows"]) != len(original["rows"]):
        raise ValueError("recomputed candidate count differs")
    by_code = {row["ts_code"]: row for row in profit["rows"]}
    if len(by_code) != len(profit["rows"]):
        raise ValueError("duplicate recomputed profit member")
    maximum = {"promotion_probability": 0.0, "path_change_pct": 0.0, "profit_score": 0.0}
    for old, new in zip(original["rows"], actual["rows"]):
        for field in ("ts_code", "name", "industry", "stage_transition", "promotion_rank", "path_label"):
            if old[field] != new[field]:
                raise ValueError(f"recomputed promotion identity/order/path mismatch: {field}")
        candidate = by_code.get(old["ts_code"])
        if candidate is None or candidate["profit_rank"] != old["profit_rank"]:
            raise ValueError("recomputed profit order differs")
        for field in maximum:
            x, y = old[field], (candidate[field] if field == "profit_score" else new[field])
            if x is None or y is None:
                if x is not y:
                    raise ValueError("recomputed missing path differs")
                continue
            difference = abs(x - y)
            if not math.isfinite(difference) or difference > 1e-12:
                raise ValueError(f"recomputed numeric value differs: {field}")
            maximum[field] = max(maximum[field], difference)
    return {"status": "RECOMPUTED_REPLAY_MATCH", "signal_date": original["signal_date"],
            "candidate_count": len(actual["rows"]), "source_revision": p0_receipt["source_revision"],
            "maximum_absolute_differences": maximum, "tolerance": 1e-12,
            "forward_ledger_days_added": 0, "production_cutover": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    promotion = sub.add_parser("promotion")
    promotion.add_argument("--signal-date", required=True)
    promotion.add_argument("--output", type=Path, required=True)
    profit = sub.add_parser("profit")
    profit.add_argument("--primary", type=Path, required=True)
    profit.add_argument("--output", type=Path, required=True)
    compare = sub.add_parser("compare", help="post-compute historical equivalence audit only")
    compare.add_argument("--primary", type=Path, required=True)
    compare.add_argument("--profit", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "promotion":
        result = compute_promotion(args.root, args.output, args.signal_date)
    elif args.command == "profit":
        result = compute_profit(args.root, args.primary, args.output)
    else:
        result = compare_replay(args.root, args.primary, args.profit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
