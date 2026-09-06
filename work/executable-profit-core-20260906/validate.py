#!/usr/bin/env python3
"""Independent input, replay, future-poison and fixed-policy checks; no release."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

import evaluate as ev


def check_training_poison(frame, columns, plan, saved):
    results = []
    dates = sorted(saved.signal_date.unique())
    for cutoff in [dates[0], dates[-40]]:
        target = frame.loc[frame.signal_date.eq(cutoff)].copy()
        future = frame.scheduled_exit_date.ge(cutoff)
        poisoned = frame.copy()
        poisoned.loc[future, "strategy_slot_net_return"] = 7.0
        poisoned.loc[future, "conditional_net_return_after_cost"] = -5.0
        poisoned.loc[future, "public_market_buyable_proxy"] = 1 - poisoned.loc[future, "public_market_buyable_proxy"].fillna(0)
        poisoned.loc[future, columns] = 1234.0
        original_models, original_audit = ev.fit_heads(frame, columns, cutoff, plan)
        poisoned_models, poisoned_audit = ev.fit_heads(poisoned, columns, cutoff, plan)
        original = ev.predict_heads(original_models, target, columns)
        changed = ev.predict_heads(poisoned_models, target, columns)
        differences = np.abs(original[ev.HEADS + ev.SCORES].to_numpy() - changed[ev.HEADS + ev.SCORES].to_numpy())
        ev.expect(np.max(differences) < 1e-12, "future training labels/features changed earlier predictions")
        ev.expect(original_audit == poisoned_audit, "future poison changed training cohort")
        saved_date = saved.loc[saved.signal_date.eq(cutoff)].sort_values(ev.KEY)
        ev.expect(saved_date.ts_code.tolist() == original.ts_code.tolist(), "replay candidates differ")
        replay_difference = np.abs(original[ev.HEADS + ev.SCORES].to_numpy() - saved_date[ev.HEADS + ev.SCORES].to_numpy()).max()
        ev.expect(replay_difference < 1e-12, "deterministic replay changed predictions")
        result = {"cutoff": cutoff, "future_rows_poisoned_including_equal_exit_date": int(future.sum()), "training_sets_unchanged": True, "all_four_heads_and_five_scores_unchanged": True, "maximum_absolute_change": float(differences.max()), "maximum_saved_replay_difference": float(replay_difference)}
        results.append(result)
        print(json.dumps({"future_training_poison_passed": cutoff, "rows_poisoned": int(future.sum())}), flush=True)
    return results


def load_prior_module(repo):
    path = repo / "work/executable-profit-lagged-features-20260824/lagged_priors.py"
    spec = importlib.util.spec_from_file_location("dc20_profit_core_prior_validator", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_prior_poison(repo, frame, plan, provenance):
    module = load_prior_module(repo)
    full = ev.read_csv(repo / provenance["historical_full_source"]["path"])
    priors = ev.read_csv(repo / plan["source_inputs"]["full_lagged_priors"]["path"])
    columns = module.feature_columns("fullhist")
    opened = module.read_sse_open_dates(repo / plan["source_inputs"]["calendar"]["path"])
    dates = sorted(frame.signal_date.unique())[-360:]
    results = []
    for cutoff in [dates[0], dates[180], dates[-1]]:
        target = frame.loc[frame.signal_date.eq(cutoff)]
        future = full.target_exit_date.ge(cutoff)
        poisoned = full.copy()
        poisoned.loc[future, "market_fill"] = 1.0
        poisoned.loc[future, "net_return"] = 3.0
        poisoned.loc[future, "profit_hit"] = 1.0
        poisoned.loc[future, "big_loss_hit"] = 0.0
        built = module.build_lagged_features(history=full, targets=target, open_dates=opened, source_kind="full", prefix="fullhist").sort_values(ev.KEY)
        changed = module.build_lagged_features(history=poisoned, targets=target, open_dates=opened, source_kind="full", prefix="fullhist").sort_values(ev.KEY)
        frozen = priors.loc[priors.signal_date.eq(cutoff)].sort_values(ev.KEY)
        ev.expect(built.ts_code.tolist() == frozen.ts_code.tolist(), "prior rebuild candidates differ")
        poison_difference = np.abs(built[columns].to_numpy() - changed[columns].to_numpy()).max()
        frozen_difference = np.abs(built[columns].to_numpy() - frozen[columns].to_numpy()).max()
        ev.expect(poison_difference < 1e-12 and frozen_difference < 1e-10, "future labels contaminated lagged prior or frozen artifact mismatch")
        results.append({"D": cutoff, "future_full_source_rows_poisoned": int(future.sum()), "features": len(columns), "targets": len(target), "maximum_future_poison_change": float(poison_difference), "maximum_frozen_rebuild_difference": float(frozen_difference)})
        print(json.dumps({"future_prior_poison_passed": cutoff, "features": len(columns)}), flush=True)
    return results


def check_outputs(output, report, plan, frame):
    for name, checksum in report["outputs"].items():
        ev.expect(ev.sha(output / name) == checksum, "output hash mismatch: " + name)
    scores = ev.read_csv(output / "candidate_predictions.csv.gz")
    selected = ev.read_csv(output / "selected_records.csv.gz")
    daily = ev.read_csv(output / "daily_returns.csv.gz")
    ev.expect(np.allclose(scores.direct_slot_net, scores.direct_prediction, rtol=0, atol=1e-15), "direct slot was multiplied by fill")
    ev.expect(np.allclose(scores.fill_conditional_net, scores.fill_prediction * scores.conditional_prediction, rtol=0, atol=1e-15), "conditional multiplication changed")
    ev.expect(np.allclose(scores.conditional_net_downside, scores.conditional_prediction - .5 * scores.downside_prediction, rtol=0, atol=1e-15), "downside formula drift")
    ev.expect(np.allclose(scores.fill_conditional_net_downside, scores.fill_prediction * scores.conditional_net_downside, rtol=0, atol=1e-15), "fill/downside formula drift")
    ev.expect(scores.signal_date.nunique() == 360, "walk-forward date count changed")
    original = frame.loc[frame.signal_date.isin(scores.signal_date.unique())]
    ev.expect(original[ev.KEY].values.tolist() == scores.sort_values(ev.KEY)[ev.KEY].values.tolist(), "frozen candidate universe changed")
    incomplete = sorted(original.groupby("signal_date").apply(lambda g: g[["strategy_slot_net_return", "strategy_slot_net_return_2x_cost", "public_market_buyable_proxy"]].isna().any().any(), include_groups=False).loc[lambda s: s].index.tolist())
    ev.expect(incomplete == report["cohorts"]["all_360_signal_dates"]["common_excluded_dates"], "common missing-truth date rule changed")
    expected_days = set(scores.signal_date) - set(incomplete)
    for policy in ev.SCORES + ["promotion_top2"]:
        got = selected.loc[selected.policy.eq(policy)]
        ev.expect(set(got.signal_date) == expected_days, "policy silently skipped dates")
        for date, group in got.groupby("signal_date"):
            universe = scores.loc[scores.signal_date.eq(date)]
            expected = universe.sort_values([policy, "ts_code"], ascending=[False, True], kind="stable").head(2).ts_code.tolist()
            ev.expect(group.sort_values("slot").ts_code.tolist() == expected, "not fixed Top2 or padded candidates")
        daily_policy = daily.loc[daily.policy.eq(policy)]
        ev.expect(len(daily_policy) == len(expected_days) * 3, "Top1/Top2/equal missing")
    ev.expect(not daily[["net", "stress", "fill"]].isna().any().any(), "missing truth hidden in metrics")
    # The immutable ledger rounds base net to 10 decimals but retains greater
    # precision for stress net. Do not rewrite labels merely to remove rounding.
    cost_residual = np.abs(daily.stress - daily.net + .0045 * daily.fill).max()
    ev.expect(cost_residual < 1e-10, "cost treatment inconsistent beyond source rounding")
    ev.expect((frame.promotion_oof_train_end.astype(str) < frame.signal_date).all(), "frozen promotion training reaches candidate D")
    ev.expect(not report["release_allowed"] and not report["new_forward_evidence"] and not report["independent_unviewed_confirmation"], "research misrepresented as release evidence")
    return scores, {"five_score_identities_passed": True, "direct_net_no_double_fill": True, "identical_frozen_candidate_panel": True, "frozen_promotion_train_end_precedes_D": True, "identical_common_truth_dates": True, "missing_truth_dates": incomplete, "complete_dates": len(expected_days), "fixed_Top1_Top2_no_positive_filter": True, "45bp_90bp_cost_identity_passed": True, "maximum_source_rounding_cost_residual": float(cost_residual), "source_rounding_tolerance": 1e-10, "no_production_release": True}


def validate(repo, output):
    plan = json.loads((ev.HERE / "PLAN.json").read_text())
    report = json.loads((output / "comparison.json").read_text())
    ev.expect(report["plan_sha256"] == ev.sha(ev.HERE / "PLAN.json"), "plan changed after run")
    ev.expect(report["evaluate_script_sha256"] == ev.sha(ev.HERE / "evaluate.py"), "evaluator changed after run")
    frame, columns, provenance = ev.load(repo, plan)
    saved, output_checks = check_outputs(output, report, plan, frame)
    result = {"schema_version": "dc20_profit_core_research_validation_v1", "status": "PASS_RESEARCH_VALIDATION_ONLY", "release_allowed": False, "comparison_sha256": ev.sha(output / "comparison.json"), "plan_sha256": ev.sha(ev.HERE / "PLAN.json"), "validate_script_sha256": ev.sha(__file__), "output_checks": output_checks, "future_training_label_and_feature_poison": check_training_poison(frame, columns, plan, saved), "lagged_feature_rebuild_and_future_poison": check_prior_poison(repo, frame, plan, provenance), "limitations": ["This proves tested computational isolation, not live source publication timestamps.", "Research remains viewed retrospective data, not independent forward performance.", "Daily-open execution proxy and blocked exits remain unresolved."]}
    ev.write_json(output / "validation.json", result)
    print(json.dumps({"validation_complete": True, "status": result["status"], "release_allowed": False}), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--output", type=Path, default=ev.HERE / "outputs")
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        validate(ev.find_repo(args.repo), args.output)
