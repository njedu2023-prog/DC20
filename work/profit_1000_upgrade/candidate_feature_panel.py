"""Read-only, SHA-bound D-only feature panel for the isolated v3 candidate.

This verifies the registered feature files and reconstructs the frozen cohort;
it does NOT independently verify a whole source artifact, HTTP receipt, fresh
label report, or any claimed trade. The source-verifying runner supplies those
external authorities. Old ledger outcome columns are never selected as model
inputs. Fresh labels are identity-checked and content-hashed, not evaluated or
used to change the feature population. No fitting, output or network occurs.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import stat

from work.profit_1000_upgrade import research_v3 as research, policy_v3
from work.profit_1000_upgrade import candidate_labels_overlay as overlay

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCHEMA = "dc20_profit_1000_candidate_feature_panel_v3"
BASE_ARCHIVE_SHA = "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
SOURCE_SHA = "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
PINNED = {
    "research_v3.py": "d576e5fc0fd5cadbc5aa4dec71212ca381ae70ade10f9ea1ffdcef075d1097a7",
    "PLAN_V3.json": "7a6f7cbabe850b8429af9a8fbf1ceb05e2c0765119d09e875d19cf71c42957a2",
    "policy_v3.py": "a384365e7200643738c4f03e6f8236cd4ac8984eb879ff69752f5a4de0ad729b",
    "acceptance.py": "f3f9b2efd967f3e28d1b27d043f4ad5b604e4d86a0c2acf45eaf9db8a9cca003",
    "candidate_labels_overlay.py": "1c1aa1acf316e46fd0c86ed7f7310bf36474fc8bff47dcbcf8b9678ac68cc5f3"}
SOURCE_INPUTS = {
    "ledger": {"path": "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz", "sha256": SOURCE_SHA},
    "manifest": {"path": "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
                 "sha256": "3fd457dbe8438b28bbd80d0521ebd9a2ba2d17845be019412238b7898cce69f5"},
    "calendar": {"path": "data/market/trade_cal_sse.csv",
                 "sha256": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"}}
ROWS, DAYS, D_START, D_END = 6753, 910, "20221111", "20260814"
AS_OF, TRAIN_CUTOFF, HOLDOUT_START = "20260911", "20251111", "20260914"
MISSING_SIGNALS = ("promotion_probability", "path_change", "path_label", "existing_profit_rank")


def require(ok, reason):
    if not ok: raise ValueError(reason)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha_value(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "EXTERNAL_REPORT_OR_RECEIPT_SHA_REQUIRED")
    return value


def file_sha(path):
    path = Path(path)
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
            and stat.S_ISREG(path.stat().st_mode) and path.stat().st_nlink == 1,
            "FEATURE_FILE_MUST_BE_REGULAR_UNALIASED")
    return research.sha(path)


_SELF_SHA = file_sha(Path(__file__))


def guard():
    bindings = {HERE / name: digest for name, digest in PINNED.items()}
    bindings[Path(__file__).resolve()] = _SELF_SHA
    require(all(file_sha(path) == digest for path, digest in bindings.items()), "FEATURE_PANEL_CODE_CHANGED")
    plan = research.load_plan()  # Also verifies all frozen adapters and prior plan.
    require(plan["source_inputs"] == SOURCE_INPUTS, "REGISTERED_FEATURE_SOURCE_BINDINGS_CHANGED")
    require((plan["historical_rows"], plan["historical_D_dates"], plan["historical_D_start"], plan["historical_D_end"],
             plan["as_of_date"], plan["training_cutoff_date"], plan["future_holdout_start_date"], plan["cost_rate"])
            == (ROWS, DAYS, D_START, D_END, AS_OF, TRAIN_CUTOFF, HOLDOUT_START, .0045), "REGISTERED_FEATURE_SCOPE_CHANGED")
    return plan, {path.relative_to(ROOT).as_posix(): digest for path, digest in sorted(bindings.items())}


def date(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{8}", value), "INVALID_FEATURE_DATE")
    datetime.strptime(value, "%Y%m%d")
    return value


def identity(row):
    require(type(row) is dict, "EXACT_FEATURE_OR_LABEL_ROW_REQUIRED")
    day, code = date(row.get("signal_date")), row.get("ts_code")
    require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code), "INVALID_FEATURE_STOCK")
    return day, code


def _read_model_rows(path, columns):
    """Select only the original D whitelist and five identity/OOF fields."""
    extras = ("signal_date", "ts_code", "promotion_rank", "stage", "promotion_oof_train_end")
    require(type(columns) is list and len(columns) == 48 and len(set(columns)) == len(columns)
            and all(type(k) is str for k in columns) and not set(columns) & set(extras + MISSING_SIGNALS),
            "FIXED_D_FEATURE_WHITELIST_REQUIRED")
    wanted, result, seen, ranks = columns + list(extras), [], set(), {}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        require(len(header) == len(set(header)) and set(wanted) <= set(header), "FROZEN_LEDGER_HEADER_CHANGED")
        indices = {key: header.index(key) for key in wanted}
        for raw in reader:
            require(len(raw) == len(header), "FROZEN_LEDGER_ROW_SHAPE_CHANGED")
            # Do not select, convert, validate, hash separately, or copy old
            # buyability/return/outcome columns into the feature panel.
            selected = {key: raw[index] for key, index in indices.items()}
            day, code = identity({"signal_date": selected["signal_date"], "ts_code": selected["ts_code"]})
            require(D_START <= day <= D_END and (day, code) not in seen, "FEATURE_SCOPE_OR_DUPLICATE_CHANGED")
            seen.add((day, code))
            trained_until = date(selected["promotion_oof_train_end"])
            require(trained_until < day, "PROMOTION_OOF_NOT_STRICTLY_BEFORE_D")
            require(re.fullmatch(r"[1-9][0-9]*", selected["promotion_rank"]) is not None, "INVALID_PROMOTION_RANK")
            rank = int(selected["promotion_rank"])
            require(rank <= 10 and rank not in ranks.setdefault(day, set()), "DUPLICATE_OR_INVALID_PROMOTION_RANK")
            ranks[day].add(rank)
            require(selected["stage"] in ("2", "3"), "ONLY_2_TO_3_AND_3_TO_4_FEATURES")
            features = {key: None if selected[key] == "" else float(selected[key]) for key in columns}
            require(all(value is None or math.isfinite(value) for value in features.values()), "NONFINITE_D_FEATURE")
            result.append({**features, "signal_date": day, "ts_code": code, "promotion_rank": rank,
                "board_stage": int(selected["stage"]), "feature_as_of_date": day, "promotion_oof_train_end": trained_until})
            require(len(result) <= ROWS, "EXTRA_FEATURE_ROWS_FORBIDDEN")
    counts = Counter(row["signal_date"] for row in result)
    require(len(result) == ROWS and len(counts) == DAYS and min(counts) == D_START and max(counts) == D_END,
            "REGISTERED_6753_ROWS_910_DATES_CHANGED")
    require(all(ranks[day] == set(range(1, count + 1)) for day, count in counts.items()), "NONCONTIGUOUS_FROZEN_PROMOTION_RANK")
    return result, dict(counts)


def _inputs(root):
    return {key: research.safe_input(root, binding) for key, binding in SOURCE_INPUTS.items()}


def _snapshot(root):
    return {**{key: file_sha(path) for key, path in _inputs(root).items()},
            "research_marker": file_sha(root / research.MARKER)}


def _development_identity_scope(original_manifest, label_rows):
    """Reject nondevelopment input before serializing features or outcomes."""
    manifest_rows = original_manifest.get("rows")
    require(type(manifest_rows) is list and len(manifest_rows) == ROWS and len(label_rows) == ROWS,
            "ALL_6753_FROZEN_CANDIDATES_REQUIRE_EXPLICIT_LABEL_OR_PENDING")
    populations = []
    for rows in (manifest_rows, label_rows):
        keys = []
        for row in rows:
            key = identity(row)
            require(D_START <= key[0] <= D_END, "ALL_6753_FROZEN_CANDIDATES:NONDEVELOPMENT_IDENTITY")
            keys.append(key)
        require(len(set(keys)) == ROWS, "ALL_6753_FROZEN_CANDIDATES:DUPLICATE_IDENTITY")
        populations.append(tuple(keys))
    require(set(populations[0]) == set(populations[1]),
            "ALL_6753_FROZEN_CANDIDATES_REQUIRE_EXPLICIT_LABEL_OR_PENDING")
    return tuple(populations)


def prepare_model_panel(base_root, original_manifest, *, label_report_sha256,
                        candidate_receipt_sha256, market_receipt_sha256, label_rows):
    """Return model rows and a bound research manifest; never filter pending.

    `label_report_sha256` and receipt SHAs are caller-supplied external bindings,
    not independent source proof. `market_receipt_sha256=None` explicitly means
    only base market evidence. The candidate runner separately checks every
    fresh label's v3/overlay contract and all fixed quality gates before fitting.
    """
    sha_value(label_report_sha256); sha_value(candidate_receipt_sha256)
    if market_receipt_sha256 is not None: sha_value(market_receipt_sha256)
    require(type(original_manifest) is dict and type(label_rows) is list, "EXACT_MANIFEST_AND_ALL_LABEL_ROWS_REQUIRED")
    require(".." not in Path(base_root).parts, "UNALIASED_BASE_ROOT_REQUIRED")
    identity_scope = _development_identity_scope(original_manifest, label_rows)
    original_sha, labels_sha = canonical_sha(original_manifest), canonical_sha(label_rows)
    plan, code = guard()
    root = research.require_research_mirror(base_root)
    before = _snapshot(root)
    rebuilt = research.prepare_history(root)
    require(canonical_sha(rebuilt) == original_sha, "ORIGINAL_MANIFEST_DIFFERS_FROM_FROZEN_RECONSTRUCTION")
    inputs = _inputs(root)
    feature_manifest = research._json(inputs["manifest"].read_bytes())
    feature_contract = feature_manifest["feature_contract"]
    require(feature_contract["known_at"] == "D close" and feature_contract["future_or_cross_head_outputs_used"] is False,
            "D_CLOSE_NO_CROSS_HEAD_FEATURES_REQUIRED")
    columns = feature_contract["columns"]
    require(columns == rebuilt["feature_columns"], "FEATURE_COLUMNS_DIFFER_FROM_FROZEN_MANIFEST")
    rows, counts = _read_model_rows(inputs["ledger"], columns)
    require(counts == {day: len(codes) for day, codes in rebuilt["expected_candidate_codes"].items()},
            "FEATURE_DAY_COUNTS_DIFFER_FROM_FROZEN_MANIFEST")
    require(len(rows) == len(rebuilt["rows"]), "FEATURE_POPULATION_CHANGED")
    for row, frozen in zip(rows, rebuilt["rows"]):
        require(identity(row) == identity(frozen) and row["promotion_rank"] == frozen["promotion_rank"]
                and row["feature_as_of_date"] == frozen["feature_as_of_date"]
                and {key: row[key] for key in columns} == frozen["features"]
                and frozen["stage_transition"] == f"{row['board_stage']}_to_{row['board_stage'] + 1}",
                "MODEL_ROW_DIFFERS_FROM_FROZEN_D_FEATURES")
    label_keys = [identity(row) for row in label_rows]
    require(len(label_keys) == ROWS and len(set(label_keys)) == ROWS and set(label_keys) == {identity(row) for row in rows},
            "ALL_6753_FROZEN_CANDIDATES_REQUIRE_EXPLICIT_LABEL_OR_PENDING")
    model_manifest = {
        "schema_version": SCHEMA, "source_sha256": SOURCE_SHA, "base_archive_sha256": BASE_ARCHIVE_SHA,
        "source_provenance_verified": True,
        "source_provenance_verification_scope": "REGISTERED_D_FEATURE_FILES_SHA_AND_EXACT_MANIFEST_RECONSTRUCTION_ONLY",
        "whole_source_artifact_independently_verified_by_panel": False,
        "label_report_independently_verified_by_panel": False, "receipt_identity_independently_verified_by_panel": False,
        "label_contract_verified_by_panel": False,
        "feature_timestamp_semantics": "RETROSPECTIVE_D_ONLY_BOUND", "promotion_prediction_provenance": "HISTORICAL_OOF",
        "day_candidate_counts": counts, "historical_rows": ROWS, "historical_D_dates": DAYS,
        "historical_D_start": D_START, "historical_D_end": D_END, "as_of_date": AS_OF,
        "training_cutoff_date": TRAIN_CUTOFF, "validation_end_date": D_END, "future_holdout_start_date": HOLDOUT_START,
        "natural_freeze_verified": False, "entry_policy_id": policy_v3.ENTRY_POLICY_ID, "cost_rate": plan["cost_rate"],
        "entry_policy_matches_production_shadow": False,
        "causal_feature_audit_scope": "Pinned existing D-close feature contract; not new independent proof for every raw feature",
        "promotion_rank_use": plan["promotion_rank_use"],
        "missing_historical_signals": list(MISSING_SIGNALS), "source_overlay_contract": deepcopy(overlay.CONTRACT),
        "label_report_sha256": label_report_sha256, "candidate_collection_receipt_sha256": candidate_receipt_sha256,
        "market_collection_receipt_sha256": market_receipt_sha256,
        "development_frozen_rows_sha256": canonical_sha(rows), "development_label_rows_sha256": labels_sha,
        "frozen_manifest_sha256": original_sha, "plan_version": "v3", "plan_sha256": PINNED["PLAN_V3.json"],
        "source_file_bindings": deepcopy(list(SOURCE_INPUTS.values())), "execution_file_bindings": code,
        "research_only": True, "training_performed": False, "label_rebuild_performed": False,
        "legacy_outcome_columns_used_as_features": False, "feature_values_imputed": False,
        "forward_holdout_touched": False, "production_activation_allowed": False, "actual_execution_claimed": False}
    require(guard() == (plan, code) and _snapshot(root) == before, "FEATURE_CODE_OR_SOURCE_CHANGED_DURING_READ")
    require(_development_identity_scope(original_manifest, label_rows) == identity_scope,
            "CALLER_IDENTITIES_CHANGED_DURING_READ")
    require(canonical_sha(original_manifest) == original_sha and canonical_sha(label_rows) == labels_sha,
            "CALLER_MANIFEST_OR_LABEL_ROWS_CHANGED_DURING_READ")
    return rows, model_manifest
