"""Supplemental evidence windows never replace the original complete CI gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import yaml

from scripts import diagnose_core_supervisor as supervisor


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/diagnose_decision_core.yml"
FROZEN_WORKFLOW = ROOT / ".github/workflows/test_decision_core.yml"


def _core():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["test-decision-core"]
    step = next(item for item in job["steps"] if item.get("name") == "Run decision core regression tests")
    return job, step


def test_frozen_ci_files_remain_exact_reviewed_bytes():
    review = json.loads((ROOT / "models/decision_source_surface_review_20260912_ci_partition.json").read_bytes())
    for name in (".github/workflows/test_decision_core.yml", "tests/test_decision_core_ci_partition.py"):
        entry = next(item for item in review["source_changes"] if item["path"] == name)
        raw = (ROOT / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["current_sha256"]
        assert len(raw) == entry["current_bytes"]


def test_supplemental_keeps_full_original_selector_numeric_runtime_and_locked_deps(tmp_path):
    original = yaml.safe_load(FROZEN_WORKFLOW.read_text())
    diagnostic = yaml.safe_load(WORKFLOW.read_text())
    assert diagnostic["env"] == original["env"]
    assert diagnostic["permissions"] == {"contents": "read"}
    events = diagnostic.get("on", diagnostic.get(True))
    assert set(events) == {"push", "workflow_dispatch"}
    assert events["push"] == {"branches": ["main"], "paths": [".github/workflows/diagnose_decision_core.yml"]}
    assert set(diagnostic["jobs"]) == {"test-decision-core"}
    base_job = original["jobs"]["test-decision-core"]
    job, step = _core()
    base_step = next(item for item in base_job["steps"] if item.get("name") == step["name"])
    line = next(line.strip() for line in base_step["run"].splitlines() if " -m pytest " in line)
    original_words = shlex.split(line.split(" 2>&1 | tee ")[0])[3:]
    actual_words = supervisor.pytest_command(tmp_path)[4:]
    assert actual_words[actual_words.index("-o") + 1] == "faulthandler_timeout=120"
    index = actual_words.index("-o"); del actual_words[index:index + 2]
    assert [w for w in actual_words if not w.startswith("--junitxml=")] == [
        w for w in original_words if not w.startswith("--junitxml=")]
    assert step["env"]["PYTHONPATH"] == base_step["env"]["PYTHONPATH"]
    for name in ("Setup Python", "Install deps"):
        assert next(item for item in job["steps"] if item["name"] == name) == next(item for item in base_job["steps"] if item["name"] == name)
    assert not any("secrets." in str(item) for item in job["steps"])


def test_short_supervisor_has_upload_headroom_and_no_tee_pipe_masking():
    job, step = _core()
    assert job["timeout-minutes"] == 25
    assert supervisor.WINDOW_SECONDS == 600
    assert supervisor.TERM_GRACE_SECONDS == supervisor.KILL_WAIT_SECONDS == 5
    assert step["env"]["PYTHONUNBUFFERED"] == step["env"]["PYTHONFAULTHANDLER"] == "1"
    words = shlex.split(next(line for line in step["run"].splitlines() if line.strip().startswith("python -u")))
    assert words == ["python", "-u", "scripts/diagnose_core_supervisor.py", "--output", "$RUNNER_TEMP/core-timeout-diagnostic"]
    assert "| tee " not in step["run"] and "timeout --" not in step["run"]
    assert "set -euo pipefail" in step["run"] and "|| true" not in step["run"]
    assert not step.get("continue-on-error") and not job.get("continue-on-error")
    upload = next(item for item in job["steps"] if item.get("name") == "Preserve core regression diagnostics")
    assert upload["if"] == "always()"
    for name in ("core-regression.log", "core-regression.xml", "core-progress.jsonl", "core-supervisor.json"):
        assert name in upload["with"]["path"]
    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True, timeout=5)


def test_cli_has_no_window_override_or_arbitrary_command():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/diagnose_core_supervisor.py"), "--help"],
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0 and "--output" in result.stdout
    assert "--command" not in result.stdout and "--window" not in result.stdout


def test_linux_real_process_selftest_must_pass_without_skips_before_core():
    job, core = _core()
    step = next(s for s in job["steps"] if s.get("name") == "Validate supervisor on actual Linux process groups")
    assert job["steps"].index(step) < job["steps"].index(core)
    assert "python -m pytest tests/test_core_diagnostic_supervisor.py" in step["run"]
    assert '==30' in step["run"]
    assert '"skipped","failures","errors"' in step["run"]
    assert 'sys.platform.startswith("linux")' in step["run"]
    assert not step.get("continue-on-error") and "|| true" not in step["run"]
    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True, timeout=5)
