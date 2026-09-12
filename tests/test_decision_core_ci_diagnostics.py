"""Bounded offline checks; timeout stubs test the shell boundary, not GNU timeout."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test_decision_core.yml"


def _core():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["test-decision-core"]
    step = next(item for item in job["steps"] if item.get("name") == "Run decision core regression tests")
    return job, step


def _environment(tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               PYTHONFAULTHANDLER="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTEST_ADDOPTS="", RUNNER_TEMP=str(tmp_path))
    return env


def _executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)


def test_core_has_process_group_cutoff_before_job_deadline_and_stack_diagnostics():
    job, step = _core()
    assert job["timeout-minutes"] == 60
    assert step["env"]["PYTHONUNBUFFERED"] == "1"
    assert step["env"]["PYTHONFAULTHANDLER"] == "1"
    command = next(line.strip() for line in step["run"].splitlines() if line.strip().startswith("timeout "))
    words = shlex.split(command.split(" 2>&1 | tee ")[0])
    assert words[:9] == ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "50m", "python", "-u", "-m", "pytest"]
    assert "--foreground" not in words  # GNU timeout must retain group signalling.
    assert words[words.index("-o") + 1] == "faulthandler_timeout=120"
    assert "-vv" in words
    assert "set -euo pipefail" in step["run"]
    assert "|| true" not in step["run"]
    assert not step.get("continue-on-error") and not job.get("continue-on-error")
    upload = next(item for item in job["steps"] if item.get("name") == "Preserve core regression diagnostics")
    assert upload["if"] == "always()"
    assert "core-regression.log" in upload["with"]["path"]
    assert "core-regression.xml" in upload["with"]["path"]
    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True, timeout=5)


@pytest.mark.parametrize("returncode", [0, 1, 124, 137])
def test_shell_boundary_simulation_retains_timeout_failure_and_progress(tmp_path, returncode):
    """Simulated timeout executable: no claim this runs GNU timeout on macOS."""
    binary = tmp_path / "bin"
    binary.mkdir()
    _executable(binary / "node", "print('offline node-version fixture')\n")
    _executable(binary / "timeout", "import sys\n"
                "assert sys.argv[1:5] == ['--verbose', '--signal=TERM', '--kill-after=30s', '50m']\n"
                "assert sys.argv[5:9] == ['python', '-u', '-m', 'pytest']\n"
                "print('tests/test_offline_fixture.py::test_pending', flush=True)\n"
                "print('offline timeout-boundary simulation', file=sys.stderr, flush=True)\n"
                f"sys.exit({returncode})\n")
    env = _environment(tmp_path)
    env["PATH"] = str(binary) + os.pathsep + env["PATH"]
    _, step = _core()
    result = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == returncode
    log = (tmp_path / "core-regression.log").read_text()
    assert "tests/test_offline_fixture.py::test_pending" in log
    assert "offline timeout-boundary simulation" in log


@pytest.mark.parametrize("passes", [False, True])
def test_exact_pytest_options_retain_real_fixture_outcome_and_xml(tmp_path, passes):
    """Run a tiny real pytest fixture, forwarding through a non-timing stub."""
    binary = tmp_path / "bin"
    binary.mkdir()
    _executable(binary / "node", "print('offline node-version fixture')\n")
    _executable(binary / "timeout", "import os, sys\n"
                "assert sys.argv[1:5] == ['--verbose', '--signal=TERM', '--kill-after=30s', '50m']\n"
                "os.execv(sys.executable, [sys.executable, *sys.argv[6:]])\n")
    (tmp_path / "test_offline_fixture.py").write_text(f"def test_fixture():\n    assert {passes!r}\n")
    env = _environment(tmp_path)
    env["PATH"] = str(binary) + os.pathsep + env["PATH"]
    _, step = _core()
    result = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == (0 if passes else 1), result.stdout + result.stderr
    assert "test_offline_fixture.py::test_fixture" in (tmp_path / "core-regression.log").read_text()
    xml = (tmp_path / "core-regression.xml").read_text()
    assert f'failures="{0 if passes else 1}"' in xml


def test_real_pytest_faulthandler_emits_stack_with_default_capture(tmp_path):
    (tmp_path / "test_offline_fixture.py").write_text(
        "import time\ndef test_waits_briefly():\n    time.sleep(0.3)\n")
    result = subprocess.run(
        [sys.executable, "-u", "-m", "pytest", "-vv", "-p", "no:cacheprovider",
         "-o", "faulthandler_timeout=0.1", str(tmp_path / "test_offline_fixture.py")],
        cwd=tmp_path, env=_environment(tmp_path), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "Timeout (" in combined
    assert "in test_waits_briefly" in combined
    assert "1 passed" in combined
