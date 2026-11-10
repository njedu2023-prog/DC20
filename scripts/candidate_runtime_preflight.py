"""Reuse successful main-push regression only for identical static source bytes.

Read-only approval, never a source-data, scoring, freeze or publication authority.
Daily market evidence is checked by the existing run-bound production validators.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

REPO = "njedu2023-prog/DC20"
WORKFLOW = ".github/workflows/research_candidate_natural_forward.yml"
WORKFLOW_ID = 357010624
JOB = "Validate fixed natural candidate contracts"
REGRESSION_STEP = "Run complete fixed candidate contract regression"
FREEZE_STEP = "Validate production freeze and unchanged source tree"
RECORD_STEP = "Record approved static runtime surface"
SCHEMA = "dc20_candidate_runtime_ci_approval_v1"
STATIC_PREFIXES = ("src/", "scripts/", "tests/", "models/", ".github/")
STATIC_EXACT = {"requirements.lock", "requirements-dev.lock", "pyproject.toml", "setup.cfg",
                "setup.py", "pytest.ini", "conftest.py", "tox.ini", "forward/model_inventory.json"}
UPGRADE = "work/profit_1000_upgrade/"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def exact_sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "EXACT_COMMIT_REQUIRED")
    return value


def static_path(path):
    """Static code/model/test surface; dated inputs and output ledgers are separate."""
    require(isinstance(path, str) and path and not path.startswith("/")
            and all(p not in ("", ".", "..") for p in path.split("/")), "UNSAFE_TREE_PATH")
    if path.startswith(STATIC_PREFIXES) or path in STATIC_EXACT:
        return True
    if path.startswith(UPGRADE + "candidate_natural_model/"):
        return True
    relative = path.removeprefix(UPGRADE)
    if relative != path and "/" not in relative and relative.endswith((".py", ".json")):
        return True
    return path.startswith("forward/") and path.endswith(".py")


def surface(entries, *, require_complete=True):
    require(isinstance(entries, list), "TREE_ENTRIES_REQUIRED")
    result, seen = {}, set()
    for entry in entries:
        require(isinstance(entry, dict), "INVALID_TREE_ENTRY")
        path = entry.get("path")
        require(path not in seen, "DUPLICATE_TREE_ENTRY")
        seen.add(path)
        selected = static_path(path)
        if entry.get("type") == "tree":
            continue
        if selected:
            require(entry.get("type") == "blob" and entry.get("mode") in ("100644", "100755"),
                    "STATIC_SURFACE_MUST_BE_REGULAR_FILES")
            result[path] = {"mode": entry["mode"], "sha": exact_sha(entry.get("sha"))}
    require(not require_complete or (WORKFLOW in result and "scripts/candidate_runtime_preflight.py" in result
            and "models/decision_model_freeze.json" in result
            and "requirements-dev.lock" in result), "INCOMPLETE_STATIC_APPROVAL_SURFACE")
    return dict(sorted(result.items()))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_surface(root, expected_head):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])
    head = git("rev-parse", "HEAD").decode().strip()
    require(head == exact_sha(expected_head), "CHECKOUT_HEAD_CHANGED")
    entries = []
    for item in git("ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            entries.append({"path": path.decode(), "type": kind, "mode": mode, "sha": sha})
    approved = surface(entries)
    for path, binding in approved.items():
        file = root / path
        require(file.is_file() and not file.is_symlink(), "STATIC_CHECKOUT_FILE_MISSING_OR_UNSAFE")
        raw = file.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(actual == binding["sha"], "STATIC_CHECKOUT_BYTES_CHANGED:" + path)
    for path in git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0"):
        if path:
            require(not static_path(path), "UNTRACKED_STATIC_SOURCE:" + path)
    return approved


class GitHub:
    def __init__(self, token):
        require(bool(token), "READ_ONLY_GITHUB_TOKEN_REQUIRED")
        self.token, self.calls = token, 0

    def request(self, path):
        require(path.startswith("/actions/") or path.startswith("/git/"), "READ_ENDPOINT_REJECTED")
        self.calls += 1
        require(self.calls <= 128, "APPROVAL_READ_BUDGET_EXCEEDED")
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                require(len(raw) <= 16 * 1024 * 1024, "APPROVAL_RESPONSE_TOO_LARGE")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise ValueError("APPROVAL_GITHUB_HTTP_" + str(exc.code)) from None
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise ValueError("APPROVAL_API_UNAVAILABLE_NOT_APPROVED") from None
            time.sleep(attempt + 1)


def remote_surface(api, head):
    # Immutable commit surfaces are safe to reuse across bounded CI polling.
    if not hasattr(api, "_surface_cache"):
        api._surface_cache = {}
    if head in api._surface_cache:
        return api._surface_cache[head]
    commit = api.request("/git/commits/" + exact_sha(head))
    require(commit.get("sha") == head, "APPROVAL_COMMIT_IDENTITY_CHANGED")
    tree_sha = exact_sha(commit.get("tree", {}).get("sha"))
    tree = api.request("/git/trees/" + tree_sha + "?recursive=1")
    require(tree.get("sha") == tree_sha and tree.get("truncated") is False, "APPROVAL_TREE_INCOMPLETE")
    result = surface(tree.get("tree"), require_complete=False)
    api._surface_cache[head] = result
    return result


def approval_origin(run):
    return (isinstance(run, dict) and type(run.get("id")) is int and run["id"] > 0
            and run.get("event") == "push" and type(run.get("run_attempt")) is int
            and run.get("run_attempt") == 1
            and run.get("head_branch") == "main" and run.get("path") == WORKFLOW
            and run.get("workflow_id") == WORKFLOW_ID
            and run.get("repository", {}).get("full_name") == REPO
            and run.get("head_repository", {}).get("full_name") == REPO)


def verified_run(run):
    return approval_origin(run) and run.get("status") == "completed" and run.get("conclusion") == "success"


def verify_regression_steps(api, run):
    value = api.request(f"/actions/runs/{run['id']}/attempts/1/jobs?per_page=100")
    jobs = value.get("jobs")
    require(isinstance(jobs, list) and type(value.get("total_count")) is int
            and value["total_count"] == len(jobs), "APPROVAL_JOBS_INCOMPLETE")
    matches = [job for job in jobs if job.get("name") == JOB]
    require(len(matches) == 1, "APPROVAL_REGRESSION_JOB_MISSING_OR_DUPLICATE")
    job = matches[0]
    require(job.get("run_id") == run["id"] and job.get("head_sha") == run["head_sha"]
            and job.get("status") == "completed" and job.get("conclusion") == "success",
            "APPROVAL_REGRESSION_JOB_NOT_SUCCESSFUL")
    steps = job.get("steps")
    require(isinstance(steps, list), "APPROVAL_REGRESSION_STEPS_MISSING")
    for name in (REGRESSION_STEP, FREEZE_STEP, RECORD_STEP):
        found = [step for step in steps if step.get("name") == name]
        require(len(found) == 1 and found[0].get("status") == "completed"
                and found[0].get("conclusion") == "success", "APPROVAL_REQUIRED_STEP_NOT_RUN:" + name)


def approve(api, current, head):
    # Only successful push runs can issue approval. Daily runs never approve themselves.
    value = api.request(f"/actions/workflows/{WORKFLOW_ID}/runs?event=push&status=success&branch=main&per_page=100")
    runs = value.get("workflow_runs")
    require(isinstance(runs, list) and type(value.get("total_count")) is int
            and value["total_count"] >= len(runs), "APPROVAL_RUN_LIST_INVALID")
    checked = set()
    for candidate in runs:
        if not verified_run(candidate):
            continue
        approved_head = exact_sha(candidate.get("head_sha"))
        if approved_head in checked:
            continue
        checked.add(approved_head)
        require(len(checked) <= 30, "NO_MATCHING_APPROVAL_WITHIN_BOUNDED_HISTORY")
        # This comparison also binds the workflow, gate itself, tests and model.
        # A historical workflow without the required CI steps cannot approve this one.
        if remote_surface(api, approved_head) != current:
            continue
        run = api.request(f"/actions/runs/{candidate['id']}")
        require(verified_run(run) and run["id"] == candidate["id"]
                and run["head_sha"] == approved_head, "APPROVAL_RUN_CHANGED")
        verify_regression_steps(api, run)
        return {"schema_version": SCHEMA, "status": "APPROVED_IDENTICAL_STATIC_SURFACE",
                "repository": REPO, "current_head_sha": exact_sha(head), "approved_head_sha": approved_head,
                "approval_run_id": run["id"], "approval_run_url": run.get("html_url"),
                "surface_sha256": digest(current), "file_count": len(current),
                "daily_source_evidence_admitted": False, "production_freeze_issued": False}
    raise ValueError("NO_SUCCESSFUL_PUSH_REGRESSION_FOR_CURRENT_STATIC_SURFACE")


def pending_approval(api, current):
    value = api.request(f"/actions/workflows/{WORKFLOW_ID}/runs?event=push&branch=main&per_page=100")
    runs = value.get("workflow_runs")
    require(isinstance(runs, list) and type(value.get("total_count")) is int
            and value["total_count"] >= len(runs), "APPROVAL_RUN_LIST_INVALID")
    for run in runs:
        if approval_origin(run) and run.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
            if remote_surface(api, exact_sha(run.get("head_sha"))) == current:
                return run["id"]
    return None


def approve_with_wait(api, current, head, wait_seconds=0, *, clock=time.monotonic, sleep=time.sleep):
    require(type(wait_seconds) is int and 0 <= wait_seconds <= 480, "BOUNDED_APPROVAL_WAIT_REQUIRED")
    deadline, polls = clock() + wait_seconds, 0
    while True:
        try:
            result = approve(api, current, head)
            result["approval_wait_polls"] = polls
            return result
        except ValueError as exc:
            if str(exc) not in ("NO_SUCCESSFUL_PUSH_REGRESSION_FOR_CURRENT_STATIC_SURFACE",
                                "NO_MATCHING_APPROVAL_WITHIN_BOUNDED_HISTORY") or not wait_seconds:
                raise
            pending = pending_approval(api, current)
            if pending is None:
                raise
            remaining = deadline - clock()
            require(remaining > 0, "APPROVAL_CI_PENDING_TIMEOUT:" + str(pending))
            print(json.dumps({"status": "WAITING_FOR_MATCHING_PUSH_CI", "run_id": pending,
                              "seconds_remaining": round(remaining, 1)}), flush=True)
            sleep(min(30, remaining))
            polls += 1


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "verify"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    require(os.environ.get("GITHUB_REPOSITORY") == REPO and os.environ.get("GITHUB_REF") == "refs/heads/main",
            "EXACT_MAIN_REPOSITORY_CONTEXT_REQUIRED")
    head = exact_sha(os.environ.get("GITHUB_SHA"))
    root = Path(args.root).resolve()
    current = local_surface(root, head)
    surface_finished = time.monotonic()
    api = None
    if args.command == "record":
        require(os.environ.get("GITHUB_EVENT_NAME") == "push", "ONLY_PUSH_CI_CAN_RECORD_APPROVAL_SURFACE")
        value = {"schema_version": SCHEMA, "status": "CI_SURFACE_RECORDED_REQUIRES_SUCCESSFUL_RUN",
                 "head_sha": head, "surface_sha256": digest(current), "files": current}
    else:
        require(os.environ.get("GITHUB_EVENT_NAME") in ("workflow_run", "workflow_dispatch"), "DAILY_EVENT_REQUIRED")
        api = GitHub(os.environ.get("DC20_CANDIDATE_GITHUB_TOKEN"))
        value = approve_with_wait(api, current, head, args.wait_seconds)
    finished = time.monotonic()
    value["timing"] = {"static_surface_seconds": round(surface_finished - started, 6),
                       "approval_lookup_seconds": round(finished - surface_finished, 6),
                       "total_seconds": round(finished - started, 6),
                       "github_read_calls": api.calls if api else 0,
                       "scope": "Read-only static code approval; excludes daily source validation and scoring."}
    value["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    receipt = Path(args.receipt).resolve()
    require(receipt != root and root not in receipt.parents, "RECEIPT_MUST_BE_OUTSIDE_CHECKOUT")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({k: v for k, v in value.items() if k != "files"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
