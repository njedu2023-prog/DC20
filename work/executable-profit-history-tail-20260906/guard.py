"""One request-only push after one additive isolated installation; read-only."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

PREFIX = "work/executable-profit-history-tail-20260906/"
WORKFLOW = ".github/workflows/research_profit_history_tail.yml"
REQUEST = PREFIX + "REQUEST.json"
INSTALL_BASE = "0904cb6f1fd0bc62a56d47b9a915d8c5374df076"
NONCE = "dc20-profit-history-tail-20260906-19-partitions-v1"
FILES = sorted([WORKFLOW] + [PREFIX + name for name in ("collect_tail.py", "guard.py", "PLAN.json", "README.md", "test_collect_tail.py", "test_guard.py")])
INSTALL_FILES = sorted(FILES + [REQUEST])
PREVIOUS_RUN = "34023469106"
PREVIOUS_MANIFEST_SHA = "1be7507a8fdea6764c2ce5e9e0e617f38792a5eb41971b5d3d10e1e94c7e6953"


def require(value, message):
    if not value:
        raise ValueError(message)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def validate(root, env, event, now):
    root = Path(root).resolve(strict=True)
    require(env.get("GITHUB_REPOSITORY") == "njedu2023-prog/DC20", "WRONG_REPOSITORY")
    require(env.get("GITHUB_EVENT_NAME") == "push" and env.get("GITHUB_REF") == "refs/heads/main", "REQUEST_MAIN_PUSH_ONLY")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1", "RERUN_FORBIDDEN")
    require(datetime(2026, 9, 6, 10, tzinfo=timezone.utc) <= now < datetime(2026, 9, 6, 14, tzinfo=timezone.utc), "OUTSIDE_FIXED_TAIL_WINDOW")
    require(not event.get("deleted") and not event.get("forced"), "DELETED_OR_FORCED_PUSH")
    require(event.get("repository", {}).get("full_name") == env["GITHUB_REPOSITORY"] and event.get("ref") == env["GITHUB_REF"], "EVENT_SCOPE_MISMATCH")
    head = git(root, "rev-parse", "HEAD")
    require(head == env.get("GITHUB_SHA") == event.get("after"), "CHECKOUT_SHA_MISMATCH")
    require(not git(root, "status", "--porcelain=v1"), "DIRTY_CHECKOUT")
    path = root / REQUEST
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "UNSAFE_REQUEST")
    activation = json.loads(path.read_text())["_activation"]
    require(activation.get("state") == "ACTIVATED" and activation.get("request_nonce") == NONCE, "REQUEST_NOT_ACTIVATED")
    install = activation.get("installation_commit")
    require(isinstance(install, str) and re.fullmatch(r"[0-9a-f]{40}", install), "INSTALLATION_SHA_INVALID")
    require(activation.get("installation_base") == INSTALL_BASE, "INSTALLATION_BASE_CHANGED")
    require(activation.get("previous_run_id") == PREVIOUS_RUN and activation.get("previous_manifest_sha256") == PREVIOUS_MANIFEST_SHA
            and activation.get("old_failure_status_preserved") is True, "PREVIOUS_FAILURE_BINDING_CHANGED")
    require(git(root, "rev-list", "--parents", "-n", "1", head).split() == [head, install] and event.get("before") == install, "ACTIVATION_NOT_DIRECT_INSTALL_CHILD")
    require(git(root, "rev-list", "--parents", "-n", "1", install).split() == [install, INSTALL_BASE], "INSTALLATION_NOT_DIRECT_BASE_CHILD")
    require("[skip ci]" in git(root, "show", "-s", "--format=%B", install), "INSTALLATION_NOT_ISOLATED")
    require(git(root, "diff", "--name-status", install, head) == "M\t" + REQUEST, "ACTIVATION_NOT_REQUEST_ONLY")
    require(sorted(git(root, "diff", "--name-status", INSTALL_BASE, install).splitlines()) == ["A\t" + name for name in INSTALL_FILES], "INSTALLATION_NOT_ADDITIVE_TAIL_ONLY")
    pins = activation.get("files_sha256", {})
    require(set(pins) == set(FILES), "ACTIVATION_PINS_INCOMPLETE")
    for relative, digest in pins.items():
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "FILE_PIN_INVALID")
        file = root / relative
        require(file.is_file() and not any(p.is_symlink() for p in (file, *file.parents)), "UNSAFE_TAIL_SOURCE")
        require(hashlib.sha256(file.read_bytes()).hexdigest() == digest, "TAIL_SOURCE_PIN_MISMATCH")
    return {"status": "ONE_TIME_TAIL_SOURCE_REQUEST_AUTHORIZED", "installation_base": INSTALL_BASE,
            "installation_commit": install, "execution_commit": head, "previous_run_id": PREVIOUS_RUN,
            "old_failure_status_preserved": True, "only_missing_required_partitions": 19,
            "production_writer_invoked": False, "run_attempt": 1}


def main():
    root = Path(__file__).resolve().parents[2]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    print(json.dumps(validate(root, os.environ, event, datetime.now(timezone.utc)), sort_keys=True))


if __name__ == "__main__":
    main()
