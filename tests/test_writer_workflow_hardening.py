from __future__ import annotations

import hashlib
import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
WRITERS = (
    "run_auction_v3.yml",
    "run_decision_daily.yml",
    "verify_decision_observations.yml",
    "backfill_decision_v11_history.yml",
)
UPLOAD_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_SHA = "d3f86a106a0bac45b974a628896c90dbdf5c8093"
ALLOWLISTS = {
    "run_auction_v3.yml": (
        "outputs/auction_v3/**",
        "outputs/decision/action_plan_*.json",
        "outputs/decision/report_index.json",
        "models/decision_v12_frozen_history_20260805.csv.gz",
        "data/market/trade_cal_sse.csv",
        "data/market/minute_1m/**",
        "data/market/raw/**/stk_auction_o.csv",
        "data/market/raw/**/stk_auction_o.meta.json",
        "docs/reports/auction_v3*.html",
    ),
    "run_decision_daily.yml": (
        "data/pred/pred_source_latest.csv",
        "data/pred/_pred_source_meta.json",
        "data/pred/archive/**",
        "data/pred/pred_top10_*.csv",
        "data/market/raw/**",
        "data/market/trade_cal_sse.csv",
        "data/market/features_base_*.csv",
        "data/market/features_limit_*.csv",
        "data/market/truth_close_*.csv",
        "data/market/_meta_*.json",
        "docs/weights/**",
        "docs/signals/**",
        "outputs/decision/**",
        "data/decision/**",
        "docs/reports/*.md",
    ),
    "verify_decision_observations.yml": (
        "outputs/auction_v3/verification/observation_*.csv",
        "outputs/auction_v3/verification/manual_actual_latest.csv",
        "outputs/auction_v3/metrics/observation_cumulative_latest.json",
        "outputs/auction_v3/metrics/manual_actual_cumulative_latest.json",
        "data/market/trade_cal_sse.csv",
        "data/market/raw/**/stk_auction_o.csv",
        "data/market/raw/**/stk_auction_o.meta.json",
    ),
    "backfill_decision_v11_history.yml": (
        "data/auction_v3/history/**",
        "data/market/trade_cal_sse.csv",
        "outputs/auction_v3/**",
        "outputs/decision/action_plan_*.json",
        "outputs/decision/report_index.json",
        "docs/reports/auction_v3*.html",
    ),
}


def _text(name: str) -> str:
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def _embedded_python_after(text: str, marker: str) -> str:
    section = text.split(marker, 1)[1]
    source = section.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(source)


def _embedded_python_blocks_between(text: str, marker: str, end_marker: str) -> list[str]:
    section = text.split(marker, 1)[1].split(end_marker, 1)[0]
    return [
        textwrap.dedent(block.split("\n          PY", 1)[0])
        for block in section.split("python - <<'PY'\n")[1:]
    ]


def _assert_auction_trigger_isolated(text: str) -> None:
    header = text.split("\npermissions:", 1)[0]
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert "workflow_run:" not in header
    assert "github.event.workflow_run" not in text
    assert 'cron: "35 1 * * 1-5"' in header
    assert "github.event_name == 'schedule' && github.ref == 'refs/heads/main'" in compute


def _assert_publish_hardening(text: str, name: str) -> None:
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    publish = text[text.index("\n  publish:") :]
    assert "candidate.patch.sha256" in compute, name
    assert "hashlib.sha256(patch.read_bytes()).hexdigest()" in compute, name
    assert "candidate.patch.sha256" in publish, name
    assert "expected_files = {'base_sha.txt', 'candidate.patch', 'candidate.patch.sha256'}" in publish, name
    assert "candidate envelope file set mismatch" in publish, name
    assert "candidate.patch SHA256 mismatch" in publish, name
    assert "publish_staged_paths.bin" in publish, name
    assert "non-allowlisted" in publish and "publish paths staged" in publish, name
    assert re.search(
        r"if bad: raise SystemExit\(f'non-allowlisted [A-Za-z]+ publish paths staged: \{bad\}'\)",
        publish,
    ), name
    for pattern in ALLOWLISTS[name]:
        assert pattern in publish, (name, pattern)


def _assert_verify_market_pin(text: str) -> None:
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert "Resolve immutable market upstream commit" in compute
    assert "https://api.github.com/repos/njedu2023-prog/a-share-top3-data/commits/main" in compute
    assert "re.fullmatch(r'[0-9a-f]{40}', sha)" in compute
    assert "MARKET_RAW_COMMIT: ${{ steps.upstream.outputs.market_sha }}" in compute


def test_every_writer_defaults_manual_dispatch_to_read_only_dry_run() -> None:
    for name in WRITERS:
        text = _text(name)
        dispatch = text.index("workflow_dispatch:")
        jobs = text.index("\njobs:")
        header = text[dispatch:jobs]
        assert re.search(
            r"dry_run:\s*\n\s+description:.*\n\s+required: true\s*\n"
            r"\s+default: true\s*\n\s+type: boolean",
            header,
        ), name
        assert 'INPUT_DRY_RUN: ${{ inputs.dry_run }}' in text, name
        assert "publish=false" in text, name
        assert "github.ref == 'refs/heads/main'" in text, name


