#!/usr/bin/env python3
"""Offline evidence inventory only; does not train, label trades, or use network.

Writes only this research directory's outputs/source_coverage.json. Coverage
means a dated row exists, never that an order was or could actually be filled.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import stat
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


RESEARCH_DIR = Path("work/executable-profit-execution-v2-20260906")
OLD_LEDGER = Path("data/decision_executable_profit/historical_oof_top10_ledger.csv.gz")
SOURCE_LEDGER = Path("data/decision_three_engines/five_year_supervised_ledger.csv.gz")
CALENDAR = Path("data/market/trade_cal_sse.csv")
KINDS = ("daily", "stk_limit", "stk_auction", "stk_auction_o")
SOURCE_LEDGER_SHA256 = "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
SCRIPT = Path(__file__).absolute()
HERE = SCRIPT.parent.resolve()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_path(root: Path, relative: Path | str, *, directory: bool = False) -> Path:
    relative = Path(relative)
    require(not relative.is_absolute() and bool(relative.parts) and ".." not in relative.parts, "unsafe input path")
    root = root.resolve(strict=True)
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), f"symlink input forbidden: {relative}")
    require(current.is_dir() if directory else current.is_file(), f"missing or invalid input: {relative}")
    require(current.resolve().is_relative_to(root), "input escaped source root")
    return current


def checked_here() -> Path:
    # Check the lexical script ancestors before resolving them. In particular,
    # a symlink work/ or research directory must not redirect output elsewhere.
    code = safe_path(SCRIPT.parents[2], RESEARCH_DIR / "audit_sources.py")
    require(code.resolve() == SCRIPT.resolve() and code.parent == HERE, "unexpected audit script location")
    return code.parent


def write_output(output: dict) -> Path:
    here = checked_here()
    directory = here / "outputs"
    require(not directory.is_symlink(), "symlink output directory forbidden")
    directory.mkdir(exist_ok=True)
    require(directory.is_dir() and directory.resolve().parent == here, "output escaped script research directory")
    target = directory / "source_coverage.json"
    require(not target.is_symlink(), "symlink output file forbidden")
    require(not target.exists() or (target.is_file() and target.stat().st_nlink == 1), "hardlinked or nonfile output forbidden")
    payload = (json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    # Open without truncation, then check inode/link count before writing.
    # Holding a directory fd also prevents a parent-symlink replacement from
    # redirecting the file open into an unrelated location.
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=directory_fd)
        try:
            metadata = os.fstat(fd)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, "aliased output inode forbidden")
            os.ftruncate(fd, 0)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(payload)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    return target


def rows(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def number(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def positive(value: object) -> bool:
    result = number(value)
    return result is not None and result > 0


def inferred_limit(value: object, ratio: str) -> Decimal | None:
    result = number(value)
    if result is None or result <= 0:
        return None
    return (result * Decimal(ratio)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fraction(count: int, denominator: int) -> dict[str, int | float]:
    return {"rows": count, "denominator": denominator, "fraction": count / denominator if denominator else 0.0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE.parents[1])
    args = parser.parse_args()
    require(not args.root.is_symlink() and ".." not in args.root.parts, "symlink or unsafe source root forbidden")
    root = args.root.resolve(strict=True)
    require(root.is_dir(), "source root is not a directory")
    here = checked_here()
    plan_path = safe_path(here, "PLAN.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    require(plan.get("schema_version") == "dc20_profit_execution_v2_plan", "unexpected PLAN schema")
    as_of = plan.get("as_of_date", "")
    require(isinstance(as_of, str) and re.fullmatch(r"20\d{6}", as_of), "invalid fixed as_of_date")
    source_commit = plan.get("source_commit", "")
    require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit), "invalid source commit")
    inputs = plan.get("source_inputs", {})
    pinned_paths = {}
    for key, expected in (("ledger", OLD_LEDGER), ("calendar", CALENDAR)):
        pin = inputs.get(key, {})
        require(pin.get("path") == expected.as_posix(), f"unexpected {key} source path")
        require(isinstance(pin.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]), f"invalid {key} pin")
        pinned_paths[key] = safe_path(root, pin["path"])
        require(digest(pinned_paths[key]) == pin["sha256"], f"PLAN {key} SHA256 mismatch")
    source_path = safe_path(root, SOURCE_LEDGER)
    require(digest(source_path) == SOURCE_LEDGER_SHA256, "five-year source ledger SHA256 mismatch")
    old = rows(pinned_paths["ledger"])
    source = rows(source_path)
    source_by_key = {(item["signal_date"], item["ts_code"]): item for item in source}
    assert len(source_by_key) == len(source), "duplicate five-year source identities"
    assert len({(item["signal_date"], item["ts_code"]) for item in old}) == len(old), "duplicate old ledger identities"
    joined = [source_by_key[(item["signal_date"], item["ts_code"])] for item in old]
    wanted_codes = {item["ts_code"] for item in old}
    cal = rows(pinned_paths["calendar"])
    sessions = [item["cal_date"] for item in cal if item.get("exchange") == "SSE" and item["is_open"] == "1"]
    require(sessions == sorted(set(sessions)) and all(re.fullmatch(r"20\d{6}", day) for day in sessions), "invalid strict SSE calendar")
    require(as_of in sessions, "PLAN as_of_date is not an SSE open session")
    next_day = dict(zip(sessions, sessions[1:]))
    adjacency_failures = sum(
        next_day.get(item["signal_date"]) != item["exec_date"]
        or next_day.get(item["exec_date"]) != item["scheduled_exit_date"]
        for item in old
    )
    assert adjacency_failures == 0, "old ledger is not strict D/T/T+1"

    inventory = []
    tables: dict[str, dict[tuple[str, str], dict[str, str]]] = {kind: {} for kind in KINDS}
    raw_dates: dict[str, list[str]] = {}
    coverage = {}
    safe_path(root, "data/market/raw", directory=True)
    for kind in KINDS:
        dates = []
        total = 0
        for path in sorted(root.glob(f"data/market/raw/20*/20*/{kind}.csv")):
            day = path.parent.name
            require(re.fullmatch(r"20\d{6}", day) and path.parent.parent.name == day[:4], "unsafe raw date partition")
            if day > as_of:
                continue
            path = safe_path(root, path.relative_to(root))
            values = rows(path)
            dates.append(day)
            total += len(values)
            assert all(item.get("trade_date") == day for item in values), f"dated file mismatch: {path}"
            assert len({item["ts_code"] for item in values}) == len(values), f"duplicate codes: {path}"
            inventory.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path),
                              "trade_date": day, "kind": kind, "rows": len(values),
                              "fields": list(values[0]) if values else []})
            tables[kind].update({(item["ts_code"], day): item for item in values if item["ts_code"] in wanted_codes})
        raw_dates[kind] = dates
        coverage[kind] = {"files": len(dates), "all_stock_raw_rows": total}
        for role, field in (("D", "signal_date"), ("T", "exec_date"), ("T1", "scheduled_exit_date")):
            count = sum((item["ts_code"], item[field]) in tables[kind] for item in old)
            coverage[kind][role] = fraction(count, len(old))

    daily_limits = set(tables["daily"]) & set(tables["stk_limit"])
    official_auction = set(tables["stk_auction_o"])
    paired = []
    recent = Counter()
    recent_examples = []
    t_auction_and_t1_daily = 0
    both_auction = 0
    for item in old:
        t = (item["ts_code"], item["exec_date"])
        t1 = (item["ts_code"], item["scheduled_exit_date"])
        t_auction_and_t1_daily += t in official_auction and t1 in daily_limits
        both_auction += t in official_auction and t1 in official_auction
        if t not in daily_limits or t1 not in daily_limits:
            continue
        paired.append(item)
        start, end = tables["daily"][t], tables["daily"][t1]
        upper, lower = number(tables["stk_limit"][t].get("up_limit")), number(tables["stk_limit"][t1].get("down_limit"))
        opening, exit_open = number(start.get("open")), number(end.get("open"))
        entry_below = opening is not None and upper is not None and opening < upper
        exit_above = exit_open is not None and lower is not None and exit_open > lower
        recent["T_open_strictly_below_official_upper"] += entry_below
        recent["T_positive_daily_volume"] += positive(start.get("vol"))
        recent["T1_open_strictly_above_official_lower"] += exit_above
        recent["T1_positive_daily_volume"] += positive(end.get("vol"))
        original = source_by_key[(item["signal_date"], item["ts_code"])]
        old_fill = number(item.get("public_market_buyable_proxy")) == 1
        recent["old_filled_but_T_open_not_below_official_upper"] += old_fill and not entry_below
        recent["old_matured_but_T1_open_not_above_official_lower"] += positive(item.get("conditional_entry_price_proxy")) and number(item.get("conditional_net_return_after_cost")) is not None and not exit_above
        recent["T_pre_close_differs_from_source_D_close"] += number(start.get("pre_close")) != number(original.get("d_close"))
        recent["T1_pre_close_differs_from_observed_T_close"] += number(end.get("pre_close")) != number(start.get("close"))
        if old_fill and not entry_below and len(recent_examples) < 5:
            recent_examples.append({"signal_date": item["signal_date"], "ts_code": item["ts_code"],
                                    "T_open": str(opening), "T_official_up_limit": str(upper),
                                    "old_buyable_proxy": 1})

    # Diagnostic only: old code assumed a standard 10% price limit based on
    # the previous unadjusted close. These are NOT official-limit observations.
    sensitivity = Counter()
    tolerance = Decimal("0.011")
    for item in joined:
        upper = inferred_limit(item.get("d_close"), "1.10")
        lower = inferred_limit(item.get("t_close"), "0.90")
        opening, exit_open = number(item.get("t_open")), number(item.get("tplus1_open"))
        open_up = upper is not None and opening is not None and opening >= upper - tolerance
        exit_down = lower is not None and exit_open is not None and exit_open <= lower + tolerance
        old_fill = number(item.get("market_fill")) == 1
        matured = number(item.get("net_return")) is not None
        sensitivity["T_open_at_inferred_upper"] += open_up
        sensitivity["old_filled_even_T_open_at_inferred_upper"] += open_up and old_fill
        sensitivity["old_matured_T1_exit_at_inferred_lower"] += exit_down and old_fill and matured

    recovery_inventory = []
    recovery_keys = set()
    recovery_codes = set()
    safe_path(root, "data/decision_three_engines/recovery/20260821/daily_bars", directory=True)
    for path in sorted(root.glob("data/decision_three_engines/recovery/20260821/daily_bars/*.csv.gz")):
        path = safe_path(root, path.relative_to(root))
        all_values = rows(path)
        require(all(re.fullmatch(r"20\d{6}", item.get("trade_date", "")) for item in all_values), "invalid recovery bar date")
        values = [item for item in all_values if item["trade_date"] <= as_of]
        recovery_keys.update((item["ts_code"], item["trade_date"]) for item in values)
        recovery_codes.update(item["ts_code"] for item in values)
        recovery_inventory.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path),
                                   "rows": len(values), "rows_after_as_of_excluded": len(all_values) - len(values), "fields": list(values[0]) if values else [],
                                   "start": min((item["trade_date"] for item in values), default=None),
                                   "end": max((item["trade_date"] for item in values), default=None)})

    source_fields = set(joined[0])
    audited_fields = ["t_open", "t_close", "t_high", "t_low", "t_amount", "t_turnover_pct", "t_pct_change", "t_volume", "t_pre_close",
                      "tplus1_open", "tplus1_high", "tplus1_low", "tplus1_close", "tplus1_volume", "tplus1_pre_close",
                      "t_up_limit", "tplus1_down_limit", "adj_factor", "suspend_type", "dividend", "entry_price_cap"]
    source_coverage = {field: {"present": field in source_fields,
                              "finite_rows": sum(number(item.get(field)) is not None for item in joined)} for field in audited_fields}
    manifest_paths = [OLD_LEDGER, SOURCE_LEDGER, CALENDAR,
                      Path("data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"),
                      Path("data/decision_three_engines/five_year_ledger_manifest.json")]
    output = {
        "schema_version": "dc20_profit_execution_v2_source_coverage_audit_v1",
        "plan_sha256": digest(plan_path),
        "code_sha256": digest(safe_path(here, "audit_sources.py")),
        "source_commit": source_commit,
        "as_of_date": as_of,
        "current_v2_policy": {"id": plan["label_policy"]["id"], "additional_D_frozen_cap_implemented": False,
                              "same_policy_as_existing_forward_shadow": False,
                              "meaning": "Conservative daily-open proxy only; an extra D-frozen cap and true auction-capacity contract are future gates, not implemented in this stage."},
        "read_only_sources": True,
        "actual_order_fill_observed": False,
        "auction_capacity_or_actual_execution_release_allowed": False,
        "status": "BLOCK_FULL_HISTORY_EXECUTABLE_LABELS_REQUIRE_NEW_IMMUTABLE_SOURCES",
        "source_hashes": [{"path": path.as_posix(), "sha256": digest(safe_path(root, path))} for path in manifest_paths],
        "old_ledger": {"rows": len(old), "signal_dates": len({item["signal_date"] for item in old}),
                       "codes": len(wanted_codes), "start_D": min(item["signal_date"] for item in old),
                       "end_D": max(item["signal_date"] for item in old),
                       "outcome_status_counts": dict(sorted(Counter(item["outcome_status"] for item in old).items())),
                       "T_open_price_and_volume_are_not_actual_fills": True},
        "strict_calendar": {"path": CALENDAR.as_posix(), "open_sessions": len(sessions),
                            "start_open": sessions[0], "end_open": sessions[-1], "old_D_T_T1_adjacency_violations": adjacency_failures},
        "source_five_year_fields": source_coverage,
        "raw_dates": raw_dates,
        "raw_source_inventory": inventory,
        "old_ledger_raw_row_coverage": coverage,
        "recent_official_daily_limit_pairs": {**fraction(len(paired), len(old)),
             "signal_dates": len({item["signal_date"] for item in paired}),
             "start_D": min((item["signal_date"] for item in paired), default=None),
             "end_D": max((item["signal_date"] for item in paired), default=None),
             "diagnostics": dict(sorted(recent.items())), "old_fill_counterexamples": recent_examples,
             "entry_cap_frozen_in_old_ledger": False,
             "coverage_is_not_executability": True},
        "official_auction_pairs": {"T_auction_and_T1_daily_limits": fraction(t_auction_and_t1_daily, len(old)),
                                   "T_and_T1_stk_auction_o": fraction(both_auction, len(old))},
        "inferred_10pct_sensitivity_NOT_OFFICIAL_TRUTH": {"diagnostic_only": True, "tolerance": str(tolerance),
                                                        "counts": dict(sorted(sensitivity.items()))},
        "nine_recovery_bar_files": {"inventory": recovery_inventory,
             "old_rows_with_both_T_T1_bar": sum((item["ts_code"], item["exec_date"]) in recovery_keys and (item["ts_code"], item["scheduled_exit_date"]) in recovery_keys for item in old),
             "old_codes_covered": len(wanted_codes & recovery_codes), "official_limits_or_corporate_actions_in_files": False},
        "required_new_evidence": [
            "Immutable unadjusted daily OHLC, volume, amount and official pre_close for every selected code from D through actual resolved exit; retain unavailable sessions explicitly.",
            "Official dated price-limit and suspension/status evidence; missing data must not be called suspension or no fill.",
            "Corporate-action/adjustment/cash/share entitlement evidence, or keep pre_close-versus-prior-close discontinuities unresolved.",
            "Opening-auction evidence with timestamp and amount/volume for entry and intended exit; never substitute generic stk_auction by name alone.",
            "For a future cap/auction-aligned strategy: a predeclared D-known extra price cap and aligned exit policy. Current v2 intentionally has no extra D-frozen cap and is not the existing forward-Shadow cap policy.",
            "Order/queue/capacity evidence for actual fill claims; market price observations alone only support a conservative research proxy."
        ],
        "allowed_now": "Offline label-engine and deterministic source/edge-case tests; a separately named recent-window daily-open proxy research audit only. No full-history execution-aware training or release from this coverage.",
    }
    target = write_output(output)
    print(json.dumps({"output": str(target), "status": output["status"],
                      "rows": len(old), "signal_dates": output["old_ledger"]["signal_dates"],
                      "official_daily_limit_pairs": len(paired)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
