#!/usr/bin/env python3
"""Validate the rejected executable-profit research evidence package.

This validator deliberately has no release mode.  A valid package proves only
that the bound retrospective research was rejected and that every publication
surface remains disabled.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = Path(__file__).resolve().with_name("research_state.json")
SCHEMA = "dc20_executable_profit_rejected_research_evidence_v1"
STATUS = "RESEARCH_NOT_READY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_INPUTS = {
    "design_contract",
    "historical_top10_ledger",
    "historical_top10_manifest",
    "frozen_three_engine_oof",
    "strict_sse_calendar",
    "v2_runner",
    "v2_tests",
}
REQUIRED_CANDIDATES = {"lr_distribution", "hgb_distribution"}
EXPECTED_INPUT_BINDINGS = {
    "design_contract": {
        "path": "models/decision_executable_profit_shadow_contract.json",
        "sha256": "95f1953ca32afba9e92a40b717d8d02494bb71f3537e2d95a99e3b67cdd9cac2",
    },
    "historical_top10_ledger": {
        "path": "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz",
        "sha256": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd",
    },
    "historical_top10_manifest": {
        "path": "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
        "sha256": "3fd457dbe8438b28bbd80d0521ebd9a2ba2d17845be019412238b7898cce69f5",
    },
    "frozen_three_engine_oof": {
        "path": "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
        "sha256": "c768cb0eb019fba6be7ca41284841006195dd54bf4d641f426d2fbbf513a4ebd",
    },
    "strict_sse_calendar": {
        "path": "data/market/trade_cal_sse.csv",
        "sha256": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748",
    },
    "v2_runner": {
        "path": "work/executable-profit-model-v2-20260824/prototype_v2.py",
        "sha256": "d9c14a1e94d46ce7771562bf3feedcf6579286aafbbdea26e2fb6ba673a59682",
    },
    "v2_tests": {
        "path": "work/executable-profit-model-v2-20260824/tests/test_prototype_v2.py",
        "sha256": "9423aa23d5d3d702e5001f55cacd61deb065b3559bc33d24cfe2509507bdd41c",
    },
}
EXPECTED_PROMOTION_SOURCE = {
    "path": "src/top10decision/decision/three_engine_models.py",
    "sha256": "f7358d952fef888d1614672128c1ab524add02d4863bac7e45217550b842fb34",
}
APPROVED_PROMOTION_SOURCE_ROTATION = {
    "current_sha256": (
        "9a4a2405e3b95af9f1c05100aa8b97dc8b3ee62d63b4dda12e13f7f0fcd1de4c"
    ),
    "rotation_id": "dc20_restore_canonical_source_external_runtime_20260826",
    "evidence_path": "models/decision_source_surface_rotation_20260824.json",
    "classification": "promotion_only_primary_d_loader",
}
EXPECTED_CANDIDATE_BINDINGS = {
    "lr_distribution": {
        "validation_report": {
            "path": "work/executable-profit-model-v2-20260824/outputs-lr/validation_report.json",
            "sha256": "168f9c8629d1c88f2645bd9130bdb1d9bdc130279dbc67772d4d6491e86764d9",
        },
        "oof": {
            "path": "work/executable-profit-model-v2-20260824/outputs-lr/oof_lr_distribution.csv.gz",
            "sha256": "62cb3aa79acdd28bd83f7a731f05610d1a3dbc2f960aa699d84aaf93cab84ae7",
            "rows": 4996,
            "dates": 610,
        },
    },
    "hgb_distribution": {
        "validation_report": {
            "path": "work/executable-profit-model-v2-20260824/outputs-hgb/validation_report.json",
            "sha256": "8abd8b7605fbdc4d60e5f9cf843f48d276e20f67d49c5bcce1c455d5d988ac04",
        },
        "oof": {
            "path": "work/executable-profit-model-v2-20260824/outputs-hgb/oof_hgb_distribution.csv.gz",
            "sha256": "593f24d245a2ee87ddf43bbe330512474c263bb3bffbcea0a1e9eb6b7e50ebc8",
            "rows": 4996,
            "dates": 610,
        },
    },
}


class ValidationError(ValueError):
    """Raised when rejected-research evidence is incomplete or unsafe."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    raise ValidationError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    return value


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    _require(not path.is_absolute(), f"file binding must be repo-relative: {value}")
    resolved_root = repo_root.resolve()
    resolved = (resolved_root / path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        _fail(f"file binding escapes repository root: {value}")
    return resolved


def _verify_file_binding(repo_root: Path, binding: Mapping[str, Any], name: str) -> Path:
    path_value = binding.get("path")
    expected_sha = binding.get("sha256")
    _require(isinstance(path_value, str) and path_value, f"{name}.path is missing")
    _require(
        isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha) is not None,
        f"{name}.sha256 is invalid",
    )
    path = _resolve(repo_root, path_value)
    _require(path.is_file(), f"{name} file is missing: {path}")
    actual_sha = sha256_path(path)
    _require(actual_sha == expected_sha, f"{name} hash drifted: {actual_sha}")
    return path