def test_daily_preserves_the_last_auction_validated_action_plan() -> None:
    daily = _text("run_decision_daily.yml")
    prerequisite_step = daily.split(
        "- name: Require persisted Auction action for real Daily publication",
        1,
    )[1].split(
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
        1,
    )[0]
    replay_step = daily.split(
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
        1,
    )[1].split("- name: Resolve Daily exchange write eligibility", 1)[0]
    preserve_step = daily.split(
        "- name: Pin the last Auction-validated action plan",
        1,
    )[1].split("- name: Run Daily Decision once with learning and refresh disabled", 1)[0]
    run_step = daily.split(
        "- name: Run Daily Decision once with learning and refresh disabled",
        1,
    )[1].split("- name: Build exact allowlisted Daily candidate patch", 1)[0]
    assert "outputs/decision/action_plan_latest.json" in prerequisite_step
    assert "validate_action_plan_artifact" in prerequisite_step
    assert "AUCTION_ACTION_PREREQUISITE_FAILED" in prerequisite_step
    assert "path.is_symlink()" in prerequisite_step
    assert "validated=false\\nsimulation=true\\nsemantic_sha256=\\n" in prerequisite_step
    assert daily.index("Require persisted Auction action for real Daily publication") < daily.index(
        "Rebuild exact-base frozen Auction runtime"
    )
    assert "PERSISTED_ACTION_SEMANTIC_SHA256: ${{ steps.persisted_action.outputs.semantic_sha256 }}" in replay_step
    assert "PERSISTED_ACTION_VALIDATED: ${{ steps.persisted_action.outputs.validated }}" in replay_step
    assert "canonical_json_bytes(action)" in prerequisite_step
    assert "canonical_json_bytes(action)" in replay_step
    assert "object_pairs_hook=reject_duplicate_keys" in prerequisite_step
    assert "object_pairs_hook=reject_duplicate_keys" in replay_step
    assert "action.pop('generated_at_utc', None)" in prerequisite_step
    assert "action.pop('generated_at_utc', None)" in replay_step
    assert "Daily replayed action semantics differ from persisted Auction action" in replay_step
    assert "outputs/decision/action_plan_latest.json" in preserve_step
    assert "validate_action_plan_artifact" in preserve_step
    assert "force_enforcement=not model_freeze_active(manifest)" in preserve_step
    assert "audit.get('validated') is not True" in preserve_step
    assert "audit.get('enforced') is not True" in preserve_step
    assert "hashlib.sha256(raw).hexdigest()" in preserve_step
    assert "python scripts/replay_frozen_canonical_v2.py" in replay_step
    assert 'replay_report="${RUNNER_TEMP}/daily-frozen-replay.json"' in replay_step
    assert '> "${replay_stdout}"' in replay_step
    assert "> /dev/null" not in replay_step
    assert daily.index("Rebuild exact-base frozen Auction runtime") < daily.index(
        "Resolve Daily exchange write eligibility"
    )
    assert daily.index("Resolve Daily exchange write eligibility") < daily.index(
        "Sync prediction and market source snapshots"
    )
    assert daily.index("Sync prediction and market source snapshots") < daily.index(
        "Pin the last Auction-validated action plan"
    )
    assert "FROZEN_ACTION_SHA256: ${{ steps.frozen_action.outputs.sha256 }}" in preserve_step
    assert "hmac.compare_digest" in preserve_step
    assert "Daily source synchronization modified the frozen Auction action plan" in preserve_step
    assert "python scripts/run_v2.py" in run_step
    assert "PRESERVED_ACTION_SHA256" in run_step
    assert "hmac.compare_digest" in run_step
    assert "python scripts/validate_io_contract.py --strict-semantic" in run_step
    assert "validate_decision_model_freeze.py --runtime" in run_step
    assert "validate_decision_model_freeze.py --runtime --force-inactive" in run_step
    assert "validate_decision_model_freeze.py --history-only" not in daily
    assert "python scripts/publish_decision_action.py" not in run_step
    assert "python scripts/publish_decision_action.py" not in daily
    assert "git reset -q HEAD -- ':(glob)outputs/decision/action_plan_*.json'" in daily
    assert "forbidden=('outputs/auction_v3/**','outputs/decision/action_plan_*.json','outputs/decision/report_index.json')" in daily

    auction = _text("run_auction_v3.yml")
    assert "python scripts/publish_decision_action.py" in auction
    assert "python scripts/validate_decision_model_freeze.py --runtime" in auction


