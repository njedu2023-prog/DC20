"""The slow audit is moved, never deselected from the complete CI test set."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test_decision_core.yml"
AUDIT = "tests/test_decision_source_surface_rotation.py"


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _test_step(job):
    matches = [step for step in job["steps"] if "python -m pytest " in step.get("run", "")]
    assert len(matches) == 1
    return matches[0]


def _selection(job):
    step = _test_step(job)
    lines = [line.strip() for line in step["run"].splitlines() if line.strip().startswith("python -m pytest ")]
    assert len(lines) == 1
    command = lines[0].split(" 2>&1 | tee ")[0]
    words = shlex.split(command)
    assert words[:3] == ["python", "-m", "pytest"]
    allowed = {"-vv", "--tb=short", "--durations=25"}
    selection = []
    for word in words[3:]:
        if word in allowed or word.startswith("--junitxml=$RUNNER_TEMP/"):
            continue
        assert word in {AUDIT, "--ignore=" + AUDIT}, f"unreviewed test selector: {word}"
        selection.append(word)
    return selection


def _collected_nodes(selection):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=f"{ROOT}:{ROOT / 'src'}")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *selection],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    nodes = [line for line in result.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    assert nodes and len(nodes) == len(set(nodes)), result.stdout
    return set(nodes)


def test_live_collection_partitions_are_disjoint_and_cover_every_original_node():
    jobs = _jobs()
    core = _selection(jobs["test-decision-core"])
    audit = _selection(jobs["source-surface-audit"])
    assert core == ["--ignore=" + AUDIT]
    assert audit == [AUDIT]
    complete_nodes = _collected_nodes([])
    core_nodes = _collected_nodes(core)
    audit_nodes = _collected_nodes(audit)
    assert not core_nodes & audit_nodes
    assert core_nodes | audit_nodes == complete_nodes
    assert all(node.startswith(AUDIT + "::") for node in audit_nodes)


def test_both_partitions_preserve_failures_and_always_upload_diagnostics():
    jobs = _jobs()
    for name, minutes in (("test-decision-core", 60), ("source-surface-audit", 45)):
        job = jobs[name]
        assert job["timeout-minutes"] == minutes
        assert job["runs-on"] == "ubuntu-24.04"
        assert not job.get("continue-on-error")
        step = _test_step(job)
        assert step["shell"] == "bash"
        assert "set -euo pipefail" in step["run"]
        assert "-vv --tb=short --durations=25" in step["run"]
        assert " 2>&1 | tee " in step["run"]
        assert "|| true" not in step["run"] and not step.get("continue-on-error")
        assert step["env"]["PYTHONPATH"] == "${{ github.workspace }}:${{ github.workspace }}/src"
        uploads = [item for item in job["steps"] if item.get("uses", "").startswith("actions/upload-artifact@")]
        assert len(uploads) == 1
        upload = uploads[0]
        assert upload["if"] == "always()"
        assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
        assert ".log" in upload["with"]["path"] and ".xml" in upload["with"]["path"]
    assert "node --version" in _test_step(jobs["test-decision-core"])["run"]


def test_existing_frozen_replay_and_locked_numeric_runtime_remain_required():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    jobs = workflow["jobs"]
    frozen = jobs["frozen-canonical-replay"]
    assert frozen["timeout-minutes"] == 15
    assert not frozen.get("continue-on-error")
    assert "Run and diagnose full frozen canonical runtime replay" in [step.get("name") for step in frozen["steps"]]
    assert workflow["env"]["OMP_NUM_THREADS"] == "1"
    assert workflow["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert workflow["env"]["PYTHONHASHSEED"] == "0"
    for job in jobs.values():
        assert any("--require-hashes -r requirements-dev.lock" in step.get("run", "") for step in job["steps"])
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["uses"] == "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
        assert checkout["with"]["persist-credentials"] is False
        setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
        assert setup["uses"] == "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
        assert setup["with"]["python-version"] == "3.12.13"
