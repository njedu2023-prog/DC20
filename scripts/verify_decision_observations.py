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

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine  # noqa: E402
from top10decision.decision.action_plan import (  # noqa: E402
    refresh_action_plan_observations,
)
from top10decision.decision.observation import (  # noqa: E402
    OBSERVATION_START_EXEC_DATE,
)
from top10decision.writers.io_contract import is_a_share_trading_day  # noqa: E402


def _date(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Settle Decision Top10 observation truth and cumulative statistics"
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--from-exec-date",
        default=OBSERVATION_START_EXEC_DATE,
        help="First T execution date included in cumulative truth, YYYYMMDD",
    )
    parser.add_argument(
        "--check-trading-date",
        default="",
        help="Only check the strict A-share calendar; exit 3 when closed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    check_date = _date(args.check_trading_date)
    if check_date:
        is_open = is_a_share_trading_day(check_date)
        print(
            json.dumps(
                {
                    "trade_date": check_date,
                    "is_a_share_trading_day": is_open,
                    "calendar": "strict_sse_snapshot",
                },
                ensure_ascii=False,
            )
        )
        return 0 if is_open else 3

    start_date = _date(args.from_exec_date) or OBSERVATION_START_EXEC_DATE
    root = Path(args.root).resolve()
    config = AuctionV3Config(
        root=root,
        observation_validation_start_date=start_date,
    )
    ledger, metrics = AuctionV3Engine(config).settle_observations()
    changed = refresh_action_plan_observations(root, start_date)
    print(
        json.dumps(
            {
                "status": metrics.get("status"),
                "validation_start_exec_date": start_date,
                "observation_rows": int(len(ledger)),
                "t_validated_rows": int(metrics.get("t_validated_rows", 0) or 0),
                "final_verified_trades": int(
                    metrics.get("final_verified_trades", 0) or 0
                ),
                "refreshed_action_plans": [
                    str(path.relative_to(root)) for path in changed
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
