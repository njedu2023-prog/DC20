#!/usr/bin/env python3
"""Offline conservative open-price labels; never writes production/Shadow data.

Daily bars establish only a research proxy, not actual auction fills/capacity.
Unknown evidence is censored, not cash. No imports of production writers.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMINAL = {"SETTLED_OPEN_PROXY", "NO_FILL_OPEN_LIMIT_UP_PROXY", "NO_FILL_ZERO_VOLUME_PROXY"}
IDENTITY = ["signal_date", "exec_date", "scheduled_exit_date", "ts_code", "stage", "promotion_rank", "top10_members_sha256"]
VALUE_COLUMNS = ["label_status", "proxy_fill", "slot_net_return", "slot_net_return_stress", "conditional_net_return", "label_available_date", "actual_exit_date", "blocked_exit_sessions", "entry_price_proxy", "exit_price_proxy", "gross_return_proxy", "missing_evidence_date", "reason", "actual_order_fill_observed"]


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_file(root, relative):
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "unsafe input path")
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "symlink input forbidden")
    require(current.is_file(), f"missing input {relative}")
    return current


def output_directory():
    # No caller-supplied output path; symlinks cannot redirect to production.
    target = HERE / "outputs"
    require(not target.is_symlink(), "symlink output directory forbidden")
    target.mkdir(exist_ok=True)
    require(target.resolve().parent == HERE, "output escaped research directory")
    return target


def write_bytes(path, payload):
    require(path.parent == output_directory(), "only research outputs are writable")
    require(not path.is_symlink(), "symlink output file forbidden")
    require(not path.exists() or (path.is_file() and path.stat().st_nlink == 1), "aliased or nonfile output forbidden")
    path.write_bytes(payload)


def write_json(path, data):
    write_bytes(path, (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode())


def csv_rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def tick(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_calendar(path):
    source = csv_rows(path)
    dates = [r["cal_date"] for r in source if r["exchange"] == "SSE" and r["is_open"] == "1"]
    require(dates == sorted(set(dates)) and all(re.fullmatch(r"20\d{6}", d) for d in dates), "invalid strict SSE calendar")
    return dates


def validate_identities(rows, dates):
    positions = {d: i for i, d in enumerate(dates)}
    keys, groups = set(), {}
    for row in rows:
        d, t, t1, code = [row[c] for c in IDENTITY[:4]]
        require(d in positions and positions[d] + 2 < len(dates), "D outside complete calendar")
        require([t, t1] == dates[positions[d] + 1:positions[d] + 3], "D/T/T+1 are not adjacent SSE dates")
        require(re.fullmatch(r"\d{6}\.(SH|SZ)", code), "invalid candidate code")
        require((d, code) not in keys, "duplicate frozen candidate")
        require(row["stage"] in {"2", "3"}, "outside frozen promotion scope")
        keys.add((d, code))
        groups.setdefault(d, []).append(row)
    for d, group in groups.items():
        require(len(group) <= 10, f"padded candidates on {d}")
        require(sorted(int(r["promotion_rank"]) for r in group) == list(range(1, len(group) + 1)), "noncontiguous promotion rank")
        require(len({r["top10_members_sha256"] for r in group}) == 1, "membership binding disagreement")


class MarketEvidence:
    def __init__(self, root, asof):
        self.root, self.asof = Path(root).resolve(strict=True), asof
        self.cache, self.bindings = {}, {}

    def rows(self, date, kind):
        require(date <= self.asof, "future market read forbidden")
        require(re.fullmatch(r"20\d{6}", date) and kind in {"daily", "stk_limit"}, "invalid market key")
        key = (date, kind)
        if key not in self.cache:
            candidates = [Path("data/market/raw") / date[:4] / date / f"{kind}.csv", Path("data/market/raw") / date / f"{kind}.csv", Path("data/market/raw") / f"{kind}_{date}.csv"]
            present = [safe_file(self.root, p) for p in candidates if (self.root / p).exists() or (self.root / p).is_symlink()]
            require(len({sha(p) for p in present}) <= 1, f"conflicting exact-date partitions {date}/{kind}")
            found = {}
            if present:
                path = present[0]
                self.bindings[str(path.relative_to(self.root))] = sha(path)
                for row in csv_rows(path):
                    require(row.get("trade_date") == date, f"wrong date inside {path}")
                    code = row.get("ts_code", "")
                    require(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code), "invalid market code")
                    require(code not in found, f"duplicate market code {date}/{code}")
                    found[code] = row
            self.cache[key] = found
        return self.cache[key]

    def candidate(self, date, code):
        return self.rows(date, "daily").get(code), self.rows(date, "stk_limit").get(code)


def bar_state(daily, limit):
    if daily is None or limit is None:
        return "MISSING", None
    up, down, vol = [number(v) for v in (limit.get("up_limit"), limit.get("down_limit"), daily.get("vol"))]
    if up is None or down is None or vol is None or not 0 < down < up or vol < 0:
        return "INVALID", None
    prices = {k: number(daily.get(k)) for k in ("open", "high", "low", "close", "pre_close")}
    # An explicit zero-volume row is not the same as an absent stock row.
    if vol == 0:
        return "ZERO_VOLUME", dict(prices, vol=vol, up_limit=up, down_limit=down)
    if any(v is None or v <= 0 for v in prices.values()):
        return "INVALID", None
    op, hi, lo, close, pre = [prices[k] for k in ("open", "high", "low", "close", "pre_close")]
    if not (tick(down) <= tick(lo) <= min(tick(op), tick(close)) <= max(tick(op), tick(close)) <= tick(hi) <= tick(up)):
        return "INVALID", None
    return "VALID", dict(prices, vol=vol, up_limit=up, down_limit=down)


def label_row(frozen, dates, asof, evidence, cost=.0045, stress=.009):
    result = {k: frozen[k] for k in IDENTITY}
    result.update({k: None for k in VALUE_COLUMNS})
    result.update(label_status="PENDING_T", blocked_exit_sessions=0, actual_order_fill_observed=False)
    d, t, t1, code = [frozen[k] for k in IDENTITY[:4]]

    def unresolved(status, date, reason):
        result.update(label_status=status, missing_evidence_date=date, reason=reason)
        return result

    if t > asof:
        return result
    state, bar = bar_state(*evidence.candidate(t, code))
    if state in {"MISSING", "INVALID"}:
        return unresolved(f"{state}_T_TRUTH", t, "requires exact-date official daily and limits")
    if state == "ZERO_VOLUME" or tick(bar["open"]) == tick(bar["up_limit"]):
        result.update(label_status="NO_FILL_ZERO_VOLUME_PROXY" if state == "ZERO_VOLUME" else "NO_FILL_OPEN_LIMIT_UP_PROXY", proxy_fill=0, slot_net_return=0.0, slot_net_return_stress=0.0, label_available_date=t, reason="known no-entry under the fixed conservative proxy; no fee")
        return result
    result.update(proxy_fill=1, entry_price_proxy=bar["open"], label_status="PENDING_T1")
    if t1 > asof:
        return result
    previous_close = bar["close"]
    for session in dates[dates.index(t1):]:
        if session > asof:
            break
        state, exit_bar = bar_state(*evidence.candidate(session, code))
        if state in {"MISSING", "INVALID"}:
            return unresolved(f"{state}_EXIT_TRUTH", session, "never skip an unobserved intervening session")
        if exit_bar["pre_close"] is None or exit_bar["close"] is None or exit_bar["pre_close"] <= 0 or exit_bar["close"] <= 0:
            return unresolved("INVALID_EXIT_TRUTH", session, "cannot bridge zero-volume session without prices")
        if tick(exit_bar["pre_close"]) != tick(previous_close):
            return unresolved("CORPORATE_ACTION_UNRESOLVED", session, "reference-price discontinuity without corporate-action/adjustment evidence")
        if state == "ZERO_VOLUME" and tick(exit_bar["close"]) != tick(previous_close):
            return unresolved("CORPORATE_ACTION_UNRESOLVED", session, "zero-volume close changed without adjustment or trading evidence")
        if state == "ZERO_VOLUME" or tick(exit_bar["open"]) == tick(exit_bar["down_limit"]):
            result["blocked_exit_sessions"] += 1
            previous_close = exit_bar["close"]
            continue
        gross = exit_bar["open"] / result["entry_price_proxy"] - 1
        result.update(label_status="SETTLED_OPEN_PROXY", slot_net_return=gross - cost, slot_net_return_stress=gross - stress, conditional_net_return=gross - cost, gross_return_proxy=gross, exit_price_proxy=exit_bar["open"], actual_exit_date=session, label_available_date=session, reason="first eligible observed open; proxy, not actual fill")
        return result
    return unresolved("UNRESOLVED_EXIT", asof, "no eligible exit by the fixed as-of; return stays unknown")


def build(repo, plan):
    repo = Path(repo).resolve(strict=True)
    inputs = {}
    for key, spec in plan["source_inputs"].items():
        path = safe_file(repo, spec["path"])
        require(sha(path) == spec["sha256"], f"pinned input changed {key}")
        inputs[key] = path
    dates = read_calendar(inputs["calendar"])
    asof = plan["as_of_date"]
    require(asof in dates, "as-of outside strict open calendar")
    rows = csv_rows(inputs["ledger"])
    require(len(rows) == 6753 and len({r["signal_date"] for r in rows}) == 910, "historical frozen scope drift")
    validate_identities(rows, dates)
    require(plan["training"]["terminal_label_statuses"] == ["SETTLED_OPEN_PROXY", "NO_FILL_OPEN_LIMIT_UP_PROXY", "NO_FILL_ZERO_VOLUME_PROXY"], "terminal contract drift")
    policy = plan["label_policy"]
    require(policy["base_all_in_assumed_cost_rate"] == .0045 and policy["stress_all_in_assumed_cost_rate"] == .009, "fixed cost plan drift")
    evidence = MarketEvidence(repo, asof)
    labels = [label_row(r, dates, asof, evidence) for r in rows]
    for relative, expected in evidence.bindings.items():
        require(sha(safe_file(repo, relative)) == expected, "market source changed during label build")
    for key, spec in plan["source_inputs"].items():
        require(sha(inputs[key]) == spec["sha256"], "pinned input changed during label build")
    groups = {}
    for row in labels:
        groups.setdefault(row["signal_date"], []).append(row)
    complete = sorted(d for d, group in groups.items() if all(r["label_status"] in TERMINAL for r in group))
    incomplete = [{"signal_date": d, "rows": len(group), "known_rows": sum(r["label_status"] in TERMINAL for r in group), "statuses": dict(Counter(r["label_status"] for r in group))} for d, group in sorted(groups.items()) if d not in complete]
    terminal = [r for r in labels if r["label_status"] in TERMINAL]
    manifest = dict(schema_version="dc20_profit_execution_v2_labels", status="OFFLINE_RESEARCH_LABELS_NOT_FORWARD_RECORDS", source_commit=plan["source_commit"], as_of_date=asof, rows=len(labels), signal_dates=len(groups), terminal_rows=len(terminal), complete_signal_dates=complete, incomplete_signal_dates=incomplete, status_counts=dict(Counter(r["label_status"] for r in labels)), source_inputs=plan["source_inputs"], market_source_files=evidence.bindings, identity_unchanged=all(all(a[k] == b[k] for k in IDENTITY) for a, b in zip(rows, labels)), actual_execution_claimed=False, missing_as_zero=False, production_or_shadow_writes=False, builder_sha256=sha(HERE / "build_labels.py"), plan_sha256=sha(HERE / "PLAN.json"))
    return labels, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=HERE.parents[1])
    args = parser.parse_args()
    plan = json.loads((HERE / "PLAN.json").read_text())
    labels, manifest = build(args.repo, plan)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=IDENTITY + VALUE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(labels)
    output = output_directory()
    path = output / "execution_labels.csv.gz"
    write_bytes(path, gzip.compress(text.getvalue().encode(), mtime=0))
    manifest["labels_sha256"] = sha(path)
    write_json(output / "label_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("rows", "signal_dates", "terminal_rows", "complete_signal_dates", "status_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