def _verify_historical_promotion_source(
    repo_root: Path,
    binding: Mapping[str, Any],
) -> Path:
    """Verify an immutable old source pin through its exact reviewed rotation."""

    _require(
        {"path": binding.get("path"), "sha256": binding.get("sha256")}
        == EXPECTED_PROMOTION_SOURCE,
        "promotion source authoritative binding drifted",
    )
    source_path = _resolve(repo_root, str(binding["path"]))
    _require(source_path.is_file(), f"promotion source file is missing: {source_path}")
    actual_sha256 = sha256_path(source_path)
    if actual_sha256 == binding.get("sha256"):
        return source_path

    approved = APPROVED_PROMOTION_SOURCE_ROTATION
    _require(
        actual_sha256 == approved["current_sha256"],
        f"promotion.frozen_source_code hash drifted: {actual_sha256}",
    )
    freeze_path = _resolve(repo_root, "models/decision_model_freeze.json")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    rotation_ref = _mapping(
        freeze.get("source_surface_rotation"),
        "freeze.source_surface_rotation",
    )
    _require(
        rotation_ref.get("schema_version")
        == "decision_source_surface_rotation_v1"
        and rotation_ref.get("rotation_id") == approved["rotation_id"]
        and rotation_ref.get("evidence_path") == approved["evidence_path"],
        "promotion source rotation reference drifted",
    )
    evidence_path = _resolve(repo_root, str(rotation_ref["evidence_path"]))
    _require(evidence_path.is_file(), "promotion source rotation evidence is missing")
    _require(
        sha256_path(evidence_path) == rotation_ref.get("evidence_sha256"),
        "promotion source rotation evidence hash drifted",
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    _require(
        evidence.get("rotation_id") == approved["rotation_id"],
        "promotion source rotation id drifted",
    )
    changes = evidence.get("pin_changes")
    _require(isinstance(changes, list), "promotion source rotation changes are missing")
    matches = [
        item
        for item in changes
        if isinstance(item, Mapping) and item.get("path") == binding.get("path")
    ]
    _require(len(matches) == 1, "promotion source rotation is not unique")
    change = matches[0]
    _require(
        change.get("prior_sha256") == binding.get("sha256")
        and change.get("current_sha256") == actual_sha256
        and change.get("classification") == approved["classification"],
        "promotion source reviewed rotation drifted",
    )
    freeze_pins = _mapping(freeze.get("pinned_files"), "freeze.pinned_files")
    _require(
        freeze_pins.get(str(binding["path"])) == actual_sha256,
        "promotion source active freeze pin drifted",
    )
    return source_path


def _same_number(actual: Any, expected: Any, name: str) -> float:
    _require(
        isinstance(actual, (int, float)) and not isinstance(actual, bool),
        f"{name} actual value is not numeric",
    )
    _require(
        isinstance(expected, (int, float)) and not isinstance(expected, bool),
        f"{name} expected value is not numeric",
    )
    actual_number = float(actual)
    expected_number = float(expected)
    _require(
        math.isfinite(actual_number) and math.isfinite(expected_number),
        f"{name} is not finite",
    )
    _require(
        math.isclose(actual_number, expected_number, rel_tol=1e-12, abs_tol=1e-15),
        f"{name} drifted: {actual_number} != {expected_number}",
    )
    return actual_number


def _csv_shape(path: Path) -> tuple[int, int]:
    if path.suffix == ".gz":
        handle_context = gzip.open(path, mode="rt", encoding="utf-8", newline="")
    else:
        handle_context = path.open(mode="rt", encoding="utf-8", newline="")
    with handle_context as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None, f"OOF CSV has no header: {path}")
        _require("signal_date" in reader.fieldnames, "OOF CSV lacks signal_date")
        rows = 0
        dates: set[str] = set()
        for row in reader:
            rows += 1
            dates.add(str(row["signal_date"]))
    return rows, len(dates)


