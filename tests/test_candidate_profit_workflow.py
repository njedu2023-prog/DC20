"""Offline publication boundary tests; no market or GitHub calls."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts import publish_candidate_profit as m
from work.profit_1000_upgrade import candidate_natural_journal_git as git_api


def context(event="schedule"):
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": m.REPOSITORY,
           "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
           "GITHUB_WORKFLOW_REF": m.REPOSITORY + "/" + m.WORKFLOW_PATH + "@refs/heads/main",
           "GITHUB_EVENT_NAME": event, "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "11"}
    registration = {"id": 42, "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME, "state": "active"}
    run = {"id": 11, "run_attempt": 1, "head_sha": "a" * 40, "head_branch": "main",
           "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME, "workflow_id": 42, "event": event,
           "status": "in_progress", "conclusion": None, "repository": {"full_name": m.REPOSITORY},
           "head_repository": {"full_name": m.REPOSITORY}}
    trigger = {"workflow_run": {"workflow_id": 357027830,
        "path": m.UPSTREAMS[357027830], "status": "completed", "conclusion": "success",
        "run_attempt": 1, "head_branch": "main", "event": "workflow_run",
        "repository": {"full_name": m.REPOSITORY}, "head_repository": {"full_name": m.REPOSITORY}}}
    return env, trigger, run, registration


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch", "workflow_run"])
def test_only_registered_first_main_run(event):
    assert m.validate_workflow_context(*context(event)) == "a" * 40


@pytest.mark.parametrize("key,value", [("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo"),
    ("GITHUB_REF", "refs/heads/fork"), ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "push"),
    ("GITHUB_SHA", "bad"), ("GITHUB_WORKFLOW_REF", "other/path")])
def test_bad_environment_rejected(key, value):
    args = context(); args[0][key] = value
    with pytest.raises(ValueError): m.validate_workflow_context(*args)


@pytest.mark.parametrize("key,value", [("workflow_id", 1), ("path", "other.yml"),
    ("conclusion", "failure"), ("run_attempt", 2), ("head_branch", "other"), ("event", "push"),
    ("repository", {"full_name": "fork/DC20"}), ("head_repository", {"full_name": "fork/DC20"})])
def test_failed_fork_or_validation_only_upstream_rejected(key, value):
    args = context("workflow_run"); args[1]["workflow_run"][key] = value
    with pytest.raises(ValueError): m.validate_workflow_context(*args)


def package():
    return {m.PUBLIC_ROOT + "index.json": m.encoded({"test_only": True}),
            m.PUBLIC_ROOT + "summary.json": m.encoded({"test_only": True})}


@pytest.mark.parametrize("path", ["decision.html", "models/profit.joblib", "outputs/decision/three_rank_top10_20260914.json",
    m.PUBLIC_ROOT + "../evil.json", m.PUBLIC_ROOT + "day_unknown.json"])
def test_writer_rejects_unrelated_paths(path):
    files = package(); files[path] = m.encoded({"test_only": True})
    with pytest.raises(ValueError): m.validate_public_files(files)


def node(raw):
    return {"type": "blob", "mode": "100644", "sha": git_api.git_blob(raw), "size": len(raw)}


class FakeGit:
    def __init__(self, old):
        self.old = old; self.calls = []; self.head = "a" * 40; self.new = None
    def call(self, method, path, data=None):
        self.calls.append((method, path, deepcopy(data)))
        if method == "GET" and path == "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": self.head}}
        if method == "POST" and path == "/git/trees":
            assert data["base_tree"] == "b" * 40
            self.new = deepcopy(self.old)
            for item in data["tree"]:
                self.new[item["path"]] = node(item["content"].encode())
            return {"sha": "c" * 40}
        if method == "GET" and path == "/git/trees/" + "c" * 40 + "?recursive=1":
            return self.new
        if method == "POST" and path == "/git/commits":
            assert data["parents"] == ["a" * 40]
            return {"sha": "d" * 40, "tree": {"sha": "c" * 40}, "parents": [{"sha": "a" * 40}]}
        if method == "PATCH" and path == "/git/refs/heads/main":
            assert data == {"sha": "d" * 40, "force": False}
            self.head = data["sha"]; return {"object": {"sha": self.head}}
        pytest.fail("unexpected Git call: " + method + path)


def publish(files, client, guard=lambda: None):
    return m.publish_files(files, source_head="a" * 40, source_tree_sha="b" * 40,
        source_tree=client.old, client=client, guard=guard)


def test_only_public_json_additions_with_nonforce_cas(monkeypatch):
    monkeypatch.setattr(git_api, "_tree", lambda document, sha: document)  # storage tree SHA tested natively elsewhere
    git = FakeGit({"old/model": node(b"untouched")})
    result = publish(package(), git)
    assert result["files_changed"] == 2 and git.new["old/model"] == git.old["old/model"]
    assert result["commit_sha"] == "d" * 40


def test_identical_package_is_read_only():
    files = package(); git = FakeGit({p: node(raw) for p, raw in files.items()})
    assert publish(files, git)["status"] == "UNCHANGED"
    assert all(method == "GET" for method, _, _ in git.calls)


def test_frozen_day_never_replaced():
    path = m.PUBLIC_ROOT + "day_20260914.json"
    files = package(); files[path] = m.encoded({"rank": "changed"})
    git = FakeGit({path: node(m.encoded({"rank": "original"}))})
    with pytest.raises(ValueError, match="FROZEN_PUBLIC_DAY"): publish(files, git)
    assert not git.calls


def test_concurrent_main_change_never_writes():
    git = FakeGit({}); git.head = "e" * 40
    with pytest.raises(ValueError, match="MAIN_MOVED"): publish(package(), git)
    assert all(method == "GET" for method, _, _ in git.calls)


def test_source_guard_failure_prevents_writes():
    git = FakeGit({})
    def guard(): raise ValueError("source changed")
    with pytest.raises(ValueError, match="source changed"): publish(package(), git, guard)
    assert all(method == "GET" for method, _, _ in git.calls)


def test_successful_cas_checks_source_and_deadline_again_after_ack(monkeypatch):
    monkeypatch.setattr(git_api, "_tree", lambda document, sha: document)
    git = FakeGit({})
    observed = []
    result = publish(package(), git, guard=lambda: observed.append(git.head))
    assert result["status"] == "PUBLISHED"
    assert observed == ["a" * 40, "a" * 40, "d" * 40]
    assert len([call for call in git.calls if call[:2] == ("GET", "/git/ref/heads/main")]) == 3


def test_post_cas_deadline_failure_is_explicit_and_never_rewrites_acknowledged_day(monkeypatch):
    monkeypatch.setattr(git_api, "_tree", lambda document, sha: document)
    git = FakeGit({})
    observed = []
    path = m.PUBLIC_ROOT + "day_20260914.json"
    files = {**package(), path: m.encoded({"original_frozen": True})}
    def guard():
        observed.append(git.head)
        if git.head == "d" * 40:
            raise ValueError("NEW_FORMAL_DAY_PUBLICATION_DEADLINE_PASSED")
    with pytest.raises(ValueError, match="PUBLISHED_CAS_ACKNOWLEDGED_BUT_POST_GUARD_FAILED_READ_ONLY_RECONCILIATION_REQUIRED"):
        publish(files, git, guard)
    assert observed == ["a" * 40, "a" * 40, "d" * 40]
    assert git.head == "d" * 40
    assert git.new[path] == node(files[path])
    assert len([call for call in git.calls if call[0] == "PATCH"]) == 1
    assert len([call for call in git.calls if call[:2] == ("POST", "/git/commits")]) == 1


def test_unrelated_tree_mutation_prevents_cas(monkeypatch):
    def corrupt(document, sha):
        document["old/model"] = node(b"corrupted")
        return document
    monkeypatch.setattr(git_api, "_tree", corrupt)
    git = FakeGit({"old/model": node(b"original")})
    with pytest.raises(ValueError, match="UNEXPECTED_PUBLICATION_FILE"): publish(package(), git)
    assert not any(method == "PATCH" for method, _, _ in git.calls)


def test_workflow_has_no_market_token_or_git_push_and_deploys_exact_head():
    raw = (m.ROOT / m.WORKFLOW_PATH).read_text()
    flow = yaml.load(raw, Loader=yaml.BaseLoader)
    assert "TUSHARE_TOKEN" not in raw and "git push" not in raw and "ssh" not in raw
    assert flow["permissions"] == {"contents": "read"}
    assert flow["jobs"]["publish"]["if"] == "github.event_name != 'push'"
    assert flow["jobs"]["deploy"]["with"]["expected_head"] == "${{ needs.publish.outputs.commit_sha }}"
    assert flow["on"]["workflow_dispatch"]["inputs"]["dry_run"]["default"] == "true"
    assert "schedule" in flow["on"] and "workflow_run" in flow["on"]
    for job in ("validate", "publish"):
        checkout = next(step for step in flow["jobs"][job]["steps"] if "actions/checkout@" in step.get("uses", ""))
        assert checkout["with"]["persist-credentials"] == "false"
