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

from top10decision.decision.executable_profit_shadow import (  # noqa: E402
    ExecutableProfitShadowError,
    score_and_materialize_internal_forward_shadow,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Score and freeze the NOT_READY internal executable-profit research "
            "Shadow. This command never publishes a front-end rank or trade action."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--d-features-csv",
        type=Path,
        required=True,
        help=(
            "Canonical D-frozen complete-pool Auction feature surface. The exact "
            "promotion TopN is selected from it; old feature-incomplete files fail "
            "closed and all 18 promotion-source features are required."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.root.resolve()
    feature_path = (
        args.d_features_csv
        if args.d_features_csv.is_absolute()
        else repo_root / args.d_features_csv
    ).resolve()
    try:
        payload, json_path, csv_path, index_path, pointer = (
            score_and_materialize_internal_forward_shadow(
                repo_root=repo_root,
                d_feature_path=feature_path,
            )
        )
        source_path = (
            repo_root
            / "outputs"
            / "decision"
            / f"three_rank_top10_{payload['signal_date']}.json"
        )
    except (
        ExecutableProfitShadowError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "INTERNAL_CHALLENGER_NOT_READY",
                    "research_only": True,
                    "front_end_rank_allowed": False,
                    "official_trade_action_allowed": False,
                    "materialized": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "research_only": True,
                "front_end_rank_allowed": False,
                "official_trade_action_allowed": False,
                "source_three_rank": str(source_path),
                "signal_date": payload["signal_date"],
                "snapshot_sha256": payload["snapshot_sha256"],
                "top2": payload["shadow_top2"]["rows"],
                "json": str(json_path),
                "csv": str(csv_path),
                "pointer": str(index_path),
                "pointer_signal_date": pointer["latest_signal_date"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
