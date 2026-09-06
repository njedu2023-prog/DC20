#!/usr/bin/env python3
"""Read-only, reproducible audit of frozen promotion-history input features.

No network, fitting, label rebuilding, or source edits. main creates one new
fixed output exclusively; build_feature_audit() returns the same report without
writing, so a reviewer can reproduce it without overwriting the saved evidence.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
from typing import Sequence

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = {
    "promotion_prior_builder": "src/top10decision/auction_v3/promotion_model.py",
    "five_year_builder": "scripts/build_three_engine_five_year_ledger.py",
    "frozen_training_allowlist": "work/executable-profit-execution-v2-20260906/train_candidate.py",
    "five_year_source": "data/decision_three_engines/five_year_supervised_ledger.csv.gz",
    "frozen_feature_ledger": "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz",
    "frozen_feature_manifest": "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
    "calendar": "data/market/trade_cal_sse.csv",
}
PINS = {
    "five_year_source": "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46",
    "frozen_feature_ledger": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd",
    "calendar": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748",
}
CUTOFF = "20240816"
PROFIT_COLUMNS = ("net_return", "profit_hit", "big_loss_hit", "market_fill", "tplus1_open")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checksum(payload):
    return hashlib.sha256(payload).hexdigest()


def no_symlinks(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink path forbidden")


def literal_assignment(tree, name):
    matches = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(matches) == 1, "expected one literal assignment: " + name)
    return ast.literal_eval(matches[0].value)


def mismatch_count(left, right):
    return int((~np.isclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float),
                           rtol=0, atol=1e-12, equal_nan=True)).sum())


def build_feature_audit():
    payloads, inventory = {}, {}
    for key, relative in SOURCES.items():
        path = ROOT / relative
        no_symlinks(path)
        payload = path.read_bytes()
        digest = checksum(payload)
        require(key not in PINS or digest == PINS[key], "source pin changed: " + key)
        payloads[key] = payload
        inventory[key] = {"path": relative, "sha256": digest, "bytes": len(payload)}
    promotion_tree = ast.parse(payloads["promotion_prior_builder"])
    five_year_tree = ast.parse(payloads["five_year_builder"])
    train_tree = ast.parse(payloads["frozen_training_allowlist"])
    prior_features = literal_assignment(promotion_tree, "PROMOTION_PRIOR_FEATURES")
    features = literal_assignment(train_tree, "FEATURES")
    require(len(prior_features) == 8 and len(features) == len(set(features)) == 48, "feature allowlist changed")
    manifest = json.loads(payloads["frozen_feature_manifest"])
    require(manifest["feature_contract"]["columns"] == features, "feature manifest/allowlist mismatch")
    require(manifest["inputs"]["five_year_source_ledger"]["sha256"] == PINS["five_year_source"], "manifest source pin mismatch")
    require(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "to_csv"
                and any(k.arg == "float_format" and isinstance(k.value, ast.Constant) and k.value.value == "%.10g" for k in n.keywords)
                for n in ast.walk(five_year_tree)), "five-year writer precision changed")

    # Load precisely the two existing pure dataframe functions. Removing their
    # one package import avoids unrelated sklearn package initialization; its
    # symbols are bound to the exact extracted function and literal list below.
    grid = next(n for n in promotion_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_prior_grid")
    recompute = next(n for n in five_year_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_recompute_point_in_time_promotion_priors")
    imports = [n for n in recompute.body if isinstance(n, ast.ImportFrom)]
    require(len(imports) == 1 and imports[0].module == "top10decision.auction_v3.promotion_model"
            and {n.name for n in imports[0].names} == {"PROMOTION_PRIOR_FEATURES", "_prior_grid"}, "pure-function import changed")
    recompute.body = [n for n in recompute.body if not isinstance(n, ast.ImportFrom)]
    context = {"pd": pd, "np": np, "math": math, "Sequence": Sequence, "PROMOTION_PRIOR_FEATURES": prior_features}
    program = ast.fix_missing_locations(ast.Module(body=[grid, recompute], type_ignores=[]))
    exec(compile(program, "pinned_existing_pure_dataframe_functions", "exec"), context)
    rebuild = context["_recompute_point_in_time_promotion_priors"]
    source = pd.read_csv(io.BytesIO(payloads["five_year_source"]), compression="gzip",
                         dtype={k: str for k in ("signal_date", "ts_code", "buy_date", "target_exit_date")})
    frozen = pd.read_csv(io.BytesIO(payloads["frozen_feature_ledger"]), compression="gzip", dtype={"signal_date": str, "ts_code": str})
    calendar = pd.read_csv(io.BytesIO(payloads["calendar"]), dtype=str)
    dates = calendar.loc[(calendar.exchange == "SSE") & (calendar.is_open == "1"), "cal_date"].tolist()
    require(len(source) == 12322 and len(frozen) == 6753, "frozen row counts changed")
    require(dates == sorted(set(dates)), "SSE calendar not sorted unique")
    require(CUTOFF in dates and min(source.signal_date) < CUTOFF < max(source.signal_date), "mutation cutoff out of range")
    keys = ["signal_date", "ts_code"]
    priors = prior_features + ["five_year_stock_prior_rate", "five_year_stock_prior_samples_log"]
    baseline = rebuild(source)
    rounded = baseline[keys + priors].copy()
    for field in priors:
        rounded[field] = rounded[field].map(lambda v: float(format(float(v), ".10g")) if pd.notna(v) else v)
    joined = frozen[keys + priors].merge(rounded, on=keys, suffixes=("_frozen", "_replayed"), validate="one_to_one")
    require(len(joined) == len(frozen), "replayed source candidate join incomplete")
    prior_results = {field: mismatch_count(joined[field + "_frozen"], joined[field + "_replayed"]) for field in priors}

    source_features = source.copy()
    source_features["stage_2"] = (pd.to_numeric(source.stage) == 2).astype(float)
    source_features["stage_3"] = (pd.to_numeric(source.stage) == 3).astype(float)
    source_features["board_sh_main"] = (source.board == "SH_MAIN").astype(float)
    source_features["board_sz_main"] = (source.board == "SZ_MAIN").astype(float)
    all_joined = frozen[keys + features].merge(source_features[keys + features], on=keys, suffixes=("_frozen", "_source"), validate="one_to_one")
    require(len(all_joined) == len(frozen), "all-feature source join incomplete")
    all_results = {field: mismatch_count(all_joined[field + "_frozen"], all_joined[field + "_source"]) for field in features}

    poisoned = source.copy()
    future = poisoned.signal_date.ge(CUTOFF)
    poisoned.loc[future, "promotion_hit"] = 1 - pd.to_numeric(poisoned.loc[future, "promotion_hit"], errors="coerce")
    future_rebuilt = rebuild(poisoned)
    before = baseline.loc[baseline.signal_date.le(CUTOFF)].sort_values(keys)
    after = future_rebuilt.loc[future_rebuilt.signal_date.le(CUTOFF)].sort_values(keys)
    future_changes = mismatch_count(before[priors], after[priors])
    poisoned = source.copy()
    require(set(PROFIT_COLUMNS).issubset(poisoned.columns), "profit mutation columns missing")
    poisoned[list(PROFIT_COLUMNS)] = float("nan")
    profit_rebuilt = rebuild(poisoned)
    profit_changes = mismatch_count(baseline.sort_values(keys)[priors], profit_rebuilt.sort_values(keys)[priors])
    successor = dict(zip(dates, dates[1:]))
    date_violations = int(source.buy_date.ne(source.signal_date.map(successor)).sum())
    event_dates = set(source.signal_date)
    calendar_window = {d for d in dates if min(event_dates) <= d <= max(event_dates)}
    all_passed = not (sum(prior_results.values()) or sum(all_results.values()) or future_changes or profit_changes or date_violations)
    return {
        "schema_version": "dc20_frozen_promotion_feature_audit_v1",
        "status": "CHECKED_PRIOR_TEMPORAL_DIRECTION_AND_FROZEN_VALUE_IDENTITY" if all_passed else "BLOCKED_FEATURE_AUDIT",
        "all_scoped_checks_passed": all_passed,
        "audit_script_sha256": checksum(Path(__file__).read_bytes()), "source_files": inventory,
        "runtime": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__},
        "method": "Two existing pure dataframe functions extracted from AST; only their import replaced with exact local definitions; no model package initialization or fitting",
        "source_rows": len(source), "frozen_rows": len(frozen), "source_first_D": min(event_dates), "source_last_D": max(event_dates),
        "prior_replay": {"feature_count": len(priors), "value_checks": len(joined) * len(priors), "source_writer_float_format": "%.10g",
                         "comparison_absolute_tolerance": 1e-12, "mismatches_by_feature": prior_results},
        "frozen_feature_copy_check": {"feature_count": len(features), "value_checks": len(all_joined) * len(features), "mismatches_by_feature": all_results},
        "current_and_future_promotion_truth_poisoning": {"cutoff_D": CUTOFF, "mutated_signal_dates": "D >= cutoff; flip observed promotion_hit",
                                                        "checked_signal_dates": "D <= cutoff", "checked_rows": len(before), "changed_prior_values": future_changes},
        "profit_target_independence": {"columns_set_to_missing": list(PROFIT_COLUMNS), "checked_rows": len(baseline), "changed_prior_values": profit_changes},
        "source_next_SSE_session_violations": date_violations,
        "recent_window_semantics": {"event_D_grid_count": len(event_dates), "SSE_session_count_same_range": len(calendar_window),
                                    "SSE_sessions_absent_from_event_grid": sorted(calendar_window - event_dates),
                                    "meaning": "20/60 rolling rows of event-D grid, not explicitly calendar-completed 20/60 SSE sessions"},
        "limitations": [
            "Causality result is scoped to the ten promotion-history prior features; the 48-feature copy equality is not a full causal audit of every other feature.",
            "Prior outcome is earlier-D promotion_hit at its next SSE T close; usable for current D after-close generation, not automatically current-D premarket.",
            "Old promotion_hit was constructed from Tencent daily bars and rounded pre_close*1.10, not newly audited official dated stk_limit truth.",
            "This does not verify original historical retrieval timestamps, market-data revisions, historical label accuracy, or untouched forward performance.",
            "Only in-memory mutation witnesses are used; no current execution labels, new raw collection, frozen features, production rankings, or Shadow records are changed.",
        ],
        "network_accessed": False, "models_trained": 0, "source_files_changed": False,
        "training_authorized": False, "production_release_authorized": False,
    }


def main():
    report = build_feature_audit()
    directory = HERE / "outputs"
    no_symlinks(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / "feature_audit.json"
    no_symlinks(target)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        require(os.fstat(handle.fileno()).st_nlink == 1, "output hardlink forbidden")
        handle.write((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
    print(json.dumps({"status": report["status"], "output": str(target), "output_sha256": checksum(target.read_bytes())}))
    return 0 if report["all_scoped_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
