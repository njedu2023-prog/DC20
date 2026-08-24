from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCHEMA = "dc20_executable_profit_lagged_prior_benchmark_v1"
RANDOM_STATE = 20260824
DEVELOPMENT_DATES = 180
CONFIRMATION_DATES = 180
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_BLOCK_DATES = 5


class BenchmarkError(ValueError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    # bool is an int subclass; preserve contract booleans before integer coercion.
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if value is pd.NA:
        return None
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _deterministic_gzip(frame: pd.DataFrame) -> bytes:
    text = io.StringIO(newline="")
    frame.to_csv(text, index=False, lineterminator="\n", na_rep="", float_format="%.17g")
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as handle:
        handle.write(text.getvalue().encode("utf-8"))
    return output.getvalue()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(value, dict), f"JSON object required: {path}")
    return value


def _model(kind: str) -> Pipeline:
    if kind == "lr":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.20,
                        max_iter=3000,
                        solver="lbfgs",
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        )
    if kind == "hgb":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.04,
                        max_iter=120,
                        max_leaf_nodes=15,
                        min_samples_leaf=40,
                        l2_regularization=1.0,
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        )
    raise BenchmarkError(f"unknown model kind: {kind}")


@dataclass
class TwoStageModel:
    fill: Pipeline
    conditional_profit: Pipeline
    feature_columns: list[str]
    training_audit: dict[str, Any]

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = frame[self.feature_columns]
        p_fill = np.asarray(self.fill.predict_proba(x)[:, 1], dtype=float)
        p_profit = np.asarray(self.conditional_profit.predict_proba(x)[:, 1], dtype=float)
        p_joint = np.clip(p_fill, 0, 1) * np.clip(p_profit, 0, 1)
        return np.clip(p_fill, 0, 1), np.clip(p_profit, 0, 1), np.clip(p_joint, 0, 1)


def fit_two_stage(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    kind: str,
    label_available_before: str,
) -> TwoStageModel:
    available = label_available_mask(frame, label_available_before)
    fill_y = pd.to_numeric(frame["public_market_buyable_proxy"], errors="coerce")
    profit_y = pd.to_numeric(frame["conditional_profit_hit"], errors="coerce")
    fill_sample = frame.loc[available & fill_y.notna()].copy()
    profit_sample = frame.loc[available & fill_y.eq(1) & profit_y.notna()].copy()
    _expect(len(fill_sample) >= 3000, "fill training sample too small")
    _expect(len(profit_sample) >= 2500, "conditional-profit training sample too small")
    _expect(fill_y.loc[fill_sample.index].nunique() == 2, "fill target is constant")
    _expect(profit_y.loc[profit_sample.index].nunique() == 2, "profit target is constant")
    fill_model = _model(kind)
    profit_model = _model(kind)
    fill_model.fit(fill_sample[feature_columns], fill_y.loc[fill_sample.index].astype(int))
    profit_model.fit(
        profit_sample[feature_columns],
        profit_y.loc[profit_sample.index].astype(int),
    )
    max_exit = str(frame.loc[available, "scheduled_exit_date"].astype(str).max())
    _expect(max_exit < label_available_before, "training label cutoff leaked")
    return TwoStageModel(
        fill_model,
        profit_model,
        feature_columns,
        {
            "cutoff_exclusive": label_available_before,
            "maximum_used_scheduled_exit_date": max_exit,
            "fill_rows": int(len(fill_sample)),
            "conditional_profit_rows": int(len(profit_sample)),
        },
    )


def label_available_mask(frame: pd.DataFrame, cutoff_exclusive: str) -> pd.Series:
    _expect(len(cutoff_exclusive) == 8 and cutoff_exclusive.isdigit(), "invalid label cutoff")
    values = frame["scheduled_exit_date"].astype(str)
    _expect(values.str.fullmatch(r"20\d{6}").all(), "invalid scheduled exit date")
    return values.lt(cutoff_exclusive)


