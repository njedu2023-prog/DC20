from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .calibration import ProbabilityCalibrator, fit_probability_calibrator


PROMOTION_PRIOR_FEATURES = [
    "five_year_stage_board_prior_rate",
    "five_year_stage_prior_rate",
    "five_year_recent_20d_rate",
    "five_year_recent_60d_rate",
    "five_year_prior_samples_log",
    "five_year_recent_60d_samples_log",
    "five_year_regime_delta",
    "five_year_board_stage_delta",
]

PROMOTION_CONTEXT_FEATURES = [
    "five_year_pre_streak_1d_return",
    "five_year_pre_streak_3d_return",
    "five_year_pre_streak_volatility",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "five_year_streak_runup",
    "five_year_price_log",
    "five_year_stock_prior_rate",
    "five_year_stock_prior_samples_log",
]

PROMOTION_SOURCE_FEATURES = PROMOTION_PRIOR_FEATURES + PROMOTION_CONTEXT_FEATURES
CALIBRATION_METHODS = ("identity", "platt", "beta", "isotonic")
MODEL_KINDS = ("lr", "hgb", "extra_trees", "pairwise_lr")


def load_promotion_validation(root: Path) -> dict[str, Any]:
    path = Path(root) / "models" / "decision_promotion_v13_validation.json"
    if not path.is_file():
        return {"validated": False, "reason": "validation_artifact_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"validated": False, "reason": "validation_artifact_invalid"}
    checks = payload.get("gate_checks") or {}
    comparison = payload.get("comparison") or {}
    bootstrap = (payload.get("bootstrap") or {}).get("brier_improvement") or {}
    source = payload.get("source") or {}
    required_checks = (
        "strict_oos_dates_at_least_500",
        "brier_improvement_positive",
        "challenger_ece_not_worse_and_at_most_8pct",
        "auc_improvement_positive",
        "top1_hit_rate_improvement_positive",
        "top3_row_hit_rate_improvement_positive",
        "top3_any_hit_rate_improvement_positive",
        "brier_bootstrap_lower_bound_positive",
    )
    validated = bool(
        payload.get("direct_promotion_pass") is True
        and all(checks.get(name) is True for name in required_checks)
        and int(source.get("oos_dates") or (payload.get("challenger") or {}).get("dates") or 0)
        >= 500
        and float(bootstrap.get("ci95_low") or 0.0) > 0.0
        and float(comparison.get("top1_hit_rate_improvement") or 0.0) > 0.0
        and float(comparison.get("top3_row_hit_rate_improvement") or 0.0) > 0.0
    )
    return {
        "validated": validated,
        "reason": "strict_oos_gate_passed" if validated else "strict_oos_gate_failed",
        "artifact": str(path.relative_to(Path(root))),
        "oos_dates": int(
            source.get("oos_dates")
            or (payload.get("challenger") or {}).get("dates")
            or 0
        ),
        "comparison": comparison,
        "bootstrap": bootstrap,
    }


def _normal_date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        left, right = text.split(".", 1)
        return f"{left.zfill(6)}.{right}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        return text
    digits = digits[:6]
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def _board_from_code(value: Any) -> str:
    code = _normal_code(value)
    digits = code.split(".", 1)[0]
    if digits.startswith(("300", "301")):
        return "CHINEXT"
    if digits.startswith("688"):
        return "STAR"
    if digits.startswith(("8", "4", "92")):
        return "BSE"
    return "SH_MAIN" if code.endswith(".SH") else "SZ_MAIN"


def _stage_value(frame: pd.DataFrame) -> pd.Series:
    if "limit_times" in frame.columns:
        stage = pd.to_numeric(frame["limit_times"], errors="coerce")
    else:
        stage = frame.get("stage", pd.Series(index=frame.index, dtype=object)).map(
            lambda value: str(value or "").replace("→", "->").split("->", 1)[0]
        )
        stage = pd.to_numeric(stage, errors="coerce")
    return stage.round()


@lru_cache(maxsize=8)
def _read_prior_tables(root_text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = Path(root_text)
    prior_root = root / "data" / "auction_v3" / "promotion_prior"
    daily_path = prior_root / "five_year_daily_stage_board.csv"
    event_path = prior_root / "five_year_event_features.csv.gz"
    if not daily_path.exists() or not event_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    daily = pd.read_csv(daily_path, low_memory=False)
    events = pd.read_csv(event_path, low_memory=False)
    daily["signal_date"] = daily["signal_date"].map(_normal_date)
    daily["stage"] = pd.to_numeric(daily["stage"], errors="coerce").round()
    events["signal_date"] = events["signal_date"].map(_normal_date)
    events["ts_code"] = events["ts_code"].map(_normal_code)
    events["stage"] = pd.to_numeric(events["stage"], errors="coerce").round()
    return daily, events


def _prior_grid(daily: pd.DataFrame, dates: Sequence[str]) -> pd.DataFrame:
    source_dates = sorted(daily["signal_date"].astype(str).unique())
    all_dates = sorted(set(source_dates).union(str(value) for value in dates))
    stages = (2.0, 3.0)
    boards = ("SH_MAIN", "SZ_MAIN")
    grid = pd.MultiIndex.from_product(
        [all_dates, stages, boards], names=["signal_date", "stage", "board"]
    ).to_frame(index=False)
    grid = grid.merge(daily, on=["signal_date", "stage", "board"], how="left")
    grid[["samples", "hits"]] = grid[["samples", "hits"]].fillna(0.0)

    global_daily = daily.groupby("signal_date", as_index=False).agg(
        global_samples=("samples", "sum"), global_hits=("hits", "sum")
    )
    global_grid = pd.DataFrame({"signal_date": all_dates}).merge(
        global_daily, on="signal_date", how="left"
    ).fillna({"global_samples": 0.0, "global_hits": 0.0})
    global_grid["global_samples_prior"] = (
        global_grid["global_samples"].cumsum().shift(1).fillna(0.0)
    )
    global_grid["global_hits_prior"] = (
        global_grid["global_hits"].cumsum().shift(1).fillna(0.0)
    )
    global_grid["global_prior_rate"] = (
        global_grid["global_hits_prior"] + 1.0
    ) / (global_grid["global_samples_prior"] + 2.0)

    stage_daily = daily.groupby(["signal_date", "stage"], as_index=False).agg(
        stage_samples=("samples", "sum"), stage_hits=("hits", "sum")
    )
    stage_grid = pd.MultiIndex.from_product(
        [all_dates, stages], names=["signal_date", "stage"]
    ).to_frame(index=False)
    stage_grid = stage_grid.merge(stage_daily, on=["signal_date", "stage"], how="left")
    stage_grid[["stage_samples", "stage_hits"]] = stage_grid[
        ["stage_samples", "stage_hits"]
    ].fillna(0.0)
    stage_grid = stage_grid.sort_values(["stage", "signal_date"])
    stage_grid["stage_samples_prior"] = stage_grid.groupby("stage")["stage_samples"].transform(
        lambda values: values.cumsum().shift(1).fillna(0.0)
    )
    stage_grid["stage_hits_prior"] = stage_grid.groupby("stage")["stage_hits"].transform(
        lambda values: values.cumsum().shift(1).fillna(0.0)
    )
    stage_grid = stage_grid.merge(
        global_grid[["signal_date", "global_prior_rate"]], on="signal_date", how="left"
    )
    stage_grid["five_year_stage_prior_rate"] = (
        stage_grid["stage_hits_prior"] + 20.0 * stage_grid["global_prior_rate"]
    ) / (stage_grid["stage_samples_prior"] + 20.0)

    grid = grid.sort_values(["stage", "board", "signal_date"])
    grouped = grid.groupby(["stage", "board"], sort=False)
    grid["prior_samples"] = grouped["samples"].transform(
        lambda values: values.cumsum().shift(1).fillna(0.0)
    )
    grid["prior_hits"] = grouped["hits"].transform(
        lambda values: values.cumsum().shift(1).fillna(0.0)
    )
    for window in (20, 60):
        grid[f"recent_{window}_samples"] = grouped["samples"].transform(
            lambda values, window=window: values.shift(1).rolling(window, min_periods=1).sum()
        ).fillna(0.0)
        grid[f"recent_{window}_hits"] = grouped["hits"].transform(
            lambda values, window=window: values.shift(1).rolling(window, min_periods=1).sum()
        ).fillna(0.0)
    grid = grid.merge(
        stage_grid[["signal_date", "stage", "five_year_stage_prior_rate"]],
        on=["signal_date", "stage"],
        how="left",
    )
    grid["five_year_stage_board_prior_rate"] = (
        grid["prior_hits"] + 20.0 * grid["five_year_stage_prior_rate"]
    ) / (grid["prior_samples"] + 20.0)
    grid["five_year_recent_20d_rate"] = (
        grid["recent_20_hits"] + 10.0 * grid["five_year_stage_board_prior_rate"]
    ) / (grid["recent_20_samples"] + 10.0)
    grid["five_year_recent_60d_rate"] = (
        grid["recent_60_hits"] + 15.0 * grid["five_year_stage_board_prior_rate"]
    ) / (grid["recent_60_samples"] + 15.0)
    grid["five_year_prior_samples_log"] = np.log1p(grid["prior_samples"])
    grid["five_year_recent_60d_samples_log"] = np.log1p(grid["recent_60_samples"])
    grid["five_year_regime_delta"] = (
        grid["five_year_recent_60d_rate"] - grid["five_year_stage_board_prior_rate"]
    )
    grid["five_year_board_stage_delta"] = (
        grid["five_year_stage_board_prior_rate"] - grid["five_year_stage_prior_rate"]
    )
    return grid[["signal_date", "stage", "board", *PROMOTION_PRIOR_FEATURES]]


def attach_promotion_source_features(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    if frame.empty:
        return frame
    daily, events = _read_prior_tables(str(Path(root).resolve()))
    output = frame.copy()
    for feature in PROMOTION_SOURCE_FEATURES:
        if feature not in output.columns:
            output[feature] = np.nan
    if daily.empty or events.empty:
        return output

    output["_promotion_date"] = output["signal_date"].map(_normal_date)
    output["_promotion_code"] = output["ts_code"].map(_normal_code)
    output["_promotion_stage"] = _stage_value(output)
    output["_promotion_board"] = output["ts_code"].map(_board_from_code)
    priors = _prior_grid(daily, output["_promotion_date"].unique()).rename(
        columns={
            "signal_date": "_promotion_date",
            "stage": "_promotion_stage",
            "board": "_promotion_board",
            **{
                feature: f"{feature}_prior_source"
                for feature in PROMOTION_PRIOR_FEATURES
            },
        }
    )
    output = output.merge(
        priors,
        on=["_promotion_date", "_promotion_stage", "_promotion_board"],
        how="left",
    )
    for feature in PROMOTION_PRIOR_FEATURES:
        source = f"{feature}_prior_source"
        if source in output.columns:
            output[feature] = pd.to_numeric(output[feature], errors="coerce").fillna(
                pd.to_numeric(output[source], errors="coerce")
            )

    exact = events[["signal_date", "ts_code", *PROMOTION_CONTEXT_FEATURES]].rename(
        columns={
            "signal_date": "_promotion_date",
            "ts_code": "_promotion_code",
            **{feature: f"{feature}_event_source" for feature in PROMOTION_CONTEXT_FEATURES},
        }
    )
    output = output.merge(exact, on=["_promotion_date", "_promotion_code"], how="left")
    for feature in PROMOTION_CONTEXT_FEATURES:
        source = f"{feature}_event_source"
        output[feature] = pd.to_numeric(output[feature], errors="coerce").fillna(
            pd.to_numeric(output.get(source), errors="coerce")
        )

    # A future live candidate has no exact row in the frozen five-year event
    # table.  Reuse only the latest strictly earlier stock posterior; this is
    # deliberately one event conservative and cannot expose same-day truth.
    stock_features = (
        "five_year_stock_prior_rate",
        "five_year_stock_prior_samples_log",
    )
    missing_stock = output[list(stock_features)].isna().any(axis=1)
    if missing_stock.any():
        stock_history = events[
            ["signal_date", "ts_code", *stock_features]
        ].copy()
        stock_history["signal_date"] = stock_history["signal_date"].map(_normal_date)
        stock_history["ts_code"] = stock_history["ts_code"].map(_normal_code)
        stock_history = stock_history.sort_values(["ts_code", "signal_date"])
        by_code = {
            code: group.reset_index(drop=True)
            for code, group in stock_history.groupby("ts_code", sort=False)
        }
        for row_index in output.index[missing_stock]:
            code = str(output.at[row_index, "_promotion_code"])
            signal_date = str(output.at[row_index, "_promotion_date"])
            history = by_code.get(code)
            if history is None or history.empty:
                continue
            dates = history["signal_date"].to_numpy(dtype=str)
            position = int(np.searchsorted(dates, signal_date, side="left")) - 1
            if position < 0:
                continue
            source_row = history.iloc[position]
            for feature in stock_features:
                if pd.isna(output.at[row_index, feature]):
                    output.at[row_index, feature] = pd.to_numeric(
                        source_row.get(feature), errors="coerce"
                    )
    helper_columns = [
        column
        for column in output.columns
        if column.startswith("_promotion_")
        or column.endswith("_prior_source")
        or column.endswith("_event_source")
    ]
    return output.drop(columns=helper_columns, errors="ignore")


def date_balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    dates = frame["signal_date"].astype(str)
    counts = dates.groupby(dates).transform("count").clip(lower=1)
    weights = 1.0 / counts.astype(float)
    return (weights / weights.mean()).to_numpy(dtype=float)


def _brier(y: np.ndarray, p: np.ndarray, weights: np.ndarray) -> float:
    return float(np.average((p - y) ** 2, weights=weights))


def _ece(y: np.ndarray, p: np.ndarray, weights: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(weights.sum())
    value = 0.0
    for index in range(bins):
        mask = (p >= edges[index]) & (
            (p < edges[index + 1]) if index < bins - 1 else (p <= edges[index + 1])
        )
        if not mask.any():
            continue
        bucket_weight = float(weights[mask].sum())
        value += bucket_weight / total * abs(
            float(np.average(y[mask], weights=weights[mask]))
            - float(np.average(p[mask], weights=weights[mask]))
        )
    return float(value)


class PairwiseLogisticRanker:
    def __init__(self) -> None:
        self.imputer = SimpleImputer(
            strategy="median", add_indicator=True, keep_empty_features=True
        )
        self.scaler = StandardScaler()
        self.model = LogisticRegression(
            C=0.12, fit_intercept=False, max_iter=2_000, random_state=20260815
        )

    def fit(self, features: pd.DataFrame, truth: np.ndarray, dates: Sequence[str]) -> "PairwiseLogisticRanker":
        transformed = self.scaler.fit_transform(self.imputer.fit_transform(features))
        date_values = np.asarray([str(value) for value in dates])
        pairs: list[np.ndarray] = []
        labels: list[int] = []
        weights: list[float] = []
        for date in sorted(set(date_values.tolist())):
            positions = np.flatnonzero(date_values == date)
            positives = positions[truth[positions] == 1]
            negatives = positions[truth[positions] == 0]
            combinations = [(positive, negative) for positive in positives for negative in negatives]
            if not combinations:
                continue
            weight = 1.0 / (2.0 * len(combinations))
            for positive, negative in combinations:
                difference = transformed[positive] - transformed[negative]
                pairs.extend((difference, -difference))
                labels.extend((1, 0))
                weights.extend((weight, weight))
        if not pairs:
            raise ValueError("pairwise promotion model requires mixed-label dates")
        sample_weight = np.asarray(weights, dtype=float)
        sample_weight /= sample_weight.mean()
        self.model.fit(np.asarray(pairs), np.asarray(labels), sample_weight=sample_weight)
        return self

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        transformed = self.scaler.transform(self.imputer.transform(features))
        score = self.model.decision_function(transformed)
        probability = 1.0 / (1.0 + np.exp(-np.clip(score, -30.0, 30.0)))
        return np.column_stack((1.0 - probability, probability))


def _classifier(kind: str) -> Any:
    if kind == "pairwise_lr":
        return PairwiseLogisticRanker()
    if kind == "extra_trees":
        estimator: Any = ExtraTreesClassifier(
            n_estimators=200,
            min_samples_leaf=20,
            max_features=0.70,
            n_jobs=1,
            random_state=20260716,
        )
        return Pipeline(
            [("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]
        )
    if kind == "hgb":
        estimator = HistGradientBoostingClassifier(
            learning_rate=0.045,
            max_iter=160,
            max_leaf_nodes=15,
            min_samples_leaf=25,
            l2_regularization=0.25,
            random_state=20260716,
        )
        return Pipeline(
            [("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]
        )
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=0.20, max_iter=2_000, random_state=20260716)),
        ]
    )


@dataclass
class PromotionBlendModel:
    incumbent_model: Any
    incumbent_calibrator: ProbabilityCalibrator
    incumbent_features: tuple[str, ...]
    challenger_model: Any
    challenger_calibrator: ProbabilityCalibrator
    challenger_features: tuple[str, ...]
    weight: float

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        incumbent = self.incumbent_calibrator.transform(
            self.incumbent_model.predict_proba(frame[list(self.incumbent_features)])[:, 1]
        )
        challenger = self.challenger_calibrator.transform(
            self.challenger_model.predict_proba(frame[list(self.challenger_features)])[:, 1]
        )
        probability = (1.0 - self.weight) * incumbent + self.weight * challenger
        return np.column_stack((1.0 - probability, probability))


@dataclass
class PromotionBlendResult:
    model: Any
    calibrator: ProbabilityCalibrator
    features: tuple[str, ...]
    selection: dict[str, Any]


@dataclass
class _Candidate:
    model: Any
    calibrator: ProbabilityCalibrator
    selection_probability: np.ndarray
    selection_brier: float
    selection_ece: float
    model_kind: str
    calibration_method: str
    feature_name: str
    features: tuple[str, ...]


def fit_promotion_blend(
    *,
    incumbent_model: Any,
    incumbent_calibrator: ProbabilityCalibrator,
    incumbent_features: Sequence[str],
    constant: float,
    fit_frame: pd.DataFrame,
    calibration_frame: pd.DataFrame,
    target: str,
    feature_sets: dict[str, Sequence[str]],
) -> PromotionBlendResult:
    identity = ProbabilityCalibrator("identity", constant)
    incumbent_features = tuple(incumbent_features)
    if incumbent_model is None or len(calibration_frame) < 80:
        return PromotionBlendResult(
            incumbent_model,
            incumbent_calibrator,
            incumbent_features,
            {"selected": "incumbent", "weight": 0.0, "reason": "insufficient_support"},
        )
    calibration_dates = sorted(calibration_frame["signal_date"].astype(str).unique())
    split = max(10, int(math.floor(len(calibration_dates) * 0.55)))
    fit_dates = set(calibration_dates[:split])
    eval_dates = set(calibration_dates[min(split + 1, len(calibration_dates)):])
    calibration_date_key = calibration_frame["signal_date"].astype(str)
    cal_fit = calibration_frame[calibration_date_key.isin(fit_dates)].copy()
    cal_eval = calibration_frame[calibration_date_key.isin(eval_dates)].copy()
    if cal_fit.empty or cal_eval.empty:
        return PromotionBlendResult(
            incumbent_model,
            incumbent_calibrator,
            incumbent_features,
            {"selected": "incumbent", "weight": 0.0, "reason": "empty_nested_calibration"},
        )
    y_fit = fit_frame[target].astype(int).to_numpy()
    y_cal_fit = cal_fit[target].astype(int).to_numpy()
    y_cal_eval = cal_eval[target].astype(int).to_numpy()
    eval_weights = date_balanced_weights(cal_eval)
    fit_weights = date_balanced_weights(fit_frame)

    incumbent_raw_fit = incumbent_model.predict_proba(cal_fit[list(incumbent_features)])[:, 1]
    incumbent_raw_eval = incumbent_model.predict_proba(cal_eval[list(incumbent_features)])[:, 1]
    incumbent_method = getattr(incumbent_calibrator, "method", "identity")
    incumbent_nested_calibrator = fit_probability_calibrator(
        incumbent_method,
        incumbent_raw_fit,
        y_cal_fit,
        sample_weight=date_balanced_weights(cal_fit),
        constant=constant,
    ) or ProbabilityCalibrator("identity", constant)
    incumbent_probability = incumbent_nested_calibrator.transform(incumbent_raw_eval)
    incumbent_brier = _brier(y_cal_eval, incumbent_probability, eval_weights)
    incumbent_ece = _ece(y_cal_eval, incumbent_probability, eval_weights)
    options: list[tuple[float, float, float, Optional[_Candidate]]] = [
        (incumbent_brier, incumbent_ece, 0.0, None)
    ]
    candidate_audit: dict[str, Any] = {}

    for feature_name, raw_features in feature_sets.items():
        features = tuple(feature for feature in raw_features if feature in fit_frame.columns)
        if not features:
            continue
        for kind in MODEL_KINDS:
            model = _classifier(kind)
            try:
                if kind == "pairwise_lr":
                    model.fit(
                        fit_frame[list(features)],
                        y_fit,
                        fit_frame["signal_date"].astype(str).to_numpy(),
                    )
                else:
                    model.fit(
                        fit_frame[list(features)],
                        y_fit,
                        model__sample_weight=fit_weights,
                    )
            except (ValueError, TypeError):
                continue
            raw_fit = model.predict_proba(cal_fit[list(features)])[:, 1]
            raw_eval = model.predict_proba(cal_eval[list(features)])[:, 1]
            calibrators: list[
                tuple[float, float, str, ProbabilityCalibrator]
            ] = []
            for method in CALIBRATION_METHODS:
                calibrator = fit_probability_calibrator(
                    method,
                    raw_fit,
                    y_cal_fit,
                    sample_weight=date_balanced_weights(cal_fit),
                    constant=constant,
                )
                if calibrator is None:
                    continue
                probability = calibrator.transform(raw_eval)
                calibrators.append(
                    (
                        _brier(y_cal_eval, probability, eval_weights),
                        _ece(y_cal_eval, probability, eval_weights),
                        method,
                        calibrator,
                    )
                )
            if not calibrators:
                continue
            _, _, method, nested_calibrator = min(
                calibrators, key=lambda item: (item[0], item[1], item[2])
            )
            full_raw = model.predict_proba(calibration_frame[list(features)])[:, 1]
            production_calibrator = fit_probability_calibrator(
                method,
                full_raw,
                calibration_frame[target].astype(int).to_numpy(),
                sample_weight=date_balanced_weights(calibration_frame),
                constant=constant,
            ) or ProbabilityCalibrator("identity", constant)
            selection_probability = nested_calibrator.transform(raw_eval)
            candidate = _Candidate(
                model=model,
                calibrator=production_calibrator,
                selection_probability=selection_probability,
                selection_brier=_brier(y_cal_eval, selection_probability, eval_weights),
                selection_ece=_ece(y_cal_eval, selection_probability, eval_weights),
                model_kind=kind,
                calibration_method=method,
                feature_name=feature_name,
                features=features,
            )
            key = f"{feature_name}:{kind}+{method}"
            blends: list[dict[str, float]] = []
            for weight in np.linspace(0.1, 1.0, 10):
                blended = (
                    (1.0 - weight) * incumbent_probability
                    + weight * selection_probability
                )
                brier = _brier(y_cal_eval, blended, eval_weights)
                ece = _ece(y_cal_eval, blended, eval_weights)
                blends.append({"weight": float(weight), "brier": brier, "ece": ece})
                options.append((brier, ece, float(weight), candidate))
            candidate_audit[key] = {
                "selection_brier": candidate.selection_brier,
                "selection_ece": candidate.selection_ece,
                "blends": blends,
            }

    best_brier, best_ece, best_weight, best_candidate = min(
        options, key=lambda item: (item[0], item[1], item[2])
    )
    if best_candidate is None or best_weight <= 0.0:
        return PromotionBlendResult(
            incumbent_model,
            incumbent_calibrator,
            incumbent_features,
            {
                "selected": "incumbent",
                "weight": 0.0,
                "reason": "challenger_did_not_improve_nested_calibration",
                "incumbent_brier": incumbent_brier,
                "incumbent_ece": incumbent_ece,
                "candidates": candidate_audit,
            },
        )
    union_features = tuple(
        dict.fromkeys([*incumbent_features, *best_candidate.features])
    )
    model = PromotionBlendModel(
        incumbent_model=incumbent_model,
        incumbent_calibrator=incumbent_calibrator,
        incumbent_features=incumbent_features,
        challenger_model=best_candidate.model,
        challenger_calibrator=best_candidate.calibrator,
        challenger_features=best_candidate.features,
        weight=best_weight,
    )
    return PromotionBlendResult(
        model=model,
        calibrator=identity,
        features=union_features,
        selection={
            "selected": "validated_five_year_promotion_blend",
            "weight": best_weight,
            "challenger_model": best_candidate.model_kind,
            "challenger_calibration": best_candidate.calibration_method,
            "challenger_feature_set": best_candidate.feature_name,
            "nested_calibration_brier": best_brier,
            "nested_calibration_ece": best_ece,
            "incumbent_brier": incumbent_brier,
            "incumbent_ece": incumbent_ece,
            "validation_artifact": "models/decision_promotion_v13_validation.json",
            "candidates": candidate_audit,
        },
    )


__all__ = [
    "PROMOTION_CONTEXT_FEATURES",
    "PROMOTION_PRIOR_FEATURES",
    "PROMOTION_SOURCE_FEATURES",
    "PromotionBlendModel",
    "PromotionBlendResult",
    "attach_promotion_source_features",
    "fit_promotion_blend",
    "load_promotion_validation",
]