def _validate_candidate(
    *,
    repo_root: Path,
    name: str,
    evidence: Mapping[str, Any],
    input_sha: Mapping[str, str],
) -> dict[str, Any]:
    _require(name in REQUIRED_CANDIDATES, f"unexpected candidate: {name}")
    report_binding = _mapping(evidence.get("validation_report"), f"{name}.validation_report")
    oof_binding = _mapping(evidence.get("oof"), f"{name}.oof")
    report_path = _verify_file_binding(repo_root, report_binding, f"{name}.validation_report")
    oof_path = _verify_file_binding(repo_root, oof_binding, f"{name}.oof")
    _require(
        {"path": report_binding.get("path"), "sha256": report_binding.get("sha256")}
        == EXPECTED_CANDIDATE_BINDINGS[name]["validation_report"],
        f"{name} authoritative report binding drifted",
    )
    _require(
        {
            "path": oof_binding.get("path"),
            "sha256": oof_binding.get("sha256"),
            "rows": oof_binding.get("rows"),
            "dates": oof_binding.get("dates"),
        }
        == EXPECTED_CANDIDATE_BINDINGS[name]["oof"],
        f"{name} authoritative OOF binding drifted",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    _require(report.get("status") == "NOT_READY", f"{name} report status is publishable")
    _require(report.get("shadow_only") is True, f"{name} report is not shadow-only")
    _require(report.get("front_end_allowed") is False, f"{name} report enables frontend")
    _require(
        report.get("official_trade_action_allowed") is False,
        f"{name} report enables a trade action",
    )
    _require(report.get("promotion_model_touched") is False, f"{name} touched promotion")
    _require(
        report.get("retrospective_window_has_been_viewed") is True,
        f"{name} does not admit retrospective unblinding",
    )
    _require(
        report.get("independent_untouched_confirmation_available") is False,
        f"{name} falsely claims an untouched confirmation set",
    )

    candidates = _mapping(report.get("candidates"), f"{name}.report.candidates")
    _require(set(candidates) == {name}, f"{name} report candidate identity drifted")
    candidate = _mapping(candidates[name], f"{name}.report.candidate")
    _require(candidate.get("status") == "NOT_READY", f"{name} candidate status drifted")
    _require(candidate.get("front_end_allowed") is False, f"{name} candidate enables frontend")
    _require(
        candidate.get("official_trade_action_allowed") is False,
        f"{name} candidate enables trading",
    )
    _require(
        candidate.get("partial_prototype_historical_checks_passed") is False,
        f"{name} unexpectedly passes partial prototype checks",
    )
    _require(
        candidate.get("retrospective_confirmation_is_not_forward_release_evidence") is True,
        f"{name} treats retrospective evidence as forward evidence",
    )
    _require(
        candidate.get("prototype_check_count_is_completion_percentage") is False,
        f"{name} treats a check count as completion",
    )

    report_inputs = _mapping(report.get("inputs"), f"{name}.report.inputs")
    _require(
        _mapping(report_inputs.get("ledger"), f"{name}.report.inputs.ledger").get("sha256")
        == input_sha["historical_top10_ledger"],
        f"{name} ledger input binding drifted",
    )
    _require(
        _mapping(
            report_inputs.get("promotion_oof_and_frozen_p_fill"),
            f"{name}.report.inputs.frozen_oof",
        ).get("sha256")
        == input_sha["frozen_three_engine_oof"],
        f"{name} frozen OOF input binding drifted",
    )
    _require(
        _mapping(
            report_inputs.get("strict_sse_calendar"),
            f"{name}.report.inputs.calendar",
        ).get("sha256")
        == input_sha["strict_sse_calendar"],
        f"{name} calendar input binding drifted",
    )

    report_oof = _mapping(candidate.get("oof_artifact"), f"{name}.report.oof_artifact")
    _require(report_oof.get("sha256") == oof_binding.get("sha256"), f"{name} OOF report hash drifted")
    rows, dates = _csv_shape(oof_path)
    _require(rows == int(oof_binding.get("rows", -1)), f"{name} OOF row count drifted")
    _require(dates == int(oof_binding.get("dates", -1)), f"{name} OOF date count drifted")
    _require(rows == int(report_oof.get("rows", -1)), f"{name} report OOF rows drifted")
    _require(dates == int(report_oof.get("dates", -1)), f"{name} report OOF dates drifted")

    observed = _mapping(evidence.get("observed_rejection_metrics"), f"{name}.metrics")
    retrospective = _mapping(
        candidate.get("retrospective_confirmation"), f"{name}.retrospective"
    )
    panel = _mapping(retrospective.get("common_mature_panel"), f"{name}.panel")
    policies = _mapping(panel.get("policies"), f"{name}.panel.policies")
    top2 = _mapping(policies.get("executable_profit_top2"), f"{name}.panel.top2")
    probability = _mapping(retrospective.get("joint_probability"), f"{name}.probability")
    lifts = _mapping(panel.get("paired_lifts"), f"{name}.paired_lifts")
    pfill_lift = _mapping(lifts.get("frozen_p_fill_top2"), f"{name}.pfill_lift")
    return_lift = _mapping(pfill_lift.get("return"), f"{name}.return_lift")
    profit_lift = _mapping(pfill_lift.get("profit_rate"), f"{name}.profit_lift")
    brier_ci = _mapping(
        probability.get("brier_improvement_bootstrap"), f"{name}.brier_ci"
    )

    _require(
        int(retrospective.get("signal_dates", -1))
        == int(observed.get("retrospective_signal_dates", -1))
        == 180,
        f"{name} retrospective date count drifted",
    )
    _require(
        retrospective.get("start") == observed.get("retrospective_start"),
        f"{name} retrospective start drifted",
    )
    _require(
        retrospective.get("end") == observed.get("retrospective_end"),
        f"{name} retrospective end drifted",
    )
    _require(
        int(panel.get("common_mature_dates", -1))
        == int(observed.get("common_mature_dates", -1)),
        f"{name} common mature date count drifted",
    )
    mean_return = _same_number(top2.get("mean_return"), observed.get("top2_mean_return"), f"{name}.mean_return")
    stress_return = _same_number(
        top2.get("mean_stress_return"),
        observed.get("top2_double_cost_mean_return"),
        f"{name}.stress_return",
    )
    brier_improvement = _same_number(
        probability.get("brier_improvement"),
        observed.get("joint_brier_improvement"),
        f"{name}.brier_improvement",
    )
    brier_low = _same_number(
        brier_ci.get("ci95_low"),
        observed.get("joint_brier_ci95_low"),
        f"{name}.brier_ci_low",
    )
    brier_high = _same_number(
        brier_ci.get("ci95_high"),
        observed.get("joint_brier_ci95_high"),
        f"{name}.brier_ci_high",
    )
    return_low = _same_number(
        return_lift.get("ci95_low"),
        observed.get("return_lift_vs_pfill_ci95_low"),
        f"{name}.return_lift_ci_low",
    )
    return_high = _same_number(
        return_lift.get("ci95_high"),
        observed.get("return_lift_vs_pfill_ci95_high"),
        f"{name}.return_lift_ci_high",
    )
    profit_low = _same_number(
        profit_lift.get("ci95_low"),
        observed.get("profit_lift_vs_pfill_ci95_low"),
        f"{name}.profit_lift_ci_low",
    )
    profit_high = _same_number(
        profit_lift.get("ci95_high"),
        observed.get("profit_lift_vs_pfill_ci95_high"),
        f"{name}.profit_lift_ci_high",
    )

    _require(mean_return < 0.0, f"{name} Top2 mean return is not negative")
    _require(stress_return < 0.0, f"{name} double-cost return is not negative")
    _require(brier_improvement < 0.0, f"{name} Brier improvement is not negative")
    _require(brier_low < 0.0 and brier_high < 0.0, f"{name} Brier CI is not wholly negative")
    _require(return_low < 0.0 < return_high, f"{name} return-lift CI does not cross zero")
    _require(profit_low < 0.0 < profit_high, f"{name} profit-lift CI does not cross zero")

    return {
        "candidate": name,
        "oof_rows": rows,
        "oof_dates": dates,
        "retrospective_dates": 180,
        "common_mature_dates": int(panel["common_mature_dates"]),
        "top2_mean_return": mean_return,
        "top2_double_cost_mean_return": stress_return,
        "rejected": True,
    }


def validate_research_state(
    state_path: Path = DEFAULT_STATE,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    _require(state.get("schema_version") == SCHEMA, "research state schema drifted")
    _require(state.get("status") == STATUS, "research state must remain RESEARCH_NOT_READY")
    _require(state.get("evidence_role") == "REJECTED_RETROSPECTIVE_RESEARCH_ONLY", "evidence role drifted")

    publication = _mapping(state.get("publication_boundary"), "publication_boundary")
    for field in (
        "front_end_allowed",
        "official_trade_action_allowed",
        "model_publish_allowed",
        "production_model_selected",
        "formal_model_artifact_created",
    ):
        _require(publication.get(field) is False, f"publication flag must remain false: {field}")
    _require(
        publication.get("release_validator_or_publish_mode_exists") is False,
        "a release or publish mode must not exist",
    )

    information = _mapping(state.get("information_state"), "information_state")
    _require(information.get("last_180_dates_have_been_viewed") is True, "last 180 dates must be marked viewed")
    _require(
        information.get("independent_untouched_confirmation_set") is False,
        "retrospective data must not be called an untouched confirmation set",
    )
    _require(information.get("forward_release_evidence") is False, "retrospective data cannot be forward evidence")
    _require(
        information.get("role") == "RETROSPECTIVE_EXPLORATION_ONLY",
        "retrospective role drifted",
    )
    _require(
        information.get("retrospective_start") == "20251119"
        and information.get("retrospective_end") == "20260814"
        and information.get("retrospective_signal_dates") == 180,
        "retrospective information window drifted",
    )

    interpretation = _mapping(state.get("interpretation"), "interpretation")
    _require(
        interpretation.get("prototype_check_count_is_completion_percentage") is False,
        "prototype checks must not be represented as completion",
    )
    _require(
        interpretation.get("candidate_family_was_locked_before_viewing") is False,
        "state falsely claims preregistration",
    )
    _require(
        interpretation.get("formal_release_protocol_implemented") is False,
        "state falsely claims a formal release protocol",
    )
    _require(
        interpretation.get("gate_count_or_check_count_can_authorize_release") is False,
        "state treats a diagnostic count as release authority",
    )

    authority = _mapping(state.get("authority"), "authority")
    reviewed_base = authority.get("reviewed_base_commit_sha")
    _require(
        isinstance(reviewed_base, str) and COMMIT_RE.fullmatch(reviewed_base) is not None,
        "reviewed base commit is invalid",
    )
    _require(
        reviewed_base == "cdbc43f67401c876d98f61585bea6d9375117e5b",
        "reviewed base commit drifted",
    )
    _require(authority.get("repository") == "njedu2023-prog/DC20", "repository authority drifted")
    _require(authority.get("branch") == "main", "branch authority drifted")
    _require(
        authority.get("reviewed_base_role")
        == "exact research input and promotion-isolation baseline",
        "reviewed base role drifted",
    )

    inputs = _mapping(state.get("inputs"), "inputs")
    _require(set(inputs) == REQUIRED_INPUTS, "research input inventory drifted")
    input_paths: dict[str, Path] = {}
    input_sha: dict[str, str] = {}
    for name in sorted(REQUIRED_INPUTS):
        binding = _mapping(inputs[name], f"inputs.{name}")
        input_paths[name] = _verify_file_binding(repo_root, binding, f"inputs.{name}")
        input_sha[name] = str(binding["sha256"])
        _require(
            {"path": binding.get("path"), "sha256": binding.get("sha256")}
            == EXPECTED_INPUT_BINDINGS[name],
            f"inputs.{name} authoritative binding drifted",
        )

    manifest = json.loads(input_paths["historical_top10_manifest"].read_text(encoding="utf-8"))
    _require(
        _mapping(manifest.get("output"), "manifest.output").get("sha256")
        == input_sha["historical_top10_ledger"],
        "manifest ledger binding drifted",
    )
    manifest_inputs = _mapping(manifest.get("inputs"), "manifest.inputs")
    _require(
        _mapping(manifest_inputs.get("promotion_oof_top10"), "manifest.oof").get("sha256")
        == input_sha["frozen_three_engine_oof"],
        "manifest OOF binding drifted",
    )
    _require(
        _mapping(manifest_inputs.get("strict_sse_calendar"), "manifest.calendar").get("sha256")
        == input_sha["strict_sse_calendar"],
        "manifest calendar binding drifted",
    )

    promotion = _mapping(state.get("promotion_isolation"), "promotion_isolation")
    _require(promotion.get("promotion_model_touched") is False, "promotion touched flag drifted")
    _require(promotion.get("promotion_rank_changed") is False, "promotion rank changed flag drifted")
    source_binding = _mapping(promotion.get("frozen_source_code"), "promotion.frozen_source_code")
    source_path = _verify_historical_promotion_source(repo_root, source_binding)
    contract = json.loads(input_paths["design_contract"].read_text(encoding="utf-8"))
    code_pins = _mapping(
        _mapping(contract.get("promotion_identity"), "contract.promotion_identity").get(
            "code_and_runtime_pins"
        ),
        "contract.code_and_runtime_pins",
    )
    relative_source = str(source_path.relative_to(repo_root))
    _require(code_pins.get(relative_source) == source_binding.get("sha256"), "promotion source pin drifted")

    candidates = _mapping(state.get("candidate_evidence"), "candidate_evidence")
    _require(set(candidates) == REQUIRED_CANDIDATES, "candidate evidence inventory drifted")
    summaries = [
        _validate_candidate(
            repo_root=repo_root,
            name=name,
            evidence=_mapping(candidates[name], f"candidate_evidence.{name}"),
            input_sha=input_sha,
        )
        for name in sorted(REQUIRED_CANDIDATES)
    ]

    return {
        "valid": True,
        "schema_version": SCHEMA,
        "status": STATUS,
        "reviewed_base_commit_sha": reviewed_base,
        "front_end_allowed": False,
        "official_trade_action_allowed": False,
        "model_publish_allowed": False,
        "promotion_model_touched": False,
        "last_180_dates_have_been_viewed": True,
        "candidates": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    try:
        summary = validate_research_state(
            args.state.resolve(), repo_root=args.repo_root.resolve()
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
