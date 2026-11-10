"""A daily run may reuse CI only when its complete static surface was tested."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess

import pytest

from scripts import candidate_runtime_preflight as p

HEAD, APPROVED, TREE = "a" * 40, "b" * 40, "c" * 40


def entries():
    return [{"path": path, "mode": "100644", "type": "blob", "sha": "d" * 40}
            for path in (p.WORKFLOW, "scripts/candidate_runtime_preflight.py",
                         "models/decision_model_freeze.json", "models/fixed-ridge.json",
                         "src/top10decision/decision/three_rank.py", "requirements-dev.lock",
                         "tests/fixtures/continuation.json", "forward/storage.py",
                         p.UPGRADE + "candidate_forward_predict.py",
                         p.UPGRADE + "candidate_natural_model/evaluation.json")]


def run():
    return {"id": 123, "event": "push", "status": "completed", "conclusion": "success",
            "run_attempt": 1, "head_branch": "main", "path": p.WORKFLOW,
            "workflow_id": p.WORKFLOW_ID, "repository": {"full_name": p.REPO},
            "head_repository": {"full_name": p.REPO}, "head_sha": APPROVED,
            "html_url": "https://github.com/" + p.REPO + "/actions/runs/123"}


class FakeAPI:
    def __init__(self):
        self.run, self.entries, self.calls = run(), entries(), []
        self.job = {"name": p.JOB, "run_id": 123, "head_sha": APPROVED,
                    "status": "completed", "conclusion": "success", "steps": [
                        {"name": name, "status": "completed", "conclusion": "success"}
                        for name in (p.REGRESSION_STEP, p.FREEZE_STEP, p.RECORD_STEP)]}
        self.truncated, self.job_count = False, 1

    def request(self, path):
        self.calls.append(path)
        if path.startswith("/actions/workflows/"):
            return {"total_count": 1, "workflow_runs": [deepcopy(self.run)]}
        if path == "/git/commits/" + APPROVED:
            return {"sha": APPROVED, "tree": {"sha": TREE}}
        if path == "/git/trees/" + TREE + "?recursive=1":
            return {"sha": TREE, "truncated": self.truncated, "tree": deepcopy(self.entries)}
        if path == "/actions/runs/123":
            return deepcopy(self.run)
        if path == "/actions/runs/123/attempts/1/jobs?per_page=100":
            return {"total_count": self.job_count, "jobs": [deepcopy(self.job)]}
        raise AssertionError(path)


def test_data_only_commit_reuses_exact_tested_static_code_and_does_not_admit_data():
    api = FakeAPI()
    old = entries() + [{"path": "data/market/confirmed_suspensions.json", "mode": "100644",
                        "type": "blob", "sha": "e" * 40}]
    new = entries() + [{"path": "data/market/confirmed_suspensions.json", "mode": "100644",
                        "type": "blob", "sha": "f" * 40}]
    api.entries = old
    result = p.approve(api, p.surface(new), HEAD)
    assert result["approved_head_sha"] == APPROVED != HEAD
    assert result["status"] == "APPROVED_IDENTICAL_STATIC_SURFACE"
    assert result["daily_source_evidence_admitted"] is False
    assert result["production_freeze_issued"] is False


@pytest.mark.parametrize("index", range(10))
def test_each_static_code_model_fixture_dependency_or_workflow_change_requires_new_ci(index):
    api, current = FakeAPI(), entries()
    current[index]["sha"] = "e" * 40
    with pytest.raises(ValueError, match="NO_SUCCESSFUL_PUSH_REGRESSION"):
        p.approve(api, p.surface(current), HEAD)
    assert not any("/attempts/" in call for call in api.calls)


def test_new_source_file_cannot_reuse_previous_approval():
    current = entries() + [{"path": "scripts/new_execution.py", "mode": "100644", "type": "blob", "sha": "f" * 40}]
    with pytest.raises(ValueError, match="NO_SUCCESSFUL_PUSH_REGRESSION"):
        p.approve(FakeAPI(), p.surface(current), HEAD)


@pytest.mark.parametrize("key,value", [("event", "workflow_run"), ("event", "workflow_dispatch"),
                                      ("head_branch", "other"), ("run_attempt", 2),
                                      ("status", "in_progress"), ("conclusion", "failure"),
                                      ("path", ".github/workflows/other.yml"), ("workflow_id", 1),
                                      ("head_repository", {"full_name": "foreign/repo"})])
def test_only_successful_first_attempt_exact_main_push_issues_approval(key, value):
    api = FakeAPI()
    api.run[key] = value
    with pytest.raises(ValueError, match="NO_SUCCESSFUL_PUSH_REGRESSION"):
        p.approve(api, p.surface(entries()), HEAD)
    assert len(api.calls) == 1


@pytest.mark.parametrize("name", [p.REGRESSION_STEP, p.FREEZE_STEP, p.RECORD_STEP])
@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled"])
def test_success_run_label_cannot_replace_required_executed_ci_steps(name, conclusion):
    api = FakeAPI()
    next(step for step in api.job["steps"] if step["name"] == name)["conclusion"] = conclusion
    with pytest.raises(ValueError, match="APPROVAL_REQUIRED_STEP_NOT_RUN"):
        p.approve(api, p.surface(entries()), HEAD)


def test_historical_ci_without_new_approval_steps_cannot_approve_runtime():
    api = FakeAPI()
    api.job["steps"] = [{"name": "Old unchecked validation", "status": "completed", "conclusion": "success"}]
    with pytest.raises(ValueError, match="APPROVAL_REQUIRED_STEP_NOT_RUN"):
        p.approve(api, p.surface(entries()), HEAD)


@pytest.mark.parametrize("fault,reason", [("tree", "APPROVAL_TREE_INCOMPLETE"), ("jobs", "APPROVAL_JOBS_INCOMPLETE")])
def test_incomplete_api_results_are_never_treated_as_success(fault, reason):
    api = FakeAPI()
    if fault == "tree":
        api.truncated = True
    else:
        api.job_count = 2
    with pytest.raises(ValueError, match=reason):
        p.approve(api, p.surface(entries()), HEAD)


def test_api_failure_does_not_fall_back_to_unapproved_or_empty_state():
    class Offline:
        def request(self, path):
            raise ValueError("APPROVAL_API_UNAVAILABLE_NOT_APPROVED")
    with pytest.raises(ValueError, match="API_UNAVAILABLE_NOT_APPROVED"):
        p.approve(Offline(), p.surface(entries()), HEAD)


def test_same_surface_pending_ci_is_waited_for_then_verified_not_skipped():
    api, now = FakeAPI(), [0.0]
    api.run.update(status="in_progress", conclusion=None)
    def sleep(seconds):
        now[0] += seconds
        api.run.update(status="completed", conclusion="success")
    result = p.approve_with_wait(api, p.surface(entries()), HEAD, 60, clock=lambda: now[0], sleep=sleep)
    assert result["approval_wait_polls"] == 1 and now[0] == 30
    assert any("/attempts/1/jobs" in call for call in api.calls)


def test_pending_ci_timeout_never_issues_approval():
    api, now = FakeAPI(), [0.0]
    api.run.update(status="queued", conclusion=None)
    with pytest.raises(ValueError, match="APPROVAL_CI_PENDING_TIMEOUT:123"):
        p.approve_with_wait(api, p.surface(entries()), HEAD, 30,
                            clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert now[0] == 30


def test_pending_ci_for_different_static_surface_cannot_hold_or_approve_current_code():
    api = FakeAPI()
    api.run.update(status="in_progress", conclusion=None)
    current = entries()
    current[0]["sha"] = "e" * 40
    with pytest.raises(ValueError, match="NO_SUCCESSFUL_PUSH_REGRESSION"):
        p.approve_with_wait(api, p.surface(current), HEAD, 60,
                            sleep=lambda seconds: pytest.fail("unrelated CI must not be waited for"))


def test_dynamic_evidence_and_results_are_excluded_but_runtime_and_tests_are_bound():
    for path in ("data/market/confirmed_suspensions.json", "data/market/raw/2026/20261110/daily.csv",
                 "outputs/decision/three_rank_top10_20261110.json", p.UPGRADE + "candidate_natural_forward/day_20261110.json"):
        assert not p.static_path(path), path
    for path in (p.WORKFLOW, "scripts/new_guard.py", "src/module.py", "models/ridge.joblib",
                 "tests/fixtures/original.json", p.UPGRADE + "candidate_eligible_forward_registration.json"):
        assert p.static_path(path), path


def test_local_checkout_bytes_are_checked_against_git_not_only_head(monkeypatch, tmp_path):
    values = entries()
    for item in values:
        file = tmp_path / item["path"]
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"verified\n")
        item["sha"] = hashlib.sha1(b"blob 9\0verified\n").hexdigest()
    def git(args):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return HEAD.encode()
        if "ls-tree" in args:
            return b"\0".join((f"{v['mode']} blob {v['sha']}\t{v['path']}").encode() for v in values) + b"\0"
        if "ls-files" in args:
            return b""
        raise AssertionError(args)
    monkeypatch.setattr(subprocess, "check_output", git)
    assert p.local_surface(tmp_path, HEAD) == p.surface(values)
    (tmp_path / "scripts/candidate_runtime_preflight.py").write_text("changed\n")
    with pytest.raises(ValueError, match="STATIC_CHECKOUT_BYTES_CHANGED"):
        p.local_surface(tmp_path, HEAD)


def test_workflow_keeps_full_push_ci_and_both_daily_jobs_require_approval():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / p.WORKFLOW).read_text()
    assert p.REGRESSION_STEP in workflow and p.FREEZE_STEP in workflow and p.RECORD_STEP in workflow
    assert "if: github.event_name == 'push'" in workflow
    assert workflow.count("python scripts/candidate_runtime_preflight.py verify") == 2
    assert "needs: validate" in workflow
    assert "python scripts/validate_decision_model_freeze.py --root ." in workflow
    assert "candidate_natural_trigger_gate" in workflow
    assert "continue-on-error" not in workflow
    for prefix in p.STATIC_PREFIXES:
        assert '"' + prefix + '**"' in workflow


def test_forward_model_inventory_is_approved_static_surface():
    assert p.static_path("forward/model_inventory.json")
