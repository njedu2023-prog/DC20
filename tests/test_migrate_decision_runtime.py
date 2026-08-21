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
        "candidates": [{"action": "WATCH", "target_weight": 0.0}],
        "stage_watchlist": [{"action": "WATCH", "target_weight": 0.0}],
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
        "validator_summary": {},
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timing", "PROSPECTIVE_LIVE"),
        ("live_delivery_met", True),
        ("execution_or_fill_claimed", True),
        ("pins_enforced", False),
        ("forced_inactive", False),
        ("post_prune_validators_passed", False),
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

    action = {
        "model": {"truth_ledgers": rebound["truth_ledgers"]},
        "observation_statistics": truth_metrics["market_open_observation"],
        "stage_watchlist": [
            {
                "ts_code": "000001.SZ",
                "actual_open_price": 10.5,
                "validation_status": "FINAL_VERIFIED",
                "truth_generated_at_utc": "2026-08-18T08:00:00+00:00",
            }
        ],
    }
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
    with pytest.raises(MIGRATION.MigrationError, match="observation bytes"):
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

    index = {
        "schema_version": "decision_report_index_v2_action_truth",
        "latest_report_date": REPORT_DATE,
        "reports": [
            {
                "report_date": REPORT_DATE,
                "report_file": f"decision_report_{REPORT_DATE}.md",
                "report_url": report_path,
                "eval_url": evaluation_path,
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
    for relative in receipt["changed_paths"]:
        action_path = bundle / "files" / relative
        action = json.loads(action_path.read_text(encoding="utf-8"))
        action["migration"]["base_sha"] = base_sha
        data = (json.dumps(action, ensure_ascii=False, indent=2) + "\n").encode()
        action_path.write_bytes(data)
        receipt["output_sha256"][relative] = hashlib.sha256(data).hexdigest()
        receipt["output_size"][relative] = len(data)
        receipt["base_blob_sha1"][relative] = None
    _rewrite_receipt(bundle, receipt)
    assert (
        MIGRATION.verify_envelope(
            bundle,
            expected_base_sha=base_sha,
            exact_base_root=source_root,
        )["source_evidence"]
        == source_evidence
    )

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
    for method in ("getCommit", "getTree", "createBlob", "createTree", "createCommit", "updateRef"):
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


def test_workflow_invokes_frozen_replay_wrapper_from_detached_worktree() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "git worktree add --detach" in workflow
    assert "--base-sha" in workflow
    assert "--candidate-root" in workflow
    assert "replay_frozen_canonical_v2.py" in script
    assert "run_full_validators(root, manifest)" in script
    assert script.count("run_full_validators(root, manifest)") == 2
    assert "restore_outside_allowlist" in script
    assert "_assert_historical_actions_unchanged" in script
    assert "required_receipt_source_paths" in script
    assert "data/pred/_pred_source_meta.json" in script
    assert "_sync_meta.json" in script
    assert "validate_action_plan_artifact" in script
    assert "validate_report_index_action_truth" in script
    assert "--strict-semantic" in script
