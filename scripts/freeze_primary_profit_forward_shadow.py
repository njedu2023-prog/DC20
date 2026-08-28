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

from top10decision.decision.primary_profit_forward_shadow_bridge import (  # noqa: E402
    PrimaryProfitForwardShadowError,
    freeze_primary_profit_forward_shadow,
    project_primary_profit_forward_shadow_state,
    validate_primary_profit_forward_shadow_repository_chain,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze already-published P1 mixed Top1/Top2 as forward Shadow, "
            "rebuild its separate statistics and publish an immutable public "
            "sidecar. This command never re-runs a model or creates Action."
        )
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--signal-date",
        required=True,
        help="Exact natural P1 signal D in YYYYMMDD; historical recovery is forbidden",
    )
    parser.add_argument(
        "--as-of-date",
        default="",
        help=(
            "Optional existing-selection truth cutoff in YYYYMMDD. When supplied, "
            "only rebuild statistics/public sidecar; selection bytes remain immutable."
        ),
    )
    return parser.parse_args()


def _relative(root: Path, value: Path) -> str:
    return value.resolve(strict=True).relative_to(root).as_posix()


def main() -> int:
    args = parse_args()
    try:
        root = args.root.resolve(strict=True)
        if args.as_of_date:
            projected = project_primary_profit_forward_shadow_state(
                root,
                args.signal_date,
                args.as_of_date,
            )
            chain = validate_primary_profit_forward_shadow_repository_chain(
                root,
                args.signal_date,
            )
            result = {
                "status": "PRIMARY_MIXED_FORWARD_SHADOW_STATE_PROJECTED",
                "signal_date": chain["signal_date"],
                "as_of_date": chain["as_of_date"],
                "selected_slots": chain["selected_slots"],
                "statistics": _relative(root, projected["statistics"]),
                "public_state": _relative(root, projected["public_state"]),
                "public_index": _relative(root, projected["public_index"]),
                "model_rescored": False,
                "promotion_membership_or_rank_changed": False,
                "official_trade_action_created": False,
            }
        else:
            frozen = freeze_primary_profit_forward_shadow(
                root,
                args.signal_date,
            )
            payload = frozen["payload"]
            chain = frozen["chain"]
            result = {
                "status": "PRIMARY_MIXED_FORWARD_SHADOW_FROZEN",
                "signal_date": chain["signal_date"],
                "exec_date": payload["exec_date"],
                "exit_date": payload["exit_date"],
                "as_of_date": chain["as_of_date"],
                "candidate_count": payload["top10_count"],
                "selected_slots": chain["selected_slots"],
                "top1_top2": payload["shadow_top2"]["rows"],
                "selection_json": _relative(root, frozen["selection_json"]),
                "selection_csv": _relative(root, frozen["selection_csv"]),
                "selection_index": _relative(root, frozen["selection_index"]),
                "statistics": _relative(root, frozen["statistics"]),
                "public_state": _relative(root, frozen["public_state"]),
                "public_index": _relative(root, frozen["public_index"]),
                "model_rescored": False,
                "promotion_membership_or_rank_changed": False,
                "official_trade_action_created": False,
            }
    except (PrimaryProfitForwardShadowError, OSError, UnicodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "error": str(exc),
                    "model_rescored": False,
                    "promotion_membership_or_rank_changed": False,
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
