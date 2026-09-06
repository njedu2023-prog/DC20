"""Authorize one immutable research-data request; no credentials or mutations."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "work/executable-profit-history-source-20260906/"
WORKFLOW = ".github/workflows/research_profit_history_source.yml"
REQUEST = PREFIX + "REQUEST.json"
BASE = "3e2299a07f7b4430002da0b870c47ecf57c49bb3"
INSTALL_BASE = "8c12086f24c92463d569861d5637d5de1f55805b"
FAILED_COLLECTION_RUN = 34022598288
FAILED_MANIFEST_SHA = "6b6dc4651a73eaeb44e76953266b11a8d2fc71a7a6d38c16eee43cc7b2b523a0"
INSTALL_UPDATES = sorted(PREFIX + name for name in (
    "README.md", "collect.py", "guard.py", "test_collect.py", "test_guard.py"
))
FILES = sorted([WORKFLOW] + [PREFIX + name for name in (
    "PLAN.json", "README.md", "collect.py", "guard.py", "test_collect.py", "test_guard.py"
)])


def require(value, reason):
    if not value:
        raise ValueError(reason)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def validate(root, env, event, now):
    root = Path(root).resolve(strict=True)
    require(env.get("GITHUB_REPOSITORY") == "njedu2023-prog/DC20", "WRONG_REPOSITORY")
    require(env.get("GITHUB_EVENT_NAME") == "push", "PUSH_ONLY")
    require(env.get("GITHUB_REF") == "refs/heads/main", "MAIN_ONLY")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1", "RERUN_FORBIDDEN")
    require(datetime(2026, 9, 6, 8, tzinfo=timezone.utc) <= now < datetime(2026, 9, 6, 12, tzinfo=timezone.utc), "OUTSIDE_ONE_TIME_RESEARCH_WINDOW")
    require(not event.get("deleted") and not event.get("forced"), "DELETED_OR_FORCED_PUSH")
    require(event.get("repository", {}).get("full_name") == env["GITHUB_REPOSITORY"], "EVENT_REPOSITORY_MISMATCH")
    require(event.get("ref") == env["GITHUB_REF"], "EVENT_REF_MISMATCH")
    head = git(root, "rev-parse", "HEAD")
    require(not git(root, "status", "--porcelain=v1"), "DIRTY_CHECKOUT_BEFORE_COLLECTION")
    require(head == env.get("GITHUB_SHA") == event.get("after"), "CHECKOUT_SHA_MISMATCH")
    path = root / REQUEST
    require(path.is_file() and not path.is_symlink(), "UNSAFE_REQUEST")
    request = json.loads(path.read_text())
    activation = request["_activation"]
    install = activation["installation_commit"]
    require(isinstance(install, str) and re.fullmatch(r"[0-9a-f]{40}", install), "INVALID_INSTALLATION_SHA")
    require(activation.get("source_commit") == BASE, "SOURCE_COMMIT_DRIFT")
    require(activation.get("request_nonce") == "dc20-profit-history-source-20260906-null-evidence-v3", "REQUEST_NONCE_DRIFT")
    require(activation.get("supersedes_collection_run_id") == FAILED_COLLECTION_RUN
            and activation.get("previous_collection_started") is True
            and activation.get("previous_manifest_sha256") == FAILED_MANIFEST_SHA
            and activation.get("previous_requests_attempted") == 83
            and activation.get("previous_sessions_completed") == 25
            and activation.get("previous_artifact_reused_for_training") is False,
            "UNREVIEWED_NULL_EVIDENCE_CORRECTION")
    require(git(root, "rev-list", "--parents", "-n", "1", head).split() == [head, install], "ACTIVATION_NOT_DIRECT_SINGLE_CHILD")
    require(event.get("before") == install, "PUSH_BEFORE_NOT_INSTALLATION")
    require(git(root, "rev-list", "--parents", "-n", "1", install).split() == [install, INSTALL_BASE], "INSTALLATION_NOT_DIRECT_BASE_CHILD")
    require("[skip ci]" in git(root, "show", "-s", "--format=%B", install), "INSTALLATION_MISSING_PRODUCTION_TRIGGER_ISOLATION")
    require(git(root, "diff", "--name-status", install, head) == "M\t" + REQUEST, "ACTIVATION_DIFF_NOT_REQUEST_ONLY")
    installed = git(root, "diff", "--name-status", INSTALL_BASE, install).splitlines()
    require(sorted(installed) == ["M\t" + name for name in INSTALL_UPDATES], "INSTALLATION_DIFF_NOT_RESEARCH_ONLY")
    require(set(activation["files_sha256"]) == set(FILES), "INCOMPLETE_RESEARCH_HASH_BINDINGS")
    for relative, expected in activation["files_sha256"].items():
        require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "INVALID_RESEARCH_FILE_SHA")
        target = root / relative
        require(target.is_file() and not any(p.is_symlink() for p in (target, *target.parents)), "UNSAFE_RESEARCH_SOURCE")
        require(hashlib.sha256(target.read_bytes()).hexdigest() == expected, "RESEARCH_SOURCE_HASH_MISMATCH")
    return {"status": "ONE_TIME_RESEARCH_REQUEST_AUTHORIZED", "source_commit": BASE, "installation_commit": install,
            "execution_commit": head, "supersedes_collection_run": FAILED_COLLECTION_RUN,
            "previous_collection_started": True, "previous_manifest_sha256": FAILED_MANIFEST_SHA,
            "previous_artifact_reused_for_training": False, "production_writer_invoked": False, "run_attempt": 1}


def main():
    root = Path(__file__).resolve().parents[2]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    print(json.dumps(validate(root, os.environ, event, datetime.now(timezone.utc)), sort_keys=True))


if __name__ == "__main__":
    main()
