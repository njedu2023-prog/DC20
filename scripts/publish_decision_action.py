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

from top10decision.decision.action_plan import (  # noqa: E402
    publish_action_plan,
    refresh_action_plan_observations,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish the unified Decision action-plan and latest-report index")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--report-date", default="", help="Execution/report date YYYYMMDD; defaults to latest report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    dated, latest, index, plan = publish_action_plan(root, args.report_date)
    refreshed = refresh_action_plan_observations(root)
    model = plan.get("model") or {}
    print(
        json.dumps(
            {
                "dated": str(dated),
                "latest": str(latest),
                "index": str(index),
                "status_code": plan.get("status_code"),
                "formal_buy_count": plan.get("formal_buy_count"),
                "v2_integrity_match": model.get("v2_integrity_match"),
                "v2_eligibility_match": model.get("v2_eligibility_match"),
                "artifact_v2_fingerprints_match": model.get(
                    "artifact_v2_fingerprints_match"
                ),
                "fingerprint_v2_valid": model.get("fingerprint_v2_valid"),
                "trade_selector_artifacts_v2_match": model.get(
                    "trade_selector_artifacts_v2_match"
                ),
                "trade_selector_fingerprint_v2_valid": model.get(
                    "trade_selector_fingerprint_v2_valid"
                ),
                "observation_action_plans_refreshed": len(refreshed),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
