from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _embedded_python_after(text: str, marker: str) -> str:
    section = text.split(marker, 1)[1]
    source = section.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(source)


def test_daily_freezes_only_exact_isolated_d_surface_then_projects_n_without_padding() -> None:
    text = _text("run_decision_daily.yml")
    step = _between(
        text,
        "- name: Freeze immutable executable-profit research order and public D projection",
        "- name: Project isolated legacy-profit relative research sidecar",
    )
    assert text.index("Build isolated full Daily research context") < text.index(
        "Freeze immutable executable-profit research order"
    )
    assert (
        'feature_path="${research_root}/outputs/auction_v3/predictions/'
        'pred_${signal_date}.csv"'
    ) in step
    assert (
        'test -f "${research_root}/outputs/decision/'
        'three_rank_top10_${signal_date}.json"'
    ) in step
    assert (
        'test -f "${research_root}/outputs/decision/'
        'three_rank_top10_${signal_date}.csv"'
    ) in step
    assert "validate_decision_run_receipt" in step
    assert (
        'PYTHONPATH="${research_root}/src" python "${research_root}/scripts/'
        'run_decision_executable_profit_forward_shadow.py"'
    ) in step
    assert step.count('--root "${research_root}"') == 3
    assert '--root "${GITHUB_WORKSPACE}"' not in step
    assert step.index("run_decision_executable_profit_forward_shadow.py") < step.index(
        "--statistics-only"
    ) < step.index("project_decision_executable_profit_research.py")
    assert '--as-of-date "${signal_date}"' in step
    assert "validate_internal_forward_shadow_payload" in step
    assert "validate_internal_forward_shadow_index" in step
    assert "validate_selection_index_chain(root, selection_index)" in step
    assert "validate_research_projection(projection, require_downloads=True)" in step
    assert "validate_research_projection_index(public_index)" in step
    assert "validate_public_index_chain(root, public_index)" in step
    assert "validate_shadow_statistics_projection(statistics)" in step
    assert "candidate_count') > 10" in step
    assert "len(set(exact_relatives)) != 8" in step
    assert "'outputs_auction_v3_copied': False" in step
    assert "Daily rewrote an immutable selection" in step
    assert "Daily created a foreign selection artifact" in step
    assert "Daily executable-profit projection modified the preserved action" in step
    assert "github.com/njedu2023-prog/top10-decision" not in step
    assert "codex" not in step.lower()


def test_daily_executes_scorer_root_binding_against_canonical_pred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _text("run_decision_daily.yml")
    source = _embedded_python_after(
        text,
        'ROOT_CONTRACT_EVIDENCE="${root_contract_evidence}"',
    )
    signal_date = "20260824"
    research_root = tmp_path / "research-root"
    canonical = (
        research_root
        / "outputs"
        / "auction_v3"
        / "predictions"
        / f"pred_{signal_date}.csv"
    )
    canonical.parent.mkdir(parents=True)
    canonical.write_text("signal_date,ts_code\n20260824,000001.SZ\n", encoding="utf-8")
    evidence_path = tmp_path / "root-contract.json"
    monkeypatch.setenv("RESEARCH_ROOT", str(research_root))
    monkeypatch.setenv("FEATURE_PATH", str(canonical))
    monkeypatch.setenv("SIGNAL_DATE", signal_date)
    monkeypatch.setenv("ROOT_CONTRACT_EVIDENCE", str(evidence_path))

    exec(compile(source, "<daily-scorer-root-binding>", "exec"), {})
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence == {
        "canonical_relative_path": (
            "outputs/auction_v3/predictions/pred_20260824.csv"
        ),
        "d_feature_path": canonical.resolve().as_posix(),
        "repo_root": research_root.resolve().as_posix(),
        "same_repository_root": True,
        "schema_version": "dc20_executable_profit_scorer_root_binding_v1",
        "signal_date": signal_date,
    }

    foreign = tmp_path / "foreign" / f"pred_{signal_date}.csv"
    foreign.parent.mkdir()
    foreign.write_bytes(canonical.read_bytes())
    monkeypatch.setenv("FEATURE_PATH", str(foreign))
    with pytest.raises(
        SystemExit,
        match="scorer root and canonical pred_D feature root differ",
    ):
        exec(compile(source, "<daily-scorer-root-mismatch>", "exec"), {})


