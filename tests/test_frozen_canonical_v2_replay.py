from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "replay_frozen_canonical_v2.py"
SPEC = importlib.util.spec_from_file_location("replay_frozen_canonical_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def _inactive_freeze(tmp_path: Path) -> tuple[Path, str]:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260804", "20260805"],
            "buy_date": ["20260805", "20260806"],
            "target_exit_date": ["20260806", "20260807"],
            "ts_code": ["000001.SZ", "600000.SH"],
            "net_return": [0.01, -0.02],
        }
    )
    snapshot = tmp_path / "models" / "frozen.csv.gz"
    snapshot.parent.mkdir(parents=True)
    frame.to_csv(
        snapshot,
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "decision_model_freeze_v1",
        "active": False,
        "freeze_id": "inactive-maintenance-window",
        "training_cutoff_signal_date": "20260805",
        "production": {},
        "pinned_files": {},
        "history_snapshot": {
            "path": "models/frozen.csv.gz",
            "sha256": sha,
            "bootstrap_mode": False,
        },
    }
    manifest_path = tmp_path / "models" / "decision_model_freeze.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, sha


def test_inactive_manifest_forces_exact_snapshot_without_mutating_disk(
    tmp_path: Path,
) -> None:
    manifest_path, sha = _inactive_freeze(tmp_path)
    before = manifest_path.read_bytes()
    history, forced, audit = replay.load_forced_frozen_history(
        tmp_path,
        expected_sha256=sha,
        expected_rows=2,
        expected_dates=2,
    )
    assert forced["active"] is True
    assert json.loads(before)["active"] is False
    assert manifest_path.read_bytes() == before
    assert history["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert audit["sha256"] == sha
    assert audit["source"] == "frozen_snapshot"
    assert audit["forced_frozen_replay"] is True
    assert audit["v1_runtime_gate"] == "intentionally_not_run_diagnostic_only"


def test_forced_replay_rejects_unexpected_or_drifted_snapshot(tmp_path: Path) -> None:
    manifest_path, sha = _inactive_freeze(tmp_path)
    with pytest.raises(RuntimeError, match="unexpected frozen snapshot SHA"):
        replay.load_forced_frozen_history(
            tmp_path,
            expected_sha256="0" * 64,
            expected_rows=2,
            expected_dates=2,
        )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["history_snapshot"]["sha256"] = "1" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unexpected frozen snapshot SHA"):
        replay.load_forced_frozen_history(
            tmp_path,
            expected_sha256=sha,
            expected_rows=2,
            expected_dates=2,
        )


def test_candidate_source_evidence_covers_full_executed_action_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_paths = {
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/trade_selector.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/action_plan.py",
        "scripts/publish_decision_action.py",
        "scripts/replay_frozen_canonical_v2.py",
    }
    for index, relative in enumerate(sorted(expected_paths), start=1):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source-{index}\n", encoding="utf-8")
    monkeypatch.setattr(
        replay.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="a" * 40 + "\n",
        ),
    )

    before = replay.candidate_source_evidence(tmp_path)

    assert before["base_commit"] == "a" * 40
    assert set(before["file_sha256"]) == expected_paths
    assert all(
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(set("0123456789abcdef"))
        for value in before["file_sha256"].values()
    )

    action_path = tmp_path / "src/top10decision/decision/action_plan.py"
    action_path.write_text("mutated-action-source\n", encoding="utf-8")
    after = replay.candidate_source_evidence(tmp_path)
    assert (
        before["file_sha256"]["src/top10decision/decision/action_plan.py"]
        != after["file_sha256"]["src/top10decision/decision/action_plan.py"]
    )
    for relative in expected_paths - {
        "src/top10decision/decision/action_plan.py"
    }:
        assert before["file_sha256"][relative] == after["file_sha256"][relative]


def test_identity_comparison_rejects_ambiguous_duplicate_rows() -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805", "20260805"],
            "ts_code": ["000001.SZ", "000001.SZ"],
        }
    )
    with pytest.raises(RuntimeError, match="ambiguous duplicate identities"):
        replay._identity_index(frame, label="candidate")


