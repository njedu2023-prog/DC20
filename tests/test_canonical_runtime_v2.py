from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace

import pandas as pd
import pytest

from top10decision.auction_v3.config import AuctionV3Config
from top10decision.auction_v3.engine import (
    MARKET_SENTIMENT_FEATURES,
    MODEL_FEATURES,
    PROMOTION_SOURCE_FEATURES,
    AuctionV3Engine,
    _finalize_model_bundle_v2,
    _model_fingerprint_v2,
    _model_policy_fingerprint_v2,
)
from top10decision.decision.canonical_fingerprint import CanonicalSchemaError
from top10decision.decision.trade_selector import (
    TRADE_SELECTOR_FEATURES,
    TradeSelectorConfig,
    _bundle_fingerprint_v2,
    _finalize_trade_selector_bundle_v2,
    _selector_policy_fingerprint_v2,
    fit_trade_selector,
)


def _model_policy() -> dict:
    return {
        "version": "nested_temporal_utility_v1",
        "ready": False,
        "reason": "no_policy_passed_independent_holdout",
        "max_positions": 2,
        "thresholds": {
            "max_big_loss_probability": 0.4,
            "min_mean_return_lcb": -0.03,
            "min_fill_probability": 0.1,
            "min_exit_probability": 0.2,
            "min_conservative_ev": -0.01,
            "min_selection_score": 0.02,
        },
        "diagnostics": {"raw_objective": 0.123456789},
        "checks": {"positive_return": False},
    }


def _selector_policy() -> dict:
    return {
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
        "metrics": {"raw_objective": 0.123456789},
        "checks": {"buyable_mean_positive": False},
    }


def _model_semantic_frame() -> pd.DataFrame:
    columns = list(
        dict.fromkeys(
            [
                *MODEL_FEATURES,
                *MARKET_SENTIMENT_FEATURES,
                *PROMOTION_SOURCE_FEATURES,
            ]
        )
    )
    row = {column: 0.12345670 for column in columns}
    row.update(
        {
            "signal_date": "20260805",
            "buy_date": "20260806",
            "target_exit_date": "20260807",
            "ts_code": "000001.SZ",
            "actual_buy_gap": 0.012345670,
            "gross_return": 0.023456770,
            "net_return": 0.018956770,
            "profit_hit": 1,
            "big_loss_hit": 0,
            "continuation_limit_up_hit": 1,
            "exit_on_time": 1,
            "market_fill": 1,
            "market_sentiment_regime_code": "NEUTRAL",
        }
    )
    return pd.DataFrame([row])


def _selector_semantic_frame() -> pd.DataFrame:
    row = {column: 0.12345670 for column in TRADE_SELECTOR_FEATURES}
    row.update(
        {
            "signal_date": "20260805",
            "ts_code": "000001.SZ",
            "stage": "2→3",
            "observation_rank": 1,
            "market_fill": 1,
            "net_return": 0.018956770,
            "big_loss_hit": 0,
            "continuation_limit_up_hit": 1,
        }
    )
    return pd.DataFrame([row])


def test_model_policy_hash_uses_only_complete_executable_projection() -> None:
    policy = _model_policy()
    original = _model_policy_fingerprint_v2(policy)
    audit_changed = copy.deepcopy(policy)
    audit_changed["diagnostics"]["raw_objective"] = -999.0
    audit_changed["checks"]["positive_return"] = True
    assert _model_policy_fingerprint_v2(audit_changed) == original

    executable_changed = copy.deepcopy(policy)
    executable_changed["thresholds"]["min_selection_score"] += 0.00000002
    assert (
        _model_policy_fingerprint_v2(executable_changed)["sha256"]
        != original["sha256"]
    )


@pytest.mark.parametrize(
    "threshold",
    [
        "max_big_loss_probability",
        "min_mean_return_lcb",
        "min_fill_probability",
        "min_exit_probability",
        "min_conservative_ev",
        "min_selection_score",
    ],
)
def test_each_model_executable_threshold_above_1e8_rotates_policy(
    threshold: str,
) -> None:
    policy = _model_policy()
    original = _model_policy_fingerprint_v2(policy)["sha256"]
    policy["thresholds"][threshold] += 0.00000002
    assert _model_policy_fingerprint_v2(policy)["sha256"] != original


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["thresholds"].pop("min_exit_probability"), "missing threshold"),
        (lambda value: value["thresholds"].__setitem__("min_fill_probability", float("nan")), "non-finite"),
        (lambda value: value["thresholds"].__setitem__("max_big_loss_probability", float("inf")), "non-finite"),
        (lambda value: value.__setitem__("ready", "false"), "ready must be boolean"),
        (lambda value: value.__setitem__("max_positions", 1.5), "nonnegative integer"),
    ],
)
def test_model_policy_fingerprint_rejects_invalid_contract(
    mutation,
    message: str,
) -> None:
    policy = _model_policy()
    mutation(policy)
    with pytest.raises(ValueError, match=message):
        _model_policy_fingerprint_v2(policy)