def test_daily_candidate_and_publisher_allow_only_exact_research_artifacts() -> None:
    text = _text("run_decision_daily.yml")
    exact_patterns = (
        "data/decision_executable_profit/forward/selections/shadow_20??????.json",
        "data/decision_executable_profit/forward/selections/shadow_20??????.csv",
        "data/decision_executable_profit/forward/selections/index.json",
        "data/decision_executable_profit/forward/statistics/summary.json",
        "outputs/decision/executable_profit_research/projection_20??????.json",
        "outputs/decision/executable_profit_research/projection_20??????.csv",
        (
            "outputs/decision/executable_profit_research/"
            "shadow_statistics_20??????_asof_20??????.json"
        ),
        "outputs/decision/executable_profit_research/index.json",
    )
    for pattern in exact_patterns:
        assert text.count(pattern) >= 2, pattern
    assert text.count("forbidden=('outputs/auction_v3/**','outputs/decision/action_plan_*.json','outputs/decision/report_index.json')") == 2


def test_daily_main_allowlists_execute_fail_closed_for_auction_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _text("run_decision_daily.yml")
    compute = _embedded_python_after(
        text,
        "- name: Build exact allowlisted Daily candidate patch",
    )
    publish = _embedded_python_after(
        text,
        "- name: Apply exact base candidate and create one commit",
    )
    forbidden = "outputs/auction_v3/predictions/pred_20260824.csv"
    paths = tmp_path / "paths.bin"
    index = tmp_path / "index.bin"
    paths.write_bytes(forbidden.encode() + b"\0")
    index.write_bytes(
        b"100644 " + b"0" * 40 + b" 0\t" + forbidden.encode() + b"\0"
    )
    monkeypatch.setenv("STAGED_PATHS", str(paths))
    monkeypatch.setenv("STAGED_INDEX", str(index))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(paths))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(index))

    with pytest.raises(SystemExit, match="non-allowlisted Daily paths staged"):
        exec(compile(compute, "<daily-auction-output-compute>", "exec"), {})
    with pytest.raises(
        SystemExit,
        match="non-allowlisted Daily publish paths staged",
    ):
        exec(compile(publish, "<daily-auction-output-publish>", "exec"), {})


def test_verify_continues_all_frozen_forward_dates_to_exact_asof_and_keeps_latest_d() -> None:
    text = _text("verify_decision_observations.yml")
    step = _between(
        text,
        "- name: Settle immutable executable-profit Shadow truth and project exact as-of",
        "- name: Build exact allowlisted Verify candidate patch",
    )
    guard = (
        "steps.session.outputs.is_open == 'true' && "
        "steps.truth_sync.outputs.complete == 'true'"
    )
    assert guard in step
    assert "selection_root.glob('shadow_20??????.json')" in step
    assert "load_selection(root, signal_date)" in step
    assert "if signal_date <= as_of_date:" in step
    assert "if settlement_path.exists():" in step
    assert "targets.append(signal_date)" in step
    assert "validate_internal_forward_shadow_index(index)" in step
    assert "_validate_existing_pointer_chain(root, index)" in step
    assert "latest != max(all_signal_dates)" in step
    assert "latest > as_of_date" in step
    assert "selection CSV binding drifted" in step
    assert 'done < "${eligible_dates}"' in step
    assert 'if [ -s "${latest_date}" ]; then' in step
    assert 'if [ -s "${eligible_dates}" ]; then' in step
    assert step.index('done < "${eligible_dates}"') < step.index("--statistics-only")
    assert '--signal-date "${signal_date}"' in step
    assert '--as-of-date "${AS_OF_DATE}"' in step
    assert "--statistics-only" in step
    assert step.index("settle_decision_executable_profit_forward_shadow.py") < step.index(
        "project_decision_executable_profit_research.py"
    )
    assert "after_selections != before['selections']" in step
    assert "Verify rewrote or created an executable-profit selection" in step
    assert "after_actions != before['actions']" in step
    assert "Verify rewrote immutable Shadow truth" in step
    assert "index.get('latest_signal_date') != latest" in step
    assert "statistics.get('as_of_date') != as_of_date" in step
    assert "if latest:" in step
    assert "if targets:" not in step
    assert "run_decision_executable_profit_forward_shadow.py" not in step
    assert "github.com/njedu2023-prog/top10-decision" not in step
    assert "codex" not in step.lower()


def test_verify_partial_current_truth_cannot_freeze_public_shadow_asof() -> None:
    text = _text("verify_decision_observations.yml")
    sync = _between(
        text,
        "- name: Sync all same-date truth without optional bypass",
        "- name: Validate frozen runtime in an isolated exact-base worktree",
    )
    assert "id: truth_sync" in sync
    assert "('partial_success', 'post_close_truth_partial')" in sync
    assert (
        "complete = as_of != today or (status, reason) == "
        "('success', 'post_close_truth_success')"
    ) in sync
    assert "output.write(f\"complete={'true' if complete else 'false'}\\n\")" in sync