def test_same_machine_raw_behavior_requires_exact_identity_gate_and_gap() -> None:
    reference = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [1],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [0.035],
        }
    )
    result = replay._compare_behavior(
        reference,
        reference.copy(),
        label="same-machine raw",
        columns=("stage",),
    )
    assert result["identity_equal"] is True
    assert all(value == 0 for value in result["changed_rows"].values())

    changed = reference.copy()
    changed.loc[0, "diagnostic_gap"] = 0.04
    changed.loc[0, "recommended_max_gap"] = 0.04
    with pytest.raises(RuntimeError, match="behavior changed in diagnostic_gap"):
        replay._compare_behavior(
            reference,
            changed,
            label="same-machine raw",
            columns=("stage",),
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("stage", None, "missing/empty stage"),
        ("risk_gate_pass", None, "missing risk_gate_pass"),
        ("diagnostic_gap", float("nan"), "missing diagnostic_gap"),
        ("diagnostic_gap", float("inf"), "non-finite diagnostic_gap"),
    ],
)
def test_behavior_schema_rejects_same_invalid_value_on_both_sides(
    column: str,
    value,
    message: str,
) -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match=message):
        replay._compare_behavior(
            frame,
            frame.copy(),
            label="invalid same-side",
            columns=("stage", "risk_gate_pass"),
        )


def test_recommended_gap_presence_must_exactly_follow_risk_gate() -> None:
    rejected = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    result = replay._compare_behavior(
        rejected,
        rejected.copy(),
        label="legal rejected gap",
        columns=("stage", "risk_gate_pass"),
    )
    assert result["candidate_schema"]["recommended_max_gap_missing"] == 1

    illegal = rejected.copy()
    illegal.loc[0, "recommended_max_gap"] = 0.035
    with pytest.raises(RuntimeError, match="missing-state disagrees"):
        replay._compare_behavior(
            illegal,
            illegal.copy(),
            label="illegal rejected gap",
            columns=("stage", "risk_gate_pass"),
        )

    passed = rejected.copy()
    passed.loc[0, "risk_gate_pass"] = 1
    passed.loc[0, "recommended_max_gap"] = 0.04
    with pytest.raises(RuntimeError, match="differs from diagnostic_gap"):
        replay._compare_behavior(
            passed,
            passed.copy(),
            label="illegal passed gap",
            columns=("stage", "risk_gate_pass"),
        )


def test_canonical_score_comparison_gates_at_eight_and_audits_higher_precision() -> None:
    reference = pd.DataFrame(
        {
            "signal_date": ["20260805", "20260806"],
            "ts_code": ["000001.SZ", "600000.SH"],
            "selection_score": [0.1234567801, 0.2],
        }
    )
    candidate = reference.copy()
    candidate.loc[0, "selection_score"] = 0.1234567805
    report = replay._canonical_score_comparison(
        reference,
        candidate,
        label="model",
        columns=("selection_score",),
    )
    assert report["8"]["equal"] is True
    assert report["8"]["gate"] == "hard"
    assert report["6"]["gate"] == "audit_only"
    assert report["10"]["gate"] == "audit_only"
    assert report["10"]["equal"] is False

    candidate.loc[0, "selection_score"] = 0.12345680
    with pytest.raises(RuntimeError, match="8-decimal scores drifted"):
        replay._canonical_score_comparison(
            reference,
            candidate,
            label="model",
            columns=("selection_score",),
        )


def test_canonical_score_comparison_rejects_shared_missing_or_nonfinite_score() -> None:
    for value, message in (
        (float("nan"), "missing selection_score"),
        (float("inf"), "non-finite selection_score"),
        ("bad", "invalid numeric selection_score"),
    ):
        frame = pd.DataFrame(
            {
                "signal_date": ["20260805"],
                "ts_code": ["000001.SZ"],
                "selection_score": [value],
            }
        )
        with pytest.raises(RuntimeError, match=message):
            replay._canonical_score_comparison(
                frame,
                frame.copy(),
                label="invalid score",
                columns=("selection_score",),
            )


