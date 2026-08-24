from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


INDEX_SCHEMA = "dc20_executable_profit_lagged_prior_research_artifact_index_v1"
INDEX_STATUS = "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY"
REPORT_STATUS = "RESEARCH_ONLY_NO_RELEASE"
AUDIT_STATUS = "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY"
PRIOR_STATUS = "RESEARCH_ONLY_NOT_A_MODEL_NOT_RELEASED"
REVIEWED_BASE_COMMIT_SHA = "cdbc43f67401c876d98f61585bea6d9375117e5b"

CODE_PATHS = (
    "lagged_priors.py",
    "benchmark.py",
    "fit_internal_challenger.py",
    "validate_artifact_index.py",
    "tests/test_lagged_priors.py",
    "tests/test_benchmark.py",
    "tests/test_validate_artifact_index.py",
)
ARTIFACT_NAMES = (
    "full_lagged_priors.csv.gz",
    "top10_lagged_priors.csv.gz",
    "lagged_priors_manifest.json",
    "benchmark_predictions.csv.gz",
    "benchmark_report.json",
    "internal_forward_challenger.pkl",
    "internal_forward_challenger_audit.json",
)
EXPECTED_DECISIONS = {
    f"{kind}:{variant}"
    for kind in ("lr", "hgb")
    for variant in ("full_priors", "top10_priors", "both_priors")
}
FORBIDDEN_TRUE_FLAGS = {
    "front_end_rank_allowed",
    "front_end_shadow_rank_allowed",
    "official_trade_action_allowed",
    "production_model_publish_allowed",
    "formal_ranking_allowed",
}


class ArtifactIndexError(ValueError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ArtifactIndexError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(value, dict), f"JSON object required: {path}")
    return value


