"""Research-only counterfactual labels for the versioned 10:00/limit-hold exit.

This is not a ledger writer. It never reads an old opening-exit outcome and
never relabels an official historical record. Features and complete candidate
membership are bound separately from later price truth. A reconstructed D-only
feature snapshot is explicitly not evidence of a naturally frozen prediction.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision.shadow_exit_1000 import EXIT_POLICY_ID, resolve_exit_1000
from top10decision.decision.shadow_exit_minute_truth import load_exit_minutes

SCHEMA = "dc20_profit_1000_research_candidates_v1"
LABEL_SCHEMA = "dc20_profit_1000_research_labels_v1"
SETTLED = "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"
COST_RATE = 0.0045
SHANGHAI = ZoneInfo("Asia/Shanghai")
_FEATURE_FORBIDDEN = re.compile(r"(?:^|_)(?:label|target|exit|profit|future|t1|tplus1|outcome|net|fill|pnl)(?:_|$)", re.I)


def _date(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"20\d{6}", value) is None:
        raise ValueError("date must be exact YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def _finite(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric truth")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("invalid finite numeric truth")
    return number


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("feature availability timestamp missing")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("availability timestamp needs timezone")
    return stamp.astimezone(SHANGHAI)


def _at(day: str, time: str) -> str:
    return datetime.strptime(day + " " + time, "%Y%m%d %H:%M:%S").replace(tzinfo=SHANGHAI).isoformat()


def _binding(root: Path, binding: Mapping[str, Any]) -> tuple[Path, bytes]:
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
        raise ValueError("source binding must contain exact path and sha256")
    relative, sha = binding["path"], binding["sha256"]
    if not isinstance(relative, str) or not relative or "\\" in relative or relative.startswith("/") or any(p in ("", ".", "..") for p in relative.split("/")):
        raise ValueError("unsafe source path")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise ValueError("source SHA invalid")
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root) or root not in path.resolve().parents or not path.is_file():
        raise ValueError("source must be a bound regular repository file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("source SHA mismatch")
    return path, raw


def _load_candidates(root: Path, manifest: Mapping[str, Any], dates: list[str]) -> tuple[list[dict], list[dict]]:
    if manifest.get("schema_version") != SCHEMA or manifest.get("evidence_kind") not in {"RETROSPECTIVE_D_ONLY_RECONSTRUCTION", "NATURAL_PRE_BUY_FREEZE"}:
        raise ValueError("candidate source/evidence contract missing")
    columns = manifest.get("feature_columns")
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)) or any(not isinstance(x, str) or not x or _FEATURE_FORBIDDEN.search(x) for x in columns):
        raise ValueError("explicit non-outcome feature allowlist required")
    bindings = manifest.get("source_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("original immutable feature sources must be bound")
    seen = set()
    for item in bindings:
        _binding(root, item)
        if item["path"] in seen:
            raise ValueError("duplicate feature source binding")
        seen.add(item["path"])
    bindings = [dict(item) for item in bindings]
    if ("rows" in manifest) == ("candidate_file" in manifest):
        raise ValueError("supply exactly one candidate rows or bound candidate file")
    if "candidate_file" in manifest:
        file_binding = manifest["candidate_file"]
        _, raw = _binding(root, file_binding)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("candidate CSV header missing or duplicated")
        raw_rows = list(reader)
        if file_binding["path"] not in seen:
            bindings.append(dict(file_binding))
    else:
        raw_rows = manifest["rows"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("candidate cohort cannot be empty")
    expected = manifest.get("expected_candidate_codes")
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError("complete D cohort membership is required")
    for day, codes in expected.items():
        _date(day)
        if not isinstance(codes, list) or not codes or len(codes) != len(set(codes)) or any(not isinstance(c, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", c) is None for c in codes):
            raise ValueError("invalid expected candidate membership")
    result, identities, by_day, ranks = [], set(), {}, {}
    for source in raw_rows:
        if not isinstance(source, Mapping):
            raise ValueError("candidate row is not a mapping")
        day, code = _date(source.get("signal_date")), source.get("ts_code")
        if not isinstance(code, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", code) is None or (day, code) in identities:
            raise ValueError("candidate code invalid or duplicated")
        identities.add((day, code))
        if day not in dates or dates.index(day) + 2 >= len(dates):
            raise ValueError("candidate D/T/T+1 calendar coverage missing")
        index = dates.index(day)
        t, t1 = dates[index + 1:index + 3]
        for key, correct in (("exec_date", t), ("scheduled_exit_date", t1)):
            if source.get(key) not in (None, "", correct):
                raise ValueError("candidate supplied wrong adjacent trading dates")
        stage = str(source.get("stage_transition", source.get("stage", "")))
        stage = {"2": "2→3", "3": "3→4", "2_to_3": "2→3", "3_to_4": "3→4"}.get(stage, stage)
        if stage not in {"2→3", "3→4"}:
            raise ValueError("only frozen 2-to-3 and 3-to-4 candidates allowed")
        rank = _finite(source.get("promotion_rank"), positive=True)
        if rank != int(rank) or int(rank) in ranks.setdefault(day, set()):
            raise ValueError("promotion rank must be a unique positive integer per D")
        ranks[day].add(int(rank))
        feature_day = _date(source.get("feature_as_of_date"))
        available = _timestamp(source.get("feature_available_at"))
        if feature_day > day or available < _timestamp(_at(feature_day, "00:00:00")) or available >= _timestamp(_at(t, "09:25:00")):
            raise ValueError("features are not known before auction entry")
        features = source.get("features")
        if features is None:
            features = json.loads(source.get("features_json", "null"))
        if not isinstance(features, Mapping) or set(features) != set(columns):
            raise ValueError("features must match explicit non-outcome allowlist")
        # Missing historical D features remain missing. Imputation belongs to
        # each training fold, never to a global preprocessing pass here.
        features = {key: None if features[key] is None else _finite(features[key]) for key in columns}
        cap = source.get("shadow_max_price")
        cap = None if cap in (None, "") else _finite(cap, positive=True)
        result.append({"signal_date": day, "ts_code": code, "stage_transition": stage,
                       "promotion_rank": int(rank), "exec_date": t, "scheduled_exit_date": t1,
                       "feature_as_of_date": feature_day, "feature_available_at": available.isoformat(),
                       "features": features, "shadow_max_price": cap})
        by_day.setdefault(day, set()).add(code)
    if by_day != {day: set(codes) for day, codes in expected.items()}:
        raise ValueError("candidate D cohort incomplete or unexpected rows present")
    return sorted(result, key=lambda r: (r["signal_date"], r["promotion_rank"])), bindings


def build_labels(repo_root: Path, manifest: Mapping[str, Any], *, as_of_date: str) -> dict:
    """Replay complete research cohorts from exact repository price sources.

    ``as_of_date`` means the last *completed* exchange session. Labels mature at
    actual exit but are only available after that session's complete 240-bar
    source exists (15:00). Optional ``truth_sources`` locks a repeated build to
    the previous build's source bytes; a changed or new unbound source fails.
    No promotion rank, outcome, weight, selection, or ledger is written.
    """
    root = Path(repo_root).resolve(strict=True)
    dates = settlement._strict_open_dates(root)
    as_of = _date(as_of_date)
    if as_of not in dates:
        raise ValueError("as-of must be a completed strict exchange session")
    candidates, feature_bindings = _load_candidates(root, manifest, dates)
    if any(row["signal_date"] > as_of for row in candidates):
        raise ValueError("candidate features are beyond as-of cutoff")
    sources = {b["path"]: dict(b) for b in feature_bindings}
    calendar_binding = settlement._source_binding(root, root / settlement.CALENDAR_PATH)
    sources[calendar_binding["path"]] = calendar_binding
    expected = manifest.get("truth_sources")
    if expected is not None:
        if not isinstance(expected, list) or len({b["path"] for b in expected}) != len(expected):
            raise ValueError("truth source bindings invalid or duplicated")
        for binding in expected:
            _binding(root, binding)
        expected = {b["path"]: b["sha256"] for b in expected}

    def bind(binding):
        _binding(root, binding)
        if expected is not None and expected.get(binding["path"]) != binding["sha256"]:
            raise ValueError("truth source is unbound or changed")
        if binding["path"] in sources and sources[binding["path"]] != binding:
            raise ValueError("source changed during label replay")
        sources[binding["path"]] = dict(binding)

    table_cache = {}
    auction_cache = {}
    candidate_codes_by_t = {}
    for candidate in candidates:
        candidate_codes_by_t.setdefault(candidate["exec_date"], set()).add(candidate["ts_code"])
    def missing(day, name, code):
        row.update(missing_evidence_date=day, missing_evidence_code=code, missing_evidence_kind=name)

    def table(day, name):
        if day > as_of:
            raise ValueError("future market truth forbidden")
        key = (day, name)
        if key not in table_cache:
            path = settlement._find_market_file(root, day, name)
            if path is None:
                missing(day, name, row["ts_code"])
                return None, {}
            binding = settlement._source_binding(root, path)
            bind(binding)
            rows = settlement._market_rows(path, day)
            table_cache[key] = path, rows, binding
        path, rows, binding = table_cache[key]
        bind(binding)
        return path, rows

    def exact_row(day, name, code):
        value = table(day, name)[1].get(code)
        if value is None:
            missing(day, name, code)
        return value

    output = []
    for candidate in candidates:
        row = {**candidate, "label_policy_id": EXIT_POLICY_ID,
               "entry_policy_id": "research_auction_or_open_no_cap_v1" if candidate["shadow_max_price"] is None else "research_auction_or_open_frozen_cap_v1",
               "label_status": "PENDING_T", "proxy_fill": None,
               "entry_price": None, "entry_price_source": None, "capacity_proxy_verified": False,
               "actual_capacity_verified": False,
               "net_return": None, "conditional_net_return": None, "slot_net_return": None,
               "label_available_date": None, "label_available_at": None, "label_maturity_at": None,
               "actual_exit_date": None, "actual_exit_time": None, "decision_time": None,
               "held_limit_up_sessions": None, "round_trip_cost_rate": COST_RATE,
               "research_only": True, "actual_execution_claimed": False,
               "feature_evidence_kind": manifest["evidence_kind"], "cohort_complete": False,
               "missing_evidence_date": None, "missing_evidence_code": None, "missing_evidence_kind": None}
        output.append(row)
        t, t1, code = row["exec_date"], row["scheduled_exit_date"], row["ts_code"]
        if t > as_of:
            continue
        try:
            daily_path, daily_rows = table(t, "daily")
            _, limit_rows = table(t, "stk_limit")
            daily, limits = daily_rows.get(code), limit_rows.get(code)
            if daily is None or limits is None:
                missing(t, "daily" if daily is None else "stk_limit", code)
                row["label_status"] = "PENDING_T_MISSING_DAILY_OR_LIMITS"
                continue
            prices = {key: _finite(daily.get(key), positive=True) for key in ("open", "high", "low", "close", "pre_close")}
            volume = _finite(daily.get("vol"))
            up, down = _finite(limits.get("up_limit"), positive=True), _finite(limits.get("down_limit"), positive=True)
            if volume < 0 or not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"] or down >= up or prices["high"] > up + 1e-8 or prices["low"] < down - 1e-8:
                raise ValueError("invalid T daily OHLC/limit/volume")
            if t not in auction_cache:
                auction_sources = settlement._verified_auction_sources_v2(root, t)
                for auction in auction_sources:
                    bind(auction["file"])
                    bind(auction["metadata"])
                # Verify every full-market row and its original file/metadata
                # first, then retain only this T's entire candidate cohort.
                # Caching 5,000 rows for each historical T would otherwise keep
                # millions of dictionaries alive during a label replay.
                auction_cache[t] = [
                    {**auction, "rows": {candidate_code: auction["rows"][candidate_code]
                                         for candidate_code in sorted(candidate_codes_by_t[t])
                                         if candidate_code in auction["rows"]}}
                    for auction in auction_sources
                ]
            auction_sources = auction_cache[t]
            price = settlement._entry_price_v2(code, daily_path, prices["open"], root, auction_sources)
            row.update(entry_price=price["price"], entry_price_source=price["entry_price_source"],
                       entry_price_evidence=price, capacity_proxy_verified=price["amount"] is not None)
            status = None
            if volume == 0:
                status = "NO_FILL_SUSPENDED"
            elif not settlement._same_rounded_price(price["price"], prices["open"]):
                row["label_status"] = "PENDING_ENTRY_SOURCE_CONFLICT"
                continue
            elif row["shadow_max_price"] is not None and settlement._rounded_price_tick(price["price"]) > settlement._rounded_price_tick(row["shadow_max_price"]):
                status = "NO_FILL_ABOVE_FROZEN_CAP"
            elif settlement._same_rounded_price(price["price"], up):
                status = "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
            elif price["amount"] is not None and price["amount"] * settlement.MAX_AUCTION_PARTICIPATION + 1e-9 < settlement.SHADOW_NOTIONAL_CNY:
                status = "NO_FILL_CAPACITY"
            if status is not None:
                row.update(label_status=status, proxy_fill=0, slot_net_return=0.0,
                           label_available_date=t, label_available_at=_at(t, "15:00:00"), label_maturity_at=_at(t, "09:25:00"))
                continue
            row["proxy_fill"] = 1
            def minutes(day):
                if day > as_of:
                    raise ValueError("future minute truth forbidden")
                payload = load_exit_minutes(root, day, code)
                if payload is None:
                    missing(day, "exit_1000_1m", code)
                else:
                    for binding in payload["source_files"]:
                        bind(binding)
                return payload
            result, status = resolve_exit_1000(
                dates, t1, as_of, code, price["price"], prices["close"],
                lambda day: exact_row(day, "daily", code),
                lambda day: exact_row(day, "stk_limit", code), minutes,
            )
            row["label_status"] = status
            if result is not None:
                net = result["gross_return"] - COST_RATE
                row.update(label_status=SETTLED, net_return=net, conditional_net_return=net, slot_net_return=net,
                           actual_exit_date=result["actual_exit_date"], actual_exit_time=result["actual_exit_time"],
                           decision_time=result["decision_time"], held_limit_up_sessions=result["held_limit_up_sessions"],
                           label_maturity_at=result["actual_exit_time"], label_available_date=result["actual_exit_date"],
                           label_available_at=_at(result["actual_exit_date"], "15:00:00"), exit_evidence=result)
        except (ValueError, OSError, TypeError, settlement.ExecutableProfitSettlementError) as exc:
            row.update(label_status="PENDING_INVALID_SOURCE", error_type=type(exc).__name__,
                       net_return=None, conditional_net_return=None, slot_net_return=None)
    cohorts = {}
    for day in sorted(manifest["expected_candidate_codes"]):
        rows = [r for r in output if r["signal_date"] == day]
        complete = all(r["label_status"] == SETTLED or r["label_status"].startswith("NO_FILL_") for r in rows)
        for row in rows:
            row["cohort_complete"] = complete
        cohorts[day] = {"expected_rows": len(rows), "terminal_rows": sum(r["label_status"] == SETTLED or r["label_status"].startswith("NO_FILL_") for r in rows),
                        "complete": complete, "statuses": dict(Counter(r["label_status"] for r in rows)),
                        "label_available_date": max(r["label_available_date"] for r in rows) if complete else None}
    # Detect source changes even when another candidate did not revisit a file.
    for binding in sources.values():
        _binding(root, binding)
    return {"schema_version": LABEL_SCHEMA, "label_policy_id": EXIT_POLICY_ID,
            "candidate_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "as_of_date": as_of, "research_only": True, "historical_counterfactual": True,
            "natural_forward_ledger_rewritten": False, "old_open_exit_labels_consumed": False,
            "actual_execution_claimed": False, "round_trip_cost_rate": COST_RATE,
            "feature_evidence_kind": manifest["evidence_kind"], "feature_columns": manifest["feature_columns"],
            "rows": output, "cohorts_by_date": cohorts, "source_files": sorted(sources.values(), key=lambda b: b["path"])}
