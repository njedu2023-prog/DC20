#!/usr/bin/env python3
"""New composite-source research only; original single-source admission unchanged."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dc20_history_closure_local_audit", HERE / "audit_closure.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def validate_training_contract(repo, plan, R):
    C.require(plan.get("schema_version") == "dc20_profit_history_closure_plan_v1", "CLOSURE_PLAN_SCHEMA_INVALID")
    legacy = json.loads(C.pinned(repo, R.LEGACY_PACKAGE + "/PLAN.json", R.LEGACY_HASHES["PLAN.json"]).read_text())
    for key in ("source_inputs", "label_policy", "training", "hgb_parameters", "future_acceptance", "boundaries"):
        C.require(plan.get(key) == legacy[key], "FROZEN_TRAINING_OR_LABEL_POLICY_CHANGED:" + key)
    C.require(plan["as_of_date"] == legacy["as_of_date"] == "20260904" and plan["source_commit"] == legacy["source_commit"], "FROZEN_ASOF_OR_POLICY_COMMIT_CHANGED")
    C.require(plan["legacy_reuse"]["files"] == R.LEGACY_HASHES and plan["legacy_reuse"]["feature_manifest"] == R.FEATURE_MANIFEST, "LEGACY_CODE_OR_FEATURE_PINS_CHANGED")
    for name, digest in R.LEGACY_HASHES.items():
        C.pinned(repo, R.LEGACY_PACKAGE + "/" + name, digest)
    for entry in (*plan["source_inputs"].values(), R.FEATURE_MANIFEST):
        C.pinned(repo, entry["path"], entry["sha256"])


def stage_composite(repo, plan, plan_bytes, report, roots, workspace, R):
    C.require(report.get("source_ready_for_label_rebuild") is True and report.get("single_successful_run_claim") is False and report.get("old_failure_status_preserved") is True, "COMPOSITE_SOURCE_NOT_ADMITTED")
    overlay = workspace / "overlay"
    overlay.mkdir()
    study = overlay / "work/execution_study"
    study.mkdir(parents=True)
    bindings, provenance = {}, []
    def copy(source, relative, digest):
        C.require(C.sha(source.read_bytes()) == digest, "SOURCE_CHANGED_BEFORE_OVERLAY_COPY")
        target = R.write_new(overlay, relative, source.read_bytes())
        C.require(C.sha(target.read_bytes()) == digest, "OVERLAY_COPY_HASH_CHANGED")
        bindings[relative] = digest
    for entry in (*plan["source_inputs"].values(), R.FEATURE_MANIFEST):
        copy(C.pinned(repo, entry["path"], entry["sha256"]), entry["path"], entry["sha256"])
    for name in ("build_labels.py", "train_candidate.py"):
        copy(C.pinned(repo, R.LEGACY_PACKAGE + "/" + name, R.LEGACY_HASHES[name]), "work/execution_study/" + name, R.LEGACY_HASHES[name])
    R.write_new(overlay, "work/execution_study/PLAN.json", plan_bytes)
    bindings["work/execution_study/PLAN.json"] = C.sha(plan_bytes)
    for part in report["source_partition_map"]:
        date, api, origin = part["trade_date"], part["api"], part["source_execution"]
        C.require(origin in ("original", "tail") and api in ("daily", "stk_limit"), "UNEXPECTED_COMPOSITE_PARTITION_SOURCE")
        target = f"data/market/raw/{date[:4]}/{date}/{api}.csv"
        C.require(target not in bindings, "COMPOSITE_PARTITION_OVERWRITE_FORBIDDEN")
        source = C.pinned(roots[origin], part["artifact_relative_path"], part["sha256"])
        copy(source, target, part["sha256"])
        provenance.append({**part, "overlay_relative_path": target})
    C.require(len(provenance) == 1852 and sum(p["source_execution"] == "original" for p in provenance) == 1833 and sum(p["source_execution"] == "tail" for p in provenance) == 19, "COMPOSITE_OVERLAY_COUNTS_CHANGED")
    return overlay, study, bindings, provenance


def output_directory(mode):
    C.require(mode in ("audit", "research"), "OUTPUT_MODE_INVALID")
    base = C.physical(HERE / "outputs")
    base.mkdir(exist_ok=True)
    target = C.physical(base / mode)
    C.require(not target.exists(), "EXISTING_CLOSURE_OUTPUT_REFUSE_OVERWRITE")
    target.mkdir(mode=0o700)
    return target


def checked_workspace_path(path, *, repo, original_root, tail_root):
    def normalized_physical(value):
        raw = str(value)
        parsed = Path(raw)
        C.require(parsed.is_absolute() and raw == str(parsed) and not {".", ".."}.intersection(raw.split("/")), "WORKSPACE_PATH_MUST_BE_NORMALIZED_ABSOLUTE")
        return C.physical(parsed)
    workspace = normalized_physical(path)
    for name, source in (("REPOSITORY", repo), ("ORIGINAL_SOURCE", original_root), ("TAIL_SOURCE", tail_root)):
        source = normalized_physical(source)
        C.require(not workspace.is_relative_to(source) and not source.is_relative_to(workspace), "WORKSPACE_OVERLAPS_" + name)
    return workspace


def prepare_workspace(path, *, repo, original_root, tail_root, R):
    workspace = checked_workspace_path(path, repo=repo, original_root=original_root, tail_root=tail_root)
    return R.empty_workspace(workspace, repo=repo, artifact_root=original_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", required=True)
    parser.add_argument("--original-audit", required=True)
    parser.add_argument("--original-audit-sha256", required=True)
    parser.add_argument("--tail-root", required=True)
    for prefix in ("original", "tail"):
        for name in ("run-id", "run-sha", "manifest-sha256"):
            parser.add_argument("--" + prefix + "-" + name, required=True)
        parser.add_argument("--" + prefix + "-run-attempt", default="1")
    parser.add_argument("--workspace", help="Required only for --train-after-gates: new physical external empty temp directory")
    parser.add_argument("--train-after-gates", action="store_true")
    args = parser.parse_args()
    report = {"schema_version": "dc20_two_run_closure_research_run_v1", "status": "SOURCE_ADMISSION_PENDING", "source_ready_for_label_rebuild": False, "models_fit": 0, "model_weights_saved": False, "release_allowed": False, "production_changed": False, "P0_ranking_changed": False, "shadow_ledger_changed": False, "old_failure_status_preserved": True, "single_successful_run_claim": False, "result_artifacts_valid": False, "evidence_role": "RETROSPECTIVE_DAILY_EXECUTION_PROXY_NOT_FORWARD_OR_ACTUAL_TRADES"}
    output, phase = None, "source_admission"
    try:
        repo = C.physical(HERE.parents[1])
        plan_path = C.file(HERE, "PLAN.json")
        payload = plan_path.read_bytes()
        plan = json.loads(payload)
        original = {k: getattr(args, "original_" + k) for k in ("run_id", "run_sha", "run_attempt", "manifest_sha256")}
        tail = {k: getattr(args, "tail_" + k) for k in ("run_id", "run_sha", "run_attempt", "manifest_sha256")}
        C.external_identity(plan["composite_source"]["tail"], tail)
        R = C.load_module(repo, plan["composite_source"]["training_helpers"], "dc20_closure_pinned_training_helpers")
        validate_training_contract(repo, plan, R)
        if args.train_after_gates:
            C.require(args.workspace, "EXPLICIT_EMPTY_WORKSPACE_REQUIRED")
            checked_workspace_path(args.workspace, repo=repo, original_root=args.original_root, tail_root=args.tail_root)
        closure = C.audit_closure(repo, plan, C.physical(args.original_root), C.physical(args.original_audit), original, args.original_audit_sha256, C.physical(args.tail_root), tail)
        output = output_directory("research" if args.train_after_gates else "audit")
        R.write_new(output, "composite_source_audit.json", C.canonical(closure) + b"\n")
        report.update(source_ready_for_label_rebuild=True, composite_audit_sha256=C.sha((output / "composite_source_audit.json").read_bytes()), plan_sha256=C.sha(payload), closure_auditor_sha256=C.sha(C.file(HERE, "audit_closure.py").read_bytes()), runner_sha256=C.sha(Path(__file__).read_bytes()), original_execution=original, tail_execution=tail, original_audit_issues=closure["original_audit_issues"], status="COMPOSITE_SOURCE_READY_NO_TRAINING_REQUESTED")
        if args.train_after_gates:
            tail_path = C.physical(args.tail_root)
            workspace = prepare_workspace(args.workspace, repo=repo, original_root=C.physical(args.original_root), tail_root=tail_path, R=R)
            roots = {"original": C.physical(args.original_root), "tail": tail_path}
            overlay, study, bindings, mapping = stage_composite(repo, plan, payload, closure, roots, workspace, R)
            report["source_to_overlay"] = mapping
            phase = "label_rebuild"
            R.verify_overlay(overlay, bindings)
            R.command(study, overlay, "build_labels.py", workspace)
            phase = "readonly_label_replay_and_readiness"
            R.verify_overlay(overlay, bindings)
            readiness = R.inspect_readiness(study, overlay)
            if readiness["ready"]:
                phase = "offline_training"
                R.verify_overlay(overlay, bindings)
                R.command(study, overlay, "train_candidate.py", workspace)
                readiness = json.loads(C.file(study, "outputs/training_readiness.json").read_text())
            report["models_fit"] = readiness["models_fit"]
            phase = "final_revalidation"
            C.require(readiness["release_allowed"] is False and readiness["model_weights_saved"] is False and readiness["provenance"]["read_only_market_to_label_replay_verified"] is True, "LEGACY_RESEARCH_BOUNDARY_CHANGED")
            R.verify_overlay(overlay, bindings)
            validate_training_contract(repo, plan, R)
            C.require(C.sha(plan_path.read_bytes()) == C.sha(payload), "PLAN_CHANGED_DURING_RESEARCH")
            report["result_files"] = R.export_outputs(study, workspace, output, output / "composite_source_audit.json")
            report["result_artifacts_valid"] = readiness["result_artifacts_valid"]
            report["status"] = "RETROSPECTIVE_EVALUATION_COMPLETE_NO_RELEASE" if readiness["result_artifacts_valid"] else "BLOCKED_INSUFFICIENT_EXECUTION_LABELS"
        result = 0 if not args.train_after_gates or report["result_artifacts_valid"] else 2
    except (ValueError, KeyError, OSError, TypeError, ImportError, AttributeError) as error:
        report.update(status="BLOCKED_COMPOSITE_RESEARCH", failure_phase=phase, failure_code=str(error) if isinstance(error, C.ClosureError) else "INPUT_OR_AUDIT_OR_LEGACY_VALIDATION_FAILURE", result_artifacts_valid=False, models_fit=None if phase == "offline_training" else report["models_fit"])
        result = 2
    report["completed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if output is not None:
        with C.physical(output / "run_manifest.json").open("xb") as stream:
            stream.write(C.canonical(report) + b"\n")
    print(json.dumps({"status": report["status"], "models_fit": report["models_fit"], "source_ready_for_label_rebuild": report["source_ready_for_label_rebuild"], "release_allowed": False}), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
