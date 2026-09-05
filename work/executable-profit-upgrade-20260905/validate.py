#!/usr/bin/env python3
"""Independent temporal/provenance checks for offline return research."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as e


def run(repo, research):
    frame, columns, baseline, provenance = e.load(repo, research)
    manifest = json.loads((repo / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json").read_text())
    calpath = repo / manifest["inputs"]["strict_sse_calendar"]["path"]
    e.expect(e.sha(calpath) == manifest["inputs"]["strict_sse_calendar"]["sha256"], "calendar hash mismatch")
    cal = pd.read_csv(calpath, dtype=str)
    opened = sorted(cal.loc[cal["exchange"].eq("SSE") & cal["is_open"].eq("1"), "cal_date"].unique())
    positions = {date: i for i, date in enumerate(opened)}
    for row in frame[["signal_date", "exec_date", "scheduled_exit_date"]].drop_duplicates().itertuples(index=False):
        i = positions[row.signal_date]
        e.expect([row.exec_date, row.scheduled_exit_date] == opened[i + 1:i + 3], "D/T/T1 are not adjacent SSE dates")
    spec = importlib.util.spec_from_file_location("lagged_priors", research / "lagged_priors.py")
    lag = importlib.util.module_from_spec(spec)
    sys.modules["lagged_priors"] = lag
    spec.loader.exec_module(lag)
    source = e.read_csv(repo / manifest["inputs"]["five_year_source_ledger"]["path"])
    recreated = lag.build_lagged_features(history=source, targets=frame, open_dates=opened, source_kind="full", prefix="fullhist")
    pcols = lag.feature_columns("fullhist")
    joined = frame.merge(recreated[e.KEY + pcols], on=e.KEY, suffixes=("", "_recreated"), validate="one_to_one")
    actual = joined[pcols].to_numpy()
    recomputed = joined[[c + "_recreated" for c in pcols]].to_numpy()
    max_delta = float(np.max(np.abs(actual - recomputed)))
    e.expect(np.allclose(actual, recomputed, rtol=0, atol=1e-11), "stored lagged features cannot be reconstructed")
    future_feature_tests = []
    for date in ("20250225", "20251119", "20260814"):
        targets = frame.loc[frame["signal_date"].eq(date)].head(2)
        poison = source.copy()
        future = poison["target_exit_date"].ge(date)
        available = future & poison["market_fill"].eq(1) & poison["net_return"].notna()
        poison.loc[available, "net_return"] *= -1.
        poison.loc[available, "profit_hit"] = poison.loc[available, "net_return"].gt(0).astype(float)
        poison.loc[available, "big_loss_hit"] = poison.loc[available, "net_return"].le(-.03).astype(float)
        p = lag.build_lagged_features(history=poison, targets=targets, open_dates=opened, source_kind="full", prefix="fullhist")
        original = recreated.loc[recreated["signal_date"].eq(date) & recreated["ts_code"].isin(targets["ts_code"])].sort_values("ts_code")
        p = p.sort_values("ts_code")
        e.expect(np.array_equal(p[pcols].to_numpy(), original[pcols].to_numpy()), "future outcomes changed D-known lagged features")
        future_feature_tests.append({"D": date, "changed_future_rows": int(available.sum()), "identical_features": True})
    future_model_tests = []
    for cutoff in ("20250225", "20251119"):
        m, d, audit = e.fit_return_models(frame, columns, cutoff)
        poisoned = frame.copy()
        future = poisoned["scheduled_exit_date"].ge(cutoff)
        poisoned.loc[future, "strategy_slot_net_return"] = .99
        m2, d2, audit2 = e.fit_return_models(poisoned, columns, cutoff)
        query = frame[columns].iloc[-50:]
        e.expect(np.array_equal(m.predict(query), m2.predict(query)), "future labels changed mean model")
        e.expect(np.array_equal(d.predict(query), d2.predict(query)), "future labels changed downside model")
        e.expect(audit == audit2, "poisoned future changed training audit")
        future_model_tests.append({"cutoff": cutoff, "poisoned_future_rows": int(future.sum()), "mean_and_downside_predictions_identical": True})
    old_report = json.loads((research / "outputs/benchmark_report.json").read_text())
    new_report = json.loads((HERE / "outputs/comparison.json").read_text())
    reproduction = {}
    for split in ("development", "confirmation"):
        old = old_report["evaluations"][split]["hgb:full_priors"]["policy"]
        new = new_report["results"][split]["mixed_hgb_baseline"]["portfolio"]
        for oldkey, newkey in [("mean_daily_top2_net_return", "mean_daily_net"), ("maximum_drawdown", "maximum_drawdown"), ("compounded_top2_net_return", "cumulative_reinvested_return")]:
            e.expect(np.isclose(old[oldkey], new[newkey], rtol=0, atol=1e-14), "legacy baseline not exactly reproduced")
        reproduction[split] = True
    result = {"schema_version": "dc20_offline_return_downside_validation_v1", "passed": True, "calendar_sha256": e.sha(calpath), "all_D_T_T1_adjacent_SSE_dates": True, "reconstructed_lagged_feature_rows": len(recreated), "lagged_reconstruction_max_abs_delta": max_delta, "future_outcome_feature_invariance": future_feature_tests, "future_label_model_invariance": future_model_tests, "legacy_baseline_reproduced": reproduction, "independent_confirmation_claim": False, "release_allowed": False}
    e.write_json(HERE / "outputs/validation.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, default=HERE.parents[1])
    p.add_argument("--research", type=Path, default=None)
    a = p.parse_args()
    research_root = a.research or a.repo / "work/executable-profit-lagged-features-20260824"
    run(a.repo.resolve(), research_root.resolve())
