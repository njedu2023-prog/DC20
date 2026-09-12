"""Ten-minute evidence window; never a replacement for the required core CI.

Only the process group created here is signalled. The child leader is observed
with waitid(WNOWAIT), not reaped before group cleanup, so its PID cannot be
reused for an unrelated process group. Output goes directly to regular files;
an inherited stdout descriptor cannot keep a PIPE/tee reader waiting forever.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


WINDOW_SECONDS = 600
PROGRESS_SECONDS = 10
TERM_GRACE_SECONDS = 5
KILL_WAIT_SECONDS = 5
MAX_TAIL_BYTES = 4096
CHECKOUT = Path(__file__).resolve().parents[1]
SCHEMA = "dc20_core_short_diagnostic_supervisor_v1"


def pytest_command(output):
    return [sys.executable, "-u", "-m", "pytest", "-vv", "--tb=short", "--durations=25",
            "-o", "faulthandler_timeout=120", "--ignore=tests/test_decision_source_surface_rotation.py",
            "--junitxml=" + str(Path(output) / "core-regression.xml")]


def _fresh_output(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("fresh unaliased absolute output required")
    parent = path.parent.resolve(strict=True)
    path = parent / path.name
    if path == CHECKOUT or CHECKOUT in path.parents or path in CHECKOUT.parents:
        raise ValueError("diagnostic output must be outside checkout")
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def _tail(handle):
    # Read the already-owned descriptor, never reopen a child-replaceable path.
    # pread does not move the offset shared by the child's log writes.
    size = os.fstat(handle.fileno()).st_size
    raw = os.pread(handle.fileno(), MAX_TAIL_BYTES, max(0, size - MAX_TAIL_BYTES))
    return size, raw.decode("utf-8", errors="replace")


def _leader_status(child):
    # WNOWAIT keeps the child (including a zombie leader) owned and unreaped.
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def _signal_owned_group(child, sig):
    _leader_status(child)  # ECHILD forbids any signal if ownership was lost.
    if child.pid <= 1 or child.pid == os.getpgrp():
        raise RuntimeError("refusing non-isolated process group")
    if os.getpgid(child.pid) != child.pid or os.getsid(child.pid) != child.pid:
        raise RuntimeError("created process group identity changed")
    os.killpg(child.pid, sig)


def _bounded_leader_wait(child, seconds):
    deadline = time.monotonic() + seconds
    while True:
        status = _leader_status(child)
        if status is not None:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(.05, remaining))


def _stop_group(child, term_grace, kill_wait):
    result = {"term_sent": False, "kill_sent": False, "leader_reaped": False,
              "cleanup_error": None, "child_returncode": None}
    try:
        _signal_owned_group(child, signal.SIGTERM)
        result["term_sent"] = True
        _bounded_leader_wait(child, term_grace)
        # Always clean remaining members even when the leader already exited.
        # Its unreaped PID still pins the identity of the group being killed.
        _signal_owned_group(child, signal.SIGKILL)
        result["kill_sent"] = True
        result["child_returncode"] = child.wait(timeout=kill_wait)
        result["leader_reaped"] = True
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        result["cleanup_error"] = type(error).__name__
    return result


class _Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


def supervise(command, *, output, cwd, window_seconds=WINDOW_SECONDS,
              progress_seconds=PROGRESS_SECONDS, term_grace_seconds=TERM_GRACE_SECONDS,
              kill_wait_seconds=KILL_WAIT_SECONDS):
    """Internal harness; the CLI fixes the complete selector and 600s window.

    Tests use tiny local child fixtures and smaller positive bounds. No shell,
    environment snapshot, broad process matching, market access or retry is
    performed. Escaped sessions are not claimed to be tracked or terminated.
    """
    if not all(type(v) in (int, float) and 0 < v <= limit for v, limit in (
            (window_seconds, WINDOW_SECONDS), (progress_seconds, PROGRESS_SECONDS),
            (term_grace_seconds, TERM_GRACE_SECONDS), (kill_wait_seconds, KILL_WAIT_SECONDS))):
        raise ValueError("invalid bounded diagnostic timing")
    if type(command) is not list or not command or not all(type(v) is str and v for v in command):
        raise ValueError("explicit nonempty argument vector required")
    if not sys.platform.startswith("linux") or not callable(getattr(os, "waitid", None)):
        raise RuntimeError("Linux waitid(WNOWAIT) required before launching a child")
    for flag in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"):
        if not hasattr(os, flag):
            raise RuntimeError("non-reaping child observation unavailable")
    destination = _fresh_output(output)
    log_path, progress_path = destination / "core-regression.log", destination / "core-progress.jsonl"
    child, started = None, time.monotonic()
    report = {"schema_version": SCHEMA, "diagnostic_only": True,
              "replaces_required_core_ci": False, "full_core_pass_claimed": False,
              "timeout_meaning": "DIAGNOSTIC_COLLECTION_WINDOW_EXHAUSTED_NOT_FULL_CORE_RESULT",
              "containment_scope": "CREATED_PROCESS_GROUP_ONLY_ESCAPED_SESSIONS_NOT_TRACKED",
              "window_seconds": window_seconds, "term_grace_seconds": term_grace_seconds,
              "kill_wait_seconds": kill_wait_seconds, "status": "SUPERVISOR_ERROR", "exit_code": 70,
              "timed_out": False, "interrupt_signal": None, "child_pid": None,
              "command": command, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "error_class": None, "cleanup": None}
    old_handlers = {}
    def interrupted(signum, _frame):
        raise _Interrupted(signum)
    with log_path.open("x+b", buffering=0) as log, progress_path.open("x", encoding="utf-8") as progress:
        def record(event):
            size, tail = _tail(log)
            row = {"event": event, "elapsed_seconds": round(time.monotonic() - started, 6),
                   "child_pid": report["child_pid"], "log_bytes": size, "log_tail": tail}
            progress.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            progress.flush(); os.fsync(progress.fileno())
            print(f"core diagnostic: {event}, elapsed={row['elapsed_seconds']:.1f}s, log_bytes={size}", flush=True)
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                old_handlers[sig] = signal.signal(sig, interrupted)
            record("STARTING")
            child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            report["child_pid"] = child.pid
            next_progress = time.monotonic()
            while True:
                status = _leader_status(child)
                if status is not None:
                    report["status"] = "CHILD_FINISHED"
                    break
                now = time.monotonic()
                if now - started >= window_seconds:
                    report.update(status="DIAGNOSTIC_WINDOW_TIMEOUT", timed_out=True, exit_code=124)
                    break
                if now >= next_progress:
                    record("RUNNING")
                    next_progress = now + progress_seconds
                time.sleep(min(.05, max(.001, window_seconds - (now - started))))
        except _Interrupted as error:
            report.update(status="SUPERVISOR_INTERRUPTED", interrupt_signal=error.signum, exit_code=128 + error.signum)
        except Exception as error:
            report["error_class"] = type(error).__name__
        finally:
            # Ignore repeat termination while performing bounded cleanup; no
            # unbounded wait or pipe-drain is introduced in this path.
            for sig in old_handlers:
                signal.signal(sig, signal.SIG_IGN)
            if child is not None:
                report["cleanup"] = _stop_group(child, term_grace_seconds, kill_wait_seconds)
                if report["status"] == "CHILD_FINISHED":
                    code = report["cleanup"]["child_returncode"]
                    if report["cleanup"]["cleanup_error"] is None and type(code) is int:
                        report["exit_code"] = code if code >= 0 else 128 - code
                    else:
                        report.update(status="SUPERVISOR_CLEANUP_ERROR", exit_code=70)
            report["elapsed_seconds"] = round(time.monotonic() - started, 6)
            record("FINISHED")
            with (destination / "core-supervisor.json").open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        return supervise(pytest_command(args.output), output=args.output, cwd=CHECKOUT)["exit_code"]
    except Exception:
        print("CORE_DIAGNOSTIC_SUPERVISOR_FAILED", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
