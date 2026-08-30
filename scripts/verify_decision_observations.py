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
from top10decision.decision.observation import (  # noqa: E402
    OBSERVATION_START_EXEC_DATE,
    PUBLIC_STATISTICS_START_SIGNAL_DATE,
)
from top10decision.writers.io_contract import is_a_share_trading_day  # noqa: E402


def _date(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _public_observation_metrics(
    engine: AuctionV3Engine,
    ledger: object,
) -> dict[str, object]:
    """Project public cumulative truth without changing model runtime bytes."""

    source_rows = int(len(ledger))
    public_ledger = ledger.copy()
    if not public_ledger.empty:
        if "signal_date" not in public_ledger.columns:
            raise ValueError("observation ledger is missing signal_date")
        public_ledger["signal_date"] = public_ledger["signal_date"].map(_date)
        public_ledger = public_ledger[
            public_ledger["signal_date"].ge(PUBLIC_STATISTICS_START_SIGNAL_DATE)
        ].copy()

    metrics = engine._observation_metrics(public_ledger)
    metrics["public_start_signal_date"] = PUBLIC_STATISTICS_START_SIGNAL_DATE
    metrics["historical_ledger_retained"] = True
    metrics["excluded_pre_cutover_rows"] = source_rows - int(len(public_ledger))
    metrics.setdefault("observation_dates", 0)
    for field in (
        "t_validated_rows",
        "t_pending_rows",
        "premarket_valid_rows",
        "premarket_validated_rows",
        "retrospective_truth_rows",
        "unknown_timing_truth_rows",
        "official_auction_truth_rows",
        "minute_proxy_truth_rows",
        "daily_open_proxy_truth_rows",
        "final_verified_trades",
        "matured_portfolio_dates",
    ):
        metrics.setdefault(field, 0)
    metrics.setdefault("stage_2_to_3", {"samples": 0, "hits": 0, "hit_rate": None})
    metrics.setdefault("stage_3_to_4", {"samples": 0, "hits": 0, "hit_rate": None})
    metrics.setdefault("top3_continuation", {"samples": 0, "hits": 0, "hit_rate": None})
    metrics.setdefault(
        "all_truth_summary",
        {
            "t_validated_rows": 0,
            "fillable_rows": 0,
            "final_verified_trades": 0,
            "final_win_rate": None,
            "mean_final_net_return": None,
        },
    )
    metrics.setdefault("daily_portfolio", [])
    metrics.setdefault("path_performance", {})
    metrics.setdefault(
        "trading_date_windows",
        {
            label: {
                "portfolio_dates": 0,
                "filled_trades": 0,
                "mean_net_return": None,
                "win_rate": None,
                "equal_slot_cumulative_return": None,
            }
            for label in ("20", "60", "all")
        },
    )
    top1 = dict(metrics.get("top1_continuation") or {})
    top1.update(
        {
            "start_signal_date": PUBLIC_STATISTICS_START_SIGNAL_DATE,
            "rank_field": "promotion_rank",
            "rank_value": 1,
        }
    )
    top1.setdefault("samples", 0)
    top1.setdefault("hits", 0)
    top1.setdefault("hit_rate", None)
    metrics["top1_continuation"] = top1
    forward = dict(metrics.get("forward_shadow") or {})
    forward["start_signal_date"] = PUBLIC_STATISTICS_START_SIGNAL_DATE
    metrics["forward_shadow"] = forward
    return metrics


def _write_public_observation_metrics(root: Path, metrics: dict[str, object]) -> None:
    path = root / "outputs/auction_v3/metrics/observation_cumulative_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


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
    engine = AuctionV3Engine(config)
    ledger, _ = engine.settle_observations()
    metrics = _public_observation_metrics(engine, ledger)
    _write_public_observation_metrics(root, metrics)
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
