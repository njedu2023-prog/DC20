#!/usr/bin/env python3
"""Hash-admitted, offline replay of the unchanged v2 research in an empty overlay.

No market/network operation. No production model, ranking, Shadow or old study
write. The default performs label replay and readiness only; actual research fit
additionally requires --train-after-gates, and can never authorize release.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
HEX = re.compile(r"[0-9a-f]{64}")
LEGACY_PACKAGE = "work/executable-profit-execution-v2-20260906"
LEGACY_HASHES = {"build_labels.py": "04cd20c97e1aaf1179b5460759bb22fddc84f1106cd802321678c675e6ad174f", "train_candidate.py": "50e01cbcd7da0aea7ec3329b0f980e58fd17d5a350e251452ba2dc26e6eaaaa8", "PLAN.json": "8a6b0614ad5510fb6b0b79cbe5dfd24a30b26e7f273d0c09420e5f8fcfba4629"}
FEATURE_MANIFEST = {"path": "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json", "sha256": "3fd457dbe8438b28bbd80d0521ebd9a2ba2d17845be019412238b7898cce69f5"}
RESULT_FILES = {"execution_labels.csv.gz", "label_manifest.json", "training_readiness.json", "training_candidate_predictions.csv.gz", "training_selected_records.csv.gz", "training_daily_returns.csv.gz", "training_comparison.json"}
ASOF = "20260904"


class ResearchError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ResearchError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    return sha_bytes(Path(path).read_bytes())


def unique_pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(payload):
    return json.loads(payload, object_pairs_hook=unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ResearchError("NONFINITE_JSON")))


def no_symlinks(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_PATH_FORBIDDEN")
    return path


def safe_file(root, relative):
    root, rel = no_symlinks(root), Path(relative)
    require(isinstance(relative, str) and "\\" not in relative and relative == rel.as_posix() and not rel.is_absolute() and not {"", ".", ".."}.intersection(rel.parts), "UNSAFE_RELATIVE_PATH")
    path = no_symlinks(root / rel)
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), "INPUT_NOT_REGULAR_FILE")
    return path


def pinned(root, relative, expected):
    require(isinstance(expected, str) and HEX.fullmatch(expected) is not None, "PIN_REQUIRED")
    path = safe_file(root, relative)
    require(sha(path) == expected, "INPUT_SHA_MISMATCH:" + relative)
    return path


def write_new(root, relative, payload):
    root, relative = no_symlinks(root), Path(relative)
    require(not relative.is_absolute() and not {"", ".", ".."}.intersection(relative.parts), "UNSAFE_OUTPUT_PATH")
    path = no_symlinks(root / relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    no_symlinks(path)
    with path.open("xb") as handle:
        handle.write(payload)
    return path


def assert_plan(repo, plan):
    require(plan.get("schema_version") == "dc20_profit_history_retrain_plan_v1", "RESEARCH_PLAN_SCHEMA_MISMATCH")
    reuse = plan.get("legacy_reuse", {})
    require(reuse.get("package") == LEGACY_PACKAGE and reuse.get("files") == LEGACY_HASHES, "LEGACY_REUSE_PINS_CHANGED")
    require(reuse.get("feature_manifest") == FEATURE_MANIFEST, "FROZEN_FEATURE_MANIFEST_PIN_CHANGED")
    legacy = decode(pinned(repo, LEGACY_PACKAGE + "/PLAN.json", LEGACY_HASHES["PLAN.json"]).read_bytes())
    # All policy, model, maturity and acceptance settings are literally inherited
    # from the predeclared v2 plan, not reselected after viewing refreshed labels.
    for key in ("source_inputs", "label_policy", "training", "hgb_parameters", "future_acceptance", "boundaries"):
        require(plan.get(key) == legacy.get(key), "FROZEN_V2_POLICY_CHANGED:" + key)
    require(plan.get("as_of_date") == legacy["as_of_date"] == ASOF, "ASOF_CHANGED")
    require(plan.get("original_policy_source_commit") == legacy["source_commit"] and plan.get("source_commit") == plan["source_admission"]["expected_run_sha"], "POLICY_OR_EXECUTION_SOURCE_IDENTITY_CHANGED")
    require(plan["future_acceptance"]["this_experiment_can_release"] is False, "RELEASE_FORBIDDEN")
    require(plan["overlay_policy"]["required_apis"] == ["daily", "stk_limit"] and plan["overlay_policy"]["copy_old_market_partitions"] is False and plan["overlay_policy"]["use_adjustment_factor_to_resolve_corporate_actions"] is False, "OVERLAY_POLICY_CHANGED")
    for name, digest in LEGACY_HASHES.items():
        pinned(repo, LEGACY_PACKAGE + "/" + name, digest)
    for spec in (*plan["source_inputs"].values(), reuse["feature_manifest"]):
        pinned(repo, spec["path"], spec["sha256"])
    audit = plan["source_admission"]["audit_script"]
    pinned(repo, audit["path"], audit["sha256"])
    for name, digest in plan["source_admission"]["source_control_sha256"].items():
        pinned(repo, plan["source_admission"]["source_package"] + "/" + name, digest)


def validate_audit_report(report, plan, *, expected_run_id, expected_run_sha, expected_run_attempt, expected_manifest_sha256):
    contract = plan["source_admission"]
    expected = {"expected_run_id": expected_run_id, "expected_run_sha": expected_run_sha, "expected_run_attempt": expected_run_attempt, "expected_manifest_sha256": expected_manifest_sha256}
    for key in ("expected_run_id", "expected_run_sha", "expected_run_attempt"):
        require(expected[key] == contract[key], "EXTERNAL_RUN_DIFFERS_FROM_PREDECLARED_SOURCE")
    require(HEX.fullmatch(expected_manifest_sha256 or "") is not None, "EXTERNAL_MANIFEST_SHA_REQUIRED")
    require(report.get("schema_version") == "dc20_profit_history_artifact_audit_v1" and report.get("status") == "SOURCE_VERIFIED_FOR_SEPARATE_LABEL_REBUILD", "AUDIT_STATUS_NOT_ADMITTED")
    require(report.get("source_ready_for_label_rebuild") is True and report.get("issues") == [], "AUDIT_NOT_READY_OR_HAS_ISSUES")
    require(all(report.get(key) == value for key, value in expected.items()), "AUDIT_EXTERNAL_IDENTITY_MISMATCH")
    require(report.get("artifact_manifest_sha256") == expected_manifest_sha256, "AUDIT_MANIFEST_DIGEST_MISMATCH")
    require(report.get("audit_code_sha256") == contract["audit_script"]["sha256"], "AUDIT_CODE_PIN_MISMATCH")
    require(report.get("source_repository") == "njedu2023-prog/DC20" and report.get("source_commit") == contract["source_commit"], "AUDIT_SOURCE_IDENTITY_MISMATCH")
    require(report.get("source_contract") == {"plan_sha256": contract["source_control_sha256"]["PLAN.json"], "request_sha256": contract["source_control_sha256"]["REQUEST.json"], "collector_sha256": contract["source_control_sha256"]["collect.py"], "source_inputs": plan["source_inputs"]}, "AUDIT_SOURCE_CONTRACT_MISMATCH")
    for key in ("session_plan_sha256", "requested_sessions", "requested_code_date_keys", "expected_required_partitions"):
        require(report.get(key) == contract[key], "AUDIT_WINDOW_MISMATCH:" + key)
    require(report.get("verified_required_partitions") == contract["expected_required_partitions"] and report.get("missing_required_partitions") == [], "AUDIT_REQUIRED_PARTITIONS_INCOMPLETE")
    require(report.get("overlap", {}).get("conflicts") == [] and report.get("overlap", {}).get("unverified") == [], "OVERLAP_REQUIRES_SEPARATE_REVIEW")
    require(report.get("as_of_date") == ASOF and report.get("tail_sessions") == 20, "AUDIT_ASOF_OR_WINDOW_CHANGED")
    for key in ("training_authorized", "production_release_authorized", "actual_fill_observed", "historically_available_at_D", "tail_window_is_forced_exit"):
        require(report.get(key) is False, "AUDIT_CLAIMS_OUTSIDE_SOURCE_SCOPE:" + key)


def admit_source(repo, plan, artifact_root, audit_path, *, expected_audit_sha256, expected_manifest_sha256, expected_run_id, expected_run_sha, expected_run_attempt="1"):
    artifact_root, audit_path = no_symlinks(artifact_root), no_symlinks(audit_path)
    require(artifact_root.is_dir(), "ARTIFACT_ROOT_MISSING")
    report = decode(pinned(audit_path.parent, audit_path.name, expected_audit_sha256).read_bytes())
    validate_audit_report(report, plan, expected_run_id=expected_run_id, expected_run_sha=expected_run_sha, expected_run_attempt=expected_run_attempt, expected_manifest_sha256=expected_manifest_sha256)
    manifest = decode(pinned(artifact_root, "artifact_manifest.json", expected_manifest_sha256).read_bytes())
    require(manifest.get("schema_version") == "dc20_isolated_source_artifact_manifest_v1" and manifest.get("status") == report["collection_status"] and manifest["status"] in ("COLLECTED_REQUIRED_SOURCES", "COLLECTED_REQUIRED_SOURCES_WITH_GAPS"), "ARTIFACT_STATUS_NOT_ADMITTED")
    identity = {"github_run_id": expected_run_id, "github_sha": expected_run_sha, "github_run_attempt": expected_run_attempt}
    require(all(manifest.get(k) == v for k, v in identity.items()), "ARTIFACT_RUN_IDENTITY_MISMATCH")
    require(manifest.get("source_data_only") is True and manifest.get("production_writes") is False, "ARTIFACT_ISOLATION_MISMATCH")
    files = manifest.get("files")
    require(isinstance(files, dict) and "artifact_manifest.json" not in files, "ARTIFACT_FILE_MAP_INVALID")
    for relative, info in files.items():
        path = pinned(artifact_root, relative, info["sha256"])
        require(path.stat().st_size == info["bytes"], "ARTIFACT_FILE_SIZE_MISMATCH")
    actual = set()
    for path in artifact_root.rglob("*"):
        no_symlinks(path)
        if path.is_file():
            actual.add(path.relative_to(artifact_root).as_posix())
    require(actual == set(files) | {"artifact_manifest.json"}, "UNMANIFESTED_OR_MISSING_ARTIFACT_FILE")
    contract = plan["source_admission"]
    for name in ("PLAN.json", "REQUEST.json"):
        require(files[name]["sha256"] == contract["source_control_sha256"][name], "ARTIFACT_CONTROL_FILE_CHANGED")
    sessions = decode(safe_file(artifact_root, "session_plan.json").read_bytes())
    require(sha_bytes(canonical(sessions)) == contract["session_plan_sha256"], "SESSION_PLAN_CHANGED")
    require(len(sessions) == contract["requested_sessions"] and sum(len(s["codes"]) for s in sessions) == contract["requested_code_date_keys"], "SESSION_PLAN_COUNT_CHANGED")
    required = [f"candidate_sources/{s['trade_date']}/{api}.csv" for s in sessions for api in ("daily", "stk_limit")]
    require(all(name in files for name in required), "ADMITTED_REQUIRED_CSV_MISSING")
    # Old partitions are audited only as overlap references, never copied into
    # this new execution overlay or selected to improve the research outcome.
    for ref in report["overlap"]["reference_files"]:
        pinned(repo, ref["path"], ref["sha256"])
    return {"report": report, "manifest": manifest, "sessions": sessions, "audit_sha256": expected_audit_sha256, "manifest_sha256": expected_manifest_sha256, "identity": identity}


def empty_workspace(path, *, repo, artifact_root):
    path = no_symlinks(path)
    require(path.is_absolute() and len(path.parts) >= 4, "WORKSPACE_MUST_BE_EXPLICIT_DEEP_TEMP_DIRECTORY")
    for source in (no_symlinks(repo), no_symlinks(artifact_root)):
        require(not path.is_relative_to(source) and not source.is_relative_to(path), "WORKSPACE_OVERLAPS_INPUTS_OR_REPOSITORY")
    require(path.parent.is_dir(), "WORKSPACE_PARENT_MISSING")
    if path.exists():
        require(path.is_dir() and not any(path.iterdir()), "WORKSPACE_NOT_EMPTY_REFUSE_OVERWRITE")
    else:
        path.mkdir(mode=0o700)
    return path


def stage_overlay(repo, plan, plan_bytes, artifact_root, admitted, workspace):
    overlay = workspace / "overlay"
    overlay.mkdir()
    study = overlay / "work" / "execution_study"
    study.mkdir(parents=True)
    bindings = {}
    def copy(source, relative, expected):
        require(sha(source) == expected, "SOURCE_CHANGED_BEFORE_COPY")
        target = write_new(overlay, relative, source.read_bytes())
        require(sha(target) == expected, "ISOLATED_COPY_HASH_MISMATCH")
        bindings[relative] = expected
    for spec in (*plan["source_inputs"].values(), plan["legacy_reuse"]["feature_manifest"]):
        copy(pinned(repo, spec["path"], spec["sha256"]), spec["path"], spec["sha256"])
    for name in ("build_labels.py", "train_candidate.py"):
        copy(pinned(repo, LEGACY_PACKAGE + "/" + name, LEGACY_HASHES[name]), "work/execution_study/" + name, LEGACY_HASHES[name])
    write_new(overlay, "work/execution_study/PLAN.json", plan_bytes)
    bindings["work/execution_study/PLAN.json"] = sha_bytes(plan_bytes)
    mapping = []
    for session in admitted["sessions"]:
        date = session["trade_date"]
        require(re.fullmatch(r"20\d{6}", date) is not None and date <= ASOF, "OVERLAY_DATE_OUT_OF_SCOPE")
        for api in ("daily", "stk_limit"):
            relative = f"candidate_sources/{date}/{api}.csv"
            expected = admitted["manifest"]["files"][relative]["sha256"]
            target = f"data/market/raw/{date[:4]}/{date}/{api}.csv"
            copy(pinned(artifact_root, relative, expected), target, expected)
            mapping.append({"artifact_relative_path": relative, "overlay_relative_path": target, "sha256": expected})
    return overlay, study, bindings, mapping


def verify_overlay(overlay, bindings):
    for relative, expected in bindings.items():
        pinned(overlay, relative, expected)
    actual_raw = set()
    for path in (overlay / "data/market/raw").rglob("*"):
        no_symlinks(path)
        if path.is_file():
            actual_raw.add(path.relative_to(overlay).as_posix())
    require(actual_raw == {name for name in bindings if name.startswith("data/market/raw/")}, "UNBOUND_OR_OLD_MARKET_PARTITION_IN_OVERLAY")


def child_environment():
    # Neither market credentials nor GitHub tokens are inherited by offline
    # children. Known byte-pinned scripts make no network calls.
    return {"PATH": os.environ.get("PATH", os.defpath), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "NUMEXPR_NUM_THREADS": "2"}


def command(study, overlay, name, workspace):
    require(name in ("build_labels.py", "train_candidate.py"), "UNKNOWN_LEGACY_ENTRYPOINT")
    result = subprocess.run([sys.executable, "-B", str(study / name), "--repo", str(overlay)], cwd=overlay, env=child_environment(), capture_output=True, check=False)
    write_new(workspace, "logs/" + name + ".stdout.txt", result.stdout)
    write_new(workspace, "logs/" + name + ".stderr.txt", result.stderr)
    require(result.returncode == 0, "LEGACY_ENTRYPOINT_FAILED:" + name)


def load_training_module(study):
    spec = importlib.util.spec_from_file_location("dc20_history_retrain_unchanged_v2", study / "train_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_readiness(study, overlay):
    module = load_training_module(study)
    plan, frame, provenance = module.load_inputs(overlay, study / "outputs", study / "PLAN.json")
    readiness = module.assess_readiness(frame, plan)
    require(readiness["models_fit"] == 0 and readiness["release_allowed"] is False and provenance["read_only_market_to_label_replay_verified"] is True, "READINESS_OR_REPLAY_CONTRACT_CHANGED")
    readiness["provenance"] = provenance
    module.write_json(study / "outputs/training_readiness.json", readiness)
    return readiness


def package_output_directory():
    target = no_symlinks(HERE / "outputs")
    require(not target.exists(), "EXISTING_PACKAGE_OUTPUTS_REFUSE_OVERWRITE")
    target.mkdir(mode=0o700)
    return target


def export_outputs(study, workspace, output, audit_path):
    result = {}
    for path in sorted((study / "outputs").iterdir()):
        require(path.name in RESULT_FILES, "UNEXPECTED_STUDY_OUTPUT_MODEL_OR_OTHER_FILE")
        payload = safe_file(study / "outputs", path.name).read_bytes()
        write_new(output, path.name, payload)
        result[path.name] = {"sha256": sha_bytes(payload), "bytes": len(payload)}
    for relative, source in [("audited_source_report.json", audit_path), ("runtime_PLAN.json", study / "PLAN.json")]:
        payload = safe_file(source.parent, source.name).read_bytes()
        write_new(output, relative, payload)
        result[relative] = {"sha256": sha_bytes(payload), "bytes": len(payload)}
    for source in sorted((workspace / "logs").glob("*.txt")):
        relative = "logs/" + source.name
        payload = safe_file(workspace, relative).read_bytes()
        write_new(output, relative, payload)
        result[relative] = {"sha256": sha_bytes(payload), "bytes": len(payload)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--expected-audit-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-sha", required=True)
    parser.add_argument("--expected-run-attempt", default="1")
    parser.add_argument("--workspace", required=True, help="physical absolute external empty temporary directory")
    parser.add_argument("--train-after-gates", action="store_true", help="Explicitly execute unchanged v2 offline fits only if every complete-D maturity gate passes")
    args = parser.parse_args()
    phase, output, readiness = "source_admission", None, None
    report = {"schema_version": "dc20_history_retrain_run_v1", "evidence_role": "RETROSPECTIVE_EXECUTION_PROXY_RESEARCH_NOT_FORWARD_OR_ACTUAL_FILLS", "release_allowed": False, "production_changed": False, "P0_ranking_changed": False, "shadow_ledger_changed": False, "model_weights_saved": False, "models_fit": 0, "training_requested": args.train_after_gates, "result_artifacts_valid": False, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        repo = no_symlinks(HERE.parents[1])
        plan_path = safe_file(HERE, "PLAN.json")
        plan_bytes = plan_path.read_bytes()
        plan = decode(plan_bytes)
        assert_plan(repo, plan)
        admitted = admit_source(repo, plan, args.artifact_root, args.audit_report, expected_audit_sha256=args.expected_audit_sha256, expected_manifest_sha256=args.expected_manifest_sha256, expected_run_id=args.expected_run_id, expected_run_sha=args.expected_run_sha, expected_run_attempt=args.expected_run_attempt)
        workspace = empty_workspace(args.workspace, repo=repo, artifact_root=args.artifact_root)
        output = package_output_directory()
        overlay, study, bindings, mapping = stage_overlay(repo, plan, plan_bytes, args.artifact_root, admitted, workspace)
        report.update(plan_sha256=sha_bytes(plan_bytes), runner_sha256=sha(__file__), source_audit_sha256=admitted["audit_sha256"], source_artifact_manifest_sha256=admitted["manifest_sha256"], source_execution_identity=admitted["identity"], source_contract=admitted["report"]["source_contract"], legacy_scripts_unchanged=LEGACY_HASHES, isolated_input_bindings=bindings, source_to_overlay=mapping)
        phase = "label_rebuild"
        verify_overlay(overlay, bindings)
        command(study, overlay, "build_labels.py", workspace)
        phase = "readonly_label_replay_and_readiness"
        verify_overlay(overlay, bindings)
        readiness = inspect_readiness(study, overlay)
        if args.train_after_gates and readiness["ready"]:
            phase = "offline_training"
            command(study, overlay, "train_candidate.py", workspace)
            readiness = decode(safe_file(study, "outputs/training_readiness.json").read_bytes())
        require(readiness["release_allowed"] is False and readiness["model_weights_saved"] is False, "UNEXPECTED_RELEASE_OR_MODEL_SERIALIZATION")
        require(not readiness["result_artifacts_valid"] or (args.train_after_gates and readiness["ready"]), "UNAUTHORIZED_RESULT_ARTIFACTS")
        report["models_fit"] = readiness["models_fit"]
        phase = "final_revalidation_and_export"
        verify_overlay(overlay, bindings)
        assert_plan(repo, plan)
        require(sha(plan_path) == sha_bytes(plan_bytes), "PLAN_CHANGED_DURING_RUN")
        require(sha(Path(args.audit_report)) == args.expected_audit_sha256, "AUDIT_CHANGED_DURING_RUN")
        report["result_files"] = export_outputs(study, workspace, output, Path(args.audit_report))
        report["read_only_market_to_label_replay_verified"] = readiness["provenance"]["read_only_market_to_label_replay_verified"]
        report["result_artifacts_valid"] = readiness["result_artifacts_valid"]
        report["status"] = "RETROSPECTIVE_EVALUATION_COMPLETE_NO_RELEASE" if readiness["result_artifacts_valid"] else ("READY_OFFLINE_TRAINING_NOT_REQUESTED" if readiness["ready"] else "BLOCKED_INSUFFICIENT_EXECUTION_LABELS")
        result = 0 if readiness["ready"] else 2
    except (ResearchError, ValueError, KeyError, OSError, TypeError, ImportError) as error:
        report.update(status="BLOCKED_RESEARCH_PIPELINE", failure_phase=phase, failure_code=str(error) if isinstance(error, ResearchError) else "INPUT_OR_DEPENDENCY_OR_LEGACY_VALIDATION_FAILURE", result_artifacts_valid=False, models_fit=None if phase == "offline_training" else report["models_fit"])
        result = 2
    report["completed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if output is not None:
        write_new(output, "run_manifest.json", canonical(report) + b"\n")
    print(json.dumps({"status": report["status"], "models_fit": report["models_fit"], "release_allowed": False, "failure_phase": report.get("failure_phase")}), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
