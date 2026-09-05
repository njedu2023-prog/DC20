#!/usr/bin/env python3
"""Verify frozen forecast inputs without retraining the unrelated Auction model.

Truth settlement consumes immutable P0/P1 forecasts, not a newly replayed
Auction action.  This gate validates that complete input chain and every frozen
Shadow selection.  Legacy-only repositories retain their existing full replay;
an incomplete or corrupt primary chain must never fall back to that mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.publish_primary_profit_rankings import (
    MIXED_INDEX_SCHEMA,
    load_primary_inputs,
    validate_primary_profit_bundle,
)
from scripts.publish_primary_three_rank import (
    build_primary_d_runtime_index,
    load_strict_sse_dates,
    validate_primary_d_runtime_index,
)
from top10decision.decision.executable_profit_shadow_settlement import load_selection
from top10decision.decision.model_freeze import (
    load_model_freeze,
    model_freeze_active,
    validate_pinned_files,
)
from top10decision.decision.primary_profit_forward_shadow_bridge import (
    validate_primary_profit_forward_shadow_repository_chain,
)

INDEX = Path("outputs/decision/primary_d_runtime_index.json")
DATE_RE = re.compile(r"20\d{6}")
START_D = "20260828"


def _json_file(root: Path, relative: Path) -> dict:
    path = root / relative
    if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
        raise ValueError(f"Verify forecast file is missing or unsafe: {relative}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Verify forecast file must be one object: {relative}")
    return payload


def validate_forecast_inputs(
    root: Path, *, as_of_date: str, require_active: bool = True,
    expected_base_sha: str = "",
) -> dict:
    root = root.resolve(strict=True)
    if not DATE_RE.fullmatch(as_of_date):
        raise ValueError("Verify as-of date must be YYYYMMDD")
    if expected_base_sha:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", expected_base_sha) or head != expected_base_sha:
            raise ValueError("Verify forecast workspace is not the exact captured base")
    manifest = load_model_freeze(root, required=True)
    if require_active and not model_freeze_active(manifest):
        raise ValueError("real Verify requires active production freeze")
    pins = validate_pinned_files(root, manifest, force_enforcement=True)
    if pins.get("enforced") is not True:
        raise ValueError("Verify forecast inputs require enforced source/model pins")
    # Also validates the complete strict SSE calendar and the as-of session.
    load_strict_sse_dates(root, as_of_date)
    receipts = sorted((root / "outputs/decision").glob("primary_d_receipt_*.json"))
    if not (root / INDEX).exists() and not (root / INDEX).is_symlink():
        mixed_path = root / "outputs/decision/executable_profit_research/index.json"
        mixed = _json_file(root, mixed_path.relative_to(root)) if mixed_path.exists() else {}
        if receipts or mixed.get("schema_version") == MIXED_INDEX_SCHEMA:
            raise ValueError("primary forecast chain is orphaned; legacy fallback forbidden")
        return {"status": "pass", "mode": "LEGACY_AUCTION", "pins_enforced": True}

    index = _json_file(root, INDEX)
    validate_primary_d_runtime_index(index)
    date = index["latest_signal_date"]
    if date > as_of_date:
        raise ValueError("Verify as-of predates the primary forecast pointer")
    actual = build_primary_d_runtime_index(
        root,
        receipt_path=root / index["latest_receipt_url"],
        runtime_path=root / index["latest_runtime_features_url"],
        three_rank_json_path=root / index["latest_three_rank_json_url"],
        three_rank_csv_path=root / index["latest_three_rank_csv_url"],
    )
    if actual != index:
        raise ValueError("Verify primary pointer differs from exact receipt/runtime/TopN")
    bundle = validate_primary_profit_bundle(root, expected_signal_date=date)
    checked_dates = []
    all_dates = []
    for receipt_path in receipts:
        match = re.fullmatch(r"primary_d_receipt_(20\d{6})\.json", receipt_path.name)
        if match is None:
            raise ValueError("Verify primary receipt filename is invalid")
        d = match.group(1)
        all_dates.append(d)
        if d < START_D:
            continue
        if d > date or d > as_of_date:
            raise ValueError("Verify primary pointer omits a later dated receipt")
        receipt = _json_file(root, receipt_path.relative_to(root))
        inputs = load_primary_inputs(root, d, receipt.get("generation_mode"))
        t, exit_date, _ = load_strict_sse_dates(root, d)
        if inputs.three_rank["exec_date"] != t or inputs.three_rank["exit_date"] != exit_date:
            raise ValueError("Verify primary forecast D/T/T+1 differs from strict SSE calendar")
        checked_dates.append(d)
    if not all_dates or max(all_dates) != date:
        raise ValueError("Verify primary pointer is not latest complete D")

    selection_root = root / "data/decision_executable_profit/forward/selections"
    selection_dates = []
    for path in sorted(selection_root.glob("shadow_*.json")):
        match = re.fullmatch(r"shadow_(20\d{6})\.json", path.name)
        if match is None:
            raise ValueError("Verify Shadow selection filename is invalid")
        d = match.group(1)
        _, selection, _ = load_selection(root, d)
        t, exit_date, _ = load_strict_sse_dates(root, d)
        if selection["exec_date"] != t or selection["exit_date"] != exit_date:
            raise ValueError("Verify Shadow D/T/T+1 differs from strict SSE calendar")
        if d > as_of_date:
            raise ValueError("Verify as-of predates frozen Shadow selection")
        selection_dates.append(d)
    # A retrospective P1 must not invent forward slots.  Existing natural slots
    # remain strict and independently verifiable in either mode.
    if bundle["mixed"]["index"]["prospective"]:
        if not selection_dates or max(selection_dates) != date:
            raise ValueError("latest natural mixed Top1/Top2 has no exact-D frozen Shadow")
        validate_primary_profit_forward_shadow_repository_chain(root, date)
    elif selection_dates:
        validate_primary_profit_forward_shadow_repository_chain(root, max(selection_dates))
    return {
        "status": "pass", "mode": "PRIMARY_FROZEN_FORECASTS",
        "as_of_date": as_of_date, "latest_signal_date": date,
        "primary_dates_validated": checked_dates,
        "shadow_dates_validated": selection_dates,
        "pins_enforced": True, "action_input_consumed": False,
        "forecast_model_retrained": False,
        "primary_index_sha256": hashlib.sha256((root / INDEX).read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--expected-base-sha", default="")
    parser.add_argument("--force-inactive", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        payload = validate_forecast_inputs(
            Path(args.root), as_of_date=args.as_of_date,
            require_active=not args.force_inactive,
            expected_base_sha=args.expected_base_sha,
        )
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        payload = {"status": "fail", "error": str(exc)}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if payload["status"] != "pass":
        return 1
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write(f"contract_mode={payload['mode']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