def _prediction_policy_fixture() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    rows = 51
    domain_rows = 9
    domain = pd.Series([1] * domain_rows + [0] * (rows - domain_rows))
    model_projection = {
        "version": "model-policy-v1",
        "ready": True,
        "reason": "ready",
        "max_positions": 2,
        "thresholds": {
            "max_big_loss_probability": 0.2,
            "min_mean_return_lcb": 0.0,
            "min_fill_probability": 0.5,
            "min_exit_probability": 0.8,
            "min_conservative_ev": 0.0,
            "min_selection_score": -0.023437114100227405,
        },
    }
    selector_projection = {
        "version": "selector-policy-v1",
        "ready": True,
        "reason": "ready",
        "max_positions": 2,
        "tail_risk_weight": 0.25,
        "thresholds": {
            "min_trade_score": 0.1,
            "min_mean_return_lcb": 0.0,
            "min_fill_probability": 0.5,
            "max_big_loss_probability": 0.2,
        },
    }
    frame = pd.DataFrame(
        {
            "signal_date": ["20260814"] * rows,
            "ts_code": [f"{index:06d}.SZ" for index in range(rows)],
            "observation_selected": domain,
            "predicted_big_loss_probability": [0.1] * rows,
            "predicted_mean_return_lcb": [0.1] * rows,
            "predicted_fill_probability": [0.8] * rows,
            "predicted_public_market_buyable_probability": [0.8] * rows,
            "predicted_actual_order_fill_probability": [float("nan")] * rows,
            "actual_order_fill_probability_available": [0] * rows,
            "predicted_exit_probability": [0.9] * rows,
            "conservative_ev": [0.1] * rows,
            "selection_score": [0.2] * rows,
            "stage_focus": [1] * rows,
            "gate_policy_ready": [1] * rows,
            "gate_stage_focus": [1] * rows,
            "gate_exit_probability": [1] * rows,
            "gate_fill_probability": [1] * rows,
            "gate_big_loss_probability": [1] * rows,
            "gate_mean_return_lcb": [1] * rows,
            "gate_conservative_ev": [1] * rows,
            "gate_selection_score": [1] * rows,
            "risk_gate_pass": [1] * rows,
            "trade_gate_pass": [1, 1] + [0] * (rows - 2),
            "trade_shadow_selected": [0] * rows,
            "trade_selected": [0] * rows,
            "trade_selector_policy_ready": [1] * domain_rows
            + [0] * (rows - domain_rows),
            "trade_selector_promoted": [0] * rows,
            "trade_model_reason": ["learned_policy_pass"] * domain_rows
            + ["outside_observation_top10"] * (rows - domain_rows),
        }
    )
    for name, column in {
        "max_big_loss_probability": "policy_max_big_loss_probability",
        "min_mean_return_lcb": "policy_min_mean_return_lcb",
        "min_fill_probability": "policy_min_fill_probability",
        "min_exit_probability": "policy_min_exit_probability",
        "min_conservative_ev": "policy_min_conservative_ev",
        "min_selection_score": "policy_min_selection_score",
    }.items():
        frame[column] = model_projection["thresholds"][name]
    domain_values = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    for column in replay.SELECTOR_PREDICTION_SCORE_COLUMNS:
        frame[column] = domain_values
    frame["trade_score"] = [0.2] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_mean_return_lcb"] = [0.1] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_fill_probability"] = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_public_market_buyable_probability"] = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_big_loss_probability"] = [0.1] * domain_rows + [float("nan")] * (rows - domain_rows)
    for column in replay.SELECTOR_PREDICTION_RANK_COLUMNS:
        frame[column] = list(range(1, domain_rows + 1)) + [float("nan")] * (
            rows - domain_rows
        )
    for column, value in (
        ("trade_selector_artifact_sha256", "a" * 64),
        ("trade_selector_artifact_v2_sha256", "b" * 64),
    ):
        frame[column] = [value] * domain_rows + [None] * (rows - domain_rows)
    text = frame.map(lambda value: "" if pd.isna(value) else str(value))
    return frame, text, model_projection, selector_projection


