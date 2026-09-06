"""In-memory synthetic fixtures only; never trains on production history."""
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

_spec = importlib.util.spec_from_file_location("dc20_execution_v2_training_test_module", Path(__file__).with_name("train_candidate.py"))
training = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = training
_spec.loader.exec_module(training)


def fixed_plan():
    return {"hgb_parameters": dict(training.PARAMETERS), "training": dict(training.TRAINING_CONTRACT)}


def synthetic(days=20, names=5):
    dates = pd.bdate_range("2022-01-03", periods=days + 10).strftime("%Y%m%d").tolist()
    rows, labels = [], []
    rng = np.random.default_rng(20260906)
    for i, date in enumerate(dates[:days]):
        for rank in range(1, names + 1):
            row = {"signal_date": date, "ts_code": f"{rank:06d}.SZ", "exec_date": dates[i + 1], "scheduled_exit_date": dates[i + 2], "promotion_rank": rank}
            row.update({column: float(value) for column, value in zip(training.FEATURES, rng.normal(size=48))})
            rows.append(row)
            net = .01 * np.tanh(row[training.FEATURES[0]]) - .003
            labels.append({**{k: row[k] for k in training.KEY + ["exec_date", "scheduled_exit_date"]}, "label_status": "SETTLED_OPEN_PROXY", "proxy_fill": 1., "slot_net_return": net, "slot_net_return_stress": net - .0045, "conditional_net_return": net, "label_available_date": dates[i + 2], "actual_exit_date": dates[i + 2], "blocked_exit_sessions": 0, "entry_price_proxy": 100., "exit_price_proxy": 100. * (1. + net + .0045), "gross_return_proxy": net + .0045})
    return pd.DataFrame(rows), pd.DataFrame(labels), dates


def test_small_realistic_label_support_blocks_without_fit(monkeypatch):
    frozen, labels, _ = synthetic(30)
    labels = labels.iloc[:132].copy()
    frame = training.merge_labels(frozen, labels)
    monkeypatch.setattr(training, "fit_heads", lambda _: pytest.fail("insufficient data must never call fit"))
    ready, result = training.evaluate_frames(frame, fixed_plan())
    assert not ready["ready"] and result is None
    assert ready["models_fit"] == 0 and not ready["model_weights_saved"]
    assert not ready["result_artifacts_valid"] and ready["valid_output_files"] == ["training_readiness.json"]
    assert ready["terminal_rows"] == 132
    assert ready["complete_signal_dates"] == 26
    assert not ready["missing_truth_as_zero"] and not ready["release_allowed"]
    assert frame.loc[frame.label_status.eq("MISSING_LABEL_ROW"), "slot_net_return"].isna().all()


def test_actual_exit_availability_not_scheduled_exit_controls_training():
    frozen, labels, dates = synthetic(6)
    labels.loc[:4, "actual_exit_date"] = dates[7]
    labels.loc[:4, "label_available_date"] = dates[7]
    labels.loc[:4, "blocked_exit_sessions"] = 5
    frame = training.merge_labels(frozen, labels)
    assert dates[0] not in training.complete_dates(frame, dates[7])
    assert dates[0] in training.complete_dates(frame, dates[8])
    train = training.training_at(frame, dates[7])
    assert train.label_available_date.max() < dates[7]


def test_one_missing_or_nonterminal_candidate_excludes_whole_D():
    frozen, labels, dates = synthetic(5)
    labels.loc[0, "label_status"] = "UNRESOLVED_EXIT"
    # Even a mistakenly provided numeric zero cannot promote unresolved truth.
    labels.loc[0, "slot_net_return"] = 0.
    frame = training.merge_labels(frozen, labels)
    assert dates[0] not in training.complete_dates(frame)
    ready = training.assess_readiness(frame, fixed_plan())
    assert ready["excluded_incomplete_D_dates"][0]["terminal_candidates"] == 4


def test_nofill_cash_is_known_not_imputed():
    frozen, labels, _ = synthetic(2)
    labels.loc[0, "label_status"] = "NO_FILL_OPEN_LIMIT_UP_PROXY"
    labels.loc[0, ["proxy_fill", "slot_net_return", "slot_net_return_stress"]] = 0.
    labels.loc[0, "conditional_net_return"] = np.nan
    labels.loc[0, "actual_exit_date"] = ""
    labels.loc[0, "label_available_date"] = labels.loc[0, "exec_date"]
    frame = training.merge_labels(frozen, labels)
    assert frame.iloc[0]._trainable and frame.iloc[0].slot_net_return == 0
    bad = labels.copy()
    bad.loc[0, "slot_net_return"] = .01
    with pytest.raises(ValueError, match="KNOWN_NO_FILL_NOT_CASH_ZERO"):
        training.merge_labels(frozen, bad)


