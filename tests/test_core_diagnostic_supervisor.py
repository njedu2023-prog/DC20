"""Tiny real local children; never evidence that the complete core CI passed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts import diagnose_core_supervisor as supervisor

NATIVE = pytest.mark.skipif(not sys.platform.startswith("linux") or not callable(getattr(os, "waitid", None)),
    reason="Requires the actual Linux waitid(WNOWAIT) process-group boundary; no macOS simulation claim")


def run(tmp_path, body, *, window=1):
    return supervisor.supervise([sys.executable, "-u", "-c", body], output=tmp_path / "evidence",
        cwd=tmp_path, window_seconds=window, progress_seconds=.05,
        term_grace_seconds=.1, kill_wait_seconds=.5)


def report(tmp_path):
    return json.loads((tmp_path / "evidence/core-supervisor.json").read_text())


@pytest.mark.parametrize("code", [0, 1, 2, 124, 137])
@NATIVE
def test_finished_child_exit_not_masked_and_no_full_ci_claim(tmp_path, code):
    value = run(tmp_path, f"import sys; print('tiny fixture progress'); sys.exit({code})")
    assert value == report(tmp_path)
    assert value["status"] == "CHILD_FINISHED" and value["exit_code"] == code
    assert value["cleanup"]["leader_reaped"] and value["cleanup"]["cleanup_error"] is None
    assert not value["timed_out"] and not value["full_core_pass_claimed"]
    assert not value["replaces_required_core_ci"]
    assert "tiny fixture progress" in (tmp_path / "evidence/core-regression.log").read_text()


@pytest.mark.parametrize("ignore_term", [False, True])
@NATIVE
def test_timeout_124_reaps_owned_process_and_preserves_progress(tmp_path, ignore_term):
    value = run(tmp_path, "import signal,time\n" + (
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "") +
        "print('waiting fixture', flush=True)\ntime.sleep(30)\n", window=.15)
    assert value["timed_out"] and value["exit_code"] == 124
    assert value["status"] == "DIAGNOSTIC_WINDOW_TIMEOUT"
    assert value["cleanup"]["term_sent"] and value["cleanup"]["kill_sent"]
    assert value["cleanup"]["leader_reaped"] and value["elapsed_seconds"] < 3
    with pytest.raises(ChildProcessError): os.waitpid(value["child_pid"], os.WNOHANG)
    assert "waiting fixture" in (tmp_path / "evidence/core-regression.log").read_text()


@NATIVE
def test_stdout_inheriting_descendant_cannot_keep_supervisor_waiting(tmp_path):
    body = ("import subprocess,sys\n"
            "child=subprocess.Popen([sys.executable,'-u','-c',"
            "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'])\n"
            "print('descendant',child.pid,flush=True)\n")
    value = run(tmp_path, body)
    assert value["exit_code"] == 0 and value["elapsed_seconds"] < 3
    assert value["cleanup"]["kill_sent"]
    assert "descendant" in (tmp_path / "evidence/core-regression.log").read_text()


@NATIVE
def test_child_not_reaped_before_group_signals(tmp_path, monkeypatch):
    original_signal, original_wait = supervisor._signal_owned_group, subprocess.Popen.wait
    events = []
    def tracked_signal(child, sig):
        assert child.returncode is None
        supervisor._leader_status(child)
        events.append(sig)
        return original_signal(child, sig)
    def tracked_wait(child, *args, **kwargs):
        assert events == [signal.SIGTERM, signal.SIGKILL]
        assert kwargs["timeout"] == .5
        return original_wait(child, *args, **kwargs)
    monkeypatch.setattr(supervisor, "_signal_owned_group", tracked_signal)
    monkeypatch.setattr(subprocess.Popen, "wait", tracked_wait)
    assert run(tmp_path, "pass")["exit_code"] == 0


@pytest.mark.parametrize("lost", ["already_reaped", "wrong_group", "wrong_session", "own_group"])
def test_identity_loss_never_signals_unrelated_group(monkeypatch, lost):
    class Child: pid = 998877
    def status(_):
        if lost == "already_reaped": raise ChildProcessError("not our child")
        return None
    monkeypatch.setattr(supervisor, "_leader_status", status)
    monkeypatch.setattr(os, "getpgrp", lambda: Child.pid if lost == "own_group" else 22)
    monkeypatch.setattr(os, "getpgid", lambda _: 23 if lost == "wrong_group" else Child.pid)
    monkeypatch.setattr(os, "getsid", lambda _: 24 if lost == "wrong_session" else Child.pid)
    monkeypatch.setattr(os, "killpg", lambda *_: pytest.fail("unowned group was signalled"))
    with pytest.raises((ChildProcessError, RuntimeError)):
        supervisor._signal_owned_group(Child(), signal.SIGKILL)


@NATIVE
def test_launch_failure_nonzero_retains_report(tmp_path):
    value = supervisor.supervise([str(tmp_path / "missing")], output=tmp_path / "evidence", cwd=tmp_path)
    assert value["exit_code"] == 70 and value["error_class"] == "FileNotFoundError"
    assert report(tmp_path) == value and value["cleanup"] is None


@NATIVE
def test_cleanup_failure_nonzero_without_retry(tmp_path, monkeypatch):
    original = supervisor._stop_group
    def cleanup(child, term, kill):
        value = original(child, term, kill)
        return dict(value, cleanup_error="TimeoutExpired", child_returncode=None, leader_reaped=False)
    monkeypatch.setattr(supervisor, "_stop_group", cleanup)
    value = run(tmp_path, "pass")
    assert value["exit_code"] == 70 and value["status"] == "SUPERVISOR_CLEANUP_ERROR"


def test_large_log_tail_is_bounded(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"x" * 1_000_000 + b"last progress\n")
    with path.open("rb") as handle:
        size, tail = supervisor._tail(handle)
    assert size == 1_000_014 and len(tail.encode()) <= supervisor.MAX_TAIL_BYTES
    assert tail.endswith("last progress\n")


@pytest.mark.parametrize("kind", ["existing", "symlink", "relative", "parent_traversal", "checkout"])
def test_output_fresh_external_nonaliased(tmp_path, kind):
    path = tmp_path / "new"
    if kind == "existing": path.mkdir()
    if kind == "symlink": path.symlink_to(tmp_path, target_is_directory=True)
    if kind == "relative": path = Path("relative")
    if kind == "parent_traversal": path = tmp_path / ".." / "new"
    if kind == "checkout": path = supervisor.CHECKOUT / "never-created-diagnostic"
    with pytest.raises((ValueError, FileExistsError)): supervisor._fresh_output(path)


@pytest.mark.parametrize("key,value", [("window_seconds", 601), ("window_seconds", True),
    ("progress_seconds", 0), ("term_grace_seconds", 6), ("kill_wait_seconds", float("nan"))])
def test_bounds_cannot_expand_or_disable_supervision(tmp_path, key, value):
    with pytest.raises(ValueError):
        supervisor.supervise([sys.executable], output=tmp_path / "new", cwd=tmp_path, **{key: value})
    assert not (tmp_path / "new").exists()


@NATIVE
def test_no_shell_pipe_or_environment_capture(tmp_path, monkeypatch):
    original = subprocess.Popen
    def popen(command, **kwargs):
        assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
        assert kwargs["stdout"] is not subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT and kwargs["stdin"] == subprocess.DEVNULL
        assert "shell" not in kwargs and "env" not in kwargs
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("SYNTHETIC_SECRET_NOT_FOR_LOGS", "opaque-secret-value")
    run(tmp_path, "pass")
    assert "opaque-secret-value" not in "".join(p.read_text() for p in (tmp_path / "evidence").iterdir())


@NATIVE
def test_supervisor_sigterm_bounded_cleanup_nonzero_report(tmp_path):
    output = tmp_path / "signal-evidence"
    command = ("from scripts.diagnose_core_supervisor import supervise\nimport sys\n"
        f"r=supervise([sys.executable,'-u','-c','import time; print(123,flush=True); time.sleep(30)'],output={str(output)!r},"
        f"cwd={str(tmp_path)!r},window_seconds=5,progress_seconds=.05,term_grace_seconds=.1,kill_wait_seconds=.5)\n"
        "raise SystemExit(r['exit_code'])")
    env = dict(os.environ, PYTHONPATH=str(supervisor.CHECKOUT), PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen([sys.executable, "-c", command], cwd=tmp_path, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 3
    try:
        path = output / "core-progress.jsonl"
        while not path.exists() or "RUNNING" not in path.read_text():
            assert time.monotonic() < deadline
            time.sleep(.02)
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=3) == 143
        value = json.loads((output / "core-supervisor.json").read_text())
        assert value["status"] == "SUPERVISOR_INTERRUPTED" and value["cleanup"]["leader_reaped"]
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=3)


def test_no_native_waitid_fails_before_files_or_spawn(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "waitid", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("spawn before capability gate"))
    with pytest.raises(RuntimeError, match="Linux waitid"):
        supervisor.supervise([sys.executable], output=tmp_path / "new", cwd=tmp_path)
    assert not (tmp_path / "new").exists()


def test_tail_uses_owned_descriptor_not_replaced_path(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"owned progress")
    secret = tmp_path / "not-a-log"
    secret.write_text("synthetic-never-read-secret")
    with path.open("rb") as handle:
        path.unlink(); path.symlink_to(secret)
        size, tail = supervisor._tail(handle)
    assert (size, tail) == (14, "owned progress")
