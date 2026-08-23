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

from top10decision.decision.research_context import publish_research_context  # noqa: E402
from top10decision.decision.research_context import HISTORICAL_PARITY_SCHEMA  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a date-bound, non-action DC2.0 Daily research context"
    )
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--output-root", default=str(ROOT))
    parser.add_argument("--report-date", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path, context = publish_research_context(
        Path(args.source_root),
        Path(args.output_root),
        args.report_date,
    )
    preserved_historical = context.get("schema_version") == HISTORICAL_PARITY_SCHEMA
    print(
        json.dumps(
            {
                "path": str(path),
                "report_date": context["report_date"],
                "signal_date": context["signal_date"],
                "stage_watch_count": context.get("stage_watch_count"),
                "candidate_count": (
                    len(context.get("candidates", []))
                    if not preserved_historical
                    else None
                ),
                "action_authorized": context["action_authorized"],
                "preserved_historical": preserved_historical,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