def test_date_and_outside_candidate_mismatch_fails_closed():
    frozen, labels, _ = synthetic(2)
    bad = labels.copy()
    bad.loc[0, "exec_date"] = "20990101"
    with pytest.raises(ValueError, match="LABEL_DATE_BINDING_MISMATCH"):
        training.merge_labels(frozen, bad)
    bad = labels.copy()
    bad.loc[0, "ts_code"] = "999999.SZ"
    with pytest.raises(ValueError, match="LABEL_KEYS_OUTSIDE"):
        training.merge_labels(frozen, bad)


def test_fixed_minimum_support_cannot_be_reduced_by_plan():
    plan = fixed_plan()
    plan["training"]["min_train_rows"] = 5
    with pytest.raises(ValueError, match="PLAN_TRAINING_CONTRACT_MISMATCH"):
        training.validate_plan(plan)


def test_asof_itself_must_be_an_open_session():
    frozen, labels, dates = synthetic(2)
    frame = training.merge_labels(frozen, labels)
    with pytest.raises(ValueError, match="AS_OF_NOT_STRICT_SSE_SESSION"):
        training.validate_calendar(frame, dates, "20220109")


def test_all_unknown_gross_audit_has_no_fake_values():
    _, labels, _ = synthetic(2)
    labels["label_status"] = "MISSING_T_TRUTH"
    for column in ["entry_price_proxy", "exit_price_proxy", "gross_return_proxy"]:
        labels[column] = None
    training.validate_gross_prices(labels)


def test_replay_payload_preserves_unknown_versus_zero_and_changed_values():
    rows = [{"date": "20260828", "net": None}, {"date": "20260831", "net": 0.0}]
    payload = training.replay_payload(rows, ["date", "net"])
    assert payload == b"date,net\n20260828,\n20260831,0.0\n"
    rows[0]["net"] = 0.0
    assert training.replay_payload(rows, ["date", "net"]) != payload


def test_negative_scores_fixed_two_slots_ties_and_cash():
    frozen, labels, _ = synthetic(2, names=3)
    frame = training.merge_labels(frozen, labels).iloc[:4].copy()
    frame["direct_slot_net"] = -1.
    selected, daily = training.select_fixed_top2(frame, "direct_slot_net")
    assert selected.ts_code.tolist() == ["000001.SZ", "000002.SZ", "000001.SZ"]
    assert daily.loc[daily.slot.eq("Top2"), "absent_cash_slots"].tolist() == [0, 1]
    assert daily.loc[daily.slot.eq("equal_Top2"), "candidate_slots"].tolist() == [2, 1]


def test_synthetic_fit_future_poison_and_no_double_fill():
    frozen, labels, dates = synthetic(270)
    frame = training.merge_labels(frozen, labels)
    cutoff = dates[260]
    train = training.training_at(frame, cutoff)
    target = frame.loc[frame.signal_date.eq(cutoff)].copy()
    poisoned = frame.copy()
    future = poisoned.label_available_date.ge(cutoff)
    poisoned.loc[future, "slot_net_return"] = 8.
    poisoned.loc[future, training.FEATURES] = 1000.
    poison_train = training.training_at(poisoned, cutoff)
    assert train[training.KEY].values.tolist() == poison_train[training.KEY].values.tolist()
    with threadpool_limits(limits=2):
        before = training.predict_heads(training.fit_heads(train), target)
        after = training.predict_heads(training.fit_heads(poison_train), target)
    assert np.allclose(before[training.SCORES], after[training.SCORES], rtol=0, atol=1e-12)
    assert np.array_equal(before.direct_slot_net, before.direct_prediction)
    assert np.allclose(before.direct_slot_net_downside, before.direct_prediction - .5 * before.downside_prediction)
    target_changed_fill = target.copy()
    target_changed_fill["proxy_fill"] = 0.
    with threadpool_limits(limits=2):
        heads = training.fit_heads(train)
        fill0 = training.predict_heads(heads, target_changed_fill)
        fill1 = training.predict_heads(heads, target)
    assert np.array_equal(fill0[training.SCORES], fill1[training.SCORES])
    assert len(training.FEATURES) == 48
    assert not any(c.startswith("fullhist_") for c in training.FEATURES)


def test_settled_base_stress_and_gross_must_match_fixed_costs():
    frozen, labels, _ = synthetic(3)
    training.validate_gross_prices(labels)
    bad = labels.copy()
    bad.loc[0, "slot_net_return_stress"] -= .01
    with pytest.raises(ValueError, match="SETTLED_STRESS_COST_DIFFERENCE_NOT_45BP"):
        training.merge_labels(frozen, bad)
    bad = labels.copy()
    bad.loc[0, "exit_price_proxy"] *= 1.1
    with pytest.raises(ValueError, match="GROSS_PRICE_FORMULA_MISMATCH"):
        training.validate_gross_prices(bad)
    bad = labels.copy()
    bad.loc[0, "slot_net_return"] += .001
    with pytest.raises(ValueError, match="BASE_COST_NOT_45BP_FROM_GROSS"):
        training.validate_gross_prices(bad)