def test_selector_policy_hash_excludes_metrics_and_checks() -> None:
    policy = _selector_policy()
    original = _selector_policy_fingerprint_v2(policy)
    audit_changed = copy.deepcopy(policy)
    audit_changed["metrics"]["raw_objective"] = -999.0
    audit_changed["checks"]["buyable_mean_positive"] = True
    assert _selector_policy_fingerprint_v2(audit_changed) == original

    executable_changed = copy.deepcopy(policy)
    executable_changed["tail_risk_weight"] += 0.00000002
    assert (
        _selector_policy_fingerprint_v2(executable_changed)["sha256"]
        != original["sha256"]
    )


@pytest.mark.parametrize(
    "threshold",
    [
        "min_trade_score",
        "min_mean_return_lcb",
        "min_fill_probability",
        "max_big_loss_probability",
    ],
)
def test_each_selector_executable_threshold_above_1e8_rotates_policy(
    threshold: str,
) -> None:
    policy = _selector_policy()
    original = _selector_policy_fingerprint_v2(policy)["sha256"]
    policy["thresholds"][threshold] += 0.00000002
    assert _selector_policy_fingerprint_v2(policy)["sha256"] != original


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("version", " nested_temporal_utility_v1 "),
        ("reason", " no_policy_passed_independent_holdout "),
        ("reason", "ｎｏ＿ｐｏｌｉｃｙ"),
    ],
)
def test_model_policy_hash_preserves_execution_exact_text(
    field: str,
    replacement: str,
) -> None:
    policy = _model_policy()
    original = _model_policy_fingerprint_v2(policy)["sha256"]
    policy[field] = replacement
    assert _model_policy_fingerprint_v2(policy)["sha256"] != original


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("version", " trade_selector_v2_nested_oos_top10_promotion_rank "),
        ("reason", " best_shadow_policy_failed_profit_or_coverage_gate "),
        ("reason", "ｓｈａｄｏｗ＿ｐｏｌｉｃｙ"),
    ],
)
def test_selector_policy_hash_preserves_execution_exact_text(
    field: str,
    replacement: str,
) -> None:
    policy = _selector_policy()
    original = _selector_policy_fingerprint_v2(policy)["sha256"]
    policy[field] = replacement
    assert _selector_policy_fingerprint_v2(policy)["sha256"] != original


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["thresholds"].pop("min_trade_score"), "missing threshold"),
        (lambda value: value["thresholds"].__setitem__("min_fill_probability", float("nan")), "non-finite"),
        (lambda value: value.__setitem__("tail_risk_weight", float("inf")), "non-finite"),
        (lambda value: value.__setitem__("reason", ""), "reason is required"),
        (lambda value: value.__setitem__("max_positions", "bad"), "max_positions is invalid"),
    ],
)
def test_selector_policy_fingerprint_rejects_invalid_contract(
    mutation,
    message: str,
) -> None:
    policy = _selector_policy()
    mutation(policy)
    with pytest.raises(ValueError, match=message):
        _selector_policy_fingerprint_v2(policy)


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("actual_buy_gap", 0.012345690),
        ("gross_return", 0.023456790),
        ("market_sentiment_regime_code", "EBB"),
        ("market_sentiment_regime_code", " NEUTRAL "),
        ("market_sentiment_regime_code", "ＮＥＵＴＲＡＬ"),
    ],
)
def test_model_semantic_execution_input_mutation_rotates_v2_artifact(
    tmp_path,
    column: str,
    replacement,
) -> None:
    frame = _model_semantic_frame()
    config = AuctionV3Config(root=tmp_path)
    original = _model_fingerprint_v2(frame, config, _model_policy())
    changed = frame.copy()
    changed.loc[0, column] = replacement
    mutated = _model_fingerprint_v2(changed, config, _model_policy())
    assert mutated["semantic_sha256"] != original["semantic_sha256"]
    assert mutated["artifact_sha256"] != original["artifact_sha256"]

    with pytest.raises(CanonicalSchemaError, match="missing_columns"):
        _model_fingerprint_v2(
            frame.drop(columns=[column]),
            config,
            _model_policy(),
        )


