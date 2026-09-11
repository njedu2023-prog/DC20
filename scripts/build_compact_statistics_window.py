#!/usr/bin/env python3
"""Build a read-only display window; never rewrite D28 ledgers or truth.

Only explicit business-source failures become an unavailable section. Invalid
configuration and unexpected software errors remain fatal. No inference,
settlement, materialization or network operation occurs here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.publish_primary_three_rank import build_primary_d_runtime_index
from scripts.settle_primary_observations import observation_row
from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision.primary_profit_forward_shadow_bridge import (
    validate_primary_profit_forward_shadow_public_index,
    validate_primary_profit_forward_shadow_public_state,
)

SCHEMA = "dc20_compact_statistics_window_v1"
CONFIG_PATH = "models/decision_compact_statistics_window_v1.json"
OUTPUT_PATH = "outputs/decision/compact_statistics_window.json"
PRIMARY_SUMMARY = "outputs/decision/primary_observation/summary.json"
PRIMARY_ROWS = "outputs/decision/primary_observation/rows.csv"
SHADOW_INDEX = "outputs/decision/executable_profit_research/shadow_index.json"
START = "20260910"
CONFIG = {
    "schema_version": "dc20_compact_statistics_window_config_v1",
    "window_id": "dc20_compact_statistics_from_d20260910",
    "start_signal_date": START,
    "date_axis": "signal_date_inclusive",
    "profit_scope": "NATURAL_FROZEN_PRIMARY_MIXED_SHADOW",
    "promotion_scope": "FROZEN_PRIMARY_PROMOTION_TOP3",
    "research_only": True,
    "source_ledger_mutation_allowed": False,
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
YMD = re.compile(r"20\d{6}\Z")


class WindowSourceError(ValueError):
    """A missing or invalid business source; do not publish its aggregates."""


def require(condition, message):
    if not condition:
        raise WindowSourceError(message)


def normalize(value):
    """Use the same integer representation after JSON.parse in the browser."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite window number")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def canonical_sha256(value):
    raw = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(WindowSourceError("non-finite JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WindowSourceError("invalid UTF-8 JSON source") from exc


class Sources:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.checked = {}
        self.published = {}

    def read(self, relative, expected=None, *, publish=True):
        require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
                and "\\" not in relative and all(p not in (".", "..") for p in relative.split("/")), "unsafe source path")
        path = self.root / relative
        require(all(not part.is_symlink() for part in (path, *path.parents) if part != self.root.parent), "symlink source")
        require(path.is_file() and path.resolve().is_relative_to(self.root), f"source missing: {relative}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise WindowSourceError(f"source unreadable: {relative}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        require(expected is None or SHA.fullmatch(str(expected)) and digest == expected, f"source SHA mismatch: {relative}")
        require(relative not in self.checked or self.checked[relative] == digest, "source changed during build")
        self.checked[relative] = digest
        if publish:
            self.published[relative] = digest
        return raw

    def json(self, relative, expected=None, *, publish=True):
        result = strict_json(self.read(relative, expected, publish=publish))
        require(isinstance(result, dict), "JSON source must be an object")
        return result

    def bindings(self, items):
        require(isinstance(items, list), "source manifest missing")
        paths = [item.get("path") for item in items if isinstance(item, dict)]
        require(len(paths) == len(items) == len(set(paths)), "duplicate source manifest")
        for item in items:
            self.read(item["path"], item.get("sha256"))

    def finish(self):
        for path, sha in tuple(self.checked.items()):
            self.read(path, sha, publish=False)
        return [{"path": path, "sha256": sha} for path, sha in sorted(self.published.items())]


def original_validator(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, OSError) as exc:
        raise WindowSourceError(str(exc)) from exc


def valid_asof(value, dates, closed):
    require(isinstance(value, str) and YMD.fullmatch(value) and value in dates and value <= closed,
            "as-of is not a closed pinned SSE session")
    return value


def bound_selection_inputs(sources, selected):
    bindings = selected.get("source_bindings", {})
    for key in ("mixed_projection", "primary_receipt", "runtime_features"):
        item = bindings.get(key)
        require(isinstance(item, dict), "natural selection source binding missing")
        sources.read(item.get("path"), item.get("sha256"))
        if item.get("csv_path"):
            sources.read(item["csv_path"], item.get("csv_sha256"))
    item = bindings.get("three_rank", {})
    sources.read(item.get("json_path"), item.get("json_sha256"))
    sources.read(item.get("csv_path"), item.get("csv_sha256"))


def profit_section(sources, signal_date, dates, closed):
    index = sources.json(SHADOW_INDEX, publish=False)
    original_validator(validate_primary_profit_forward_shadow_public_index, index)
    require(index.get("latest_signal_date") == signal_date, "current report has no same-D natural Shadow state")
    state = sources.json(index.get("latest_state_url"), index.get("latest_state_sha256"))
    original_validator(validate_primary_profit_forward_shadow_public_state, state, repo_root=sources.root)
    require(state.get("signal_date") == signal_date, "public Shadow report date mismatch")
    binding = state.get("source_bindings", {}).get("statistics", {})
    require(set(binding) == {"path", "sha256", "snapshot_sha256", "as_of_date"}, "statistics binding surface mismatch")
    summary = sources.json(binding["path"], binding["sha256"])
    original_validator(settlement.validate_statistics, summary, require_public_cumulative=True)
    asof = valid_asof(summary.get("as_of_date"), dates, closed)
    require(asof >= START, "profit source predates display window")
    require(binding["as_of_date"] == asof and binding["snapshot_sha256"] == summary.get("snapshot_sha256"), "statistics snapshot binding mismatch")
    items = summary.get("input_files")
    require(isinstance(items, list), "statistics input manifest missing")
    paths = [item.get("path") for item in items if isinstance(item, dict)]
    require(len(paths) == len(items) == len(set(paths)), "statistics manifest duplicate")
    inputs = {item["path"]: item["sha256"] for item in items}
    records, recorded_days = [], 0
    for path in sorted(inputs):
        match = re.fullmatch(r"data/decision_executable_profit/forward/selections/shadow_(20\d{6})\.json", path)
        if not match or not START <= match[1] <= asof:
            continue
        d = match[1]
        require(d <= signal_date, "statistics contain a selection after report D")
        sources.read(path, inputs[path])
        selection_path, selection, selected = original_validator(settlement.load_selection, sources.root, d)
        original_validator(settlement._validate_adjacent_dates, dates, d, selection["exec_date"], selection["exit_date"])
        require(selection.get("schema_version") == settlement.PRIMARY_MIXED_SELECTION_SCHEMA
                and selection.get("source_bindings", {}).get("mixed_projection", {}).get("generation_mode") == "NATURAL"
                and selection.get("source_bindings", {}).get("primary_receipt", {}).get("generation_mode") == "NATURAL", "only natural primary mixed selections belong in this window")
        bound_selection_inputs(sources, selection)
        recorded_days += 1
        expected = settlement._selection_binding(selection_path, selection, selected)
        t_rows, t1_rows = {}, {}
        t_path = f"data/decision_executable_profit/forward/verifications/t_verification_{d}.json"
        t1_path = f"data/decision_executable_profit/forward/settlements/settlement_{d}.json"
        for relative, validator, rows in ((t_path, settlement.validate_t_verification, t_rows), (t1_path, settlement.validate_t1_settlement, t1_rows)):
            if relative not in inputs:
                continue  # Never pick unbound files from a newer directory snapshot.
            require(selection["exec_date"] <= asof, "future T truth in statistics")
            truth = sources.json(relative, inputs[relative])
            original_validator(validator, truth)
            require(truth.get("selection") == expected, "truth selection binding mismatch")
            require((truth.get("signal_date"), truth.get("exec_date"), truth.get("exit_date")) == (d, selection["exec_date"], selection["exit_date"]), "truth dates mismatch")
            sources.bindings(truth.get("source_files"))
            for raw in truth["source_files"]:
                date = re.search(r"/(20\d{6})/", raw["path"])
                require(date is not None and date[1] <= asof, "future market source")
            if relative == t1_path:
                require(t_path in inputs and truth.get("t_verification", {}).get("file_sha256") == inputs[t_path], "settlement T SHA mismatch")
                for row in truth["rows"]:
                    if row.get("proxy_fill") == 1:
                        actual = row.get("actual_exit_date")
                        require(actual in dates and selection["exit_date"] <= actual <= asof, "future/invalid actual exit")
                        examined = dates[dates.index(selection["exit_date"]):dates.index(actual)+1]
                        require(len(examined)-1 == row.get("delayed_trading_days"), "exit skipped SSE sessions")
                        for day in examined:
                            require(all(any(f"/{day}/" in src["path"] and src["path"].endswith(f"/{table}.csv") for src in truth["source_files"]) for table in ("daily", "stk_limit")), "exit truth partition skipped")
            rows.update({int(row["shadow_slot"]): row for row in truth["rows"]})
        for row in selected:
            slot = row["shadow_slot"]
            t, t1 = t_rows.get(slot), t1_rows.get(slot)
            fill = t.get("proxy_fill") if t is not None else None
            require(not t1 or t and t1.get("proxy_fill") == fill, "T/T1 fill mismatch")
            records.append(dict(signal_date=d, shadow_slot=slot, ts_code=row["ts_code"], stage_transition=row["stage_transition"],
                t_validated=t is not None, proxy_fill=fill, terminal=t1 is not None or fill == 0,
                net_return_after_cost=t1.get("net_return_after_cost") if t1 else None,
                stress_net_return=t1.get("stress_net_return") if t1 else None,
                strategy_slot_return=t1.get("strategy_slot_return") if t1 else 0.0 if fill == 0 else None,
                delayed_trading_days=t1.get("delayed_trading_days") if t1 else None,
                blocked_exit_sessions=t1.get("blocked_exit_sessions") if t1 else 0))
    groups = {"all_selected_slots": records, "shadow_slot_1": [r for r in records if r["shadow_slot"] == 1],
              "shadow_slot_2": [r for r in records if r["shadow_slot"] == 2],
              "stage_2_to_3": [r for r in records if r["stage_transition"] == "2→3"],
              "stage_3_to_4": [r for r in records if r["stage_transition"] == "3→4"]}
    return dict(status="READY", as_of_date=asof, source_statistics=dict(binding),
        recorded_days=recorded_days, recorded_slots=len(records),
        cohorts={key: settlement._cohort_metrics(rows) for key, rows in groups.items()},
        forward_signal_date_progress_180=dict(observed_signal_dates=recorded_days, target_signal_dates=180,
            remaining_signal_dates=max(0, 180-recorded_days), progress_pct=min(1, recorded_days/180)*100,
            release_sample_reached=recorded_days >= 180), probability_diagnostics=summary["probability_diagnostics"])


def numeric(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise WindowSourceError("invalid observation number") from exc
    require(math.isfinite(number), "non-finite observation number")
    return number


def promotion_section(sources, signal_date, dates, closed):
    summary = sources.json(PRIMARY_SUMMARY)
    require(summary.get("schema_version") == "dc20_primary_observation_summary_v1"
            and summary.get("public_start_signal_date") == "20260828" and summary.get("scope") == "frozen_primary_topn", "primary observation identity mismatch")
    policy = summary.get("policy", {})
    require(policy.get("id") == "p0_daily_open_observation_proxy_v1" and policy.get("round_trip_cost_rate") == 0.0045
            and policy.get("mixed_shadow_ledger_included") is False and policy.get("retrospective_recovery_included") is False
            and policy.get("official_trade_action_created") is False, "primary observation policy mismatch")
    require(summary.get("rows_path") == PRIMARY_ROWS, "primary row path mismatch")
    asof = valid_asof(summary.get("as_of_date"), dates, closed)
    require(asof >= START, "promotion source predates display window")
    raw = sources.read(PRIMARY_ROWS, summary.get("rows_sha256"))
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "duplicate CSV columns")
    all_rows = list(reader)
    require(all(None not in row for row in all_rows), "malformed CSV row")
    require(len(all_rows) == summary.get("statistics", {}).get("observation_rows"), "observation row count mismatch")
    days = summary.get("daily_summaries")
    bindings = summary.get("bindings")
    require(isinstance(days, list) and isinstance(bindings, list), "primary daily bindings missing")
    day_dates = [day.get("signal_date") for day in days]
    require(len(day_dates) == len(set(day_dates)) and set(row.get("signal_date") for row in all_rows) <= set(day_dates), "primary daily dates duplicated or unbound")
    raw_inputs = summary.get("source_files")
    require(isinstance(raw_inputs, list) and all(isinstance(i, dict) for i in raw_inputs), "primary truth sources missing")
    raw_paths = [item.get("path") for item in raw_inputs]
    require(len(raw_paths) == len(set(raw_paths)), "primary raw manifest duplicate")
    table_cache = {}
    def tables(date, name):
        require(date <= asof, "future observation source read")
        key = (date, name)
        if key not in table_cache:
            matches = [item for item in raw_inputs if f"/{date}/" in str(item.get("path")) and str(item.get("path")).endswith(f"/{name}.csv")]
            require(len(matches) <= 1, "ambiguous bound observation partition")
            if not matches:
                table_cache[key] = {}
            else:
                item = matches[0]
                sources.read(item["path"], item.get("sha256"))
                table_cache[key] = original_validator(settlement._market_rows, sources.root/item["path"], date)
        return table_cache[key]
    rows_for_window = []
    for day in days:
        d, t, t1 = (day.get(key) for key in ("signal_date", "exec_date", "exit_date"))
        original_validator(settlement._validate_adjacent_dates, dates, d, t, t1)
        group = [row for row in all_rows if row.get("signal_date") == d]
        require(len(group) == day.get("rows") and len({row.get("ts_code") for row in group}) == len(group), "duplicate/missing daily member")
        if d < START:
            continue
        require(d <= asof and d <= signal_date, "future observation D")
        same = [item for item in bindings if item.get("signal_date") == d]
        require(len(same) == 1 and same[0].get("status") == "PROSPECTIVE", "natural P0 binding missing")
        p0 = same[0].get("p0", {})
        expected_paths = {"latest_receipt_url": f"outputs/decision/primary_d_receipt_{d}.json",
                          "latest_runtime_features_url": f"outputs/decision/primary_d_runtime_features_{d}.csv",
                          "latest_three_rank_json_url": f"outputs/decision/three_rank_top10_{d}.json",
                          "latest_three_rank_csv_url": f"outputs/decision/three_rank_top10_{d}.csv"}
        for key, path in expected_paths.items():
            require(p0.get(key) == path, "P0 dated source mismatch")
            sources.read(path, p0.get(key.removesuffix("url") + "sha256"))
        actual = original_validator(build_primary_d_runtime_index, sources.root,
            receipt_path=sources.root / expected_paths["latest_receipt_url"],
            runtime_path=sources.root / expected_paths["latest_runtime_features_url"],
            three_rank_json_path=sources.root / expected_paths["latest_three_rank_json_url"],
            three_rank_csv_path=sources.root / expected_paths["latest_three_rank_csv_url"])
        require(actual == p0, "P0 runtime pointer does not match original validation")
        receipt = sources.json(expected_paths["latest_receipt_url"])
        frozen = sources.json(expected_paths["latest_three_rank_json_url"])
        require(receipt.get("generation_mode") == "NATURAL" and receipt.get("prospective") is True
                and receipt.get("forward_eligible") is True and receipt.get("not_forward_generated") is False, "retrospective P0 cannot enter window")
        try:
            generated = datetime.fromisoformat(frozen["generated_at_utc"].replace("Z", "+00:00"))
        except (ValueError, KeyError) as exc:
            raise WindowSourceError("invalid P0 generation timestamp") from exc
        deadline = datetime.strptime(t, "%Y%m%d").replace(hour=9, minute=25, tzinfo=ZoneInfo("Asia/Shanghai"))
        require(generated.tzinfo is not None and generated < deadline, "P0 was not frozen before T09:25")
        require(receipt.get("inputs", {}).get("calendar", {}).get("sha256") == settlement.CALENDAR_SHA256, "P0 calendar changed")
        frozen_rows = {row["ts_code"]: row for row in frozen["rows"]}
        require(len(frozen_rows) == len(group), "P0 member count mismatch")
        require(len({row.get("promotion_rank") for row in group}) == len(group), "duplicate promotion rank")
        clean = []
        for row in group:
            identity = frozen_rows.get(row.get("ts_code"))
            rank, hit, fill, net, slot = [numeric(row.get(key)) for key in ("promotion_rank", "continuation_limit_up_hit", "proxy_fill", "actual_net_return", "slot_net_return")]
            status = row.get("validation_status")
            pending_t = status in ("PENDING_T", "MISSING_T_TRUTH")
            require(identity and row.get("name") == identity["name"] and rank == identity["promotion_rank"]
                    and (row.get("exec_date"), row.get("exit_date")) == (t, t1)
                    and row.get("truth_source") == "daily_open_proxy" and row.get("actual_order_fill_observed") == "False", "observation identity mismatch")
            require(status in ("PENDING_T", "MISSING_T_TRUTH", "PENDING_T1", "MISSING_T1_TRUTH", "UNRESOLVED_EXIT_PROXY", "FINAL_VERIFIED_PROXY", "FINAL_NO_FILL_PROXY"), "unknown observation status")
            require((hit is None and fill is None) if pending_t else (hit in (0, 1) and fill in (0, 1)), "T truth/status conflict")
            require((net is not None and slot == net and fill == 1) if status == "FINAL_VERIFIED_PROXY" else net is None, "observation filled return conflict")
            require((slot == 0 and fill == 0) if status == "FINAL_NO_FILL_PROXY" else (status == "FINAL_VERIFIED_PROXY" or slot is None), "observation no-fill return conflict")
            require(not ((not pending_t and asof < t) or (status == "PENDING_T" and asof >= t)
                or (status == "MISSING_T_TRUTH" and asof < t) or (status == "PENDING_T1" and asof >= t1)
                or (status in ("MISSING_T1_TRUTH", "UNRESOLVED_EXIT_PROXY", "FINAL_VERIFIED_PROXY", "FINAL_NO_FILL_PROXY") and asof < t1)), "observation maturity mismatch")
            reproduced = observation_row(identity, d, t, t1, asof, tables, dates)
            require(status == reproduced["validation_status"], "observation status differs from bound daily truth")
            for key, value in (("continuation_limit_up_hit", hit), ("proxy_fill", fill), ("actual_net_return", net), ("slot_net_return", slot)):
                expected = reproduced.get(key)
                require(value is None if expected is None else value is not None and abs(value-expected) < 1e-12,
                        "observation value differs from bound daily truth")
            if status == "FINAL_VERIFIED_PROXY":
                require(row.get("actual_exit_date") in dates and t1 <= row["actual_exit_date"] <= asof
                        and row["actual_exit_date"] == reproduced["actual_exit_date"], "future observation exit")
            clean.append(dict(rank=int(rank), hit=hit, net=net, slot=slot, status=status))
        counts = dict(t_validated_rows=sum(r["hit"] is not None for r in clean),
                      final_verified_trades=sum(r["net"] is not None for r in clean), settled_rows=sum(r["slot"] is not None for r in clean))
        counts.update({key: sum(r["status"] == status for r in clean) for key, status in (
            ("pending_t_rows", "PENDING_T"), ("pending_t1_rows", "PENDING_T1"), ("missing_t_truth_rows", "MISSING_T_TRUTH"),
            ("missing_t1_truth_rows", "MISSING_T1_TRUTH"), ("unresolved_exit_rows", "UNRESOLVED_EXIT_PROXY"))})
        require(all(day.get(key) == count for key, count in counts.items()), "observation daily counts mismatch")
        rows_for_window.extend(clean)
    ranks = []
    for rank in (1, 2, 3):
        selected = [row for row in rows_for_window if row["rank"] == rank]
        verified = [row for row in selected if row["hit"] is not None]
        hits = sum(row["hit"] for row in verified)
        ranks.append(dict(rank=rank, count=len(selected), verified=len(verified), hits=hits,
                          hit_rate=hits/len(verified) if verified else None))
    return dict(status="READY", as_of_date=asof,
        source_summary=dict(path=PRIMARY_SUMMARY, sha256=sources.checked[PRIMARY_SUMMARY]),
        rows_sha256=sources.checked[PRIMARY_ROWS], ranks=ranks)


def validate_window(payload):
    require(set(payload) == {"schema_version", "start_signal_date", "report_signal_date", "config_sha256", "profit", "promotion", "source_files", "snapshot_sha256"}, "window field surface mismatch")
    require(payload["schema_version"] == SCHEMA and payload["start_signal_date"] == START
            and YMD.fullmatch(str(payload["report_signal_date"])) and SHA.fullmatch(str(payload["config_sha256"])), "window identity mismatch")
    for name in ("profit", "promotion"):
        section = payload[name]
        require(isinstance(section, dict) and section.get("status") in ("READY", "UNAVAILABLE"), "invalid window section")
        if section["status"] == "UNAVAILABLE":
            require(set(section) == {"status", "reason"} and isinstance(section["reason"], str) and section["reason"], "unavailable section must not expose aggregates")
        else:
            require(YMD.fullmatch(str(section.get("as_of_date"))) and section["as_of_date"] >= START, "section as-of missing")
            required = {"source_statistics", "cohorts", "recorded_days", "recorded_slots", "forward_signal_date_progress_180", "probability_diagnostics"} if name == "profit" else {"source_summary", "rows_sha256", "ranks"}
            require(set(section) == required | {"status", "as_of_date"}, "ready section surface mismatch")
            if name == "profit":
                binding = section["source_statistics"]
                require(isinstance(binding, dict) and set(binding) == {"path", "sha256", "snapshot_sha256", "as_of_date"}
                        and binding["as_of_date"] == section["as_of_date"]
                        and all(SHA.fullmatch(str(binding[key])) for key in ("sha256", "snapshot_sha256")), "profit binding invalid")
                cohorts = section["cohorts"]
                require(isinstance(cohorts, dict) and set(cohorts) == {"all_selected_slots", "shadow_slot_1", "shadow_slot_2", "stage_2_to_3", "stage_3_to_4"}, "profit cohort surface invalid")
                for cohort in cohorts.values():
                    require(isinstance(cohort, dict) and set(cohort) == set(settlement._cohort_metrics([])), "profit cohort fields invalid")
                    for key in ("selected_slots", "selection_dates", "effective_dates", "t_validated_slots", "proxy_fill_slots", "proxy_no_fill_slots", "t1_settled_slots", "wins_after_cost", "terminal_slots", "pending_slots", "pending_validation_slots", "pending_settlement_slots"):
                        require(type(cohort[key]) is int and cohort[key] >= 0, "profit count invalid")
                    require(cohort["wins_after_cost"] <= cohort["t1_settled_slots"] <= cohort["proxy_fill_slots"]
                            and cohort["proxy_fill_slots"] + cohort["proxy_no_fill_slots"] == cohort["t_validated_slots"] <= cohort["selected_slots"]
                            and cohort["terminal_slots"] + cohort["pending_slots"] == cohort["selected_slots"]
                            and cohort["pending_validation_slots"] + cohort["pending_settlement_slots"] == cohort["pending_slots"], "profit denominator invalid")
                    settled = cohort["t1_settled_slots"]
                    require((cohort["win_rate"] is None and cohort["mean_net_return_after_cost"] is None) if not settled else
                            (cohort["mean_net_return_after_cost"] is not None and abs(cohort["win_rate"]-cohort["wins_after_cost"]/settled) < 1e-10), "profit win/mean invalid")
                    daily = cohort["daily_portfolio"]
                    require(isinstance(daily, list) and len(daily) == cohort["effective_dates"], "profit completed days invalid")
                    dates = [row.get("signal_date") for row in daily]
                    require(dates == sorted(set(dates)) and all(START <= d <= min(section["as_of_date"], payload["report_signal_date"]) for d in dates), "profit portfolio dates invalid")
                    rebuilt = settlement._portfolio_metrics([(row["signal_date"], row["equal_weight_strategy_return"]) for row in daily])
                    for key in ("equal_weight_cumulative_return", "maximum_drawdown"):
                        require(cohort[key] is None if rebuilt[key] is None else cohort[key] is not None and abs(cohort[key]-rebuilt[key]) < 1e-9, "profit portfolio does not rebase at window start")
                slots = cohorts["all_selected_slots"]["selected_slots"]
                require(type(section["recorded_days"]) is int and section["recorded_days"] >= 0 and section["recorded_slots"] == slots
                        and slots <= 2*section["recorded_days"]
                        and slots == cohorts["shadow_slot_1"]["selected_slots"] + cohorts["shadow_slot_2"]["selected_slots"]
                        and slots == cohorts["stage_2_to_3"]["selected_slots"] + cohorts["stage_3_to_4"]["selected_slots"], "profit window size invalid")
                progress = section["forward_signal_date_progress_180"]
                require(progress["observed_signal_dates"] == section["recorded_days"] and progress["target_signal_dates"] == 180
                        and progress["remaining_signal_dates"] == max(0, 180-section["recorded_days"])
                        and progress["release_sample_reached"] is (section["recorded_days"] >= 180), "window progress invalid")
                diagnostics = section["probability_diagnostics"]
                require(diagnostics.get("status") == "UNCALIBRATED" and all(diagnostics.get(key) is None for key in ("brier_score", "expected_calibration_error", "log_loss")), "window proxy calibration claim invalid")
            else:
                binding = section["source_summary"]
                require(isinstance(binding, dict) and set(binding) == {"path", "sha256"} and binding["path"] == PRIMARY_SUMMARY
                        and SHA.fullmatch(str(binding["sha256"])) and SHA.fullmatch(str(section["rows_sha256"])), "promotion binding invalid")
                ranks = section["ranks"]
                require(isinstance(ranks, list) and len(ranks) == 3 and [row.get("rank") for row in ranks] == [1, 2, 3], "promotion ranks invalid")
                for row in ranks:
                    require(set(row) == {"rank", "count", "verified", "hits", "hit_rate"}
                            and all(type(row[key]) is int and row[key] >= 0 for key in ("count", "verified", "hits"))
                            and row["hits"] <= row["verified"] <= row["count"], "promotion count invalid")
                    require(row["hit_rate"] is None if not row["verified"] else row["hit_rate"] is not None and abs(row["hit_rate"]-row["hits"]/row["verified"]) < 1e-10, "promotion success rate invalid")
    inputs = payload["source_files"]
    require(isinstance(inputs, list) and all(isinstance(i, dict) and set(i) == {"path", "sha256"} and SHA.fullmatch(str(i["sha256"])) for i in inputs), "invalid window sources")
    paths = [item["path"] for item in inputs]
    require(paths == sorted(set(paths)), "window sources must be sorted and unique")
    require({i["path"]: i["sha256"] for i in inputs}.get(CONFIG_PATH) == payload["config_sha256"], "config not source-bound")
    bindings = {i["path"]: i["sha256"] for i in inputs}
    for name, key in (("profit", "source_statistics"), ("promotion", "source_summary")):
        if payload[name]["status"] == "READY":
            source = payload[name][key]
            require(bindings.get(source["path"]) == source["sha256"], "section not source-bound")
    if payload["promotion"]["status"] == "READY":
        require(bindings.get(PRIMARY_ROWS) == payload["promotion"]["rows_sha256"], "promotion rows not source-bound")
    require(payload["snapshot_sha256"] == canonical_sha256({k: v for k, v in payload.items() if k != "snapshot_sha256"}), "window snapshot mismatch")


def build_window(root, *, signal_date, now=None):
    root = Path(root).resolve(strict=True)
    config_sources = Sources(root)
    try:
        config = config_sources.json(CONFIG_PATH)
        require(config == CONFIG, "compact statistics window config drifted")
    except WindowSourceError as exc:
        raise ValueError(f"invalid window configuration: {exc}") from exc
    if not isinstance(signal_date, str) or not YMD.fullmatch(signal_date):
        raise ValueError("invalid report signal date")
    dates = settlement._strict_open_dates(root)
    if START not in dates or signal_date not in dates:
        raise ValueError("window/report date is not a pinned SSE session")
    config_sources.read(str(settlement.CALENDAR_PATH), settlement.CALENDAR_SHA256)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("clock must have a timezone")
    clock = clock.astimezone(ZoneInfo("Asia/Shanghai"))
    closed = (clock if clock.hour >= 15 else clock-timedelta(days=1)).strftime("%Y%m%d")
    payload = dict(schema_version=SCHEMA, start_signal_date=START, report_signal_date=signal_date,
                   config_sha256=config_sources.checked[CONFIG_PATH])
    collected = dict(config_sources.published)
    for name, function in (("profit", profit_section), ("promotion", promotion_section)):
        sources = Sources(root)
        try:
            section = function(sources, signal_date, dates, closed)
            source_files = sources.finish()
        except WindowSourceError as exc:
            payload[name] = dict(status="UNAVAILABLE", reason=str(exc))
        else:
            payload[name] = section
            for item in source_files:
                require(item["path"] not in collected or collected[item["path"]] == item["sha256"], "section source conflict")
                collected[item["path"]] = item["sha256"]
    config_sources.finish()
    payload["source_files"] = [dict(path=path, sha256=sha) for path, sha in sorted(collected.items())]
    payload = normalize(payload)
    payload["snapshot_sha256"] = canonical_sha256(payload)
    validate_window(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_window(args.root, signal_date=args.signal_date)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output:
        if args.output.name != "compact_statistics_window.json" or args.output.is_symlink() or args.output.exists():
            raise ValueError("output must be a new compact_statistics_window.json artifact")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    else:
        print(raw, end="")


if __name__ == "__main__":
    main()