def test_calendar_and_future_asof_fail_closed():
    frozen, labels, dates = synthetic(3)
    frame = training.merge_labels(frozen, labels)
    training.validate_calendar(frame, dates, dates[-1])
    with pytest.raises(ValueError, match="LABEL_AVAILABILITY_AFTER_AS_OF_DATE"):
        training.validate_calendar(frame, dates, dates[2])
    bad = frame.copy()
    bad.loc[0, "actual_exit_date"] = "20220108"
    bad.loc[0, "label_available_date"] = "20220108"
    with pytest.raises(ValueError, match="NOT_SSE_SESSION"):
        training.validate_calendar(bad, dates, dates[-1])
    bad = frame.copy()
    bad.loc[0, "exec_date"] = dates[2]
    with pytest.raises(ValueError, match="D_T_T1_NOT_ADJACENT"):
        training.validate_calendar(bad, dates, dates[-1])


def test_manifest_source_builder_asof_pins():
    plan = {"source_commit": "a" * 40, "as_of_date": "20260904", "source_inputs": {"calendar": {"path": "calendar.csv", "sha256": "b" * 64}}}
    manifest = {**plan, "plan_sha256": "p" * 64, "builder_sha256": "c" * 64, "identity_unchanged": True, "missing_as_zero": False, "actual_execution_claimed": False}
    training.validate_manifest_metadata(manifest, plan, "p" * 64, "c" * 64)
    for key, bad_value in [("source_commit", "d" * 40), ("as_of_date", "20990101"), ("source_inputs", {}), ("builder_sha256", "f" * 64), ("plan_sha256", "x" * 64)]:
        bad = {**manifest, key: bad_value}
        with pytest.raises(ValueError):
            training.validate_manifest_metadata(bad, plan, "p" * 64, "c" * 64)


def test_paths_cannot_redirect_to_production_or_through_symlinks(monkeypatch):
    with pytest.raises(ValueError, match="ONLY_FIXED_RESEARCH_OUTPUTS_ARE_WRITABLE"):
        training.write_json(training.HERE.parents[1] / "outputs/decision/production.json", {})
    with pytest.raises(ValueError, match="UNSAFE_INPUT_RELATIVE_PATH"):
        training.safe_input(training.HERE, "../PLAN.json")
    original = Path.is_symlink
    evil_directory = training.HERE / "evil"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == evil_directory or original(path))
    with pytest.raises(ValueError, match="SYMLINK_PATH_FORBIDDEN"):
        training.safe_input(training.HERE, "evil/source.csv")
    evil_output = training.HERE / "outputs/training_readiness.json"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == evil_output or original(path))
    with pytest.raises(ValueError, match="SYMLINK_PATH_FORBIDDEN"):
        training.write_json(evil_output, {})


def test_stale_comparison_is_not_reused_or_deleted(monkeypatch):
    captured = []
    output = training.HERE / "outputs"
    original = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: path == output / "training_comparison.json" or original(path))
    monkeypatch.setattr(training, "output_directory", lambda: output)
    monkeypatch.setattr(training, "load_inputs", lambda *args: pytest.fail("stale results must block before loading/fitting"))
    monkeypatch.setattr(training, "write_json", lambda path, data: captured.append((path, data)))
    monkeypatch.setattr(sys, "argv", ["train_candidate.py"])
    assert training.main() == 2
    assert len(captured) == 1
    assert captured[0][0].name == "training_readiness.json"
    assert not captured[0][1]["result_artifacts_valid"]
    assert captured[0][1]["valid_output_files"] == ["training_readiness.json"]
    assert captured[0][1]["models_fit"] == 0


def test_error_after_gate_does_not_claim_zero_models(monkeypatch):
    captured = []
    output = training.HERE / "outputs"
    original = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: False if path.parent == output and path.name in training.RESULT_FILES else original(path))
    monkeypatch.setattr(training, "output_directory", lambda: output)
    monkeypatch.setattr(training, "find_repo", lambda *_: training.HERE.parents[1])
    monkeypatch.setattr(training, "load_inputs", lambda *args: (fixed_plan(), None, {}))
    def failing_evaluation(*args):
        raise ValueError("synthetic error after gate")
    monkeypatch.setattr(training, "evaluate_frames", failing_evaluation)
    monkeypatch.setattr(training, "write_json", lambda path, data: captured.append(data))
    monkeypatch.setattr(sys, "argv", ["train_candidate.py"])
    assert training.main() == 2
    assert captured[0]["status"] == "ERROR_IN_OFFLINE_EVALUATION"
    assert captured[0]["models_fit"] is None
