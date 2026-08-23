from __future__ import annotations

import csv
import hashlib
import io
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
PAGES_HANDOFF_WRITERS = WRITERS + ("migrate_decision_runtime.yml",)
NUMERIC_WORKFLOWS = PAGES_HANDOFF_WRITERS + (
    "diagnose_decision_fingerprint.yml",
    "test_decision_core.yml",
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
        "data/market/raw/20[0-9][0-9]/20[0-9][0-9][0-9][0-9][0-9][0-9]/stk_auction_o.csv",
        "data/market/raw/20[0-9][0-9]/20[0-9][0-9][0-9][0-9][0-9][0-9]/stk_auction_o.meta.json",
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
        "outputs/decision/decision_report_20??????.md",
        "outputs/decision/eval_20??????.json",
        "outputs/decision/forward_observation_protocol_v12.json",
        "outputs/decision/research_context_20??????.json",
        "data/decision/**",
        "docs/reports/*.md",
    ),
    "verify_decision_observations.yml": (
        "outputs/auction_v3/verification/observation_*.csv",
        "outputs/auction_v3/verification/manual_actual_latest.csv",
        "outputs/auction_v3/metrics/observation_cumulative_latest.json",
        "outputs/auction_v3/metrics/manual_actual_cumulative_latest.json",
        "data/market/trade_cal_sse.csv",
        "data/market/raw/20[0-9][0-9]/20[0-9][0-9][0-9][0-9][0-9][0-9]/stk_auction_o.csv",
        "data/market/raw/20[0-9][0-9]/20[0-9][0-9][0-9][0-9][0-9][0-9]/stk_auction_o.meta.json",
    ),
    "backfill_decision_v11_history.yml": (
        "data/auction_v3/history/tplus1_open_0930_v1/training_*.csv",
        "data/auction_v3/history/tplus1_open_0930_v1/manifest_latest.json",
        "data/market/trade_cal_sse.csv",
    ),
}


def _text(name: str) -> str:
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def _with_exact_retrospective_migration(action: dict[str, object]) -> dict[str, object]:
    migrated = json.loads(json.dumps(action))
    migrated.update(
        {
            "publication_timing": "RETROSPECTIVE",
            "live_delivery_met": False,
            "execution_or_fill_claimed": False,
            "migration": {
                "schema_version": "decision_runtime_migration_v1",
                "source": "frozen_canonical_replay",
                "timing": "RETROSPECTIVE",
                "base_sha": "a" * 40,
                "signal_date": migrated["signal_date"],
                "report_date": migrated["report_date"],
                "exec_date": migrated["exec_date"],
                "exit_date": migrated["exit_date"],
                "live_delivery_met": False,
                "execution_created": False,
                "fill_created": False,
                "broker_execution_claimed": False,
                "observation_truth_is_not_a_fill_claim": True,
            },
        }
    )
    return migrated


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
    if name == "backfill_decision_v11_history.yml":
        assert "expected_files = {'base_sha.txt', 'backfill-receipt.json', 'candidate.patch', 'candidate.patch.sha256'}" in publish, name
    else:
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
    assert "comparison_profile=\\n" in prerequisite_step
    assert "signal_date=\\n" in prerequisite_step
    assert "report_date=\\n" in prerequisite_step
    assert daily.index("Require persisted Auction action for real Daily publication") < daily.index(
        "Rebuild exact-base frozen Auction runtime"
    )
    assert "PERSISTED_ACTION_SEMANTIC_SHA256: ${{ steps.persisted_action.outputs.semantic_sha256 }}" in replay_step
    assert "PERSISTED_ACTION_COMPARISON_PROFILE: ${{ steps.persisted_action.outputs.comparison_profile }}" in replay_step
    assert "PERSISTED_ACTION_SIGNAL_DATE: ${{ steps.persisted_action.outputs.signal_date }}" in replay_step
    assert "PERSISTED_ACTION_REPORT_DATE: ${{ steps.persisted_action.outputs.report_date }}" in replay_step
    assert "PERSISTED_ACTION_VALIDATED: ${{ steps.persisted_action.outputs.validated }}" in replay_step
    assert "datetime.strptime(signal_date, '%Y%m%d')" in prerequisite_step
    assert "f'signal_date={signal_date}\\n'" in prerequisite_step
    assert "datetime.strptime(report_date, '%Y%m%d')" in prerequisite_step
    assert "f'report_date={report_date}\\n'" in prerequisite_step
    assert '--signal-date "${PERSISTED_ACTION_SIGNAL_DATE}"' in replay_step
    assert '--report-date "${PERSISTED_ACTION_REPORT_DATE}"' in replay_step
    assert '--signal-date "${TRADE_DATE}"' not in replay_step
    assert "Daily replayed action signal_date differs from persisted Auction action" in replay_step
    assert "Daily replayed action report_date differs from persisted Auction action" in replay_step
    assert "Daily dry-run unexpectedly received persisted action dates" in replay_step
    assert "canonical_json_bytes(semantic_action)" in prerequisite_step
    assert "canonical_json_bytes(semantic_action)" in replay_step
    assert "action_plan_semantic_comparison_profile_v3" in prerequisite_step
    assert "action_plan_semantic_projection_v3(" in prerequisite_step
    assert "action_plan_semantic_projection_v3(" in replay_step
    assert "comparison_profile=action_plan_semantic_comparison_profile_v3(action)" in replay_step
    assert "daily_action_semantic_comparison_v3" in replay_step
    assert "persisted_semantic_sha256" in replay_step
    assert "replayed_semantic_sha256" in replay_step
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
    assert (
        "python scripts/run_deterministic_numeric.py "
        "scripts/replay_frozen_canonical_v2.py"
    ) in replay_step
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
        "comparison_profile=\nsignal_date=\nreport_date=\n"
    )

    replay_blocks = _embedded_python_blocks_between(
        daily,
        "- name: Rebuild exact-base frozen Auction runtime before live source mutation",
        "- name: Resolve Daily exchange write eligibility",
    )
    replay_source = replay_blocks[1]
    action_path = tmp_path / "outputs/decision/action_plan_latest.json"
    action_path.parent.mkdir(parents=True)
    action_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-08-20T14:00:00+00:00",
                "schema_version": "decision_action_plan_v12_top10_trade_selector",
                "ordinary_prospective_semantics": {"must_remain": True},
            }
        ),
        encoding="utf-8",
    )
    replay_output = tmp_path / "replay-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(replay_output))
    monkeypatch.setenv("PERSISTED_ACTION_VALIDATED", "false")
    monkeypatch.setenv("PERSISTED_ACTION_SEMANTIC_SHA256", "")
    monkeypatch.setenv("PERSISTED_ACTION_COMPARISON_PROFILE", "")
    monkeypatch.setenv("PERSISTED_ACTION_SIGNAL_DATE", "")
    monkeypatch.setenv("PERSISTED_ACTION_REPORT_DATE", "")
    monkeypatch.setattr(
        model_freeze,
        "load_model_freeze",
        lambda *_args, **_kwargs: {"active": True},
    )
    monkeypatch.setattr(model_freeze, "model_freeze_active", lambda _manifest: True)
    monkeypatch.setattr(
        model_freeze,
        "validate_action_plan_artifact",
        lambda *_args, **_kwargs: {"validated": True, "enforced": True},
    )
    exec(compile(replay_source, "<daily-replay-action-dry-run>", "exec"), {})
    assert re.fullmatch(
        r"sha256=[0-9a-f]{64}\n",
        replay_output.read_text(encoding="utf-8"),
    )

    monkeypatch.setenv("PERSISTED_ACTION_SIGNAL_DATE", "20260814")
    with pytest.raises(
        SystemExit,
        match="Daily dry-run unexpectedly received persisted action dates",
    ):
        exec(compile(replay_source, "<daily-replay-action-dry-run-signal>", "exec"), {})