def test_daily_real_publication_rejects_invalid_persisted_auction_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from top10decision.decision import model_freeze

    daily = _text("run_decision_daily.yml")
    source = _embedded_python_after(
        daily,
        "- name: Require persisted Auction action for real Daily publication",
    )
    output_path = tmp_path / "github-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setattr(model_freeze, "load_model_freeze", lambda *_args, **_kwargs: {"active": True})
    monkeypatch.setattr(model_freeze, "model_freeze_active", lambda _manifest: True)

    def reject_transient_repair(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise model_freeze.DecisionModelFreezeError(
            "legacy action at /home/runner/work/DC20 contains 600000.SH token=TOPSECRET"
        )

    monkeypatch.setattr(model_freeze, "validate_action_plan_artifact", reject_transient_repair)
    monkeypatch.setenv("PUBLISH", "true")
    with pytest.raises(SystemExit) as exc_info:
        exec(compile(source, "<daily-persisted-action-prerequisite>", "exec"), {})
    assert exc_info.value.code == 1
    rendered = capsys.readouterr().out
    assert json.loads(rendered) == {
        "component": "daily_auction_action_prerequisite",
        "reason_code": "AUCTION_ACTION_PREREQUISITE_FAILED",
        "status": "fail",
    }
    assert "/home/runner" not in rendered
    assert "600000.SH" not in rendered
    assert "TOPSECRET" not in rendered
    assert not output_path.exists()


def test_daily_dry_run_marks_persisted_action_as_simulation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from top10decision.decision import model_freeze

    daily = _text("run_decision_daily.yml")
    source = _embedded_python_after(
        daily,
        "- name: Require persisted Auction action for real Daily publication",
    )
    output_path = tmp_path / "github-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("PUBLISH", "false")

    def must_not_validate(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("dry-run must not claim that persisted Auction action is valid")

    monkeypatch.setattr(model_freeze, "validate_action_plan_artifact", must_not_validate)
    exec(compile(source, "<daily-persisted-action-dry-run>", "exec"), {})
    assert output_path.read_text(encoding="utf-8") == (
        "validated=false\nsimulation=true\nsemantic_sha256=\n"
    )


def test_daily_real_action_comparison_ignores_only_valid_generation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from top10decision.decision import model_freeze

    daily = _text("run_decision_daily.yml")
    persisted_source = _embedded_python_after(
        daily,
        "- name: Require persisted Auction action for real Daily publication",
    )
    replay_blocks = _embedded_python_blocks_between(
        daily,
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
        "- name: Resolve Daily exchange write eligibility",
    )
    assert len(replay_blocks) == 2
    replay_validation_source = replay_blocks[1]
    action_path = tmp_path / "outputs/decision/action_plan_latest.json"
    action_path.parent.mkdir(parents=True)
    action = {
        "generated_at_utc": "2026-08-20T14:00:00+00:00",
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "candidates": [{"ts_code": "600000.SH", "action": "REJECT"}],
    }
    action_path.write_text(json.dumps(action), encoding="utf-8")
    persisted_output = tmp_path / "persisted-output.txt"
    replay_output = tmp_path / "replay-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISH", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(persisted_output))
    monkeypatch.setattr(model_freeze, "load_model_freeze", lambda *_args, **_kwargs: {"active": True})
    monkeypatch.setattr(model_freeze, "model_freeze_active", lambda _manifest: True)
    monkeypatch.setattr(
        model_freeze,
        "validate_action_plan_artifact",
        lambda *_args, **_kwargs: {"validated": True, "enforced": True},
    )
    exec(compile(persisted_source, "<daily-persisted-action-positive>", "exec"), {})
    persisted = dict(
        line.split("=", 1)
        for line in persisted_output.read_text(encoding="utf-8").splitlines()
    )
    assert persisted["validated"] == "true"
    assert persisted["simulation"] == "false"
    assert re.fullmatch(r"[0-9a-f]{64}", persisted["semantic_sha256"])

    raw_persisted_sha = hashlib.sha256(action_path.read_bytes()).hexdigest()
    action["generated_at_utc"] = "2026-08-20T14:00:01+00:00"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    assert hashlib.sha256(action_path.read_bytes()).hexdigest() != raw_persisted_sha
    monkeypatch.setenv("GITHUB_OUTPUT", str(replay_output))
    monkeypatch.setenv("PERSISTED_ACTION_VALIDATED", "true")
    monkeypatch.setenv(
        "PERSISTED_ACTION_SEMANTIC_SHA256",
        persisted["semantic_sha256"],
    )
    exec(compile(replay_validation_source, "<daily-replay-action-positive>", "exec"), {})
    assert re.fullmatch(
        r"sha256=[0-9a-f]{64}\n",
        replay_output.read_text(encoding="utf-8"),
    )

    action["status_code"] = "ACTIONABLE_BUY"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(SystemExit, match="semantics differ from persisted Auction action"):
        exec(compile(replay_validation_source, "<daily-replay-action-drift>", "exec"), {})

    duplicate_action = (
        '{"generated_at_utc":"2026-08-20T14:00:00+00:00",'
        '"generated_at_utc":"2026-08-20T14:00:01+00:00",'
        '"schema_version":"decision_action_plan_v12_top10_trade_selector",'
        '"status_code":"NO_TRADE_MODEL_NOT_PROMOTED","candidates":[]}'
    )
    action_path.write_text(duplicate_action, encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "duplicate-persisted-output.txt"))
    with pytest.raises(SystemExit) as persisted_duplicate:
        exec(compile(persisted_source, "<daily-persisted-action-duplicate>", "exec"), {})
    assert persisted_duplicate.value.code == 1
    with pytest.raises(SystemExit, match="replayed action JSON is invalid"):
        exec(compile(replay_validation_source, "<daily-replay-action-duplicate>", "exec"), {})


