#!/usr/bin/env python3
"""Finite v2 executable-profit fusion challengers, research-only and fail-closed.

The frozen promotion OOF Top10 defines the universe.  The frozen p_fill OOF
probability is an upstream component of the joint probability, never a model
feature.  No final calibrator is allowed to alter the product identity:

    q = P(fill proxy) * P(profit | fill proxy)

This module cannot train or overwrite promotion, publish a frontend field, or
create a trade action.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
DEFAULT_LEDGER = (
    ROOT / "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz"
)
DEFAULT_MANIFEST = (
    ROOT / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
)
DEFAULT_OOF = ROOT / "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
DEFAULT_CALENDAR = ROOT / "data/market/trade_cal_sse.csv"

EXPECTED_LEDGER_SHA256 = (
    "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
)
EXPECTED_OOF_SHA256 = (
    "c768cb0eb019fba6be7ca41284841006195dd54bf4d641f426d2fbbf513a4ebd"
)
EXPECTED_CALENDAR_SHA256 = (
    "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
)
EXPECTED_FEATURES_SHA256 = (
    "9f403117278b73653014a3682442072f026d8e73abef37d318086565dae23425"
)
BUCKETS = ("BIG_LOSS", "NON_PROFIT", "PROFIT")
SCHEMA = "dc20_executable_profit_shadow_v2_research_validation_v1"
POLICIES = (
    "executable_profit_top2",
    "promotion_top2",
    "promotion_top10_equal_weight",
    "frozen_p_fill_top2",
)


@dataclass(frozen=True)
class V2Config:
    warmup_dates: int = 300
    outer_block_dates: int = 20
    embargo_open_dates: int = 2
    inner_fit_fraction: float = 0.60
    inner_component_fraction: float = 0.18
    minimum_inner_fit_dates: int = 120
    minimum_component_dates: int = 30
    minimum_final_audit_dates: int = 30
    minimum_conditional_fit_rows: int = 350
    minimum_oof_dates: int = 500
    confirmation_dates: int = 180
    maximum_ece: float = 0.08
    quantile_alpha: float = 0.10
    regression_max_iter: int = 90
    classifier_max_iter: int = 120
    bootstrap_samples: int = 1_000
    bootstrap_block_dates: int = 5
    random_state: int = 20260824


@dataclass
class ConditionalBundle:
    candidate: str
    feature_columns: tuple[str, ...]
    classifier: Pipeline
    temperature: float
    mean_regressor: Pipeline
    lower_regressor: Pipeline
    fit_start: str
    fit_end: str
    component_start: str
    component_end: str
    final_start: str
    final_end: str
    conditional_profit_base_rate: float
    final_audit: dict[str, Any]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        json_safe(payload),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_gzip_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                frame.to_csv(gz, index=False, lineterminator="\n")
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    dates = frame["signal_date"].astype(str)
    counts = dates.map(dates.value_counts()).astype(float).clip(lower=1.0)
    weights = (1.0 / counts).to_numpy(dtype=float)
    return weights / max(float(weights.mean()), 1e-12)


def open_dates_from_calendar(path: Path) -> list[str]:
    frame = pd.read_csv(path, dtype={"cal_date": "string"})
    if "cal_date" not in frame or "is_open" not in frame:
        raise ValueError("strict SSE calendar columns are missing")
    dates = sorted(
        frame.loc[pd.to_numeric(frame["is_open"], errors="coerce").eq(1), "cal_date"]
        .astype(str)
        .tolist()
    )
    if len(dates) != len(set(dates)):
        raise ValueError("strict SSE calendar contains duplicate open dates")
    return dates


def advance_for_embargo(
    dates: Sequence[str],
    proposed_index: int,
    previous_last: str,
    open_index: Mapping[str, int],
    embargo: int,
) -> int:
    index = proposed_index
    while index < len(dates):
        if open_index[str(dates[index])] - open_index[previous_last] > embargo:
            return index
        index += 1
    return len(dates)


def inner_partitions(
    training: pd.DataFrame,
    open_dates: Sequence[str],
    test_start: str,
    config: V2Config,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(training["signal_date"].astype(str).unique())
    open_index = {date: index for index, date in enumerate(open_dates)}
    if any(date not in open_index for date in dates) or test_start not in open_index:
        raise ValueError("partition date is absent from strict SSE calendar")
    fit_count = max(
        config.minimum_inner_fit_dates,
        int(math.floor(len(dates) * config.inner_fit_fraction)),
    )
    if fit_count >= len(dates):
        raise ValueError("inner fit block consumes all training dates")
    fit_dates = dates[:fit_count]
    component_start_index = advance_for_embargo(
        dates,
        fit_count,
        fit_dates[-1],
        open_index,
        config.embargo_open_dates,
    )
    component_count = max(
        config.minimum_component_dates,
        int(math.floor(len(dates) * config.inner_component_fraction)),
    )
    component_end_index = min(
        len(dates), component_start_index + component_count
    )
    component_dates = dates[component_start_index:component_end_index]
    if not component_dates:
        raise ValueError("component calibration block is empty")
    final_start_index = advance_for_embargo(
        dates,
        component_end_index,
        component_dates[-1],
        open_index,
        config.embargo_open_dates,
    )
    final_dates = dates[final_start_index:]
    if (
        len(fit_dates) < config.minimum_inner_fit_dates
        or len(component_dates) < config.minimum_component_dates
        or len(final_dates) < config.minimum_final_audit_dates
    ):
        raise ValueError("inner chronological blocks lack date support")

    fit = training[training["signal_date"].astype(str).isin(fit_dates)].copy()
    component = training[
        training["signal_date"].astype(str).isin(component_dates)
    ].copy()
    final = training[training["signal_date"].astype(str).isin(final_dates)].copy()

    def latest_label_exit(frame: pd.DataFrame) -> str:
        labelled = frame[
            frame["public_market_buyable_proxy"].eq(1)
            & frame["conditional_return_bucket"].notna()
        ]
        if labelled.empty:
            raise ValueError("chronological block has no conditional labels")
        return str(labelled["scheduled_exit_date"].astype(str).max())

    fit_exit = latest_label_exit(fit)
    component_exit = latest_label_exit(component)
    final_exit = latest_label_exit(final)
    audit = {
        "fit_start": fit_dates[0],
        "fit_end": fit_dates[-1],
        "fit_latest_label_exit": fit_exit,
        "component_start": component_dates[0],
        "component_end": component_dates[-1],
        "component_latest_label_exit": component_exit,
        "final_start": final_dates[0],
        "final_end": final_dates[-1],
        "final_latest_label_exit": final_exit,
        "test_start": test_start,
        "fit_exit_before_component": fit_exit < component_dates[0],
        "component_exit_before_final": component_exit < final_dates[0],
        "final_exit_before_test": final_exit < test_start,
        "fit_component_open_gap": (
            open_index[component_dates[0]] - open_index[fit_dates[-1]] - 1
        ),
        "component_final_open_gap": (
            open_index[final_dates[0]] - open_index[component_dates[-1]] - 1
        ),
    }
    if not (
        audit["fit_exit_before_component"]
        and audit["component_exit_before_final"]
        and audit["final_exit_before_test"]
        and int(audit["fit_component_open_gap"]) >= config.embargo_open_dates
        and int(audit["component_final_open_gap"]) >= config.embargo_open_dates
    ):
        raise ValueError(f"strict inner truth-timing assertion failed: {audit}")
    return fit, component, final, audit


def classifier(candidate: str, config: V2Config) -> Pipeline:
    if candidate == "lr_distribution":
        return Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                ("scaler", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.25,
                        max_iter=2_000,
                        random_state=config.random_state,
                    ),
                ),
            ]
        )
    if candidate == "hgb_distribution":
        return Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.04,
                        max_iter=config.classifier_max_iter,
                        max_leaf_nodes=15,
                        min_samples_leaf=25,
                        l2_regularization=1.0,
                        random_state=config.random_state,
                    ),
                ),
            ]
        )
    raise ValueError(f"unsupported fixed candidate: {candidate}")


def return_regressor(
    *,
    quantile: float | None,
    config: V2Config,
) -> Pipeline:
    parameters: dict[str, Any] = {
        "learning_rate": 0.04,
        "max_iter": config.regression_max_iter,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 25,
        "l2_regularization": 1.0,
        "random_state": config.random_state,
    }
    if quantile is None:
        parameters["loss"] = "squared_error"
    else:
        parameters["loss"] = "quantile"
        parameters["quantile"] = quantile
    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(
                    strategy="median",
                    add_indicator=True,
                    keep_empty_features=True,
                ),
            ),
            ("model", HistGradientBoostingRegressor(**parameters)),
        ]
    )


def temperature_fit(
    probability: np.ndarray,
    truth_index: np.ndarray,
    weights: np.ndarray,
) -> float:
    p = np.clip(np.asarray(probability, dtype=float), 1e-8, 1.0)
    y = np.asarray(truth_index, dtype=int)
    w = np.asarray(weights, dtype=float)
    if len(p) < 30 or len(np.unique(y)) != 3:
        raise ValueError("temperature calibration lacks three-class support")
    logp = np.log(p)

    def objective(log_temperature: float) -> float:
        temperature = math.exp(float(log_temperature))
        logits = logp / temperature
        logits -= logits.max(axis=1, keepdims=True)
        q = np.exp(logits)
        q /= q.sum(axis=1, keepdims=True)
        return float(np.average(-np.log(q[np.arange(len(q)), y] + 1e-12), weights=w))

    result = minimize_scalar(
        objective,
        bounds=(math.log(0.20), math.log(5.0)),
        method="bounded",
        options={"xatol": 1e-8},
    )
    if not result.success:
        raise ValueError("temperature calibration failed")
    return float(math.exp(float(result.x)))


def temperature_apply(probability: np.ndarray, temperature: float) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=float), 1e-8, 1.0)
    logits = np.log(p) / float(temperature)
    logits -= logits.max(axis=1, keepdims=True)
    q = np.exp(logits)
    return q / q.sum(axis=1, keepdims=True)


def ordered_class_probability(
    model: Pipeline,
    features: pd.DataFrame,
    temperature: float,
) -> np.ndarray:
    raw = np.asarray(model.predict_proba(features), dtype=float)
    classes = tuple(str(value) for value in model.named_steps["model"].classes_)
    if set(classes) != set(BUCKETS):
        raise ValueError(f"conditional class inventory drifted: {classes}")
    calibrated = temperature_apply(raw, temperature)
    ordered = calibrated[:, [classes.index(name) for name in BUCKETS]]
    if not np.allclose(ordered.sum(axis=1), 1.0, atol=1e-10):
        raise ValueError("conditional probabilities do not sum to one")
    return ordered


def conditional_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[
        frame["public_market_buyable_proxy"].eq(1)
        & frame["conditional_return_bucket"].notna()
        & frame["conditional_net_return_after_cost"].notna()
    ].copy()


def fit_bundle(
    training: pd.DataFrame,
    feature_columns: Sequence[str],
    open_dates: Sequence[str],
    test_start: str,
    candidate: str,
    config: V2Config,
) -> tuple[ConditionalBundle, dict[str, Any]]:
    fit, component, final, timing = inner_partitions(
        training, open_dates, test_start, config
    )
    fit_conditional = conditional_rows(fit)
    component_conditional = conditional_rows(component)
    final_conditional = conditional_rows(final)
    if len(fit_conditional) < config.minimum_conditional_fit_rows:
        raise ValueError("conditional fit rows are insufficient")
    if fit_conditional["conditional_return_bucket"].nunique() != 3:
        raise ValueError("conditional fit block lacks three classes")
    if component_conditional["conditional_return_bucket"].nunique() != 3:
        raise ValueError("component block lacks three classes")
    if final_conditional["conditional_return_bucket"].nunique() != 3:
        raise ValueError("final audit block lacks three classes")

    model = classifier(candidate, config)
    model.fit(
        fit_conditional[list(feature_columns)],
        fit_conditional["conditional_return_bucket"].astype(str),
        model__sample_weight=date_weights(fit_conditional),
    )
    raw_component = model.predict_proba(
        component_conditional[list(feature_columns)]
    )
    classes = tuple(str(value) for value in model.named_steps["model"].classes_)
    class_index = {name: index for index, name in enumerate(classes)}
    component_truth = component_conditional["conditional_return_bucket"].astype(str).map(
        class_index
    )
    if component_truth.isna().any():
        raise ValueError("component calibration contains an unknown class")
    temperature = temperature_fit(
        raw_component,
        component_truth.astype(int).to_numpy(),
        date_weights(component_conditional),
    )

    mean_model = return_regressor(quantile=None, config=config)
    lower_model = return_regressor(quantile=config.quantile_alpha, config=config)
    target = fit_conditional["conditional_net_return_after_cost"].astype(float)
    fit_weights = date_weights(fit_conditional)
    mean_model.fit(
        fit_conditional[list(feature_columns)],
        target,
        model__sample_weight=fit_weights,
    )
    lower_model.fit(
        fit_conditional[list(feature_columns)],
        target,
        model__sample_weight=fit_weights,
    )

    final_probabilities = ordered_class_probability(
        model, final_conditional[list(feature_columns)], temperature
    )
    final_profit = final_probabilities[:, BUCKETS.index("PROFIT")]
    final_truth = final_conditional["conditional_profit_hit"].astype(int).to_numpy()
    final_weights = date_weights(final_conditional)
    final_mean = mean_model.predict(final_conditional[list(feature_columns)])
    final_lower = lower_model.predict(final_conditional[list(feature_columns)])
    actual_return = final_conditional["conditional_net_return_after_cost"].to_numpy(
        dtype=float
    )
    final_audit = {
        "rows": int(len(final_conditional)),
        "dates": int(final_conditional["signal_date"].nunique()),
        "profit_brier": float(
            np.average((final_profit - final_truth) ** 2, weights=final_weights)
        ),
        "profit_auc": (
            float(roc_auc_score(final_truth, final_profit, sample_weight=final_weights))
            if len(np.unique(final_truth)) == 2
            else None
        ),
        "mean_return_mae": float(
            np.average(np.abs(final_mean - actual_return), weights=final_weights)
        ),
        "lower_bound_coverage": float(
            np.average(actual_return >= final_lower, weights=final_weights)
        ),
        "truth_or_performance_used_for_selection": False,
    }
    profit_base = float(
        np.average(
            fit_conditional["conditional_profit_hit"].astype(float),
            weights=fit_weights,
        )
    )
    return (
        ConditionalBundle(
            candidate=candidate,
            feature_columns=tuple(feature_columns),
            classifier=model,
            temperature=temperature,
            mean_regressor=mean_model,
            lower_regressor=lower_model,
            fit_start=timing["fit_start"],
            fit_end=timing["fit_end"],
            component_start=timing["component_start"],
            component_end=timing["component_end"],
            final_start=timing["final_start"],
            final_end=timing["final_end"],
            conditional_profit_base_rate=profit_base,
            final_audit=final_audit,
        ),
        timing,
    )


def score_bundle(bundle: ConditionalBundle, frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    if output["frozen_p_fill_probability"].isna().any():
        raise ValueError("frozen p_fill probability is missing in a scored Top10 date")
    features = output[list(bundle.feature_columns)]
    probability = ordered_class_probability(
        bundle.classifier, features, bundle.temperature
    )
    p_loss = probability[:, BUCKETS.index("BIG_LOSS")]
    p_middle = probability[:, BUCKETS.index("NON_PROFIT")]
    p_profit = probability[:, BUCKETS.index("PROFIT")]
    p_fill = output["frozen_p_fill_probability"].to_numpy(dtype=float)
    q = p_fill * p_profit
    predicted_mean = bundle.mean_regressor.predict(features)
    predicted_lower = bundle.lower_regressor.predict(features)
    output["predicted_conditional_big_loss_probability"] = p_loss
    output["predicted_conditional_non_profit_probability"] = p_middle
    output["predicted_conditional_profit_probability"] = p_profit
    output["predicted_conditional_mean_net_return"] = predicted_mean
    output["expected_net_return_lcb"] = np.minimum(predicted_mean, predicted_lower)
    output["predicted_executable_net_profit_probability"] = q
    output["fold_conditional_profit_base_rate"] = (
        bundle.conditional_profit_base_rate
    )
    output["fold_baseline_executable_probability"] = (
        p_fill * bundle.conditional_profit_base_rate
    )
    tolerance = 1e-12
    if np.any(q < -tolerance) or np.any(q > p_fill + tolerance) or np.any(
        q > p_profit + tolerance
    ):
        raise ValueError("joint executable-profit probability violates product bounds")
    if not np.allclose(q, p_fill * p_profit, atol=1e-15, rtol=0.0):
        raise ValueError("joint executable-profit probability is not the exact product")
    return output


def allowed_outer_training_dates(
    prior_signal_dates: Sequence[str],
    test_start: str,
    open_dates: Sequence[str],
    embargo: int,
) -> list[str]:
    open_index = {date: index for index, date in enumerate(open_dates)}
    if test_start not in open_index:
        raise ValueError("test start is absent from strict SSE calendar")
    cutoff = open_index[test_start] - embargo - 1
    return [
        str(date)
        for date in prior_signal_dates
        if str(date) in open_index and open_index[str(date)] <= cutoff
    ]


def walkforward(
    ledger: pd.DataFrame,
    feature_columns: Sequence[str],
    open_dates: Sequence[str],
    candidate: str,
    config: V2Config,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    dates = sorted(ledger["signal_date"].astype(str).unique())
    if len(dates) <= config.warmup_dates:
        raise ValueError("historical ledger lacks outer OOF support")
    open_index = {date: index for index, date in enumerate(open_dates)}
    output: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    fold = 0
    for block_start in range(
        config.warmup_dates, len(dates), config.outer_block_dates
    ):
        test_dates = dates[block_start : block_start + config.outer_block_dates]
        if not test_dates:
            continue
        test_start = test_dates[0]
        train_dates = allowed_outer_training_dates(
            dates[:block_start],
            test_start,
            open_dates,
            config.embargo_open_dates,
        )
        training = ledger[ledger["signal_date"].astype(str).isin(train_dates)].copy()
        if training.empty:
            continue
        if str(training["scheduled_exit_date"].astype(str).max()) >= test_start:
            training = training[
                training["scheduled_exit_date"].astype(str).lt(test_start)
            ].copy()
        bundle, timing = fit_bundle(
            training,
            feature_columns,
            open_dates,
            test_start,
            candidate,
            config,
        )
        test = ledger[ledger["signal_date"].astype(str).isin(test_dates)].copy()
        scored = score_bundle(bundle, test)
        fold += 1
        scored["v2_oof_fold"] = fold
        scored["v2_candidate"] = candidate
        scored["v2_test_start"] = test_start
        scored["v2_test_end"] = test_dates[-1]
        scored["v2_fit_end"] = bundle.fit_end
        scored["v2_component_end"] = bundle.component_end
        scored["v2_final_audit_end"] = bundle.final_end
        output.append(scored)

        latest_signal = str(training["signal_date"].astype(str).max())
        latest_exit = str(training["scheduled_exit_date"].astype(str).max())
        outer_gap = open_index[test_start] - open_index[latest_signal] - 1
        audit = {
            "fold": fold,
            "candidate": candidate,
            "test_start": test_start,
            "test_end": test_dates[-1],
            "test_dates": len(test_dates),
            "test_rows": int(len(test)),
            "training_dates": int(training["signal_date"].nunique()),
            "training_rows": int(len(training)),
            "outer_latest_training_signal": latest_signal,
            "outer_latest_training_exit": latest_exit,
            "outer_open_date_gap": outer_gap,
            "outer_exit_before_test": latest_exit < test_start,
            "outer_embargo_passed": outer_gap >= config.embargo_open_dates,
            **timing,
            "temperature": bundle.temperature,
            "conditional_profit_base_rate": bundle.conditional_profit_base_rate,
            "final_audit": bundle.final_audit,
        }
        if not audit["outer_exit_before_test"] or not audit["outer_embargo_passed"]:
            raise ValueError(f"strict outer timing assertion failed: {audit}")
        audits.append(audit)
    if not output:
        raise ValueError("v2 walk-forward produced no OOF predictions")
    return pd.concat(output, ignore_index=True), audits


def freeze_top2(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    ordered = output.sort_values(
        [
            "signal_date",
            "predicted_executable_net_profit_probability",
            "expected_net_return_lcb",
            "predicted_conditional_big_loss_probability",
            "ts_code",
        ],
        ascending=[True, False, False, True, True],
        kind="stable",
    )
    ranks = ordered.groupby("signal_date", sort=False).cumcount() + 1
    output.loc[ordered.index, "executable_profit_shadow_rank"] = ranks.to_numpy()
    output["executable_profit_shadow_rank"] = output[
        "executable_profit_shadow_rank"
    ].astype("Int64")
    output["shadow_slot"] = output["executable_profit_shadow_rank"].where(
        output["executable_profit_shadow_rank"].le(2)
    )
    return output


def ece(probability: np.ndarray, truth: np.ndarray, weights: np.ndarray) -> float:
    p = np.asarray(probability, dtype=float)
    y = np.asarray(truth, dtype=float)
    w = np.asarray(weights, dtype=float)
    total = float(w.sum())
    value = 0.0
    edges = np.linspace(0.0, 1.0, 11)
    for index in range(10):
        mask = (p >= edges[index]) & (
            p <= edges[index + 1] if index == 9 else p < edges[index + 1]
        )
        if not mask.any():
            continue
        weight = float(w[mask].sum())
        value += weight / total * abs(
            float(np.average(p[mask], weights=w[mask]))
            - float(np.average(y[mask], weights=w[mask]))
        )
    return float(value)


def block_bootstrap(
    values: pd.Series,
    config: V2Config,
    seed_offset: int,
) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"dates": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    array = clean.to_numpy(dtype=float)
    block_size = min(config.bootstrap_block_dates, len(array))
    blocks = [
        array[index : index + block_size]
        for index in range(0, len(array), block_size)
    ]
    rng = np.random.default_rng(config.random_state + seed_offset)
    estimates = np.empty(config.bootstrap_samples, dtype=float)
    for index in range(config.bootstrap_samples):
        pieces: list[np.ndarray] = []
        count = 0
        while count < len(array):
            piece = blocks[int(rng.integers(0, len(blocks)))]
            pieces.append(piece)
            count += len(piece)
        estimates[index] = float(np.mean(np.concatenate(pieces)[: len(array)]))
    return {
        "dates": int(len(array)),
        "mean": float(np.mean(array)),
        "ci95_low": float(np.quantile(estimates, 0.025)),
        "ci95_high": float(np.quantile(estimates, 0.975)),
        "bootstrap_samples": config.bootstrap_samples,
        "block_dates": block_size,
    }


def probability_metrics(frame: pd.DataFrame, config: V2Config) -> dict[str, Any]:
    valid = frame[
        frame["executable_profit_proxy_hit"].notna()
        & frame["predicted_executable_net_profit_probability"].notna()
    ].copy()
    y = valid["executable_profit_proxy_hit"].astype(int).to_numpy()
    q = valid["predicted_executable_net_profit_probability"].to_numpy(dtype=float)
    baseline = valid["fold_baseline_executable_probability"].to_numpy(dtype=float)
    weights = date_weights(valid)
    improvement_rows = (baseline - y) ** 2 - (q - y) ** 2
    daily_improvement = pd.DataFrame(
        {
            "signal_date": valid["signal_date"].astype(str),
            "value": improvement_rows,
        }
    ).groupby("signal_date", sort=True)["value"].mean()
    return {
        "rows": int(len(valid)),
        "dates": int(valid["signal_date"].nunique()),
        "positive_rate": float(np.average(y, weights=weights)),
        "mean_prediction": float(np.average(q, weights=weights)),
        "brier": float(np.average((q - y) ** 2, weights=weights)),
        "baseline_brier": float(
            np.average((baseline - y) ** 2, weights=weights)
        ),
        "brier_improvement": float(np.average(improvement_rows, weights=weights)),
        "brier_improvement_bootstrap": block_bootstrap(
            daily_improvement, config, 5
        ),
        "log_loss": float(log_loss(y, q, sample_weight=weights, labels=[0, 1])),
        "baseline_log_loss": float(
            log_loss(y, baseline, sample_weight=weights, labels=[0, 1])
        ),
        "ece": ece(q, y, weights),
        "auc": (
            float(roc_auc_score(y, q, sample_weight=weights))
            if len(np.unique(y)) == 2
            else None
        ),
    }


def conditional_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    valid = conditional_rows(frame)
    truth_profit = valid["conditional_profit_hit"].astype(int).to_numpy()
    p_profit = valid["predicted_conditional_profit_probability"].to_numpy(
        dtype=float
    )
    weights = date_weights(valid)
    truth_bucket = valid["conditional_return_bucket"].astype(str).map(
        {name: index for index, name in enumerate(BUCKETS)}
    )
    probability = valid[
        [
            "predicted_conditional_big_loss_probability",
            "predicted_conditional_non_profit_probability",
            "predicted_conditional_profit_probability",
        ]
    ].to_numpy(dtype=float)
    actual = valid["conditional_net_return_after_cost"].to_numpy(dtype=float)
    mean_prediction = valid["predicted_conditional_mean_net_return"].to_numpy(
        dtype=float
    )
    lower = valid["expected_net_return_lcb"].to_numpy(dtype=float)
    alpha = 0.10
    residual = actual - lower
    pinball = np.maximum(alpha * residual, (alpha - 1.0) * residual)
    return {
        "rows": int(len(valid)),
        "dates": int(valid["signal_date"].nunique()),
        "profit_brier": float(
            np.average((p_profit - truth_profit) ** 2, weights=weights)
        ),
        "profit_ece": ece(p_profit, truth_profit, weights),
        "profit_auc": (
            float(roc_auc_score(truth_profit, p_profit, sample_weight=weights))
            if len(np.unique(truth_profit)) == 2
            else None
        ),
        "multiclass_log_loss": float(
            log_loss(
                truth_bucket.astype(int).to_numpy(),
                probability,
                sample_weight=weights,
                labels=[0, 1, 2],
            )
        ),
        "mean_return_mae": float(
            np.average(np.abs(mean_prediction - actual), weights=weights)
        ),
        "mean_return_rmse": float(
            math.sqrt(np.average((mean_prediction - actual) ** 2, weights=weights))
        ),
        "lower_bound_coverage": float(np.average(actual >= lower, weights=weights)),
        "lower_bound_pinball_loss": float(np.average(pinball, weights=weights)),
    }


def policy_rows(frame: pd.DataFrame, policy: str) -> tuple[pd.DataFrame, int | None]:
    if policy == "executable_profit_top2":
        return frame[frame["executable_profit_shadow_rank"].le(2)].copy(), 2
    if policy == "promotion_top2":
        return frame[pd.to_numeric(frame["promotion_rank"]).le(2)].copy(), 2
    if policy == "frozen_p_fill_top2":
        return frame[pd.to_numeric(frame["frozen_p_fill_rank"]).le(2)].copy(), 2
    if policy == "promotion_top10_equal_weight":
        return frame.copy(), None
    raise ValueError(f"unknown policy: {policy}")


def policy_daily(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    selected, fixed_slots = policy_rows(frame, policy)
    rows: list[dict[str, Any]] = []
    for date, group in selected.groupby("signal_date", sort=True):
        strategy = pd.to_numeric(group["strategy_slot_net_return"], errors="coerce")
        stress = pd.to_numeric(
            group["strategy_slot_net_return_2x_cost"], errors="coerce"
        )
        event = pd.to_numeric(group["executable_profit_proxy_hit"], errors="coerce")
        fill = pd.to_numeric(group["public_market_buyable_proxy"], errors="coerce")
        loss = pd.Series(np.nan, index=group.index, dtype=float)
        known_fill = fill.notna()
        loss.loc[known_fill & fill.eq(0)] = 0.0
        conditional_loss = pd.to_numeric(
            group["conditional_big_loss_hit"], errors="coerce"
        )
        loss.loc[known_fill & fill.eq(1)] = conditional_loss.loc[
            known_fill & fill.eq(1)
        ]
        denominator = fixed_slots or len(group)
        cash_slots = max(0, denominator - len(group))
        complete = (
            strategy.notna().all()
            and stress.notna().all()
            and event.notna().all()
            and fill.notna().all()
            and loss.notna().all()
        )
        rows.append(
            {
                "signal_date": str(date),
                "candidate_rows": int(len(group)),
                "capital_slots": int(denominator),
                "cash_slots": int(cash_slots),
                "pending_rows": int(strategy.isna().sum()),
                "return": float(strategy.sum() / denominator) if complete else np.nan,
                "stress_return": (
                    float(stress.sum() / denominator) if complete else np.nan
                ),
                "profit_rate": float(event.sum() / denominator) if complete else np.nan,
                "fill_rate": float(fill.sum() / denominator) if complete else np.nan,
                "big_loss_rate": float(loss.sum() / denominator) if complete else np.nan,
            }
        )
    return pd.DataFrame(rows).set_index("signal_date")


def panel_policy_metrics(daily: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(daily["return"], errors="coerce").dropna()
    wealth = (1.0 + returns).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0 if len(wealth) else pd.Series(dtype=float)
    worst_count = max(1, int(math.ceil(len(returns) * 0.10))) if len(returns) else 0
    return {
        "dates": int(len(daily)),
        "candidate_rows": int(daily["candidate_rows"].sum()),
        "capital_slots": int(daily["capital_slots"].sum()),
        "cash_slots": int(daily["cash_slots"].sum()),
        "mean_return": float(daily["return"].mean()),
        "median_return": float(daily["return"].median()),
        "mean_stress_return": float(daily["stress_return"].mean()),
        "profit_rate": float(daily["profit_rate"].mean()),
        "fill_rate": float(daily["fill_rate"].mean()),
        "big_loss_rate": float(daily["big_loss_rate"].mean()),
        "compound_return": float(wealth.iloc[-1] - 1.0) if len(wealth) else None,
        "maximum_drawdown": float(drawdown.min()) if len(drawdown) else None,
        "worst_10pct_expected_shortfall": (
            float(returns.nsmallest(worst_count).mean()) if worst_count else None
        ),
    }


def common_mature_panel(
    frame: pd.DataFrame,
    config: V2Config,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    all_daily = {policy: policy_daily(frame, policy) for policy in POLICIES}
    common_dates: set[str] | None = None
    for daily in all_daily.values():
        mature = set(
            daily.index[
                daily[["return", "stress_return", "profit_rate"]].notna().all(axis=1)
            ].astype(str)
        )
        common_dates = mature if common_dates is None else common_dates.intersection(mature)
    ordered_dates = sorted(common_dates or set())
    panel = {policy: daily.loc[ordered_dates].copy() for policy, daily in all_daily.items()}
    metrics = {
        "common_mature_dates": len(ordered_dates),
        "start": ordered_dates[0] if ordered_dates else None,
        "end": ordered_dates[-1] if ordered_dates else None,
        "policies": {
            policy: panel_policy_metrics(daily) for policy, daily in panel.items()
        },
        "paired_lifts": {},
    }
    candidate_daily = panel["executable_profit_top2"]
    for offset, baseline in enumerate(POLICIES[1:], start=1):
        metrics["paired_lifts"][baseline] = {
            "return": block_bootstrap(
                candidate_daily["return"] - panel[baseline]["return"],
                config,
                100 + offset,
            ),
            "profit_rate": block_bootstrap(
                candidate_daily["profit_rate"] - panel[baseline]["profit_rate"],
                config,
                200 + offset,
            ),
        }
    return metrics, panel


def stage_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    selected = frame[frame["executable_profit_shadow_rank"].le(2)].copy()
    output: dict[str, Any] = {}
    for stage in (2, 3):
        sample = selected[pd.to_numeric(selected["stage"]).eq(stage)].copy()
        returns = pd.to_numeric(sample["strategy_slot_net_return"], errors="coerce")
        event = pd.to_numeric(sample["executable_profit_proxy_hit"], errors="coerce")
        output[f"{stage}_to_{stage + 1}"] = {
            "selected_rows": int(len(sample)),
            "matured_rows": int(returns.notna().sum()),
            "mean_slot_return": float(returns.mean()) if returns.notna().any() else None,
            "profit_rate": float(event.mean()) if event.notna().any() else None,
        }
    return output


def subset_report(frame: pd.DataFrame, name: str, config: V2Config) -> dict[str, Any]:
    panel, _ = common_mature_panel(frame, config)
    return {
        "name": name,
        "signal_dates": int(frame["signal_date"].nunique()),
        "rows": int(len(frame)),
        "start": str(frame["signal_date"].min()),
        "end": str(frame["signal_date"].max()),
        "joint_probability": probability_metrics(frame, config),
        "conditional_components": conditional_metrics(frame),
        "common_mature_panel": panel,
        "stages": stage_metrics(frame),
    }


def evaluate(
    oof: pd.DataFrame,
    audits: list[dict[str, Any]],
    candidate: str,
    config: V2Config,
) -> dict[str, Any]:
    dates = sorted(oof["signal_date"].astype(str).unique())
    if len(dates) <= config.confirmation_dates:
        raise ValueError("OOF history cannot supply the fixed confirmation slice")
    confirmation_dates = dates[-config.confirmation_dates :]
    development_dates = dates[: -config.confirmation_dates]
    development = oof[oof["signal_date"].astype(str).isin(development_dates)].copy()
    confirmation = oof[oof["signal_date"].astype(str).isin(confirmation_dates)].copy()
    development_report = subset_report(development, "development", config)
    confirmation_report = subset_report(confirmation, "retrospective_confirmation", config)

    q = oof["predicted_executable_net_profit_probability"].to_numpy(dtype=float)
    p_fill = oof["frozen_p_fill_probability"].to_numpy(dtype=float)
    p_profit = oof["predicted_conditional_profit_probability"].to_numpy(dtype=float)
    product_identity = bool(np.allclose(q, p_fill * p_profit, atol=1e-15, rtol=0.0))
    bounds = bool(np.all(q >= -1e-12) and np.all(q <= p_fill + 1e-12) and np.all(q <= p_profit + 1e-12))
    confirmation_panel = confirmation_report["common_mature_panel"]
    confirmation_candidate = confirmation_panel["policies"]["executable_profit_top2"]
    confirmation_pfill = confirmation_panel["paired_lifts"]["frozen_p_fill_top2"]
    confirmation_probability = confirmation_report["joint_probability"]
    confirmation_stages = confirmation_report["stages"]
    gates = {
        "exact_input_hashes": True,
        "strict_fit_exit_before_component_all_folds": all(
            item["fit_exit_before_component"] for item in audits
        ),
        "strict_component_exit_before_final_all_folds": all(
            item["component_exit_before_final"] for item in audits
        ),
        "strict_final_exit_before_test_all_folds": all(
            item["final_exit_before_test"] for item in audits
        ),
        "strict_outer_embargo_all_folds": all(
            item["outer_embargo_passed"] for item in audits
        ),
        "joint_probability_exact_product": product_identity,
        "joint_probability_component_upper_bounds": bounds,
        "oof_dates_at_least_500": len(dates) >= config.minimum_oof_dates,
        "confirmation_dates_exactly_180": len(confirmation_dates)
        == config.confirmation_dates,
        "confirmation_joint_brier_improvement_positive": float(
            confirmation_probability["brier_improvement"]
        )
        > 0.0,
        "confirmation_joint_brier_ci_lower_positive": float(
            confirmation_probability["brier_improvement_bootstrap"]["ci95_low"]
        )
        > 0.0,
        "confirmation_joint_ece_at_most_8pct": float(
            confirmation_probability["ece"]
        )
        <= config.maximum_ece,
        "confirmation_top2_absolute_return_positive": float(
            confirmation_candidate["mean_return"]
        )
        > 0.0,
        "confirmation_top2_double_cost_nonnegative": float(
            confirmation_candidate["mean_stress_return"]
        )
        >= 0.0,
        "confirmation_return_lift_ci_lower_vs_pfill_positive": float(
            confirmation_pfill["return"]["ci95_low"]
        )
        > 0.0,
        "confirmation_profit_lift_ci_lower_vs_pfill_positive": float(
            confirmation_pfill["profit_rate"]["ci95_low"]
        )
        > 0.0,
        "confirmation_return_lift_ci_lower_all_baselines_positive": all(
            float(item["return"]["ci95_low"]) > 0.0
            for item in confirmation_panel["paired_lifts"].values()
        ),
        "confirmation_profit_lift_ci_lower_all_baselines_positive": all(
            float(item["profit_rate"]["ci95_low"]) > 0.0
            for item in confirmation_panel["paired_lifts"].values()
        ),
        "confirmation_stage_support": all(
            int(item["matured_rows"]) >= 50 for item in confirmation_stages.values()
        ),
        "confirmation_stage_return_not_materially_negative": all(
            item["mean_slot_return"] is not None
            and float(item["mean_slot_return"]) >= -0.002
            for item in confirmation_stages.values()
        ),
        "append_only_forward_shadow_180_dates": False,
        "actual_order_fill_observed": False,
        "blocked_limit_down_exit_truth_available": False,
    }
    historical_names = [
        name
        for name in gates
        if name
        not in {
            "append_only_forward_shadow_180_dates",
            "actual_order_fill_observed",
            "blocked_limit_down_exit_truth_available",
        }
    ]
    return {
        "schema_version": SCHEMA,
        "candidate": candidate,
        "status": "NOT_READY",
        "shadow_only": True,
        "front_end_allowed": False,
        "official_trade_action_allowed": False,
        "partial_prototype_historical_checks_passed": all(
            gates[name] for name in historical_names
        ),
        "development_was_used_for_research_comparison_only": True,
        "retrospective_confirmation_is_not_forward_release_evidence": True,
        "why_not_ready": [name for name, passed in gates.items() if not passed],
        "configuration": asdict(config),
        "probability_contract": {
            "identity": "q=P(frozen_fill_proxy)*P(profit_given_fill_proxy)",
            "final_post_product_calibrator": None,
            "p_fill_is_model_feature": False,
            "p_fill_statistics_merged": False,
            "product_identity_passed": product_identity,
            "component_upper_bounds_passed": bounds,
        },
        "development": development_report,
        "retrospective_confirmation": confirmation_report,
        "folds": audits,
        "prototype_checks": gates,
        "prototype_check_pass_count": int(
            sum(bool(value) for value in gates.values())
        ),
        "prototype_check_total_count": len(gates),
        "prototype_check_count_is_completion_percentage": False,
    }


def load_inputs(
    ledger_path: Path,
    manifest_path: Path,
    oof_path: Path,
    calendar_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    actual = {
        "ledger": sha256_path(ledger_path),
        "oof": sha256_path(oof_path),
        "calendar": sha256_path(calendar_path),
    }
    expected = {
        "ledger": EXPECTED_LEDGER_SHA256,
        "oof": EXPECTED_OOF_SHA256,
        "calendar": EXPECTED_CALENDAR_SHA256,
    }
    if actual != expected:
        raise ValueError(f"v2 exact input hash gate failed: {actual}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("output", {}).get("sha256") != EXPECTED_LEDGER_SHA256:
        raise ValueError("ledger manifest output hash drifted")
    if (
        manifest.get("inputs", {}).get("promotion_oof_top10", {}).get("sha256")
        != EXPECTED_OOF_SHA256
    ):
        raise ValueError("ledger manifest promotion OOF hash drifted")
    if (
        manifest.get("inputs", {}).get("strict_sse_calendar", {}).get("sha256")
        != EXPECTED_CALENDAR_SHA256
    ):
        raise ValueError("ledger manifest SSE calendar hash drifted")
    features = manifest.get("feature_contract", {}).get("columns")
    if not isinstance(features, list) or canonical_sha256(features) != EXPECTED_FEATURES_SHA256:
        raise ValueError("v2 feature inventory hash drifted")
    forbidden = {
        "promotion_rank",
        "predicted_promotion_probability",
        "p_fill_shadow_probability",
        "predicted_profit_probability",
        "predicted_big_loss_probability",
    }
    if set(features).intersection(forbidden):
        raise ValueError("frozen upstream output leaked into conditional model features")

    ledger = pd.read_csv(
        ledger_path,
        dtype={
            "signal_date": "string",
            "exec_date": "string",
            "scheduled_exit_date": "string",
            "ts_code": "string",
        },
        low_memory=False,
    )
    frozen = pd.read_csv(
        oof_path,
        usecols=[
            "signal_date",
            "ts_code",
            "p_fill_shadow_probability",
            "p_fill_shadow_rank",
        ],
        dtype={"signal_date": "string", "ts_code": "string"},
        low_memory=False,
    ).rename(
        columns={
            "p_fill_shadow_probability": "frozen_p_fill_probability",
            "p_fill_shadow_rank": "frozen_p_fill_rank",
        }
    )
    if ledger.duplicated(["signal_date", "ts_code"]).any() or frozen.duplicated(
        ["signal_date", "ts_code"]
    ).any():
        raise ValueError("v2 input identity is not unique")
    merged = ledger.merge(
        frozen,
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )
    return merged, manifest, [str(value) for value in features]


def run(
    *,
    ledger_path: Path,
    manifest_path: Path,
    oof_path: Path,
    calendar_path: Path,
    output_dir: Path,
    candidates: Iterable[str],
    config: V2Config,
) -> dict[str, Any]:
    ledger, manifest, features = load_inputs(
        ledger_path, manifest_path, oof_path, calendar_path
    )
    open_dates = open_dates_from_calendar(calendar_path)
    reports: dict[str, Any] = {}
    for candidate in candidates:
        oof, audits = walkforward(
            ledger, features, open_dates, candidate, config
        )
        oof = freeze_top2(oof)
        report = evaluate(oof, audits, candidate, config)
        export_columns = [
            "signal_date",
            "exec_date",
            "scheduled_exit_date",
            "ts_code",
            "stage",
            "stage_transition",
            "promotion_rank",
            "top10_members_sha256",
            "public_market_buyable_proxy",
            "conditional_return_bucket",
            "executable_profit_proxy_hit",
            "strategy_slot_net_return",
            "strategy_slot_net_return_2x_cost",
            "outcome_status",
            "frozen_p_fill_probability",
            "frozen_p_fill_rank",
            "predicted_conditional_big_loss_probability",
            "predicted_conditional_non_profit_probability",
            "predicted_conditional_profit_probability",
            "predicted_conditional_mean_net_return",
            "expected_net_return_lcb",
            "predicted_executable_net_profit_probability",
            "fold_baseline_executable_probability",
            "executable_profit_shadow_rank",
            "shadow_slot",
            "v2_oof_fold",
            "v2_candidate",
            "v2_test_start",
            "v2_test_end",
            "v2_fit_end",
            "v2_component_end",
            "v2_final_audit_end",
        ]
        exported = oof[export_columns].sort_values(
            ["signal_date", "executable_profit_shadow_rank", "ts_code"],
            kind="stable",
        )
        artifact_path = output_dir / f"oof_{candidate}.csv.gz"
        atomic_gzip_csv(artifact_path, exported)
        report["oof_artifact"] = {
            "path": artifact_path.name,
            "sha256": sha256_path(artifact_path),
            "rows": int(len(exported)),
            "dates": int(exported["signal_date"].nunique()),
        }
        reports[candidate] = report

    development_leader = max(
        reports,
        key=lambda name: float(
            reports[name]["development"]["common_mature_panel"]["paired_lifts"][
                "frozen_p_fill_top2"
            ]["return"]["mean"]
        ),
    )
    combined = {
        "schema_version": SCHEMA,
        "status": "NOT_READY",
        "shadow_only": True,
        "front_end_allowed": False,
        "official_trade_action_allowed": False,
        "promotion_model_touched": False,
        "development_leader": development_leader,
        "development_leader_is_not_production_selection": True,
        "retrospective_window_has_been_viewed": True,
        "independent_untouched_confirmation_available": False,
        "inputs": {
            "ledger": {
                "path": display_path(ledger_path),
                "sha256": sha256_path(ledger_path),
            },
            "promotion_oof_and_frozen_p_fill": {
                "path": display_path(oof_path),
                "sha256": sha256_path(oof_path),
                "role": "upstream probability component and separate same-date baseline only",
                "is_model_feature": False,
                "statistics_merged": False,
            },
            "strict_sse_calendar": {
                "path": display_path(calendar_path),
                "sha256": sha256_path(calendar_path),
            },
            "feature_columns": features,
            "feature_columns_sha256": manifest["feature_contract"][
                "columns_sha256"
            ],
        },
        "candidates": reports,
    }
    atomic_json(output_dir / "validation_report.json", combined)
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--output-dir", type=Path, default=WORK / "outputs")
    parser.add_argument(
        "--candidates",
        default="lr_distribution,hgb_distribution",
        help="comma-separated fixed candidates; no tuning is performed",
    )
    args = parser.parse_args()
    candidates = tuple(
        value.strip() for value in args.candidates.split(",") if value.strip()
    )
    result = run(
        ledger_path=args.ledger.resolve(),
        manifest_path=args.manifest.resolve(),
        oof_path=args.oof.resolve(),
        calendar_path=args.calendar.resolve(),
        output_dir=args.output_dir.resolve(),
        candidates=candidates,
        config=V2Config(),
    )
    summary = {
        "status": result["status"],
        "development_leader": result["development_leader"],
        "candidates": {
            name: {
                "prototype_check_score_not_completion_percentage": (
                    f"{item['prototype_check_pass_count']}/"
                    f"{item['prototype_check_total_count']}"
                ),
                "partial_prototype_historical_checks_passed": item[
                    "partial_prototype_historical_checks_passed"
                ],
                "confirmation_top2_return": item["retrospective_confirmation"][
                    "common_mature_panel"
                ]["policies"]["executable_profit_top2"]["mean_return"],
                "confirmation_return_lift_vs_pfill": item[
                    "retrospective_confirmation"
                ]["common_mature_panel"]["paired_lifts"]["frozen_p_fill_top2"][
                    "return"
                ],
                "not_ready": item["why_not_ready"],
            }
            for name, item in result["candidates"].items()
        },
    }
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
