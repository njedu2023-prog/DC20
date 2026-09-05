#!/usr/bin/env python3
"""Offline, pre-fixed return/downside research. No production writes or network."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
KEY = ["signal_date", "ts_code"]
SEED = 20260905


def expect(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    path.write_text(json.dumps(safe(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def read_csv(path):
    return pd.read_csv(path, low_memory=False, dtype={"signal_date": str, "exec_date": str, "scheduled_exit_date": str, "target_exit_date": str, "ts_code": str, "lagged_prior_max_history_exit_date": str})


def csv_gz(path, frame):
    text = frame.to_csv(index=False, lineterminator="\n", float_format="%.17g", na_rep="")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            handle.write(text.encode())


def ci(values):
    a = np.asarray(values, dtype=float)
    rng = np.random.default_rng(SEED)
    block, samples = 5, 2000
    estimates = []
    for _ in range(samples):
        starts = rng.integers(0, len(a) - block + 1, size=math.ceil(len(a) / block))
        sample = np.concatenate([a[s:s + block] for s in starts])[:len(a)]
        estimates.append(sample.mean())
    return {"estimate": a.mean(), "ci95_low": np.quantile(estimates, .025), "ci95_high": np.quantile(estimates, .975), "samples": samples, "block_dates": block}


def portfolio_metrics(frame):
    a = frame["net"].to_numpy()
    wealth = np.r_[1., np.cumprod(1. + a)]
    return {"dates": len(a), "mean_daily_net": a.mean(), "mean_daily_gross": frame["gross"].mean(), "mean_daily_2x_cost_net": frame["stress"].mean(), "mean_daily_cost_drag": (frame["gross"] - frame["net"]).mean(), "cumulative_reinvested_return": wealth[-1] - 1., "maximum_drawdown": (wealth / np.maximum.accumulate(wealth) - 1.).min(), "mean_net_95ci": ci(a), "positive_day_rate": (a > 0).mean(), "selected_slots": frame["selected"].sum(), "cash_slots": frame["cash"].sum()}


def selected_metrics(selected, total_dates):
    if not len(selected):
        return {"rows": 0, "fill_rate": None, "mean_net_on_fill": None, "note": "all cash"}
    net = selected["strategy_slot_net_return"]
    fill = selected["public_market_buyable_proxy"].eq(1)
    gains, losses = net[net > 0], net[net < 0]
    t_intraday = (selected["audit_t_close"] / selected["conditional_entry_price_proxy"] - 1.).where(fill)
    overnight_contribution = ((selected["conditional_exit_price_proxy"] - selected["audit_t_close"]) / selected["conditional_entry_price_proxy"]).where(fill)
    return {"rows": len(selected), "fixed_slots": total_dates * 2, "fill_rate": fill.mean(), "slot_win_rate": (net > 0).sum() / (total_dates * 2), "mean_net_on_fill": net[fill].mean(), "conditional_win_rate": (net[fill] > 0).mean(), "mean_win": gains.mean(), "mean_loss": losses.mean(), "mean_win_to_mean_loss_ratio": gains.mean() / abs(losses.mean()) if len(losses) else None, "loss_below_minus_3pct_rate": (net <= -.03).mean(), "worst_decile_mean": net.nsmallest(max(1, math.ceil(len(net) * .1))).mean(), "median_entry_premium_to_D_close": (selected.loc[fill, "conditional_entry_price_proxy"] / selected.loc[fill, "d_close"] - 1.).median(), "held_T_intraday_contribution_per_slot": t_intraday.fillna(0.).sum() / (total_dates * 2), "Tclose_to_T1open_contribution_per_slot": overnight_contribution.fillna(0.).sum() / (total_dates * 2), "t_close_coverage_on_fill": selected.loc[fill, "audit_t_close"].notna().mean(), "stage_breakdown": {str(stage): {"rows": len(g), "mean_net": g["strategy_slot_net_return"].mean(), "win_rate": (g["strategy_slot_net_return"] > 0).mean()} for stage, g in selected.groupby("stage")}}


def policy(frame, score, positive_only=False):
    pieces, daily = [], []
    for date, group in frame.groupby("signal_date", sort=True):
        ordered = group.sort_values([score, "ts_code"], ascending=[False, True], kind="stable")
        chosen = ordered.loc[ordered[score] > 0].head(2) if positive_only else ordered.head(2)
        pieces.append(chosen)
        fill = chosen["public_market_buyable_proxy"].to_numpy()
        net = chosen["strategy_slot_net_return"].to_numpy()
        daily.append({"signal_date": date, "net": net.sum() / 2., "gross": (net + .0045 * fill).sum() / 2., "stress": chosen["strategy_slot_net_return_2x_cost"].sum() / 2., "selected": len(chosen), "cash": 2 - len(chosen)})
    selected = pd.concat(pieces, ignore_index=True)
    daily = pd.DataFrame(daily)
    return daily, {"portfolio": portfolio_metrics(daily), "selection": selected_metrics(selected, len(daily))}, selected


def ridge():
    return Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)), ("scale", StandardScaler()), ("regressor", Ridge(alpha=1000., solver="svd"))])


def fit_return_models(frame, columns, cutoff):
    train = frame.loc[frame["scheduled_exit_date"].lt(cutoff) & frame["strategy_slot_net_return"].notna()].copy()
    expect(len(train) >= 3000, "training rows unexpectedly sparse")
    expect(train["scheduled_exit_date"].max() < cutoff, "training outcomes cross cutoff")
    y = train["strategy_slot_net_return"].to_numpy()
    mean_model, downside_model = ridge(), ridge()
    mean_model.fit(train[columns], y)
    downside_model.fit(train[columns], np.maximum(-y, 0.))
    return mean_model, downside_model, {"cutoff_exclusive": cutoff, "rows": len(train), "dates": train["signal_date"].nunique(), "max_label_exit_date": train["scheduled_exit_date"].max(), "train_target_mean": y.mean(), "train_downside_mean": np.maximum(-y, 0.).mean(), "training_keys_sha256": hashlib.sha256(train[KEY].to_csv(index=False).encode()).hexdigest()}


def load(repo, research):
    lm_path = repo / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
    lm = json.loads(lm_path.read_text())
    ledger_path = repo / lm["output"]["path"]
    expect(sha(ledger_path) == lm["output"]["sha256"] == "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd", "ledger hash mismatch")
    frame = read_csv(ledger_path)
    pm_path = research / "outputs/lagged_priors_manifest.json"
    pm = json.loads(pm_path.read_text())
    prior_path = research / "outputs" / pm["outputs"]["full"]["path"]
    expect(sha(prior_path) == pm["outputs"]["full"]["sha256"], "lagged prior hash mismatch")
    priors = read_csv(prior_path)
    expect(not priors.duplicated(KEY).any() and not frame.duplicated(KEY).any(), "duplicate candidates")
    max_dates = priors["lagged_prior_max_history_exit_date"].fillna("")
    expect((max_dates.lt(priors["signal_date"]) | max_dates.eq("")).all(), "lagged outcome availability violation")
    pcols = pm["outputs"]["full"]["feature_columns"]
    frame = frame.merge(priors[KEY + pcols], on=KEY, validate="one_to_one", how="left")
    expect(frame[pcols].notna().all().all(), "missing joined priors")
    columns = lm["feature_contract"]["columns"] + pcols
    forbidden = {"promotion_rank", "predicted_promotion_probability", "strategy_slot_net_return", "conditional_profit_hit", "public_market_buyable_proxy", "audit_t_close"}
    expect(not forbidden.intersection(columns), "target/cross-head leaked into features")
    source_path = repo / lm["inputs"]["five_year_source_ledger"]["path"]
    expect(sha(source_path) == lm["inputs"]["five_year_source_ledger"]["sha256"], "source truth hash mismatch")
    src = read_csv(source_path)
    frame = frame.merge(src[KEY + ["t_close"]].rename(columns={"t_close": "audit_t_close"}), on=KEY, validate="one_to_one", how="left")
    predict_path = research / "outputs/benchmark_predictions.csv.gz"
    expect(sha(predict_path) == "9ff2b3558ef4138b5de4015e9fe70cae9b8c2d7aa1eeeb71c1650be705c810f1", "saved historical predictions hash drifted")
    baseline = read_csv(predict_path)
    baseline = baseline.loc[baseline["model_kind"].eq("hgb") & baseline["variant"].eq("full_priors")].copy()
    return frame, columns, baseline, {"ledger": sha(ledger_path), "ledger_manifest": sha(lm_path), "lagged_priors": sha(prior_path), "prior_manifest": sha(pm_path), "historical_source_truth": sha(source_path), "saved_baseline_predictions": sha(predict_path), "feature_columns_sha256": hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode()).hexdigest(), "feature_count": len(columns), "source_benchmark_script": sha(research / "benchmark.py"), "ledger_builder": sha(repo / "scripts/build_decision_executable_profit_ledger.py")}


def audit_frame(frame):
    fill, r = frame["public_market_buyable_proxy"], frame["strategy_slot_net_return"]
    mature = fill.eq(1) & frame["conditional_net_return_after_cost"].notna()
    expect(frame["stage"].isin([2, 3]).all(), "candidate stage changed")
    expect(frame.groupby("signal_date").size().le(10).all(), "candidate count above Top10")
    expect(np.allclose(frame.loc[mature, "conditional_gross_return"] - frame.loc[mature, "conditional_net_return_after_cost"], .0045, atol=1e-10), "45bp arithmetic failed")
    expect(np.allclose(frame.loc[mature, "conditional_gross_return"], frame.loc[mature, "conditional_exit_price_proxy"] / frame.loc[mature, "conditional_entry_price_proxy"] - 1., atol=1e-10), "entry/exit arithmetic failed")
    expect(frame.loc[fill.eq(0), "strategy_slot_net_return"].eq(0).all(), "nonfill not cash")
    expect(np.allclose(frame.loc[mature, "strategy_slot_net_return_2x_cost"], r[mature] - .0045, atol=1e-10), "stress cost drift")
    expect(frame.loc[r.notna(), "executable_profit_proxy_hit"].eq(r[r.notna()].gt(0).astype(float)).all(), "profit target differs from net outcome")
    return {"rows": len(frame), "dates": frame["signal_date"].nunique(), "pending_rows": r.isna().sum(), "missing_buyability_rows": fill.isna().sum(), "formula_checks_passed": True, "gross_formula": "T1_open/T_open-1", "net_formula": "gross-0.0045", "stress_formula": "gross-0.009", "nonfill_policy": "cash=0", "actual_fill_observed": False, "blocked_limit_down_exit_resolved": False, "causal_entry_or_exit_optimization_claim": False, "intraday_overnight_attribution": "T_close/T_open-1 + (T1_open-T_close)/T_open = held gross return; descriptive only"}


def run(repo, research, output):
    plan = json.loads((HERE / "PLAN.json").read_text())
    frame, columns, saved, provenance = load(repo, research)
    audit = audit_frame(frame)
    dates = sorted(frame["signal_date"].unique())
    splits = {"development": dates[-360:-180], "confirmation": dates[-180:]}
    results, training, comparisons, split_audits = {}, {}, {}, {}
    all_scores, all_selected, all_daily = [], [], []
    for split, split_dates in splits.items():
        cutoff = split_dates[0]
        candidate_panel = frame.loc[frame["signal_date"].isin(split_dates)].copy()
        complete = candidate_panel.groupby("signal_date")["strategy_slot_net_return"].apply(lambda x: x.notna().all())
        complete_dates = complete.index[complete].tolist()
        expect(len(complete_dates) == 178, "legacy common panel date count changed")
        baseline = saved.loc[saved["split"].eq(split)]
        expect(set(map(tuple, baseline[KEY].to_numpy())) == set(map(tuple, candidate_panel[KEY].to_numpy())), "baseline candidate set changed")
        joined = candidate_panel.merge(baseline[KEY + ["predicted_executable_profit_probability", "predicted_profit_given_fill_probability", "predicted_fill_probability", "strategy_slot_net_return"]].rename(columns={"strategy_slot_net_return": "saved_return"}), on=KEY, validate="one_to_one")
        expect(np.allclose(joined["strategy_slot_net_return"], joined["saved_return"], equal_nan=True), "baseline outcomes changed")
        mean_model, down_model, training[split] = fit_return_models(frame, columns, cutoff)
        joined["expected_net"] = mean_model.predict(joined[columns])
        joined["expected_downside"] = np.maximum(down_model.predict(joined[columns]), 0.)
        joined["risk_adjusted"] = joined["expected_net"] - .5 * joined["expected_downside"]
        joined["promotion_score"] = -joined["promotion_rank"]
        joined["split"] = split
        all_scores.append(joined[KEY + ["split", "promotion_rank", "expected_net", "expected_downside", "risk_adjusted", "predicted_executable_profit_probability", "strategy_slot_net_return"]])
        panel = joined.loc[joined["signal_date"].isin(complete_dates)].copy()
        tie_rows = panel.duplicated(["signal_date", "predicted_executable_profit_probability"], keep=False).sum()
        expect(tie_rows == 0, "old vs current tie-breaker matters; explicit reproduction required")
        split_audits[split] = {"start": split_dates[0], "end": split_dates[-1], "calendar_dates": 180, "complete_dates": len(complete_dates), "incomplete_dates_excluded_for_all": complete.index[~complete].tolist(), "complete_candidate_rows": len(panel), "candidate_rows_before_common_truth_filter": len(joined), "same_candidates_and_outcomes": True, "exact_joint_score_ties": tie_rows, "candidate_keys_sha256": hashlib.sha256(panel[KEY].sort_values(KEY).to_csv(index=False).encode()).hexdigest(), "evidence_role": "viewed_retrospective_comparison_not_independent_confirmation"}
        specs = [("mixed_hgb_baseline", "predicted_executable_profit_probability", False), ("promotion_top2", "promotion_score", False), ("ridge_expected_net", "expected_net", False), ("ridge_return_downside", "risk_adjusted", False), ("ridge_expected_net_positive_only", "expected_net", True), ("ridge_return_downside_positive_only", "risk_adjusted", True)]
        results[split], dailies = {}, {}
        for name, score, positive in specs:
            daily, info, selected = policy(panel, score, positive)
            results[split][name], dailies[name] = info, daily
            all_selected.append(selected[KEY + ["stage", "promotion_rank", "expected_net", "expected_downside", "risk_adjusted", "strategy_slot_net_return"]].assign(split=split, policy=name))
            all_daily.append(daily.assign(split=split, policy=name))
        pool_daily = panel.groupby("signal_date").agg(net=("strategy_slot_net_return", "mean"), stress=("strategy_slot_net_return_2x_cost", "mean"), fill=("public_market_buyable_proxy", "mean")).reset_index()
        pool_daily["gross"] = pool_daily["net"] + .0045 * pool_daily["fill"]
        pool_daily["selected"], pool_daily["cash"] = 2, 0
        results[split]["same_date_pool_equal_weight"] = {"portfolio": portfolio_metrics(pool_daily), "note": "each D equal weights over identical candidate universe; synthetic 2-slot exposure equivalent"}
        cash = pool_daily.assign(net=0., gross=0., stress=0., selected=0, cash=2)
        results[split]["cash"] = {"portfolio": portfolio_metrics(cash)}
        base = dailies["mixed_hgb_baseline"]["net"].to_numpy()
        comparisons[split] = {name: {"paired_mean_daily_net_lift_vs_mixed": ci(day["net"].to_numpy() - base), "absolute_mean_daily_net": ci(day["net"])} for name, day in dailies.items() if name != "mixed_hgb_baseline"}
        print(json.dumps({"split_finished": split, "dates": len(complete_dates), "mean_returns": {k: v["portfolio"]["mean_daily_net"] for k, v in results[split].items()}}), flush=True)
    report = {"schema_version": "dc20_offline_return_downside_evaluation_v1", "status": "RETROSPECTIVE_RESEARCH_ONLY_NO_RELEASE", "plan_sha256": sha(HERE / "PLAN.json"), "script_sha256": sha(Path(__file__)), "provenance": provenance, "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__}, "input_audit": audit, "splits": split_audits, "training_audits": training, "results": results, "paired_comparisons": comparisons, "release_allowed": False, "independent_unviewed_confirmation": False, "production_changes": False, "new_forward_evidence": False}
    output.mkdir(parents=True, exist_ok=True)
    csv_gz(output / "candidate_scores.csv.gz", pd.concat(all_scores, ignore_index=True))
    csv_gz(output / "selected_records.csv.gz", pd.concat(all_selected, ignore_index=True))
    csv_gz(output / "daily_returns.csv.gz", pd.concat(all_daily, ignore_index=True))
    report["outputs"] = {p.name: sha(p) for p in output.glob("*.csv.gz")}
    write_json(output / "comparison.json", report)
    return report


def self_test():
    f = pd.DataFrame({"signal_date": ["20250101"] * 3, "ts_code": ["a", "b", "c"], "score": [.3, .1, -.2]})
    expect(f.sort_values(["score", "ts_code"], ascending=[False, True]).head(2)["ts_code"].tolist() == ["a", "b"], "selection invariant")
    dates = pd.Series(["20250101", "20250102", "20250103"])
    expect(dates.lt("20250102").tolist() == [True, False, False], "strict maturity invariant")
    expect(np.allclose(np.array([.05, -.10, 0.]) - .5 * np.array([0., .10, 0.]), [.05, -.15, 0.]), "downside penalty invariant")
    print("self-test passed", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=HERE.parents[1], help="DC20 repository root; inferred from this script by default")
    ap.add_argument("--research", type=Path, default=None, help="Existing lagged-prior research; defaults under repository work/")
    ap.add_argument("--output", type=Path, default=HERE / "outputs")
    args = ap.parse_args()
    self_test()
    research_root = args.research or args.repo / "work/executable-profit-lagged-features-20260824"
    result = run(args.repo.resolve(), research_root.resolve(), args.output.resolve())
    print(json.dumps({"completed": True, "output": str(args.output.resolve()), "release_allowed": False}), flush=True)