def test_daily_candidate_staging_tolerates_absent_optional_pred_top10(
    tmp_path: Path,
) -> None:
    daily = _text("run_decision_daily.yml")
    candidate_step = daily.split(
        "- name: Build exact allowlisted Daily candidate patch",
        1,
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    staging_lines = [
        line.strip()
        for line in candidate_step.splitlines()
        if line.strip().startswith("git add -A -- data/pred")
    ]
    assert staging_lines == ["git add -A -- data/pred"]
    assert "git add -A -- 'data/pred/pred_top10_*.csv'" not in candidate_step

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    pred_root = tmp_path / "data/pred"
    pred_root.mkdir(parents=True)
    (pred_root / "pred_source_latest.csv").write_text("trade_date\n20260820\n", encoding="utf-8")
    (pred_root / "_pred_source_meta.json").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        staging_lines[0].split(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged == [
        "data/pred/_pred_source_meta.json",
        "data/pred/pred_source_latest.csv",
    ]
    assert not list(pred_root.glob("pred_top10_*.csv"))


def test_daily_candidate_staging_tolerates_absent_optional_reports_and_tracks_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _text("run_decision_daily.yml")
    candidate_step = daily.split(
        "- name: Build exact allowlisted Daily candidate patch",
        1,
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    add_lines = [
        line.strip()
        for line in candidate_step.splitlines()
        if line.strip().startswith("git add -A --")
    ]
    assert "git add -A -- data/market" in add_lines
    assert "git add -A -- docs" in add_lines
    assert all("*" not in line for line in add_lines)

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    report = tmp_path / "docs/reports/daily.md"
    report.parent.mkdir(parents=True)
    report.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "docs"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DC20 Test",
            "-c",
            "user.email=dc20-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=tmp_path,
        check=True,
    )
    report.unlink()
    subprocess.run(["git", "add", "-A", "--", "docs"], cwd=tmp_path, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--no-renames", "--name-status"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged == ["D\tdocs/reports/daily.md"]

    compute_source = _embedded_python_blocks_between(
        daily,
        "- name: Build exact allowlisted Daily candidate patch",
        "- name: Upload immutable candidate patch",
    )[0]
    publish_source = _embedded_python_after(
        daily,
        "- name: Apply exact base candidate and create one commit",
    )
    staged_paths = tmp_path / "staged-paths.bin"
    staged_paths.write_bytes(b"docs/reports/nested/escape.md\0")
    monkeypatch.setenv("STAGED_PATHS", str(staged_paths))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(staged_paths))
    with pytest.raises(SystemExit, match="non-allowlisted Daily paths staged"):
        exec(compile(compute_source, "<daily-compute-segment-allowlist>", "exec"), {})
    with pytest.raises(SystemExit, match="non-allowlisted Daily publish paths staged"):
        exec(compile(publish_source, "<daily-publish-segment-allowlist>", "exec"), {})


def test_daily_restores_auction_reports_before_live_source_mutation(
    tmp_path: Path,
) -> None:
    daily = _text("run_decision_daily.yml")
    replay_step = daily.split(
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
        1,
    )[1].split("- name: Resolve Daily exchange write eligibility", 1)[0]
    report_pathspec = ":(glob,top)docs/reports/auction_v3*.html"
    assert "git ls-files -z --cached --" in replay_step
    assert "git restore --worktree --source=HEAD" in replay_step
    assert "--pathspec-from-file=\"${auction_reports}\" --pathspec-file-nul" in replay_step
    assert f"git clean -f -- '{report_pathspec}'" in replay_step
    assert "daily-auction-report-dirty.bin" in replay_step

    repo = tmp_path / "report-restore"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    reports = repo / "docs/reports"
    reports.mkdir(parents=True)
    tracked = reports / "auction_v3_latest.html"
    tracked.write_text("tracked baseline\n", encoding="utf-8")
    daily_owned = reports / "daily.md"
    daily_owned.write_text("daily baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "docs"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DC20 Test",
            "-c",
            "user.email=dc20-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=repo,
        check=True,
    )
    tracked.write_text("replay mutation\n", encoding="utf-8")
    generated = reports / "auction_v3_generated.html"
    generated.write_text("replay generated\n", encoding="utf-8")
    daily_owned.write_text("daily mutation\n", encoding="utf-8")

    tracked_paths = tmp_path / "tracked-auction-reports.bin"
    tracked_paths.write_bytes(
        subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--", report_pathspec],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout
    )
    subprocess.run(
        [
            "git",
            "restore",
            "--worktree",
            "--source=HEAD",
            f"--pathspec-from-file={tracked_paths}",
            "--pathspec-file-nul",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "clean", "-f", "--", report_pathspec], cwd=repo, check=True)
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            report_pathspec,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert dirty == b""
    assert tracked.read_text(encoding="utf-8") == "tracked baseline\n"
    assert not generated.exists()
    assert daily_owned.read_text(encoding="utf-8") == "daily mutation\n"


def test_daily_allowlists_expose_both_sides_of_renames(
    tmp_path: Path,
) -> None:
    daily = _text("run_decision_daily.yml")
    assert daily.count("git diff --cached --no-renames --name-only -z") == 2

    repo = tmp_path / "rename-case"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "data/pred/unexpected_tracked.txt"
    source.parent.mkdir(parents=True)
    source.write_text("unexpected\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "data/pred"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DC20 Test",
            "-c",
            "user.email=dc20-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=repo,
        check=True,
    )
    destination = repo / "data/pred/archive/renamed.csv"
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    subprocess.run(["git", "add", "-A", "--", "data/pred"], cwd=repo, check=True)
    visible = subprocess.run(
        ["git", "diff", "--cached", "--no-renames", "--name-only", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    assert {item.decode() for item in visible if item} == {
        "data/pred/unexpected_tracked.txt",
        "data/pred/archive/renamed.csv",
    }


def test_daily_allowlists_reject_symlink_and_gitlink_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _text("run_decision_daily.yml")
    assert daily.count("git ls-files --stage -z") == 2
    compute_source = _embedded_python_blocks_between(
        daily,
        "- name: Build exact allowlisted Daily candidate patch",
        "- name: Upload immutable candidate patch",
    )[0]
    publish_source = _embedded_python_after(
        daily,
        "- name: Apply exact base candidate and create one commit",
    )

    repo = tmp_path / "mode-case"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    archive = repo / "data/pred/archive"
    archive.mkdir(parents=True)
    (archive / "link.csv").symlink_to("missing-target.csv")
    embedded = archive / "embedded"
    subprocess.run(["git", "init", "-q", str(embedded)], check=True)
    (embedded / "payload.txt").write_text("embedded\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "payload.txt"], cwd=embedded, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DC20 Test",
            "-c",
            "user.email=dc20-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "embedded",
        ],
        cwd=embedded,
        check=True,
    )
    subprocess.run(["git", "add", "-A", "--", "data/pred"], cwd=repo, check=True)
    staged_paths = tmp_path / "mode-staged-paths.bin"
    staged_paths.write_bytes(
        subprocess.run(
            ["git", "diff", "--cached", "--no-renames", "--name-only", "-z"],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout
    )
    staged_index = tmp_path / "mode-staged-index.bin"
    index_bytes = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    staged_index.write_bytes(index_bytes)
    assert b"120000 " in index_bytes
    assert b"160000 " in index_bytes

    monkeypatch.setenv("STAGED_PATHS", str(staged_paths))
    monkeypatch.setenv("STAGED_INDEX", str(staged_index))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(staged_paths))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(staged_index))
    with pytest.raises(SystemExit, match="non-regular Daily paths staged") as compute_error:
        exec(compile(compute_source, "<daily-compute-mode-allowlist>", "exec"), {})
    assert "120000" in str(compute_error.value) and "160000" in str(compute_error.value)
    with pytest.raises(SystemExit, match="non-regular Daily publish paths staged") as publish_error:
        exec(compile(publish_source, "<daily-publish-mode-allowlist>", "exec"), {})
    assert "120000" in str(publish_error.value) and "160000" in str(publish_error.value)


def test_daily_frozen_replay_failure_summary_is_safe_and_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    daily = _text("run_decision_daily.yml")
    source = _embedded_python_after(
        daily,
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
    )
    cases = (
        ("pinned file mismatch at /home/runner/secret", "PIN_DRIFT"),
        ("action watchlist differs for 600000.SH", "ACTION_WATCHLIST_DRIFT"),
        ("canonical runtime differs", "CANONICAL_RUNTIME_DRIFT"),
        ("frozen history snapshot differs", "FROZEN_HISTORY_DRIFT"),
        ("unexpected token=TOPSECRET", "FROZEN_RUNTIME_VALIDATION_FAILED"),
    )
    report = tmp_path / "replay-report.json"
    monkeypatch.setenv("REPLAY_REPORT", str(report))
    for error, expected_reason in cases:
        report.write_text(json.dumps({"status": "fail", "error": error}), encoding="utf-8")
        exec(compile(source, "<daily-frozen-replay-summary>", "exec"), {})
        rendered = capsys.readouterr().out
        assert rendered.count("\n") == 1
        payload = json.loads(rendered)
        assert payload == {
            "component": "daily_frozen_replay",
            "error_sha256": hashlib.sha256(error.encode("utf-8")).hexdigest(),
            "reason_code": expected_reason,
            "status": "fail",
        }
        assert error not in rendered
        assert "/home/runner" not in rendered
        assert "600000.SH" not in rendered
        assert "TOPSECRET" not in rendered


def test_daily_action_plan_pin_and_byte_preservation_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _text("run_decision_daily.yml")
    pin_source = _embedded_python_after(
        daily,
        "- name: Pin the last Auction-validated action plan",
    )
    preserve_source = _embedded_python_after(
        daily,
        "- name: Run Daily Decision once with learning and refresh disabled",
    )
    action_path = tmp_path / "outputs/decision/action_plan_latest.json"
    action_path.parent.mkdir(parents=True)
    action = {
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "formal_buy_count": 0,
        "guidance_only": True,
        "broker_connected": False,
        "order_execution": "manual_only",
        "candidates": [{"action": "SHADOW_ONLY"}, {"action": "REJECT"}],
        "model": {"promoted": False},
    }
    action_path.write_text(json.dumps(action), encoding="utf-8")
    output_path = tmp_path / "github-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    assert "validate_action_plan_artifact" in pin_source
    expected = hashlib.sha256(action_path.read_bytes()).hexdigest()
    monkeypatch.setenv("PRESERVED_ACTION_SHA256", expected)
    exec(compile(preserve_source, "<daily-action-preserve>", "exec"), {})

    action["status_code"] = "PENDING_AUCTION_MODEL"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(SystemExit, match="modified the preserved Auction action plan"):
        exec(compile(preserve_source, "<daily-action-preserve>", "exec"), {})


def test_compute_jobs_are_read_only_and_never_publish() -> None:
    for name in WRITERS:
        text = _text(name)
        compute = text[text.index("  compute:") : text.index("\n  publish:")]
        assert "permissions:\n      contents: read" in compute, name
        assert "persist-credentials: false" in compute, name
        assert "persist-credentials: true" not in text, name
        assert "git commit" not in compute, name
        assert "git push" not in compute, name
        assert "contents: write" not in compute, name
        assert "actions/upload-artifact@" + UPLOAD_SHA in compute, name


def test_real_publication_requires_active_freeze_and_enforced_pins() -> None:
    for name in WRITERS:
        text = _text(name)
        compute = text[text.index("  compute:") : text.index("\n  publish:")]
        assert "model_freeze_active" in compute, name
        assert "validate_pinned_files" in compute, name
        assert "real " in compute and "requires active Decision freeze" in compute, name
        assert "audit.get('enforced') is not True" in compute, name


def test_publish_jobs_are_single_commit_exact_base_cas() -> None:
    for name in WRITERS:
        text = _text(name)
        publish = text[text.index("\n  publish:") :]
        assert text.count("contents: write") == 1, name
        assert text.count("git commit -m") == 1, name
        assert text.count("git push ") == 1, name
        assert "permissions:\n      contents: write" in publish, name
        assert "actions/download-artifact@" + DOWNLOAD_SHA in publish, name
        assert "persist-credentials: false" in publish, name
        assert 'test "$(git rev-parse HEAD)" = "${expected}"' in publish, name
        assert 'test "$(git rev-parse origin/main)" = "${expected}"' in publish, name
        assert "git apply --index --binary" in publish, name
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in publish, name
        assert "git rebase" not in text, name


def test_candidate_patches_enforce_exact_staged_path_allowlists() -> None:
    for name in WRITERS:
        text = _text(name)
        compute = text[text.index("  compute:") : text.index("\n  publish:")]
        assert "staged_paths.bin" in compute, name
        assert "non-allowlisted" in compute, name
        assert "git diff --cached --binary --full-index" in compute, name
        assert "git add -A ." not in compute, name


def test_auction_has_independent_0935_schedule_and_no_daily_workflow_run() -> None:
    text = _text("run_auction_v3.yml")
    _assert_auction_trigger_isolated(text)
    mutated = text.replace('cron: "35 1 * * 1-5"', 'cron: "15 13 * * 1-5"', 1)
    with pytest.raises(AssertionError):
        _assert_auction_trigger_isolated(mutated)


def test_publishers_recheck_envelope_checksum_and_exact_allowlist() -> None:
    for name in WRITERS:
        text = _text(name)
        _assert_publish_hardening(text, name)
        mutated = text.replace("if bad: raise SystemExit(f'non-allowlisted", "if False: raise SystemExit(f'non-allowlisted")
        with pytest.raises(AssertionError):
            _assert_publish_hardening(mutated, name)


@pytest.mark.parametrize("name", WRITERS)
def test_publisher_envelope_rejects_patch_checksum_mismatch(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / name / "candidate"
    candidate.mkdir(parents=True)
    (candidate / "base_sha.txt").write_text("a" * 40 + "\n", encoding="ascii")
    patch = b"candidate patch bytes\n"
    (candidate / "candidate.patch").write_bytes(patch)
    checksum = candidate / "candidate.patch.sha256"
    checksum.write_text(hashlib.sha256(patch).hexdigest() + "\n", encoding="ascii")
    monkeypatch.setenv("CANDIDATE_DIR", str(candidate))
    source = _embedded_python_after(_text(name), "- name: Verify immutable candidate envelope")
    exec(compile(source, f"<{name}-envelope>", "exec"), {})
    checksum.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(SystemExit, match=r"candidate\.patch SHA256 mismatch"):
        exec(compile(source, f"<{name}-tampered-envelope>", "exec"), {})


def test_auction_current_run_sync_evidence_allows_clean_closed_checkout_and_ignores_stale_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _text("run_auction_v3.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert '${RUNNER_TEMP}/auction-minute-sync.json' in compute
    assert "SYNC_EVIDENCE" in compute
    assert "data/market/minute_1m/sync_latest.json" not in compute
    stale = tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"status": "fail", "active_window": True}), encoding="utf-8")
    evidence = tmp_path / "runner-temp" / "auction-minute-sync.json"
    evidence.parent.mkdir()
    evidence.write_text(
        json.dumps(
            {
                "status": "not_applicable",
                "reason": "exchange_closed",
                "active_window": False,
                "candidate_codes": 0,
                "minute_files_written": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYNC_EVIDENCE", str(evidence))
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    source = _embedded_python_after(compute, "SYNC_EVIDENCE=\"${sync_evidence}\"")
    exec(compile(source, "<auction-sync-evidence>", "exec"), {})
    assert output.read_text(encoding="utf-8") == "write_eligible=false\n"


def test_auction_current_run_sync_evidence_fails_open_session_without_valid_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "auction-minute-sync.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "partial_success",
                "reason": "minute_partial_success",
                "active_window": True,
                "candidate_codes": 3,
                "minute_files_written": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SYNC_EVIDENCE", str(evidence))
    compute = _text("run_auction_v3.yml").split("\n  publish:", 1)[0]
    source = _embedded_python_after(compute, "SYNC_EVIDENCE=\"${sync_evidence}\"")
    with pytest.raises(SystemExit, match="produced no valid minute rows"):
        exec(compile(source, "<auction-open-empty-sync-evidence>", "exec"), {})


@pytest.mark.parametrize("reason", ("exchange_closed", "outside_active_minute_window"))
def test_daily_and_auction_no_write_sessions_make_publish_unreachable(
    reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = _text("run_decision_daily.yml")
    auction = _text("run_auction_v3.yml")
    for text, gate in ((daily, "session"), (auction, "minute_sync")):
        compute = text[text.index("  compute:") : text.index("\n  publish:")]
        assert "has_changes: ${{ steps.candidate.outputs.has_changes || 'false' }}" in compute
        assert f"if: ${{{{ steps.{gate}.outputs.write_eligible == 'true' }}}}" in compute
        assert (
            f"steps.{gate}.outputs.write_eligible == 'true' && steps.mode.outputs.publish == 'true'"
            in compute
        )
        publish = text[text.index("\n  publish:") :]
        assert "needs.compute.outputs.has_changes == 'true'" in publish

    payload = {
        "status": "not_applicable",
        "reason": reason,
        "active_window": False,
        "candidate_codes": 0,
        "minute_files_written": 0,
    }
    for name, marker, env_name in (
        ("run_decision_daily.yml", 'SESSION_EVIDENCE="${session_evidence}"', "SESSION_EVIDENCE"),
        ("run_auction_v3.yml", 'SYNC_EVIDENCE="${sync_evidence}"', "SYNC_EVIDENCE"),
    ):
        evidence = tmp_path / f"{name}-{reason}.json"
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        output = tmp_path / f"{name}-{reason}.out"
        monkeypatch.setenv(env_name, str(evidence))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        source = _embedded_python_after(_text(name).split("\n  publish:", 1)[0], marker)
        exec(compile(source, f"<{name}-{reason}>", "exec"), {})
        assert output.read_text(encoding="utf-8") == "write_eligible=false\n"


def test_verify_resolves_immutable_market_commit_and_missing_token_fails_before_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _text("verify_decision_observations.yml")
    _assert_verify_market_pin(text)
    mutated = text.replace("MARKET_RAW_COMMIT: ${{ steps.upstream.outputs.market_sha }}", "")
    with pytest.raises(AssertionError):
        _assert_verify_market_pin(mutated)
    monkeypatch.delenv("GH_API_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output"))
    source = _embedded_python_after(text, "- name: Resolve immutable market upstream commit")
    with pytest.raises(SystemExit, match="GITHUB_TOKEN is required"):
        exec(compile(source, "<verify-missing-market-commit>", "exec"), {})


def test_verify_prior_date_uses_current_stdout_and_ignores_stale_sync_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _text("verify_decision_observations.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert '${RUNNER_TEMP}/verify-minute-sync.json' in compute
    assert "data/market/minute_1m/sync_latest.json" not in compute
    stale = tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"status": "success", "active_window": True}), encoding="utf-8")
    as_of = "19990104"
    evidence = tmp_path / "verify-minute-sync.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "not_applicable",
                "reason": "non_current_trade_date",
                "trade_date": as_of,
                "active_window": False,
                "candidate_codes": 4,
                "minute_files_written": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AS_OF_DATE", as_of)
    monkeypatch.setenv("MINUTE_EVIDENCE", str(evidence))
    source = _embedded_python_after(compute, 'MINUTE_EVIDENCE="${minute_evidence}"')
    exec(compile(source, "<verify-prior-date-minute-evidence>", "exec"), {})


def test_verify_current_date_requires_post_close_truth_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    text = _text("verify_decision_observations.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert "minute_args+=(--post-close-truth)" in compute
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    evidence = tmp_path / "verify-post-close.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "success",
                "reason": "post_close_truth_success",
                "trade_date": today,
                "active_window": False,
                "candidate_codes": 4,
                "minute_files_written": 4,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AS_OF_DATE", today)
    monkeypatch.setenv("MINUTE_EVIDENCE", str(evidence))
    source = _embedded_python_after(compute, 'MINUTE_EVIDENCE="${minute_evidence}"')
    exec(compile(source, "<verify-current-post-close-evidence>", "exec"), {})
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload.update(status="success", reason="post_close_truth_success", minute_files_written=0)
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="post-close Verify minute sync produced no valid rows"):
        exec(compile(source, "<verify-current-zero-row-evidence>", "exec"), {})
    payload.update(status="not_applicable", reason="outside_active_minute_window", minute_files_written=0)
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="post-close Verify minute sync produced no valid rows"):
        exec(compile(source, "<verify-current-outside-evidence>", "exec"), {})
    payload.update(status="not_applicable", reason="exchange_closed", minute_files_written=0)
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="post-close Verify minute sync produced no valid rows"):
        exec(compile(source, "<verify-current-closed-evidence>", "exec"), {})


