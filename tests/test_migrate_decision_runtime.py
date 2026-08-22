from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "migrate_decision_runtime.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "migrate_decision_runtime.yml"
SPEC = importlib.util.spec_from_file_location("migrate_decision_runtime", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)


BASE_SHA = "1" * 40
BASE_TREE_SHA = "2" * 40
SIGNAL_DATE = "20260821"
REPORT_DATE = "20260824"
EXIT_DATE = "20260825"


def _binding() -> object:
    return MIGRATION.MigrationBinding(
        signal_date=SIGNAL_DATE,
        report_date=REPORT_DATE,
        exec_date=REPORT_DATE,
        exit_date=EXIT_DATE,
        evaluation_path=f"outputs/decision/eval_{REPORT_DATE}.json",
        report_path=f"outputs/decision/decision_report_{REPORT_DATE}.md",
        candidates_path=f"data/decision/decision_candidates_{SIGNAL_DATE}.csv",
        execution_path=f"data/decision/decision_execution_{REPORT_DATE}.csv",
    )


def _action_payload() -> dict[str, object]:
    return {
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "signal_date": SIGNAL_DATE,
        "report_date": REPORT_DATE,
        "exec_date": REPORT_DATE,
        "exit_date": EXIT_DATE,
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "formal_buy_count": 0,
        "guidance_only": True,
        "broker_connected": False,
        "order_execution": "manual_only",
        "candidates": [
            {
                "ts_code": "000001.SZ",
                "stage_transition": "2→3",
                "action": "WATCH",
                "target_weight": 0.0,
            }
        ],
        "stage_watchlist": [
            {
                "ts_code": "000001.SZ",
                "stage_transition": "2→3",
                "action": "WATCH",
                "target_weight": 0.0,
            }
        ],
    }


def _strict_receipt_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_bundle(
    root: Path,
    *,
    action_override: dict[str, object] | None = None,
    receipt_override: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object]]:
    bundle = root / "bundle"
    files = bundle / "files"
    latest_path = "outputs/decision/action_plan_latest.json"
    dated_path = f"outputs/decision/action_plan_{REPORT_DATE}.json"
    action = MIGRATION.annotate_retrospective_action(
        action_override or _action_payload(), _binding(), BASE_SHA
    )
    contents: dict[str, bytes] = {
        latest_path: (json.dumps(action, ensure_ascii=False, indent=2) + "\n").encode(),
        dated_path: (json.dumps(action, ensure_ascii=False, indent=2) + "\n").encode(),
    }
    for relative, data in contents.items():
        destination = files / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    source_paths = set(MIGRATION.required_receipt_source_paths(SIGNAL_DATE))
    source_paths.update(
        relative
        for pair in MIGRATION.TRUTH_LEDGER_BINDINGS.values()
        for relative in pair
    )
    source_evidence = {
        relative: {
            "git_blob_sha1": hashlib.sha1(relative.encode()).hexdigest(),
            "sha256": hashlib.sha256(relative.encode()).hexdigest(),
            "size": len(relative.encode()),
        }
        for relative in sorted(source_paths)
    }
    numeric_runtime = {
        "schema_version": MIGRATION.NUMERIC_RUNTIME_SCHEMA,
        "host_contract": MIGRATION.NUMERIC_RUNTIME_HOST,
        "launcher_sha256": source_evidence[MIGRATION.NUMERIC_LAUNCHER_PATH]["sha256"],
        "numpy_cpu_dispatch_cap": "X86_V3",
        "numpy_x86_v4_disabled": True,
        "openblas_coretype": "Haswell",
        "target": Path(MIGRATION.NUMERIC_REPLAY_TARGET_PATH).name,
        "target_sha256": source_evidence[MIGRATION.NUMERIC_REPLAY_TARGET_PATH]["sha256"],
        "threadpools": [
            {
                "architecture": "Haswell",
                "internal_api": "openblas",
                "num_threads": 1,
                "prefix": "libscipy_openblas",
                "version": "0.3.30",
            },
            {
                "architecture": None,
                "internal_api": "openmp",
                "num_threads": 1,
                "prefix": "libgomp",
                "version": None,
            },
        ],
    }
    action_rebuild_stability = MIGRATION.build_action_rebuild_stability(action, action)
    receipt: dict[str, object] = {
        "schema_version": MIGRATION.RECEIPT_SCHEMA,
        "allowlist_version": MIGRATION.ALLOWLIST_VERSION,
        "status": "candidate_generated",
        "mode": "publish_candidate",
        "base_sha": BASE_SHA,
        "base_tree_sha": BASE_TREE_SHA,
        "signal_date": SIGNAL_DATE,
        "report_date": REPORT_DATE,
        "exec_date": REPORT_DATE,
        "exit_date": EXIT_DATE,
        "timing": "RETROSPECTIVE",
        "live_delivery_met": False,
        "execution_or_fill_claimed": False,
        "replay_source": "frozen_canonical_replay",
        "replay_status": "pass",
        "replay_report_sha256": "4" * 64,
        "replay_numeric_runtime": numeric_runtime,
        "action_rebuild_stability": action_rebuild_stability,
        "freeze_active": False,
        "forced_inactive": True,
        "pins_enforced": True,
        "validators_passed": True,
        "post_prune_validators_passed": True,
        "truth_binding_summary": {
            "model_truth_metrics_exact": True,
            "action_truth_ledgers_exact": True,
            "action_observation_statistics_exact": True,
            "action_watchlist_truth_exact": True,
            "watchlist_rows": 1,
            "matched_observation_rows": 0,
        },
        "validator_summary": {"before_prune": {}, "after_prune": {}},
        "changed_paths": sorted(contents),
        "restored_paths": ["outputs/decision/action_plan_20260817.json"],
        "output_sha256": {
            relative: hashlib.sha256(data).hexdigest()
            for relative, data in contents.items()
        },
        "output_size": {relative: len(data) for relative, data in contents.items()},
        "base_blob_sha1": {relative: "3" * 40 for relative in contents},
        "source_evidence": source_evidence,
        "truth_reference_evidence": {
            relative: source_evidence[relative]
            for pair in MIGRATION.TRUTH_LEDGER_BINDINGS.values()
            for relative in pair
        },
    }
    if receipt_override:
        receipt.update(receipt_override)
    receipt_bytes = _strict_receipt_bytes(receipt)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "migration-receipt.json").write_bytes(receipt_bytes)
    (bundle / "migration-receipt.sha256").write_text(
        hashlib.sha256(receipt_bytes).hexdigest() + "\n", encoding="ascii"
    )
    return bundle, receipt