def score_panel(
    frame: pd.DataFrame,
    model: TwoStageModel,
    *,
    variant: str,
    kind: str,
    split: str,
) -> pd.DataFrame:
    output = frame.copy()
    p_fill, p_profit, p_joint = model.predict(output)
    output["predicted_fill_probability"] = p_fill
    output["predicted_profit_given_fill_probability"] = p_profit
    output["predicted_executable_profit_probability"] = p_joint
    _expect(
        np.allclose(p_joint, p_fill * p_profit, rtol=0, atol=1e-15),
        "joint probability lost the two-stage identity",
    )
    output["variant"] = variant
    output["model_kind"] = kind
    output["split"] = split
    return output


def _complete_date_panel(frame: pd.DataFrame, dates: Sequence[str]) -> list[str]:
    sample = frame[frame["signal_date"].astype(str).isin(dates)]
    complete = sample.groupby("signal_date", sort=True).agg(
        joint_complete=("executable_profit_proxy_hit", lambda value: pd.to_numeric(value, errors="coerce").notna().all()),
        return_complete=("strategy_slot_net_return", lambda value: pd.to_numeric(value, errors="coerce").notna().all()),
    )
    return complete.index[complete["joint_complete"] & complete["return_complete"]].astype(str).tolist()


def probability_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    y = pd.to_numeric(frame["executable_profit_proxy_hit"], errors="coerce").to_numpy(dtype=float)
    p = pd.to_numeric(frame["predicted_executable_profit_probability"], errors="coerce").to_numpy(dtype=float)
    p = np.clip(p, 1e-9, 1 - 1e-9)
    fill_y = pd.to_numeric(frame["public_market_buyable_proxy"], errors="coerce")
    fill_p = pd.to_numeric(frame["predicted_fill_probability"], errors="coerce")
    cond_mask = fill_y.eq(1) & pd.to_numeric(frame["conditional_profit_hit"], errors="coerce").notna()
    cond_y = pd.to_numeric(frame.loc[cond_mask, "conditional_profit_hit"], errors="coerce").to_numpy(dtype=float)
    cond_p = pd.to_numeric(frame.loc[cond_mask, "predicted_profit_given_fill_probability"], errors="coerce").to_numpy(dtype=float)
    return {
        "rows": int(len(frame)),
        "dates": int(frame["signal_date"].nunique()),
        "joint_event_rate": float(np.mean(y)),
        "joint_brier": float(np.mean((p - y) ** 2)),
        "joint_log_loss": float(log_loss(y.astype(int), p, labels=[0, 1])),
        "fill_brier": float(np.mean((fill_p.to_numpy(dtype=float) - fill_y.to_numpy(dtype=float)) ** 2)),
        "conditional_profit_brier": float(np.mean((cond_p - cond_y) ** 2)),
    }


