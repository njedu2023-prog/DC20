"""Synthetic arithmetic only: never a real model, forward source or trade."""
import ast
import builtins
from copy import deepcopy
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import candidate_forward_predict as m
from work.profit_1000_upgrade import candidate as old, candidate_v3, candidate_labels_overlay as overlay


def synthetic_model():
    return {"schema_version": m.MODEL_SCHEMA, "research_only": True, "production_activation_allowed": False,
        "label_policy_id": m.EXIT_POLICY_ID, "entry_policy_id": m.ENTRY_POLICY_ID,
        "specification": deepcopy(m.MODEL_SPEC), "features": list(m.FEATURES),
        "imputation_train_medians": [0.] * 29, "scaling_train_means": [0.] * 29,
        "scaling_train_scales": [1.] * 29, "coefficients": [0.] * 29, "intercept": -1.,
        "sklearn_version": "1.7.2", "numpy_version": "2.3.2", "fit_signal_date_min": "20221111",
        "fit_signal_date_max": "20251107", "fit_label_available_date_max": "20251110",
        "round_trip_cost_rate": .0045, "frozen_source_sha256": m.SOURCE_SHA,
        "source_overlay_contract": deepcopy(m.OVERLAY_CONTRACT),
        "input_bindings": {**{k: "a" * 64 for k in m.BINDING_KEYS},
            "source_sha256": m.SOURCE_SHA, "base_archive_sha256": m.BASE_ARCHIVE_SHA},
        "provider_timestamp_semantics_confirmed": False,
        "training_cutoff_date": m.TRAIN_CUTOFF, "holdout_start_date": m.FORWARD_START}


def row(code="600002.SH", rank=1, stage=2):
    return {**{f: None for f in m.NUMERIC_FEATURES}, "signal_date": "20260914", "ts_code": code,
        "promotion_rank": rank, "board_stage": stage, "feature_as_of_date": "20260914",
        "promotion_oof_train_end": "20260911", "path_label": None, "existing_profit_rank": None}


def predict(model=None, rows=None, **kwargs):
    model = synthetic_model() if model is None else model
    rows = [row(), row("600001.SH", 2, 3)] if rows is None else rows
    return m.predict_forward(model, rows, expected_model_sha256=kwargs.pop("expected_model_sha256", m.canonical_sha(model)),
        signal_date=kwargs.pop("signal_date", "20260914"), **kwargs)


def test_exact_frozen_constants_and_static_real_serialized_model_field_set():
    assert m.MODEL_SPEC == old.MODEL_SPEC == candidate_v3.MODEL_SPEC
    assert m.NUMERIC_FEATURES == old.NUMERIC_FEATURES and m.PATHS == old.PATHS and m.FEATURES == old.FEATURES
    assert len(m.FEATURES) == 29 and m.OVERLAY_CONTRACT == overlay.CONTRACT
    assert m.canonical_sha(m.OVERLAY_CONTRACT) == candidate_v3.canonical_sha(overlay.CONTRACT)
    tree = ast.parse(Path(old.__file__).read_text())
    fit = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_fit_ridge")
    model_dict = next(n.value for n in ast.walk(fit) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "model" for t in n.targets))
    tree = ast.parse(Path(candidate_v3.__file__).read_text())
    metadata = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "metadata" for t in n.targets))
    assert m.MODEL_KEYS == {k.value for k in model_dict.keys} | {k.value for k in metadata.keys}
    assert set(synthetic_model()) == m.MODEL_KEYS


def test_hand_computed_imputation_scaling_coefficients_and_intercept():
    model = synthetic_model()
    model["imputation_train_medians"][3] = 6.
    model["scaling_train_means"][3] = 2.
    model["scaling_train_scales"][3] = 2.
    model["coefficients"][1] = -.2
    model["coefficients"][3] = .5
    model["coefficients"][20:22] = [.1, -.1]
    model["intercept"] = -.75
    rows = [row(), {**row("600001.SH", 2, 3), "d_pct_change": 2.}]
    before = deepcopy((model, rows))
    result = predict(model, rows)
    assert [x["candidate_score"] for x in result["rows"]] == pytest.approx([.15, -1.25])
    assert [x["ts_code"] for x in result["rows"]] == ["600002.SH", "600001.SH"]
    assert (model, rows) == before and result["fees_subtracted_again"] is False
    assert all(s["net_return"] is None for s in result["slots"].values())