def test_verify_candidate_and_publisher_recheck_exact_truth_and_forbid_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _text("verify_decision_observations.yml")
    compute = _embedded_python_after(
        text,
        "- name: Build exact allowlisted Verify candidate patch",
    )
    publish = _embedded_python_after(
        text,
        "- name: Apply exact base candidate and create one commit",
    )
    valid = (
        "data/market/raw/2026/20260825/daily.csv",
        "data/market/raw/2026/20260825/stk_limit.csv",
        (
            "data/decision_executable_profit/forward/verifications/"
            "t_verification_20260824.json"
        ),
        (
            "data/decision_executable_profit/forward/settlements/"
            "settlement_20260824.json"
        ),
        "data/decision_executable_profit/forward/statistics/summary.json",
        (
            "outputs/decision/executable_profit_research/"
            "shadow_statistics_20260824_asof_20260825.json"
        ),
        "outputs/decision/executable_profit_research/index.json",
    )
    paths = tmp_path / "paths.bin"
    index = tmp_path / "index.bin"
    paths.write_bytes(b"\0".join(value.encode() for value in valid) + b"\0")
    index.write_bytes(
        b"".join(
            b"100644 " + b"0" * 40 + b" 0\t" + value.encode() + b"\0"
            for value in valid
        )
    )
    monkeypatch.setenv("STAGED_PATHS", str(paths))
    monkeypatch.setenv("STAGED_INDEX", str(index))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(paths))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(index))
    exec(compile(compute, "<verify-profit-compute>", "exec"), {})
    exec(compile(publish, "<verify-profit-publish>", "exec"), {})

    forbidden = (
        "data/decision_executable_profit/forward/selections/"
        "shadow_20260824.json"
    )
    paths.write_bytes(forbidden.encode() + b"\0")
    index.write_bytes(
        b"100644 " + b"0" * 40 + b" 0\t" + forbidden.encode() + b"\0"
    )
    with pytest.raises(SystemExit, match="non-allowlisted Verify paths staged"):
        exec(compile(compute, "<verify-profit-forbidden-compute>", "exec"), {})
    with pytest.raises(SystemExit, match="non-allowlisted Verify publish paths staged"):
        exec(compile(publish, "<verify-profit-forbidden-publish>", "exec"), {})


def test_pages_validates_and_publicly_refetches_exact_profit_chain() -> None:
    text = _text("deploy_dc20_pages.yml")
    assert "_validate_existing_index_chain(repo_root, index)" in text
    assert "_validate_existing_index_chain(site_root, index)" in text
    assert text.index("_validate_existing_index_chain(repo_root, index)") < text.index(
        "copy_exact_source(kind, relative, expected_sha256)"
    ) < text.index("_validate_existing_index_chain(site_root, index)")
    assert "copy_exact_source" in text
    assert "copied_source_paths" in text
    assert "source copy inventory is duplicated" in text
    assert "cp -R data" not in text
    assert "cp -R models" not in text
    for source_path in (
        "decision_executable_profit_research_projection_contract.json",
        "data/decision_executable_profit/forward/selections/",
        "data/decision_executable_profit/forward/verifications/",
        "data/decision_executable_profit/forward/settlements/",
        "data/decision_executable_profit/forward/statistics/summary.json",
    ):
        assert source_path in text
    assert "validate_research_projection_index" in text
    assert "validate_research_projection(" in text
    assert "validate_shadow_statistics_projection" in text
    assert "executable-profit public index differs from exact build" in text
    assert "executable-profit public bytes differ from build" in text
    assert "executable-profit public source SHA256 mismatch" in text
    assert "executable-profit public source bytes differ" in text
    assert "executable-profit local source is missing" in text
    assert "'latest_projection_json_sha256'" in text
    assert "'latest_projection_csv_sha256'" in text
    assert "'latest_statistics_json_sha256'" in text
    for field in (
        "executable_profit_research_available",
        "executable_profit_signal_date",
        "executable_profit_statistics_as_of_date",
        "executable_profit_projection_url",
        "executable_profit_statistics_url",
    ):
        assert text.count(field) >= 2, field


def test_profit_wiring_keeps_shared_non_cancelling_writer_lock() -> None:
    for name in (
        "run_decision_daily.yml",
        "run_auction_v3.yml",
        "verify_decision_observations.yml",
        "backfill_decision_v11_history.yml",
    ):
        text = _text(name)
        assert "group: decision-auction-main-writer" in text, name
        assert "cancel-in-progress: false" in text, name