def test_verify_closed_session_cannot_reach_candidate_artifact_or_publisher() -> None:
    text = _text("verify_decision_observations.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert "has_changes: ${{ steps.candidate.outputs.has_changes || 'false' }}" in compute
    assert "id: upstream\n        if: ${{ steps.session.outputs.is_open == 'true' }}" in compute
    assert "id: candidate\n        if: ${{ steps.session.outputs.is_open == 'true' }}" in compute
    assert "steps.session.outputs.is_open == 'true' && steps.mode.outputs.publish == 'true'" in compute
    publish = text[text.index("\n  publish:") :]
    assert "needs.compute.outputs.has_changes == 'true'" in publish


def test_verify_isolates_full_runtime_from_observation_settlement() -> None:
    text = _text("verify_decision_observations.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    runtime = compute.split(
        "- name: Validate frozen runtime in an isolated exact-base worktree", 1
    )[1].split("- name: Settle observation truth without changing action ownership", 1)[0]
    settle = compute.split(
        "- name: Settle observation truth without changing action ownership", 1
    )[1].split("- name: Build exact allowlisted Verify candidate patch", 1)[0]
    assert 'runtime_root="${RUNNER_TEMP}/verify-runtime"' in runtime
    assert 'git worktree add --detach "${runtime_root}"' in runtime
    assert '--root "${runtime_root}"' in runtime
    assert '--root "${GITHUB_WORKSPACE}"' not in runtime
    assert "validate_decision_model_freeze.py" in runtime
    assert "--runtime --force-inactive" in runtime
    assert "--history-only" not in runtime
    assert 'git worktree remove --force "${runtime_root}"' in runtime
    assert "replay_frozen_canonical_v2.py" not in settle
    assert "validate_decision_model_freeze.py" not in settle
    assert "git status --porcelain=v1 --untracked-files=all -- outputs/decision" in settle
    assert settle.count("git status --porcelain=v1") == 2
    assert "python scripts/verify_decision_observations.py" in settle
    assert "git add -A -- 'outputs/decision/action_plan_20*.json'" not in compute
    assert "forbidden=('outputs/decision/action_plan_*.json','outputs/decision/report_index.json')" in compute
    publish = text[text.index("\n  publish:") :]
    assert "outputs/decision/action_plan_20*.json" not in publish
    assert "forbidden=('outputs/decision/action_plan_*.json','outputs/decision/report_index.json')" in publish

    verify_script = (ROOT / "scripts" / "verify_decision_observations.py").read_text(
        encoding="utf-8"
    )
    assert "refresh_action_plan_observations" not in verify_script
    assert "refreshed_action_plans" not in verify_script


def test_truth_writers_do_not_keep_optional_or_continue_on_error_bypasses() -> None:
    for name in ("run_auction_v3.yml", "run_decision_daily.yml", "verify_decision_observations.yml"):
        text = _text(name)
        assert "--optional" not in text, name
        assert "continue-on-error" not in text, name
    assert "open production Auction sync produced no valid minute rows" in _text(
        "run_auction_v3.yml"
    )
    assert "current post-close Verify minute sync produced no valid rows" in _text(
        "verify_decision_observations.yml"
    )


def test_daily_production_learning_and_refresh_jobs_are_fail_closed() -> None:
    text = _text("run_decision_daily.yml")
    forbidden = (
        "train_pfill.py",
        "train_eret.py",
        "Commit learning artifacts",
        "Commit refreshed decision outputs",
    )
    for marker in forbidden:
        assert marker not in text
    learning = text.split("  pfill_learning:", 1)[1].split(
        "  decision_refresh_after_learning:", 1
    )[0]
    refresh = text.split("  decision_refresh_after_learning:", 1)[1].split(
        "  publish:", 1
    )[0]
    assert "if: ${{ false }}" in learning
    assert "if: ${{ false }}" in refresh
    assert "contents: write" not in learning + refresh
    assert "git commit" not in learning + refresh
    assert "git push" not in learning + refresh


def test_backfill_has_no_early_history_commit_before_all_gates() -> None:
    text = _text("backfill_decision_v11_history.yml")
    compute = text[text.index("  compute:") : text.index("\n  publish:")]
    assert "Run all Backfill gates before candidate creation" in compute
    assert "Persist immutable history before model rebuild" not in text
    assert "git commit" not in compute