@pytest.mark.parametrize(
    ("field", "invalid_action_date"),
    (
        ("signal_date", None),
        ("signal_date", 20260814),
        ("signal_date", ""),
        ("signal_date", "20260814 "),
        ("signal_date", "20260230"),
        ("signal_date", "2026-08-14"),
        ("report_date", None),
        ("report_date", 20260817),
        ("report_date", ""),
        ("report_date", "20260817 "),
        ("report_date", "20260230"),
        ("report_date", "20260814"),
    ),
)
def test_daily_real_publication_rejects_invalid_persisted_action_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid_action_date: object,
) -> None:
    from top10decision.decision import model_freeze

    source = _embedded_python_after(
        _text("run_decision_daily.yml"),
        "- name: Require persisted Auction action for real Daily publication",
    )
    action = json.loads(
        (ROOT / "outputs/decision/action_plan_latest.json").read_text(
            encoding="utf-8"
        )
    )
    for field in (
        "publication_timing",
        "live_delivery_met",
        "execution_or_fill_claimed",
        "migration",
    ):
        action.pop(field, None)
    action["generated_at_utc"] = "2026-08-20T14:00:00+00:00"
    action[field] = invalid_action_date
    action_path = tmp_path / "outputs/decision/action_plan_latest.json"
    action_path.parent.mkdir(parents=True)
    action_path.write_text(json.dumps(action), encoding="utf-8")
    output_path = tmp_path / "github-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("PUBLISH", "true")
    monkeypatch.setattr(
        model_freeze,
        "load_model_freeze",
        lambda *_args, **_kwargs: {"active": True},
    )
    monkeypatch.setattr(model_freeze, "model_freeze_active", lambda _manifest: True)
    monkeypatch.setattr(
        model_freeze,
        "validate_action_plan_artifact",
        lambda *_args, **_kwargs: {"validated": True, "enforced": True},
    )

    with pytest.raises(SystemExit) as exc_info:
        exec(compile(source, "<daily-persisted-action-invalid-signal>", "exec"), {})
    assert exc_info.value.code == 1
    assert not output_path.exists()


