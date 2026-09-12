#!/usr/bin/env python3
"""Rebuild an immutable research capital report; never collect data or trade.

Previously saved candidate outputs may identify an explicitly expected upstream
run. Their predictions, labels and performance are never used as truth. The
registered candidate and both capital comparisons are recomputed from sources.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]

from work.profit_1000_upgrade import run
from work.profit_1000_upgrade.candidate import DEFAULT_GATES, MODEL_SPEC, run_candidate
from work.profit_1000_upgrade.capital import replay_capital_from_repository
from work.profit_1000_upgrade.labels import build_labels
from work.profit_1000_upgrade import policy_v2

SCHEMA = "dc20_profit_1000_capital_report_v1"
OUTPUT_NAMES = ("capital.json", "candidate_replay.json")


class Blocked(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _expect(condition, status, reason):
    if not condition:
        raise Blocked(status, reason)


def _canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _binding(root, value):
    _expect(isinstance(value, dict) and set(value) == {"path", "sha256"}, "BLOCKED_SOURCE_EVIDENCE", "MALFORMED_SOURCE_BINDING")
    path, sha = value["path"], value["sha256"]
    _expect(isinstance(path, str) and path and not path.startswith("/") and "\\" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha),
            "BLOCKED_SOURCE_EVIDENCE", "UNSAFE_SOURCE_PATH_OR_SHA")
    run.safe_input(root, value)
    return dict(value)


def _existing_json(root, relative, status):
    path = root / relative
    _expect(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), status, "REQUIRED_FILE_MISSING_OR_ALIASED")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise Blocked(status, "INVALID_JSON") from exc
    _expect(isinstance(payload, dict), status, "JSON_OBJECT_REQUIRED")
    return payload, {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}


def _validate_upstream(root, expected_run_id, expected_commit):
    if expected_run_id is None and expected_commit is None:
        return {"required": False, "saved_candidate_consumed_as_truth": False}, None
    _expect(isinstance(expected_run_id, str) and re.fullmatch(r"[1-9]\d*", expected_run_id)
            and isinstance(expected_commit, str) and re.fullmatch(r"[0-9a-f]{40}", expected_commit),
            "BLOCKED_UPSTREAM_OUTPUT", "EXPECTED_SOURCE_RUN_AND_COMMIT_MUST_BE_PAIRED")
    payload, binding = _existing_json(root, "research_results/candidate.json", "BLOCKED_UPSTREAM_OUTPUT")
    evidence = payload.get("execution_provenance")
    _expect(isinstance(evidence, dict) and str(evidence.get("run_id")) == expected_run_id
            and evidence.get("run_commit") == expected_commit,
            "BLOCKED_UPSTREAM_OUTPUT", "UPSTREAM_RUN_OR_COMMIT_MISMATCH")
    return {"required": True, "verified_run_id": expected_run_id, "verified_commit": expected_commit,
            "saved_candidate_consumed_as_truth": False, "metadata_source": binding}, binding


def _validate_collection(root, manifest, plan):
    if plan.get("entry_policy_id") == policy_v2.ENTRY_POLICY_ID:
        return run.validate_collection_evidence(root, manifest, plan, plan_version="v2")
    receipt, binding = _existing_json(root, "collection_receipt.json", "BLOCKED_COLLECTION_EVIDENCE")
    _expect(receipt.get("schema_version") == "dc20_profit_1000_collection_receipt_v1"
            and receipt.get("as_of_date") == plan["as_of_date"]
            and receipt.get("auction_attempts_complete") is True
            and receipt.get("production_writes") is False
            and receipt.get("existing_truth_overwritten") is False
            and receipt.get("credential_persisted") is False
            and receipt.get("candidate_source_bindings") == manifest["source_bindings"],
            "BLOCKED_COLLECTION_EVIDENCE", "COLLECTION_SOURCE_OR_AUCTION_CONTRACT_INVALID")
    files = receipt.get("new_source_files")
    requests = receipt.get("request_receipts")
    _expect(isinstance(files, list) and isinstance(requests, list), "BLOCKED_COLLECTION_EVIDENCE", "COLLECTION_BINDINGS_OR_RECEIPTS_MISSING")
    sources = {binding["path"]: binding}
    for item in files:
        verified = _binding(root, item)
        _expect(verified["path"] not in sources, "BLOCKED_COLLECTION_EVIDENCE", "DUPLICATE_NEW_SOURCE_BINDING")
        sources[verified["path"]] = verified
    considered = set()
    for item in requests:
        _expect(isinstance(item, dict), "BLOCKED_COLLECTION_EVIDENCE", "INVALID_REQUEST_RECEIPT")
        if item.get("endpoint") != "stk_auction_o":
            continue
        date = item.get("trade_date")
        _expect(isinstance(date, str) and date not in considered, "BLOCKED_COLLECTION_EVIDENCE", "DUPLICATE_OR_INVALID_AUCTION_RECEIPT")
        _expect(item.get("network_request_performed") is True
                or item.get("status") in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"},
                "BLOCKED_COLLECTION_EVIDENCE", "AUCTION_SOURCE_NOT_ACTUALLY_ATTEMPTED")
        considered.add(date)
    _expect(considered == {row["exec_date"] for row in manifest["rows"]},
            "BLOCKED_COLLECTION_EVIDENCE", "NOT_EVERY_FROZEN_T_AUCTION_WAS_ATTEMPTED")
    return sources, {"sha256": binding["sha256"], "verified_auction_dates": len(considered),
                     "new_source_files_verified": len(files), "reported_status": receipt.get("status"),
                     "upstream_collection_request_sha256": receipt.get("collection_request_sha256")}


def _prediction_cohorts(result, frozen, plan):
    _expect(result.get("status") == "DEVELOPMENT_ONLY_FITTED"
            and isinstance(result.get("report"), dict) and result["report"].get("training_performed") is True
            and result["report"].get("production_activation_allowed") is False
            and isinstance(result.get("candidate_model"), dict) and result["candidate_model"],
            "BLOCKED_CANDIDATE_DATA", "CANDIDATE_NOT_GENUINELY_FITTED")
    predictions = result.get("predictions")
    _expect(isinstance(predictions, list) and predictions, "BLOCKED_CANDIDATE_DATA", "VALIDATION_PREDICTIONS_MISSING")
    if plan.get("entry_policy_id") == policy_v2.ENTRY_POLICY_ID:
        try:
            policy_v2.validate_contract(plan)
            for value in (result["report"], result["candidate_model"], *predictions):
                policy_v2.validate_contract(value)
            _expect(result["report"].get("provider_timestamp_semantics_confirmed") is False
                    and result["candidate_model"].get("provider_timestamp_semantics_confirmed") is False,
                    "BLOCKED_CANDIDATE_DATA", "RESEARCH_TIMESTAMP_ASSUMPTION_NOT_DISCLOSED")
        except (ValueError, TypeError, KeyError) as exc:
            raise Blocked("BLOCKED_CANDIDATE_DATA", "V2_CANDIDATE_SOURCE_POLICY_MISMATCH") from exc
    expected = {(row["signal_date"], row["ts_code"]): row for row in frozen
                if plan["training_cutoff_date"] <= row["signal_date"] <= plan["validation_end_date"]}
    observed, ranks = set(), {}
    for row in predictions:
        _expect(isinstance(row, dict), "BLOCKED_CANDIDATE_DATA", "PREDICTION_ROW_INVALID")
        key = row.get("signal_date"), row.get("ts_code")
        _expect(key in expected and key not in observed, "BLOCKED_CANDIDATE_DATA", "PREDICTION_OUTSIDE_FIXED_VALIDATION_UNIVERSE")
        observed.add(key)
        score, rank = row.get("candidate_score"), row.get("candidate_rank")
        _expect(type(score) in (int, float) and math.isfinite(score)
                and type(rank) is int and rank > 0 and rank not in ranks.setdefault(key[0], set()),
                "BLOCKED_CANDIDATE_DATA", "PREDICTION_RANK_OR_SCORE_INVALID")
        _expect(row.get("promotion_rank") == expected[key]["promotion_rank"],
                "BLOCKED_CANDIDATE_DATA", "FROZEN_PROMOTION_RANK_CHANGED")
        ranks[key[0]].add(rank)
    _expect(observed == set(expected) and all(values == set(range(1, len(values) + 1)) for values in ranks.values()),
            "BLOCKED_CANDIDATE_DATA", "INCOMPLETE_VALIDATION_PREDICTION_COHORT")
    promotion_rows = [expected[key] for key in sorted(expected)]
    return predictions, promotion_rows, sorted(ranks)


def _source_provenance(plan_version="v1"):
    files = [HERE / name for name in ("capital_report.py", "capital.py", "run.py", "labels.py", "candidate.py", "collect.py", "PLAN.json", "COLLECTION.json")]
    if plan_version == "v2":
        files += [HERE / name for name in ("PLAN_V2.json", "COLLECTION_V2.json", "policy_v2.py",
                                          "auction_truth.py", "minute_truth.py", "resume_v2.py")]
    files += [CHECKOUT / relative for relative in (
        "src/top10decision/decision/shadow_exit_1000.py",
        "src/top10decision/decision/shadow_exit_minute_truth.py",
        "src/top10decision/decision/executable_profit_shadow_settlement.py",
        "requirements.lock", "requirements-dev.lock")]
    return {"run_commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
            "source_files": [{"path": path.relative_to(CHECKOUT).as_posix(), "sha256": run.sha(path)} for path in files]}


def generate_capital_report(root, *, expected_source_run_id=None, expected_source_commit=None):
    """Create two immutable files beneath a verified, outside-checkout mirror."""
    root = run.require_research_mirror(root)
    output = root / "research_capital"
    for name in OUTPUT_NAMES:
        path = output / name
        if path.exists() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("capital research outputs are immutable; use a new run directory")
    version = run.plan_version_for(root)
    provenance = _source_provenance(version)
    plan = run.load_plan() if version == "v1" else run.load_plan(version)
    report = {
        "schema_version": SCHEMA, "status": "BLOCKED_INPUT", "research_only": True,
        "production_activation_allowed": False, "production_weights_changed": False,
        "data_collected": False, "old_saved_labels_consumed": False,
        "actual_execution_claimed": False, "profitability_improvement_proven": False,
        "as_of_date": plan["as_of_date"], "label_policy_id": plan["label_policy_id"],
        "entry_policy_id": plan["research_entry_policy_id"], "plan_sha256": run.sha(run.plan_path(version)),
        "validation_window": {"start": plan["training_cutoff_date"], "end": plan["validation_end_date"],
                              "evidence_role": "DEVELOPMENT_NOT_UNTOUCHED_FORWARD_TEST"},
        "forward_holdout_start_date": plan["future_holdout_start_date"], "forward_holdout_evaluated": False,
        "capital_comparisons": None, "execution_provenance": provenance, "data_source_files": [],
        "account_interpretation": "Candidate and promotion each contain separate Top1 and Top2 benchmark accounts; never sum these into the user's CNY 1,000,000 portfolio.",
    }
    candidate = {"status": "BLOCKED_INPUT", "candidate_model": None, "predictions": [],
                 "report": {"training_performed": False, "production_activation_allowed": False}}
    if version == "v2":
        report.update(**policy_v2.validate_contract(plan), plan_version="v2",
                      source_policy_contract=policy_v2.source_policy_contract(),
                      provider_timestamp_semantics_confirmed=False)
    marker = root / ".dc20-profit-1000-research-root.json"
    stage, data_sources = "BLOCKED_INPUT", {
        marker.name: {"path": marker.name, "sha256": run.sha(marker)}
    }
    candidate_returned = False
    try:
        _expect(plan["data_gates"] == DEFAULT_GATES
                and set(plan["candidate_model"]) == {"estimator", "alpha", "solver", "target", "hyperparameter_search"}
                and all(plan["candidate_model"].get(key) == MODEL_SPEC[key] for key in plan["candidate_model"]),
                "BLOCKED_PLAN_DRIFT", "REGISTERED_ENGINEERING_GATES_OR_FIXED_MODEL_CHANGED")
        stage = "BLOCKED_UPSTREAM_OUTPUT"
        upstream, upstream_binding = _validate_upstream(root, expected_source_run_id, expected_source_commit)
        report["upstream_provenance"] = upstream
        if upstream_binding:
            data_sources[upstream_binding["path"]] = upstream_binding
        stage = "BLOCKED_COLLECTION_EVIDENCE"
        manifest, frozen, evidence = run.prepare_history(root)
        sources, receipt = _validate_collection(root, manifest, plan)
        data_sources.update(sources)
        report["collection_evidence"] = receipt
        stage = "BLOCKED_LABEL_REPLAY"
        labels = build_labels(root, manifest, as_of_date=plan["as_of_date"])
        report["rebuilt_label_sha256"] = _canonical_sha(labels)
        report["label_status_counts"] = dict(Counter(row["label_status"] for row in labels["rows"]))
        for binding in labels["source_files"]:
            binding = _binding(root, binding)
            _expect(binding["path"] not in data_sources or data_sources[binding["path"]] == binding,
                    "BLOCKED_SOURCE_EVIDENCE", "SOURCE_CHANGED_DURING_LABEL_REPLAY")
            data_sources[binding["path"]] = binding
        stage = "BLOCKED_CANDIDATE_DATA"
        candidate = run_candidate(frozen, labels["rows"], as_of_date=plan["as_of_date"],
                                  training_cutoff_date=plan["training_cutoff_date"],
                                  validation_end_date=plan["validation_end_date"],
                                  holdout_start_date=plan["future_holdout_start_date"],
                                  frozen_manifest=evidence, gates=plan["data_gates"])
        candidate_returned = True
        report["candidate_status"] = candidate.get("status")
        predicted, promotion, compared_dates = _prediction_cohorts(candidate, frozen, plan)
        stage = "BLOCKED_CAPITAL_REPLAY"
        comparisons = {}
        for name, rows, rank_field in (("candidate", predicted, "candidate_rank"), ("frozen_promotion", promotion, "promotion_rank")):
            value = replay_capital_from_repository(root, rows, labels["rows"], candidate_manifest=manifest,
                                                   as_of_date=plan["as_of_date"], rank_field=rank_field,
                                                   entry_policy_id=plan["research_entry_policy_id"])
            _expect(value.get("label_verification") == "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES"
                    and value.get("production_activation_allowed") is False,
                    "BLOCKED_CAPITAL_REPLAY", "CAPITAL_LABELS_NOT_INDEPENDENTLY_REBUILT")
            if version == "v2":
                policy_v2.validate_contract(value)
                _expect(value.get("provider_timestamp_semantics_confirmed") is False,
                        "BLOCKED_CAPITAL_REPLAY", "RESEARCH_TIMESTAMP_ASSUMPTION_NOT_DISCLOSED")
            comparisons[name] = value
            for binding in value["source_files"]:
                binding = _binding(root, binding)
                _expect(binding["path"] not in data_sources or data_sources[binding["path"]] == binding,
                        "BLOCKED_SOURCE_EVIDENCE", "SOURCE_CHANGED_DURING_CAPITAL_REPLAY")
                data_sources[binding["path"]] = binding
        for binding in data_sources.values():
            _binding(root, binding)
        report.update(status="CAPITAL_REPLAY_COMPLETE", capital_comparisons=comparisons,
                      compared_validation_dates=compared_dates,
                      compared_candidate_rows=len(predicted))
    except Blocked as exc:
        report.update(status=exc.status, reason=exc.reason)
    except (ValueError, OSError, TypeError, KeyError, ImportError) as exc:
        report.update(status=stage, reason="REPLAY_OR_EVIDENCE_VALIDATION_FAILED", error_type=type(exc).__name__)
    if report["status"] == "BLOCKED_CANDIDATE_DATA" and candidate.get("report", {}).get("training_performed"):
        # An invalid purportedly fitted result is not a usable model artifact.
        # Preserve that fitting was reported, but never serialize NaN scores or
        # incomplete predictions into a later-consumable candidate file.
        candidate = {"status": "BLOCKED_CANDIDATE_OUTPUT", "candidate_model": None, "predictions": [],
                     "report": {"training_performed": True, "candidate_output_rejected": True,
                                "production_activation_allowed": False, "reason": report.get("reason")}}
    if report["status"] != "CAPITAL_REPLAY_COMPLETE" and not candidate.get("report", {}).get("training_performed"):
        if not candidate_returned:
            candidate["status"] = report["status"]
        candidate.update(candidate_model=None, predictions=[])
        candidate["report"].update(training_performed=False, production_activation_allowed=False)
    report["data_source_files"] = sorted(data_sources.values(), key=lambda value: value["path"])
    candidate.update(plan_sha256=report["plan_sha256"], execution_provenance=provenance,
                     production_release_allowed=False, capital_report_status=report["status"])
    report["candidate_replay_sha256"] = _canonical_sha(candidate)
    run.write_json(output / "candidate_replay.json", candidate)
    run.write_json(output / "capital.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-source-run-id")
    parser.add_argument("--expected-source-commit")
    args = parser.parse_args()
    result = generate_capital_report(args.root, expected_source_run_id=args.expected_source_run_id,
                                     expected_source_commit=args.expected_source_commit)
    print(json.dumps({"status": result["status"], "candidate_status": result.get("candidate_status"),
                      "reason": result.get("reason"), "production_activation_allowed": False}, sort_keys=True))
    return 0 if result["status"] == "CAPITAL_REPLAY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
