"""Read-only natural-schedule input acceptance; no inference or production writes.

GitHub supplies the event envelope. Its run API supplies immutable created_at.
Only after both identities and the committed calendar pass may upstream main
refs be resolved, once each, to immutable source commits. This entry point is
not a production scheduler, model adapter, list publisher, or shadow writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .schedule import read_calendar
from .storage import encoded
from .trigger import bind_schedule
from .inputs import FetchError

REPOSITORY = "njedu2023-prog/DC20"
WORKFLOW = ".github/workflows/accept_forward_inputs.yml"
UPSTREAMS = ("njedu2023-prog/a-top10", "njedu2023-prog/a-share-top3-data")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect refused by read-only source allowlist")


def fetch_bytes(url, *, token=""):
    """GET only, bounded retries; credentials never leave api.github.com."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in
            {"api.github.com", "raw.githubusercontent.com"} or parsed.query or parsed.fragment):
        raise ValueError("source URL is outside the HTTPS allowlist")
    if parsed.netloc == "api.github.com":
        patterns = [rf"/repos/{REPOSITORY}/actions/runs/[1-9][0-9]*",
                    rf"/repos/{REPOSITORY}/actions/workflows/[1-9][0-9]*"]
        patterns += [rf"/repos/{repo}/git/ref/heads/main" for repo in UPSTREAMS]
        if not any(re.fullmatch(pattern, parsed.path) for pattern in patterns):
            raise ValueError("API path is outside the read-only allowlist")
    elif not any(re.fullmatch(rf"/{repo}/[0-9a-f]{{40}}/[A-Za-z0-9_./-]+", parsed.path)
                 for repo in UPSTREAMS) or any(part in {".", ".."} for part in parsed.path.split("/")):
        raise ValueError("raw source requires an approved repository and immutable commit")
    headers = {"User-Agent": "DC20-forward-input-acceptance"}
    if parsed.netloc == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if token:
            headers["Authorization"] = "Bearer " + token
    opener = build_opener(NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(Request(url, headers=headers, method="GET"), timeout=40) as response:
                content = response.read(64 * 1024 * 1024 + 1)
                if len(content) > 64 * 1024 * 1024:
                    raise ValueError("source exceeds input size limit")
                return content
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise FetchError(f"source GET failed: HTTP {exc.code}", status=exc.code) from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise FetchError("source GET unavailable after bounded retries") from None
        time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def validate_run(env, event, run, workflow, checkout_sha):
    """Check trusted Actions envelope against independently read API identity."""
    expected_ref = f"{REPOSITORY}/{WORKFLOW}@refs/heads/main"
    if (env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "schedule"
            or env.get("GITHUB_WORKFLOW_REF") != expected_ref):
        raise ValueError("not the approved repository/main natural workflow envelope")
    if not SHA.fullmatch(checkout_sha) or env.get("GITHUB_SHA") != checkout_sha or env.get("GITHUB_WORKFLOW_SHA") != checkout_sha:
        raise ValueError("checkout, event and workflow source SHA differ")
    run_id = env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("invalid run id or repeated attempt")
    cron = env.get("FORWARD_SCHEDULE")
    if not isinstance(event, dict) or event.get("schedule") != cron:
        raise ValueError("natural event schedule envelope mismatch")
    if (type(run.get("id")) is not int or run.get("id") != int(run_id)
            or type(run.get("run_attempt")) is not int
            or run.get("run_attempt") != 1 or run.get("event") != "schedule"
            or run.get("head_sha") != checkout_sha or run.get("head_branch") != "main"
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("path") != WORKFLOW or run.get("status") != "in_progress"):
        raise ValueError("GitHub run API identity does not match this first natural run")
    if (type(workflow.get("id")) is not int or workflow["id"] <= 0
            or type(run.get("workflow_id")) is not int or workflow.get("id") != run.get("workflow_id")
            or workflow.get("path") != WORKFLOW or workflow.get("state") != "active"):
        raise ValueError("workflow identity/state mismatch")
    return {"run_id": int(run_id), "run_attempt": 1,
            "workflow_id": workflow["id"], "workflow_path": WORKFLOW,
            "source_revision": checkout_sha, "event_name": "schedule", "schedule": cron,
            "run_created_at_utc": run.get("created_at"),
            "url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"}


def resolve_source(repo, get):
    if repo not in UPSTREAMS:
        raise ValueError("unapproved upstream")
    value = json.loads(get(f"https://api.github.com/repos/{repo}/git/ref/heads/main"))
    obj = value.get("object", {})
    if value.get("ref") != "refs/heads/main" or obj.get("type") != "commit" or not SHA.fullmatch(obj.get("sha", "")):
        raise ValueError("upstream main does not resolve to an immutable commit")
    return obj["sha"]


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _checkout(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("root must be the reviewed repository checkout")
    if git("status", "--porcelain=v1", "--untracked-files=no"):
        raise ValueError("tracked checkout differs from reviewed commit")
    return git("rev-parse", "HEAD")


def accept_inputs(root, output, *, env=None, get=None, clock=_utc, collector=None, checkout=_checkout):
    """Write only an external evidence artifact, including explicit CLOSED exits."""
    from .inputs import collect_inputs
    root = Path(root).resolve(strict=True)
    output = Path(output)
    if not output.is_absolute() or output.resolve() != output or output.is_relative_to(root):
        raise ValueError("output must be a physical absolute directory outside the repository")
    if output.exists():
        raise ValueError("input acceptance output must be new; no overwrite")
    env = os.environ if env is None else env
    get = get or (lambda url: fetch_bytes(url, token=env.get("GITHUB_TOKEN", "")))
    collector = collector or collect_inputs
    config = json.loads((root / "forward/config.json").read_bytes())
    if (config.get("production_enabled") is not False or config.get("activated_at_utc") is not None
            or config.get("start_signal_date") is not None or config.get("phase") != "MIGRATION_ACCEPTANCE"
            or config.get("formal_trade_actions_allowed") is not False
            or config.get("legacy_statistics_import_allowed") is not False):
        raise ValueError("this read-only entry point requires an inactive migration config")
    dates = read_calendar(root / config["calendar_path"], config["calendar_sha256"])
    source_revision = checkout(root)
    run_id = env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("invalid run id")
    # Only the DC20 run/workflow identity API is read before the calendar gate.
    run = json.loads(get(f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}"))
    workflow_id = run.get("workflow_id")
    if type(workflow_id) is not int or workflow_id <= 0:
        raise ValueError("invalid workflow id in run response")
    workflow = json.loads(get(f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{workflow_id}"))
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    identity = validate_run(env, event, run, workflow, source_revision)
    def gate():
        return bind_schedule("schedule", identity["schedule"], identity["run_created_at_utc"], clock(), dates)
    bound = gate()
    result = {"schema_version": "dc20_forward_input_acceptance_v1", "identity": identity,
              "gate": bound, "started_at_utc": bound["checked_at_utc"],
              "read_only": True, "production_activated": False,
              "inference_performed": False, "ledger_written": False, "published_list": False}
    if bound["status"] == "CLOSED":
        result.update(status="CLOSED", sources={}, bundle_sha256=None)
    else:
        sources = {repo: resolve_source(repo, get) for repo in UPSTREAMS}
        gate()  # Ref lookup delays do not extend the original slot.
        output.mkdir(parents=True, exist_ok=False)
        manifest = collector(root, output / "bundle", bound["signal_date"],
                             sources[UPSTREAMS[0]], sources[UPSTREAMS[1]], fetch=get, now_utc=clock())
        if checkout(root) != source_revision:
            raise ValueError("checkout changed during input acceptance")
        manifest_path = output / "bundle" / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("collector completion manifest missing")
        manifest_bytes = manifest_path.read_bytes()
        if json.loads(manifest_bytes) != manifest:
            raise ValueError("collector manifest differs from returned evidence")
        result.update(status="INPUTS_VALIDATED_NOT_PRODUCTION", sources=sources,
                      bundle_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    if not output.exists():
        output.mkdir(parents=True, exist_ok=False)
    # Recheck after all collection, checkout and manifest IO; none of that work
    # may extend the original slot's admission boundary.
    result["gate"] = gate()
    result["finished_at_utc"] = result["gate"]["checked_at_utc"]
    # The completion receipt is last; a partial bundle is never a successful run.
    with (output / "receipt.json").open("xb") as handle:
        handle.write(encoded(result))
    return result


def emit_job_outputs(values, env=None):
    """Publish bounded evidence scalars, never untrusted multiline output."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") != "true" or not env.get("GITHUB_OUTPUT"):
        return
    allowed = {"status", "manifest_sha256", "bundle_sha256", "receipt_sha256"}
    if set(values) - allowed or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_]*", v) for v in values.values()):
        raise ValueError("unsafe job output value")
    for key, value in values.items():
        if key == "status":
            if not 1 <= len(value) <= 64:
                raise ValueError("job output status length invalid")
        elif not re.fullmatch(r"[0-9a-f]{64}", value) and not (key == "bundle_sha256" and value == ""):
            raise ValueError("job output digest invalid")
    descriptor = os.open(env["GITHUB_OUTPUT"], os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "a") as handle:
        handle.write("".join(f"{key}={value}\n" for key, value in values.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = accept_inputs(args.root, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"BLOCK: {exc}\n")
    emit_job_outputs({"status": receipt["status"], "bundle_sha256": receipt["bundle_sha256"] or "",
                      "receipt_sha256": hashlib.sha256((args.output / "receipt.json").read_bytes()).hexdigest()})
    print(json.dumps({"status": receipt["status"], "signal_date": receipt["gate"]["signal_date"],
                      "late": receipt["gate"]["late"],
                      "primary_deadline_missed": receipt["gate"]["primary_deadline_missed"],
                      "production_activated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
