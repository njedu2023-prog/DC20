from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "train_decision_three_engines.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str | None = None) -> str:
    value = text.split(start, 1)[1]
    return value if end is None else value.split(end, 1)[0]


def test_retraining_is_default_read_only_and_sunday_is_explicitly_guarded() -> None:
    text = _text()
    header = text.split("\npermissions:", 1)[0]
    compute = _section(text, "  compute:", "\n  publish:")
    mode = _section(
        text,
        "- name: Resolve dry-run mode and immutable base",
        "- name: Snapshot the four existing writer workflow states",
    )

    assert 'cron: "15 3 * * 0"' in header
    assert re.search(
        r"dry_run:\s*\n\s+description:.*\n\s+required: true\s*\n"
        r"\s+default: true\s*\n\s+type: boolean",
        header,
    )
    assert "github.event_name == 'schedule' && vars.DC20_THREE_ENGINE_RETRAIN_ENABLED == 'true'" in compute
    assert "INPUT_DRY_RUN: ${{ inputs.dry_run }}" in mode
    assert "RETRAIN_ENABLED: ${{ vars.DC20_THREE_ENGINE_RETRAIN_ENABLED }}" in mode
    assert 'requested_publish=false' in mode
    assert 'test "${RETRAIN_ENABLED:-false}" = "true"' in mode
    assert "Real three-engine publication requires vars.DC20_THREE_ENGINE_RETRAIN_ENABLED=true" in mode


def test_retraining_shares_the_non_cancelling_main_writer_lock() -> None:
    text = _text()
    assert "group: decision-auction-main-writer" in text
    assert "cancel-in-progress: false" in text


def test_compute_uses_immutable_main_without_credentials_and_hash_locked_deps() -> None:
    text = _text()
    compute = _section(text, "  compute:", "\n  publish:")
    assert "Checkout immutable main base without writer credentials" in compute
    assert "ref: main" in compute
    assert "fetch-depth: 1" in compute
    assert "persist-credentials: false" in compute
    assert "git rev-parse HEAD" in compute
    assert "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09" in compute
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in compute
    assert "--only-binary=:all: --require-hashes -r requirements-dev.lock" in compute
    assert "python -m pip check" in compute


def test_compute_builds_validates_and_trains_real_five_year_models_in_order() -> None:
    text = _text()
    freeze = text.index(
        "- name: Validate the active freeze before overwriting any model asset"
    )
    cache = text.index(
        "- name: Restore best-effort Tencent market-data download cache"
    )
    build = text.index("python scripts/build_three_engine_five_year_ledger.py")
    validate = text.index("python scripts/validate_three_engine_five_year_ledger.py")
    train = text.index(
        "python scripts/run_deterministic_numeric.py scripts/train_three_engine_models.py"
    )
    gate = text.index("- name: Resolve fail-closed model release eligibility")
    assert freeze < cache < build < validate < train < gate
    freeze_step = _section(
        text,
        "- name: Validate the active freeze before overwriting any model asset",
        "- name: Restore best-effort Tencent market-data download cache",
    )
    assert "python scripts/validate_decision_model_freeze.py" in freeze_step
    assert "--minimum-price-coverage 0.98" in text
    assert 'int(source.get("rows") or 0) < 10_000' in text
    assert 'int(source.get("dates") or 0) < 1_100' in text
    assert 'data_validation.get("valid") is not True' in text
    assert 'independence.get("owner") != "njedu2023-prog/DC20"' in text
    assert 'independence.get("runtime_dependency_on_top10_decision") is not False' in text


def test_clean_runner_cache_is_fixed_sha_best_effort_and_never_release_truth() -> None:
    text = _text()
    cache = _section(
        text,
        "- name: Restore best-effort Tencent market-data download cache",
        "- name: Build DC20-owned five-year point-in-time ledger",
    )
    build = _section(
        text,
        "- name: Build DC20-owned five-year point-in-time ledger",
        "- name: Validate five-year ledger and independent ownership",
    )
    assert "actions/cache@5a3ec84eff668545956fd18022155c47e93e2684" in cache
    assert "${{ runner.temp }}/dc20-three-engine-tencent-cache" in cache
    seed = "57683c2ee65de2b9debd6c7ca253c5ef18e393c121b943884bc577054cd7fe3e"
    assert seed in cache
    assert "${{ github.run_id }}" in cache
    assert "restore-keys:" in cache
    assert '--cache-root "${RUNNER_TEMP}/dc20-three-engine-tencent-cache"' in build
    # Cache content is never named in release authorization.  Rebuilt ledger,
    # manifest, and validation hashes remain the only truth gates.
    release = _section(
        text,
        "- name: Resolve fail-closed model release eligibility",
        "- name: Atomically re-freeze",
    )
    assert "dc20-three-engine-tencent-cache" not in release
    assert 'manifest.get("ledger_sha256") != sha256(ledger_path)' in release
    assert 'source.get("ledger_sha256") != sha256(ledger_path)' in release


