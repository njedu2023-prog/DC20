#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine  # noqa: E402
from top10decision.decision.model_freeze import (  # noqa: E402
    apply_frozen_history_cutoff,
    capture_frozen_history_snapshot,
    load_model_freeze,
    load_frozen_history_snapshot,
    model_freeze_active,
    validate_pinned_files,
    validate_runtime_artifacts,
)


class FreezeAwareAuctionV3Engine(AuctionV3Engine):
    def __init__(self, config: AuctionV3Config, manifest: dict[str, object]):
        super().__init__(config)
        self.model_freeze_manifest = manifest
        self.model_freeze_history_audit: dict[str, object] = {}

    def build_history(self):
        frozen, audit = load_frozen_history_snapshot(
            self.config.root,
            self.model_freeze_manifest,
        )
        if frozen is not None:
            self.model_freeze_history_audit = audit
            return frozen
        history = super().build_history()
        history, audit = apply_frozen_history_cutoff(
            history,
            self.model_freeze_manifest,
        )
        history, audit = capture_frozen_history_snapshot(
            self.config.root,
            self.model_freeze_manifest,
            history,
        )
        self.model_freeze_history_audit = audit
        return history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Decision V12 observation Top10 plus independent trade "
            "selector with fixed T+1 09:30 exit and auction truth"
        )
    )
    parser.add_argument(
        "--signal-date",
        default="",
        help="D-day signal date, YYYYMMDD; default latest frozen pred source",
    )
    parser.add_argument("--root", default=str(ROOT), help="Repository root")
    parser.add_argument(
        "--force-prediction",
        action="store_true",
        help="Replace a dated prediction snapshot; disabled by default",
    )
    parser.add_argument(
        "--order-amount",
        type=float,
        default=100_000.0,
        help="Reference amount used only by auction-capacity simulation; no order is sent",
    )
    parser.add_argument(
        "--round-trip-cost-bps",
        type=float,
        default=35.0,
        help="Commission, taxes and fees excluding modeled slippage",
    )
    parser.add_argument("--slippage-bps-each-side", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    manifest = load_model_freeze(root, required=True)
    validate_pinned_files(root, manifest)
    config = AuctionV3Config(
        root=root,
        order_amount_cny=max(0.0, args.order_amount),
        round_trip_cost_bps=max(0.0, args.round_trip_cost_bps),
        slippage_bps_each_side=max(0.0, args.slippage_bps_each_side),
    )
    engine: AuctionV3Engine
    if model_freeze_active(manifest):
        engine = FreezeAwareAuctionV3Engine(config, manifest)
    else:
        engine = AuctionV3Engine(config)
    result = engine.run(
        args.signal_date,
        force_prediction=args.force_prediction,
    )
    runtime_audit = validate_runtime_artifacts(
        root,
        manifest,
        check_action_plan=False,
    )
    print(
        json.dumps(
            {
                "result": asdict(result),
                "model_freeze": {
                    **runtime_audit,
                    "history": getattr(
                        engine,
                        "model_freeze_history_audit",
                        {},
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