def test_prediction_policy_exact_decimal_surface_and_51_9_domain_pass() -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    result = replay.validate_prediction_policy_execution(
        frame,
        prediction_text=text,
        model_projection=model,
        selector_projection=selector,
    )
    assert result["rows"] == 51
    assert result["selector_domain_rows"] == 9
    assert result["selector_outside_rows"] == 42
    assert result["policy_threshold_text_surface"]["exact_decimal_match"] is True
    assert result["selector_trade_gate_pass_rows"] == 2
    assert result["fill_contract"]["selector_alias_equal_rows"] == 9


@pytest.mark.parametrize(
    "raw",
    (
        "-0.0234371141002274",
        "-0.023437094100227405",
        "NaN",
        "Inf",
        "",
        "malformed",
    ),
)
def test_prediction_policy_text_surface_rejects_any_decimal_drift(raw: str) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    text.loc[0, "policy_min_selection_score"] = raw
    with pytest.raises(RuntimeError, match="policy text surface|differs from model"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_projection=model,
            selector_projection=selector,
        )


def test_exact_text_reader_rejects_duplicate_policy_header(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    path.write_text(
        "policy_min_selection_score,policy_min_selection_score\n0.1,0.1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="duplicate header"):
        replay._read_csv_exact_text(path)


def test_prediction_policy_text_surface_requires_same_row_count() -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    with pytest.raises(RuntimeError, match="row count differs"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text.iloc[:-1].copy(),
            model_projection=model,
            selector_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("gate_policy_ready", 0.5),
        ("risk_gate_pass", 2),
        ("stage_focus", "true"),
        ("observation_selected", 0.5),
        ("trade_gate_pass", 2),
        ("trade_shadow_selected", 0.5),
        ("trade_selected", 2),
        ("trade_selector_policy_ready", "true"),
        ("trade_selector_promoted", 0.5),
    ),
)
def test_prediction_policy_rejects_nonbinary_aliases(column: str, value) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match="non-binary|non-integral|invalid numeric"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_projection=model,
            selector_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "row", "value"),
    (
        ("trade_score", 9, 0.1),
        ("trade_selector_artifact_v2_sha256", 9, "c" * 64),
        ("trade_gate_pass", 9, 1),
        ("trade_model_reason", 9, "learned_policy_pass"),
        ("trade_rank", 0, float("nan")),
        ("promotion_rank", 1, 1),
    ),
)
def test_prediction_selector_domain_contract_rejects_mixed_domain(
    column: str,
    row: int,
    value,
) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    frame.loc[row, column] = value
    with pytest.raises(RuntimeError, match="selector domain"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_projection=model,
            selector_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "row", "value"),
    (
        ("predicted_public_market_buyable_probability", 0, 0.7),
        ("predicted_fill_probability", 0, 1.1),
        ("trade_predicted_public_market_buyable_probability", 0, 0.7),
        ("trade_predicted_fill_probability", 9, 0.8),
        ("predicted_actual_order_fill_probability", 0, 0.5),
        ("actual_order_fill_probability_available", 0, 0.5),
        ("actual_order_fill_probability_available", 0, 1),
        ("predicted_actual_order_fill_probability", 0, float("inf")),
        ("predicted_fill_probability", 0, float("nan")),
    ),
)
def test_prediction_fill_contract_rejects_alias_range_or_availability_drift(
    column: str,
    row: int,
    value,
) -> None:
    frame, _, _, _ = _prediction_policy_fixture()
    frame[column] = frame[column].astype(object)
    frame.loc[row, column] = value
    with pytest.raises(RuntimeError, match="fill contract|selector domain"):
        replay.validate_prediction_fill_contract(frame)


