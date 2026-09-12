"""Read-only, source-bound execution-latency sensitivity; no fitting or trading.

The original 10:00/break decision is unchanged. The extra 60 seconds concerns
order execution only, under the existing conditional BAR_END interpretation.
It neither confirms provider timestamp semantics nor supplies BAR_START data.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]

from work.profit_1000_upgrade import acceptance, labels, minute_truth, policy_v2, run
from work.profit_1000_upgrade.exit_latency import compare_exit_latency
from top10decision.decision import executable_profit_shadow_settlement as settlement

SCHEMA = "dc20_exit_execution_latency_report_20260912_v1"
_IMPLEMENTATIONS = (Path(__file__), HERE / "exit_latency.py")
_IMPORTED_SOURCE_HASHES = {path: run.sha(path) for path in _IMPLEMENTATIONS}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _verified_mirror(root, archive):
    """Match every original archive member, including receipts, to a safe mirror.

    Called only after the ZIP's complete integrity audit. Additional files do
    not become price evidence: all consumed prices must also be receipt-bound.
    """
    root = run.require_research_mirror(Path(root), plan_version="v2")
    verified = {}
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            if member.is_dir():
                continue
            relative = acceptance._path(member.filename)
            digest = hashlib.sha256()
            with handle.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            binding = {"path": relative, "sha256": digest.hexdigest()}
            labels._binding(root, binding)
            verified[relative] = binding
    return root, verified


def _read_json(root, bindings, path):
    _require(path in bindings, "JSON_NOT_IN_AUDITED_ARCHIVE:" + path)
    _, raw = labels._binding(root, bindings[path])
    value = acceptance._json(raw, path)
    _require(isinstance(value, dict), "JSON_OBJECT_REQUIRED:" + path)
    return value


def _summary(rows):
    paired = [r for r in rows if r.get("pair_status") == "BOTH_SETTLED"]
    no_fill = sum(r.get("pair_status") == "KNOWN_NO_FILL" for r in rows)
    unresolved = len(rows) - len(paired) - no_fill
    # These means explicitly describe the paired settled subset, never the
    # whole cohort, a capital NAV, or a successful complete validation sample.
    statistics = {}
    for cost in ("45bp", "90bp"):
        b = [r["baseline_net_" + cost] for r in paired]
        d = [r["delayed_net_" + cost] for r in paired]
        statistics[cost] = {
            "paired_settled_rows": len(paired),
            "baseline_mean_net": sum(b) / len(b) if b else None,
            "delayed_mean_net": sum(d) / len(d) if d else None,
            "mean_change": sum(y-x for x, y in zip(b, d)) / len(b) if b else None,
            "positive_to_nonpositive": sum(x > 0 and y <= 0 for x, y in zip(b, d)),
            "nonpositive_to_positive": sum(x <= 0 and y > 0 for x, y in zip(b, d)),
        }
    dates = defaultdict(list)
    for row in rows:
        dates[row["signal_date"]].append(row)
    return {"rows": len(rows), "known_no_fill_rows": no_fill,
            "unresolved_rows": unresolved, "paired_settled_rows": len(paired),
            "sample_complete": unresolved == 0 and bool(rows),
            "complete_D_dates": sum(all(r.get("pair_status") in {"BOTH_SETTLED", "KNOWN_NO_FILL"}
                                        for r in group) for group in dates.values()),
            "total_D_dates": len(dates), "cost_sensitivity": statistics,
            "mean_denominator": "PAIRED_SETTLED_SUBSET_ONLY_EXCLUDES_NO_FILL_AND_UNRESOLVED",
            "account_NAV_computed": False}


def _source_callbacks(root, allowed, used, dates, asof, code, market_cache=None):
    cache = OrderedDict() if market_cache is None else market_cache

    def bind(binding):
        _require(allowed.get(binding["path"]) == binding,
                 "LATENCY_SOURCE_NOT_IN_VERIFIED_COLLECTION:" + binding["path"])
        labels._binding(root, binding)
        _require(binding["path"] not in used or used[binding["path"]] == binding,
                 "LATENCY_SOURCE_CHANGED")
        used[binding["path"]] = dict(binding)

    def date(day):
        _require(day in dates and day <= asof, "OUT_OF_SCOPE_LATENCY_DATE")

    def market(day, kind):
        date(day)
        path = settlement._find_market_file(root, day, kind)
        if path is None:
            return None
        binding = settlement._source_binding(root, path)
        bind(binding)
        key = (day, kind, binding["sha256"])
        if key not in cache:
            cache[key] = settlement._market_rows(path, day)
            while len(cache) > 8:
                cache.popitem(last=False)
        cache.move_to_end(key)
        return cache[key].get(code)

    def minutes(day):
        date(day)
        payload = minute_truth.load(root, day, code)
        if payload is not None:
            _require(payload.get("provider_timestamp_semantics_confirmed") is False
                     and payload.get("time_semantics") == minute_truth.TIME_SEMANTICS,
                     "LATENCY_MINUTE_SEMANTICS_CHANGED")
            for binding in payload["source_files"]:
                bind(binding)
        return payload

    return lambda day: market(day, "daily"), lambda day: market(day, "stk_limit"), minutes


def _pair_rows(root, rebuilt, allowed, dates, asof):
    output, used = [], {}
    market_cache = OrderedDict()
    for row in rebuilt["rows"]:
        item = {key: row[key] for key in ("signal_date", "ts_code", "promotion_rank", "exec_date", "scheduled_exit_date")}
        item.update(baseline_label_status=row["label_status"],
                    baseline_net_45bp=None, delayed_net_45bp=None,
                    baseline_net_90bp=None, delayed_net_90bp=None,
                    pair_status="BASELINE_UNRESOLVED", comparison=None)
        output.append(item)
        status = row["label_status"]
        if status in policy_v2.NO_FILL_STATUSES:
            _require(row["slot_net_return"] == 0 and row["proxy_fill"] == 0, "INVALID_NO_FILL_LABEL")
            item.update(pair_status="KNOWN_NO_FILL", baseline_net_45bp=0.0, delayed_net_45bp=0.0,
                        baseline_net_90bp=0.0, delayed_net_90bp=0.0)
            continue
        if status != labels.SETTLED:
            _require(status.startswith("PENDING_") and row["slot_net_return"] is None,
                     "UNKNOWN_OR_FALSE_ZERO_BASELINE_LABEL")
            continue
        callbacks = _source_callbacks(root, allowed, used, dates, asof, row["ts_code"], market_cache)
        t_daily = callbacks[0](row["exec_date"])
        _require(t_daily is not None, "REBUILT_ENTRY_DAILY_DISAPPEARED")
        pair = compare_exit_latency(dates, row["scheduled_exit_date"], asof, row["ts_code"],
                                    row["entry_price"], t_daily["close"], *callbacks)
        base, delayed = pair["baseline"], pair["delayed"]
        _require(base["result"] == row["exit_evidence"], "BASELINE_EXIT_REPLAY_CHANGED")
        _require(abs(base["net_return_45bp"] - row["slot_net_return"]) <= 1e-12,
                 "BASELINE_NET_REPLAY_CHANGED")
        item.update(comparison=pair, baseline_net_45bp=base["net_return_45bp"],
                    baseline_net_90bp=base["net_return_90bp"],
                    delayed_net_45bp=delayed["net_return_45bp"],
                    delayed_net_90bp=delayed["net_return_90bp"],
                    pair_status="BOTH_SETTLED" if delayed["result"] is not None else "DELAYED_UNRESOLVED")
    return output, used


def generate_report(root, archive, *, expected_zip_sha256, expected_run_id, expected_commit):
    """Return JSON-serializable evidence, never write to the mirror or repository."""
    for path, expected in _IMPORTED_SOURCE_HASHES.items():
        _require(run.sha(path) == expected, "LATENCY_IMPLEMENTATION_CHANGED_SINCE_IMPORT")
    audit = acceptance.audit_archive(archive, expected_zip_sha256=expected_zip_sha256,
                                     expected_run_id=expected_run_id, expected_commit=expected_commit)
    root, archived = _verified_mirror(root, archive)
    plan = run.load_plan("v2")
    manifest = _read_json(root, archived, "research_inputs/manifest.json")
    fresh_manifest, _, _ = run.prepare_history(root, plan_version="v2")
    _require(manifest == fresh_manifest, "FROZEN_D_FEATURE_RECONSTRUCTION_CHANGED")
    allowed, collection = run.validate_collection_evidence(root, manifest, plan, plan_version="v2")
    for path, binding in allowed.items():
        _require(archived.get(path) == binding, "COLLECTION_BINDING_NOT_IN_AUDITED_ARCHIVE:" + path)
    rebuilt = labels.build_labels(root, manifest, as_of_date=plan["as_of_date"])
    saved = _read_json(root, archived, "research_results/labels.json")
    _require(rebuilt == saved, "SOURCE_REBUILT_LABELS_DIFFER_FROM_ARCHIVE")
    for binding in rebuilt["source_files"]:
        _require(archived.get(binding["path"]) == binding, "BASELINE_PRICE_OUTSIDE_AUDITED_ARCHIVE")
    dates = settlement._strict_open_dates(root)
    rows, used = _pair_rows(root, rebuilt, allowed, dates, plan["as_of_date"])
    # Detect mutated source bytes even if they were not revisited by an arm.
    for binding in archived.values():
        labels._binding(root, binding)
    _require(run.sha(Path(archive)) == expected_zip_sha256, "ARCHIVE_CHANGED_DURING_REPLAY")
    for path, expected in _IMPORTED_SOURCE_HASHES.items():
        _require(run.sha(path) == expected, "LATENCY_IMPLEMENTATION_CHANGED_DURING_REPLAY")
    all_summary = _summary(rows)
    return {"schema_version": SCHEMA,
            "status": "LATENCY_REPLAY_COMPLETE" if all_summary["sample_complete"] else "LATENCY_REPLAY_WITH_UNRESOLVED_ROWS",
            "source_run_id": expected_run_id, "source_commit": expected_commit,
            "source_archive_sha256": expected_zip_sha256, "source_archive_acceptance": audit,
            "plan_sha256": run.sha(run.plan_path("v2")), "baseline_label_sha256": _sha(rebuilt),
            "time_semantics": minute_truth.TIME_SEMANTICS, "execution_delay_seconds": 60,
            "decision_rule_changed": False, "provider_timestamp_semantics_confirmed": False,
            "not_bar_start_validation": True, "exit_capacity_verified": False,
            "BAR_START_interpretation_tested": False, "candidate_fitted_or_reranked": False,
            "production_activation_allowed": False, "actual_execution_claimed": False,
            "profitability_improvement_proven": False, "forward_holdout_evaluated": False,
            "historical_role": plan["historical_evidence_role"],
            "rows": rows, "summary": all_summary,
            "promotion_rank_summaries": {str(rank): _summary([r for r in rows if r["promotion_rank"] == rank])
                                         for rank in (1, 2, 3)},
            "source_files_used_by_exit_pairs": sorted(used.values(), key=lambda x: x["path"]),
            "collection_evidence": collection,
            "code_sources": [{"path": path.relative_to(CHECKOUT).as_posix(), "sha256": expected}
                             for path, expected in _IMPORTED_SOURCE_HASHES.items()],
            "limitations": ["Fixed execution-latency sensitivity only; source timestamp interpretation remains unconfirmed.",
                            "Subset averages do not establish whole-cohort performance or account NAV.",
                            "No model choice, production activation or prospective validation is performed."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    result = generate_report(args.root, args.archive, expected_zip_sha256=args.expected_zip_sha256,
                             expected_run_id=args.expected_run_id, expected_commit=args.expected_commit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
