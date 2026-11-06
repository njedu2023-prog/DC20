"""Daily production cannot reactivate retired publication or dated recovery work."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / ".github/workflows"
ARCHIVE = ROOT / ".github/archived-workflows"
RETIRED = {
    "run_primary_profit_rankings.yml": "dc20-retired-p1-audit-${{ github.run_id }}",
    "run_primary_profit_forward_shadow.yml": "dc20-retired-shadow-audit-${{ github.run_id }}",
}
ARCHIVED_NAMES = {
    "accept_forward_inputs.yml", "cancel_superseded_tests_20260923.yml",
    "collect_posthoc_20260921.yml", "publish_entry_prices_20260923.yml",
    "recover_candidate_20260922.yml", "recover_entry_prices_20260923.yml",
    "recover_observer_20260922.yml", "recover_publication_20260922.yml",
    "recover_settlement_20260923.yml", "research_profit_1000_capital.yml",
}


def _workflow(name):
    # BaseLoader preserves the GitHub key `on` rather than YAML 1.1 boolean True.
    return yaml.load((ACTIVE / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.parametrize("name,group", RETIRED.items())
def test_retired_publication_is_manual_readonly_audit(name, group):
    value = _workflow(name)
    assert set(value["on"]) == {"workflow_dispatch"}
    assert value["on"]["workflow_dispatch"]["inputs"]["dry_run"]["default"] == "true"
    assert value["concurrency"] == {"group": group, "cancel-in-progress": "false"}
    compute = value["jobs"]["compute"]
    assert "github.event_name == 'workflow_dispatch' && inputs.dry_run == true" in compute["if"]
    assert compute["permissions"]["contents"] == "read"
    # Publication is blocked at the producer. Deployers must either inherit
    # that gate or depend on the successful published commit; disabled history
    # jobs may retain an unconditional false guard.
    assert "needs.compute.outputs.publish == 'true'" in value["jobs"]["publish"]["if"]
    for key, job in value["jobs"].items():
        if key in {"compute", "publish"}:
            continue
        assert (job["if"] == "${{ false }}"
                or "needs.compute.outputs.publish == 'true'" in job["if"]
                or ("publish" in job["needs"] and "needs.publish.result == 'success'" in job["if"]
                    and "needs.publish.outputs.published_head != ''" in job["if"]))


@pytest.mark.parametrize("name", RETIRED)
@pytest.mark.parametrize("event,dry_run,accepted", [
    ("workflow_dispatch", "true", True),
    ("workflow_dispatch", "false", False),
    ("workflow_run", "true", False),
    ("schedule", "true", False),
])
def test_actual_mode_script_never_publishes_retired_workflow(tmp_path, name, event, dry_run, accepted):
    value = _workflow(name)
    mode = next(step for step in value["jobs"]["compute"]["steps"] if step.get("id") == "mode")
    # Execute the real script. A deterministic git stub supplies only the immutable
    # checkout SHA; any other git command fails rather than mutating a repository.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    git = bindir / "git"
    git.write_text("#!/bin/sh\n[ \"$*\" = 'rev-parse HEAD' ] || exit 99\nprintf '%s\\n' '1111111111111111111111111111111111111111'\n")
    git.chmod(0o755)
    output = tmp_path / "outputs"
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               GITHUB_EVENT_NAME=event, INPUT_DRY_RUN=dry_run,
               GITHUB_OUTPUT=str(output), RUNNER_TEMP=str(tmp_path))
    result = subprocess.run(["bash", "-c", mode["run"]], env=env, text=True, capture_output=True)
    if accepted:
        assert result.returncode == 0, result.stderr
        assert "publish=false\n" in output.read_text()
        assert "publish=true" not in output.read_text()
    else:
        assert result.returncode != 0
        assert "manual dry-run audit only" in result.stderr
        assert not output.exists()
        assert not (tmp_path / "base_sha.txt").exists()


def test_archives_preserve_original_bytes_and_are_not_active_actions():
    manifest = json.loads((ARCHIVE / "manifest.json").read_text())
    assert manifest["schema_version"] == "dc20_archived_workflows_v1"
    assert manifest["baseline_commit"] == "6a0f9e0a6ea5e57957fbac5c30849dd508d1daf2"
    assert manifest["production_data_removed"] is False
    assert {Path(row["original_path"]).name for row in manifest["entries"]} == ARCHIVED_NAMES
    for row in manifest["entries"]:
        assert Path(row["original_path"]).parent == Path(".github/workflows")
        assert Path(row["archive_path"]).parent == Path(".github/archived-workflows")
        assert not (ROOT / row["original_path"]).exists()
        assert hashlib.sha256((ROOT / row["archive_path"]).read_bytes()).hexdigest() == row["sha256"]


def test_manual_diagnostic_and_replay_do_not_restore_natural_reader_identity():
    assert set(_workflow("diagnose_candidate_handoff.yml")["on"]) == {"workflow_dispatch"}
    replay = _workflow("test_forward_rebuild.yml")
    assert set(replay["on"]) == {"workflow_dispatch"}
    assert replay["permissions"]["contents"] == "read"
    assert "FORWARD_SCHEDULE" not in (ACTIVE / "test_forward_rebuild.yml").read_text()


def test_legacy_named_p0_scheduler_bridge_and_current_delivery_remain_available():
    bridge = _workflow("run_decision_daily.yml")
    assert bridge["on"]["schedule"]
    assert bridge["jobs"]["primary_d_schedule_bridge"]["uses"] == "./.github/workflows/run_primary_d_daily.yml"
    assert _workflow("run_primary_d_daily.yml")["on"]["schedule"]
    for name in (
        "research_candidate_natural_forward.yml", "research_candidate_natural_observer.yml",
        "publish_candidate_profit.yml", "profit_research.yml",
    ):
        assert (ACTIVE / name).is_file()