def test_prediction_fill_contract_accepts_available_actual_probability() -> None:
    frame, _, _, _ = _prediction_policy_fixture()
    frame.loc[0, "predicted_actual_order_fill_probability"] = 0.6
    frame.loc[0, "actual_order_fill_probability_available"] = 1
    result = replay.validate_prediction_fill_contract(frame)
    assert result["actual_probability_available_rows"] == 1
    assert result["actual_probability_missing_rows"] == 50


def _action_plan_fixture() -> dict:
    return {
        "signal_date": "20260805",
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "formal_buy_count": 0,
        "stage_watchlist": [
            {
                "ts_code": "000001.SZ",
                "stage_watch_rank": 1,
                "trade_rank": 1,
                "trade_shadow_selected": 1,
                "watch_label": "二筛影子",
                "action": "SHADOW_ONLY",
                "target_weight": 0.0,
            },
            {
                "ts_code": "600000.SH",
                "stage_watch_rank": 2,
                "trade_rank": 2,
                "trade_shadow_selected": 1,
                "watch_label": "二筛影子",
                "action": "SHADOW_ONLY",
                "target_weight": 0.0,
            },
            {
                "ts_code": "000002.SZ",
                "stage_watch_rank": 3,
                "trade_rank": 3,
                "trade_shadow_selected": 0,
                "watch_label": "仅观察",
                "action": "REJECT",
                "target_weight": 0.0,
            },
        ],
    }


def test_action_plan_candidate_projection_hashes_discrete_and_weight() -> None:
    base = _action_plan_fixture()
    original = replay._action_candidate_contract(base)
    changed = _action_plan_fixture()
    changed["stage_watchlist"][2]["stage_watch_rank"] = 4
    mutated = replay._action_candidate_contract(changed)
    assert (
        mutated["action_plan_candidates_sha256_q8"]
        != original["action_plan_candidates_sha256_q8"]
    )
    for replacement in (" 仅观察 ", "ＷＡＴＣＨ"):
        exact_changed = _action_plan_fixture()
        exact_changed["stage_watchlist"][2]["watch_label"] = replacement
        with pytest.raises(RuntimeError, match="shadow contract drifted"):
            replay._action_candidate_contract(exact_changed)

    invalid_action = _action_plan_fixture()
    invalid_action["stage_watchlist"][0]["action"] = "BUY"
    with pytest.raises(RuntimeError, match="action is invalid"):
        replay._action_candidate_contract(invalid_action)

    invalid_weight = _action_plan_fixture()
    invalid_weight["stage_watchlist"][0]["target_weight"] = 0.01
    with pytest.raises(RuntimeError, match="target_weight is nonzero"):
        replay._action_candidate_contract(invalid_weight)


def test_action_plan_candidate_requires_exact_relative_best_two() -> None:
    missing = _action_plan_fixture()
    missing["stage_watchlist"][1]["action"] = "REJECT"
    missing["stage_watchlist"][1]["watch_label"] = "仅观察"
    missing["stage_watchlist"][1]["trade_shadow_selected"] = 0
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(missing)

    extra = _action_plan_fixture()
    extra["stage_watchlist"][2]["action"] = "SHADOW_ONLY"
    extra["stage_watchlist"][2]["watch_label"] = "二筛影子"
    extra["stage_watchlist"][2]["trade_shadow_selected"] = 1
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(extra)

    wrong = _action_plan_fixture()
    for index in (0, 2):
        is_shadow = index == 2
        wrong["stage_watchlist"][index]["action"] = (
            "SHADOW_ONLY" if is_shadow else "REJECT"
        )
        wrong["stage_watchlist"][index]["watch_label"] = (
            "二筛影子" if is_shadow else "仅观察"
        )
        wrong["stage_watchlist"][index]["trade_shadow_selected"] = int(
            is_shadow
        )
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(wrong)


