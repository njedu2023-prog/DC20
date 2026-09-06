from __future__ import annotations

import hashlib
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
    assert "require_regular_nonempty()" in step
    assert (
        '"${research_root}/outputs/decision/'
        'three_rank_top10_${signal_date}.json"'
    ) in step
    assert (
        '"${research_root}/outputs/decision/'
        'three_rank_top10_${signal_date}.csv"'
    ) in step
    assert (
        '"${research_root}/outputs/decision/three_rank_index.json"'
    ) in step
    assert (
        '"${research_root}/outputs/decision/'
        'research_context_dc20_${report_date}.json"'
    ) in step
    assert "Daily isolated ${label} is missing, empty, or unsafe" in step
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
    assert "validate_three_rank_contract(three_rank)" in step
    assert "validate_three_rank_index(three_rank_index)" in step
    assert "three_rank_index != expected_three_rank_index" in step
    assert "validate_research_context(" in step
    assert "research_context.get('three_rank') != three_rank" in step
    assert "!= expected_context_files" in step
    assert "Daily isolated three-rank/context binding drifted" in step
    assert "Daily copied three-rank/context binding drifted" in step
    assert "Daily executable-profit scorer modified the isolated" in step
    assert "_three_engine_projection_is_complete" in step
    assert "Daily canonical pred_D hard-range inference pool is empty" in step
    assert "Daily canonical pred_D promotion membership or order drifted" in step
    assert "Daily canonical pred_D selection/projection SHA binding drifted" in step
    assert "Daily canonical pred_D contains formal action" in step
    assert "Daily canonical pred_D contains a non-shadow action" in step
    assert "Daily canonical pred_D action boundary drifted" in step
    assert "Daily canonical pred_D order_type boundary drifted" in step
    assert "candidate_count') > 10" in step
    assert "len(set(exact_relatives)) != 13" in step
    immutable = step.split("immutable_relatives = (", 1)[1].split(
        "mutable_relatives = (", 1
    )[0]
    mutable = step.split("mutable_relatives = (", 1)[1].split(
        "exact_relatives =", 1
    )[0]
    for relative in (
        "canonical_prediction_relative",
        "three_rank_json_relative",
        "three_rank_csv_relative",
        "research_context_relative",
        "selection_json_relative",
        "selection_csv_relative",
        "projection_json_relative",
        "projection_csv_relative",
        "public_statistics_relative",
    ):
        assert relative in immutable
    for relative in (
        "three_rank_index_relative",
        "statistics_relative",
        "selection_index_relative",
        "public_index_relative",
    ):
        assert relative in mutable
    assert "'outputs_auction_v3_copied': True" in step
    assert "'canonical_prediction_only': True" in step
    assert "'isolated_three_rank_and_context_copied': True" in step
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
        "outputs/auction_v3/predictions/pred_20??????.csv",
    )
    for pattern in exact_patterns:
        assert text.count(pattern) >= 2, pattern
    assert text.count(
        "forbidden=('outputs/decision/action_plan_*.json',"
        "'outputs/decision/report_index.json')"
    ) == 2