@pytest.mark.parametrize("stage", [2, 3])
@pytest.mark.parametrize("nullable", [True, False])
def test_feature_encoding_exactly_matches_old_feature_row(stage, nullable):
    original = row(stage=stage)
    if not nullable:
        for f in m.NUMERIC_FEATURES:
            if f not in m.MISSING_SIGNALS and f != "promotion_rank": original[f] = .123
    selected = m._project([original], "20260914")[0]
    assert m._feature_row(selected) == old._feature_row(selected)


def test_negative_scores_all_retained_and_ties_use_code_not_promotion_rank():
    result = predict(rows=[row("600003.SH", 1), row("600001.SH", 3), row("600002.SH", 2)])
    assert [r["ts_code"] for r in result["rows"]] == ["600001.SH", "600002.SH", "600003.SH"]
    assert [r["promotion_rank"] for r in result["rows"]] == [3, 2, 1]
    assert [r["candidate_rank"] for r in result["rows"]] == [1, 2, 3]
    assert all(r["candidate_score"] == -1 for r in result["rows"])
    assert result["slots"]["top1"]["ts_code"] == "600001.SH" and result["slots"]["top2"]["ts_code"] == "600002.SH"
    assert result["negative_score_skip_allowed"] is False


def test_ranking_lambda_is_ast_identical_to_frozen_candidate_not_new_tie_policy():
    tree = ast.parse(Path(old.__file__).read_text())
    compare = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_compare")
    sorters = next(n.value for n in ast.walk(compare) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "sorters" for t in n.targets))
    fixed = next(value for key, value in zip(sorters.keys, sorters.values) if key.value == "candidate")
    ours = ast.parse(Path(m.__file__).read_text())
    predict_fn = next(n for n in ours.body if isinstance(n, ast.FunctionDef) and n.name == "predict_forward")
    keys = [n for n in ast.walk(predict_fn) if isinstance(n, ast.Lambda)]
    assert len(keys) == 1
    assert ast.dump(keys[0], include_attributes=False) == ast.dump(fixed, include_attributes=False)


def test_unknown_model_field_is_rejected_before_serialization_or_scoring(monkeypatch):
    model = synthetic_model(); model["unregistered_outcome"] = object()
    monkeypatch.setattr(m, "canonical_sha", lambda value: (_ for _ in ()).throw(AssertionError("UNREGISTERED_MODEL_HASH")))
    with pytest.raises(ValueError, match="MODEL_KEYS"):
        m.predict_forward(model, [row()], expected_model_sha256="a"*64, signal_date="20260914")


@pytest.mark.parametrize("count", [0, 1, 10])
def test_zero_to_ten_complete_rows_two_explicit_slots_no_replacement(count):
    result = predict(rows=[row(f"{600001+i}.SH", i+1) for i in range(count)])
    assert len(result["rows"]) == count and result["candidate_count"] == count
    assert set(result["slots"]) == {"top1", "top2"}
    for i, slot in enumerate(result["slots"].values()):
        if i >= count:
            assert slot == {"status": "MISSING_CANDIDATE", "ts_code": None, "candidate_rank": None,
                "candidate_score": None, "promotion_rank": None, "net_return": None}
    assert result["complete_D_universe_independently_verified"] is False