def test_action_plan_candidate_projection_rejects_duplicate_or_empty_identity() -> None:
    duplicate = _action_plan_fixture()
    duplicate["stage_watchlist"][1]["ts_code"] = "000001.SZ"
    with pytest.raises(RuntimeError, match="ambiguous duplicate identities"):
        replay._action_candidate_contract(duplicate)
    empty = _action_plan_fixture()
    empty["stage_watchlist"][0]["ts_code"] = ""
    with pytest.raises(RuntimeError, match="empty ts_code"):
        replay._action_candidate_contract(empty)


def _fingerprint_integrity_fixture() -> tuple[dict, dict, dict, dict]:
    model_policy = {
        "version": "nested_temporal_utility_v1",
        "ready": False,
        "reason": "no_policy_passed_independent_holdout",
        "max_positions": 2,
        "thresholds": {
            "max_big_loss_probability": 0.4,
            "min_mean_return_lcb": -0.03,
            "min_fill_probability": 0.1,
            "min_exit_probability": 0.9,
            "min_conservative_ev": -0.01,
            "min_selection_score": 0.02,
        },
    }
    selector_policy = {
        "version": "trade_selector_v2_nested_oos_top10_promotion_rank",
        "ready": False,
        "reason": "best_shadow_policy_failed_profit_or_coverage_gate",
        "max_positions": 2,
        "tail_risk_weight": 0.75,
        "thresholds": {
            "min_trade_score": -0.02,
            "min_mean_return_lcb": -0.03,
            "min_fill_probability": 0.1,
            "max_big_loss_probability": 0.5,
        },
    }
    model_projection = replay._model_executable_policy_projection(model_policy)
    selector_projection = replay._selector_executable_policy_projection(
        selector_policy
    )
    model_policy_sha = replay.canonical_mapping_sha256(
        {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "artifact_kind": "decision_model_executable_policy",
            "projection": model_projection,
        },
        decimals=8,
        exact_strings=True,
    )
    selector_policy_sha = replay.canonical_policy_fingerprint(
        selector_projection,
        decimals=8,
    )["sha256"]
    model = {
        "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": replay.MODEL_CANONICAL_V2,
        "canonical_contract": {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "layer": "model",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        },
        "policy_projection": model_projection,
        "policy_sha256": model_policy_sha,
        "provenance_sha256": "a" * 64,
        "semantic_sha256": "b" * 64,
        "schema_valid": True,
        "missing_columns": [],
        "invalid_cell_count": 0,
    }
    model["artifact_sha256"] = replay.compose_artifact_fingerprint(
        artifact_kind="decision_model_canonical_runtime_v2",
        provenance_sha256=model["provenance_sha256"],
        semantic_sha256=model["semantic_sha256"],
        policy_sha256=model_policy_sha,
        decimals=8,
    )
    selector = {
        "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": replay.TRADE_SELECTOR_CANONICAL_V2,
        "canonical_contract": {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "layer": "trade_selector",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        },
        "policy_projection": selector_projection,
        "policy_sha256": selector_policy_sha,
        "provenance_sha256": "c" * 64,
        "semantic_sha256": "d" * 64,
        "schema_valid": True,
        "missing_columns": [],
        "invalid_cell_count": 0,
    }
    selector["artifact_sha256"] = replay.compose_artifact_fingerprint(
        artifact_kind="decision_trade_selector_canonical_runtime_v2",
        provenance_sha256=selector["provenance_sha256"],
        semantic_sha256=selector["semantic_sha256"],
        policy_sha256=selector_policy_sha,
        decimals=8,
    )
    return model, selector, model_policy, selector_policy


