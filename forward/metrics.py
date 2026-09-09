"""Cumulative evidence for this epoch only; unresolved slots are never zero."""
from __future__ import annotations

from .ledger import T_FINAL, T1_FINAL, _validate_ledger

GROUPS = {
    "promotion_top1": ("promotion_rank", {1}),
    "promotion_top2": ("promotion_rank", {2}),
    "promotion_top3": ("promotion_rank", {3}),
    "promotion_top3_combined": ("promotion_rank", {1, 2, 3}),
    "profit_top1": ("profit_rank", {1}),
    "profit_top2": ("profit_rank", {2}),
    "profit_top2_combined": ("profit_rank", {1, 2}),
}


def statistics(ledger):
    _validate_ledger(ledger)
    groups = {}
    for name, (rank_field, ranks) in GROUPS.items():
        item = {"frozen_slots": 0, "d_days": 0, "t_validated": 0,
                "promotion_hits": 0, "promotion_hit_rate": None,
                "t1_settled": 0, "no_fill_slots": 0, "conditional_trades": 0,
                "conditional_positive_net_rate": None, "mean_net_return": None,
                "resolved_slots": 0, "resolved_group_days": 0,
                "unresolved_slots": 0, "unresolved_group_days": 0,
                "equity_basis": "resolved_cohort_reference_only",
                "complete": False,
                "cumulative_return": None, "max_drawdown": None,
                "daily_equity": []}
        net_returns = []
        equity, peak, worst = 1.0, 1.0, 0.0
        for day in ledger["days"]:
            selected = [row for row in day["rows"] if row[rank_field] in ranks]
            if not selected:
                continue
            item["d_days"] += 1
            item["frozen_slots"] += len(selected)
            latest = day["verifications"][-1] if day["verifications"] else {"rows": []}
            truth = {row["ts_code"]: row for row in latest["rows"]}
            resolved = []
            for candidate in selected:
                row = truth.get(candidate["ts_code"])
                if row is None:
                    continue
                if row["t_status"] in T_FINAL:
                    item["t_validated"] += 1
                    item["promotion_hits"] += int(row["t_status"] == "PROMOTED")
                if row["t1_status"] in T1_FINAL:
                    item["resolved_slots"] += 1
                    resolved.append(row["slot_return"])
                if row["t1_status"] == "NO_FILL":
                    item["no_fill_slots"] += 1
                elif row["t1_status"] == "SETTLED":
                    item["t1_settled"] += 1
                    net_returns.append(row["net_return"])
            if len(resolved) == len(selected):
                daily_return = sum(resolved) / len(selected)
                equity *= 1 + daily_return
                peak = max(peak, equity)
                drawdown = equity / peak - 1
                worst = min(worst, drawdown)
                item["daily_equity"].append({"signal_date": day["signal_date"],
                                             "slots": len(selected),
                                             "daily_return": daily_return,
                                             "equity": equity,
                                             "drawdown": drawdown})
        item["conditional_trades"] = len(net_returns)
        if net_returns:
            item["conditional_positive_net_rate"] = sum(value > 0 for value in net_returns) / len(net_returns)
            item["mean_net_return"] = sum(net_returns) / len(net_returns)
        if item["t_validated"]:
            item["promotion_hit_rate"] = item["promotion_hits"] / item["t_validated"]
        item["resolved_group_days"] = len(item["daily_equity"])
        item["unresolved_slots"] = item["frozen_slots"] - item["resolved_slots"]
        item["unresolved_group_days"] = item["d_days"] - item["resolved_group_days"]
        item["complete"] = item["d_days"] > 0 and item["unresolved_group_days"] == 0
        if item["resolved_group_days"]:
            item["cumulative_return"] = equity - 1
            item["max_drawdown"] = worst
        groups[name] = item
    return {"epoch_id": ledger["epoch_id"], "activated_at_utc": ledger["activated_at_utc"],
            "start_signal_date": ledger["start_signal_date"], "groups": groups}