@pytest.mark.parametrize(
    "column",
    ["big_loss_hit", "continuation_limit_up_hit"],
)
def test_selector_training_target_mutation_rotates_v2_artifact(
    column: str,
) -> None:
    frame = _selector_semantic_frame()
    config = TradeSelectorConfig()
    original = _bundle_fingerprint_v2(
        frame,
        _selector_policy(),
        config,
        cost_rate=0.0045,
    )
    changed = frame.copy()
    changed.loc[0, column] = 1 - int(frame.loc[0, column])
    mutated = _bundle_fingerprint_v2(
        changed,
        _selector_policy(),
        config,
        cost_rate=0.0045,
    )
    assert mutated["semantic_sha256"] != original["semantic_sha256"]
    assert mutated["artifact_sha256"] != original["artifact_sha256"]

    with pytest.raises(CanonicalSchemaError, match="missing_columns"):
        _bundle_fingerprint_v2(
            frame.drop(columns=[column]),
            _selector_policy(),
            config,
            cost_rate=0.0045,
        )


def test_selector_cost_rate_is_part_of_v2_provenance() -> None:
    frame = _selector_semantic_frame()
    config = TradeSelectorConfig()
    original = _bundle_fingerprint_v2(
        frame,
        _selector_policy(),
        config,
        cost_rate=0.0045,
    )
    changed = _bundle_fingerprint_v2(
        frame,
        _selector_policy(),
        config,
        cost_rate=0.00450002,
    )
    assert changed["provenance_sha256"] != original["provenance_sha256"]
    assert changed["artifact_sha256"] != original["artifact_sha256"]


def test_model_execution_config_text_is_hashed_exactly(tmp_path) -> None:
    frame = _model_semantic_frame()
    original = _model_fingerprint_v2(
        frame,
        AuctionV3Config(root=tmp_path, model_version="model_v2"),
        _model_policy(),
    )
    changed = _model_fingerprint_v2(
        frame,
        AuctionV3Config(root=tmp_path, model_version=" model_v2 "),
        _model_policy(),
    )
    assert changed["provenance_sha256"] != original["provenance_sha256"]
    assert changed["artifact_sha256"] != original["artifact_sha256"]


def test_walkforward_fit_functions_never_mint_canonical_v2() -> None:
    model_fit_source = inspect.getsource(AuctionV3Engine.fit_models)
    selector_fit_source = inspect.getsource(fit_trade_selector)
    assert "_model_fingerprint_v2" not in model_fit_source
    assert "_finalize_model_bundle_v2" not in model_fit_source
    assert "_bundle_fingerprint_v2" not in selector_fit_source
    assert "_finalize_trade_selector_bundle_v2" not in selector_fit_source


def test_final_production_finalize_is_strict_and_sets_nonempty_artifact(
    tmp_path,
) -> None:
    model_bundle = SimpleNamespace(
        selection_policy=_model_policy(),
        runtime_canonical_contract={},
        model_fingerprint_v2={},
        model_artifact_v2_sha256="",
    )
    _finalize_model_bundle_v2(
        model_bundle,
        _model_semantic_frame(),
        AuctionV3Config(root=tmp_path),
    )
    assert len(model_bundle.model_artifact_v2_sha256) == 64
    assert (
        model_bundle.model_fingerprint_v2["artifact_sha256"]
        == model_bundle.model_artifact_v2_sha256
    )

    invalid_model = SimpleNamespace(
        selection_policy={
            "version": "nested_temporal_utility_v1",
            "ready": False,
            "reason": "early_legal_empty_policy",
            "max_positions": 0,
            "thresholds": {},
        },
        runtime_canonical_contract={},
        model_fingerprint_v2={},
        model_artifact_v2_sha256="",
    )
    with pytest.raises(ValueError, match="missing threshold"):
        _finalize_model_bundle_v2(
            invalid_model,
            _model_semantic_frame(),
            AuctionV3Config(root=tmp_path),
        )

    selector_bundle = SimpleNamespace(
        policy=_selector_policy(),
        canonical_contract={},
        fingerprint_v2={},
        artifact_v2_sha256="",
    )
    _finalize_trade_selector_bundle_v2(
        selector_bundle,
        _selector_semantic_frame(),
        TradeSelectorConfig(),
        cost_rate=0.0045,
    )
    assert len(selector_bundle.artifact_v2_sha256) == 64
    assert selector_bundle.fingerprint_v2["artifact_sha256"] == selector_bundle.artifact_v2_sha256
