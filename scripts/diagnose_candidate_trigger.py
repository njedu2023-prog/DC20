"""Read-only explanation of one of the two recorded duplicate P0 gate failures."""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_P0_RUNS = {"42723200937", "42726704719"}
REPOSITORY = "njedu2023-prog/DC20"
WORKFLOW_PATH = ".github/workflows/diagnose_candidate_trigger.yml"
IDENT = re.compile(r"[A-Z][A-Z0-9_]{2,120}")
SHA = re.compile(r"[0-9a-f]{40}")

def require(value, code):
    if not value:
        raise ValueError(code)

def static_codes(root):
    """Only literal identifiers in checked-out trusted source may reach logs."""
    result = set()
    for directory in ("scripts", "work/profit_1000_upgrade", "src/top10decision"):
        for path in (root / directory).rglob("*.py"):
            if path.is_symlink():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            result.update(node.value for node in ast.walk(tree)
                          if isinstance(node, ast.Constant) and isinstance(node.value, str)
                          and IDENT.fullmatch(node.value))
    return result

def safe_reason(error, codes):
    value = str(error)
    return value if value in codes else "REDACTED_NON_STATIC_EXCEPTION"

def verify_context(environ, expected_head, p0_run_id):
    require(p0_run_id in ALLOWED_P0_RUNS, "DIAGNOSTIC_P0_RUN_NOT_ALLOWED")
    require(bool(SHA.fullmatch(expected_head)), "DIAGNOSTIC_EXPECTED_HEAD_INVALID")
    require(environ.get("GITHUB_ACTIONS") == "true"
        and environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and environ.get("GITHUB_REF") == "refs/heads/main"
        and environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and environ.get("GITHUB_SHA") == expected_head
        and bool(re.fullmatch(r"[1-9][0-9]*", environ.get("GITHUB_RUN_ID", ""))),
        "DIAGNOSTIC_EXACT_MANUAL_MAIN_CONTEXT_REQUIRED")

def validate_actual_run(run, environ, expected_head):
    require(type(run.get("id")) is int and run["id"] == int(environ["GITHUB_RUN_ID"])
        and run.get("path") == WORKFLOW_PATH and run.get("head_branch") == "main"
        and run.get("head_sha") == expected_head and run.get("event") == "workflow_dispatch"
        and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "in_progress" and run.get("conclusion") is None
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY,
        "DIAGNOSTIC_ACTUAL_RUN_IDENTITY_REQUIRED")

def exception_trace(root, codes, trace_events):
    """Trace project exceptions without tracing millions of parsing-loop lines."""
    prefix = str(root) + os.sep
    project_prefixes = tuple(str(root / p) + os.sep for p in
                             ("scripts", "work/profit_1000_upgrade", "src/top10decision"))
    def trace(frame, event, arg):
        filename = frame.f_code.co_filename
        if not filename.startswith(project_prefixes):
            return None
        if event == "call":
            frame.f_trace_lines = False
            frame.f_trace_opcodes = False
        elif event == "exception":
            reason = safe_reason(arg[1], codes)
            if reason != "REDACTED_NON_STATIC_EXCEPTION":
                record = {"source": filename[len(prefix):], "line": frame.f_lineno, "reason": reason}
                if not trace_events or trace_events[-1] != record:
                    trace_events.append(record)
                    del trace_events[:-24]
        return trace
    return trace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p0-run-id", required=True, choices=sorted(ALLOWED_P0_RUNS))
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    codes = static_codes(ROOT)
    stage = "CONTEXT"
    trace_events = []
    trace = exception_trace(ROOT, codes, trace_events)
    previous_trace = sys.gettrace()
    try:
        verify_context(os.environ, args.expected_head, args.p0_run_id)
        stage = "IMPORT_ORIGINAL_CODE"
        sys.settrace(trace)
        from work.profit_1000_upgrade import candidate_natural_trigger_gate as gate
        _, gh = gate.workflow.dependencies()
        client = gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV, ""))
        stage = "VERIFY_DIAGNOSTIC_API_IDENTITY"
        run = client.get_json(gh.API_PREFIX + "/actions/runs/" + os.environ["GITHUB_RUN_ID"])
        validate_actual_run(run, os.environ, args.expected_head)
        head = client.get_json(gh.API_PREFIX + "/git/ref/heads/main")
        require(head.get("ref") == "refs/heads/main"
            and head.get("object", {}).get("type") == "commit"
            and head["object"].get("sha") == args.expected_head, "DIAGNOSTIC_CURRENT_MAIN_MOVED")
        # Use a fresh instance so diagnostic API reads do not consume the
        # original gate's bounded read budget or change any original behavior.
        original_client = gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV, ""))
        stage = "ORIGINAL_DUPLICATE_P0_PREFLIGHT"
        result = gate.inspect_p0(run_id=args.p0_run_id, github_client=original_client)
        stage = "FINAL_CURRENT_MAIN_CHECK"
        final_head = client.get_json(gh.API_PREFIX + "/git/ref/heads/main")
        require(final_head == head, "DIAGNOSTIC_CURRENT_MAIN_MOVED")
        print(json.dumps({"status": "READ_ONLY_PREFLIGHT_PASSED",
            "p0_run_id": int(args.p0_run_id), "expected_head": args.expected_head,
            "preflight_status": result["status"], "signal_date": result["signal_date"],
            "needs_work": result["needs_work"], "actions_performed": "GET_ONLY_NO_FREEZE_NO_PUBLISH"}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "READ_ONLY_PREFLIGHT_BLOCKED", "stage": stage,
            "reason": safe_reason(error, codes), "trace": trace_events,
            "p0_run_id": int(args.p0_run_id), "expected_head": args.expected_head,
            "actions_performed": "GET_ONLY_NO_FREEZE_NO_PUBLISH"}, sort_keys=True))
        return 1
    finally:
        sys.settrace(previous_trace)

if __name__ == "__main__":
    raise SystemExit(main())
