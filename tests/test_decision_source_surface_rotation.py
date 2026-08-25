from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models/decision_model_freeze.json"
EVIDENCE = ROOT / "models/decision_source_surface_rotation_20260824.json"
EXPECTED_ADDED_RUNTIME_PINS: set[str] = set()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_reviewed_source_surface_rotation_is_hash_bound_and_model_preserving() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    rotation = manifest["source_surface_rotation"]

    assert evidence["schema_version"] == "decision_source_surface_rotation_v1"
    assert rotation == {
        "schema_version": "decision_source_surface_rotation_v1",
        "rotation_id": evidence["rotation_id"],
        "approved_base_commit": evidence["approved_base_commit"],
        "prior_manifest_sha256": evidence["prior_manifest_sha256"],
        "evidence_path": EVIDENCE.relative_to(ROOT).as_posix(),
        "evidence_sha256": _sha256(EVIDENCE),
    }
    assert evidence["approved_base_commit"] == (
        "90d52c12ea2e0892410b2703263215b07ab4f7f5"
    )
    assert evidence["prior_manifest_sha256"] == (
        "671789aa65ae8209db45cf824002e8dbd2ff39d840b72e9b64457496d95e7c7b"
    )
    assert evidence["prior_evidence_sha256"] == (
        "b39ab27e7021ea04bf254e886ada72382719a2e0822a2136bfe52b01ab4898b6"
    )
    assert evidence["prior_manifest_sha256"] != _sha256(MANIFEST)
    assert manifest["pinned_files"][rotation["evidence_path"]] == (
        rotation["evidence_sha256"]
    )

    identity = evidence["protected_model_identity"]
    assert identity == {
        "freeze_id": manifest["freeze_id"],
        "training_cutoff_signal_date": manifest["training_cutoff_signal_date"],
        "history_snapshot_sha256": manifest["history_snapshot"]["sha256"],
        "three_rank_contract_sha256": _canonical_sha256(
            manifest["production"]["three_rank"]
        ),
        "model_identity_changed": False,
        "training_ledger_changed": False,
        "action_plan_changed": False,
    }

    changes = evidence["pin_changes"]
    paths = [item["path"] for item in changes]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths)) == 8
    assert set(paths) == {
        "decision.html",
        "models/decision_executable_profit_research_projection_contract.json",
        "scripts/check_tushare_health.py",
        "src/top10decision/data/tushare_minute.py",
        "src/top10decision/decision/executable_profit_research_projection.py",
        "src/top10decision/rt_min_contract.py",
        "tests/test_decision_contract.py",
        "tests/test_decision_tushare_health.py",
    }
    current_surface: dict[str, str] = {}
    for item in changes:
        assert item["prior_sha256"] != item["current_sha256"]
        assert item["classification"] in {
            "executable_profit_probability_surface_upgrade",
            "rt_min_native_wire_contract_repair",
        }
        target = ROOT / item["path"]
        assert target.is_file() and not target.is_symlink()
        assert _sha256(target) == item["current_sha256"]
        assert manifest["pinned_files"][item["path"]] == item["current_sha256"]
        current_surface[item["path"]] = item["current_sha256"]
    assert _canonical_sha256(current_surface) == evidence["changed_surface_sha256"]

    added_runtime_pins = evidence["added_runtime_pins"]
    assert added_runtime_pins == {}
    assert set(added_runtime_pins) == EXPECTED_ADDED_RUNTIME_PINS

    reconstructed_prior_pins = dict(manifest["pinned_files"])
    assert reconstructed_prior_pins[rotation["evidence_path"]] == (
        rotation["evidence_sha256"]
    )
    reconstructed_prior_pins[rotation["evidence_path"]] = evidence[
        "prior_evidence_sha256"
    ]
    for item in changes:
        assert reconstructed_prior_pins[item["path"]] == item["current_sha256"]
        reconstructed_prior_pins[item["path"]] = item["prior_sha256"]
    assert _canonical_sha256(reconstructed_prior_pins) == (
        evidence["prior_pinned_files_sha256"]
    )
    assert len(manifest["pinned_files"]) == len(reconstructed_prior_pins)

    assert evidence["release_contract"] == {
        "candidate_count": "ACTUAL_N_0_TO_10_NO_PADDING",
        "shadow_slots": "min(2,N)",
        "public_label": (
            "MODEL_ESTIMATED_EXECUTABLE_PROFIT_PROBABILITY_"
            "UNCALIBRATED_NOT_FORMAL"
        ),
        "visible_full_n_probability_ranking": True,
        "promotion_rank_frozen_and_independent": True,
        "rt_min_wire_fields": "NATIVE_DEFAULT_EMPTY_PROJECTION",
        "rt_min_response_contract": (
            "EXACT_CODE_XOR_TS_CODE_PLUS_FREQ_TIME_OHLCVA"
        ),
        "codex_runtime_dependency": False,
        "external_top10_decision_runtime_dependency": False,
        "writer_dispatch_performed": False,
    }
