#!/usr/bin/env python3
"""Read-only P0 truth projection. Never creates selections, actions or orders.

This is a separate *daily-open observation proxy*, not the auction-cap Shadow
ledger. Missing data stays missing. Existing dated P0 receipts are the only
membership authority; a late recovery can never become a forward prediction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.publish_primary_three_rank import build_primary_d_runtime_index
from top10decision.decision.executable_profit_shadow_settlement import (
    CALENDAR_PATH, CALENDAR_SHA256, COST_RATE, _find_market_file, _market_rows,
    _strict_open_dates, _validate_adjacent_dates,
)

START = "20260828"
SCHEMA = "dc20_primary_observation_summary_v1"
OUT = Path("outputs/decision/primary_observation")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe/missing input: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tick(value):
    value = number(value)
    return int(math.floor(value * 100 + .5)) if value is not None else None


def rate(values):
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def observation_row(frozen, d, t, t1, asof, tables, exit_sessions=None):
    """No call can read a market table after asof (including pending exits)."""
    row = {k: frozen.get(k) for k in ("ts_code", "name", "industry", "stage_transition", "promotion_rank", "path_label")}
    row.update(signal_date=d, exec_date=t, exit_date=t1, validation_status="PENDING_T",
               continuation_limit_up_hit=None, market_daily_return=None,
               observation_t_return=None, proxy_fill=None, actual_net_return=None,
               slot_net_return=None, truth_source="daily_open_proxy", actual_order_fill_observed=False)
    if t > asof:
        return row
    code = row["ts_code"]
    daily, limit = tables(t, "daily").get(code), tables(t, "stk_limit").get(code)
    if not daily or not limit:
        row["validation_status"] = "MISSING_T_TRUTH"
        return row
    op, close, pre, vol, up = [number(v) for v in (
        daily.get("open"), daily.get("close"), daily.get("pre_close"), daily.get("vol"), limit.get("up_limit"))]
    if any(v is None or v <= 0 for v in (op, close, pre, up)) or vol is None or vol < 0:
        row["validation_status"] = "MISSING_T_TRUTH"
        return row
    row.update(continuation_limit_up_hit=int(tick(close) == tick(up)),
               market_daily_return=close / pre - 1, observation_t_return=close / op - 1,
               proxy_fill=int(vol > 0 and tick(op) < tick(up)), t_open=op, t_close=close)
    if t1 > asof:
        row["validation_status"] = "PENDING_T1"
        return row
    if not row["proxy_fill"]:
        row.update(validation_status="FINAL_NO_FILL_PROXY", slot_net_return=0.0)
        return row
    # Predeclared exit: first tradable open from strict T+1, never the best
    # later price. Missing intervening sessions cannot be skipped.
    value = close / op
    blocked = 0
    sessions = [s for s in (exit_sessions or [t1]) if t1 <= s <= asof]
    for session in sessions:
        exit_daily, exit_limit = tables(session, "daily").get(code), tables(session, "stk_limit").get(code)
        if not exit_daily or not exit_limit:
            row["validation_status"] = "MISSING_T1_TRUTH"
            return row
        exit_op, exit_pre, exit_vol, down, exit_close = [number(v) for v in (
            exit_daily.get("open"), exit_daily.get("pre_close"), exit_daily.get("vol"),
            exit_limit.get("down_limit"), exit_daily.get("close"))]
        if any(v is None or v <= 0 for v in (exit_op, exit_pre, down, exit_close)) or exit_vol is None or exit_vol < 0:
            row["validation_status"] = "MISSING_T1_TRUTH"
            return row
        if exit_vol <= 0 or tick(exit_op) <= tick(down):
            blocked += 1
            value *= exit_close / exit_pre
            continue
        gross = value * exit_op / exit_pre - 1
        row.update(validation_status="FINAL_VERIFIED_PROXY", actual_net_return=gross - COST_RATE,
                   slot_net_return=gross - COST_RATE, actual_exit_date=session,
                   actual_exit_price=exit_op, blocked_exit_sessions=blocked, gross_return=gross)
        return row
    row.update(validation_status="UNRESOLVED_EXIT_PROXY", blocked_exit_sessions=blocked)
    return row


def summarize(rows, daily_summaries, excluded):
    verified = [r for r in rows if r["continuation_limit_up_hit"] is not None]
    finals = [r["actual_net_return"] for r in rows if r["actual_net_return"] is not None]
    def hit(group):
        return {"samples": len(group), "hits": sum(r["continuation_limit_up_hit"] for r in group),
                "hit_rate": rate([r["continuation_limit_up_hit"] for r in group])}
    portfolios = []
    for d in daily_summaries:
        group = [r for r in rows if r["signal_date"] == d["signal_date"]]
        if group and all(r["slot_net_return"] is not None for r in group):
            portfolios.append({"signal_date": d["signal_date"], "exec_date": d["exec_date"],
                               "equal_slot_net_return": mean(r["slot_net_return"] for r in group)})
    nav = peak = 1.0
    drawdown = 0.0
    for p in portfolios:
        nav *= 1 + p["equal_slot_net_return"]
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1)
    losses, wins = -sum(v for v in finals if v < 0), sum(v for v in finals if v > 0)
    counts = {key: sum(d[key] for d in daily_summaries) for key in (
        "pending_t_rows", "pending_t1_rows", "missing_t_truth_rows", "missing_t1_truth_rows", "unresolved_exit_rows")}
    # A subset of closed cohorts is not the full-window portfolio, and a
    # delayed exit can overlap the next cohort's capital. Never publish that
    # synthetic compounding as a realizable equity curve.
    incomplete = counts["missing_t_truth_rows"] + counts["missing_t1_truth_rows"] + counts["unresolved_exit_rows"]
    delayed = any(r.get("blocked_exit_sessions", 0) for r in rows)
    curve_allowed = bool(portfolios) and not incomplete and not delayed
    portfolio_reason = ("UNRESOLVED_OR_MISSING_MATURE_TRUTH" if incomplete else
                        "DELAYED_EXIT_CAPITAL_OVERLAP_NOT_MODELED" if delayed else
                        "NO_COMPLETE_COHORT" if not portfolios else "SYNTHETIC_PER_D_RESEARCH_ONLY_NOT_CAPITAL_NAV")
    paths = {}
    for label in sorted({str(r.get("path_label") or "路径数据不足") for r in rows}):
        group = [r for r in verified if str(r.get("path_label") or "路径数据不足") == label]
        returns = [r["actual_net_return"] for r in group if r["actual_net_return"] is not None]
        paths[label] = dict(label=label, t_validated_rows=len(group),
            continuation_hit_rate=hit(group)["hit_rate"], final_verified_trades=len(returns),
            mean_final_net_return=rate(returns), win_rate=rate([int(v > 0) for v in returns]))
    return dict(observation_dates=len(daily_summaries), observation_rows=len(rows), path_performance=paths,
                premarket_valid_rows=len(rows), t_validated_rows=len(verified),
                t_pending_rows=len(rows) - len(verified), **counts,
                excluded_retrospective_rows=excluded, final_verified_trades=len(finals),
                daily_open_proxy_truth_rows=len(verified),
                matured_portfolio_dates=len(portfolios), continuation_hit_rate=hit(verified)["hit_rate"],
                stage_2_to_3=hit([r for r in verified if r["stage_transition"] == "2→3"]),
                stage_3_to_4=hit([r for r in verified if r["stage_transition"] == "3→4"]),
                top1_continuation=hit([r for r in verified if r["promotion_rank"] == 1]),
                top3_continuation=hit([r for r in verified if r["promotion_rank"] <= 3]),
                market_positive_rate=rate([int(r["market_daily_return"] > 0) for r in verified]),
                mean_market_daily_return=rate([r["market_daily_return"] for r in verified]),
                mean_t_observation_return=rate([r["observation_t_return"] for r in verified]),
                observation_fill_rate=rate([r["proxy_fill"] for r in verified]),
                final_win_rate=rate([int(v > 0) for v in finals]), mean_final_net_return=rate(finals),
                median_final_net_return=median(finals) if finals else None,
                worst_final_net_return=min(finals) if finals else None,
                tail_10pct_mean_return=mean(sorted(finals)[:max(1, math.ceil(len(finals) * .1))]) if finals else None,
                profit_factor=wins / losses if losses else None,
                equal_slot_cumulative_return=nav - 1 if curve_allowed else None,
                equal_slot_max_drawdown=drawdown if curve_allowed else None,
                portfolio_curve_reason=portfolio_reason, portfolio_is_capital_nav=False,
                daily_portfolio=portfolios)


def build(root, asof):
    root = root.resolve()
    dates = _strict_open_dates(root)
    if asof not in dates:
        raise ValueError("as-of must be an exact committed SSE open date")
    rows, daily_summaries, bindings, excluded = [], [], [], 0
    source_files, cache = {}, {}
    def tables(date, name):
        if date > asof:
            raise ValueError("future truth read forbidden")
        key = (date, name)
        if key not in cache:
            path = _find_market_file(root, date, name)
            cache[key] = _market_rows(path, date) if path else {}
            if path:
                source_files[str(path.relative_to(root))] = digest(path)
        return cache[key]
    for receipt_path in sorted((root / "outputs/decision").glob("primary_d_receipt_20??????.json")):
        d = receipt_path.stem.removeprefix("primary_d_receipt_")
        if not START <= d <= asof:
            continue
        base = root / "outputs/decision"
        bound = build_primary_d_runtime_index(root, receipt_path=receipt_path,
            runtime_path=base / f"primary_d_runtime_features_{d}.csv",
            three_rank_json_path=base / f"three_rank_top10_{d}.json",
            three_rank_csv_path=base / f"three_rank_top10_{d}.csv")
        receipt = json.loads(receipt_path.read_text())
        contract = json.loads((base / f"three_rank_top10_{d}.json").read_text())
        t, t1 = contract["exec_date"], contract["exit_date"]
        _validate_adjacent_dates(dates, d, t, t1)
        if receipt["inputs"]["calendar"]["sha256"] != CALENDAR_SHA256:
            raise ValueError("P0 calendar binding changed")
        generated = datetime.fromisoformat(contract["generated_at_utc"].replace("Z", "+00:00"))
        deadline = datetime.strptime(t, "%Y%m%d").replace(hour=9, minute=25, tzinfo=ZoneInfo("Asia/Shanghai"))
        if generated.tzinfo is None:
            raise ValueError("P0 generation timestamp must be timezone aware")
        natural = (receipt.get("generation_mode") == "NATURAL" and receipt.get("prospective") is True
                   and receipt.get("forward_eligible") is True and receipt.get("not_forward_generated") is False
                   and generated < deadline)
        if not natural:
            excluded += contract["top10_count"]
            bindings.append({"signal_date": d, "status": "EXCLUDED_RETROSPECTIVE", "p0": bound})
            continue
        with (base / f"primary_d_runtime_features_{d}.csv").open(encoding="utf-8-sig", newline="") as handle:
            runtime = {r["ts_code"]: r for r in csv.DictReader(handle)}
        day = [observation_row(dict(r, path_label=runtime[r["ts_code"]].get("path_label")),
                               d, t, t1, asof, tables, dates) for r in contract["rows"]]
        rows.extend(day)
        statuses = [r["validation_status"] for r in day]
        daily_summaries.append(dict(signal_date=d, exec_date=t, exit_date=t1, rows=len(day),
            t_validated_rows=sum(r["continuation_limit_up_hit"] is not None for r in day),
            final_verified_trades=sum(r["actual_net_return"] is not None for r in day),
            pending_t_rows=statuses.count("PENDING_T"), pending_t1_rows=statuses.count("PENDING_T1"),
            missing_t_truth_rows=statuses.count("MISSING_T_TRUTH"),
            missing_t1_truth_rows=statuses.count("MISSING_T1_TRUTH"),
            unresolved_exit_rows=statuses.count("UNRESOLVED_EXIT_PROXY"),
            settled_rows=sum(r["slot_net_return"] is not None for r in day)))
        bindings.append({"signal_date": d, "status": "PROSPECTIVE", "p0": bound})
    if not daily_summaries:
        raise ValueError("no validated natural P0 receipts in public window; refusing a fake zero summary")
    latest = daily_summaries[-1]
    stats = summarize(rows, daily_summaries, excluded)
    missing = stats["missing_t_truth_rows"] + stats["missing_t1_truth_rows"] + stats["unresolved_exit_rows"]
    payload = dict(schema_version=SCHEMA, scope="frozen_primary_topn", public_start_signal_date=START,
        status="PARTIAL_TRUTH" if missing else "PENDING_DATES" if stats["pending_t_rows"] + stats["pending_t1_rows"] else "READY",
        as_of_date=asof, latest_signal_date=latest["signal_date"], latest_exec_date=latest["exec_date"], latest_exit_date=latest["exit_date"],
        statistics=stats, daily_summaries=daily_summaries, bindings=bindings,
        calendar={"path": str(CALENDAR_PATH), "sha256": CALENDAR_SHA256},
        source_files=[{"path": p, "sha256": s} for p, s in sorted(source_files.items())],
        policy={"id": "p0_daily_open_observation_proxy_v1", "round_trip_cost_rate": COST_RATE,
                "performance_role": "historical_reconstruction_of_frozen_P0_predictions",
                "predictions_are_prospective": True,
                "return_policy_was_frozen_on_signal_D": False,
                "return_strategy_forward_evidence": False,
                "policy_definition_date": "20260905", "reconstruction_as_of_date": asof,
                "entry": "positive volume and daily open strictly below up limit; not an auction fill claim",
                "exit": "first tradable open from strict T+1, above down limit; never skip missing intervening truth",
                "corporate_action_adjustment": "chain adjusted close/pre_close through blocked sessions, exit at first tradable open/pre_close",
                "missing_truth_is_zero_return": False, "mixed_shadow_ledger_included": False,
                "retrospective_recovery_included": False, "official_trade_action_created": False})
    return payload, rows


def csv_bytes(rows):
    out = io.StringIO(newline="")
    fields = sorted({k for r in rows for k in r}) or ["signal_date", "ts_code"]
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--as-of-date", required=True)
    p.add_argument("--validate-existing", action="store_true")
    args = p.parse_args()
    payload, rows = build(args.root, args.as_of_date)
    raw = csv_bytes(rows)
    payload["rows_path"] = str(OUT / "rows.csv")
    payload["rows_sha256"] = hashlib.sha256(raw).hexdigest()
    directory = args.root / OUT
    if args.validate_existing:
        stored = json.loads((directory / "summary.json").read_text())
        generated = stored.pop("generated_at_utc", None)
        if not generated or stored != payload or (directory / "rows.csv").read_bytes() != raw:
            raise ValueError("primary observation candidate does not match exact frozen inputs/as-of")
        print("PASS primary observation exact-input recomputation")
        return
    if (directory / "summary.json").exists():
        stored = json.loads((directory / "summary.json").read_text())
        if stored["as_of_date"] > args.as_of_date:
            raise ValueError("primary observation pointer cannot regress")
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "rows.csv").write_bytes(raw)
    (directory / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": payload["status"], "as_of_date": args.as_of_date, "statistics": payload["statistics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
