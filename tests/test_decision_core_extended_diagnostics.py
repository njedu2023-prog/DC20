"""Bounded CI evidence with real GNU timeout checks, never a replacement gate."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/diagnose_decision_core_extended.yml"
ORIGINAL = ROOT / ".github/workflows/test_decision_core.yml"
NATIVE = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Requires actual Linux GNU timeout semantics; macOS is not Linux verification",
)


def _workflow():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow, workflow["jobs"]["extended-core-diagnostic"]


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def _core():
    return _step(_workflow()[1], "Run decision core regression tests")


def test_original_protected_ci_and_dependencies_keep_exact_bytes():
    from runpy import run_path
    _obs_actual_source = run_path(str(ROOT / "tests/test_decision_source_surface_rotation.py"))["_obs_actual_source"]
    expected = {
        ".github/workflows/test_decision_core.yml": "f4ad709bac2b6ed80370c17ece3b03aa5a7d3dc3211e94f9a46b57b7c0b1f5a1",
        ".github/workflows/diagnose_decision_core.yml": "08b865f34e1925d777e4845e20c1cccd133860b80b30d08af159f175eb994455",
        "scripts/diagnose_core_supervisor.py": "b21794ddd38510ce06c1cbe958fe0744ce28547d998fceb6f34a31b66a5d5a99",
        "tests/test_decision_core_ci_partition.py": "5051831911869426b7336b29c0af19957a9765e85ed12f0c335169eb661a3c34",
        "tests/test_decision_core_ci_diagnostics.py": "ef22d34dac141c0b22fa8eafb07fcac48f2cfe0193ced15014011ffe1d6dbc12",
        "tests/test_core_diagnostic_supervisor.py": "d1f766bbc1110ff5225fc0ce8f38ed1e371cb915519bd8239defedba8ef19f51",
        "requirements-dev.lock": "773ce43677ceff7e5829252816ba017738011ff60a389d60f0435b96264005f2",
        "requirements.lock": "612ef31ce0996b739b319683b06878200604e04e8f915f32a946285044ff132c",
    }
    for path, digest in expected.items():
        assert hashlib.sha256(_obs_actual_source(path)).hexdigest() == digest, path


def test_original_numeric_environment_install_and_full_selector_are_preserved():
    original = yaml.safe_load(ORIGINAL.read_text())
    workflow, job = _workflow()
    base_job = original["jobs"]["test-decision-core"]
    base_step = _step(base_job, "Run decision core regression tests")
    assert workflow["env"] == original["env"]
    assert _core()["env"]["PYTHONPATH"] == base_step["env"]["PYTHONPATH"]
    assert _core()["env"]["PYTHONUNBUFFERED"] == _core()["env"]["PYTHONFAULTHANDLER"] == "1"
    for name in ("Setup Python", "Install deps"):
        assert _step(job, name) == _step(base_job, name)
    base_line = next(line.strip() for line in base_step["run"].splitlines() if " -m pytest " in line)
    actual_line = next(line.strip() for line in _core()["run"].splitlines() if line.strip().startswith("timeout "))
    base_words = shlex.split(base_line.split(" 2>&1 | tee ")[0])[3:]
    actual_words = shlex.split(actual_line.split(" > ")[0])
    assert actual_words[:8] == ["timeout", "--signal=TERM", "--kill-after=15s", "45m", "python", "-u", "-m", "pytest"]
    actual_words = actual_words[8:]
    index = actual_words.index("-o")
    assert actual_words[index + 1] == "faulthandler_timeout=120"
    del actual_words[index:index + 2]
    assert [w for w in actual_words if not w.startswith("--junitxml=")] == [
        w for w in base_words if not w.startswith("--junitxml=")]


def test_triggers_permissions_checkout_and_actions_are_bounded():
    workflow, job = _workflow()
    events = workflow.get("on", workflow.get(True))
    assert events == {"workflow_dispatch": None, "push": {
        "branches": ["main"], "paths": [".github/workflows/diagnose_decision_core_extended.yml"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"extended-core-diagnostic"}
    assert job["if"] == "github.repository == 'njedu2023-prog/DC20' && github.ref == 'refs/heads/main'"
    assert job["runs-on"] == "ubuntu-24.04"
    checkout = _step(job, "Checkout")
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert checkout["uses"] == "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"actions/(checkout|setup-python|upload-artifact)@[0-9a-f]{40}", step["uses"])
        assert "secrets." not in str(step)
        assert not step.get("permissions") and not step.get("continue-on-error")
    assert not job.get("continue-on-error")
    for forbidden in ("workflow_run", "schedule:", "contents: write", "tushare", "git push", "diagnose_core_supervisor.py"):
        assert forbidden not in WORKFLOW.read_text()


def test_timeout_headroom_status_preservation_and_always_upload():
    _, job = _workflow()
    core = _core()
    assert job["timeout-minutes"] == 75
    assert core["timeout-minutes"] == 50
    script = core["run"]
    assert "set -euo pipefail" in script and "set +e" in script
    assert 'core_status=$?\nset -e' in script
    assert script.rstrip().endswith('exit "$core_status"')
    assert ' > "$RUNNER_TEMP/core-extended-diagnostic/core-regression.log" 2>&1' in script
    assert "| tee" not in script and "|| true" not in script
    command = next(line.strip() for line in script.splitlines() if line.strip().startswith("timeout "))
    assert "--foreground" not in command and "--preserve-status" not in command
    upload = _step(job, "Preserve extended core diagnostic evidence")
    assert upload["if"] == "always()"
    for path in ("core-regression.log", "core-regression.xml", "core-status.txt", "selftest.xml"):
        assert path in upload["with"]["path"]
    initial = _step(job, "Initialize diagnostic evidence")
    assert job["steps"].index(initial) < job["steps"].index(_step(job, "Setup Python"))
    assert "state=NOT_STARTED" in initial["run"] and "state=RUNNING" in script
    selftest = _step(job, "Validate extended diagnostic on actual Linux")
    assert selftest["timeout-minutes"] == 5
    assert job["steps"].index(selftest) < job["steps"].index(core)
    assert "python -m pytest tests/test_decision_core_extended_diagnostics.py" in selftest["run"]
    assert 'sys.platform.startswith("linux")' in selftest["run"]
    assert '"skipped","failures","errors"' in selftest["run"]


def test_all_workflow_shell_blocks_parse():
    for step in _workflow()[1]["steps"]:
        if "run" in step:
            subprocess.run(["bash", "-n", "-c", step["run"]], check=True, timeout=5)


@pytest.mark.parametrize("exit_code,state", [
    (0, "COMMAND_COMPLETED"), (7, "COMMAND_FAILED"),
    (124, "DIAGNOSTIC_WINDOW_TIMEOUT"), (137, "KILLED_CAUSE_UNDETERMINED"),
])
def test_actual_status_shell_preserves_command_exit_and_partial_logs(tmp_path, exit_code, state):
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(tmp_path / "summary.txt"))
    initial = _step(_workflow()[1], "Initialize diagnostic evidence")["run"]
    script = _core()["run"]
    command = next(line for line in script.splitlines() if line.startswith("timeout "))
    fixture = shlex.join([sys.executable, "-u", "-c", f"print('tiny fixture progress'); raise SystemExit({exit_code})"])
    script = script.replace(command, fixture + " > " + command.split(" > ", 1)[1]).replace("node --version", ":")
    with (tmp_path / "shell.log").open("wb") as output:
        subprocess.run(["bash", "-c", initial], env=env, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=5)
        result = subprocess.run(["bash", "-c", script], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=5)
    assert result.returncode == exit_code
    evidence = tmp_path / "core-extended-diagnostic"
    status = dict(line.split("=", 1) for line in (evidence / "core-status.txt").read_text().splitlines())
    assert status == {"diagnostic_only": "true", "replaces_required_core_ci": "false", "state": state, "exit_code": str(exit_code)}
    assert "tiny fixture progress" in (evidence / "core-regression.log").read_text()
    assert "does not replace the required core CI gate" in (tmp_path / "summary.txt").read_text()


@pytest.mark.parametrize("mode,expected", [("success", 0), ("failure", 7), ("hang", 124), ("ignore_term", 137)])
@NATIVE
def test_actual_linux_gnu_timeout_returns_expected_shell_status(tmp_path, mode, expected):
    executable = shutil.which("timeout")
    assert executable, "Linux runner requires GNU timeout"
    log = tmp_path / "timeout.log"
    with log.open("wb") as output:
        subprocess.run([executable, "--version"], stdout=output, stderr=subprocess.STDOUT, check=True, timeout=5)
    assert "GNU coreutils" in log.read_text()
    body = "import signal,time\n"
    if mode == "ignore_term":
        body += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    body += "print('tiny fixture ready', flush=True)\n"
    body += f"raise SystemExit({expected})\n" if mode in ("success", "failure") else "time.sleep(30)\n"
    # The outer shell gives the same 137 encoding as the workflow; timeout's
    # own SIGKILL would otherwise appear as -9 in Python. No output PIPEs.
    script = '"$1" --signal=TERM --kill-after=0.2s 0.5s "${@:2}"; status=$?; exit "$status"'
    with log.open("ab") as output:
        process = subprocess.Popen(
            ["bash", "-c", script, "fixture", executable, sys.executable, "-u", "-c", body],
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            result = process.wait(timeout=5)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assert result == expected
    assert "tiny fixture ready" in log.read_text()
