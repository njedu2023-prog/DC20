"""Isolated, predeclared path-feature ablation. Never writes production models.

Archived D-only path columns are joined by D/code, not by future outcome.
Historical results are development evidence, never untouched/forward evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
KEY = ["signal_date", "ts_code"]
PATH = ["path_strength_latest", "path_strength_delta", "path_gap_slope",
        "path_first_seal_slope", "path_open_times_slope", "path_turnover_slope",
        "path_amount_log_slope", "path_seal_ratio_slope", "path_one_price_ratio"]
LABELS = ["STABLE_STRONG", "WEAK_TO_STRONG", "ACCELERATION_CONSENSUS",
          "STRONG_TO_WEAK", "DIVERGENCE_RESEAL", "MIXED"]
SEED = 20260921
TEST_START, TEST_END = "20251111", "20260814"


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_blob(path, expected):
    raw = Path(path).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected:
        raise ValueError(f"REMOTE_SOURCE_BLOB_MISMATCH: {path}")


def normalize(frame):
    frame = frame.copy()
    frame["signal_date"] = frame.signal_date.astype(str)
    if not frame.signal_date.str.fullmatch(r"\d{8}").all():
        raise ValueError("INVALID_SIGNAL_DATE")
    return frame


def path_panel(root, pins):
    frames, sources = [], []
    fields = KEY + PATH + ["path_label_code", "path_data_coverage", "path_days_observed"]
    for relative, blob in sorted(pins["history_blobs"].items()):
        path = root / relative
        verify_blob(path, blob)
        # Deliberately never load old open-exit targets, T prices or outcomes.
        frame = normalize(pd.read_csv(path, usecols=lambda c: c in fields,
                                      dtype={"signal_date": str}, low_memory=False))
        frames.append(frame.reindex(columns=fields))
        sources.append({"path": relative, "blob": blob, "sha256": digest(path)})
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[frame.signal_date < "20260914"]
    # Conflicting historical reconstructions must not be silently preferred.
    comparable = frame.copy()
    for name in PATH + ["path_data_coverage", "path_days_observed"]:
        comparable[name] = pd.to_numeric(comparable[name], errors="coerce").round(9)
    variation = comparable.groupby(KEY, dropna=False).nunique(dropna=False)
    conflicts = variation.index[variation.gt(1).any(axis=1)]
    indexed = frame.drop_duplicates(KEY).set_index(KEY)
    indexed = indexed.drop(index=conflicts)
    good = ((indexed.path_data_coverage >= .75) & (indexed.path_days_observed >= 2)
            & indexed[PATH].notna().all(axis=1))
    indexed.loc[~good, PATH] = np.nan
    for label in LABELS:
        indexed["label_" + label] = np.where(good, (indexed.path_label_code == label).astype(float), np.nan)
    return indexed.reset_index(), {"source_files": sources, "conflicting_keys_excluded": len(conflicts),
        "unique_keys": len(indexed), "usable_keys": int(good.sum()),
        "minimum_weighted_coverage": .75, "all_numeric_path_fields_required": True}


def attach(frame, path):
    if frame.duplicated(KEY).any() or path.duplicated(KEY).any():
        raise ValueError("DUPLICATE_D_CODE")
    columns = PATH + ["label_" + x for x in LABELS]
    result = frame.merge(path[KEY + columns], on=KEY, how="left", validate="one_to_one")
    assert len(result) == len(frame)
    return result


def coverage(frame):
    usable = frame[PATH].notna().all(axis=1)
    return {"rows": len(frame), "dates": frame.signal_date.nunique(),
            "usable_path_rows": int(usable.sum()), "usable_fraction": float(usable.mean()),
            "complete_path_dates": int(usable.groupby(frame.signal_date).all().sum())}


def ranked(frame, scores):
    result = frame.copy()
    result["score"] = scores
    result = result.sort_values(["signal_date", "score", "ts_code"], ascending=[True, False, True])
    result["rank"] = result.groupby("signal_date").cumcount() + 1
    return result


def metrics(frame, target):
    out = {"dates": frame.signal_date.nunique(), "rows": len(frame)}
    if target == "promotion":
        out.update(auc=float(roc_auc_score(frame.promotion_hit, frame.score)),
                   brier=float(brier_score_loss(frame.promotion_hit, frame.score)))
        for rank in (1, 2, 3):
            rows = frame[frame["rank"] == rank]
            out[f"top{rank}"] = {"n": len(rows), "hit_rate": float(rows.promotion_hit.mean())}
    else:
        for rank in (1, 2):
            rows = frame[frame["rank"] == rank]
            fills = rows[rows.proxy_fill == 1]
            out[f"top{rank}"] = {"n": len(rows), "filled": len(fills),
                "no_fill": int((rows.proxy_fill == 0).sum()),
                "mean_slot_net": float(rows.slot_net_return.mean()),
                "mean_filled_net": float(fills.slot_net_return.mean()),
                "win_rate": float((fills.slot_net_return > 0).mean()),
                "worst_filled_net": float(fills.slot_net_return.min()),
                "mean_filled_net_cost_90bp": float(fills.slot_net_return.mean() - .0045)}
    return out


def paired_difference(base, enhanced, target):
    """Paired five-day moving-block bootstrap; not an independent holdout claim."""
    field = "promotion_hit" if target == "promotion" else "slot_net_return"
    out = {}
    for rank in ((1, 2, 3) if target == "promotion" else (1, 2)):
        a = base[base["rank"] == rank].set_index("signal_date")[field]
        b = enhanced[enhanced["rank"] == rank].set_index("signal_date")[field]
        pair = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna().sort_index()
        delta = (pair.b - pair.a).to_numpy()
        rng = np.random.default_rng(SEED)
        estimates = []
        for _ in range(2000):
            starts = rng.integers(0, len(delta), size=(len(delta) + 4) // 5)
            ids = ((starts[:, None] + np.arange(5)) % len(delta)).ravel()[:len(delta)]
            estimates.append(float(delta[ids].mean()))
        out[f"top{rank}"] = {"paired_dates": len(delta), "mean_delta": float(delta.mean()),
            "block_bootstrap_95_interval": np.quantile(estimates, [.025, .975]).tolist()}
    return out


def evaluate_variants(frames, target):
    dates = sorted(frames["A"].signal_date.unique())
    report = {name: metrics(frame, target) for name, frame in frames.items()}
    periods = []
    for block in np.array_split(dates, 3):
        periods.append({"start": str(block[0]), "end": str(block[-1]), "variants": {
            name: metrics(frame[frame.signal_date.isin(block)], target) for name, frame in frames.items()}})
    return {"overall": report, "periods": periods,
            "B_minus_A": paired_difference(frames["A"], frames["B"], target),
            "C_minus_A": paired_difference(frames["A"], frames["C"], target)}


def promotion(root, path, pins):
    ledger_path = root / "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
    validation_path = root / "models/decision_three_engines/validation_latest.json"
    verify_blob(ledger_path, pins["promotion_ledger_blob"])
    verify_blob(validation_path, pins["promotion_validation_blob"])
    ledger = normalize(pd.read_csv(ledger_path))
    for stage in (2, 3):
        ledger[f"stage_{stage}"] = (ledger.stage == stage).astype(float)
    ledger["board_sh_main"] = ledger.ts_code.str.endswith(".SH").astype(float)
    ledger["board_sz_main"] = ledger.ts_code.str.endswith(".SZ").astype(float)
    ledger = attach(ledger, path)
    train = ledger[(ledger.signal_date < "20250901") & (ledger.buy_date.astype(str) < "20250901")]
    calibration = ledger[(ledger.signal_date >= "20250901") & (ledger.signal_date < "20251107")]
    test = ledger[(ledger.signal_date >= TEST_START) & (ledger.signal_date <= TEST_END)]
    base = read_json(validation_path)["source"]["feature_columns"]
    frames = {}
    for name, extra in (("A", []), ("B", ["label_" + x for x in LABELS]), ("C", PATH)):
        columns = base + extra
        model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            HistGradientBoostingClassifier(learning_rate=.04, max_iter=160, max_leaf_nodes=15,
                min_samples_leaf=25, l2_regularization=1., random_state=20260823,
                early_stopping=False))
        model.fit(train[columns], train.promotion_hit)
        calibrator = LogisticRegression(C=1., max_iter=2000)
        def logits(data):
            p = np.clip(model.predict_proba(data[columns])[:, 1], 1e-6, 1-1e-6)
            return np.log(p/(1-p)).reshape(-1, 1)
        calibrator.fit(logits(calibration), calibration.promotion_hit)
        frames[name] = ranked(test, calibrator.predict_proba(logits(test))[:, 1])
    return {"baseline_kind": "MATCHED_HGB_PLATT_REFIT_NOT_FROZEN_PRODUCTION_ARTIFACT",
            "train": coverage(train), "calibration": coverage(calibration), "test": coverage(test),
            **evaluate_variants(frames, "promotion")}, frames


def profit(labels_path, evaluation_path, path, pins):
    verify_blob(evaluation_path, pins["profit_evaluation_blob"])
    evaluation = read_json(evaluation_path)
    model = evaluation["candidate_model"]
    expected = model["input_bindings"]["label_report_sha256"]
    if digest(labels_path) != expected:
        raise ValueError("PROFIT_LABELS_NOT_BOUND_TO_ACTIVE_MODEL")
    labels = read_json(labels_path)
    if labels["label_policy_id"] != "dc20_exit_1000_limit_hold_20260912_v1":
        raise ValueError("WRONG_EXIT_POLICY")
    rows = []
    for row in labels["rows"]:
        f = dict(row["features"])
        f.update({k: row.get(k) for k in KEY + ["promotion_rank", "proxy_fill", "slot_net_return",
                                                "label_available_date", "cohort_complete"]})
        # These signals were absent from the registered baseline panel.
        f.update(promotion_probability=np.nan, path_change=np.nan)
        for feature in model["features"]:
            if feature.startswith("path_") and feature not in f:
                f[feature] = 0.
        rows.append(f)
    frame = normalize(pd.DataFrame(rows))
    if frame.duplicated(KEY).any():
        raise ValueError("DUPLICATE_PROFIT_LABEL")
    mature = frame.slot_net_return.notna() & frame.label_available_date.notna() & frame.cohort_complete
    frame["eligible_train"] = mature & (frame.label_available_date < TEST_START)
    complete_train = frame.eligible_train.groupby(frame.signal_date).all()
    complete_test = mature.groupby(frame.signal_date).all()
    # Rename absent baseline signals so adding real paths cannot change A.
    frame = frame.rename(columns={"path_change": "baseline_path_change"})
    frame = attach(frame, path)
    train = frame[(frame.signal_date < TEST_START) & frame.signal_date.map(complete_train)]
    test = frame[(frame.signal_date >= TEST_START) & (frame.signal_date <= TEST_END)
                 & frame.signal_date.map(complete_test)]
    if len(train) != 5173 or len(test) != 1548:
        raise ValueError(f"REGISTERED_COHORT_CHANGED:{len(train)}/{len(test)}")
    columns = ["baseline_path_change" if c == "path_change" else c for c in model["features"]]
    def baseline_matrix(data):
        raw = data[columns].to_numpy(dtype=float)
        raw = np.where(np.isnan(raw), np.asarray(model["imputation_train_medians"]), raw)
        return (raw - np.asarray(model["scaling_train_means"])) / np.asarray(model["scaling_train_scales"])
    xtrain, xtest = baseline_matrix(train), baseline_matrix(test)
    baseline = xtest @ np.asarray(model["coefficients"]) + model["intercept"]
    expected_scores = pd.DataFrame(evaluation["predictions"])[KEY + ["candidate_score"]]
    aligned = test[KEY].merge(expected_scores, on=KEY, validate="one_to_one")
    error = float(np.max(np.abs(baseline-aligned.candidate_score.to_numpy())))
    if error > 1e-8:
        raise ValueError(f"BASELINE_SCORE_REPRODUCTION_FAILED:{error}")
    refit = Ridge(alpha=10., solver="svd").fit(xtrain, train.slot_net_return)
    refit_error = float(np.max(np.abs(refit.predict(xtest)-baseline)))
    if refit_error > 1e-8:
        raise ValueError(f"BASELINE_TRAINING_REPRODUCTION_FAILED:{refit_error}")
    frames = {"A": ranked(test, baseline)}
    for name, extra in (("B", ["label_" + x for x in LABELS]), ("C", PATH)):
        transform = make_pipeline(SimpleImputer(strategy="median", add_indicator=True,
                                                keep_empty_features=True), StandardScaler())
        etrain, etest = transform.fit_transform(train[extra]), transform.transform(test[extra])
        candidate = Ridge(alpha=10., solver="svd").fit(np.column_stack([xtrain, etrain]), train.slot_net_return)
        frames[name] = ranked(test, candidate.predict(np.column_stack([xtest, etest])))
    return {"baseline_kind": "EXACT_ACTIVE_RIDGE_REPRODUCED", "baseline_score_max_error": error,
            "baseline_refit_max_error": refit_error, "label_report_sha256": expected,
            "exit_policy_id": labels["label_policy_id"], "round_trip_cost_rate": .0045,
            "train": coverage(train), "test": coverage(test), **evaluate_variants(frames, "profit")}, frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--profit-labels", type=Path, required=True)
    parser.add_argument("--profit-evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("FRESH_OUTPUT_DIRECTORY_REQUIRED")
    pins = read_json(HERE / "source_pins.json")
    path, audit = path_panel(args.history_root, pins)
    print(json.dumps({"path_audit": {k:v for k,v in audit.items() if k != "source_files"}}), flush=True)
    profit_report, profit_frames = profit(args.profit_labels, args.profit_evaluation, path, pins)
    print("Profit A/B/C fitted; exact baseline reproduced", flush=True)
    promotion_report, promotion_frames = promotion(args.model_root, path, pins)
    report = {"schema": "dc20_path_ablation_v1", "source_commit": pins["source_commit"],
        "research_only": True, "production_activation_allowed": False,
        "independent_improvement_proven": False, "forward_records_consumed": False,
        "historical_role": "PREVIOUSLY_INSPECTED_DEVELOPMENT_ONLY",
        "path_evidence": "ARCHIVED_D_FEATURES_HASH_VERIFIED_NOT_RAW_SOURCE_REPLAY",
        "variants": {"A": "baseline", "B": "baseline_plus_rule_labels", "C": "baseline_plus_numeric_paths"},
        "test_start": TEST_START, "test_end": TEST_END,
        "script_sha256": digest(__file__), "path_audit": audit,
        "promotion": promotion_report, "profit": profit_report}
    args.output.mkdir(parents=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    for name, frames in (("promotion", promotion_frames), ("profit", profit_frames)):
        for variant, frame in frames.items():
            wanted = KEY + ["score", "rank"] + (["promotion_hit"] if name == "promotion" else ["proxy_fill", "slot_net_return"])
            frame[wanted].to_csv(args.output / f"{name}_{variant}_predictions.csv", index=False)
    print(json.dumps({"output":str(args.output), "production_changed":False}), flush=True)


if __name__ == "__main__":
    main()