@pytest.mark.parametrize("field,value", [
    ("schema_version", "dc20_profit_1000_candidate_v1"), ("research_only", False),
    ("production_activation_allowed", True), ("entry_policy_id", "daily_open_cap"),
    ("label_policy_id", "old_open_exit"), ("round_trip_cost_rate", .009),
    ("round_trip_cost_rate", True), ("frozen_source_sha256", "0"*64),
    ("provider_timestamp_semantics_confirmed", True), ("training_cutoff_date", "20251112"),
    ("holdout_start_date", "20260915"), ("fit_signal_date_min", "20221110"),
    ("fit_signal_date_max", "20251111"), ("fit_label_available_date_max", "20251111"),
    ("fit_label_available_date_max", "20251107"), ("fit_signal_date_max", "20221301"),
    ("sklearn_version", ""), ("numpy_version", {}), ("features", list(reversed(m.FEATURES))),
])
def test_model_metadata_and_temporal_contract_cannot_change(field, value):
    model = synthetic_model(); model[field] = value
    with pytest.raises(ValueError): predict(model)


@pytest.mark.parametrize("field", ["alpha", "fit_intercept", "solver", "target", "hyperparameter_search"])
def test_no_alternate_model_specification(field):
    model = synthetic_model(); model["specification"][field] = "changed"
    with pytest.raises(ValueError, match="SPECIFICATION"): predict(model)


@pytest.mark.parametrize("field", ["imputation_train_medians", "scaling_train_means", "scaling_train_scales", "coefficients"])
@pytest.mark.parametrize("value", [[], [0.] * 28, [0.] * 30, [True] * 29, [float("inf")] * 29, (0.,) * 29])
def test_parameter_vectors_exact_finite_29(field, value):
    model = synthetic_model(); model[field] = value
    with pytest.raises(ValueError): m.predict_forward(model, [row()], expected_model_sha256="a"*64, signal_date="20260914")


@pytest.mark.parametrize("value", [0., -1., True, None, float("nan")])
def test_scale_strictly_positive(value):
    model = synthetic_model(); model["scaling_train_scales"][0] = value
    with pytest.raises(ValueError): m.predict_forward(model, [row()], expected_model_sha256="a"*64, signal_date="20260914")


@pytest.mark.parametrize("value", [True, None, "1", float("inf"), float("nan")])
def test_intercept_finite_exact_number(value):
    model = synthetic_model(); model["intercept"] = value
    with pytest.raises(ValueError): m.predict_forward(model, [row()], expected_model_sha256="a"*64, signal_date="20260914")


@pytest.mark.parametrize("change", ["missing", "extra", "nested_extra", "source_binding", "source_binding_extra", "missing_binding", "bad_sha"])
def test_model_exact_schema_overlay_and_sha(change):
    model = synthetic_model()
    if change == "missing": model.pop("intercept")
    if change == "extra": model["source_verified"] = True
    if change == "nested_extra": model["source_overlay_contract"]["activate"] = True
    if change == "source_binding": model["input_bindings"]["source_sha256"] = "0" * 64
    if change == "source_binding_extra": model["input_bindings"]["qualified"] = True
    if change == "missing_binding": model["input_bindings"].pop("market_collection_receipt_sha256")
    with pytest.raises(ValueError): predict(model, expected_model_sha256="a"*64)


def test_explicit_base_only_model_binding_does_not_become_new_source_authority():
    model = synthetic_model(); model["input_bindings"]["market_collection_receipt_sha256"] = None
    result = predict(model)
    for flag in ("model_source_independently_verified", "D_source_independently_verified", "frozen_selection_issued",
            "production_activation_allowed", "natural_forward_ledger_written", "holdout_performance_evaluated"):
        assert result[flag] is False


@pytest.mark.parametrize("field,value", [
    ("signal_date", "20260915"), ("ts_code", "600000"), ("board_stage", 4), ("board_stage", True),
    ("promotion_rank", 0), ("promotion_rank", 2), ("promotion_rank", True), ("promotion_rank", 1.0),
    ("feature_as_of_date", "20260913"), ("feature_as_of_date", "20260915"),
    ("promotion_oof_train_end", "20260914"), ("promotion_oof_train_end", "20260915"),
    ("d_pct_change", True), ("d_pct_change", "3.2"), ("volume_ratio", float("nan")),
])
def test_forward_row_identity_causality_and_finite_whitelist(field, value):
    supplied = row(); supplied[field] = value
    with pytest.raises(ValueError): predict(rows=[supplied])


