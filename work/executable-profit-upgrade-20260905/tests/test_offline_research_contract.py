"""Guard the committed offline evidence without retraining or changing production."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def test_fixed_plan_does_not_turn_retrospective_results_into_release():
    plan = read_json(ROOT / "PLAN.json")
    result = read_json(ROOT / "outputs/comparison.json")
    assert plan["fixed_before_new_candidate_results"] is True
    assert plan["historical_windows_already_viewed"] is True
    assert plan["model"]["alpha"] == 1000.0
    assert plan["model"]["hyperparameter_search"] is False
    assert set(plan["candidates"]) == {"ridge_expected_net", "ridge_return_downside"}
    assert plan["selection"]["positive_score_threshold"] == 0.0
    assert result["status"] == "RETROSPECTIVE_RESEARCH_ONLY_NO_RELEASE"
    for key in ("release_allowed", "independent_unviewed_confirmation", "production_changes", "new_forward_evidence"):
        assert result[key] is False
    assert hashlib.sha256((ROOT / "PLAN.json").read_bytes()).hexdigest() == result["plan_sha256"]


def test_recorded_results_reject_production_replacement():
    result = read_json(ROOT / "outputs/comparison.json")
    for split in ("development", "confirmation"):
        for name in ("mixed_hgb_baseline", "ridge_expected_net", "ridge_return_downside"):
            assert result["results"][split][name]["portfolio"]["mean_daily_net"] < 0
    comparison = result["paired_comparisons"]["confirmation"]
    for name in ("ridge_expected_net", "ridge_return_downside"):
        interval = comparison[name]["paired_mean_daily_net_lift_vs_mixed"]
        assert interval["ci95_low"] < 0 < interval["ci95_high"]
    sparse = result["results"]["confirmation"]["ridge_return_downside_positive_only"]["portfolio"]
    assert sparse["selected_slots"] == 17
    assert sparse["cash_slots"] == 339
    assert sparse["mean_net_95ci"]["ci95_low"] < 0 < sparse["mean_net_95ci"]["ci95_high"]


def test_artifacts_and_temporal_audit_are_intact():
    result = read_json(ROOT / "outputs/comparison.json")
    for filename, expected in result["outputs"].items():
        assert hashlib.sha256((ROOT / "outputs" / filename).read_bytes()).hexdigest() == expected
    audit = read_json(ROOT / "outputs/validation.json")
    assert audit["passed"] is True
    assert audit["all_D_T_T1_adjacent_SSE_dates"] is True
    assert audit["reconstructed_lagged_feature_rows"] == 6753
    assert audit["lagged_reconstruction_max_abs_delta"] < 1e-11
    assert all(item["identical_features"] for item in audit["future_outcome_feature_invariance"])
    assert all(item["mean_and_downside_predictions_identical"] for item in audit["future_label_model_invariance"])
    assert all(audit["legacy_baseline_reproduced"].values())
    assert audit["release_allowed"] is False


def test_repository_relative_inputs_still_match_frozen_evidence():
    spec = importlib.util.spec_from_file_location("offline_return_research", ROOT / "evaluate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = ROOT.parents[1]
    frame, columns, baseline, provenance = module.load(repo, repo / "work/executable-profit-lagged-features-20260824")
    stored = read_json(ROOT / "outputs/comparison.json")["provenance"]
    for key in ("ledger", "historical_source_truth", "lagged_priors", "saved_baseline_predictions", "feature_columns_sha256"):
        assert provenance[key] == stored[key]
    assert len(frame) == 6753
    assert len(columns) == 156
    assert np.isfinite(baseline["predicted_executable_profit_probability"]).all()


def test_packaging_preserves_original_run_and_discloses_cli_only_changes():
    package = read_json(ROOT / "PACKAGING.json")
    result = read_json(ROOT / "outputs/comparison.json")
    assert package["models_retrained_during_packaging"] is False
    assert package["result_values_changed_during_packaging"] is False
    assert package["original_run_script_sha256"] == result["script_sha256"]
    for name, expected in package["packaged_files_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