def test_release_gate_distinguishes_all_core_partial_and_ineligible_states() -> None:
    text = _text()
    release = _section(
        text,
        "- name: Resolve fail-closed model release eligibility",
        "- name: Build exact allowlisted immutable candidate",
    )
    publish_job = _section(text, "\n  publish:", "\n  verify-writer-states:")
    assert 'heads[head].get("status") == "READY"' in release
    assert 'validation.get("status") == "READY"' in release
    assert 'heads[head].get("promoted") is True' in release
    assert 'heads[head].get("gate_failures") == []' in release
    assert '"ALL_CORE_READY"' in release
    assert '"PROMOTION_READY_PARTIAL"' in release
    assert 'else "NONE"' in release
    assert 'promotion.get("status") == "READY"' in release
    assert 'item["status"].startswith("NOT_READY_")' in release
    assert 'item.get("promoted") is False' in release
    assert 'p_fill.get("cannot_change_core_members_or_ranks") is True' in release
    assert 'release_contract.get("failed_or_constant_head_must_not_emit_official_rank") is True' in release
    assert "publish = promotion_release_eligible and requested" in release
    assert "promotion_release_eligible" in release
    assert "vars.DC20_THREE_ENGINE_RETRAIN_ENABLED == 'true'" in publish_job
    assert "needs.compute.outputs.publish == 'true'" in publish_job
    assert "needs.compute.outputs.promotion_release_eligible == 'true'" in publish_job
    assert "needs.compute.outputs.has_changes == 'true'" in publish_job
    assert "candidate lacks a consistent promotion-safe authorization" in publish_job
    assert "published validation differs from authorized release mode" in publish_job
    assert "published model artifact hash mismatch" in publish_job


def test_both_authorized_release_modes_atomically_refreeze_production() -> None:
    text = _text()
    release_end = text.index("- name: Atomically re-freeze one promotion-safe release candidate")
    candidate_start = text.index("- name: Build exact allowlisted immutable candidate")
    step = text[release_end:candidate_start]
    assert "if: ${{ steps.release.outputs.promotion_release_eligible == 'true' }}" in step
    assert "python scripts/refreeze_decision_three_rank.py" in step
    assert '--expected-release-mode "${RELEASE_MODE}"' in step
    assert "--check" in step
    assert "models/decision_model_freeze.json" in text
    publish = _section(text, "\n  publish:", "\n  verify-writer-states:")
    assert "scripts/refreeze_decision_three_rank.py" in publish
    assert '--expected-release-mode "${release_mode}"' in publish
    assert "scripts/validate_decision_model_freeze.py" in publish


def test_candidate_allowlist_is_exact_and_checked_twice_with_regular_modes() -> None:
    text = _text()
    compute = _section(text, "  compute:", "\n  publish:")
    publish = _section(text, "\n  publish:", "\n  verify-writer-states:")
    exact_paths = {
        "data/decision_three_engines/five_year_supervised_ledger.csv.gz",
        "data/decision_three_engines/five_year_ledger_manifest.json",
        "models/decision_model_freeze.json",
        "models/decision_three_engine_data_validation.json",
        "models/decision_three_engines/promotion.joblib",
        "models/decision_three_engines/big_loss.joblib",
        "models/decision_three_engines/profit.joblib",
        "models/decision_three_engines/p_fill_shadow.joblib",
        "models/decision_three_engines/validation_latest.json",
        "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
    }
    for section in (compute, publish):
        match = re.search(
            r"allowed_paths = \{(?P<body>.*?)\n\s+\}",
            section,
            re.DOTALL,
        )
        assert match is not None
        assert set(re.findall(r'"([^"\n]+)"', match.group("body"))) == exact_paths
        assert "return path in allowed_paths" in section
        assert "data/decision_three_engines/debug.tmp" not in exact_paths
        assert "models/decision_three_engines/debug.joblib" not in exact_paths
        assert "parts[:2]" not in section
    assert "git add -A -- \\" in compute
    assert "git diff --cached --no-renames --name-only -z" in compute
    assert "git diff --cached --no-renames --name-status -z" in compute
    assert "git ls-files --stage -z" in compute
    assert "git diff --cached --no-renames --name-only -z" in publish
    assert "git diff --cached --no-renames --name-status -z" in publish
    assert "git ls-files --stage -z" in publish
    assert 'value not in {"A", "M"}' in compute
    assert 'mode != "100644"' in compute
    assert 'item.get("status") not in {"A", "M"}' in publish
    assert 'item.get("mode") != "100644"' in publish
    assert "non-allowlisted three-engine candidate paths staged" in compute
    assert "non-allowlisted three-engine publish paths staged" in publish


