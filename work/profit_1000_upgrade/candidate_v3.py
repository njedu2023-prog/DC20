"""Fixed v3 research candidate, with explicit dual-source label provenance.

The source-verifying runner must supply the bound D-only panel and fresh label
report. This pure library verifies their declared identities, digests and label
contracts; it does not independently turn a receipt SHA into source authority.
No v1/v2 label is renamed, no historical holdout is reclassified as untouched,
and no output, production model, selection or ledger is written here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from work.profit_1000_upgrade import candidate as legacy
from work.profit_1000_upgrade import candidate_labels_overlay as overlay, policy_v3

SCHEMA = "dc20_profit_1000_candidate_v3"
SOURCE_SHA = "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
BASE_ARCHIVE_SHA = "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
LEGACY_SHA = "3fa606d40decbf080ba02e2ce99faad4d1ea7acab258db75658e6b07cfbca794"
AS_OF, TRAIN_CUTOFF, VALIDATION_END, HOLDOUT_START = "20260911", "20251111", "20260814", "20260914"
D_START, ROW_COUNT, DAY_COUNT = "20221111", 6753, 910
GATES = {"min_train_dates": 252, "min_train_rows": 1000, "min_validation_dates": 60,
         "min_validation_rows": 300, "min_validation_filled_rows": 50,
         "min_training_complete_day_fraction": .95, "min_validation_complete_day_fraction": 1.0}
MODEL_SPEC = {"estimator": "Ridge", "alpha": 10.0, "fit_intercept": True,
              "solver": "svd", "target": "slot_net_return", "hyperparameter_search": False}
_HERE = Path(__file__).resolve().parent
_SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_OVERLAY_SHA = hashlib.sha256(Path(overlay.__file__).read_bytes()).hexdigest()
_SHA_FIELDS = ("label_report_sha256", "candidate_collection_receipt_sha256",
               "development_frozen_rows_sha256", "development_label_rows_sha256")


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _guard():
    for path, digest in ((Path(__file__), _SELF_SHA), (_HERE / "candidate.py", LEGACY_SHA),
                         (Path(overlay.__file__), _OVERLAY_SHA)):
        _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                 and hashlib.sha256(path.read_bytes()).hexdigest() == digest, "CANDIDATE_V3_CODE_CHANGED")
    _require(legacy.DEFAULT_GATES == GATES and legacy.MODEL_SPEC == MODEL_SPEC, "FROZEN_MODEL_OR_GATES_CHANGED")
    overlay._guard()


def _manifest(manifest):
    _require(type(manifest) is dict, "BOUND_FROZEN_MANIFEST_REQUIRED")
    _require(manifest.get("source_sha256") == SOURCE_SHA and manifest.get("base_archive_sha256") == BASE_ARCHIVE_SHA,
             "REGISTERED_FEATURE_OR_BASE_ARCHIVE_SHA_CHANGED")
    _require(manifest.get("source_provenance_verified") is True
             and manifest.get("feature_timestamp_semantics") == "RETROSPECTIVE_D_ONLY_BOUND"
             and manifest.get("promotion_prediction_provenance") == "HISTORICAL_OOF",
             "SOURCE_VERIFIED_D_ONLY_OOF_FEATURES_REQUIRED")
    _require(manifest.get("entry_policy_id") == policy_v3.ENTRY_POLICY_ID and manifest.get("cost_rate") == .0045,
             "REGISTERED_V3_ENTRY_AND_45BP_COST_REQUIRED")
    _require(type(manifest.get("source_overlay_contract")) is dict
             and canonical_sha(manifest["source_overlay_contract"]) == canonical_sha(overlay.CONTRACT),
             "EXACT_DUAL_SOURCE_OVERLAY_CONTRACT_REQUIRED")
    _require("source_policy_contract" not in manifest and not any(
        isinstance(k, str) and k.endswith("source_policy_id") for k in manifest), "NO_LEGACY_OR_SINGLE_SOURCE_CONTRACT_PROJECTION")
    for key in _SHA_FIELDS:
        _require(type(manifest.get(key)) is str and re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is not None,
                 "EXPLICIT_SHA_REQUIRED:" + key)
    _require("market_collection_receipt_sha256" in manifest, "EXPLICIT_MARKET_RECEIPT_OR_NONE_REQUIRED")
    market_sha = manifest["market_collection_receipt_sha256"]
    _require(market_sha is None or type(market_sha) is str and re.fullmatch(r"[0-9a-f]{64}", market_sha),
             "INVALID_MARKET_COLLECTION_RECEIPT_SHA")
    counts = manifest.get("day_candidate_counts")
    _require(type(counts) is dict, "FROZEN_DAY_COUNTS_REQUIRED")
    for day, count in counts.items():
        legacy._date(day, "manifest_day")
        _require(type(count) is int and count > 0, "INVALID_FROZEN_DAY_COUNT")
    development = {d: n for d, n in counts.items() if d < HOLDOUT_START}
    _require(len(development) == DAY_COUNT and sum(development.values()) == ROW_COUNT
             and min(development) == D_START and max(development) == VALIDATION_END,
             "REGISTERED_6753_ROWS_910_DATES_CHANGED")
    return development


def _prepare(frozen_rows, label_rows, manifest):
    expected = _manifest(manifest)
    frozen, future, ignored = {}, set(), 0
    for supplied in frozen_rows:
        key = legacy._key(supplied)
        _require(key not in frozen and key not in future, "DUPLICATE_FROZEN_KEY")
        if key[0] >= HOLDOUT_START:
            future.add(key)
            ignored += 1
            continue  # Do not copy/hash/read feature or outcome fields here.
        frozen[key] = supplied
    labels, future_labels = {}, set()
    for supplied in label_rows:
        key = legacy._key(supplied)
        _require(key not in labels and key not in future_labels, "DUPLICATE_LABEL_KEY")
        if key[0] >= HOLDOUT_START:
            _require(key in future, "HOLDOUT_LABEL_WITHOUT_FROZEN_IDENTITY")
            future_labels.add(key)
            continue  # No future return, status, price or source qualification inspected.
        _require(key in frozen, "LABEL_OUTSIDE_FROZEN_UNIVERSE")
        labels[key] = supplied
    _require(set(labels) == set(frozen), "ALL_FROZEN_CANDIDATES_REQUIRE_EXPLICIT_LABEL_OR_PENDING_ROW")
    _require(canonical_sha(list(frozen.values())) == manifest["development_frozen_rows_sha256"]
             and canonical_sha(list(labels.values())) == manifest["development_label_rows_sha256"],
             "DEVELOPMENT_INPUT_CONTENT_SHA_MISMATCH")
    by_day = defaultdict(list)
    for key, supplied in frozen.items():
        row = dict(supplied)
        _require(key[0] <= AS_OF and legacy._date(row.get("feature_as_of_date"), "feature_as_of_date") <= key[0],
                 "FEATURE_NOT_KNOWN_BY_SIGNAL_DAY")
        _require(legacy._date(row.get("promotion_oof_train_end"), "promotion_oof_train_end") < key[0],
                 "PROMOTION_OOF_TRAINED_ON_SIGNAL_OR_FUTURE")
        _require(type(row.get("board_stage")) is int and row["board_stage"] in (2, 3), "ONLY_2_TO_3_AND_3_TO_4_ALLOWED")
        row["promotion_rank"] = legacy._positive_rank(row.get("promotion_rank"), "promotion_rank")
        for feature in legacy.NUMERIC_FEATURES:
            row[feature] = legacy._number(row.get(feature), feature, nullable=True)
        _require(row["promotion_probability"] is None or 0 <= row["promotion_probability"] <= 1,
                 "PROMOTION_PROBABILITY_NOT_FRACTION")
        _require(row.get("path_label") is None or row["path_label"] in legacy.PATHS, "UNKNOWN_PATH_LABEL")
        rank = row.get("existing_profit_rank")
        row["existing_profit_rank"] = None if rank in (None, "") else legacy._positive_rank(rank, "existing_profit_rank")
        label = labels[key]
        overlay.validate_label_contract(label)
        _require(label.get("label_policy_id") == legacy.POLICY_ID and label.get("entry_policy_id") == policy_v3.ENTRY_POLICY_ID
                 and label.get("round_trip_cost_rate") == .0045, "LABEL_EXIT_ENTRY_OR_COST_MISMATCH")
        t, t1 = legacy._date(label.get("exec_date"), "exec_date"), legacy._date(label.get("scheduled_exit_date"), "scheduled_exit_date")
        _require(key[0] < t < t1, "LABEL_D_T_T1_ORDER_INVALID")
        status = label["label_status"]
        no_fill, terminal = status in policy_v3.NO_FILL_STATUSES, status == legacy.SETTLED or status in policy_v3.NO_FILL_STATUSES
        row.update(terminal=False, slot_net_return=None, proxy_fill=None, actual_exit_date=None, label_available_date=None)
        if terminal:
            available = legacy._date(label.get("label_available_date"), "label_available_date")
            _require(available >= t, "LABEL_AVAILABLE_BEFORE_ENTRY_SESSION")
            if no_fill:
                _require(label.get("actual_exit_date") is None, "NO_FILL_HAS_EXIT_DATE")
            else:
                actual_exit = legacy._date(label.get("actual_exit_date"), "actual_exit_date")
                _require(t1 <= actual_exit <= available, "ACTUAL_EXIT_BEFORE_T1_OR_AFTER_AVAILABILITY")
                row["actual_exit_date"] = actual_exit
            row.update(terminal=available <= AS_OF,
                       slot_net_return=legacy._number(label["slot_net_return"], "slot_net_return") if available <= AS_OF else None,
                       proxy_fill=label["proxy_fill"] if available <= AS_OF else None, label_available_date=available)
        by_day[key[0]].append(row)
    _require(set(by_day) == set(expected), "FROZEN_DATE_SET_MISMATCH")
    for day, rows in by_day.items():
        _require(len(rows) == expected[day] and sorted(r["promotion_rank"] for r in rows) == list(range(1, len(rows) + 1)),
                 "INCOMPLETE_FROZEN_DAY_OR_PROMOTION_RANKS:" + day)
        known = [r["existing_profit_rank"] for r in rows if r["existing_profit_rank"] is not None]
        _require(len(known) == len(set(known)), "DUPLICATE_FROZEN_PROFIT_RANK:" + day)
    return [r for day in sorted(by_day) for r in sorted(by_day[day], key=lambda r: r["ts_code"])], ignored


def _quality(rows):
    train, validation = defaultdict(list), defaultdict(list)
    for row in rows:
        (train if row["signal_date"] < TRAIN_CUTOFF else validation)[row["signal_date"]].append(row)
    complete_train = {d: group for d, group in train.items() if all(
        r["terminal"] and r["label_available_date"] < TRAIN_CUTOFF
        and (r["actual_exit_date"] is None or r["actual_exit_date"] < TRAIN_CUTOFF) for r in group)}
    complete_validation = {d: group for d, group in validation.items() if all(r["terminal"] for r in group)}
    training = [r for d in sorted(complete_train) for r in complete_train[d]]
    validating = [r for d in sorted(complete_validation) for r in complete_validation[d]]
    coverage = {"training_expected_dates": len(train), "training_complete_dates": len(complete_train),
        "training_complete_rows": len(training), "training_complete_day_fraction": len(complete_train) / len(train) if train else 0.,
        "training_purged_or_incomplete_dates": sorted(set(train) - set(complete_train)),
        "validation_expected_dates": len(validation), "validation_complete_dates": len(complete_validation),
        "validation_complete_rows": len(validating), "validation_complete_day_fraction": len(complete_validation) / len(validation) if validation else 0.,
        "validation_incomplete_dates": sorted(set(validation) - set(complete_validation)),
        "validation_filled_rows": sum(r["proxy_fill"] == 1 for r in validating)}
    observed = {"min_train_dates": len(complete_train), "min_train_rows": len(training),
        "min_validation_dates": len(complete_validation), "min_validation_rows": len(validating),
        "min_validation_filled_rows": coverage["validation_filled_rows"],
        "min_training_complete_day_fraction": coverage["training_complete_day_fraction"],
        "min_validation_complete_day_fraction": coverage["validation_complete_day_fraction"]}
    failures = [k for k in GATES if observed[k] < GATES[k]]
    if len({r["slot_net_return"] for r in training}) < 2:
        failures.append("TRAINING_TARGET_HAS_NO_VARIATION")
    if any(len(group) < 2 for group in complete_validation.values()):
        failures.append("VALIDATION_DAY_HAS_FEWER_THAN_TWO_FROZEN_CANDIDATES")
    if len(complete_validation) != len(validation):
        failures.append("INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED")
    return training, validating, coverage, failures


def run_candidate_v3(frozen_rows, label_rows, *, frozen_manifest, as_of_date=AS_OF,
                     training_cutoff_date=TRAIN_CUTOFF, validation_end_date=VALIDATION_END,
                     holdout_start_date=HOLDOUT_START):
    """Fit the registered candidate only; no gate or hyperparameter overrides."""
    report = {"schema_version": SCHEMA, "label_policy_id": legacy.POLICY_ID, "entry_policy_id": policy_v3.ENTRY_POLICY_ID,
        "research_only": True, "production_activation_allowed": False, "training_performed": False, "training_attempted": False,
        "source_verification": "CALLER_VERIFIED_REPORT_AND_RECEIPTS_REQUIRED_NOT_INDEPENDENT_SOURCE_REPLAY",
        "historical_validation_role": "DEVELOPMENT_PREVIOUSLY_INSPECTED_NOT_UNTOUCHED",
        "holdout_role": "FUTURE_FORWARD_ONLY_NOT_EVALUATED", "holdout_start_date": HOLDOUT_START, "holdout_metrics": None,
        "profitability_improvement_proven": False, "engineering_gates_are_profit_guarantee": False,
        "top1_top2_always_selected": True, "negative_score_skip_allowed": False,
        "actual_execution_claimed": False, "engineering_defaults_weakened": False,
        "quality_gates": dict(GATES), "round_trip_cost_rate": .0045, "source_overlay_contract": deepcopy(overlay.CONTRACT),
        "provider_timestamp_semantics_confirmed": False,
        "entry_policy_production_compatibility": "ENTRY_POLICY_DIFFERENT_FROM_FORMAL_FROZEN_CAP",
        "risk_note": "Posthoc minute/auction price proxies do not prove fill, capacity or portfolio NAV."}
    result = {"status": "BLOCKED_INPUT", "report": report, "candidate_model": None, "predictions": []}
    try:
        _guard()
        _require((as_of_date, training_cutoff_date, validation_end_date, holdout_start_date)
                 == (AS_OF, TRAIN_CUTOFF, VALIDATION_END, HOLDOUT_START), "REGISTERED_DATE_WINDOWS_CANNOT_MOVE")
        rows, ignored = _prepare(frozen_rows, label_rows, frozen_manifest)
        bindings = {key: frozen_manifest[key] for key in ("source_sha256", "base_archive_sha256", *_SHA_FIELDS,
                                                         "market_collection_receipt_sha256")}
        report.update(input_bindings=bindings, frozen_source_sha256=SOURCE_SHA,
                      historical_candidate_rows_retained=len(rows), historical_candidate_dates_retained=len({r["signal_date"] for r in rows}),
                      holdout_rows_excluded_without_outcome_evaluation=ignored,
                      market_evidence_scope="BASE_ONLY" if bindings["market_collection_receipt_sha256"] is None else "CALLER_VERIFIED_MARKET_SUPPLEMENT",
                      promotion_combination="FROZEN_HISTORICAL_OOF_RANK_AS_FEATURE_AND_BASELINE; missing probabilities or paths are not backcast.",
                      feature_coverage={feature: {"known_rows": sum(r.get(feature) is not None for r in rows), "rows": len(rows)}
                                        for feature in legacy.NUMERIC_FEATURES + ("path_label",)})
        training, validation, coverage, failures = _quality(rows)
        report.update(coverage=coverage, failed_quality_gates=failures)
        _guard()
        if failures:
            result["status"] = "BLOCKED_DATA_QUALITY"
            return result
        report["training_attempted"] = True
        model, scores = legacy._fit_ridge(training, validation)
        report["training_performed"] = True
        comparisons, improvements, predictions = legacy._compare(validation, scores)
        metadata = {"schema_version": SCHEMA, "entry_policy_id": policy_v3.ENTRY_POLICY_ID,
            "round_trip_cost_rate": .0045, "frozen_source_sha256": SOURCE_SHA,
            "source_overlay_contract": deepcopy(overlay.CONTRACT), "input_bindings": bindings,
            "provider_timestamp_semantics_confirmed": False, "training_cutoff_date": TRAIN_CUTOFF, "holdout_start_date": HOLDOUT_START}
        model.update(metadata)
        for prediction in predictions:
            prediction.update(entry_policy_id=policy_v3.ENTRY_POLICY_ID, label_policy_id=legacy.POLICY_ID,
                              source_overlay_contract_sha256=canonical_sha(overlay.CONTRACT))
        _guard()
        report.update(training_performed=True, training_cutoff_date=TRAIN_CUTOFF,
            validation_start_date=min(r["signal_date"] for r in validation), validation_end_date=max(r["signal_date"] for r in validation),
            comparisons=comparisons, development_mean_slot_return_deltas=improvements,
            model_specification=dict(MODEL_SPEC), additional_fees_already_in_labels=True,
            release_readiness="NOT_ELIGIBLE_UNTOUCHED_FORWARD_HOLDOUT_REQUIRED")
        result.update(status="DEVELOPMENT_ONLY_FITTED", candidate_model=model, predictions=predictions)
    except (ValueError, TypeError, KeyError) as exc:
        report["input_error"] = str(exc)
    return result