def test_fingerprint_integrity_recomputes_live_policy_and_artifact() -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    report = replay.validate_fingerprint_integrity(
        model,
        selector,
        live_model_policy=model_policy,
        live_selector_policy=selector_policy,
    )
    assert report["live_policies_match_fingerprint_projection"] is True

    stale_model_policy = json.loads(json.dumps(model_policy))
    stale_model_policy["thresholds"]["min_selection_score"] += 0.00000002
    with pytest.raises(RuntimeError, match="live model selection_policy differs"):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=stale_model_policy,
            live_selector_policy=selector_policy,
        )

    stale_selector_policy = json.loads(json.dumps(selector_policy))
    stale_selector_policy["thresholds"]["min_trade_score"] += 0.00000002
    with pytest.raises(RuntimeError, match="live selector production_policy differs"):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=stale_selector_policy,
        )

    stale_fingerprint = dict(model)
    stale_fingerprint["policy_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="policy SHA does not recompute"):
        replay.validate_fingerprint_integrity(
            stale_fingerprint,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("unknown", 1), "envelope keys drifted"),
        (lambda value: value.pop("semantic_sha256"), "envelope keys drifted"),
        (lambda value: value.__setitem__("schema", "wrong"), "schema drifted"),
        (
            lambda value: value.__setitem__("provenance_sha256", "bad"),
            "provenance_sha256 invalid",
        ),
        (lambda value: value.__setitem__("schema_valid", False), "schema is not valid"),
        (
            lambda value: value.__setitem__("missing_columns", ["gross_return"]),
            "semantic columns missing",
        ),
        (
            lambda value: value.__setitem__("invalid_cell_count", 1),
            "semantic invalid cells present",
        ),
    ],
)
def test_fingerprint_integrity_rejects_non_strict_envelope(
    mutation,
    message: str,
) -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    mutation(model)
    with pytest.raises(RuntimeError, match=message):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


def test_golden_path_cannot_leave_integrity_validator_unused() -> None:
    source = inspect.getsource(replay.compare_frozen_golden)
    assert "validate_fingerprint_integrity(" in source
    assert "validate_prediction_policy_execution(" in source


def test_reference_profile_rejects_mutable_files_unless_explicit_same_machine(
    tmp_path: Path,
) -> None:
    for name in replay.REFERENCE_C6_GIT_BLOBS:
        (tmp_path / name).write_text(f"local {name}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="untrusted persisted-c6 reference blob"):
        replay.validate_reference_snapshot(tmp_path, profile="persisted-c6")
    report = replay.validate_reference_snapshot(
        tmp_path,
        profile="same-machine-c6",
    )
    assert report["persisted_trust_root_verified"] is False
    assert report["same_machine_reference_only"] is True


def test_behavior_contract_hashes_text_and_stage_exactly() -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "model_reason": ["selection_policy_not_ready"],
            "observation_risk_label": ["HIGH_RISK"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    discrete = (
        "stage",
        "model_reason",
        "observation_risk_label",
        "risk_gate_pass",
    )
    original = replay._candidate_frame_contract(
        frame,
        label="exact behavior",
        discrete_columns=discrete,
        score_columns=("diagnostic_gap", "recommended_max_gap"),
    )["discrete_sha256"]
    for column, replacement in (
        ("stage", "2->3"),
        ("model_reason", " selection_policy_not_ready "),
        ("observation_risk_label", "ＨＩＧＨ＿ＲＩＳＫ"),
    ):
        changed = frame.copy()
        changed.loc[0, column] = replacement
        digest = replay._candidate_frame_contract(
            changed,
            label="exact behavior",
            discrete_columns=discrete,
            score_columns=("diagnostic_gap", "recommended_max_gap"),
        )["discrete_sha256"]
        assert digest != original


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("signal_date", "2026-08-05", "invalid exact-format signal_date"),
        ("signal_date", "20260805 ", "whitespace in signal_date"),
        ("ts_code", "000001.sz", "invalid exact-format ts_code"),
        ("ts_code", " 000001.SZ", "whitespace in ts_code"),
    ],
)
def test_identity_requires_exact_canonical_format(
    column: str,
    value: str,
    message: str,
) -> None:
    frame = pd.DataFrame(
        {"signal_date": ["20260805"], "ts_code": ["000001.SZ"]}
    )
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match=message):
        replay._identity_index(frame, label="identity")
