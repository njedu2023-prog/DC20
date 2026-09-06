"""Fast contract tests; validate.py holds expensive replay and poison checks."""
import json
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
_spec = importlib.util.spec_from_file_location("dc20_profit_core_evaluate_20260906_test", Path(__file__).with_name("evaluate.py"))
ev = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ev
_spec.loader.exec_module(ev)


def test_plan_is_fixed_not_a_search():
    plan = json.loads((ev.HERE / "PLAN.json").read_text())
    assert plan["plan_fixed_before_candidate_calculation"]
    assert plan["status"] == ev.STATUS
    assert list(plan["scores"]) == ev.SCORES
    assert plan["single_fixed_HGB_parameter_set"] == {"max_iter": 200, "learning_rate": .05, "max_leaf_nodes": 7, "min_samples_leaf": 40, "l2_regularization": 10., "early_stopping": False, "random_state": 20260906}
    assert not plan["selection"]["positive_score_filter_allowed"]
    assert not any(plan["boundaries"].values())


def test_exit_label_maturity_is_strict():
    frame = pd.DataFrame({"signal_date": ["20260901"] * 3, "scheduled_exit_date": ["20260902", "20260903", "20260904"], "strategy_slot_net_return": [0., 1., 2.], "conditional_net_return_after_cost": [.1, .2, .3], "public_market_buyable_proxy": [1., 1., 1.]})
    train = ev.training_sets(frame, "20260903")
    assert all(len(part) == 1 for part in train.values())
    assert all(part.scheduled_exit_date.max() < "20260903" for part in train.values())


def test_negative_scores_still_select_two_no_padding():
    frame = pd.DataFrame({"signal_date": ["20260901"] * 3 + ["20260902"], "ts_code": ["B", "A", "C", "D"], "test_score": [-1., -1., -2., -3.], "strategy_slot_net_return": [-.01, .02, .03, -.04], "strategy_slot_net_return_2x_cost": [-.0145, .0155, .0255, -.0445], "public_market_buyable_proxy": [1.] * 4})
    selected, daily = ev.select_policy(frame, "test_score")
    assert selected.ts_code.tolist() == ["A", "B", "D"]
    absent = daily.loc[daily.signal_date.eq("20260902") & daily.slot.eq("Top2")].iloc[0]
    assert absent.net == 0 and absent.cash == 1 and absent.selected == 0
    equal = daily.loc[daily.signal_date.eq("20260902") & daily.slot.eq("equal_Top2")].iloc[0]
    assert equal.net == -.02 and equal.selected == 1


def test_saved_artifacts_and_validation_stay_nonrelease():
    output = ev.HERE / "outputs"
    report = json.loads((output / "comparison.json").read_text())
    val = json.loads((output / "validation.json").read_text())
    assert report["plan_sha256"] == ev.sha(ev.HERE / "PLAN.json")
    assert report["evaluate_script_sha256"] == ev.sha(ev.HERE / "evaluate.py")
    assert val["comparison_sha256"] == ev.sha(output / "comparison.json")
    assert val["validate_script_sha256"] == ev.sha(ev.HERE / "validate.py")
    assert not report["production_replacement_supported"]
    assert not report["release_allowed"] and not report["new_forward_evidence"]
    assert val["status"] == "PASS_RESEARCH_VALIDATION_ONLY" and not val["release_allowed"]
    assert report["cohorts"]["all_360_signal_dates"]["complete_signal_dates"] == 356
    assert len(report["training_audits"]) == 9
    assert report["experiment_count"]["parameter_sets"] == 1
    assert report["experiment_count"]["post_result_adjustments"] == 0
    for name, checksum in report["outputs"].items():
        assert ev.sha(output / name) == checksum
    for fold in report["training_audits"]:
        assert all(h["maximum_label_exit_date"] < fold["cutoff_exclusive"] for h in fold["heads"].values())
    for test in val["future_training_label_and_feature_poison"]:
        assert test["maximum_absolute_change"] < 1e-12
    for test in val["lagged_feature_rebuild_and_future_poison"]:
        assert test["maximum_future_poison_change"] < 1e-12
