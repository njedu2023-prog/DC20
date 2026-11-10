"""Read-only filter for an observer that verified old evidence without new work.

Only a proved skipped observer work job suppresses settlement. An executed work
job delegates to the unchanged settlement proof checks; this filter does not
assert that it published evidence, collected prices or admitted a new freeze.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

REPO = "njedu2023-prog/DC20"
OBSERVER_PATH = ".github/workflows/research_candidate_natural_observer.yml"
OBSERVER_NAME = "DC20 · Preserve natural candidate publication (research)"
OBSERVER_ID = 357027830
WORKFLOW_PATH = ".github/workflows/research_candidate_natural_settlement.yml"
WORKFLOW_NAME = "DC20 · Settle natural candidate research slots"
WORKFLOW_ID = 357485346
VALIDATE = "Validate publication evidence preservation"
OBSERVE = "Verify and preserve original publication evidence"
TRIGGER = "Check whether the freeze trigger produced new candidate evidence"
CONTRACTS = "Check original verifier preservation and immutable Git contracts"
WORK = "Verify original publication and append isolated evidence capsule"
MAX_BYTES, MAX_PAGES = 2 * 1024**2, 10


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def exact_id(value):
    return type(value) is int and value > 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError("TRIGGER_REDIRECT_REJECTED")


class GitHubReadOnly:
    def __init__(self, token):
        require(type(token) is str and bool(token), "TRIGGER_TOKEN_REQUIRED")
        self.token, self.calls = token, 0

    def get(self, path):
        require(bool(re.fullmatch(r"/actions/(?:workflows/[1-9][0-9]*|runs/[1-9][0-9]*(?:/attempts/1/jobs\?per_page=100&page=[1-9][0-9]*)?)", path)),
                "TRIGGER_GET_PATH_REJECTED")
        self.calls += 1
        require(self.calls <= MAX_PAGES + 4, "TRIGGER_API_BUDGET_EXCEEDED")
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
            method="GET", headers={"Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=20) as response:
                require(response.status == 200, "TRIGGER_API_STATUS_REJECTED")
                raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "TRIGGER_API_RESPONSE_TOO_LARGE")
            document = json.loads(raw)
            require(type(document) is dict, "TRIGGER_API_OBJECT_REQUIRED")
            return document
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise ValueError("TRIGGER_API_READ_FAILED") from None


def registration(value, workflow_id, path, name):
    require(type(value.get("id")) is int and value["id"] == workflow_id
        and value.get("path") == path and value.get("name") == name
        and value.get("state") == "active", "TRIGGER_REGISTERED_WORKFLOW_REQUIRED")


def same_repo(run):
    require(run.get("repository", {}).get("full_name") == REPO
        and run.get("head_repository", {}).get("full_name") == REPO,
        "TRIGGER_SAME_REPOSITORY_REQUIRED")


def observer_identity(run):
    require(exact_id(run.get("id")) and type(run.get("workflow_id")) is int
        and run["workflow_id"] == OBSERVER_ID and run.get("path") == OBSERVER_PATH
        and run.get("name") == OBSERVER_NAME and run.get("head_branch") == "main"
        and bool(re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")))
        and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("event") in {"workflow_run", "workflow_dispatch"}
        and run.get("status") == "completed" and run.get("conclusion") == "success",
        "TRIGGER_SUCCESSFUL_OBSERVER_IDENTITY_REQUIRED")
    same_repo(run)


def complete_jobs(client, run):
    total, rows, ids = None, [], set()
    for page in range(1, MAX_PAGES + 1):
        doc = client.get(f'/actions/runs/{run["id"]}/attempts/1/jobs?per_page=100&page={page}')
        count, batch = doc.get("total_count"), doc.get("jobs")
        require(type(count) is int and 0 < count <= MAX_PAGES * 100 and type(batch) is list
            and len(batch) <= 100 and (total is None or total == count), "TRIGGER_JOBS_PAGE_INVALID")
        total = count
        for job in batch:
            require(type(job) is dict and exact_id(job.get("id")) and job["id"] not in ids,
                    "TRIGGER_JOB_ID_DUPLICATE_OR_INVALID")
            for key, wanted in (("run_id", run["id"]), ("run_attempt", 1), ("head_sha", run["head_sha"]),
                                ("head_branch", "main"), ("workflow_name", OBSERVER_NAME)):
                require(type(job.get(key)) is type(wanted) and job[key] == wanted,
                        "TRIGGER_JOB_IDENTITY_CHANGED")
            require(job.get("status") == "completed" and type(job.get("steps")) is list,
                    "TRIGGER_JOB_NOT_COMPLETED")
            ids.add(job["id"]); rows.append(job)
        require(len(rows) <= total, "TRIGGER_JOBS_COUNT_CHANGED")
        if len(rows) == total:
            return rows
        require(len(batch) == 100, "TRIGGER_JOBS_INCOMPLETE")
    raise ValueError("TRIGGER_JOBS_INCOMPLETE")


def successful_step(job, name):
    steps = [step for step in job["steps"] if type(step) is dict and step.get("name") == name]
    require(len(steps) == 1 and steps[0].get("status") == "completed"
        and steps[0].get("conclusion") == "success", "TRIGGER_REQUIRED_STEP_NOT_SUCCESSFUL")


def inspect_observer(event, client):
    source = event.get("workflow_run", {})
    observer_identity(source)
    registration(client.get(f"/actions/workflows/{OBSERVER_ID}"), OBSERVER_ID, OBSERVER_PATH, OBSERVER_NAME)
    run = client.get(f'/actions/runs/{source["id"]}')
    observer_identity(run)
    for key in ("id", "workflow_id", "path", "name", "head_sha", "head_branch", "run_attempt", "event", "status", "conclusion"):
        require(type(run.get(key)) is type(source.get(key)) and run[key] == source[key],
                "TRIGGER_EVENT_API_IDENTITY_CHANGED")
    jobs = complete_jobs(client, run)
    require(len(jobs) == 2 and {j.get("name") for j in jobs} == {VALIDATE, OBSERVE},
            "TRIGGER_EXACT_OBSERVER_JOBS_REQUIRED")
    validate = next(j for j in jobs if j["name"] == VALIDATE)
    observe = next(j for j in jobs if j["name"] == OBSERVE)
    require(validate.get("conclusion") == "success", "TRIGGER_VALIDATE_NOT_SUCCESSFUL")
    successful_step(validate, CONTRACTS)
    successful_step(validate, TRIGGER)
    if observe.get("conclusion") == "skipped":
        require(observe["steps"] == [], "TRIGGER_SKIPPED_OBSERVER_HAS_STEPS")
        needed, status = False, "VERIFIED_NOOP_OBSERVER_NO_SETTLEMENT"
    else:
        require(observe.get("conclusion") == "success", "TRIGGER_OBSERVER_RESULT_REJECTED")
        successful_step(observe, WORK)
        needed, status = True, "DELEGATE_TO_EXISTING_SETTLEMENT_VALIDATION"
    return {"status": status, "needs_settlement": needed, "observer_run_id": run["id"],
            "observer_head_sha": run["head_sha"], "observer_job_id": observe["id"],
            "new_evidence_admission_issued": False, "market_requests": 0, "remote_writes": 0}


def verify_current(environ, client):
    require(environ.get("GITHUB_ACTIONS") == "true" and environ.get("GITHUB_REPOSITORY") == REPO
        and environ.get("GITHUB_REF") == "refs/heads/main" and environ.get("GITHUB_EVENT_NAME") == "workflow_run"
        and environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and environ.get("GITHUB_WORKFLOW_REF") == REPO + "/" + WORKFLOW_PATH + "@refs/heads/main"
        and bool(re.fullmatch(r"[1-9][0-9]*", environ.get("GITHUB_RUN_ID", "")))
        and bool(re.fullmatch(r"[0-9a-f]{40}", environ.get("GITHUB_SHA", ""))),
        "TRIGGER_CURRENT_CONTEXT_REQUIRED")
    registration(client.get(f"/actions/workflows/{WORKFLOW_ID}"), WORKFLOW_ID, WORKFLOW_PATH, WORKFLOW_NAME)
    run = client.get('/actions/runs/' + environ["GITHUB_RUN_ID"])
    for key, wanted in (("id", int(environ["GITHUB_RUN_ID"])), ("workflow_id", WORKFLOW_ID),
            ("path", WORKFLOW_PATH), ("name", WORKFLOW_NAME), ("head_sha", environ["GITHUB_SHA"]),
            ("head_branch", "main"), ("run_attempt", 1), ("event", "workflow_run")):
        require(type(run.get(key)) is type(wanted) and run[key] == wanted, "TRIGGER_CURRENT_RUN_CHANGED")
    require(run.get("status") == "in_progress" and run.get("conclusion") is None, "TRIGGER_CURRENT_RUN_NOT_ACTIVE")
    same_repo(run)


def main():
    try:
        client = GitHubReadOnly(os.environ.get("GITHUB_TOKEN", ""))
        verify_current(os.environ, client)
        with Path(os.environ["GITHUB_EVENT_PATH"]).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "TRIGGER_EVENT_TOO_LARGE")
        event = json.loads(raw)
        require(type(event) is dict, "TRIGGER_EVENT_OBJECT_REQUIRED")
        result = inspect_observer(event, client)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write("needs_settlement=" + str(result["needs_settlement"]).lower() + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        reason = str(error)
        safe = reason if type(error) is ValueError and re.fullmatch(r"TRIGGER_[A-Z0-9_]{3,100}", reason) else "TRIGGER_REDACTED_ERROR"
        print(json.dumps({"status": "FAILED_CLOSED", "reason": safe, "remote_writes": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
