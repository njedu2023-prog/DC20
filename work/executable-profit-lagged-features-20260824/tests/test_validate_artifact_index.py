from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest


WORK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK_ROOT))

import validate_artifact_index as validator  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    work = tmp_path / "work"
    outputs = work / "outputs"
    outputs.mkdir(parents=True)

    full_source = repo / "data/full.csv.gz"
    top10_source = repo / "data/top10.csv.gz"
    calendar = repo / "data/calendar.csv"
    for path, payload in (
        (full_source, b"full-source"),
        (top10_source, b"top10-source"),
        (calendar, b"calendar-source"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    source_manifest = {
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "inputs": {
            "five_year_source_ledger": {
                "path": "data/full.csv.gz",
                "sha256": _sha(full_source),
            },
            "strict_sse_calendar": {
                "path": "data/calendar.csv",
                "sha256": _sha(calendar),
            },
        },
        "output": {"path": "data/top10.csv.gz", "sha256": _sha(top10_source)},
    }
    _write_json(
        repo / "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
        source_manifest,
    )

    for relative in validator.CODE_PATHS:
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")

    full_prior = outputs / "full_lagged_priors.csv.gz"
    top10_prior = outputs / "top10_lagged_priors.csv.gz"
    predictions = outputs / "benchmark_predictions.csv.gz"
    pickle_path = outputs / "internal_forward_challenger.pkl"
    full_prior.write_bytes(b"full-prior")
    top10_prior.write_bytes(b"top10-prior")
    predictions.write_bytes(b"predictions")
    pickle_path.write_bytes(b"trusted-pickle")

    prior_manifest = {
        "status": validator.PRIOR_STATUS,
        "official_trade_action_allowed": False,
        "model_trained": False,
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_recovery": False,
        "calendar": {"sha256": _sha(calendar)},
        "inputs": {
            "full_history": {"sha256": _sha(full_source)},
            "top10_history_and_targets": {"sha256": _sha(top10_source)},
        },
        "outputs": {
            "full": {
                "path": full_prior.name,
                "sha256": _sha(full_prior),
                "bytes": full_prior.stat().st_size,
            },
            "top10": {
                "path": top10_prior.name,
                "sha256": _sha(top10_prior),
                "bytes": top10_prior.stat().st_size,
            },
        },
    }
    prior_manifest_path = outputs / "lagged_priors_manifest.json"
    _write_json(prior_manifest_path, prior_manifest)

    decisions = {
        name: {
            "decision": "REJECT_NOT_CONFIRMED",
            "confirmation_strictly_improved": False,
        }
        for name in validator.EXPECTED_DECISIONS
    }
    report = {
        "status": validator.REPORT_STATUS,
        "official_trade_action_allowed": False,
        "retrospective_confirmation_window_has_been_viewed": True,
        "independent_untouched_confirmation_available": False,
        "forward_release_evidence_available": False,
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_recovery": False,
        "joint_probability_identity_enforced": True,
        "retrospective_declared_primary": "lr:full_priors",
        "decisions": decisions,
        "predictions": {
            "path": "outputs/benchmark_predictions.csv.gz",
            "sha256": _sha(predictions),
        },
        "provenance": {
            "ledger_sha256": _sha(top10_source),
            "prior_manifest_sha256": _sha(prior_manifest_path),
        },
    }
    report_path = outputs / "benchmark_report.json"
    _write_json(report_path, report)

    audit = {
        "status": validator.AUDIT_STATUS,
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "historical_effect_claim_allowed": False,
        "retrospective_confirmation_window_has_been_viewed": True,
        "independent_untouched_confirmation_available": False,
        "forward_release_evidence_available": False,
        "challenger_selection_used_viewed_retrospective_results": True,
        "artifact": {
            "path": "outputs/internal_forward_challenger.pkl",
            "sha256": _sha(pickle_path),
            "bytes": pickle_path.stat().st_size,
        },
        "provenance": {
            "ledger_sha256": _sha(top10_source),
            "prior_manifest_sha256": _sha(prior_manifest_path),
        },
    }
    audit_path = outputs / "internal_forward_challenger_audit.json"
    _write_json(audit_path, audit)

    artifact_metadata: dict[str, dict[str, Any]] = {}
    for name in validator.ARTIFACT_NAMES:
        path = outputs / name
        artifact_metadata[name] = {"sha256": _sha(path), "bytes": path.stat().st_size}
    artifact_metadata["internal_forward_challenger.pkl"]["trusted_repository_artifact_only"] = True
    index = {
        "schema_version": validator.INDEX_SCHEMA,
        "status": validator.INDEX_STATUS,
        "reviewed_base_commit_sha": validator.REVIEWED_BASE_COMMIT_SHA,
        "scope": "research_only",
        "runtime_dependency_on_codex": False,
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_recovery": False,
        "information_state": {
            "retrospective_confirmation_window_has_been_viewed": True,
            "independent_untouched_confirmation_available": False,
            "forward_release_evidence_available": False,
            "challenger_selection_used_viewed_retrospective_results": True,
        },
        "publication_boundary": {
            "front_end_rank_allowed": False,
            "official_trade_action_allowed": False,
            "production_model_publish_allowed": False,
            "research_challenger_artifact_created": True,
        },
        "inputs": {
            "five_year_hard_pool_ledger_sha256": _sha(full_source),
            "historical_oof_top10_ledger_sha256": _sha(top10_source),
            "strict_sse_calendar_sha256": _sha(calendar),
        },
        "code": {relative: _sha(work / relative) for relative in validator.CODE_PATHS},
        "artifacts": artifact_metadata,
        "tests": {"passed": 1, "failed": 0},
        "research_decision": {
            "enhanced_variants_evaluated": 6,
            "enhanced_variants_rejected": 6,
            "formal_ranking_allowed": False,
        },
    }
    _write_json(work / "ARTIFACT_INDEX.json", index)
    return repo, work


def _load_index(work: Path) -> dict[str, Any]:
    return json.loads((work / "ARTIFACT_INDEX.json").read_text(encoding="utf-8"))


def _reindex_artifact(work: Path, index: dict[str, Any], name: str) -> None:
    path = work / "outputs" / name
    index["artifacts"][name]["sha256"] = _sha(path)
    index["artifacts"][name]["bytes"] = path.stat().st_size
    _write_json(work / "ARTIFACT_INDEX.json", index)


def test_valid_index_only_proves_not_ready(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    result = validator.validate_artifact_index(repo, work)
    assert result["artifact_index_integrity_valid"] is True
    assert result["validated_status"] == validator.INDEX_STATUS
    assert result["release_allowed"] is False
    assert result["front_end_rank_allowed"] is False
    assert result["production_model_publish_allowed"] is False


def test_tampered_ready_status_is_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    index = _load_index(work)
    index["status"] = "READY"
    _write_json(work / "ARTIFACT_INDEX.json", index)
    with pytest.raises(validator.ArtifactIndexError, match="strict NOT_READY"):
        validator.validate_artifact_index(repo, work)


def test_tampered_reviewed_base_is_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    index = _load_index(work)
    index["reviewed_base_commit_sha"] = "a" * 40
    _write_json(work / "ARTIFACT_INDEX.json", index)
    with pytest.raises(validator.ArtifactIndexError, match="reviewed base commit drifted"):
        validator.validate_artifact_index(repo, work)


def test_tampered_code_or_artifact_hash_is_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    index = _load_index(work)
    index["code"]["benchmark.py"] = "0" * 64
    _write_json(work / "ARTIFACT_INDEX.json", index)
    with pytest.raises(validator.ArtifactIndexError, match="code SHA mismatch"):
        validator.validate_artifact_index(repo, work)


def test_enabled_publish_flag_is_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    index = _load_index(work)
    index["publication_boundary"]["production_model_publish_allowed"] = True
    _write_json(work / "ARTIFACT_INDEX.json", index)
    with pytest.raises(validator.ArtifactIndexError, match="must be exactly false"):
        validator.validate_artifact_index(repo, work)


def test_rehashed_report_release_status_is_still_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    report_path = work / "outputs/benchmark_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "READY"
    _write_json(report_path, report)
    index = _load_index(work)
    _reindex_artifact(work, index, "benchmark_report.json")
    with pytest.raises(validator.ArtifactIndexError, match="strict no-release"):
        validator.validate_artifact_index(repo, work)


def test_rehashed_audit_front_end_flag_is_still_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    audit_path = work / "outputs/internal_forward_challenger_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["front_end_rank_allowed"] = True
    _write_json(audit_path, audit)
    index = _load_index(work)
    _reindex_artifact(work, index, "internal_forward_challenger_audit.json")
    with pytest.raises(validator.ArtifactIndexError, match="must be exactly false"):
        validator.validate_artifact_index(repo, work)


def test_rehashed_audit_pickle_hash_tamper_is_rejected(tmp_path: Path) -> None:
    repo, work = _fixture(tmp_path)
    audit_path = work / "outputs/internal_forward_challenger_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["artifact"]["sha256"] = "f" * 64
    _write_json(audit_path, audit)
    index = _load_index(work)
    _reindex_artifact(work, index, "internal_forward_challenger_audit.json")
    with pytest.raises(validator.ArtifactIndexError, match="pickle SHA disagrees"):
        validator.validate_artifact_index(repo, work)