def top2_daily(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for signal_date, group in frame.groupby("signal_date", sort=True):
        selected = _top2_for_date(group)
        returns = pd.to_numeric(selected["strategy_slot_net_return"], errors="coerce")
        events = pd.to_numeric(selected["executable_profit_proxy_hit"], errors="coerce")
        _expect(returns.notna().all() and events.notna().all(), "Top2 truth panel incomplete")
        # Fixed two slots: when the candidate pool has only one row, the absent slot is cash.
        rows.append(
            {
                "signal_date": str(signal_date),
                "daily_top2_net_return": float(returns.sum() / 2.0),
                "daily_top2_profit_rate": float(events.sum() / 2.0),
                "selected_candidates": int(len(selected)),
                "cash_slots": int(2 - len(selected)),
            }
        )
    return pd.DataFrame(rows)


def _top2_for_date(group: pd.DataFrame) -> pd.DataFrame:
    return group.sort_values(
        [
            "predicted_executable_profit_probability",
            "predicted_fill_probability",
            "predicted_profit_given_fill_probability",
            "ts_code",
        ],
        ascending=[False, False, False, True],
        kind="stable",
    ).head(2)


def top2_risk_cost_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    selected_parts = [
        _top2_for_date(group)
        for _, group in frame.groupby("signal_date", sort=True)
    ]
    selected = pd.concat(selected_parts, ignore_index=True)
    date_count = int(frame["signal_date"].nunique())
    fixed_slots = date_count * 2
    _expect(len(selected) <= fixed_slots, "Top2 selected more than two rows per date")

    base_return = pd.to_numeric(selected["strategy_slot_net_return"], errors="coerce")
    stress_return = pd.to_numeric(selected["strategy_slot_net_return_2x_cost"], errors="coerce")
    fill = pd.to_numeric(selected["public_market_buyable_proxy"], errors="coerce")
    profit = pd.to_numeric(selected["executable_profit_proxy_hit"], errors="coerce")
    conditional_big_loss = pd.to_numeric(selected["conditional_big_loss_hit"], errors="coerce")
    _expect(base_return.notna().all(), "base-cost Top2 truth incomplete")
    _expect(stress_return.notna().all(), "double-cost Top2 truth incomplete")
    _expect(fill.notna().all() and profit.notna().all(), "Top2 event truth incomplete")
    _expect(conditional_big_loss.loc[fill.eq(1)].notna().all(), "buyable Top2 big-loss truth incomplete")
    strategy_big_loss = conditional_big_loss.where(fill.eq(1), 0.0)

    stage_breakdown: dict[str, Any] = {}
    for stage, group in selected.assign(
        _base_return=base_return,
        _stress_return=stress_return,
        _fill=fill,
        _profit=profit,
        _big_loss=strategy_big_loss,
    ).groupby("stage", sort=True):
        label = "2_to_3" if int(stage) == 2 else "3_to_4"
        stage_breakdown[label] = {
            "selected_candidates": int(len(group)),
            "mean_selected_candidate_net_return_base_cost": float(group["_base_return"].mean()),
            "mean_selected_candidate_net_return_double_cost": float(group["_stress_return"].mean()),
            "selected_candidate_profit_rate": float(group["_profit"].mean()),
            "selected_candidate_big_loss_rate": float(group["_big_loss"].mean()),
            "selected_candidate_fill_rate": float(group["_fill"].mean()),
        }

    return {
        "dates": date_count,
        "fixed_slots": fixed_slots,
        "selected_candidates": int(len(selected)),
        "cash_slots": int(fixed_slots - len(selected)),
        "mean_daily_top2_net_return_base_cost": float(base_return.sum() / fixed_slots),
        "mean_daily_top2_net_return_double_cost": float(stress_return.sum() / fixed_slots),
        "top2_slot_profit_rate": float(profit.sum() / fixed_slots),
        "top2_slot_big_loss_rate": float(strategy_big_loss.sum() / fixed_slots),
        "top2_slot_fill_rate": float(fill.sum() / fixed_slots),
        "stage_breakdown_selected_candidates": stage_breakdown,
    }


def policy_metrics(daily: pd.DataFrame) -> dict[str, float | int]:
    values = daily["daily_top2_net_return"].to_numpy(dtype=float)
    wealth = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(np.r_[1.0, wealth])
    drawdown = np.r_[1.0, wealth] / peak - 1.0
    return {
        "dates": int(len(daily)),
        "selected_candidates": int(daily["selected_candidates"].sum()),
        "cash_slots": int(daily["cash_slots"].sum()),
        "mean_daily_top2_net_return": float(np.mean(values)),
        "top2_slot_profit_rate": float(daily["daily_top2_profit_rate"].mean()),
        "compounded_top2_net_return": float(wealth[-1] - 1.0) if len(wealth) else 0.0,
        "maximum_drawdown": float(np.min(drawdown)),
    }


def block_bootstrap_ci(values: np.ndarray, *, seed: int) -> dict[str, float | int]:
    values = np.asarray(values, dtype=float)
    _expect(len(values) >= 20, "bootstrap panel too short")
    rng = np.random.default_rng(seed)
    block = min(BOOTSTRAP_BLOCK_DATES, len(values))
    block_count = int(math.ceil(len(values) / block))
    maximum_start = len(values) - block
    estimates = np.empty(BOOTSTRAP_SAMPLES, dtype=float)
    for index in range(BOOTSTRAP_SAMPLES):
        starts = rng.integers(0, maximum_start + 1, size=block_count)
        sample = np.concatenate([values[start : start + block] for start in starts])[: len(values)]
        estimates[index] = float(np.mean(sample))
    return {
        "estimate": float(np.mean(values)),
        "ci95_low": float(np.quantile(estimates, 0.025)),
        "ci95_high": float(np.quantile(estimates, 0.975)),
        "samples": BOOTSTRAP_SAMPLES,
        "block_dates": block,
    }


def compare_to_base(candidate: dict[str, Any], base: dict[str, Any], *, seed: int) -> dict[str, Any]:
    candidate_daily = candidate["daily"].sort_values("signal_date").reset_index(drop=True)
    base_daily = base["daily"].sort_values("signal_date").reset_index(drop=True)
    _expect(candidate_daily["signal_date"].equals(base_daily["signal_date"]), "comparison date panel drifted")
    return_diff = (
        candidate_daily["daily_top2_net_return"].to_numpy(dtype=float)
        - base_daily["daily_top2_net_return"].to_numpy(dtype=float)
    )
    profit_diff = (
        candidate_daily["daily_top2_profit_rate"].to_numpy(dtype=float)
        - base_daily["daily_top2_profit_rate"].to_numpy(dtype=float)
    )
    return {
        "joint_brier_improvement": float(
            base["probability"]["joint_brier"] - candidate["probability"]["joint_brier"]
        ),
        "top2_return_lift": block_bootstrap_ci(return_diff, seed=seed),
        "top2_profit_rate_lift": block_bootstrap_ci(profit_diff, seed=seed + 1),
    }


def evaluate_scored(frame: pd.DataFrame, complete_dates: Sequence[str]) -> dict[str, Any]:
    panel = frame[frame["signal_date"].astype(str).isin(complete_dates)].copy()
    probability = probability_metrics(panel)
    daily = top2_daily(panel)
    policy = policy_metrics(daily)
    risk_cost = top2_risk_cost_diagnostics(panel)
    return {
        "probability": probability,
        "policy": policy,
        "risk_cost_diagnostics": risk_cost,
        "daily": daily,
    }


def load_frame(repo_root: Path, work_root: Path) -> tuple[pd.DataFrame, dict[str, list[str]], dict[str, Any]]:
    ledger_manifest = _read_json(
        repo_root / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
    )
    ledger_path = repo_root / ledger_manifest["output"]["path"]
    _expect(_sha256(ledger_path) == ledger_manifest["output"]["sha256"], "ledger SHA drifted")
    frame = pd.read_csv(ledger_path, low_memory=False, dtype={"signal_date": str, "scheduled_exit_date": str, "ts_code": str})
    prior_manifest = _read_json(work_root / "outputs/lagged_priors_manifest.json")
    prior_columns: dict[str, list[str]] = {}
    for source_kind, prefix in (("full", "fullhist"), ("top10", "top10hist")):
        info = prior_manifest["outputs"][source_kind]
        path = work_root / "outputs" / info["path"]
        _expect(_sha256(path) == info["sha256"], f"{source_kind} prior SHA drifted")
        priors = pd.read_csv(path, low_memory=False, dtype={"signal_date": str, "ts_code": str})
        _expect(not priors.duplicated(["signal_date", "ts_code"]).any(), "prior keys duplicated")
        frame = frame.merge(
            priors.drop(columns=["promotion_rank", "lagged_prior_max_history_exit_date", "lagged_prior_snapshot_sha256"]),
            on=["signal_date", "ts_code"],
            how="left",
            validate="one_to_one",
        )
        prior_columns[source_kind] = list(info["feature_columns"])
    _expect(frame[prior_columns["full"] + prior_columns["top10"]].notna().all().all(), "prior join incomplete")
    base_columns = list(ledger_manifest["feature_contract"]["columns"])
    forbidden = {
        "promotion_rank",
        "predicted_promotion_probability",
        "conditional_profit_hit",
        "public_market_buyable_proxy",
        "executable_profit_proxy_hit",
        "strategy_slot_net_return",
    }
    _expect(not (set(base_columns) & forbidden), "baseline feature leakage")
    variants = {
        "base": base_columns,
        "full_priors": [*base_columns, *prior_columns["full"]],
        "top10_priors": [*base_columns, *prior_columns["top10"]],
        "both_priors": [*base_columns, *prior_columns["full"], *prior_columns["top10"]],
    }
    for columns in variants.values():
        _expect(len(columns) == len(set(columns)), "duplicate model feature")
    return frame, variants, {
        "ledger_sha256": _sha256(ledger_path),
        "prior_manifest_sha256": _sha256(work_root / "outputs/lagged_priors_manifest.json"),
        "base_feature_columns_sha256": _canonical_sha256(base_columns),
        "variant_feature_counts": {name: len(columns) for name, columns in variants.items()},
        "variant_feature_columns_sha256": {name: _canonical_sha256(columns) for name, columns in variants.items()},
    }


def run(repo_root: Path, work_root: Path) -> dict[str, Any]:
    frame, variants, provenance = load_frame(repo_root.resolve(), work_root.resolve())
    dates = sorted(frame["signal_date"].astype(str).unique())
    _expect(len(dates) >= 800, "insufficient signal dates")
    confirm_dates = dates[-CONFIRMATION_DATES:]
    dev_dates = dates[-(CONFIRMATION_DATES + DEVELOPMENT_DATES) : -CONFIRMATION_DATES]
    dev_start = dev_dates[0]
    confirm_start = confirm_dates[0]
    _expect(dev_dates[-1] < confirm_start, "split overlap")
    complete = {
        "development": _complete_date_panel(frame, dev_dates),
        "confirmation": _complete_date_panel(frame, confirm_dates),
    }
    _expect(len(complete["development"]) >= 170 and len(complete["confirmation"]) >= 170, "mature panel too small")

    scored_outputs: list[pd.DataFrame] = []
    evaluations: dict[str, dict[str, Any]] = {"development": {}, "confirmation": {}}
    training_audits: dict[str, dict[str, Any]] = {"development": {}, "confirmation": {}}
    for split, split_dates, cutoff in (
        ("development", dev_dates, dev_start),
        ("confirmation", confirm_dates, confirm_start),
    ):
        panel = frame[frame["signal_date"].astype(str).isin(split_dates)].copy()
        for kind in ("lr", "hgb"):
            for variant, columns in variants.items():
                model = fit_two_stage(
                    frame,
                    feature_columns=columns,
                    kind=kind,
                    label_available_before=cutoff,
                )
                scored = score_panel(panel, model, variant=variant, kind=kind, split=split)
                training_audits[split][f"{kind}:{variant}"] = model.training_audit
                scored_outputs.append(
                    scored[
                        [
                            "split",
                            "model_kind",
                            "variant",
                            "signal_date",
                            "ts_code",
                            "stage",
                            "stage_transition",
                            "promotion_rank",
                            "predicted_fill_probability",
                            "predicted_profit_given_fill_probability",
                            "predicted_executable_profit_probability",
                            "public_market_buyable_proxy",
                            "conditional_profit_hit",
                            "executable_profit_proxy_hit",
                            "conditional_big_loss_hit",
                            "strategy_slot_net_return",
                            "strategy_slot_net_return_2x_cost",
                        ]
                    ]
                )
                evaluations[split][f"{kind}:{variant}"] = evaluate_scored(scored, complete[split])

    comparisons: dict[str, dict[str, Any]] = {"development": {}, "confirmation": {}}
    for split in ("development", "confirmation"):
        for kind in ("lr", "hgb"):
            base = evaluations[split][f"{kind}:base"]
            for index, variant in enumerate(("full_priors", "top10_priors", "both_priors")):
                comparisons[split][f"{kind}:{variant}"] = compare_to_base(
                    evaluations[split][f"{kind}:{variant}"],
                    base,
                    seed=RANDOM_STATE + index * 10 + (0 if kind == "lr" else 100),
                )

    decisions: dict[str, Any] = {}
    for key, confirm in comparisons["confirmation"].items():
        dev = comparisons["development"][key]
        dev_direction = (
            dev["joint_brier_improvement"] > 0
            and dev["top2_return_lift"]["estimate"] > 0
            and dev["top2_profit_rate_lift"]["estimate"] > 0
        )
        confirm_strict = (
            confirm["joint_brier_improvement"] > 0
            and confirm["top2_return_lift"]["ci95_low"] > 0
            and confirm["top2_profit_rate_lift"]["ci95_low"] > 0
        )
        decisions[key] = {
            "development_all_directions_positive": bool(dev_direction),
            "confirmation_strictly_improved": bool(confirm_strict),
            "decision": "RESEARCH_SUPPORT" if dev_direction and confirm_strict else "REJECT_NOT_CONFIRMED",
        }

    scored_all = pd.concat(scored_outputs, ignore_index=True).sort_values(
        ["split", "model_kind", "variant", "signal_date", "promotion_rank", "ts_code"],
        kind="stable",
    )
    scored_payload = _deterministic_gzip(scored_all)
    predictions_path = work_root / "outputs/benchmark_predictions.csv.gz"
    _atomic_bytes(predictions_path, scored_payload)

    def strip_daily(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip_daily(item) for key, item in value.items() if key != "daily"}
        return value

    report = {
        "schema_version": SCHEMA,
        "status": "RESEARCH_ONLY_NO_RELEASE",
        "objective": "P(fill proxy) * P(net profit after cost > 0 | fill proxy)",
        "joint_probability_identity_enforced": True,
        "official_trade_action_allowed": False,
        "runtime_dependency_on_recovery": False,
        "runtime_dependency_on_top10_decision": False,
        "retrospective_confirmation_window_has_been_viewed": True,
        "independent_untouched_confirmation_available": False,
        "forward_release_evidence_available": False,
        "retrospective_declared_primary": "lr:full_priors",
        "secondary_sensitivity": "hgb:full_priors",
        "splits": {
            "development": {
                "start": dev_dates[0],
                "end": dev_dates[-1],
                "calendar_dates": len(dev_dates),
                "complete_common_panel_dates": len(complete["development"]),
                "training_label_rule": f"scheduled_exit_date < {dev_start}",
            },
            "confirmation": {
                "start": confirm_dates[0],
                "end": confirm_dates[-1],
                "calendar_dates": len(confirm_dates),
                "complete_common_panel_dates": len(complete["confirmation"]),
                "training_label_rule": f"scheduled_exit_date < {confirm_start}",
                "evidence_role": "retrospective_exploration_only_not_forward_confirmation",
            },
        },
        "model_contract": {
            "kinds": ["lr", "hgb"],
            "two_independent_components": ["fill", "profit_given_fill"],
            "fixed_top2_slots": 2,
            "missing_candidate_slot_return": 0.0,
            "no_post_outcome_replacement": True,
            "same_common_mature_date_panel_for_all_variants": True,
            "bootstrap": {
                "samples": BOOTSTRAP_SAMPLES,
                "block_dates": BOOTSTRAP_BLOCK_DATES,
                "unit": "signal_date",
            },
        },
        "provenance": provenance,
        "evaluations": strip_daily(evaluations),
        "training_audits": training_audits,
        "comparisons_to_same_kind_base": comparisons,
        "decisions": decisions,
        "predictions": {
            "path": str(predictions_path.relative_to(work_root)),
            "sha256": hashlib.sha256(scored_payload).hexdigest(),
            "rows": int(len(scored_all)),
        },
    }
    payload = (
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    _atomic_bytes(work_root / "outputs/benchmark_report.json", payload)
    report["report_sha256"] = hashlib.sha256(payload).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        report = run(args.repo_root, args.work_root)
    except (BenchmarkError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "report_sha256": report["report_sha256"],
                "splits": report["splits"],
                "decisions": report["decisions"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
