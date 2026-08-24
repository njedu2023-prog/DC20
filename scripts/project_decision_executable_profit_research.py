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

from top10decision.decision.executable_profit_research_projection import (  # noqa: E402
    ExecutableProfitResearchProjectionError,
    build_and_materialize_research_projection,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Project the immutable executable-profit research order for public "
            "human decision support. This command does not load a model, rerank, "
            "create an order, or claim a calibrated probability."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--signal-date",
        required=True,
        help="Exact frozen selection D in YYYYMMDD",
    )
    parser.add_argument(
        "--as-of-date",
        required=True,
        help=(
            "Truth/statistics projection date A in YYYYMMDD; missing optional "
            "truth/statistics remains visible as null"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        (
            projection,
            statistics,
            projection_json,
            projection_csv,
            statistics_json,
            index_json,
            index,
        ) = build_and_materialize_research_projection(
            args.root.resolve(strict=True),
            args.signal_date,
            args.as_of_date,
        )
    except (
        ExecutableProfitResearchProjectionError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "error": str(exc),
                    "public_research_projection_allowed": False,
                    "formal_probability_allowed": False,
                    "formal_rank_allowed": False,
                    "official_trade_action_created": False,
                    "actual_execution_claimed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": projection["status"],
                "display_name": projection["display_name"],
                "signal_date": projection["signal_date"],
                "candidate_count": projection["candidate_count"],
                "shadow_actual_slots": projection["ranking_contract"][
                    "shadow_actual_slots"
                ],
                "statistics_available": statistics["statistics"] is not None,
                "projection_json": str(projection_json),
                "projection_csv": str(projection_csv),
                "statistics_json": str(statistics_json),
                "index_json": str(index_json),
                "index_signal_date": index["latest_signal_date"],
                "public_research_projection_allowed": True,
                "formal_probability_allowed": False,
                "formal_rank_allowed": False,
                "official_trade_action_created": False,
                "actual_execution_claimed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
