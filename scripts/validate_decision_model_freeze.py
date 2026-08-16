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
        help="also verify freshly generated V12 runtime fingerprints",
    )
    return parser.parse_args()


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
        },
        "files": validate_pinned_files(root, manifest),
    }
    if args.runtime:
        payload["runtime"] = validate_runtime_artifacts(root, manifest)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
