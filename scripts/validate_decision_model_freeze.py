#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.model_freeze import (  # noqa: E402
    load_model_freeze,
    load_verified_frozen_history_snapshot,
    model_freeze_active,
    validate_pinned_files,
    validate_runtime_artifacts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the active Decision production model freeze"
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="also verify canonical V2, behavior, and action-plan runtime contracts",
    )
    parser.add_argument(
        "--force-inactive",
        action="store_true",
        help="enforce a complete inactive V2 candidate in a disposable replay workspace",
    )
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="verify and read only the manifest-pinned snapshot; never use live history",
    )
    args = parser.parse_args()
    if args.history_only and args.runtime:
        parser.error("--history-only cannot be combined with --runtime")
    if args.force_inactive and not (args.history_only or args.runtime):
        parser.error("--force-inactive requires --history-only or --runtime")
    return args


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    manifest = load_model_freeze(root, required=True)
    payload = {
        "manifest": {
            "active": model_freeze_active(manifest),
            "freeze_id": str(manifest.get("freeze_id") or ""),
            "training_cutoff_signal_date": str(
                manifest.get("training_cutoff_signal_date") or ""
            ),
            "schema_version": str(manifest.get("schema_version") or ""),
        },
        "files": validate_pinned_files(
            root,
            manifest,
            force_enforcement=args.force_inactive,
        ),
        "enforcement": {
            "canonical_v2_enforced": bool(
                model_freeze_active(manifest) or args.force_inactive
            ),
            "legacy_v1_enforced": False,
            "forced_inactive": bool(
                args.force_inactive and not model_freeze_active(manifest)
            ),
        },
    }
    if args.history_only:
        _, history_audit = load_verified_frozen_history_snapshot(root, manifest)
        payload["history"] = history_audit
    if args.runtime:
        payload["runtime"] = validate_runtime_artifacts(
            root,
            manifest,
            force_enforcement=args.force_inactive,
        )
        if args.force_inactive:
            payload["history"] = payload["runtime"]["snapshot"]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
