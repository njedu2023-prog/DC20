#!/usr/bin/env python3
"""One pre-fixed offline HGB experiment. No network, production writes or release."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

HERE = Path(__file__).resolve().parent
KEY = ["signal_date", "ts_code"]
SCORES = ["direct_slot_net", "conditional_net", "fill_conditional_net", "conditional_net_downside", "fill_conditional_net_downside"]
HEADS = ["direct_prediction", "conditional_prediction", "downside_prediction", "fill_prediction"]
STATUS = "RETROSPECTIVE_RESEARCH_ONLY_NO_RELEASE"


def expect(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(safe(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def read_csv(path):
    return pd.read_csv(path, low_memory=False, dtype={k: str for k in ["signal_date", "exec_date", "scheduled_exit_date", "target_exit_date", "ts_code", "lagged_prior_max_history_exit_date"]})


def csv_gz(path, frame):
    payload = frame.to_csv(index=False, lineterminator="\n", float_format="%.17g", na_rep="").encode()
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            handle.write(payload)


def find_repo(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    for candidate in HERE.parents:
        if (candidate / "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz").is_file():
            return candidate
    raise ValueError("Use --repo /path/to/DC20 until packaged beneath repo work/")


def load(repo, plan):
    for spec in plan["source_inputs"].values():
        expect(sha(repo / spec["path"]) == spec["sha256"], "immutable research input hash changed: " + spec["path"])
    lm_path = repo / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
    pm_path = repo / "work/executable-profit-lagged-features-20260824/outputs/lagged_priors_manifest.json"
    lm, pm = json.loads(lm_path.read_text()), json.loads(pm_path.read_text())
    frame = read_csv(repo / plan["source_inputs"]["ledger"]["path"])
    priors = read_csv(repo / plan["source_inputs"]["full_lagged_priors"]["path"])
    expect(len(frame) == 6753 and frame.signal_date.nunique() == 910, "fixed historical panel changed")
    expect(not frame.duplicated(KEY).any() and not priors.duplicated(KEY).any(), "duplicate candidate keys")
    pcols = pm["outputs"]["full"]["feature_columns"]
    columns = lm["feature_contract"]["columns"] + pcols
    expect(len(columns) == len(set(columns)) == 156 and len(pcols) == 108, "feature definition changed")
    forbidden = {"promotion_rank", "predicted_promotion_probability", "public_market_buyable_proxy", "conditional_net_return_after_cost", "strategy_slot_net_return"}
    expect(not forbidden.intersection(columns), "current outcome or other model leaked into feature set")
    last = priors.lagged_prior_max_history_exit_date.fillna("")
    expect((last.lt(priors.signal_date) | last.eq("")).all(), "non-lagged outcome used in feature")
    frame = frame.merge(priors[KEY + pcols], on=KEY, validate="one_to_one", how="left")
    expect(frame[pcols].notna().all().all(), "missing joined lagged priors")
    expect(not np.isinf(frame[columns].to_numpy(dtype=float)).any(), "infinite features")
    frame = frame.sort_values(KEY, kind="stable").reset_index(drop=True)
    calendar = pd.read_csv(repo / plan["source_inputs"]["calendar"]["path"], dtype=str)
    opened = sorted(calendar.loc[calendar.exchange.eq("SSE") & calendar.is_open.eq("1"), "cal_date"].tolist())
    position = {d: i for i, d in enumerate(opened)}
    for row in frame[["signal_date", "exec_date", "scheduled_exit_date"]].drop_duplicates().itertuples(index=False):
        i = position[row.signal_date]
        expect(row.exec_date == opened[i + 1] and row.scheduled_exit_date == opened[i + 2], "D/T/T+1 calendar mismatch")
    src = repo / lm["inputs"]["five_year_source_ledger"]["path"]
    expect(sha(src) == lm["inputs"]["five_year_source_ledger"]["sha256"], "source outcomes hash changed")
    provenance = {"source_inputs": plan["source_inputs"], "ledger_manifest_sha256": sha(lm_path), "prior_manifest_sha256": sha(pm_path), "historical_full_source": {"path": str(src.relative_to(repo)), "sha256": sha(src)}, "lagged_prior_code_sha256": sha(repo / "work/executable-profit-lagged-features-20260824/lagged_priors.py"), "feature_count": len(columns), "feature_columns_sha256": canonical_sha(columns)}
    return frame, columns, provenance


def audit_truth(frame):
    fill = frame.public_market_buyable_proxy
    slot = frame.strategy_slot_net_return
    mature = fill.eq(1) & frame.conditional_net_return_after_cost.notna()
    expect(frame.stage.isin([2, 3]).all(), "candidate scope drift")
    expect(frame.groupby("signal_date").size().le(10).all(), "candidate padding or count drift")
    expect(np.allclose(frame.loc[mature, "conditional_gross_return"], frame.loc[mature, "conditional_exit_price_proxy"] / frame.loc[mature, "conditional_entry_price_proxy"] - 1), "price return arithmetic")
    expect(np.allclose(frame.loc[mature, "conditional_net_return_after_cost"], frame.loc[mature, "conditional_gross_return"] - .0045), "base cost arithmetic")
    expect(np.allclose(slot[mature], frame.loc[mature, "conditional_net_return_after_cost"]), "slot/conditional target mismatch")
    expect(frame.loc[fill.eq(0), "strategy_slot_net_return"].eq(0).all(), "unbuyable must be cash zero")
    expect(frame.loc[fill.eq(0), "strategy_slot_net_return_2x_cost"].eq(0).all(), "unbuyable stress cost must be zero")
    expect(np.allclose(frame.loc[mature, "strategy_slot_net_return_2x_cost"], slot[mature] - .0045), "90bp stress arithmetic")
    expect(frame.actual_order_fill_observed.eq(0).all(), "unexpected actual fill claim")
    return {"rows": len(frame), "signal_dates": frame.signal_date.nunique(), "missing_net_rows": slot.isna().sum(), "missing_buyability_rows": fill.isna().sum(), "missing_as_zero_allowed": False, "known_unbuyable_as_cash_zero": True, "base_cost_bps": 45, "stress_cost_bps": 90, "gross_formula": "T1_open / T_open - 1", "blocked_limit_down_exit_resolved": False, "actual_execution_observed": False, "formula_checks_passed": True}


def training_sets(frame, cutoff):
    eligible = frame.scheduled_exit_date.lt(cutoff)
    direct = frame.loc[eligible & frame.strategy_slot_net_return.notna()].copy()
    conditional = frame.loc[eligible & frame.public_market_buyable_proxy.eq(1) & frame.conditional_net_return_after_cost.notna()].copy()
    fill = frame.loc[eligible & frame.public_market_buyable_proxy.notna()].copy()
    return {"direct": direct, "conditional": conditional, "fill": fill}


def fit_heads(frame, columns, cutoff, plan):
    train = training_sets(frame, cutoff)
    audits = {}
    for name, group in train.items():
        expect(len(group) >= 200, "insufficient fixed training cohort")
        expect(group.scheduled_exit_date.max() < cutoff, "training outcome crosses cutoff")
        audits[name] = {"rows": len(group), "signal_dates": group.signal_date.nunique(), "maximum_label_exit_date": group.scheduled_exit_date.max(), "keys_sha256": canonical_sha(group[KEY].values.tolist())}
    params = plan["single_fixed_HGB_parameter_set"]
    models = {"direct_prediction": HistGradientBoostingRegressor(loss="squared_error", **params), "conditional_prediction": HistGradientBoostingRegressor(loss="squared_error", **params), "downside_prediction": HistGradientBoostingRegressor(loss="squared_error", **params), "fill_prediction": HistGradientBoostingClassifier(loss="log_loss", **params)}
    models["direct_prediction"].fit(train["direct"][columns], train["direct"].strategy_slot_net_return)
    models["conditional_prediction"].fit(train["conditional"][columns], train["conditional"].conditional_net_return_after_cost)
    models["downside_prediction"].fit(train["conditional"][columns], np.maximum(-train["conditional"].conditional_net_return_after_cost, 0.))
    expect(train["fill"].public_market_buyable_proxy.nunique() == 2, "fill classes insufficient")
    models["fill_prediction"].fit(train["fill"][columns], train["fill"].public_market_buyable_proxy.astype(int))
    return models, {"cutoff_exclusive": cutoff, "heads": audits, "conditional_downside_uses_exact_conditional_training_set": True}


def predict_heads(models, frame, columns):
    result = frame.copy()
    for name in HEADS:
        result[name] = models[name].predict_proba(frame[columns])[:, 1] if name == "fill_prediction" else models[name].predict(frame[columns])
    result["downside_prediction"] = np.maximum(result["downside_prediction"], 0.)
    result["direct_slot_net"] = result["direct_prediction"]
    result["conditional_net"] = result["conditional_prediction"]
    result["fill_conditional_net"] = result["fill_prediction"] * result["conditional_prediction"]
    result["conditional_net_downside"] = result["conditional_prediction"] - .5 * result["downside_prediction"]
    result["fill_conditional_net_downside"] = result["fill_prediction"] * result["conditional_net_downside"]
    result["promotion_top2"] = -result["promotion_rank"]
    expect(np.isfinite(result[HEADS + SCORES].to_numpy()).all(), "nonfinite predictions")
    return result


def bootstrap_ci(values, plan):
    values = np.asarray(values, dtype=float)
    config = plan["evaluation"]["bootstrap"]
    rng = np.random.default_rng(config["seed"])
    block = min(config["block_dates"], len(values))
    starts = rng.integers(0, len(values) - block + 1, (config["samples"], math.ceil(len(values) / block)))
    index = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(config["samples"], -1)[:, :len(values)]
    estimates = values[index].mean(axis=1)
    return {"estimate": values.mean(), "ci95_low": np.quantile(estimates, .025), "ci95_high": np.quantile(estimates, .975), "block_retained_dates": block, "resamples": config["samples"]}


def metrics(daily, plan):
    output = {"dates": len(daily), "actual_candidate_slots": daily.selected.sum(), "absent_cash_slots": daily.cash.sum(), "buyability_proxy_coverage": daily.fill.mean(), "actual_execution_coverage": 0.0}
    for name, column in [("cost_45bp", "net"), ("cost_90bp", "stress")]:
        a = daily[column].to_numpy()
        wealth = np.r_[1., np.cumprod(1. + a)]
        gains, losses = a[a > 0], a[a < 0]
        output[name] = {"mean_net": a.mean(), "mean_net_ci95": bootstrap_ci(a, plan), "win_rate": (a > 0).mean(), "mean_win": gains.mean() if len(gains) else None, "mean_loss": losses.mean() if len(losses) else None, "payoff_ratio": gains.mean() / abs(losses.mean()) if len(gains) and len(losses) else None, "worst_day": a.min(), "worst_decile_mean": np.sort(a)[:max(1, math.ceil(len(a) * .1))].mean(), "synthetic_reinvested_cumulative": wealth[-1] - 1, "synthetic_max_drawdown": (wealth / np.maximum.accumulate(wealth) - 1).min()}
    output["mean_gross"] = daily.gross.mean()
    output["mean_cost_drag"] = (daily.gross - daily.net).mean()
    return output


def select_policy(frame, score):
    selected, daily = [], []
    for date, group in frame.groupby("signal_date", sort=True):
        chosen = group.sort_values([score, "ts_code"], ascending=[False, True], kind="stable").head(2).copy()
        chosen["slot"] = np.arange(1, len(chosen) + 1)
        chosen["policy"] = score
        selected.append(chosen)
        slots = []
        for position in range(2):
            if position < len(chosen):
                row = chosen.iloc[position]
                net, stress, fill = float(row.strategy_slot_net_return), float(row.strategy_slot_net_return_2x_cost), float(row.public_market_buyable_proxy)
                item = {"signal_date": date, "policy": score, "slot": f"Top{position + 1}", "net": net, "stress": stress, "gross": net + .0045 * fill, "fill": fill, "selected": 1, "cash": 0}
            else:
                item = {"signal_date": date, "policy": score, "slot": f"Top{position + 1}", "net": 0., "stress": 0., "gross": 0., "fill": 0., "selected": 0, "cash": 1}
            daily.append(item)
            slots.append(item)
        daily.append({"signal_date": date, "policy": score, "slot": "equal_Top2", **{k: sum(s[k] for s in slots) / (1 if k in ["selected", "cash"] else 2) for k in ["net", "stress", "gross", "fill", "selected", "cash"]}})
    return pd.concat(selected, ignore_index=True), pd.DataFrame(daily)


def summarize(predictions, dates, plan):
    complete = predictions.groupby("signal_date").apply(lambda g: g[["strategy_slot_net_return", "strategy_slot_net_return_2x_cost", "public_market_buyable_proxy"]].notna().all().all(), include_groups=False)
    good = complete.index[complete].tolist()
    missing = complete.index[~complete].tolist()
    panel = predictions.loc[predictions.signal_date.isin(good)]
    selected, daily = [], []
    for score in SCORES + ["promotion_top2"]:
        chosen, series = select_policy(panel, score)
        selected.append(chosen)
        daily.append(series)
    selected, daily = pd.concat(selected, ignore_index=True), pd.concat(daily, ignore_index=True)
    periods = {"all_360_signal_dates": dates, "earlier_180_signal_dates": dates[:180], "recent_180_signal_dates": dates[-180:]}
    result, comparisons, audits = {}, {}, {}
    for period, cohort in periods.items():
        series = daily.loc[daily.signal_date.isin(cohort)]
        audits[period] = {"scheduled_signal_dates": len(cohort), "complete_signal_dates": len(set(cohort).intersection(good)), "start": cohort[0], "end": cohort[-1], "common_excluded_dates": sorted(set(cohort).intersection(missing)), "all_history_previously_viewed": True, "independent_holdout": False}
        result[period] = {policy: {slot: metrics(group.sort_values("signal_date"), plan) for slot, group in group_policy.groupby("slot")} for policy, group_policy in series.groupby("policy")}
        comparisons[period] = {}
        for candidate, baseline in plan["evaluation"]["primary_paired_tests"]:
            label = candidate + "__minus__" + baseline
            comparisons[period][label] = {}
            for slot in ["Top1", "Top2", "equal_Top2"]:
                one = series.loc[series.policy.eq(candidate) & series.slot.eq(slot)].set_index("signal_date").sort_index()
                two = series.loc[series.policy.eq(baseline) & series.slot.eq(slot)].set_index("signal_date").sort_index()
                expect(one.index.equals(two.index), "paired dates differ")
                comparisons[period][label][slot] = {"cost_45bp": bootstrap_ci(one.net.to_numpy() - two.net.to_numpy(), plan), "cost_90bp": bootstrap_ci(one.stress.to_numpy() - two.stress.to_numpy(), plan)}
    return result, comparisons, audits, selected, daily


def run(repo, output):
    plan_path = HERE / "PLAN.json"
    plan_hash = sha(plan_path)
    plan = json.loads(plan_path.read_text())
    expect(plan["status"] == STATUS and not plan["boundaries"]["release_allowed"], "research boundary missing")
    frame, columns, provenance = load(repo, plan)
    truth_audit = audit_truth(frame)
    dates = sorted(frame.signal_date.unique())[-360:]
    all_predictions, folds = [], []
    started = time.monotonic()
    for offset in range(0, 360, 40):
        cohort = dates[offset:offset + 40]
        models, audit = fit_heads(frame, columns, cohort[0], plan)
        target = frame.loc[frame.signal_date.isin(cohort)].copy()
        predicted = predict_heads(models, target, columns)
        predicted["fold"] = offset // 40 + 1
        audit.update({"fold": offset // 40 + 1, "target_first_D": cohort[0], "target_last_D": cohort[-1], "target_signal_dates": len(cohort), "target_candidates": len(target), "target_candidate_keys_sha256": canonical_sha(target[KEY].values.tolist())})
        folds.append(audit)
        all_predictions.append(predicted)
        print(json.dumps({"fold_complete": audit["fold"], "first_D": cohort[0], "last_D": cohort[-1], "trained_direct_rows": audit["heads"]["direct"]["rows"], "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
    predictions = pd.concat(all_predictions, ignore_index=True)
    result, paired, cohorts, selected, daily = summarize(predictions, dates, plan)
    keep = KEY + ["exec_date", "scheduled_exit_date", "promotion_rank", "stage", "fold", "public_market_buyable_proxy", "strategy_slot_net_return", "strategy_slot_net_return_2x_cost"] + HEADS + SCORES + ["promotion_top2"]
    output.mkdir(parents=True, exist_ok=True)
    csv_gz(output / "candidate_predictions.csv.gz", predictions[keep])
    csv_gz(output / "selected_records.csv.gz", selected[keep + ["policy", "slot"]])
    csv_gz(output / "daily_returns.csv.gz", daily)
    expect(sha(plan_path) == plan_hash, "fixed plan changed during experiment")
    report = {"schema_version": "dc20_profit_core_walk_forward_evaluation_v1", "status": STATUS, "plan_sha256": plan_hash, "evaluate_script_sha256": sha(__file__), "provenance": provenance, "environment": {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(), "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "sklearn": sklearn.__version__, "threadpool_limit": 2, "linux_requirements_lock_hashes_applied": False, "note": "macOS ARM wheels with pinned model-library versions; Linux wheel hashes are not portable"}, "truth_audit": truth_audit, "cohorts": cohorts, "training_audits": folds, "results": result, "paired_comparisons": paired, "outputs": {p.name: sha(p) for p in sorted(output.glob("*.csv.gz"))}, "production_replacement_supported": False, "release_allowed": False, "independent_unviewed_confirmation": False, "actual_executable_return_claim": False, "production_changed": False, "new_forward_evidence": False, "experiment_count": {"parameter_sets": 1, "train_blocks": 9, "heads_per_block": 4, "fixed_scoring_policies": 5, "same_candidate_promotion_comparator": 1, "post_result_adjustments": 0}, "metric_limitations": ["All 360 evaluation dates were previously viewed; no new independent confirmation.", "45bp/90bp transaction cost is charged only on known proxy fills; missing truth is not cash zero.", "Old daily-open exit assumes an opening price is attainable, and does not handle limit-down blocks or delayed exits.", "Synthetic compounded daily slot indexes assume reusable capital and do not model overlapping T/T+1 funds, so are risk diagnostics rather than executable portfolio backtests.", "Bootstrap intervals are per-comparison diagnostics, not multiplicity-corrected release tests; they omit model and execution-proxy uncertainty.", "The original historical frozen promotion candidate list is reused; today's production mixed weights are not claimed replayed by this walk-forward research."]}
    write_json(output / "comparison.json", report)
    print(json.dumps({"evaluation_complete": True, "complete_dates": cohorts["all_360_signal_dates"]["complete_signal_dates"], "recent180_combo_mean_net": {p: result["recent_180_signal_dates"][p]["equal_Top2"]["cost_45bp"]["mean_net"] for p in SCORES}}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--output", type=Path, default=HERE / "outputs")
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        run(find_repo(args.repo), args.output)


if __name__ == "__main__":
    main()
