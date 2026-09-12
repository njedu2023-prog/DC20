"""Independent capital-constrained shadow accounts, never an order router.

Each rank owns CNY 1,000,000 and plans one CNY 100,000 entry per candidate D.
The input ranking and new-policy labels must already be provenance verified.
Auction and minute prices remain proxies; this replay does not prove a fill.
Daily mark callbacks must return SHA-verified exact-code/date source rows.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping
from zoneinfo import ZoneInfo

from top10decision.decision.shadow_exit_1000 import EXIT_POLICY_ID

SCHEMA = "dc20_profit_1000_capital_diagnostic_v1"
INITIAL_CASH = 1_000_000.0
TARGET_NOTIONAL = 100_000.0
COST_RATE = .0045
LOT_SIZE = 100
SETTLED = "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"
ENTRY_POLICIES = {"research_auction_or_open_no_cap_v1", "research_auction_or_open_frozen_cap_v1"}
SH = ZoneInfo("Asia/Shanghai")


def _date(value):
    if not isinstance(value, str) or re.fullmatch(r"20\d{6}", value) is None:
        raise ValueError("strict YYYYMMDD date required")
    datetime.strptime(value, "%Y%m%d")
    return value


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError("boolean numeric value")
    number = float(value)
    if not math.isfinite(number) or positive and number <= 0:
        raise ValueError("invalid finite number")
    return number


def _money(value):
    return round(float(value), 2)


def _key(row):
    date, code = _date(row.get("signal_date")), row.get("ts_code")
    if not isinstance(code, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", code) is None:
        raise ValueError("exact SH/SZ stock code required")
    return date, code


def _at(day, hhmmss):
    return datetime.strptime(day + " " + hhmmss, "%Y%m%d %H:%M:%S").replace(tzinfo=SH)


def _exit_time(label, scheduled, dates):
    value = label.get("actual_exit_time")
    if not isinstance(value, str):
        raise ValueError("exact exit timestamp missing")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() != _at(scheduled, "10:00:00").utcoffset():
        raise ValueError("Shanghai exit timestamp required")
    stamp = stamp.astimezone(SH)
    day = stamp.strftime("%Y%m%d")
    if day != label.get("actual_exit_date") or day not in dates or day < scheduled:
        raise ValueError("exit violates T+1 trading date")
    if not (_at(day, "09:30:00") <= stamp <= _at(day, "11:30:00") or _at(day, "13:00:00") <= stamp < _at(day, "15:00:00")):
        raise ValueError("exit timestamp outside continuous trading")
    return stamp


def _mark_source(payload, day, code):
    if not isinstance(payload, Mapping) or payload.get("ts_code") != code or payload.get("trade_date") != day:
        raise ValueError("daily mark identity missing or wrong")
    close, pre = _number(payload.get("close"), positive=True), _number(payload.get("pre_close"), positive=True)
    sources = payload.get("source_files")
    if not isinstance(sources, list) or not sources:
        raise ValueError("daily mark source SHA binding missing")
    seen = set()
    for item in sources:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ValueError("daily mark source binding malformed")
        path, sha = item["path"], item["sha256"]
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or any(p in {"", ".", ".."} for p in path.split("/")) or path in seen or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
            raise ValueError("daily mark source path/SHA invalid")
        seen.add(path)
    return close, pre, sources


def replay_capital(ranked_rows, label_rows, *, open_dates, as_of_date, load_daily,
                   rank_field="candidate_rank", entry_policy_id="research_auction_or_open_no_cap_v1"):
    """Replay two separate accounts in event time through a completed session.

    Ranks must be frozen before entry. Missing entry truth reserves 100,450 CNY
    and makes NAV unknown. Known fills reserve 45bp future costs; that reserve
    is charged exactly once, through the already-net label, upon exit. Gross
    daily marks use the same close/pre-close wealth chain as the label engine;
    estimated open-position closing costs are deducted only for marked equity.
    """
    ranked_rows, label_rows = list(ranked_rows), list(label_rows)
    dates = list(open_dates)
    if not dates or dates != sorted(set(dates)) or any(_date(d) != d for d in dates):
        raise ValueError("strict ordered exchange calendar required")
    as_of = _date(as_of_date)
    if as_of not in dates or entry_policy_id not in ENTRY_POLICIES:
        raise ValueError("as-of or entry policy invalid")
    frozen, day_ranks = {}, {}
    for supplied in ranked_rows:
        key = _key(supplied)
        rank = _number(supplied.get(rank_field), positive=True)
        if rank != int(rank) or key in frozen or rank in day_ranks.setdefault(key[0], set()):
            raise ValueError("duplicate frozen candidate or rank")
        if key[0] not in dates or dates.index(key[0]) + 2 >= len(dates):
            raise ValueError("frozen D lacks adjacent T/T+1 calendar")
        day_ranks[key[0]].add(int(rank))
        frozen[key] = dict(supplied, shadow_rank=int(rank))
    if any(ranks != set(range(1, len(ranks) + 1)) for ranks in day_ranks.values()):
        raise ValueError("ranking cohort has missing ranks")
    labels = {}
    for label in label_rows:
        key = _key(label)
        if key in labels:
            raise ValueError("duplicate label identity")
        labels[key] = label
    sources = {}
    accounts = {}
    for rank in (1, 2):
        records, events = [], []
        for key, selected in sorted(frozen.items()):
            if selected["shadow_rank"] != rank:
                continue
            index = dates.index(key[0])
            t, t1 = dates[index + 1:index + 3]
            label = labels.get(key)
            record = {"signal_date": key[0], "ts_code": key[1], "shadow_rank": rank,
                      "exec_date": t, "scheduled_exit_date": t1, "status": "WAITING_ENTRY",
                      "shares": 0, "entry_price": None, "invested_notional": 0.0,
                      "reserved_exit_cost": 0.0, "actual_exit_time": None,
                      "net_profit": None, "charged_round_trip_cost": None,
                      "entry_policy_id": entry_policy_id, "actual_execution_claimed": False}
            records.append(record)
            if t > as_of:
                continue
            if label is not None:
                if label.get("label_policy_id") != EXIT_POLICY_ID or label.get("entry_policy_id") != entry_policy_id or label.get("round_trip_cost_rate") != COST_RATE:
                    raise ValueError("old/mixed exit, entry or cost policy")
                if label.get("exec_date") not in (None, t) or label.get("scheduled_exit_date") not in (None, t1):
                    raise ValueError("label T/T+1 date mismatch")
            events.append((_at(t, "09:25:00"), "entry", record, label))
            if label is not None and label.get("label_status") == SETTLED:
                stamp = _exit_time(label, t1, dates)
                if stamp.date() <= _at(as_of, "15:00:00").date():
                    if type(label.get("proxy_fill")) is not int or label["proxy_fill"] != 1:
                        raise ValueError("settled label must be a proxy fill")
                    net = _number(label.get("slot_net_return"))
                    if abs(net - _number(label.get("conditional_net_return"))) > 1e-10:
                        raise ValueError("filled label conditional/slot returns disagree")
                    evidence = label.get("exit_evidence")
                    if not isinstance(evidence, Mapping) or evidence.get("exit_policy_id") != EXIT_POLICY_ID or _number(evidence.get("gross_return")) <= -1 or abs(_number(evidence.get("gross_return")) - COST_RATE - net) > 1e-10:
                        raise ValueError("settled net return does not bind gross less 45bp")
                    if evidence.get("actual_exit_time") != label["actual_exit_time"] or evidence.get("actual_exit_date") != label["actual_exit_date"]:
                        raise ValueError("exit time/source evidence mismatch")
                    events.append((stamp, "exit", record, label))
        events.sort(key=lambda item: (item[0], item[1], item[2]["signal_date"], item[2]["ts_code"]))
        cash, realized = INITIAL_CASH, 0.0
        pending_entries, positions, curve = {}, {}, []
        peak, history_complete = INITIAL_CASH, True
        event_index = 0
        start = min((r["exec_date"] for r in records if r["exec_date"] <= as_of), default=None)
        active_days = [d for d in dates if start is not None and start <= d <= as_of]

        def reserved():
            return sum(p["cost_reserve"] for p in positions.values()) + sum(pending_entries.values())

        for day in active_days:
            while event_index < len(events) and events[event_index][0] <= _at(day, "15:00:00"):
                stamp, action, record, label = events[event_index]
                event_index += 1
                key = (record["signal_date"], record["ts_code"])
                if action == "exit":
                    position = positions.pop(key, None)
                    if position is None:
                        continue  # No hypothetical sale of a capital-blocked trade.
                    profit = _money(position["notional"] * _number(label["slot_net_return"]))
                    cash = _money(cash + position["notional"] + profit)
                    realized = _money(realized + profit)
                    record.update(status="SETTLED", actual_exit_time=stamp.isoformat(), net_profit=profit,
                                  charged_round_trip_cost=position["cost_reserve"], reserved_exit_cost=0.0)
                    continue
                status = "" if label is None else str(label.get("label_status", ""))
                if status.startswith("NO_FILL_"):
                    if type(label.get("proxy_fill")) is not int or label["proxy_fill"] != 0 or label.get("slot_net_return") != 0 or label.get("conditional_net_return") is not None:
                        raise ValueError("known no-fill label semantics invalid")
                    record.update(status="NO_FILL", reason=status, net_profit=0.0, charged_round_trip_cost=0.0)
                    continue
                free = cash - reserved()
                if label is None or label.get("proxy_fill") is None:
                    need = TARGET_NOTIONAL * (1 + COST_RATE)
                    if free + 1e-8 < need:
                        record.update(status="CAPITAL_UNAVAILABLE", reason="INSUFFICIENT_CASH_FOR_UNKNOWN_ENTRY_RESERVE")
                    else:
                        pending_entries[key] = need
                        record.update(status="PENDING_ENTRY_TRUTH", reserved_entry_cash=need)
                    continue
                if type(label.get("proxy_fill")) is not int or label["proxy_fill"] != 1:
                    raise ValueError("unknown nonterminal fill flag")
                price = _number(label.get("entry_price"), positive=True)
                shares = int(Decimal(str(TARGET_NOTIONAL)) / Decimal(str(price)) / LOT_SIZE) * LOT_SIZE
                notional = _money(shares * price)
                cost = _money(notional * COST_RATE)
                if shares == 0:
                    record.update(status="BUDGET_BELOW_ONE_LOT", reason="100_SHARE_LOT_EXCEEDS_FIXED_BUDGET")
                elif free + 1e-8 < notional + cost:
                    record.update(status="CAPITAL_UNAVAILABLE", reason="INSUFFICIENT_CASH_BEFORE_AUCTION")
                else:
                    cash = _money(cash - notional)
                    positions[key] = {"entry_date": record["exec_date"], "code": record["ts_code"],
                                      "price": price, "shares": shares, "notional": notional,
                                      "cost_reserve": cost, "wealth": None, "previous_close": None,
                                      "mark_broken": False, "market_value": None}
                    record.update(status="OPEN_POSITION", shares=shares, entry_price=price,
                                  invested_notional=notional, reserved_exit_cost=cost,
                                  exit_status=status or "PENDING_EXIT_TRUTH")
            missing = []
            for key, position in positions.items():
                if position["mark_broken"]:
                    missing.append({"signal_date": key[0], "ts_code": key[1], "reason": "EARLIER_MARK_CHAIN_MISSING"})
                    continue
                try:
                    payload = load_daily(day, position["code"])
                    close, pre, bindings = _mark_source(payload, day, position["code"])
                    for binding in bindings:
                        if binding["path"] in sources and sources[binding["path"]] != binding:
                            raise ValueError("daily mark source SHA changed")
                        sources[binding["path"]] = dict(binding)
                    if day == position["entry_date"]:
                        position["wealth"] = close / position["price"]
                    else:
                        if position["previous_close"] is None:
                            raise ValueError("earlier mark missing")
                        if abs(position["previous_close"] - pre) > .005:
                            corroborated = _number(payload.get("limits_pre_close"), positive=True)
                            if abs(corroborated - pre) > .005:
                                raise ValueError("corporate action mark basis unresolved")
                        position["wealth"] *= close / pre
                    position["wealth"] = _number(position["wealth"], positive=True)
                    position["previous_close"] = close
                    position["market_value"] = _number(position["notional"] * position["wealth"], positive=True)
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    position["mark_broken"], position["market_value"] = True, None
                    missing.append({"signal_date": key[0], "ts_code": key[1], "reason": "MISSING_OR_INVALID_DAILY_MARK", "error_type": type(exc).__name__})
            fully_marked = not pending_entries and not missing
            cost_reserve = sum(p["cost_reserve"] for p in positions.values())
            equity = _money(cash + sum(p["market_value"] for p in positions.values()) - cost_reserve) if fully_marked else None
            if equity is None:
                history_complete = False
            elif history_complete:
                peak = max(peak, equity)
            curve.append({"trade_date": day, "cash_balance": _money(cash),
                          "available_cash": _money(cash - reserved()),
                          "reserved_entry_cash": _money(sum(pending_entries.values())),
                          "reserved_exit_cost": _money(cost_reserve), "equity": equity,
                          "net_profit": _money(equity - INITIAL_CASH) if equity is not None else None,
                          "drawdown": equity / peak - 1 if equity is not None and history_complete else None,
                          "fully_marked": fully_marked, "missing_marks": missing,
                          "open_positions": len(positions), "pending_entry_slots": len(pending_entries)})
        final_equity = curve[-1]["equity"] if curve else INITIAL_CASH
        accounts["top" + str(rank)] = {
            "initial_cash": INITIAL_CASH, "cash_balance": _money(cash), "available_cash": _money(cash - reserved()),
            "reserved_entry_cash": _money(sum(pending_entries.values())),
            "reserved_exit_cost": _money(sum(p["cost_reserve"] for p in positions.values())),
            "invested_cost_basis": _money(sum(p["notional"] for p in positions.values())),
            "equity": final_equity, "net_profit": _money(final_equity - INITIAL_CASH) if final_equity is not None else None,
            "realized_net_profit": realized, "nav_history_complete": history_complete,
            "maximum_drawdown": min((p["drawdown"] for p in curve), default=0.0) if history_complete else None,
            "records": records, "equity_curve": curve,
            "open_positions": [dict(signal_date=k[0], ts_code=k[1], shares=p["shares"], entry_date=p["entry_date"],
                                    invested_notional=p["notional"], market_value=_money(p["market_value"]) if p["market_value"] is not None else None)
                               for k, p in sorted(positions.items())],
            "capital_unavailable_slots": sum(r["status"] == "CAPITAL_UNAVAILABLE" for r in records),
            "no_frozen_candidate_dates": [d for d, ranks in sorted(day_ranks.items()) if rank not in ranks],
        }
    return {"schema_version": SCHEMA, "research_only": True, "production_activation_allowed": False,
            "label_verification": "CALLER_VERIFIED_LABELS_REQUIRED_NOT_INDEPENDENT_SOURCE_REPLAY",
            "ranking_verification": "CALLER_MUST_BIND_FROZEN_RANKING",
            "diagnostic_status": "CAPITAL_PROXY_REPLAY" if frozen else "NO_RANKED_COHORT",
            "ranking_input_sha256": hashlib.sha256(json.dumps(ranked_rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "label_input_sha256": hashlib.sha256(json.dumps(label_rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "actual_execution_claimed": False, "as_of_date": as_of, "label_policy_id": EXIT_POLICY_ID,
            "entry_policy_id": entry_policy_id, "rank_field": rank_field,
            "account_policy": {"independent_accounts": True, "initial_cash_per_account": INITIAL_CASH,
                               "planned_notional_per_trade": TARGET_NOTIONAL, "lot_size": LOT_SIZE,
                               "round_trip_cost_rate": COST_RATE, "fees": "45BP_OF_ACTUAL_NOTIONAL_RESERVED_AT_BUY_CHARGED_ONCE_AT_EXIT_APPROXIMATION",
                               "open_equity": "CASH_PLUS_CLOSE_PRE_CLOSE_WEALTH_MARK_MINUS_RESERVED_EXIT_COST",
                               "entry_time": "09:25:00+08:00", "capital_reused_before_later_exit": False,
                               "unknown_entry_reserve": TARGET_NOTIONAL * (1 + COST_RATE),
                               "negative_score_skip_allowed": False, "compound_slot_returns_used_as_nav": False},
            "accounts": accounts, "source_files": sorted(sources.values(), key=lambda b: b["path"])}


def replay_capital_from_repository(repo_root, ranked_rows, label_rows=None, *, candidate_manifest, as_of_date,
                                   rank_field="candidate_rank", entry_policy_id="research_auction_or_open_no_cap_v1"):
    """Rebuild new-policy labels from bound raw truth before capital replay.

    External labels are optional comparison evidence, never accepted as truth.
    When supplied, their entire canonical contents must equal the fresh replay.
    Ranking provenance remains the caller's separate responsibility: this
    function verifies constituent membership, not whether a rank was published.
    """
    from top10decision.decision import executable_profit_shadow_settlement as s
    from work.profit_1000_upgrade.labels import _binding, build_labels
    root = Path(repo_root).resolve(strict=True)
    dates = s._strict_open_dates(root)
    rebuilt = build_labels(root, candidate_manifest, as_of_date=as_of_date)
    canonical = lambda rows: json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
    external_labels_supplied = label_rows is not None
    if external_labels_supplied and canonical(list(label_rows)) != canonical(rebuilt["rows"]):
        raise ValueError("external labels do not exactly match bound repository replay")
    label_rows = rebuilt["rows"]
    ranked_rows = list(ranked_rows)
    universe = {_key(label) for label in label_rows}
    if any(_key(ranked) not in universe for ranked in ranked_rows):
        raise ValueError("ranked candidate is outside bound label universe")
    bindings, cache = {}, {}
    def table(day, kind):
        if day > as_of_date:
            raise ValueError("future mark source forbidden")
        key = day, kind
        if key not in cache:
            path = s._find_market_file(root, day, kind)
            if path is None:
                return None
            binding = s._source_binding(root, path)
            cache[key] = path, s._market_rows(path, day), binding
            bindings[binding["path"]] = binding
        return cache[key]
    def daily(day, code):
        loaded = table(day, "daily")
        if loaded is None or code not in loaded[1]:
            return None
        row = dict(loaded[1][code], source_files=[loaded[2]])
        limits = table(day, "stk_limit")
        if limits is not None and code in limits[1]:
            row["limits_pre_close"] = limits[1][code].get("pre_close")
            row["source_files"].append(limits[2])
        return row
    result = replay_capital(ranked_rows, label_rows, open_dates=dates, as_of_date=as_of_date,
                            load_daily=daily, rank_field=rank_field, entry_policy_id=entry_policy_id)
    for binding in rebuilt["source_files"]:
        if binding["path"] in bindings and bindings[binding["path"]] != binding:
            raise ValueError("capital source changed since label reconstruction")
        bindings[binding["path"]] = binding
    for binding in bindings.values():
        _binding(root, binding)
    result["source_files"] = sorted(bindings.values(), key=lambda binding: binding["path"])
    result["label_verification"] = "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES"
    result["candidate_manifest_sha256"] = rebuilt["candidate_manifest_sha256"]
    result["external_labels_exactly_matched"] = external_labels_supplied
    return result