def _rewrite_receipt(bundle: Path, payload: dict[str, object]) -> None:
    rendered = _strict_receipt_bytes(payload)
    (bundle / "migration-receipt.json").write_bytes(rendered)
    (bundle / "migration-receipt.sha256").write_text(
        hashlib.sha256(rendered).hexdigest() + "\n", encoding="ascii"
    )


def test_candidate_allowlist_is_exact_and_finite() -> None:
    paths = MIGRATION.candidate_paths(SIGNAL_DATE, REPORT_DATE)
    assert paths == {
        "outputs/auction_v3/models/model_meta_latest.json",
        "outputs/auction_v3/metrics/backtest_latest.json",
        "outputs/auction_v3/metrics/backtest_top10_latest.csv",
        "outputs/auction_v3/metrics/backtest_trade_selector_oos_latest.csv",
        "outputs/auction_v3/predictions/pred_latest.csv",
        f"outputs/auction_v3/predictions/pred_{SIGNAL_DATE}.csv",
        "outputs/decision/action_plan_latest.json",
        f"outputs/decision/action_plan_{REPORT_DATE}.json",
        "outputs/decision/report_index.json",
    }
    assert f"outputs/decision/action_plan_20260817.json" not in paths
    assert f"data/decision/decision_execution_{REPORT_DATE}.csv" not in paths
    assert not any("*" in path for path in paths)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.candidate_paths("20260821junk", REPORT_DATE)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.candidate_paths(SIGNAL_DATE, "20260230")


def test_strict_json_rejects_duplicate_keys_and_nonfinite(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"base_sha":"a","base_sha":"b"}\n', encoding="utf-8")
    with pytest.raises(MIGRATION.MigrationError, match="duplicate JSON key"):
        MIGRATION.load_strict_json(duplicate, "duplicate")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}\n', encoding="utf-8")
    with pytest.raises(MIGRATION.MigrationError, match="non-finite"):
        MIGRATION.load_strict_json(nonfinite, "nonfinite")


def test_numeric_runtime_evidence_is_exact_and_source_bound(tmp_path: Path) -> None:
    _bundle, receipt = _write_bundle(tmp_path)
    evidence = receipt["replay_numeric_runtime"]
    sources = receipt["source_evidence"]
    validated = MIGRATION.validate_numeric_runtime_evidence(
        evidence,
        expected_launcher_sha256=sources[MIGRATION.NUMERIC_LAUNCHER_PATH]["sha256"],
        expected_target_sha256=sources[MIGRATION.NUMERIC_REPLAY_TARGET_PATH]["sha256"],
    )
    assert validated == evidence
    assert validated is not evidence


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("extra_top_key", True),
        ("missing_top_key", None),
        ("schema", "dc20_deterministic_numeric_runtime_v0"),
        ("host", "local_mac"),
        ("cap", "X86_V4"),
        ("disabled", False),
        ("coretype", "Zen"),
        ("target", "run_auction_v3.py"),
        ("threads", 2),
        ("threads", True),
        ("threads", 1.0),
        ("architecture", "Zen"),
        ("unknown_api", "mkl"),
        ("missing_openmp", None),
        ("missing_openblas", None),
        ("empty_pools", None),
        ("pool_extra_key", True),
        ("duplicate_pool", None),
        ("reversed_pools", None),
    ],
)
def test_numeric_runtime_evidence_rejects_contract_drift(
    tmp_path: Path, kind: str, value: object
) -> None:
    _bundle, receipt = _write_bundle(tmp_path)
    evidence = copy.deepcopy(receipt["replay_numeric_runtime"])
    if kind == "extra_top_key":
        evidence["unexpected"] = value
    elif kind == "missing_top_key":
        del evidence["openblas_coretype"]
    elif kind == "schema":
        evidence["schema_version"] = value
    elif kind == "host":
        evidence["host_contract"] = value
    elif kind == "cap":
        evidence["numpy_cpu_dispatch_cap"] = value
    elif kind == "disabled":
        evidence["numpy_x86_v4_disabled"] = value
    elif kind == "coretype":
        evidence["openblas_coretype"] = value
    elif kind == "target":
        evidence["target"] = value
    elif kind == "threads":
        evidence["threadpools"][0]["num_threads"] = value
    elif kind == "architecture":
        evidence["threadpools"][0]["architecture"] = value
    elif kind == "unknown_api":
        evidence["threadpools"][0]["internal_api"] = value
    elif kind == "missing_openmp":
        evidence["threadpools"] = evidence["threadpools"][:1]
    elif kind == "missing_openblas":
        evidence["threadpools"] = evidence["threadpools"][1:]
    elif kind == "empty_pools":
        evidence["threadpools"] = []
    elif kind == "pool_extra_key":
        evidence["threadpools"][0]["filepath"] = "/secret/library.so"
    elif kind == "duplicate_pool":
        evidence["threadpools"].append(copy.deepcopy(evidence["threadpools"][0]))
    elif kind == "reversed_pools":
        evidence["threadpools"].reverse()
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(kind)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.validate_numeric_runtime_evidence(evidence)