def test_daily_main_allowlists_only_exact_current_d_prediction(
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
    paths = tmp_path / "paths.bin"
    index = tmp_path / "index.bin"
    signal_date_file = tmp_path / "daily-signal-date.txt"
    signal_date_file.write_text("20260824\n", encoding="ascii")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STAGED_PATHS", str(paths))
    monkeypatch.setenv("STAGED_INDEX", str(index))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(paths))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(index))
    monkeypatch.setenv("SIGNAL_DATE_FILE", str(signal_date_file))

    exact = "outputs/auction_v3/predictions/pred_20260824.csv"
    exact_path = tmp_path / exact
    exact_path.parent.mkdir(parents=True)
    selection_path = (
        tmp_path
        / "data/decision_executable_profit/forward/selections/"
        / "shadow_20260824.json"
    )
    projection_path = (
        tmp_path
        / "outputs/decision/executable_profit_research/"
        / "projection_20260824.json"
    )
    selection_path.parent.mkdir(parents=True)
    projection_path.parent.mkdir(parents=True)

    header = (
        "action,selected,first_layer_selected,trade_selected,"
        "guidance_only,broker_connected,market_order_allowed,order_type\n"
    )

    def write_prediction(*, action: str = "REJECT", include_row: bool = True) -> None:
        exact_path.write_text(
            header
            + (
                f"{action},0,0,0,1,0,0,LIMIT_ONLY_MANUAL\n"
                if include_row
                else ""
            ),
            encoding="utf-8",
        )

    def write_bindings(*, selected_rows: int = 1) -> None:
        digest = hashlib.sha256(exact_path.read_bytes()).hexdigest()
        selection_path.write_text(
            json.dumps(
                {
                    "signal_date": "20260824",
                    "rows": [
                        {"ts_code": f"00000{index}.SZ"}
                        for index in range(1, selected_rows + 1)
                    ],
                    "source_d_feature": {
                        "file_name": exact_path.name,
                        "file_sha256": digest,
                        "selected_row_count": selected_rows,
                    },
                }
            ),
            encoding="utf-8",
        )
        projection_path.write_text(
            json.dumps(
                {
                    "signal_date": "20260824",
                    "source_bindings": {
                        "selection": {"d_feature_file_sha256": digest}
                    },
                }
            ),
            encoding="utf-8",
        )

    def set_path(path: str, *, include_exact_index: bool = False) -> None:
        encoded = path.encode()
        paths.write_bytes(encoded + b"\0")
        entries = [
            b"100644 " + b"0" * 40 + b" 0\t" + encoded + b"\0"
        ]
        if include_exact_index and path != exact:
            entries.append(
                b"100644 "
                + b"1" * 40
                + b" 0\t"
                + exact.encode()
                + b"\0"
            )
        index.write_bytes(b"".join(entries))

    write_prediction()
    write_bindings()
    set_path(exact)
    exec(compile(compute, "<daily-exact-pred-compute>", "exec"), {})
    exec(compile(publish, "<daily-exact-pred-publish>", "exec"), {})

    write_prediction(include_row=False)
    write_bindings(selected_rows=0)
    with pytest.raises(SystemExit, match="exact pred_D candidate pool is empty"):
        exec(compile(compute, "<daily-empty-pred-compute>", "exec"), {})
    with pytest.raises(
        SystemExit, match="publish exact pred_D candidate pool is empty"
    ):
        exec(compile(publish, "<daily-empty-pred-publish>", "exec"), {})

    for forbidden in (
        "outputs/auction_v3/predictions/pred_20260823.csv",
        "outputs/auction_v3/predictions/pred_latest.csv",
        "outputs/auction_v3/metrics/backtest_latest.json",
    ):
        set_path(forbidden)
        with pytest.raises(SystemExit, match="non-allowlisted Daily paths staged"):
            exec(compile(compute, "<daily-auction-output-compute>", "exec"), {})
        with pytest.raises(
            SystemExit,
            match="non-allowlisted Daily publish paths staged",
        ):
            exec(compile(publish, "<daily-auction-output-publish>", "exec"), {})

    write_prediction(action="BUY")
    write_bindings()
    set_path(exact)
    with pytest.raises(SystemExit, match="exact pred_D action boundary drifted"):
        exec(compile(compute, "<daily-buy-action-compute>", "exec"), {})
    with pytest.raises(
        SystemExit, match="publish exact pred_D action boundary drifted"
    ):
        exec(compile(publish, "<daily-buy-action-publish>", "exec"), {})

    write_prediction()
    write_bindings()
    exact_path.write_text(exact_path.read_text() + "\n", encoding="utf-8")
    set_path(exact)
    with pytest.raises(SystemExit, match="exact pred_D SHA binding drifted"):
        exec(compile(compute, "<daily-sha-drift-compute>", "exec"), {})
    with pytest.raises(
        SystemExit, match="publish exact pred_D SHA binding drifted"
    ):
        exec(compile(publish, "<daily-sha-drift-publish>", "exec"), {})

    write_prediction()
    write_bindings()
    paths.write_bytes(exact.encode() + b"\0")
    index.write_bytes(b"")
    with pytest.raises(SystemExit, match="exact pred_D is missing or unsafe"):
        exec(compile(compute, "<daily-deleted-index-compute>", "exec"), {})
    with pytest.raises(
        SystemExit, match="publish exact pred_D is missing or unsafe"
    ):
        exec(compile(publish, "<daily-deleted-index-publish>", "exec"), {})


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


def test_verify_legacy_partial_current_truth_cannot_freeze_public_shadow_asof() -> None:
    text = _text("verify_decision_observations.yml")
    sync = _between(
        text,
        "- name: Sync all same-date truth without optional bypass",
        "- name: Retain read-only Verify gate failure evidence",
    )
    assert "id: truth_sync" in sync
    assert "('partial_success', 'post_close_truth_partial')" in sync
    assert (
        "complete = as_of != today or (status, reason) == "
        "('success', 'post_close_truth_success')"
    ) in sync
    assert "output.write(f\"complete={'true' if complete else 'false'}\\n\")" in sync
    assert sync.index('if [ "${CONTRACT_MODE}" != LEGACY_AUCTION ]; then') < sync.index('minute_evidence=')


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


def test_pages_copies_and_refetches_primary_shadow_sidecar_exact_sources() -> None:
    text = _text("deploy_dc20_pages.yml")
    for token in (
        "[dc20-shadow-pages-owned]",
        "run_primary_profit_forward_shadow.yml",
        "validate_primary_profit_forward_shadow_repository_chain",
        "outputs/decision/executable_profit_research/shadow_index.json",
        "data/decision_executable_profit/forward/selections/primary_mixed_index.json",
        "models/decision_primary_profit_forward_shadow_bridge_contract.json",
        "t_verification_{signal_date}.json",
        "settlement_{signal_date}.json",
        "shadow_t1",
        "latest_mixed_projection_sha256",
        "Pages Shadow sidecar is not bound to same-D P1 bytes",
        "Pages exact Shadow source copy drifted",
        "public primary Shadow sidecar is not exact-D/SHA bound",
    ):
        assert token in text
    assert "cp -R data" not in text
    assert "cp -R models" not in text


def test_verify_p1_mode_never_runs_legacy_projector_or_changes_primary_projection() -> None:
    text = _text("verify_decision_observations.yml")
    step = _between(
        text,
        "- name: Settle immutable executable-profit Shadow truth and project exact as-of",
        "- name: Build exact allowlisted Verify candidate patch",
    )
    assert "MIXED_INDEX_SCHEMA" in step
    assert "project_primary_profit_forward_shadow_state" in step
    assert "validate_primary_profit_forward_shadow_repository_chain" in step
    assert "primary_mixed_index.json" in step
    assert "shadow_index.json" in step
    assert "shadow_state_" in step
    assert "P1 primary projection/index bytes changed during Verify" in step
    assert "if primary_mode:" in step
    legacy_branch = step.split("if primary_mode:", 1)[1]
    assert "project_decision_executable_profit_research.py" not in legacy_branch.split("else", 1)[0]


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