def test_daily_bootstrap_candidate_archive_is_exact_remote_history() -> None:
    archive = ROOT / "data/pred/archive/pred_source_20260814.csv"
    assert archive.is_file() and not archive.is_symlink()
    raw = archive.read_bytes()
    assert len(raw) == 38_507
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == (
        "eda0f4008756b1c72ed0a9e03b8621b1a7790512809c42f6cf0da04d1cd0e041"
    )
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    rows = list(reader)
    assert reader.fieldnames is not None
    assert len(reader.fieldnames) == len(set(reader.fieldnames)) == 65
    assert len(rows) == 51
    assert all(None not in row and len(row) == 65 for row in rows)
    assert {row["trade_date"] for row in rows} == {"20260814"}
    assert {row["verify_date"] for row in rows} == {"20260817"}
    assert sorted(int(row["rank"]) for row in rows) == list(range(1, 52))
    codes = [row["ts_code"] for row in rows]
    assert len(codes) == len(set(codes)) == 51
    assert all(re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code) for code in codes)

    action = json.loads(
        (ROOT / "outputs/decision/action_plan_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        action["signal_date"],
        action["exec_date"],
        action["exit_date"],
    ) == ("20260814", "20260817", "20260818")
    assert {row["ts_code"] for row in action["candidates"]} == set(codes)


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
    action = _with_exact_retrospective_migration(
        json.loads(
            (ROOT / "outputs/decision/action_plan_latest.json").read_text(
                encoding="utf-8"
            )
        )
    )
    action["generated_at_utc"] = "2026-08-20T14:00:00+00:00"
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
    assert (
        persisted["comparison_profile"]
        == "retrospective_frozen_replay_dynamic_evidence_v3"
    )
    assert persisted["signal_date"] == action["signal_date"]
    assert persisted["report_date"] == action["report_date"]

    raw_persisted_sha = hashlib.sha256(action_path.read_bytes()).hexdigest()
    for field in (
        "publication_timing",
        "live_delivery_met",
        "execution_or_fill_claimed",
        "migration",
    ):
        action.pop(field)
    action["generated_at_utc"] = "2026-08-20T14:00:01+00:00"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    assert hashlib.sha256(action_path.read_bytes()).hexdigest() != raw_persisted_sha
    monkeypatch.setenv("GITHUB_OUTPUT", str(replay_output))
    monkeypatch.setenv("PERSISTED_ACTION_VALIDATED", "true")
    monkeypatch.setenv(
        "PERSISTED_ACTION_SEMANTIC_SHA256",
        persisted["semantic_sha256"],
    )
    monkeypatch.setenv(
        "PERSISTED_ACTION_COMPARISON_PROFILE",
        persisted["comparison_profile"],
    )
    monkeypatch.setenv("PERSISTED_ACTION_SIGNAL_DATE", persisted["signal_date"])
    monkeypatch.setenv("PERSISTED_ACTION_REPORT_DATE", persisted["report_date"])
    exec(compile(replay_validation_source, "<daily-replay-action-positive>", "exec"), {})
    assert re.fullmatch(
        r"sha256=[0-9a-f]{64}\n",
        replay_output.read_text(encoding="utf-8"),
    )

    action["signal_date"] = "20260817"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(
        SystemExit,
        match="signal_date differs from persisted Auction action",
    ):
        exec(compile(replay_validation_source, "<daily-replay-action-signal-drift>", "exec"), {})
    action["signal_date"] = persisted["signal_date"]

    action["report_date"] = "20260818"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(
        SystemExit,
        match="report_date differs from persisted Auction action",
    ):
        exec(compile(replay_validation_source, "<daily-replay-action-report-drift>", "exec"), {})
    action["report_date"] = persisted["report_date"]

    action["unknown_business_semantics"] = "drift"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(SystemExit, match="semantics differ from persisted Auction action"):
        exec(compile(replay_validation_source, "<daily-replay-action-unknown-drift>", "exec"), {})
    action.pop("unknown_business_semantics")

    action["status_code"] = "ACTIONABLE_BUY"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with pytest.raises(SystemExit, match="semantic evidence is invalid"):
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


def test_action_semantic_projection_excludes_only_exact_migration_truth() -> None:
    from top10decision.decision.canonical_fingerprint import canonical_json_bytes
    from top10decision.decision.action_plan import (
        action_plan_semantic_projection_v1,
    )

    native = {
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "signal_date": "20260814",
        "report_date": "20260817",
        "exec_date": "20260817",
        "exit_date": "20260818",
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "formal_buy_count": 0,
        "guidance_only": True,
        "broker_connected": False,
        "order_execution": "manual_only",
        "candidates": [{"action": "REJECT", "target_weight": 0.0}],
        "stage_watchlist": [{"action": "SHADOW_ONLY", "target_weight": 0}],
        "unknown_business_semantics": {"must_remain": True},
    }
    migrated = json.loads(json.dumps(native))
    migrated.update(
        {
            "publication_timing": "RETROSPECTIVE",
            "live_delivery_met": False,
            "execution_or_fill_claimed": False,
            "migration": {
                "schema_version": "decision_runtime_migration_v1",
                "source": "frozen_canonical_replay",
                "timing": "RETROSPECTIVE",
                "base_sha": "a" * 40,
                "signal_date": "20260814",
                "report_date": "20260817",
                "exec_date": "20260817",
                "exit_date": "20260818",
                "live_delivery_met": False,
                "execution_created": False,
                "fill_created": False,
                "broker_execution_claimed": False,
                "observation_truth_is_not_a_fill_claim": True,
            },
        }
    )

    assert action_plan_semantic_projection_v1(native) == native
    assert action_plan_semantic_projection_v1(native) is not native
    assert action_plan_semantic_projection_v1(migrated) == native
    assert "migration" in migrated
    without_unknown = json.loads(json.dumps(native))
    without_unknown.pop("unknown_business_semantics")
    assert hashlib.sha256(canonical_json_bytes(native)).digest() != hashlib.sha256(
        canonical_json_bytes(without_unknown)
    ).digest()

    invalid_payloads: list[dict[str, object]] = []
    partial = json.loads(json.dumps(migrated))
    partial.pop("migration")
    invalid_payloads.append(partial)
    for path, value in (
        (("publication_timing",), "PROSPECTIVE_LIVE"),
        (("live_delivery_met",), True),
        (("execution_or_fill_claimed",), True),
        (("formal_buy_count",), 1),
        (("migration", "schema_version"), "decision_runtime_migration_v2"),
        (("migration", "base_sha"), "A" * 40),
        (("migration", "report_date"), "20260818"),
        (("migration", "unexpected"), False),
        (("exit_date",), "20260230"),
        (("exec_date",), "20260819"),
        (("candidates", 0, "action"), "BUY"),
        (("stage_watchlist", 0, "target_weight"), 0.01),
    ):
        candidate = json.loads(json.dumps(migrated))
        cursor: object = candidate
        for part in path[:-1]:
            cursor = cursor[part]  # type: ignore[index]
        cursor[path[-1]] = value  # type: ignore[index]
        invalid_payloads.append(candidate)

    for candidate in invalid_payloads:
        with pytest.raises(ValueError):
            action_plan_semantic_projection_v1(candidate)  # type: ignore[arg-type]


def test_action_semantic_projection_v2_is_strict_and_core_preserving() -> None:
    from top10decision.decision.action_plan import (
        action_plan_semantic_comparison_profile_v2,
        action_plan_semantic_projection_v2,
    )

    persisted = _with_exact_retrospective_migration(
        json.loads(
            (ROOT / "outputs/decision/action_plan_latest.json").read_text(
                encoding="utf-8"
            )
        )
    )
    persisted.pop("generated_at_utc")
    profile = action_plan_semantic_comparison_profile_v2(persisted)
    assert profile == "retrospective_frozen_replay_dynamic_evidence_v2"
    expected = action_plan_semantic_projection_v2(
        persisted,
        comparison_profile=profile,
    )

    replay = json.loads(json.dumps(persisted))
    for field in (
        "publication_timing",
        "live_delivery_met",
        "execution_or_fill_claimed",
        "migration",
    ):
        replay.pop(field)
    native_profile = action_plan_semantic_comparison_profile_v2(replay)
    assert native_profile == "native_no_trade_dynamic_evidence_v2"
    native_expected = action_plan_semantic_projection_v2(
        replay,
        comparison_profile=native_profile,
    )
    assert native_expected == expected

    # Both reviewed backfill schemas and changed truth values normalize only
    # under the retrospective profile authorized by the persisted V1 proof.
    changed_evidence = json.loads(json.dumps(replay))
    backfill = changed_evidence["model"]["data_coverage"]["backfill_manifest"]
    backfill.update(
        {
            "schema_version": "decision_v11_history_manifest_v2",
            "calendar_bytes": 123,
            "calendar_bytes_sha256": "a" * 64,
            "calendar_file": "data/market/trade_cal_sse.csv",
            "calendar_open_dates": 300,
            "evaluated_at_utc": "2026-08-22T00:00:00+00:00",
            "max_missing_dates": 0,
            "output_bytes": 456,
            "output_bytes_sha256": "b" * 64,
            "output_canonical_sha256": "c" * 64,
            "target_window_signal_dates": list(backfill["target_signal_dates"]),
        }
    )
    changed_evidence["observation_statistics"]["observation_rows"] += 1
    changed_evidence["model"]["truth_ledgers"]["market_open_observation"][
        "metrics"
    ] = json.loads(json.dumps(changed_evidence["observation_statistics"]))
    changed_evidence["model"]["truth_ledgers"]["formal_limit_proxy"][
        "metrics"
    ]["verified_trades"] += 1
    changed_evidence["observation_validation"]["final_rows"] += 1
    changed_evidence["stage_watchlist"][0]["truth_generated_at_utc"] = (
        "2026-08-22T00:00:00+00:00"
    )
    assert (
        action_plan_semantic_projection_v2(
            changed_evidence,
            comparison_profile=profile,
        )
        == expected
    )
    assert (
        action_plan_semantic_projection_v2(
            changed_evidence,
            comparison_profile=native_profile,
        )
        == native_expected
    )

    malformed: list[dict[str, object]] = []
    partial_backfill = json.loads(json.dumps(replay))
    partial_backfill["model"]["data_coverage"]["backfill_manifest"].pop(
        "output_sha256"
    )
    malformed.append(partial_backfill)
    unknown_backfill = json.loads(json.dumps(replay))
    unknown_backfill["model"]["data_coverage"]["backfill_manifest"][
        "unknown_evidence"
    ] = True
    malformed.append(unknown_backfill)
    unknown_statistics = json.loads(json.dumps(replay))
    unknown_statistics["observation_statistics"]["unknown_evidence"] = True
    malformed.append(unknown_statistics)
    unknown_ledger = json.loads(json.dumps(replay))
    unknown_ledger["model"]["truth_ledgers"]["manual_actual"]["metrics"][
        "unknown_evidence"
    ] = True
    malformed.append(unknown_ledger)
    partial_stage = json.loads(json.dumps(replay))
    partial_stage["stage_watchlist"][0].pop("truth_generated_at_utc")
    malformed.append(partial_stage)
    for candidate in malformed:
        with pytest.raises(ValueError):
            action_plan_semantic_projection_v2(
                candidate,  # type: ignore[arg-type]
                comparison_profile=profile,
            )

    # Every field outside the four reviewed evidence containers and exact
    # stage overlay remains in the digest, including all unknown fields.
    core_mutations = (
        (("candidates", 0, "trade_score"), 999.0),
        (("model", "selection_policy", "unknown_core"), True),
        (("backtest", "unknown_core"), True),
        (("signal_date",), "20260813"),
        (("execution_contract", "unknown_core"), True),
        (("market_close_comparison", "unknown_core"), True),
        (("unknown_top_level",), True),
        (("model", "unknown_model_field"), True),
        (("stage_watchlist", 0, "unknown_watch_field"), True),
        (("candidates", 0, "trade_selected"), 1),
        (("candidates", 0, "market_order_allowed"), 1),
        (("candidates", 0, "risk_gate_pass"), 1),
        (("candidates", 0, "trade_selector_promoted"), 1),
        (("candidates", 0, "order_type"), "MARKET"),
    )
    for path, value in core_mutations:
        candidate = json.loads(json.dumps(replay))
        cursor: object = candidate
        for part in path[:-1]:
            cursor = cursor[part]  # type: ignore[index]
        cursor[path[-1]] = value  # type: ignore[index]
        assert action_plan_semantic_comparison_profile_v2(candidate) == native_profile
        assert (
            action_plan_semantic_projection_v2(
                candidate,
                comparison_profile=profile,
            )
            != expected
        )

    for collection in ("candidates", "stage_watchlist"):
        candidate = json.loads(json.dumps(replay))
        current_action = candidate[collection][0]["action"]
        candidate[collection][0]["action"] = (
            "REJECT" if current_action == "SHADOW_ONLY" else "SHADOW_ONLY"
        )
        assert (
            action_plan_semantic_projection_v2(
                candidate,
                comparison_profile=profile,
            )
            != expected
        )

    unsafe_mutations = (
        (("status_code",), "ACTIONABLE_BUY"),
        (("formal_buy_count",), 1),
        (("guidance_only",), False),
        (("broker_connected",), True),
        (("order_execution",), "broker_api"),
        (("candidates", 0, "action"), "BUY"),
        (("candidates", 0, "target_weight"), 0.01),
        (("stage_watchlist", 0, "action"), "BUY"),
        (("stage_watchlist", 0, "target_weight"), 0.01),
    )
    for path, value in unsafe_mutations:
        candidate = json.loads(json.dumps(replay))
        cursor: object = candidate
        for part in path[:-1]:
            cursor = cursor[part]  # type: ignore[index]
        cursor[path[-1]] = value  # type: ignore[index]
        assert action_plan_semantic_comparison_profile_v2(candidate) == "full_action_v1"
        with pytest.raises(ValueError, match="non-executing"):
            action_plan_semantic_projection_v2(
                candidate,
                comparison_profile=profile,
            )

    full_reference = action_plan_semantic_projection_v2(
        replay,
        comparison_profile="full_action_v1",
    )
    full_truth_change = json.loads(json.dumps(replay))
    full_truth_change["observation_statistics"]["observation_rows"] += 1
    assert (
        action_plan_semantic_projection_v2(
            full_truth_change,
            comparison_profile="full_action_v1",
        )
        != full_reference
    )
    with pytest.raises(ValueError, match="cannot use the full-action profile"):
        action_plan_semantic_projection_v2(
            persisted,
            comparison_profile="full_action_v1",
        )
    with pytest.raises(ValueError, match="comparison profile"):
        action_plan_semantic_projection_v2(
            replay,
            comparison_profile="unknown",
        )


def test_action_semantic_projection_v3_normalizes_only_valid_t_close_maturity() -> None:
    from top10decision.decision.action_plan import (
        action_plan_semantic_comparison_profile_v2,
        action_plan_semantic_comparison_profile_v3,
        action_plan_semantic_projection_v2,
        action_plan_semantic_projection_v3,
    )

    persisted = json.loads(
        (ROOT / "outputs/decision/action_plan_latest.json").read_text(
            encoding="utf-8"
        )
    )
    persisted.pop("generated_at_utc")
    profile = action_plan_semantic_comparison_profile_v3(persisted)
    assert profile == "retrospective_frozen_replay_dynamic_evidence_v3"
    expected = action_plan_semantic_projection_v3(
        persisted,
        comparison_profile=profile,
    )
    assert expected["market_close_comparison"]["t"] == {
        "evidence_class": "post_decision_t_close_v3",
        "trade_date": persisted["exec_date"],
    }

    native = json.loads(json.dumps(persisted))
    for field in (
        "publication_timing",
        "live_delivery_met",
        "execution_or_fill_claimed",
        "migration",
    ):
        native.pop(field)
    assert (
        action_plan_semantic_comparison_profile_v3(native)
        == "native_no_trade_dynamic_evidence_v3"
    )

    mature = json.loads(json.dumps(native))
    d_stock_count = mature["market_close_comparison"]["d"]["stock_count"]
    mature["market_close_comparison"]["t"] = {
        "trade_date": mature["exec_date"],
        "available": True,
        "status": "FINAL_CLOSE",
        "scope": "all_a_share_daily_close",
        "stock_count": d_stock_count + 1,
        "return_coverage": 1.0,
        "up_count": d_stock_count + 1,
        "down_count": 0,
        "flat_count": 0,
        "limit_up_count": 2,
        "classified_limit_up_count": 2,
        "industry_top10": [
            {
                "rank": 1,
                "industry": "化学制品",
                "limit_up_count": 2,
                "share": 1.0,
            }
        ],
        "industry_counts": {"化学制品": 2},
        "raw_close_available": True,
        "coverage_against_d": (d_stock_count + 1) / d_stock_count,
        "maturity_status": "FINAL_T_CLOSE",
    }
    mature_projection = action_plan_semantic_projection_v3(
        mature,
        comparison_profile=profile,
    )
    assert mature["market_close_comparison"]["t"]["coverage_against_d"] > 1.0
    assert mature_projection == expected

    v2_profile = action_plan_semantic_comparison_profile_v2(native)
    assert action_plan_semantic_projection_v2(
        mature,
        comparison_profile=v2_profile,
    ) != action_plan_semantic_projection_v2(
        native,
        comparison_profile=v2_profile,
    )

    malformed: list[dict[str, object]] = []
    unknown_t = json.loads(json.dumps(mature))
    unknown_t["market_close_comparison"]["t"]["unknown"] = True
    malformed.append(unknown_t)
    bad_coverage = json.loads(json.dumps(mature))
    bad_coverage["market_close_comparison"]["t"]["coverage_against_d"] = 0.5
    malformed.append(bad_coverage)
    bad_industry = json.loads(json.dumps(mature))
    bad_industry["market_close_comparison"]["t"]["industry_counts"] = {
        "化学制品": 1
    }
    malformed.append(bad_industry)
    boolean_rank = json.loads(json.dumps(mature))
    boolean_rank["market_close_comparison"]["t"]["industry_top10"][0]["rank"] = True
    malformed.append(boolean_rank)
    boolean_count = json.loads(json.dumps(mature))
    boolean_count["market_close_comparison"]["t"]["industry_top10"][0][
        "limit_up_count"
    ] = True
    malformed.append(boolean_count)
    integer_share = json.loads(json.dumps(mature))
    integer_share["market_close_comparison"]["t"]["industry_top10"][0]["share"] = 1
    malformed.append(integer_share)
    integer_return_coverage = json.loads(json.dumps(mature))
    integer_return_coverage["market_close_comparison"]["t"]["return_coverage"] = 1
    malformed.append(integer_return_coverage)
    integer_d_coverage = json.loads(json.dumps(mature))
    integer_d_coverage["market_close_comparison"]["t"]["coverage_against_d"] = 1
    malformed.append(integer_d_coverage)
    bad_state = json.loads(json.dumps(mature))
    bad_state["market_close_comparison"]["t"]["available"] = False
    malformed.append(bad_state)
    bad_binding = json.loads(json.dumps(mature))
    bad_binding["market_close_comparison"]["t"]["trade_date"] = "20260818"
    malformed.append(bad_binding)
    unknown_parent = json.loads(json.dumps(mature))
    unknown_parent["market_close_comparison"]["unknown"] = True
    malformed.append(unknown_parent)
    unknown_d = json.loads(json.dumps(mature))
    unknown_d["market_close_comparison"]["d"]["unknown"] = True
    malformed.append(unknown_d)
    bad_signal_date = json.loads(json.dumps(mature))
    bad_signal_date["signal_date"] = "not-a-date"
    bad_signal_date["market_close_comparison"]["d"]["trade_date"] = "not-a-date"
    malformed.append(bad_signal_date)
    bad_exit_date = json.loads(json.dumps(mature))
    bad_exit_date["exit_date"] = bad_exit_date["exec_date"]
    malformed.append(bad_exit_date)
    partial_waiting = json.loads(json.dumps(native))
    partial_stock_count = 100
    partial_waiting["market_close_comparison"]["t"].update(
        {
            "status": "INCOMPLETE_DAILY_CLOSE",
            "stock_count": partial_stock_count,
            "return_coverage": 0.5,
            "up_count": 50,
            "coverage_against_d": partial_stock_count / d_stock_count,
        }
    )
    assert action_plan_semantic_projection_v3(
        partial_waiting,
        comparison_profile=profile,
    ) == expected

    incomplete_t = json.loads(json.dumps(mature))
    incomplete_stock_count = max(2, int(d_stock_count * 0.8))
    incomplete_t["market_close_comparison"]["t"].update(
        {
            "available": False,
            "raw_close_available": True,
            "maturity_status": "INCOMPLETE_T_CLOSE",
            "status": "FINAL_CLOSE",
            "stock_count": incomplete_stock_count,
            "return_coverage": 1.0,
            "up_count": incomplete_stock_count,
            "coverage_against_d": incomplete_stock_count / d_stock_count,
        }
    )
    assert action_plan_semantic_projection_v3(
        incomplete_t,
        comparison_profile=profile,
    ) == expected
    invalid_incomplete = json.loads(json.dumps(incomplete_t))
    invalid_incomplete["market_close_comparison"]["t"]["available"] = True
    malformed.append(invalid_incomplete)
    invalid_partial = json.loads(json.dumps(partial_waiting))
    invalid_partial["market_close_comparison"]["t"]["raw_close_available"] = True
    malformed.append(invalid_partial)
    waiting_integer_return = json.loads(json.dumps(native))
    waiting_integer_return["market_close_comparison"]["t"]["return_coverage"] = 0
    malformed.append(waiting_integer_return)
    waiting_integer_d_coverage = json.loads(json.dumps(native))
    waiting_integer_d_coverage["market_close_comparison"]["t"]["coverage_against_d"] = 0
    malformed.append(waiting_integer_d_coverage)
    for candidate in malformed:
        with pytest.raises(ValueError):
            action_plan_semantic_projection_v3(
                candidate,  # type: ignore[arg-type]
                comparison_profile=profile,
            )

    core_change = json.loads(json.dumps(native))
    core_change["candidates"][0]["trade_score"] = 999.0
    assert action_plan_semantic_projection_v3(
        core_change,
        comparison_profile=profile,
    ) != expected

    unsafe = json.loads(json.dumps(native))
    unsafe["status_code"] = "ACTIONABLE_BUY"
    assert action_plan_semantic_comparison_profile_v3(unsafe) == "full_action_v1"
    full_waiting = action_plan_semantic_projection_v3(
        unsafe,
        comparison_profile="full_action_v1",
    )
    unsafe["market_close_comparison"]["t"] = mature["market_close_comparison"]["t"]
    assert action_plan_semantic_projection_v3(
        unsafe,
        comparison_profile="full_action_v1",
    ) != full_waiting


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


def test_backfill_uses_owner_scoped_frozen_runtime_gates() -> None:
    backfill = _text("backfill_decision_v11_history.yml")
    gates = backfill.split(
        "- name: Validate owner-scoped Backfill artifacts before candidate creation",
        1,
    )[1]
    assert "python scripts/validate_io_contract.py" not in gates
    assert "docs/signals/top10_latest.csv" not in gates
    assert "python scripts/run_v2.py" not in gates
    assert "python scripts/run_auction_v3.py" not in gates
    assert "python scripts/publish_decision_action.py" not in gates
    assert "python scripts/validate_backfill_artifacts.py" in gates
    assert "Backfill modified non-owner paths" in gates
    assert gates.index("python scripts/validate_backfill_artifacts.py") < gates.index(
        "test_decision_contract.py"
    )
    assert 'git worktree add --detach "${runtime_root}"' in gates
    assert 'git -C "${runtime_root}" apply --index --binary' in gates
    assert 'python "${runtime_root}/scripts/run_deterministic_numeric.py"' in gates
    assert '"${runtime_root}/scripts/replay_frozen_canonical_v2.py"' in gates
    assert "backfill-frozen-replay.json" in gates
    assert "backfill_frozen_replay" in gates
    assert '"${runtime_root}/scripts/validate_decision_model_freeze.py"' in gates
    assert '--root "${runtime_root}" --runtime' in gates
    assert '--root "${runtime_root}" --runtime --force-inactive' in gates
    assert "TARGET_INDEPENDENT_OOS_DATES" in gates
    assert "type(actual) is not int" in gates
    assert "outputs/decision" not in backfill.split(
        "- name: Build one exact allowlisted Backfill candidate patch", 1
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    assert "outputs/auction_v3" not in ALLOWLISTS["backfill_decision_v11_history.yml"]
    assert "backfill-receipt.json" in gates
    assert "expected_dirty_paths" in gates
    publish = backfill.split("\n  publish:", 1)[1]
    assert "validate_backfill_artifacts" in publish
    assert "Backfill publisher patch exceeds the validated receipt inventory" in publish
    assert publish.index("Setup pinned Python for publisher revalidation") < publish.index(
        "validate_backfill_artifacts"
    )
    assert publish.index("--require-hashes -r requirements.lock") < publish.index(
        "validate_backfill_artifacts"
    )
    assert publish.index("validate_backfill_artifacts") < publish.index(
        "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}"
    )


def test_backfill_owner_guard_rejects_every_unreceipted_dirty_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _text("backfill_decision_v11_history.yml")
    source = _embedded_python_after(
        text,
        "- name: Validate owner-scoped Backfill artifacts before candidate creation",
    )
    validation = tmp_path / "validation.json"
    tracked = tmp_path / "tracked.bin"
    untracked = tmp_path / "untracked.bin"
    expected = [
        "data/market/trade_cal_sse.csv",
        "data/auction_v3/history/tplus1_open_0930_v1/manifest_latest.json",
        "data/auction_v3/history/tplus1_open_0930_v1/training_20260805_20260805.csv",
    ]
    validation.write_text(
        json.dumps(
            {
                "validated": True,
                "status": "produced",
                "live_independent_oos_capacity": 500,
                "expected_dirty_paths": expected,
            }
        ),
        encoding="utf-8",
    )
    tracked.write_bytes(b"\0".join(path.encode() for path in expected) + b"\0")
    untracked.write_bytes(b"")
    monkeypatch.setenv("VALIDATION_REPORT", str(validation))
    monkeypatch.setenv("TRACKED_DIRTY", str(tracked))
    monkeypatch.setenv("UNTRACKED_DIRTY", str(untracked))
    exec(compile(source, "<backfill-owner-guard-valid>", "exec"), {})

    for extra in (
        "data/auction_v3/history/tplus1_open_0930_v1/unbound.json",
        "data/auction_v3/history/tplus1_open_0930_v1/nested/escape.csv",
        "data/auction_v3/history/another_policy/training_escape.csv",
        "docs/signals/top10_latest.csv",
    ):
        untracked.write_bytes(extra.encode() + b"\0")
        with pytest.raises(SystemExit, match="modified non-owner paths"):
            exec(compile(source, "<backfill-owner-guard-extra>", "exec"), {})


def test_backfill_candidate_and_publisher_reject_nested_rename_and_nonregular_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backfill = _text("backfill_decision_v11_history.yml")
    assert backfill.count("git diff --cached --no-renames --name-only -z") == 2
    assert backfill.count("git ls-files --stage -z") == 2
    candidate_step = backfill.split(
        "- name: Build one exact allowlisted Backfill candidate patch",
        1,
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    add_lines = [
        line.strip()
        for line in candidate_step.splitlines()
        if line.strip().startswith("git add -A --")
    ]
    assert add_lines == [
        "git add -A -- data/auction_v3/history/tplus1_open_0930_v1 data/market/trade_cal_sse.csv"
    ]
    assert all("*" not in line for line in add_lines)

    compute_source = _embedded_python_blocks_between(
        backfill,
        "- name: Build one exact allowlisted Backfill candidate patch",
        "- name: Upload immutable candidate patch",
    )[0]
    publish_source = _embedded_python_after(
        backfill,
        "- name: Apply exact base candidate and create one commit",
    )

    bad_paths = tmp_path / "bad-backfill-paths.bin"
    bad_paths.write_bytes(b"outputs/auction_v3/escape.json\0")
    empty_index = tmp_path / "empty-index.bin"
    empty_index.write_bytes(b"")
    monkeypatch.setenv("STAGED_PATHS", str(bad_paths))
    monkeypatch.setenv("STAGED_INDEX", str(empty_index))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(bad_paths))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(empty_index))
    with pytest.raises(SystemExit, match="non-allowlisted Backfill paths staged"):
        exec(compile(compute_source, "<backfill-compute-segment>", "exec"), {})
    with pytest.raises(SystemExit, match="non-allowlisted Backfill publish paths staged"):
        exec(compile(publish_source, "<backfill-publish-segment>", "exec"), {})

    repo = tmp_path / "backfill-git-case"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "unexpected.txt"
    source.write_text("unexpected\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "unexpected.txt"], cwd=repo, check=True)
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
    destination = repo / (
        "data/auction_v3/history/tplus1_open_0930_v1/training_allowed.csv"
    )
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    symlink_path = repo / (
        "data/auction_v3/history/tplus1_open_0930_v1/training_link.csv"
    )
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to("missing.html")
    subprocess.run(
        [
            "git",
            "add",
            "-A",
            "--",
            "unexpected.txt",
            "data/auction_v3/history/tplus1_open_0930_v1",
        ],
        cwd=repo,
        check=True,
    )
    visible = subprocess.run(
        ["git", "diff", "--cached", "--no-renames", "--name-only", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert {item.decode() for item in visible.split(b"\0") if item} == {
        "unexpected.txt",
        "data/auction_v3/history/tplus1_open_0930_v1/training_allowed.csv",
        "data/auction_v3/history/tplus1_open_0930_v1/training_link.csv",
    }
    index_bytes = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert b"120000 " in index_bytes

    paths_file = tmp_path / "backfill-staged-paths.bin"
    index_file = tmp_path / "backfill-staged-index.bin"
    paths_file.write_bytes(visible)
    index_file.write_bytes(index_bytes)
    monkeypatch.setenv("STAGED_PATHS", str(paths_file))
    monkeypatch.setenv("STAGED_INDEX", str(index_file))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(paths_file))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(index_file))
    with pytest.raises(SystemExit, match="non-allowlisted Backfill paths staged"):
        exec(compile(compute_source, "<backfill-compute-rename>", "exec"), {})
    with pytest.raises(SystemExit, match="non-allowlisted Backfill publish paths staged"):
        exec(compile(publish_source, "<backfill-publish-rename>", "exec"), {})

    allowed_paths = tmp_path / "backfill-allowed-paths.bin"
    allowed_paths.write_bytes(
        b"data/auction_v3/history/tplus1_open_0930_v1/training_link.csv\0"
    )
    monkeypatch.setenv("STAGED_PATHS", str(allowed_paths))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(allowed_paths))
    with pytest.raises(SystemExit, match="non-regular Backfill paths staged"):
        exec(compile(compute_source, "<backfill-compute-mode>", "exec"), {})
    with pytest.raises(SystemExit, match="non-regular Backfill publish paths staged"):
        exec(compile(publish_source, "<backfill-publish-mode>", "exec"), {})


@pytest.mark.parametrize(
    ("name", "candidate_marker", "publisher_marker", "compute_error", "publish_error"),
    (
        (
            "run_auction_v3.yml",
            "- name: Build exact allowlisted candidate patch",
            "- name: Apply candidate with base-SHA CAS and create one commit",
            "non-allowlisted Auction paths staged",
            "non-allowlisted Auction publish paths staged",
        ),
        (
            "verify_decision_observations.yml",
            "- name: Build exact allowlisted Verify candidate patch",
            "- name: Apply exact base candidate and create one commit",
            "non-allowlisted Verify paths staged",
            "non-allowlisted Verify publish paths staged",
        ),
    ),
)
def test_auction_and_verify_allow_only_exact_dated_raw_truth_layout(
    name: str,
    candidate_marker: str,
    publisher_marker: str,
    compute_error: str,
    publish_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _text(name)
    compute_source = _embedded_python_blocks_between(
        text,
        candidate_marker,
        "- name: Upload immutable candidate patch",
    )[0]
    publish_source = _embedded_python_after(text, publisher_marker)
    paths_file = tmp_path / f"{name}-paths.bin"
    index_file = tmp_path / f"{name}-index.bin"

    valid = (
        "data/market/raw/2026/20260813/stk_auction_o.csv",
        "data/market/raw/2026/20260813/stk_auction_o.meta.json",
    )
    paths_file.write_bytes(b"\0".join(path.encode() for path in valid) + b"\0")
    index_file.write_bytes(
        b"".join(
            b"100644 " + b"0" * 40 + b" 0\t" + path.encode() + b"\0"
            for path in valid
        )
    )
    monkeypatch.setenv("STAGED_PATHS", str(paths_file))
    monkeypatch.setenv("STAGED_INDEX", str(index_file))
    monkeypatch.setenv("PUBLISH_STAGED_PATHS", str(paths_file))
    monkeypatch.setenv("PUBLISH_STAGED_INDEX", str(index_file))
    exec(compile(compute_source, f"<{name}-valid-compute>", "exec"), {})
    exec(compile(publish_source, f"<{name}-valid-publish>", "exec"), {})

    invalid = (
        "data/market/raw/20260813/stk_auction_o.csv",
        "data/market/raw/2026/20260813/nested/stk_auction_o.csv",
        "data/market/raw_evil/2026/20260813/stk_auction_o.csv",
        "data/market/raw/202X/20260813/stk_auction_o.csv",
        "data/market/raw/2026/20260A13/stk_auction_o.csv",
        "data/market/raw/2025/20260813/stk_auction_o.csv",
        "data/market/raw/2026/20261399/stk_auction_o.csv",
        "data/market/raw/2026/20260230/stk_auction_o.csv",
        "data/market/raw/2026/20260813/not_stk_auction_o.csv",
    )
    index_file.write_bytes(b"")
    for path in invalid:
        paths_file.write_bytes(path.encode() + b"\0")
        with pytest.raises(SystemExit, match=compute_error):
            exec(compile(compute_source, f"<{name}-invalid-compute>", "exec"), {})
        with pytest.raises(SystemExit, match=publish_error):
            exec(compile(publish_source, f"<{name}-invalid-publish>", "exec"), {})

    paths_file.write_bytes(valid[0].encode() + b"\0")
    index_file.write_bytes(
        b"120000 " + b"0" * 40 + b" 0\t" + valid[0].encode() + b"\0"
    )
    with pytest.raises(SystemExit, match="non-regular"):
        exec(compile(compute_source, f"<{name}-symlink-compute>", "exec"), {})
    with pytest.raises(SystemExit, match="non-regular"):
        exec(compile(publish_source, f"<{name}-symlink-publish>", "exec"), {})


def test_all_writer_candidate_and_publisher_path_gates_expand_renames_and_check_modes() -> None:
    for name in WRITERS:
        text = _text(name)
        assert text.count("git diff --cached --no-renames --name-only -z") == 2, name
        assert text.count("git ls-files --stage -z") == 2, name
        assert text.count("non-stage-zero") >= 2, name
        assert text.count("non-regular") >= 2, name


def test_auction_dry_run_rebuilds_verified_frozen_runtime_and_full_action_contract() -> None:
    text = _text("run_auction_v3.yml")
    run_step = text.split(
        "- name: Run Auction pipeline and final runtime gates", 1
    )[1].split("- name: Build exact allowlisted candidate patch", 1)[0]
    assert 'args+=(--force-inactive)' in run_step
    assert (
        "python scripts/run_deterministic_numeric.py scripts/run_auction_v3.py"
    ) in run_step
    assert "python scripts/publish_decision_action.py" in run_step
    assert "validate_action_plan_artifact" in run_step
    assert "python scripts/validate_decision_model_freeze.py --runtime" in run_step
    assert "python scripts/validate_decision_model_freeze.py --runtime --force-inactive" in run_step
    assert "validate_io_contract.py" not in run_step


def test_auction_recovery_route_is_active_only_and_never_enters_live_pipeline() -> None:
    text = _text("run_auction_v3.yml")
    header = text.split("\npermissions:", 1)[0]
    mode = text.split("- name: Resolve dry-run mode and immutable base", 1)[1].split(
        "- name: Setup Python", 1
    )[0]
    preflight = text.split(
        "- name: Require active production freeze and enforced pins for selected route", 1
    )[1].split("- name: Run Decision execution tests", 1)[0]
    sync = text.split("- name: Sync strict calendar and minute truth", 1)[1].split(
        "- name: Run Auction pipeline and final runtime gates", 1
    )[0]
    recovery = text.split(
        "- name: Build isolated retrospective action recovery candidate", 1
    )[1].split("- name: Build exact allowlisted candidate patch", 1)[0]

    assert "recovery_report_dates:" in header
    assert "route=auction" in mode and "route=recovery" in mode
    assert "recovery_report_dates is mutually exclusive with signal_date" in mode
    assert "recovery_report_dates is mutually exclusive with a custom order_amount" in mode
    assert "route=${route}" in mode
    assert "route == 'recovery' and not active" in preflight
    assert "requires active Decision freeze even in dry-run" in preflight
    assert "if: ${{ steps.mode.outputs.route == 'auction' }}" in sync
    assert "TUSHARE_TOKEN" in sync
    assert "scripts/sync_tushare_minute.py" in sync

    assert "if: ${{ steps.mode.outputs.route == 'recovery' }}" in recovery
    assert "scripts/recover_decision_action_gaps.py" in recovery
    assert '--report-dates "${RECOVERY_REPORT_DATES}"' in recovery
    assert '--base-sha "$(cat "${RUNNER_TEMP}/base_sha.txt")"' in recovery
    assert '--output-root "${candidate_root}"' in recovery
    assert "validate_report_index_action_truth" in recovery
    assert "report_index_path=root / 'outputs/decision/report_index.json'" in recovery
    for forbidden in (
        "TUSHARE_TOKEN",
        "sync_tushare_minute.py",
        "run_auction_v3.py",
        "publish_decision_action.py",
        "validate_decision_model_freeze.py --runtime",
    ):
        assert forbidden not in recovery


def test_auction_recovery_candidate_and_publisher_are_exact_receipted_cas() -> None:
    text = _text("run_auction_v3.yml")
    candidate = text.split("- name: Build exact allowlisted candidate patch", 1)[1].split(
        "- name: Upload immutable candidate patch", 1
    )[0]
    upload = text.split("- name: Upload immutable candidate patch", 1)[1].split(
        "\n\n  publish:", 1
    )[0]
    envelope = text.split("- name: Verify immutable candidate envelope", 1)[1].split(
        "- name: Apply candidate with base-SHA CAS and create one commit", 1
    )[0]
    publisher = text.split(
        "- name: Apply candidate with base-SHA CAS and create one commit", 1
    )[1].split("- name: Publish exact CAS commit", 1)[0]

    assert "git add -A --pathspec-from-file=" in candidate
    assert "receipt.get('changed_paths')" in candidate
    assert "non-allowlisted Auction recovery paths staged" in candidate
    assert "outputs/decision/action_plan_latest.json" in candidate
    assert "steps.mode.outputs.publish == 'true'" in upload
    assert "recovery-receipt.json" in upload
    assert "steps.mode.outputs.route == 'recovery'" in upload
    assert "steps.mode.outputs.publish == 'true' && steps.candidate.outputs.has_changes == 'true'" in upload

    assert "expected_files.add('recovery-receipt.json')" in envelope
    assert "recovery receipt base SHA mismatch" in envelope
    assert "recovery receipt changed-path contract mismatch" in envelope
    assert "recovery receipt output hash inventory mismatch" in envelope
    assert "recovery receipt source hash inventory is empty" in envelope

    assert 'test "$(git rev-parse HEAD)" = "${expected}"' in publisher
    assert "non-allowlisted Auction recovery publish paths staged" in publisher
    assert "recovery output SHA256 changed after patch apply" in publisher
    assert "recovery receipt source hashes differ from action payloads" in publisher
    assert "recovery source SHA256 differs from exact base" in publisher
    assert "recovery source blob differs from exact base" in publisher
    assert "action_plan_latest byte/blob identity changed during recovery" in publisher
    assert "action_plan_latest byte SHA256 changed during recovery" in publisher
    assert publisher.count("git commit -m") == 1
    assert "git push" not in publisher


def test_auction_recovery_envelope_rejects_latest_or_unbound_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    base_sha = "a" * 40
    patch = b"recovery patch\n"
    (candidate / "base_sha.txt").write_text(base_sha + "\n", encoding="ascii")
    (candidate / "candidate.patch").write_bytes(patch)
    (candidate / "candidate.patch.sha256").write_text(
        hashlib.sha256(patch).hexdigest() + "\n", encoding="ascii"
    )
    dates = ["20260818", "20260819"]
    paths = [f"outputs/decision/action_plan_{date}.json" for date in dates] + [
        "outputs/decision/report_index.json"
    ]
    receipt = {
        "schema_version": "decision_action_gap_recovery_receipt_v1",
        "status": "candidate_generated",
        "base_sha": base_sha,
        "report_dates": dates,
        "changed_paths": paths,
        "source_sha256": {"models/decision_model_freeze.json": "b" * 64},
        "output_sha256": {path: "c" * 64 for path in paths},
        "action_plan_latest_changed": False,
    }
    receipt_path = candidate / "recovery-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("CANDIDATE_DIR", str(candidate))
    monkeypatch.setenv("ROUTE", "recovery")
    source = _embedded_python_after(
        _text("run_auction_v3.yml"), "- name: Verify immutable candidate envelope"
    )
    exec(compile(source, "<auction-recovery-envelope>", "exec"), {})

    receipt["changed_paths"][-1] = "outputs/decision/action_plan_latest.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(SystemExit, match="changed-path contract mismatch"):
        exec(compile(source, "<auction-recovery-envelope-latest>", "exec"), {})

    receipt["changed_paths"] = [
        f"outputs/decision/action_plan_{date}.json" for date in dates
    ] + ["outputs/decision/report_index.json"]
    receipt["source_sha256"] = {"/tmp/unbound-secret": "b" * 64}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(SystemExit, match="invalid recovery receipt source inventory"):
        exec(compile(source, "<auction-recovery-envelope-source>", "exec"), {})


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


def test_successful_publishers_deploy_their_exact_commit_through_reusable_pages() -> None:
    for name in PAGES_HANDOFF_WRITERS:
        text = _text(name)
        publish = text[text.index("\n  publish:") : text.index("\n  deploy-pages:")]
        handoff = text[text.index("\n  deploy-pages:") :]
        assert "published_head: ${{ steps.publish.outputs.published_head }}" in publish, name
        assert "id: publish" in publish, name
        if name == "migrate_decision_runtime.yml":
            assert "core.setOutput('published_head', createdCommit.data.sha)" in publish, name
        else:
            assert 'echo "published_head=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"' in publish, name
        assert "needs: publish" in handoff, name
        assert "needs.publish.result == 'success'" in handoff, name
        assert "needs.publish.outputs.published_head != ''" in handoff, name
        assert "contents: read" in handoff, name
        assert "pages: write" in handoff, name
        assert "id-token: write" in handoff, name
        assert "uses: ./.github/workflows/deploy_dc20_pages.yml" in handoff, name
        assert "expected_head: ${{ needs.publish.outputs.published_head }}" in handoff, name
        assert "workflow_run:" not in handoff, name
        assert "github.event.workflow_run.head_sha" not in text, name


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
    if name == "backfill_decision_v11_history.yml":
        (candidate / "backfill-receipt.json").write_text("{}\n", encoding="utf-8")
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
    assert 'python "${runtime_root}/scripts/run_deterministic_numeric.py"' in runtime
    assert '"${runtime_root}/scripts/replay_frozen_canonical_v2.py"' in runtime
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


def test_all_model_workflows_pin_one_numeric_runtime_before_import() -> None:
    exact_env = {
        'PYTHONHASHSEED: "0"',
        'OMP_NUM_THREADS: "1"',
        'OMP_THREAD_LIMIT: "1"',
        'OMP_DYNAMIC: "FALSE"',
        'OPENBLAS_NUM_THREADS: "1"',
        'OPENBLAS_CORETYPE: "Haswell"',
        'GOTO_NUM_THREADS: "1"',
        'MKL_NUM_THREADS: "1"',
        'MKL_DYNAMIC: "FALSE"',
        'BLIS_NUM_THREADS: "1"',
        'VECLIB_MAXIMUM_THREADS: "1"',
        'NUMEXPR_NUM_THREADS: "1"',
        'NPY_ENABLE_CPU_FEATURES: "X86_V3"',
    }
    for name in NUMERIC_WORKFLOWS:
        text = _text(name)
        global_header = text.split("\njobs:", 1)[0]
        for binding in exact_env:
            assert binding in global_header, (name, binding)
        assert "NPY_DISABLE_CPU_FEATURES" not in global_header, name
        assert "runs-on: ubuntu-24.04" in text, name

    assert (
        "python scripts/run_deterministic_numeric.py scripts/run_auction_v3.py"
        in _text("run_auction_v3.yml")
    )
    assert (
        "python scripts/run_deterministic_numeric.py "
        "scripts/replay_frozen_canonical_v2.py"
        in _text("run_decision_daily.yml")
    )
    assert (
        "python scripts/run_deterministic_numeric.py "
        "scripts/replay_frozen_canonical_v2.py"
        in _text("diagnose_decision_fingerprint.yml")
    )


def test_diagnostic_compares_persisted_and_replayed_exact_v3_action() -> None:
    text = _text("diagnose_decision_fingerprint.yml")
    assert "DC20_NUMERIC_RUNTIME_EVIDENCE_FILE" in text
    assert "Validate allowlisted deterministic numeric runtime evidence" in text
    assert "dc20_deterministic_numeric_runtime_v1" in text
    assert "github_ubuntu_24_04_x86_64" in text
    assert "numpy_x86_v4_disabled" in text
    assert "deterministic numeric libraries were not both audited" in text
    assert "Snapshot enforced persisted action semantics" in text
    assert "Require exact V3 action semantics across the independent replay" in text
    assert "f\"signal_date={bound_dates['signal_date']}\\n\"" in text
    assert "f\"report_date={bound_dates['report_date']}\\n\"" in text
    assert '--signal-date "${{ steps.persisted_action.outputs.signal_date }}"' in text
    assert '--report-date "${{ steps.persisted_action.outputs.report_date }}"' in text
    assert "PERSISTED_SIGNAL_DATE: ${{ steps.persisted_action.outputs.signal_date }}" in text
    assert "PERSISTED_REPORT_DATE: ${{ steps.persisted_action.outputs.report_date }}" in text
    assert "diagnostic replayed action signal_date drifted" in text
    assert "diagnostic replayed action report_date drifted" in text
    assert text.count("validate_action_plan_artifact(") >= 2
    assert text.count("action_plan_semantic_projection_v3(") >= 2
    assert "diagnostic_action_semantic_comparison_v4" in text
    assert "NATIVE_NO_TRADE_COMPARISON_PROFILE_V3" in text
    assert "RETROSPECTIVE_REPLAY_COMPARISON_PROFILE_V3" in text
    assert "retrospective_persisted_vs_native_replay" in text
    assert "comparison_profile=persisted_profile" in text
    assert "persisted_comparison_profile" in text
    assert "replayed_comparison_profile" in text
    assert "profile_relation" in text
    assert "PERSISTED_IDENTITY_SHA256" in text
    assert "persisted_subtree_sha256" in text
    assert "replayed_subtree_sha256" in text
    assert "Persisted and replayed exact V3 action semantics differ" in text


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
    assert "Validate owner-scoped Backfill artifacts before candidate creation" in compute
    assert "Validate exact Backfill candidate in an isolated frozen runtime" in compute
    assert "Persist immutable history before model rebuild" not in text
    assert "git commit" not in compute