@pytest.mark.parametrize("field", ["launcher_sha256", "target_sha256"])
def test_verify_rejects_numeric_hash_not_bound_to_source(
    tmp_path: Path, field: str
) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    receipt["replay_numeric_runtime"][field] = "f" * 64
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError, match="differs from exact source"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_run_frozen_replay_captures_numeric_runtime_in_same_temp_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "run_deterministic_numeric.py"
    target = scripts / "replay_frozen_canonical_v2.py"
    launcher.write_text("# deterministic launcher\n", encoding="utf-8")
    target.write_text("# frozen replay\n", encoding="utf-8")
    report_path = tmp_path / "replay-report.json"
    evidence_path = tmp_path / "numeric-runtime.json"
    captured: dict[str, object] = {}

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        label: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, cwd=cwd, label=label, extra_env=extra_env)
        report_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "diagnostic_mode": "workspace_only_forced_frozen_canonical_v2",
                    "force_prediction": True,
                    "runtime_validation": {
                        "validated": True,
                        "canonical_v2_enforced": True,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": MIGRATION.NUMERIC_RUNTIME_SCHEMA,
                    "host_contract": MIGRATION.NUMERIC_RUNTIME_HOST,
                    "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                    "numpy_cpu_dispatch_cap": "X86_V3",
                    "numpy_x86_v4_disabled": True,
                    "openblas_coretype": "Haswell",
                    "target": target.name,
                    "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "threadpools": [
                        {
                            "architecture": "Haswell",
                            "internal_api": "openblas",
                            "num_threads": 1,
                            "prefix": "libopenblas",
                            "version": "0.3.30",
                        },
                        {
                            "architecture": None,
                            "internal_api": "openmp",
                            "num_threads": 1,
                            "prefix": "libgomp",
                            "version": None,
                        },
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(MIGRATION, "_run_checked", fake_run_checked)
    replay = MIGRATION.run_frozen_replay(
        root, _binding(), report_path, evidence_path
    )
    assert replay["numeric_runtime"]["target"] == target.name
    assert captured["command"] == [
        sys.executable,
        str(launcher),
        str(target),
        "--root",
        str(root),
        "--signal-date",
        SIGNAL_DATE,
        "--report-date",
        REPORT_DATE,
        "--report",
        str(report_path),
    ]
    assert captured["cwd"] == root
    assert captured["label"] == "frozen canonical V2 replay"
    assert captured["extra_env"] == {
        MIGRATION.NUMERIC_EVIDENCE_ENV: str(evidence_path.absolute())
    }


def test_run_frozen_replay_fails_if_launcher_omits_numeric_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_deterministic_numeric.py").write_text("# launcher\n", encoding="utf-8")
    (scripts / "replay_frozen_canonical_v2.py").write_text("# replay\n", encoding="utf-8")
    report_path = tmp_path / "replay-report.json"
    evidence_path = tmp_path / "numeric-runtime.json"

    def fake_run_checked(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        report_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "diagnostic_mode": "workspace_only_forced_frozen_canonical_v2",
                    "force_prediction": True,
                    "runtime_validation": {
                        "validated": True,
                        "canonical_v2_enforced": True,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(MIGRATION, "_run_checked", fake_run_checked)
    with pytest.raises(MIGRATION.MigrationError, match="numeric runtime evidence"):
        MIGRATION.run_frozen_replay(root, _binding(), report_path, evidence_path)


def test_run_frozen_replay_rejects_unsafe_numeric_evidence_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    report_path = tmp_path / "replay-report.json"
    existing = tmp_path / "existing-runtime.json"
    existing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(MIGRATION.MigrationError, match="must not already exist"):
        MIGRATION.run_frozen_replay(root, _binding(), report_path, existing)

    existing.unlink()
    target = tmp_path / "target-runtime.json"
    target.write_text("{}\n", encoding="utf-8")
    existing.symlink_to(target)
    with pytest.raises(MIGRATION.MigrationError, match="must not already exist"):
        MIGRATION.run_frozen_replay(root, _binding(), report_path, existing)

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(MIGRATION.MigrationError, match="replay temporary directory"):
        MIGRATION.run_frozen_replay(
            root, _binding(), report_path, other / "numeric-runtime.json"
        )


def _write_binding_root(root: Path) -> dict[str, object]:
    from top10decision.decision.action_plan import build_report_index

    decision = root / "outputs" / "decision"
    decision.mkdir(parents=True)
    newer_report_date = "20260825"
    for report_date in (newer_report_date, REPORT_DATE):
        (decision / f"decision_report_{report_date}.md").write_text(
            f"report {report_date}\n",
            encoding="utf-8",
        )
        (decision / f"eval_{report_date}.json").write_text(
            json.dumps(
                {
                    "signal_date": SIGNAL_DATE if report_date == REPORT_DATE else REPORT_DATE,
                    "exec_date": report_date,
                    "exit_date": EXIT_DATE if report_date == REPORT_DATE else "20260826",
                    "paths": {
                        "decision_report": (
                            f"outputs/decision/decision_report_{report_date}.md"
                        ),
                        "candidates": (
                            f"data/decision/decision_candidates_{SIGNAL_DATE}.csv"
                        ),
                        "execution": (
                            f"data/decision/decision_execution_{report_date}.csv"
                        ),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    (decision / f"action_plan_{REPORT_DATE}.json").write_text(
        json.dumps({"report_date": REPORT_DATE}) + "\n",
        encoding="utf-8",
    )
    for relative in (
        f"data/decision/decision_candidates_{SIGNAL_DATE}.csv",
        f"data/decision/decision_execution_{REPORT_DATE}.csv",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("header\n", encoding="utf-8")
    index = build_report_index(root, REPORT_DATE)
    (decision / "report_index.json").write_text(
        json.dumps(index) + "\n",
        encoding="utf-8",
    )
    return index


def test_discover_binding_uses_latest_action_not_newer_report(tmp_path: Path) -> None:
    index = _write_binding_root(tmp_path)
    assert index["latest_report_date"] == "20260825"
    assert index["latest_action_report_date"] == REPORT_DATE
    assert MIGRATION.discover_binding(tmp_path, SIGNAL_DATE) == _binding()


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        ("latest_action_date", "latest_action_report_date"),
        ("latest_action_url", "latest_action_url"),
        ("hidden_action", "action truth"),
        ("duplicate_report", "duplicate report_date"),
    ),
)
def test_discover_binding_rejects_invalid_latest_action_truth(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    index = _write_binding_root(tmp_path)
    if tamper == "latest_action_date":
        index["latest_action_report_date"] = "20260825"
        index["latest_action_url"] = "outputs/decision/action_plan_20260825.json"
    elif tamper == "latest_action_url":
        index["latest_action_url"] = "outputs/decision/action_plan_20260825.json"
    elif tamper == "hidden_action":
        row = next(
            item for item in index["reports"] if item["report_date"] == REPORT_DATE
        )
        row["action_available"] = False
        row.pop("action_url")
    elif tamper == "duplicate_report":
        index["reports"].append(copy.deepcopy(index["reports"][-1]))
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(tamper)
    (tmp_path / "outputs" / "decision" / "report_index.json").write_text(
        json.dumps(index) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(MIGRATION.MigrationError, match=message):
        MIGRATION.discover_binding(tmp_path, SIGNAL_DATE)


def test_action_annotation_is_explicitly_retrospective_and_nonexecuting() -> None:
    action = MIGRATION.annotate_retrospective_action(
        _action_payload(), _binding(), BASE_SHA
    )
    assert action["publication_timing"] == "RETROSPECTIVE"
    assert action["live_delivery_met"] is False
    assert action["execution_or_fill_claimed"] is False
    assert action["migration"] == {
        "schema_version": MIGRATION.MIGRATION_SCHEMA,
        "source": "frozen_canonical_replay",
        "timing": "RETROSPECTIVE",
        "base_sha": BASE_SHA,
        "signal_date": SIGNAL_DATE,
        "report_date": REPORT_DATE,
        "exec_date": REPORT_DATE,
        "exit_date": EXIT_DATE,
        "live_delivery_met": False,
        "execution_created": False,
        "fill_created": False,
        "broker_execution_claimed": False,
        "observation_truth_is_not_a_fill_claim": True,
    }


def test_action_rebuild_stability_excludes_only_reviewed_dynamic_truth() -> None:
    replayed = MIGRATION.annotate_retrospective_action(
        {
            **_action_payload(),
            "generated_at_utc": "2026-08-21T01:00:00+00:00",
            "observation_statistics": {"rows": 1},
            "observation_validation": {"status": "pending"},
            "model": {
                "selection_policy": {"threshold": 0.123456789012},
                "truth_ledgers": {"market_open_observation": {"rows": 1}},
            },
        },
        _binding(),
        BASE_SHA,
    )
    final = copy.deepcopy(replayed)
    final["generated_at_utc"] = "2026-08-21T01:01:00+00:00"
    final["observation_statistics"] = {"rows": 2}
    final["model"]["truth_ledgers"] = {
        "market_open_observation": {"rows": 2}
    }
    assert MIGRATION.build_action_rebuild_stability(replayed, final)["matched"] is True

    numeric_drift = copy.deepcopy(final)
    numeric_drift["model"]["selection_policy"]["threshold"] += 1e-12
    with pytest.raises(MIGRATION.MigrationError, match="raw action semantics"):
        MIGRATION.build_action_rebuild_stability(replayed, numeric_drift)

    row_truth_drift = copy.deepcopy(final)
    row_truth_drift["stage_watchlist"][0]["truth_generated_at_utc"] = (
        "2026-08-21T01:01:00+00:00"
    )
    assert (
        MIGRATION.build_action_rebuild_stability(replayed, row_truth_drift)["matched"]
        is True
    )

    validation_drift = copy.deepcopy(final)
    validation_drift["observation_validation"] = {"status": "final"}
    assert (
        MIGRATION.build_action_rebuild_stability(replayed, validation_drift)["matched"]
        is True
    )

    derived_label_drift = copy.deepcopy(final)
    derived_label_drift["stage_watchlist"][0]["validation_status_label"] = "final"
    assert (
        MIGRATION.build_action_rebuild_stability(replayed, derived_label_drift)["matched"]
        is True
    )

    candidate_truth_drift = copy.deepcopy(final)
    candidate_truth_drift["candidates"][0]["truth_generated_at_utc"] = "tampered"
    with pytest.raises(MIGRATION.MigrationError, match="raw action semantics"):
        MIGRATION.build_action_rebuild_stability(replayed, candidate_truth_drift)

    unknown_stage_drift = copy.deepcopy(final)
    unknown_stage_drift["stage_watchlist"][0]["unknown_field"] = "tampered"
    with pytest.raises(MIGRATION.MigrationError, match="raw action semantics"):
        MIGRATION.build_action_rebuild_stability(replayed, unknown_stage_drift)

    watch_label_drift = copy.deepcopy(final)
    watch_label_drift["stage_watchlist"][0]["watch_label"] = "tampered"
    with pytest.raises(MIGRATION.MigrationError, match="raw action semantics"):
        MIGRATION.build_action_rebuild_stability(replayed, watch_label_drift)

    reordered = copy.deepcopy(final)
    reordered["candidates"].append(
        {"action": "WATCH", "target_weight": 0.0, "ts_code": "000002.SZ"}
    )
    reordered["candidates"].reverse()
    with pytest.raises(MIGRATION.MigrationError, match="raw action semantics"):
        MIGRATION.build_action_rebuild_stability(replayed, reordered)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("formal_buy_count",), 1),
        (("status_code",), "ACTIONABLE_BUY"),
        (("guidance_only",), False),
        (("broker_connected",), True),
        (("order_execution",), "automatic"),
        (("candidates", 0, "action"), "BUY"),
        (("stage_watchlist", 0, "target_weight"), 0.5),
    ],
)
def test_action_annotation_rejects_trade_or_fill_semantics(
    path: tuple[object, ...], value: object
) -> None:
    action = _action_payload()
    cursor: object = action
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.annotate_retrospective_action(action, _binding(), BASE_SHA)


def test_verify_envelope_accepts_exact_retrospective_bundle(tmp_path: Path) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    assert MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA) == receipt


@pytest.mark.parametrize("kind", ["extra", "missing"])
def test_verify_envelope_rejects_nonexact_receipt_v2_keys(
    tmp_path: Path, kind: str
) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    if kind == "extra":
        receipt["unreviewed_claim"] = True
    else:
        del receipt["validator_summary"]
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError, match="V2 keys are not exact"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timing", "PROSPECTIVE_LIVE"),
        ("live_delivery_met", True),
        ("execution_or_fill_claimed", True),
        ("pins_enforced", False),
        ("forced_inactive", False),
        ("post_prune_validators_passed", False),
        ("replay_numeric_runtime", None),
        ("action_rebuild_stability", None),
        ("schema_version", "decision_runtime_migration_receipt_v1"),
    ],
)
def test_verify_envelope_rejects_false_receipt_claims(
    tmp_path: Path, field: str, value: object
) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    receipt[field] = value
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_verify_envelope_rejects_extra_historical_action(tmp_path: Path) -> None:
    bundle, _receipt = _write_bundle(tmp_path)
    extra = bundle / "files" / "outputs" / "decision" / "action_plan_20260817.json"
    extra.write_text("{}\n", encoding="utf-8")
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_verify_envelope_rejects_corrupt_bytes_and_digest(tmp_path: Path) -> None:
    bundle, _receipt = _write_bundle(tmp_path)
    target = bundle / "files" / "outputs" / "decision" / "action_plan_latest.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(MIGRATION.MigrationError, match="size mismatch"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)
    bundle, _receipt = _write_bundle(tmp_path / "second")
    (bundle / "migration-receipt.sha256").write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(MIGRATION.MigrationError, match="digest mismatch"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_verify_envelope_rejects_path_traversal_and_symlink(tmp_path: Path) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    receipt["changed_paths"] = ["../escape"]
    receipt["output_sha256"] = {"../escape": "4" * 64}
    receipt["output_size"] = {"../escape": 1}
    receipt["base_blob_sha1"] = {"../escape": None}
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)

    bundle, _receipt = _write_bundle(tmp_path / "second")
    target = bundle / "files" / "outputs" / "decision" / "action_plan_latest.json"
    target.unlink()
    target.symlink_to(bundle / "migration-receipt.json")
    with pytest.raises(MIGRATION.MigrationError, match="symlink"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_verify_envelope_rejects_action_payload_lie(tmp_path: Path) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    latest_relative = "outputs/decision/action_plan_latest.json"
    latest = bundle / "files" / latest_relative
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["migration"]["fill_created"] = True
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    latest.write_bytes(data)
    receipt["output_sha256"][latest_relative] = hashlib.sha256(data).hexdigest()
    receipt["output_size"][latest_relative] = len(data)
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def test_verify_envelope_rejects_final_action_stability_drift(tmp_path: Path) -> None:
    bundle, receipt = _write_bundle(tmp_path)
    latest_relative = "outputs/decision/action_plan_latest.json"
    latest = bundle / "files" / latest_relative
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["candidates"][0]["name"] = "tampered identity"
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    latest.write_bytes(data)
    receipt["output_sha256"][latest_relative] = hashlib.sha256(data).hexdigest()
    receipt["output_size"][latest_relative] = len(data)
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError, match="stability evidence"):
        MIGRATION.verify_envelope(bundle, expected_base_sha=BASE_SHA)


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_restore_prunes_historical_action_and_all_unrelated_side_effects(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    historical = repo / "outputs" / "decision" / "action_plan_20260817.json"
    latest = repo / "outputs" / "decision" / "action_plan_latest.json"
    unrelated = repo / "outputs" / "decision" / "verification_latest.json"
    for path, body in (
        (historical, "historical-base\n"),
        (latest, "latest-base\n"),
        (unrelated, "verification-base\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.name", "migration-test")
    _run_git(repo, "config", "user.email", "migration-test@example.invalid")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-qm", "base")
    base = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "checkout", "--detach", "-q", base)

    historical.write_text("wrong historical rewrite\n", encoding="utf-8")
    latest.write_text("allowed canonical runtime\n", encoding="utf-8")
    unrelated.write_text("wrong unrelated rewrite\n", encoding="utf-8")
    untracked = repo / "outputs" / "decision" / "settlement_created.json"
    untracked.write_text("wrong new settlement\n", encoding="utf-8")
    allowed_new = repo / "outputs" / "decision" / f"action_plan_{REPORT_DATE}.json"
    allowed_new.write_text("allowed dated action\n", encoding="utf-8")
    allowed = {
        "outputs/decision/action_plan_latest.json",
        f"outputs/decision/action_plan_{REPORT_DATE}.json",
    }
    restored = MIGRATION.restore_outside_allowlist(repo, base, allowed)
    assert set(restored) == {
        "outputs/decision/action_plan_20260817.json",
        "outputs/decision/settlement_created.json",
        "outputs/decision/verification_latest.json",
    }
    assert historical.read_text(encoding="utf-8") == "historical-base\n"
    assert unrelated.read_text(encoding="utf-8") == "verification-base\n"
    assert not untracked.exists()
    assert latest.read_text(encoding="utf-8") == "allowed canonical runtime\n"
    assert allowed_new.read_text(encoding="utf-8") == "allowed dated action\n"
    assert MIGRATION._changed_paths(repo, base) == allowed


def test_exact_base_precondition_rejects_clean_sparse_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "sparse-repo"
    for relative in ("keep/current.txt", "omitted/prior-session-raw.txt"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.name", "migration-test")
    _run_git(repo, "config", "user.email", "migration-test@example.invalid")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-qm", "base")
    base = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "checkout", "--detach", "-q", base)
    _run_git(repo, "sparse-checkout", "init", "--cone")
    _run_git(repo, "sparse-checkout", "set", "keep")
    assert _run_git(repo, "status", "--porcelain=v1") == ""
    assert not (repo / "omitted" / "prior-session-raw.txt").exists()
    with pytest.raises(MIGRATION.MigrationError, match="sparse checkout"):
        MIGRATION.require_exact_base(repo, base)


def test_rebound_embedded_truth_equals_restored_json_and_observation_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "truth-root"
    truth_metrics: dict[str, dict[str, object]] = {
        "formal_limit_proxy": {"status": "base-formal", "rows": 7},
        "market_open_observation": {
            "status": "base-observation",
            "observation_rows": 1,
        },
        "manual_actual": {"status": "base-manual", "rows": 0},
    }
    stale_ledgers: dict[str, dict[str, object]] = {}
    for name, (ledger_path, metrics_path) in MIGRATION.TRUTH_LEDGER_BINDINGS.items():
        ledger = root / ledger_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("marker\nbase\n", encoding="utf-8")
        metrics = root / metrics_path
        metrics.parent.mkdir(parents=True, exist_ok=True)
        metrics.write_text(
            json.dumps(truth_metrics[name], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        stale_ledgers[name] = {"path": ledger_path, "metrics": {"status": "stale"}}

    dated_observation = (
        root
        / "outputs"
        / "auction_v3"
        / "verification"
        / f"observation_{REPORT_DATE}.csv"
    )
    dated_observation.write_text(
        "ts_code,actual_open_price,validation_status,truth_generated_at_utc\n"
        "000001.SZ,10.5,FINAL_VERIFIED,2026-08-18T08:00:00+00:00\n",
        encoding="utf-8",
    )
    meta_path = root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"truth_ledgers": stale_ledgers}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rebound = MIGRATION.rebind_model_truth_ledgers(root)
    for name, (ledger_path, _metrics_path) in MIGRATION.TRUTH_LEDGER_BINDINGS.items():
        assert rebound["truth_ledgers"][name] == {
            "path": ledger_path,
            "metrics": truth_metrics[name],
        }

    from top10decision.decision.action_plan import _attach_observation_validation

    action = _attach_observation_validation(root, {
        "model": {"truth_ledgers": rebound["truth_ledgers"]},
        "exec_date": REPORT_DATE,
        "candidates": [
            {
                "ts_code": "000001.SZ",
                "stage_transition": "2→3",
                "action": "WATCH",
                "target_weight": 0.0,
            }
        ],
    })
    action_path = root / "outputs" / "decision" / "action_plan_latest.json"
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    audit = MIGRATION.validate_embedded_truth_bindings(root, binding=_binding())
    assert audit["model_truth_metrics_exact"] is True
    assert audit["action_observation_statistics_exact"] is True
    assert audit["action_watchlist_truth_exact"] is True
    assert audit["matched_observation_rows"] == 1

    action["stage_watchlist"][0]["actual_open_price"] = 99.0
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(MIGRATION.MigrationError, match="exact observation reconstruction"):
        MIGRATION.validate_embedded_truth_bindings(root, binding=_binding())

    action = _attach_observation_validation(root, action)
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    dated_observation.write_text(
        "ts_code,actual_open_price,validation_status,truth_generated_at_utc\n"
        "999999.SZ,10.5,FINAL_VERIFIED,2026-08-18T08:00:00+00:00\n",
        encoding="utf-8",
    )
    action = _attach_observation_validation(root, action)
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(MIGRATION.MigrationError, match="lacks watchlist rows"):
        MIGRATION.validate_embedded_truth_bindings(root, binding=_binding())

    dated_observation.write_text(
        "ts_code,actual_open_price,validation_status,truth_generated_at_utc\n"
        "000001.SZ,10.5,FINAL_VERIFIED,2026-08-18T08:00:00+00:00\n",
        encoding="utf-8",
    )
    action = _attach_observation_validation(root, action)
    action["stage_watchlist"][0]["validation_status_label"] = "tampered"
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(MIGRATION.MigrationError, match="exact observation reconstruction"):
        MIGRATION.validate_embedded_truth_bindings(root, binding=_binding())

    action = _attach_observation_validation(root, action)
    action["observation_validation"]["final_rows"] = 0
    action_path.write_text(
        json.dumps(action, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(MIGRATION.MigrationError, match="exact observation reconstruction"):
        MIGRATION.validate_embedded_truth_bindings(root, binding=_binding())


def test_post_prune_sequence_rebuilds_only_current_action_before_second_validation() -> None:
    build_source = inspect.getsource(MIGRATION.build_migration)
    assert build_source.index("restore_outside_allowlist") < build_source.index(
        "rebind_model_truth_ledgers"
    )
    assert build_source.index("rebind_model_truth_ledgers") < build_source.index(
        "rebuild_current_action_after_prune"
    )
    assert build_source.index("rebuild_current_action_after_prune") < build_source.index(
        "truth_bindings = validate_embedded_truth_bindings"
    )
    assert build_source.index("truth_bindings = validate_embedded_truth_bindings") < (
        build_source.rindex("run_full_validators")
    )
    rebuild_source = inspect.getsource(MIGRATION.rebuild_current_action_after_prune)
    assert "build_action_plan" in rebuild_source
    assert "build_report_index" in rebuild_source
    assert "refresh_action_plan_observations" not in rebuild_source


def test_verify_with_exact_base_reconstructs_complete_source_path_set(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    evaluation_path = f"outputs/decision/eval_{REPORT_DATE}.json"
    report_path = f"outputs/decision/decision_report_{REPORT_DATE}.md"
    candidates_path = f"data/decision/decision_candidates_{SIGNAL_DATE}.csv"
    execution_path = f"data/decision/decision_execution_{REPORT_DATE}.csv"
    required_paths = set(MIGRATION.required_receipt_source_paths(SIGNAL_DATE))
    required_paths.update(
        {
            "data/market/trade_cal_sse.csv",
            "outputs/decision/report_index.json",
            evaluation_path,
            report_path,
            candidates_path,
            execution_path,
        }
    )
    required_paths.update(
        relative
        for pair in MIGRATION.TRUTH_LEDGER_BINDINGS.values()
        for relative in pair
    )
    pinned_path = "pinned/custom_runtime_dependency.txt"
    required_paths.add(pinned_path)
    for relative in sorted(required_paths):
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"exact base source: {relative}\n", encoding="utf-8")
    exact_truth_metrics: dict[str, dict[str, object]] = {}
    for name, (_ledger_path, metrics_path) in MIGRATION.TRUTH_LEDGER_BINDINGS.items():
        exact_truth_metrics[name] = {"status": f"exact-{name}", "rows": 0}
        (source_root / metrics_path).write_text(
            json.dumps(exact_truth_metrics[name]) + "\n",
            encoding="utf-8",
        )
    exact_truth_ledgers = {
        name: {"path": ledger_path, "metrics": exact_truth_metrics[name]}
        for name, (ledger_path, _metrics_path) in MIGRATION.TRUTH_LEDGER_BINDINGS.items()
    }
    exact_model_meta = (
        source_root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
    )
    exact_model_meta.parent.mkdir(parents=True, exist_ok=True)
    exact_model_meta.write_text(
        json.dumps({"truth_ledgers": exact_truth_ledgers}) + "\n",
        encoding="utf-8",
    )
    observation_path = (
        source_root
        / "outputs"
        / "auction_v3"
        / "verification"
        / "observation_latest.csv"
    )
    observation_path.write_text(
        "ts_code,expected_buy_date,actual_open_price,validation_status,"
        "truth_generated_at_utc,prediction_timing_status,prediction_timing_valid\n"
        f"000001.SZ,{REPORT_DATE},10.5,FINAL_VERIFIED,"
        "2026-08-24T08:00:00+00:00,PREMARKET_VALID,1\n",
        encoding="utf-8",
    )

    index = {
        "schema_version": "decision_report_index_v2_action_truth",
        "generated_at_utc": "2026-08-24T08:00:00+00:00",
        "latest_report_date": REPORT_DATE,
        "latest_report_file": f"decision_report_{REPORT_DATE}.md",
        "latest_action_report_date": REPORT_DATE,
        "latest_action_url": f"outputs/decision/action_plan_{REPORT_DATE}.json",
        "reports": [
            {
                "report_date": REPORT_DATE,
                "report_file": f"decision_report_{REPORT_DATE}.md",
                "report_url": report_path,
                "eval_url": evaluation_path,
                "action_available": True,
                "action_url": f"outputs/decision/action_plan_{REPORT_DATE}.json",
            }
        ],
    }
    (source_root / "outputs" / "decision" / "report_index.json").write_text(
        json.dumps(index) + "\n", encoding="utf-8"
    )
    evaluation = {
        "signal_date": SIGNAL_DATE,
        "exec_date": REPORT_DATE,
        "exit_date": EXIT_DATE,
        "paths": {
            "decision_report": report_path,
            "candidates": candidates_path,
            "execution": execution_path,
        },
    }
    (source_root / evaluation_path).write_text(
        json.dumps(evaluation) + "\n", encoding="utf-8"
    )
    source_action_path = (
        source_root / "outputs" / "decision" / f"action_plan_{REPORT_DATE}.json"
    )
    source_action_path.write_text(
        json.dumps({"report_date": REPORT_DATE}) + "\n",
        encoding="utf-8",
    )
    pinned_sha = hashlib.sha256((source_root / pinned_path).read_bytes()).hexdigest()
    manifest = {
        "schema_version": "decision_model_freeze_v2",
        "active": False,
        "pinned_files": {pinned_path: pinned_sha},
    }
    (source_root / "models" / "decision_model_freeze.json").write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    _run_git(source_root, "init", "-q")
    _run_git(source_root, "config", "user.name", "migration-test")
    _run_git(source_root, "config", "user.email", "migration-test@example.invalid")
    _run_git(source_root, "add", ".")
    _run_git(source_root, "commit", "-qm", "exact base")
    base_sha = _run_git(source_root, "rev-parse", "HEAD")
    base_tree = _run_git(source_root, "rev-parse", "HEAD^{tree}")
    binding = MIGRATION.discover_binding(source_root, SIGNAL_DATE)
    git_base = MIGRATION.GitBase(sha=base_sha, tree_sha=base_tree)
    source_evidence = MIGRATION._source_evidence(
        source_root, git_base, binding, manifest
    )
    truth_evidence = MIGRATION.assert_truth_references_are_exact_base(
        source_root, base_sha=base_sha, binding=binding
    )

    bundle, receipt = _write_bundle(tmp_path / "artifact")
    receipt["base_sha"] = base_sha
    receipt["base_tree_sha"] = base_tree
    receipt["source_evidence"] = source_evidence
    receipt["truth_reference_evidence"] = truth_evidence
    receipt["replay_numeric_runtime"]["launcher_sha256"] = source_evidence[
        MIGRATION.NUMERIC_LAUNCHER_PATH
    ]["sha256"]
    receipt["replay_numeric_runtime"]["target_sha256"] = source_evidence[
        MIGRATION.NUMERIC_REPLAY_TARGET_PATH
    ]["sha256"]
    from top10decision.decision.action_plan import _attach_observation_validation

    for relative in receipt["changed_paths"]:
        action_path = bundle / "files" / relative
        action = json.loads(action_path.read_text(encoding="utf-8"))
        action["migration"]["base_sha"] = base_sha
        action["model"] = {"truth_ledgers": exact_truth_ledgers}
        action = _attach_observation_validation(source_root, action)
        data = (json.dumps(action, ensure_ascii=False, indent=2) + "\n").encode()
        action_path.write_bytes(data)
        receipt["output_sha256"][relative] = hashlib.sha256(data).hexdigest()
        receipt["output_size"][relative] = len(data)
        base_entry = MIGRATION._git_tree_entry(source_root, base_sha, relative)
        receipt["base_blob_sha1"][relative] = (
            base_entry["sha"] if base_entry is not None else None
        )
    receipt["action_rebuild_stability"] = MIGRATION.build_action_rebuild_stability(
        action, action
    )
    receipt["truth_binding_summary"] = {
        "model_truth_metrics_exact": True,
        "action_truth_ledgers_exact": True,
        "action_observation_statistics_exact": True,
        "action_watchlist_truth_exact": True,
        "watchlist_rows": 1,
        "matched_observation_rows": 1,
    }
    _rewrite_receipt(bundle, receipt)
    assert (
        MIGRATION.verify_envelope(
            bundle,
            expected_base_sha=base_sha,
            exact_base_root=source_root,
        )["source_evidence"]
        == source_evidence
    )

    for runtime_field, source_path in (
        ("launcher_sha256", MIGRATION.NUMERIC_LAUNCHER_PATH),
        ("target_sha256", MIGRATION.NUMERIC_REPLAY_TARGET_PATH),
    ):
        original_runtime_sha = receipt["replay_numeric_runtime"][runtime_field]
        original_source_sha = receipt["source_evidence"][source_path]["sha256"]
        fake_sha = "f" * 64
        receipt["replay_numeric_runtime"][runtime_field] = fake_sha
        receipt["source_evidence"][source_path]["sha256"] = fake_sha
        _rewrite_receipt(bundle, receipt)
        with pytest.raises(MIGRATION.MigrationError, match="exact reconstructed set"):
            MIGRATION.verify_envelope(
                bundle,
                expected_base_sha=base_sha,
                exact_base_root=source_root,
            )
        receipt["replay_numeric_runtime"][runtime_field] = original_runtime_sha
        receipt["source_evidence"][source_path]["sha256"] = original_source_sha

    model_meta_relative = "outputs/auction_v3/models/model_meta_latest.json"
    tampered_model_meta = bundle / "files" / model_meta_relative
    tampered_model_meta.parent.mkdir(parents=True, exist_ok=True)
    tampered_bytes = (
        json.dumps({"truth_ledgers": {"tampered": True}}, indent=2) + "\n"
    ).encode()
    tampered_model_meta.write_bytes(tampered_bytes)
    receipt["changed_paths"] = sorted(
        [*receipt["changed_paths"], model_meta_relative]
    )
    receipt["output_sha256"][model_meta_relative] = hashlib.sha256(
        tampered_bytes
    ).hexdigest()
    receipt["output_size"][model_meta_relative] = len(tampered_bytes)
    model_meta_entry = MIGRATION._git_tree_entry(
        source_root, base_sha, model_meta_relative
    )
    assert model_meta_entry is not None
    receipt["base_blob_sha1"][model_meta_relative] = model_meta_entry["sha"]
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError, match="model truth ledgers"):
        MIGRATION.verify_envelope(
            bundle,
            expected_base_sha=base_sha,
            exact_base_root=source_root,
        )
    tampered_model_meta.unlink()
    receipt["changed_paths"].remove(model_meta_relative)
    del receipt["output_sha256"][model_meta_relative]
    del receipt["output_size"][model_meta_relative]
    del receipt["base_blob_sha1"][model_meta_relative]

    omitted = pinned_path
    del receipt["source_evidence"][omitted]
    receipt["truth_reference_evidence"].pop(omitted, None)
    _rewrite_receipt(bundle, receipt)
    with pytest.raises(MIGRATION.MigrationError):
        MIGRATION.verify_envelope(
            bundle,
            expected_base_sha=base_sha,
            exact_base_root=source_root,
        )


def test_workflow_is_dispatch_only_and_dry_run_has_no_delivery_path() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    header = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in header
    assert "schedule:" not in header
    assert "push:" not in header
    assert "pull_request:" not in header
    assert "dry_run:" in header and "default: true" in header
    assert "group: decision-auction-main-writer" in text
    assert "cancel-in-progress: false" in text
    assert "persist-credentials: false" in text
    assert "Full working-tree materialization is a migration precondition" in text
    assert "sparse-checkout:" not in text
    assert (
        "if: ${{ steps.mode.outputs.publish == 'true' && steps.candidate.outputs.has_changes == 'true' }}"
        in text
    )
    assert (
        "if: ${{ needs.compute.outputs.publish == 'true' && needs.compute.outputs.has_changes == 'true' }}"
        in text
    )
    assert text.index("actions/upload-artifact@") < text.index("\n  publish:")
    assert text.index("Require remote main unchanged at compute completion") < text.index(
        "actions/upload-artifact@"
    )
    assert "outputs/**" not in text
    assert "git push" not in text


def test_workflow_uses_pinned_actions_and_remote_api_cas() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    for pin in (
        "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea",
    ):
        assert pin in text
    assert text.count("github.rest.git.getRef") >= 2
    for method in (
        "getCommit",
        "getTree",
        "getBlob",
        "createBlob",
        "createTree",
        "createCommit",
        "updateRef",
    ):
        assert f"github.rest.git.{method}" in text
    assert "base_tree: receipt.base_tree_sha" in text
    assert "parents: [baseSha]" in text
    assert "force: false" in text
    assert "if (baseTreeResponse.data.truncated)" in text
    assert "if (finalTreeResponse.data.truncated)" in text
    assert "receipt.source_evidence" in text
    assert text.count("receipt.mode !== 'publish_candidate'") == 1
    assert "entry.mode !== '100644'" in text
    assert "--root \"${GITHUB_WORKSPACE}\"" in text
    assert "isChangedAncestor" in text
    assert "RETROSPECTIVE" in text
    assert "execution_or_fill_claimed" in text
    assert "decision_runtime_migration_receipt_v2" in text
    assert "receipt.replay_numeric_runtime" in text


def test_publish_job_installs_locked_runtime_before_independent_reverification() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    publish = text[text.index("\n  publish:") :]
    setup = (
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
    )
    install = (
        "python -m pip install --disable-pip-version-check --only-binary=:all: "
        "--require-hashes -r requirements.lock"
    )
    verify = "python scripts/migrate_decision_runtime.py verify"

    assert publish.count(setup) == 1
    assert 'python-version: "3.12.13"' in publish
    assert publish.count(install) == 1
    assert "python -m pip check" in publish
    assert publish.index(setup) < publish.index(install) < publish.index(verify)
    assert "dc20_deterministic_numeric_runtime_v1" in text
    assert "receipt.action_rebuild_stability" in text
    assert "decision_action_rebuild_stability_v2" in text
    assert "raw_action_excluding_generation_and_exact_base_observation_truth" in text
    assert "runtime.launcher_sha256 !== launcherEvidence.sha256" in text
    assert "runtime.target_sha256 !== replayEvidence.sha256" in text
    assert text.index("github.rest.git.getBlob") < text.index(
        "github.rest.git.createBlob"
    )
    assert text.index("exactKeys(receipt.output_sha256") < text.index(
        "github.rest.git.createBlob"
    )
    assert text.index("prepared.push({relative, bytes, localBlob})") < text.index(
        "github.rest.git.createBlob"
    )
    assert text.count("github.rest.git.createBlob") == 1


def test_workflow_invokes_frozen_replay_wrapper_from_detached_worktree() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "git worktree add --detach" in workflow
    assert "--base-sha" in workflow
    assert "--candidate-root" in workflow
    assert "replay_frozen_canonical_v2.py" in script
    assert "run_deterministic_numeric.py" in script
    assert '"scripts/run_deterministic_numeric.py"' in script
    assert (
        'str(root / "scripts" / "run_deterministic_numeric.py")'
        in script
    )
    assert "run_full_validators(root, manifest)" in script
    assert script.count("run_full_validators(root, manifest)") == 2
    assert "restore_outside_allowlist" in script
    assert "_assert_historical_actions_unchanged" in script
    assert "required_receipt_source_paths" in script
    assert "data/pred/_pred_source_meta.json" in script
    assert "_sync_meta.json" in script
    assert "validate_action_plan_artifact" in script
    assert "validate_report_index_action_truth" in script
    assert "validate_io_contract.py" not in script
    assert "strict_io_semantic" not in script
