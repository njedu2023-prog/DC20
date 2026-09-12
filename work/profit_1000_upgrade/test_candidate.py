"""Contract tests; synthetic mini-panels deliberately lower reported data gates."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("profit_1000_candidate", Path(__file__).with_name("candidate.py"))
candidate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(candidate)

GATES = {"min_train_dates": 2, "min_train_rows": 4, "min_validation_dates": 2,
         "min_validation_rows": 4, "min_validation_filled_rows": 1,
         "min_training_complete_day_fraction": 0.5, "min_validation_complete_day_fraction": 1.0}


def sample():
    dates = ["20260820", "20260821", "20260824", "20260825"]
    maturity = ["20260824", "20260825", "20260826", "20260827"]
    rows, labels = [], []
    for index, (date, available) in enumerate(zip(dates, maturity)):
        for rank in (1, 2):
            code = f"60000{rank}.SH"
            rows.append({"signal_date": date, "ts_code": code, "feature_as_of_date": date,
                         "promotion_oof_train_end": "20260801",
                         "board_stage": rank + 1, "promotion_rank": rank,
                         "promotion_probability": 0.6 - rank * 0.1,
                         "path_change": 0.1 * rank, "path_label": "弱转强",
                         "existing_profit_rank": 3 - rank})
            labels.append({"signal_date": date, "ts_code": code,
                           "label_policy_id": candidate.POLICY_ID,
                           "entry_policy_id": "research_auction_or_open_no_cap_v1", "round_trip_cost_rate": 0.0045,
                           "label_status": candidate.SETTLED, "proxy_fill": 1,
                           "slot_net_return": -0.01 * rank,
                           "label_available_date": available, "actual_exit_date": available})
    # Split first 2 complete D dates into train; validation starts on the 24th.
    # Their labels must mature strictly before the split, so extend validation.
    for label in labels[:4]:
        label["actual_exit_date"] = label["label_available_date"] = "20260823"
    manifest = {"source_sha256": "a" * 64, "source_provenance_verified": True,
                "feature_timestamp_semantics": "RETROSPECTIVE_D_ONLY_BOUND",
                "promotion_prediction_provenance": "HISTORICAL_OOF",
                "entry_policy_id": "research_auction_or_open_no_cap_v1", "cost_rate": 0.0045,
                "day_candidate_counts": {date: 2 for date in dates}}
    return rows, labels, manifest


def run(rows=None, labels=None, manifest=None, **kwargs):
    defaults = sample()
    return candidate.run_candidate(defaults[0] if rows is None else rows,
                                   defaults[1] if labels is None else labels,
                                   frozen_manifest=defaults[2] if manifest is None else manifest,
                                   as_of_date="20260911", training_cutoff_date="20260824",
                                   validation_end_date="20260825", gates=GATES, **kwargs)


def test_empty_labels_fail_closed_before_any_fit(monkeypatch):
    monkeypatch.setattr(candidate, "_fit_ridge", lambda *_: pytest.fail("fit attempted"))
    result = run(labels=[])
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert result["candidate_model"] is None
    assert result["report"]["training_performed"] is False


def test_fit_is_json_research_only_negative_top_two_preserved():
    result = run()
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert len(result["predictions"]) == 4
    assert all(row["candidate_score"] < 0 for row in result["predictions"])
    assert all(row["selected_shadow_slot"] in (1, 2) for row in result["predictions"])
    assert result["report"]["production_activation_allowed"] is False
    assert result["report"]["holdout_metrics"] is None
    assert result["report"]["profitability_improvement_proven"] is False
    assert result["report"]["engineering_defaults_weakened"] is True
    assert json.loads(json.dumps(result, allow_nan=False))["status"] == result["status"]


def test_all_missing_feature_train_imputation_is_finite_zero():
    result = run()
    model = result["candidate_model"]
    index = model["features"].index("d_pct_change")
    assert model["imputation_train_medians"][index] == 0
    assert model["scaling_train_scales"][index] == 1


def test_imputation_and_scaling_do_not_use_validation():
    rows, labels, manifest = sample()
    for index, row in enumerate(rows):
        row["d_pct_change"] = index if index < 4 else 100000.0
    model = run(rows, labels, manifest)["candidate_model"]
    index = model["features"].index("d_pct_change")
    assert model["imputation_train_medians"][index] == 1.5
    assert model["scaling_train_means"][index] == 1.5


def test_actual_exit_purges_entire_training_day():
    rows, labels, manifest = sample()
    labels[0]["actual_exit_date"] = labels[0]["label_available_date"] = "20260824"
    result = run(rows, labels, manifest)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    coverage = result["report"]["coverage"]
    assert coverage["training_complete_dates"] == 1
    assert coverage["training_complete_rows"] == 2
    assert coverage["training_purged_or_incomplete_dates"] == ["20260820"]


def test_label_availability_purges_even_when_exit_is_earlier():
    rows, labels, manifest = sample()
    labels[0]["label_available_date"] = "20260824"
    assert run(rows, labels, manifest)["report"]["coverage"]["training_complete_dates"] == 1


def test_missing_validation_row_cannot_select_known_winners(monkeypatch):
    rows, labels, manifest = sample()
    monkeypatch.setattr(candidate, "_fit_ridge", lambda *_: pytest.fail("fit attempted"))
    result = run(rows, labels[:-1], manifest)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert "INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED" in result["report"]["failed_quality_gates"]


def test_frozen_universe_missing_candidate_rejected():
    rows, labels, manifest = sample()
    result = run(rows[:-1], labels[:-1], manifest)
    assert result["status"] == "BLOCKED_INPUT"
    assert "INCOMPLETE_FROZEN_DAY" in result["report"]["input_error"]


@pytest.mark.parametrize("bad", ["SETTLED_OPEN_PROXY", "FINAL_OPEN_PROXY", "whatever"])
def test_old_label_status_rejected(bad):
    rows, labels, manifest = sample()
    labels[0]["label_status"] = bad
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


def test_old_label_policy_rejected():
    rows, labels, manifest = sample()
    labels[0]["label_policy_id"] = "old_t1_open"
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


@pytest.mark.parametrize("change", [{"entry_policy_id": "research_auction_or_open_frozen_cap_v1"},
                                   {"round_trip_cost_rate": 0}, {"round_trip_cost_rate": 0.009}])
def test_mixed_entry_or_cost_labels_rejected_before_fit(change, monkeypatch):
    rows, labels, manifest = sample()
    labels[0].update(change)
    monkeypatch.setattr(candidate, "_fit_ridge", lambda *_: pytest.fail("fit attempted"))
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


def test_model_and_report_record_entry_cost_and_formal_mismatch():
    result = run()
    for section in ("report", "candidate_model"):
        assert result[section]["entry_policy_id"] == "research_auction_or_open_no_cap_v1"
        assert result[section]["round_trip_cost_rate"] == 0.0045
    assert result["report"]["entry_policy_production_compatibility"] == "ENTRY_POLICY_DIFFERENT_FROM_FORMAL_FROZEN_CAP"


def test_future_feature_rejected():
    rows, labels, manifest = sample()
    rows[0]["feature_as_of_date"] = "20260821"
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


@pytest.mark.parametrize("train_end", ["20260820", "20260910"])
def test_promotion_prediction_cannot_train_on_same_or_future_d(train_end):
    rows, labels, manifest = sample()
    rows[0]["promotion_oof_train_end"] = train_end
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


@pytest.mark.parametrize("change", [{"source_provenance_verified": False}, {"source_sha256": "x"},
                                   {"feature_timestamp_semantics": "POST_HOC"}])
def test_source_provenance_required(change):
    rows, labels, manifest = sample()
    manifest.update(change)
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


def test_existing_profit_unavailable_is_not_reconstructed():
    rows, labels, manifest = sample()
    rows[-1].pop("existing_profit_rank")
    report = run(rows, labels, manifest)["report"]
    assert report["comparisons"]["frozen_existing_profit"]["status"] == "UNAVAILABLE_SAME_UNIVERSE_BASELINE"
    assert report["development_mean_slot_return_deltas"]["frozen_existing_profit"] is None


def test_comparisons_use_identical_validation_dates_and_counts():
    comparisons = run()["report"]["comparisons"]
    assert len({tuple(value["dates"]) for value in comparisons.values()}) == 1
    assert all(value["top1"]["slots"] == 2 and value["top2"]["slots"] == 2 for value in comparisons.values())


def test_no_fill_zero_slot_not_filled_win_rate_denominator():
    rows, labels, manifest = sample()
    labels[-1].update({"label_status": "NO_FILL_AUCTION_PROXY", "proxy_fill": 0,
                       "slot_net_return": 0, "actual_exit_date": None})
    report = run(rows, labels, manifest)["report"]
    top2 = report["comparisons"]["frozen_promotion"]["top2"]
    assert top2["slots"] == 2 and top2["filled_proxy_slots"] == 1
    assert top2["known_no_fill_slots"] == 1
    assert top2["filled_proxy_win_rate"] == 0


def test_stress_90bp_same_selection_extra_cost_only_for_fills():
    rows, labels, manifest = sample()
    labels[-1].update({"label_status": "NO_FILL_AUCTION_PROXY", "proxy_fill": 0,
                       "slot_net_return": 0, "actual_exit_date": None})
    comparisons = run(rows, labels, manifest)["report"]["comparisons"]
    baseline = comparisons["frozen_promotion"]
    stress = baseline["stress_90bp_same_selections"]
    assert not stress["selection_reoptimized"]
    assert stress["round_trip_cost_rate"] == 0.009
    assert baseline["top2"]["slots"] == stress["top2"]["slots"] == 2
    assert stress["top2"]["known_no_fill_slots"] == 1
    assert baseline["top2"]["mean_net_slot_return"] - stress["top2"]["mean_net_slot_return"] == pytest.approx(0.0045 / 2)
    assert baseline["top1"]["mean_net_slot_return"] - stress["top1"]["mean_net_slot_return"] == pytest.approx(0.0045)


def test_pending_must_not_be_zero_or_fake_filled():
    rows, labels, manifest = sample()
    labels[-1].update({"label_status": "PENDING_MINUTE_TRUTH", "slot_net_return": 0})
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


def test_future_terminal_artifact_does_not_leak_into_asof():
    rows, labels, manifest = sample()
    labels[-1]["actual_exit_date"] = labels[-1]["label_available_date"] = "20260915"
    result = run(rows, labels, manifest)
    assert result["status"] == "BLOCKED_DATA_QUALITY"


def test_future_holdout_outcome_changes_do_not_affect_model():
    rows, labels, manifest = sample()
    future = copy.deepcopy(rows[-2:])
    for row in future:
        row["signal_date"] = row["feature_as_of_date"] = "20260914"
    rows.extend(future)
    manifest["day_candidate_counts"]["20260914"] = 2
    base = run(rows, labels, manifest)
    labels.extend({"signal_date": "20260914", "ts_code": row["ts_code"],
                   "slot_net_return": 999, "label_policy_id": "not-even-inspected"} for row in future)
    changed = run(rows, labels, manifest)
    assert base["candidate_model"] == changed["candidate_model"]
    assert base["predictions"] == changed["predictions"]
    assert changed["report"]["holdout_rows_excluded_without_outcome_evaluation"] == 2


def test_cannot_move_holdout_into_previously_inspected_history():
    result = run(holdout_start_date="20260910")
    assert result["status"] == "BLOCKED_INPUT"


def test_cannot_slide_holdout_later_after_observing_results():
    result = run(holdout_start_date="20260915")
    assert result["status"] == "BLOCKED_INPUT"


def test_missing_probability_and_path_are_reported_without_backcast():
    rows, labels, manifest = sample()
    for row in rows:
        row.pop("promotion_probability")
        row.pop("path_label")
    result = run(rows, labels, manifest)
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert result["report"]["feature_coverage"]["promotion_probability"]["known_rows"] == 0
    assert result["report"]["feature_coverage"]["path_label"]["known_rows"] == 0


def test_partial_existing_profit_rank_sequence_not_treated_as_top1_top2():
    rows, labels, manifest = sample()
    for row in rows:
        row["existing_profit_rank"] += 2
    report = run(rows, labels, manifest)["report"]
    assert report["comparisons"]["frozen_existing_profit"]["status"] == "UNAVAILABLE_SAME_UNIVERSE_BASELINE"


def test_no_training_target_variation_blocks_fit(monkeypatch):
    rows, labels, manifest = sample()
    for row in labels[:4]:
        row["slot_net_return"] = -0.01
    monkeypatch.setattr(candidate, "_fit_ridge", lambda *_: pytest.fail("fit attempted"))
    result = run(rows, labels, manifest)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert "TRAINING_TARGET_HAS_NO_VARIATION" in result["report"]["failed_quality_gates"]


@pytest.mark.parametrize("mutate", [lambda rows, labels: rows.append(dict(rows[0])),
                                    lambda rows, labels: labels.append(dict(labels[0])),
                                    lambda rows, labels: labels[0].update(ts_code="600999.SH")])
def test_duplicate_or_extra_keys_rejected(mutate):
    rows, labels, manifest = sample()
    mutate(rows, labels)
    assert run(rows, labels, manifest)["status"] == "BLOCKED_INPUT"


def test_disallowed_outcome_columns_never_enter_model():
    rows, labels, manifest = sample()
    for row in rows:
        row.update(t_open=999, actual_exit_price=999, future_return=999)
    result = run(rows, labels, manifest)
    assert result["candidate_model"]["features"] == list(candidate.FEATURES)
    assert not any(name in candidate.FEATURES for name in ("t_open", "actual_exit_price", "future_return"))