@pytest.mark.parametrize("field", m.MISSING_SIGNALS)
@pytest.mark.parametrize("mode", ["missing", "populated"])
def test_missing_historical_signals_never_newly_filled(field, mode):
    supplied = row()
    if mode == "missing": supplied.pop(field)
    else: supplied[field] = "弱转强" if field == "path_label" else .5
    with pytest.raises(ValueError, match="MUST_REMAIN_NONE"): predict(rows=[supplied])


def test_all_identity_pass_precedes_features_and_duplicate_rank_is_rejected():
    class Trap:
        def __float__(self): raise AssertionError("FEATURE_READ_BEFORE_IDENTITY")
    supplied = row(); supplied["d_pct_change"] = Trap()
    with pytest.raises(ValueError, match="REQUESTED_D"):
        predict(rows=[supplied, {**row("600003.SH", 2), "signal_date": "20260915"}])
    with pytest.raises(ValueError, match="DUPLICATE"): predict(rows=[row(), row(rank=2)])
    with pytest.raises(ValueError, match="RANKS"): predict(rows=[row(), row("600003.SH", 1)])
    with pytest.raises(ValueError, match="ZERO_TO_TEN"): predict(rows=[row()] * 11)


def test_unknown_future_outcome_and_T_price_values_never_read_copied_or_hashed():
    class Trap:
        def __repr__(self): raise AssertionError("UNKNOWN_REPR")
        def __float__(self): raise AssertionError("UNKNOWN_FLOAT")
        def __iter__(self): raise AssertionError("UNKNOWN_ITERATION")
        def __deepcopy__(self, memo): raise AssertionError("UNKNOWN_COPY")
        def __eq__(self, other): raise AssertionError("UNKNOWN_COMPARE")
    clean = [row()]
    poisoned = [{**row(), **{key: Trap() for key in ("entry_price", "t_close", "net_return", "slot_net_return",
        "label_status", "future_outcomes", "T_auction_price", "model_profit_after_D")}}]
    assert predict(rows=clean) == predict(rows=poisoned)


@pytest.mark.parametrize("day", ["20260913", "20260814", "20261301", True, None])
def test_history_or_invalid_D_rejected(day):
    with pytest.raises(ValueError): predict(signal_date=day)


def test_arithmetic_overflow_is_rejected_not_ranked_as_infinite():
    model = synthetic_model(); model["coefficients"][3] = 1e308
    supplied = row(); supplied["d_pct_change"] = 1e308
    with pytest.raises(ValueError, match="FINITE"): predict(model, [supplied])


@pytest.mark.parametrize("change", ["model", "feature", "future_identity"])
def test_end_guard_detects_input_mutation_without_reading_unknown_values(change, monkeypatch):
    model, rows = synthetic_model(), [row()]
    original = m._feature_row
    def mutate(selected):
        values = original(selected)
        if change == "model": model["intercept"] = 99.
        if change == "feature": rows[0]["d_pct_change"] = 99.
        if change == "future_identity": rows[0]["signal_date"] = "20260915"
        return values
    monkeypatch.setattr(m, "_feature_row", mutate)
    with pytest.raises(ValueError): predict(model, rows)


def test_pure_runtime_never_fits_imports_ml_or_uses_file_or_network(monkeypatch):
    model, rows = synthetic_model(), [row()]
    expected = m.canonical_sha(model)
    def forbidden(*a, **kw): raise AssertionError("NO_FIT_FILE_OR_NETWORK")
    monkeypatch.setattr(old, "_fit_ridge", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    original_import = builtins.__import__
    def guarded_import(name, *a, **kw):
        if name.split(".")[0] in {"sklearn", "numpy"}: forbidden()
        return original_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = m.predict_forward(model, rows, expected_model_sha256=expected, signal_date="20260914")
    assert result["training_performed"] is False and result["files_written"] == result["network_calls_performed"] == 0
    assert result["future_outcomes_read"] is False


def test_no_fit_policy_gate_or_rank_override_keywords():
    with pytest.raises(TypeError): predict(alpha=1.)
    with pytest.raises(TypeError): predict(rank_by="promotion_rank")
    with pytest.raises(TypeError): predict(source_verified=True)