def test_candidate_envelope_binds_base_patch_manifest_paths_modes_and_hashes() -> None:
    text = _text()
    compute = _section(text, "  compute:", "\n  publish:")
    publish = _section(text, "\n  publish:", "\n  verify-writer-states:")
    for name in (
        "base_sha.txt",
        "candidate.patch",
        "candidate.patch.sha256",
        "candidate_manifest.json",
        "candidate_manifest.sha256",
    ):
        assert name in compute
        assert name in publish
    assert '"schema_version": "dc20_three_engine_candidate_v2"' in compute
    assert '"base_sha": base_sha' in compute
    assert '"promotion_release_eligible": promotion_release_eligible' in compute
    assert '"release_mode": release_mode' in compute
    assert '"all_core_heads_ready":' in compute
    assert '"status": statuses[path]' in compute
    assert '"mode": modes[path]' in compute
    assert '"sha256": sha256_path(path)' in compute
    assert "candidate.patch SHA256 mismatch" in publish
    assert "candidate manifest SHA256 mismatch" in publish
    assert "candidate envelope file set mismatch" in publish
    assert "retention-days: 1" in compute
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in compute
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in publish


def test_exact_base_replay_runs_the_complete_three_engine_test_suite() -> None:
    text = _text()
    replay = _section(
        text,
        "- name: Replay candidate on exact base and run all three-engine tests",
        "- name: Upload one-day immutable candidate envelope",
    )
    assert 'git worktree add --detach "${replay_root}" "$(cat "${RUNNER_TEMP}/base_sha.txt")"' in replay
    assert 'git -C "${replay_root}" apply --index --binary' in replay
    for name in (
        "test_three_engine_five_year_ledger.py",
        "test_validate_three_engine_five_year_ledger.py",
        "test_three_engine_models.py",
        "test_d_close_features.py",
        "test_auction_v3_three_engine_runtime.py",
        "test_decision_three_rank_contract.py",
        "test_decision_three_rank_frontend.py",
        "test_build_decision_three_rank_snapshot.py",
        "test_three_engine_training_workflow.py",
        "test_three_rank_freeze.py",
    ):
        assert name in replay


def test_publisher_is_contents_write_only_and_uses_base_sha_cas() -> None:
    text = _text()
    publish = _section(text, "\n  publish:", "\n  verify-writer-states:")
    assert "contents: write" in publish
    assert "persist-credentials: false" in publish
    assert 'test "$(git rev-parse HEAD)" = "${expected}"' in publish
    assert "git apply --index --binary" in publish
    assert "git fetch origin main" in publish
    assert 'test "$(git rev-parse origin/main)" = "${expected}"' in publish
    assert 'HEAD:main' in publish
    assert "git commit -m 'model: retrain independent Decision three engines'" in publish


def test_workflow_never_changes_existing_writer_activation_state() -> None:
    text = _text()
    snapshot = _section(
        text,
        "- name: Snapshot the four existing writer workflow states",
        "- name: Setup Python",
    )
    final = _section(text, "\n  verify-writer-states:")
    for path in (
        ".github/workflows/run_decision_daily.yml",
        ".github/workflows/run_auction_v3.yml",
        ".github/workflows/verify_decision_observations.yml",
        ".github/workflows/backfill_decision_v11_history.yml",
    ):
        assert path in snapshot
    assert "Finally verify all pre-existing writer states are unchanged" in final
    assert "existing writer states changed" in final
    assert "/enable" not in text
    assert "/disable" not in text
    assert "actions: write" not in text


def test_workflow_has_no_cross_repository_runtime_or_ssh_dependency() -> None:
    text = _text()
    assert "njedu2023-prog/top10-decision" not in text
    assert "git@github.com" not in text
    assert "ssh" not in text.lower()
    assert "TUSHARE_TOKEN" not in text


def test_job_mappings_do_not_repeat_outputs_or_any_other_top_level_job_key() -> None:
    """Catch silent YAML last-key-wins bugs without relying on a YAML library."""

    text = _text()
    jobs = text.split("\njobs:\n", 1)[1]
    starts = list(re.finditer(r"(?m)^  ([a-zA-Z0-9_-]+):\s*$", jobs))
    assert starts
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(jobs)
        body = jobs[match.end() : end]
        keys = re.findall(r"(?m)^    ([a-zA-Z0-9_-]+):(?:\s|$)", body)
        assert len(keys) == len(set(keys)), (
            f"job {match.group(1)!r} repeats mapping keys: {keys}"
        )
        assert keys.count("outputs") <= 1
