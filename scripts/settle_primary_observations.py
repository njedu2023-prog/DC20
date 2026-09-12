#!/usr/bin/env python3
"""Read-only P0 truth projection. Never creates selections, actions or orders.

This is a separate *daily-open entry observation proxy*, not the auction-cap
Shadow ledger. Exit policies are versioned; missing data stays missing.
Existing dated P0 receipts are the only
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
VERSIONED_SCHEMA = "dc20_primary_observation_summary_v2"
LEGACY_EXIT_POLICY_ID = "p0_daily_open_observation_proxy_v1"
EXIT_1000_POLICY_ID = "dc20_exit_1000_limit_hold_20260912_v1"
EXIT_1000_EFFECTIVE_DATE = "20260914"
VERSIONED_POLICY_ID = "p0_daily_open_entry_versioned_exit_observation_proxy_v2"
MINUTE_TRUTH_SOURCE = "daily_open_entry_minute_exit_proxy"
EXIT_POLICY_CONFIG_PATH = "models/decision_shadow_exit_policy_1000_v1.json"
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


def _exit_policy_binding(root):
    path = root / EXIT_POLICY_CONFIG_PATH
    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents) if p != root):
        raise ValueError("unsafe exit policy configuration")
    sha = digest(path)
    policy = json.loads(path.read_bytes())
    expected = {
        "schema_version": "dc20_shadow_exit_policy_v1", "policy_id": EXIT_1000_POLICY_ID,
        "effective_scheduled_exit_date": EXIT_1000_EFFECTIVE_DATE, "timezone": "Asia/Shanghai",
        "ordinary_exit_decision_time": "10:00:00",
        "first_sell_eligible_session": "T_PLUS_1_STRICT_EXCHANGE_SESSION",
        "limit_up_hold": "HOLD_WHILE_OBSERVED_SEALED_USING_EACH_SESSION_LIMIT",
        "seal_break_exit": "FIRST_OBSERVED_BREAK_AFTER_SEAL_FROM_SELL_ELIGIBLE_DAY",
        "execution_price": "NEXT_BAR_OPEN_MINUTE_PROXY_NOT_ACTUAL_FILL",
        "missing_minute_truth": "PENDING_NO_DAILY_OPEN_FALLBACK",
        "blocked_exit": "RETAIN_POSITION_AND_PENDING_SELL_UNTIL_TRADABLE",
        "legacy_open_exit": "ARCHIVE_ONLY_FOR_EXITS_BEFORE_20260914",
        "existing_terminal_records": "IMMUTABLE", "entry_policy_changed": False,
        "promotion_model_changed": False, "actual_execution_claimed": False,
    }
    if not isinstance(policy, dict) or any(type(policy.get(key)) is not type(value) or policy[key] != value
                                           for key, value in expected.items()):
        raise ValueError("P0 exit policy configuration differs from the versioned engine contract")
    if digest(path) != sha:
        raise ValueError("exit policy configuration changed during projection")
    return {"path": EXIT_POLICY_CONFIG_PATH, "sha256": sha}


def _minute_exit_timing_valid(result):
    try:
        stamps = {key: datetime.fromisoformat(result[key]) for key in (
            "actual_exit_time", "decision_time", "execution_bar_start", "execution_bar_end")}
        if any(value.utcoffset().total_seconds() != 8 * 3600 for value in stamps.values()):
            return False
        return (result.get("timezone") == "Asia/Shanghai"
                and result.get("exit_time_semantics") == "NEXT_BAR_OPEN_MINUTE_PROXY"
                and stamps["actual_exit_time"] == stamps["execution_bar_start"]
                and stamps["execution_bar_start"] >= stamps["decision_time"]
                and stamps["execution_bar_end"] > stamps["decision_time"]
                and (stamps["execution_bar_end"] - stamps["execution_bar_start"]).total_seconds() == 60
                and stamps["actual_exit_time"].strftime("%Y%m%d") == result.get("actual_exit_date"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def observation_row(frozen, d, t, t1, asof, tables, exit_sessions=None,
                    minute_loader=None, exit_resolver=None):
    """No call can read a market table after asof (including pending exits)."""
    row = {k: frozen.get(k) for k in ("ts_code", "name", "industry", "stage_transition", "promotion_rank", "path_label")}
    row.update(signal_date=d, exec_date=t, exit_date=t1, validation_status="PENDING_T",
               continuation_limit_up_hit=None, market_daily_return=None,
               observation_t_return=None, proxy_fill=None, actual_net_return=None,
               slot_net_return=None, truth_source="daily_open_proxy", actual_order_fill_observed=False)
    minute_exit = t1 >= EXIT_1000_EFFECTIVE_DATE
    if minute_exit:
        row.update(exit_policy_id=EXIT_1000_POLICY_ID, truth_source=MINUTE_TRUTH_SOURCE,
                   actual_exit_price=None, actual_exit_time=None)
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
    if minute_exit:
        if exit_resolver is None:
            from top10decision.decision.shadow_exit_1000 import resolve_exit_1000
            exit_resolver = resolve_exit_1000
        result, status = exit_resolver(
            open_dates=exit_sessions or [t1], scheduled_exit_date=t1,
            as_of_date=asof, code=code, entry_price=op, t_close_price=close,
            load_daily=lambda date: tables(date, "daily").get(code),
            load_limits=lambda date: tables(date, "stk_limit").get(code),
            load_minutes=lambda date: minute_loader(date, code) if minute_loader else None,
        )
        row["exit_validation_status"] = status
        if result is None:
            row["validation_status"] = (
                "PENDING_EXIT_PROXY" if status == "PENDING_EXIT_LIMIT_UP_HELD" else
                "UNRESOLVED_EXIT_PROXY" if status == "PENDING_EXIT_UNSELLABLE" else
                "PENDING_T1" if status == "PENDING_T1" else "MISSING_T1_TRUTH"
            )
            return row
        gross, price = number(result.get("gross_return")), number(result.get("exit_price"))
        if (status != "SETTLED_EXIT_1000_MINUTE_PROXY" or result.get("exit_policy_id") != EXIT_1000_POLICY_ID
                or gross is None or gross <= -1 or price is None or price <= 0
                or not t1 <= str(result.get("actual_exit_date", "")) <= asof
                or not _minute_exit_timing_valid(result)
                or result.get("research_only") is not True
                or result.get("actual_execution_claimed") is not False):
            raise ValueError("invalid 10:00 exit engine result; refusing an open-price fallback")
        row.update(validation_status="FINAL_VERIFIED_PROXY", actual_net_return=gross - COST_RATE,
                   slot_net_return=gross - COST_RATE, actual_exit_price=price, gross_return=gross)
        for key in ("actual_exit_date", "actual_exit_time", "decision_time", "execution_bar_start",
                    "execution_bar_end", "exit_time_semantics", "exit_reason", "held_limit_up_sessions",
                    "blocked_exit_sessions", "suspended_exit_sessions", "delayed_trading_days",
                    "timezone", "price_basis", "wealth_chain", "minute_source_files", "research_only",
                    "actual_execution_claimed", "exit_capacity_verified"):
            if key in result:
                row[key] = result[key]
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


def _summarize_rows(rows, daily_summaries, excluded):
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
    if any("pending_exit_rows" in day for day in daily_summaries):
        counts["pending_exit_rows"] = sum(day.get("pending_exit_rows", 0) for day in daily_summaries)
    # A subset of closed cohorts is not the full-window portfolio, and a
    # delayed exit can overlap the next cohort's capital. Never publish that
    # synthetic compounding as a realizable equity curve.
    incomplete = counts["missing_t_truth_rows"] + counts["missing_t1_truth_rows"] + counts["unresolved_exit_rows"]
    delayed = any(r.get("blocked_exit_sessions", 0) or r.get("held_limit_up_sessions", 0)
                  or r.get("suspended_exit_sessions", 0) or r.get("validation_status") == "PENDING_EXIT_PROXY"
                  or (r.get("actual_exit_date") and r["actual_exit_date"] > r["exit_date"]) for r in rows)
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


def summarize(rows, daily_summaries, excluded):
    """Promotion truth spans policies; unlike returns, its target did not change."""
    stats = _summarize_rows(rows, daily_summaries, excluded)
    if not any(row.get("exit_policy_id") == EXIT_1000_POLICY_ID for row in rows):
        return stats
    policies = sorted({row.get("exit_policy_id", LEGACY_EXIT_POLICY_ID) for row in rows})
    stats["exit_policy_ids"] = policies
    stats["return_statistics_scope"] = "SEPARATE_EXIT_POLICIES" if len(policies) > 1 else "SINGLE_EXIT_POLICY"
    stats["return_statistics_by_exit_policy"] = {}
    for policy in policies:
        subset = [row for row in rows if row.get("exit_policy_id", LEGACY_EXIT_POLICY_ID) == policy]
        signal_dates = {row["signal_date"] for row in subset}
        days = [day for day in daily_summaries if day["signal_date"] in signal_dates]
        stats["return_statistics_by_exit_policy"][policy] = _summarize_rows(subset, days, 0)
    if len(policies) > 1:
        for key in ("final_win_rate", "mean_final_net_return", "median_final_net_return",
                    "worst_final_net_return", "tail_10pct_mean_return", "profit_factor",
                    "equal_slot_cumulative_return", "equal_slot_max_drawdown"):
            stats[key] = None
        stats["daily_portfolio"] = []
        stats["portfolio_curve_reason"] = "MIXED_EXIT_POLICIES_RETURNS_NOT_COMBINED"
        for path in stats["path_performance"].values():
            path["mean_final_net_return"] = path["win_rate"] = None
    return stats


def build(root, asof):
    root = root.resolve()
    dates = _strict_open_dates(root)
    if asof not in dates:
        raise ValueError("as-of must be an exact committed SSE open date")
    rows, daily_summaries, bindings, excluded = [], [], [], 0
    source_files, cache = {}, {}
    minute_cache = {}
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
    def minutes(date, code):
        if date > asof:
            raise ValueError("future minute truth read forbidden")
        key = (date, code)
        if key not in minute_cache:
            from top10decision.decision.shadow_exit_minute_truth import load_exit_minutes, minute_paths
            envelope = load_exit_minutes(root, date, code)
            if envelope:
                expected = {path.relative_to(root).as_posix() for path in minute_paths(root, date, code)}
                sources = envelope.get("source_files", [])
                if len(sources) != 2 or {source.get("path") for source in sources} != expected:
                    raise ValueError("exit minute source must bind the exact CSV and metadata pair")
                verified_sources = {}
                for source in envelope["source_files"]:
                    path = root / source["path"]
                    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents) if p != root):
                        raise ValueError("unsafe exit minute source binding")
                    sha = digest(path)
                    if sha != source["sha256"]:
                        raise ValueError("exit minute source SHA changed during projection")
                    verified_sources[source["path"]] = sha
                source_files.update(verified_sources)
            minute_cache[key] = envelope
        return minute_cache[key]
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
                               d, t, t1, asof, tables, dates, minutes) for r in contract["rows"]]
        rows.extend(day)
        statuses = [r["validation_status"] for r in day]
        daily_summaries.append(dict(signal_date=d, exec_date=t, exit_date=t1, rows=len(day),
            t_validated_rows=sum(r["continuation_limit_up_hit"] is not None for r in day),
            final_verified_trades=sum(r["actual_net_return"] is not None for r in day),
            pending_t_rows=statuses.count("PENDING_T"),
            pending_t1_rows=statuses.count("PENDING_T1") + statuses.count("PENDING_EXIT_PROXY"),
            missing_t_truth_rows=statuses.count("MISSING_T_TRUTH"),
            missing_t1_truth_rows=statuses.count("MISSING_T1_TRUTH"),
            unresolved_exit_rows=statuses.count("UNRESOLVED_EXIT_PROXY"),
            settled_rows=sum(r["slot_net_return"] is not None for r in day)))
        if t1 >= EXIT_1000_EFFECTIVE_DATE:
            daily_summaries[-1]["pending_exit_rows"] = statuses.count("PENDING_EXIT_PROXY")
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
    if any(row.get("exit_policy_id") == EXIT_1000_POLICY_ID for row in rows):
        exit_policy_config = _exit_policy_binding(root)
        payload["source_files"].append(exit_policy_config)
        payload["source_files"].sort(key=lambda item: item["path"])
        payload["schema_version"] = VERSIONED_SCHEMA
        payload["policy"].update(
            id=VERSIONED_POLICY_ID, policy_definition_date="20260912",
            exit_policy_effective_scheduled_date=EXIT_1000_EFFECTIVE_DATE,
            current_exit_policy_id=EXIT_1000_POLICY_ID, historical_exit_policy_id=LEGACY_EXIT_POLICY_ID,
            exit_policy_config=exit_policy_config,
            mixed_exit_returns_combined=False,
            exit="scheduled exit before 20260914 retains legacy open; otherwise 10:00 unless sealed limit-up, observed break exits at next executable minute proxy",
            corporate_action_adjustment="chain close/pre_close across held or blocked sessions; policy-bound exit price/pre_close",
        )
    return payload, rows


def plan_exit_minute_requests(root, asof):
    """Only validated, proxy-bought, unfinished P0 positions need minute input."""
    _, rows = build(root, asof)
    dates = _strict_open_dates(root.resolve())
    requests = {
        (date, row["ts_code"])
        for row in rows
        if row.get("exit_policy_id") == EXIT_1000_POLICY_ID and row.get("proxy_fill") == 1
        and row["validation_status"] not in {"FINAL_VERIFIED_PROXY", "FINAL_NO_FILL_PROXY"}
        for date in dates if row["exit_date"] <= date <= asof
    }
    return [{"trade_date": date, "ts_code": code} for date, code in sorted(requests)]


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