def _resolve_under(root: Path, relative: str, *, label: str) -> Path:
    _expect(isinstance(relative, str) and relative and not Path(relative).is_absolute(), f"invalid {label} path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ArtifactIndexError(f"{label} path escaped root: {relative}") from exc
    _expect(path.is_file(), f"missing {label}: {relative}")
    return path


def _expect_false(mapping: Mapping[str, Any], key: str, *, label: str) -> None:
    _expect(key in mapping and mapping[key] is False, f"{label}.{key} must be exactly false")


def _expect_true(mapping: Mapping[str, Any], key: str, *, label: str) -> None:
    _expect(key in mapping and mapping[key] is True, f"{label}.{key} must be exactly true")


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _reject_any_enabled_release_flag(value: Any, *, label: str) -> None:
    for mapping in _walk_mappings(value):
        for key in FORBIDDEN_TRUE_FLAGS.intersection(mapping):
            _expect(mapping[key] is False, f"{label}.{key} must be exactly false")


def _validate_code_and_artifacts(index: Mapping[str, Any], work_root: Path) -> dict[str, Any]:
    code = index.get("code")
    _expect(isinstance(code, dict), "index.code must be an object")
    _expect(set(code) == set(CODE_PATHS), "index.code must bind the exact research code set")
    for relative in CODE_PATHS:
        path = _resolve_under(work_root, relative, label="code")
        _expect(re.fullmatch(r"[0-9a-f]{64}", str(code[relative])) is not None, f"invalid code SHA: {relative}")
        _expect(_sha256(path) == code[relative], f"code SHA mismatch: {relative}")

    artifacts = index.get("artifacts")
    _expect(isinstance(artifacts, dict), "index.artifacts must be an object")
    _expect(set(artifacts) == set(ARTIFACT_NAMES), "index.artifacts must bind the exact artifact set")
    paths: dict[str, Path] = {}
    for name in ARTIFACT_NAMES:
        _expect(Path(name).name == name, f"invalid artifact name: {name}")
        info = artifacts[name]
        _expect(isinstance(info, dict), f"artifact metadata must be object: {name}")
        _expect(set(info).issuperset({"sha256", "bytes"}), f"artifact metadata incomplete: {name}")
        _expect(re.fullmatch(r"[0-9a-f]{64}", str(info["sha256"])) is not None, f"invalid artifact SHA: {name}")
        _expect(type(info["bytes"]) is int and info["bytes"] >= 0, f"invalid artifact bytes: {name}")
        path = _resolve_under(work_root, f"outputs/{name}", label="artifact")
        _expect(path.stat().st_size == info["bytes"], f"artifact byte count mismatch: {name}")
        _expect(_sha256(path) == info["sha256"], f"artifact SHA mismatch: {name}")
        paths[name] = path
    _expect(
        artifacts["internal_forward_challenger.pkl"].get("trusted_repository_artifact_only") is True,
        "pickle must be marked trusted_repository_artifact_only",
    )
    return {"code_files": len(code), "artifacts": len(artifacts), "paths": paths}


def _validate_inputs(index: Mapping[str, Any], repo_root: Path) -> dict[str, str]:
    source_manifest_path = _resolve_under(
        repo_root,
        "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
        label="source manifest",
    )
    source_manifest = _read_json(source_manifest_path)
    _expect(source_manifest.get("owner") == "njedu2023-prog/DC20", "source manifest owner drifted")
    _expect_false(source_manifest, "runtime_dependency_on_top10_decision", label="source manifest")

    specifications = {
        "five_year_hard_pool_ledger_sha256": source_manifest["inputs"]["five_year_source_ledger"],
        "historical_oof_top10_ledger_sha256": source_manifest["output"],
        "strict_sse_calendar_sha256": source_manifest["inputs"]["strict_sse_calendar"],
    }
    indexed_inputs = index.get("inputs")
    _expect(isinstance(indexed_inputs, dict), "index.inputs must be an object")
    _expect(set(indexed_inputs) == set(specifications), "index.inputs must bind exactly three sources")
    actual: dict[str, str] = {}
    for index_key, specification in specifications.items():
        _expect(isinstance(specification, dict), f"source manifest input invalid: {index_key}")
        path = _resolve_under(repo_root, specification["path"], label=index_key)
        digest = _sha256(path)
        _expect(digest == specification["sha256"], f"source manifest SHA mismatch: {index_key}")
        _expect(digest == indexed_inputs[index_key], f"artifact index input SHA mismatch: {index_key}")
        actual[index_key] = digest
    return actual


def _validate_documents(
    index: Mapping[str, Any],
    artifact_paths: Mapping[str, Path],
    input_hashes: Mapping[str, str],
) -> None:
    report = _read_json(artifact_paths["benchmark_report.json"])
    audit = _read_json(artifact_paths["internal_forward_challenger_audit.json"])
    priors = _read_json(artifact_paths["lagged_priors_manifest.json"])

    _expect(report.get("status") == REPORT_STATUS, "benchmark report is not strict no-release")
    _expect_false(report, "official_trade_action_allowed", label="benchmark report")
    _expect_true(report, "retrospective_confirmation_window_has_been_viewed", label="benchmark report")
    _expect_false(report, "independent_untouched_confirmation_available", label="benchmark report")
    _expect_false(report, "forward_release_evidence_available", label="benchmark report")
    _expect_false(report, "runtime_dependency_on_top10_decision", label="benchmark report")
    _expect_false(report, "runtime_dependency_on_recovery", label="benchmark report")
    _expect_true(report, "joint_probability_identity_enforced", label="benchmark report")
    _expect(
        report.get("retrospective_declared_primary") == "lr:full_priors",
        "benchmark report must identify the declared retrospective primary",
    )
    _expect("pre_registered_primary" not in report, "viewed retrospective evidence cannot be called pre-registered")
    decisions = report.get("decisions")
    _expect(isinstance(decisions, dict) and set(decisions) == EXPECTED_DECISIONS, "benchmark must contain exactly six enhanced decisions")
    for name, decision in decisions.items():
        _expect(isinstance(decision, dict), f"invalid benchmark decision: {name}")
        _expect(decision.get("decision") == "REJECT_NOT_CONFIRMED", f"enhanced variant was not rejected: {name}")
        _expect(decision.get("confirmation_strictly_improved") is False, f"enhanced variant claims confirmation: {name}")
    predictions = report.get("predictions")
    _expect(isinstance(predictions, dict), "benchmark predictions binding missing")
    _expect(predictions.get("path") == "outputs/benchmark_predictions.csv.gz", "benchmark predictions path drifted")
    _expect(
        predictions.get("sha256") == index["artifacts"]["benchmark_predictions.csv.gz"]["sha256"],
        "benchmark report prediction SHA disagrees with index",
    )
    _expect(report["provenance"].get("ledger_sha256") == input_hashes["historical_oof_top10_ledger_sha256"], "benchmark ledger provenance drifted")
    _expect(report["provenance"].get("prior_manifest_sha256") == index["artifacts"]["lagged_priors_manifest.json"]["sha256"], "benchmark prior provenance drifted")

    _expect(audit.get("status") == AUDIT_STATUS, "challenger audit is not strict NOT_READY")
    _expect_false(audit, "front_end_rank_allowed", label="challenger audit")
    _expect_false(audit, "official_trade_action_allowed", label="challenger audit")
    _expect_false(audit, "historical_effect_claim_allowed", label="challenger audit")
    _expect_true(audit, "retrospective_confirmation_window_has_been_viewed", label="challenger audit")
    _expect_false(audit, "independent_untouched_confirmation_available", label="challenger audit")
    _expect_false(audit, "forward_release_evidence_available", label="challenger audit")
    _expect_true(audit, "challenger_selection_used_viewed_retrospective_results", label="challenger audit")
    audit_artifact = audit.get("artifact")
    _expect(isinstance(audit_artifact, dict), "challenger model artifact binding missing")
    _expect(audit_artifact.get("path") == "outputs/internal_forward_challenger.pkl", "challenger pickle path drifted")
    _expect(
        audit_artifact.get("sha256") == index["artifacts"]["internal_forward_challenger.pkl"]["sha256"],
        "challenger pickle SHA disagrees with index/audit",
    )
    _expect(
        audit_artifact.get("bytes") == index["artifacts"]["internal_forward_challenger.pkl"]["bytes"],
        "challenger pickle bytes disagree with index/audit",
    )
    _expect(audit["provenance"].get("ledger_sha256") == input_hashes["historical_oof_top10_ledger_sha256"], "challenger ledger provenance drifted")
    _expect(audit["provenance"].get("prior_manifest_sha256") == index["artifacts"]["lagged_priors_manifest.json"]["sha256"], "challenger prior provenance drifted")

    _expect(priors.get("status") == PRIOR_STATUS, "lagged-prior manifest status drifted")
    _expect_false(priors, "official_trade_action_allowed", label="lagged-prior manifest")
    _expect_false(priors, "model_trained", label="lagged-prior manifest")
    _expect_false(priors, "runtime_dependency_on_top10_decision", label="lagged-prior manifest")
    _expect_false(priors, "runtime_dependency_on_recovery", label="lagged-prior manifest")
    _expect(priors["calendar"].get("sha256") == input_hashes["strict_sse_calendar_sha256"], "prior calendar SHA drifted")
    _expect(priors["inputs"]["full_history"].get("sha256") == input_hashes["five_year_hard_pool_ledger_sha256"], "prior full-history SHA drifted")
    _expect(priors["inputs"]["top10_history_and_targets"].get("sha256") == input_hashes["historical_oof_top10_ledger_sha256"], "prior Top10 SHA drifted")
    for source_kind, artifact_name in (
        ("full", "full_lagged_priors.csv.gz"),
        ("top10", "top10_lagged_priors.csv.gz"),
    ):
        output = priors["outputs"][source_kind]
        _expect(output.get("path") == artifact_name, f"prior {source_kind} output path drifted")
        _expect(output.get("sha256") == index["artifacts"][artifact_name]["sha256"], f"prior {source_kind} output SHA drifted")
        _expect(output.get("bytes") == index["artifacts"][artifact_name]["bytes"], f"prior {source_kind} output bytes drifted")

    for label, document in (
        ("artifact index", index),
        ("benchmark report", report),
        ("challenger audit", audit),
        ("lagged-prior manifest", priors),
    ):
        _reject_any_enabled_release_flag(document, label=label)


def validate_artifact_index(repo_root: Path, work_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    work_root = work_root.resolve()
    _expect(repo_root.is_dir(), "repo root missing")
    _expect(work_root.is_dir(), "work root missing")
    index = _read_json(_resolve_under(work_root, "ARTIFACT_INDEX.json", label="artifact index"))
    _expect(index.get("schema_version") == INDEX_SCHEMA, "artifact index schema drifted")
    _expect(index.get("status") == INDEX_STATUS, "artifact index is not strict NOT_READY")
    _expect(index.get("scope") == "research_only", "artifact index escaped research-only scope")
    _expect(
        index.get("reviewed_base_commit_sha") == REVIEWED_BASE_COMMIT_SHA,
        "reviewed base commit drifted",
    )
    for key in (
        "runtime_dependency_on_codex",
        "runtime_dependency_on_top10_decision",
        "runtime_dependency_on_recovery",
    ):
        _expect_false(index, key, label="artifact index")

    information = index.get("information_state")
    _expect(isinstance(information, dict), "artifact index information_state missing")
    _expect_true(information, "retrospective_confirmation_window_has_been_viewed", label="information_state")
    _expect_false(information, "independent_untouched_confirmation_available", label="information_state")
    _expect_false(information, "forward_release_evidence_available", label="information_state")
    _expect_true(information, "challenger_selection_used_viewed_retrospective_results", label="information_state")

    publication = index.get("publication_boundary")
    _expect(isinstance(publication, dict), "artifact index publication_boundary missing")
    for key in (
        "front_end_rank_allowed",
        "official_trade_action_allowed",
        "production_model_publish_allowed",
    ):
        _expect_false(publication, key, label="publication_boundary")
    _expect_true(publication, "research_challenger_artifact_created", label="publication_boundary")

    decision = index.get("research_decision")
    _expect(isinstance(decision, dict), "artifact index research_decision missing")
    _expect(decision.get("enhanced_variants_evaluated") == 6, "research decision must evaluate six enhanced variants")
    _expect(decision.get("enhanced_variants_rejected") == 6, "research decision must reject six enhanced variants")
    _expect_false(decision, "formal_ranking_allowed", label="research_decision")

    bound = _validate_code_and_artifacts(index, work_root)
    input_hashes = _validate_inputs(index, repo_root)
    _validate_documents(index, bound["paths"], input_hashes)
    tests = index.get("tests")
    _expect(isinstance(tests, dict), "artifact index tests missing")
    _expect(type(tests.get("passed")) is int and tests["passed"] >= 1, "artifact index test count invalid")
    _expect(tests.get("failed") == 0, "artifact index records failed tests")

    return {
        "artifact_index_integrity_valid": True,
        "validated_status": INDEX_STATUS,
        "scope": "research_only",
        "release_allowed": False,
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "production_model_publish_allowed": False,
        "enhanced_variants_rejected": 6,
        "code_files_validated": bound["code_files"],
        "artifacts_validated": bound["artifacts"],
        "inputs_validated": len(input_hashes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the immutable research artifact index as NOT_READY only."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = validate_artifact_index(args.repo_root, args.work_root)
    except (ArtifactIndexError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"artifact_index_integrity_valid": False, "release_allowed": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
