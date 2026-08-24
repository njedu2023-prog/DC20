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

from top10decision.decision.executable_profit_shadow_settlement import (  # noqa: E402
    ExecutableProfitSettlementError,
    build_statistics,
    materialize_statistics,
    settle_signal_date,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append immutable public-market proxy truth for one frozen executable-profit "
            "Shadow date and deterministically rebuild its separate statistics"
        )
    )
    parser.add_argument("--root", default=str(ROOT), help="DC20 repository root")
    parser.add_argument(
        "--signal-date",
        default="",
        help="Exact frozen selection D in YYYYMMDD; no automatic historical backfill",
    )
    parser.add_argument(
        "--as-of-date",
        default="",
        help=(
            "Required pinned SSE cutoff in YYYYMMDD; no T/T+1 market truth "
            "after this date may be read"
        ),
    )
    parser.add_argument(
        "--statistics-only",
        action="store_true",
        help="Only validate immutable artifacts and rebuild deterministic statistics",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve(strict=True)
    try:
        if not args.as_of_date:
            raise ExecutableProfitSettlementError(
                "--as-of-date is required for an as-of-safe settlement/statistics run"
            )
        if args.statistics_only:
            statistics = build_statistics(root, as_of_date=args.as_of_date)
            path = materialize_statistics(root, statistics)
            result = {
                "status": "STATISTICS_REBUILT",
                "statistics_path": path.relative_to(root).as_posix(),
                "progress": statistics["forward_signal_date_progress_180"],
                "official_trade_action_created": False,
            }
        else:
            if not args.signal_date:
                raise ExecutableProfitSettlementError(
                    "--signal-date is required; automatic backfill is forbidden"
                )
            result = settle_signal_date(
                root,
                args.signal_date,
                as_of_date=args.as_of_date,
            )
            result["status"] = (
                "FINAL_SETTLED"
                if result["t1_settlement_path"] is not None
                else "PENDING_TRUTH_NOT_DROPPED"
            )
    except ExecutableProfitSettlementError as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "error": str(exc),
                    "official_trade_action_created": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
