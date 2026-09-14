"""Finite local research collection/observation steps; no Git or token access.

The natural CLI supervises its own Linux process group for at most 300 seconds.
Only its successful parent emits a publishable-file manifest. Intermediate
same-asof labels are planning inputs, never versions appended to the old ledger.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).absolute().parents[2]
STATE_PARENT = Path("/tmp/dc20-candidate-natural-state")
SCHEMA = "dc20_candidate_natural_daily_manifest_v1"
MAX_STEPS, MAX_CALLS, RESERVED_CALLS, WINDOW_SECONDS = 8, 96, 12, 300
PINS = {
    "work/profit_1000_upgrade/candidate_natural_outcome_collect.py": "1d9addaa1aecaf1082023c28ab26b23b8ee5ab79d5fd7b9fd24f55e1320d9ced",
    "scripts/diagnose_core_supervisor.py": "b21794ddd38510ce06c1cbe958fe0744ce28547d998fceb6f34a31b66a5d5a99"}
FLAGS = {"research_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "natural_forward_admission_issued": False,
    "git_publication_verified": False, "formal_ledger_written": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "model_training_performed": False, "prediction_scores_recalculated": False,
    "old_sources_overwritten": False, "remote_writes_performed": 0,
    "credentials_read_by_coordinator": False, "intermediate_ledgers_publishable": False}


def require(ok, reason):
    if not ok: raise ValueError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()


for _relative, _expected in PINS.items():
    _p = ROOT / _relative
    require(not any(p.is_symlink() for p in (_p, *_p.parents)) and sha(_p.read_bytes()) == _expected,
        "REVIEWED_DAILY_DEPENDENCY_REQUIRED")
from work.profit_1000_upgrade import candidate_natural_outcome_collect as collector
from scripts import diagnose_core_supervisor as supervisor

natural, outcomes, labels = collector.natural, collector.outcomes, collector.labels
SELF_SHA = sha(natural._read(Path(__file__).absolute())[0])


def _guard():
    states = []
    for (relative, expected), module in zip(PINS.items(), (collector, supervisor)):
        path = ROOT / relative
        raw, identity = natural._read(path)
        require(Path(module.__file__).absolute() == path and sha(raw) == expected, "DAILY_DEPENDENCY_CHANGED")
        states.append(identity)
    raw, identity = natural._read(Path(__file__).absolute())
    require(sha(raw) == SELF_SHA, "DAILY_CODE_CHANGED")
    return tuple(states), identity, collector.code_guard()


def encoded(value): return natural.storage.encoded(value)


def _new(path, raw):
    path = natural._path(path)
    require(type(raw) is bytes and len(raw) <= natural.MAX_BYTES, "BOUNDED_NEW_BYTES_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return {"origin_path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def _bound(path, expected, states):
    path = natural._path(path); natural.scorer._sha(expected)
    raw, identity = natural._read(path)
    require(sha(raw) == expected, "EXTERNAL_DAILY_INPUT_SHA_CHANGED")
    states.append((path, expected, identity))
    return raw


def _dates(raw):
    return sorted({natural.scorer._date(r["cal_date"], "calendar_date")
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if r.get("exchange") == "SSE" and r.get("is_open") == "1"})


def _prior(raw, frozen, asof, dates):
    ledger = natural._json(raw)
    require(ledger.get("schema_version") == outcomes.SCHEMA
        and ledger.get("signal_date") == frozen["signal_date"]
        and ledger.get("snapshot_file_sha256") == frozen["file_sha256"], "PRIOR_LEDGER_IDENTITY_CHANGED")
    versions = ledger["versions"]
    require(type(versions) is list and 0 < len(versions) <= 4096, "BOUNDED_PRIOR_VERSIONS_REQUIRED")
    seen = []
    for version in versions:
        day = natural.scorer._date(version.get("as_of_date"), "prior_asof")
        require(frozen["signal_date"] <= day <= asof and day in dates
            and version.get("signal_date") == frozen["signal_date"], "FUTURE_OR_WRONG_PRIOR_LEDGER")
        seen.append(day)
    require(seen == sorted(set(seen)), "PRIOR_VERSIONS_NOT_APPEND_ONLY")
    outcomes._seal(ledger, "ledger_sha256")
    for version in versions:
        outcomes._seal(version, "report_sha256")
        require(version["snapshot_file_sha256"] == frozen["file_sha256"]
            and version["writer_sha256"] == collector.OUTCOMES_SHA, "PRIOR_WRITER_OR_SNAPSHOT_CHANGED")
    return ledger


def _inputs(arguments, clock):
    code_state = _guard(); states = []
    asof = natural.scorer._date(arguments["as_of_date"], "as_of_date")
    now = natural._now(clock)
    require(now >= labels._timestamp(labels._at(asof, "15:00:00")), "ASOF_SESSION_NOT_COMPLETED")
    raw = _bound(arguments["snapshot_path"], arguments["expected_snapshot_sha256"], states)
    frozen = outcomes._snapshot(raw, arguments["expected_snapshot_sha256"])
    require(clock is not None or frozen["clock_mode"] == "HOST_SYSTEM_UTC", "TEST_SNAPSHOT_CANNOT_ENTER_NATURAL_DAILY")
    require(frozen["signal_date"] <= asof and labels._timestamp(frozen["pre_cas_freeze_at_utc"]) <= now,
        "FUTURE_FROZEN_SELECTION")
    require(arguments["expected_calendar_sha256"] == labels.settlement.CALENDAR_SHA256, "PINNED_CALENDAR_REQUIRED")
    dates = _dates(_bound(arguments["calendar_path"], arguments["expected_calendar_sha256"], states))
    day = frozen["signal_date"]
    require(day in dates and asof in dates and dates[dates.index(day)+1:dates.index(day)+3]
        == [frozen["exec_date"], frozen["exit_date"]], "EXACT_D_T_T1_AND_ASOF_REQUIRED")
    frozen = {**frozen, "file_sha256": arguments["expected_snapshot_sha256"]}
    for prefix in ("collection", "outcomes"):
        require((arguments["previous_"+prefix+"_path"] is None) ==
            (arguments["expected_previous_"+prefix+"_sha256"] is None), "PAIRED_PREVIOUS_INPUT_REQUIRED")
    require(arguments["previous_outcomes_path"] is None or arguments["previous_collection_path"] is not None,
        "PRIOR_LEDGER_REQUIRES_COLLECTION")
    prior_raw = None
    if arguments["previous_outcomes_path"] is not None:
        # Check version-date metadata before hashing or interpreting its returns.
        prior_path = natural._path(arguments["previous_outcomes_path"])
        prior_raw, identity = natural._read(prior_path)
        _prior(prior_raw, frozen, asof, dates)
        expected = arguments["expected_previous_outcomes_sha256"]
        natural.scorer._sha(expected)
        require(sha(prior_raw) == expected, "EXTERNAL_PRIOR_LEDGER_SHA_CHANGED")
        states.append((prior_path, expected, identity))
    if arguments["previous_collection_path"] is not None:
        _bound(arguments["previous_collection_path"], arguments["expected_previous_collection_sha256"], states)
    return frozen, dates, prior_raw, states, code_state


def _input_guard(states, code_state):
    for path, expected, identity in states:
        require(sha(natural._read(path, identity)[0]) == expected, "DAILY_INPUT_CHANGED")
    require(_guard() == code_state, "DAILY_CODE_OR_INPUT_CHANGED")
    # The full dependency check cannot hide a late mutation of an original input.
    for path, expected, identity in states:
        require(sha(natural._read(path, identity)[0]) == expected, "DAILY_INPUT_CHANGED_AFTER_CODE_GUARD")


def _terminal_unchanged(old, new):
    if old is None: return
    for code, report in old["native_singleton_reports"].items():
        if report["rows"][0]["label_status"] in outcomes.TERMINAL:
            current = new["native_singleton_reports"][code]
            natural.scorer._exact(current["rows"], report["rows"], "INTERMEDIATE_TERMINAL_CHANGED")
            natural.scorer._exact(current["source_files"], report["source_files"], "INTERMEDIATE_TERMINAL_SOURCE_CHANGED")


def _recorded(path, raw=None):
    body = natural._read(path)[0]
    require(raw is None or body == raw, "TRUSTED_RETURN_BYTES_CHANGED")
    return {"origin_path": str(path), "sha256": sha(body), "bytes": len(body)}


def _source_bindings(receipt, root):
    result, seen = [], set()
    for binding in receipt["output_file_bindings"]:
        require(type(binding) is dict and set(binding) == {"path", "sha256", "bytes"}, "EXACT_COLLECTION_BINDING_REQUIRED")
        relative = binding["path"]
        require(type(relative) is str and not Path(relative).is_absolute() and "\\" not in relative
            and all(p not in ("", ".", "..") for p in relative.split("/")) and relative not in seen,
            "SAFE_UNIQUE_COLLECTION_BINDING_REQUIRED")
        seen.add(relative)
        record = _recorded(root / relative)
        require(record["sha256"] == binding["sha256"] and record["bytes"] == binding["bytes"], "COLLECTION_BYTES_CHANGED")
        result.append(record)
    result.append(_recorded(root/"receipt.json", encoded(receipt)))
    return result


def _pipeline(arguments, root, *, test_hooks=None):
    injected = test_hooks is not None
    hooks = test_hooks or {}
    clock, tick = hooks.get("clock"), hooks.get("monotonic", time.monotonic)
    began = tick()
    frozen, dates, prior_raw, states, code_state = _inputs(arguments, clock)
    require(injected or root == STATE_PARENT / frozen["signal_date"] / arguments["as_of_date"],
        "FIXED_NATURAL_DAY_ROOT_REQUIRED")
    prior_ledger = _prior(prior_raw, frozen, arguments["as_of_date"], dates) if prior_raw is not None else None
    original_latest = prior_ledger["versions"][-1] if prior_ledger else None
    same_asof = original_latest is not None and original_latest["as_of_date"] == arguments["as_of_date"]
    old_collection, old_collection_sha = arguments["previous_collection_path"], arguments["expected_previous_collection_sha256"]
    planning_ledger, planning_sha = arguments["previous_outcomes_path"], arguments["expected_previous_outcomes_sha256"]
    previous_latest, steps, calls = original_latest, [], 0
    final_collection = final_outcomes = None
    stop = "STEP_BUDGET_REACHED"
    if same_asof:
        # Existing same-asof evidence may only be revalidated, never recollected.
        codes = sorted({s["ts_code"] for key in ("candidate_slots", "promotion_slots") for s in frozen[key] if s["ts_code"]})
        collector.previous_state(old_collection, old_collection_sha, planning_ledger, planning_sha,
            frozen=frozen, codes=codes, dates=dates, asof=arguments["as_of_date"], states=states, injected=injected)
        _input_guard(states, code_state)
        return {"schema_version": SCHEMA, "status": "EXISTING_IDENTICAL_ASOF_ONLY_NO_COLLECTION", "publishable": False,
            "signal_date": frozen["signal_date"], "as_of_date": arguments["as_of_date"],
            "same_asof_original_kept": True, "api_calls": 0, "steps": [],
            "final_collection_receipt_path": str(old_collection), "final_collection_receipt_sha256": old_collection_sha,
            "final_outcomes_path": str(planning_ledger), "final_outcomes_sha256": planning_sha,
            "publishable_file_bindings": [], "daily_manifest_path": None, "daily_manifest_sha256": None,
            "test_hooks_injected": injected, "test_only": injected, **FLAGS}
    for step in range(1, MAX_STEPS+1):
        require(type(tick()) in (int, float) and tick() >= began, "DAILY_MONOTONIC_CLOCK_INVALID")
        if tick() - began >= WINDOW_SECONDS or calls + RESERVED_CALLS > MAX_CALLS:
            stop = "DAILY_BUDGET_REACHED"; break
        collection_root = root / f"collection_{step}" / "candidate_natural_outcome_sources"
        options = {"expected_snapshot_sha256": arguments["expected_snapshot_sha256"],
            "calendar_path": arguments["calendar_path"], "expected_calendar_sha256": arguments["expected_calendar_sha256"],
            "as_of_date": arguments["as_of_date"], "previous_collection_path": old_collection,
            "expected_previous_collection_sha256": old_collection_sha, "previous_outcomes_path": planning_ledger,
            "expected_previous_outcomes_sha256": planning_sha}
        if injected: options.update({key: hooks[key] for key in ("transport", "clock", "monotonic", "sleep")})
        collected = collector.collect_natural_outcome_sources(arguments["snapshot_path"], collection_root, **options)
        require(collected["callable_injected_for_test"] is injected
            and type(collected["api_calls"]) is int and 0 <= collected["api_calls"] <= RESERVED_CALLS,
            "COLLECTOR_MODE_OR_RESERVED_CALLS_CHANGED")
        calls += collected["api_calls"]
        require(calls <= MAX_CALLS and tick()-began < WINDOW_SECONDS, "DAILY_TOTAL_BUDGET_EXCEEDED")
        collection_bindings = _source_bindings(collected, collection_root)
        final_collection = collection_bindings[-1]
        out_root = root / f"observation_{step}" / "candidate_natural_outcomes"
        out_path = out_root / ("day_" + frozen["signal_date"] + ".json")
        if prior_raw is not None: _new(out_path, prior_raw)
        observed = outcomes.evaluate_natural_outcomes(arguments["snapshot_path"], out_root,
            expected_snapshot_sha256=arguments["expected_snapshot_sha256"], as_of_date=arguments["as_of_date"],
            source_bundle=collected["source_bundle"], expected_existing_ledger_sha256=arguments["expected_previous_outcomes_sha256"],
            clock=clock)
        final_outcomes = _recorded(out_path)
        require(final_outcomes["sha256"] == observed["ledger_file_sha256"], "OUTCOME_RETURN_SHA_CHANGED")
        ledger = natural._json(natural._read(out_path)[0])
        require(ledger["versions"][:-1] == ([] if prior_ledger is None else prior_ledger["versions"])
            and ledger["versions"][-1]["as_of_date"] == arguments["as_of_date"], "ONLY_ONE_FINAL_ASOF_APPEND_ALLOWED")
        latest = ledger["versions"][-1]
        _terminal_unchanged(previous_latest, latest)
        for name in ("candidate_slots", "promotion_slots"):
            natural.scorer._exact(latest[name], outcomes._slot_results(frozen[name], latest["native_singleton_reports"]),
                "FOUR_SLOTS_MUST_REMAIN_COMPLETE")
        steps.append({"step": step, "api_calls": collected["api_calls"], "reserved_max_api_calls": RESERVED_CALLS,
            "collection_receipt": final_collection, "observation_ledger": final_outcomes,
            "intermediate_ledger_publishable": False,
            "statuses": {code: r["rows"][0]["label_status"] for code, r in latest["native_singleton_reports"].items()}})
        previous_latest = latest
        old_collection, old_collection_sha = final_collection["origin_path"], final_collection["sha256"]
        planning_ledger, planning_sha = final_outcomes["origin_path"], final_outcomes["sha256"]
        if all(r["rows"][0]["label_status"] in outcomes.TERMINAL for r in latest["native_singleton_reports"].values()):
            stop = "ALL_PRESENT_SLOT_STOCKS_TERMINAL"; break
        if any(r["api_calls"] == 1 and not r["source_files"] for r in collected["requests"]):
            stop = "INVALID_EMPTY_OR_FAILED_EVIDENCE_PRESERVED_PENDING"; break
        if collected["api_calls"] == 0:
            stop = "NO_NEW_EVIDENCE_OR_NOT_DUE"; break
    require(final_collection is not None and final_outcomes is not None, "NO_COMPLETED_OBSERVATION_WITHIN_BUDGET")
    require(tick()-began < WINDOW_SECONDS, "DAILY_TOTAL_BUDGET_EXCEEDED")
    _input_guard(states, code_state)
    bindings = collection_bindings + [final_outcomes]
    _validate_bindings(bindings, root)
    require(_guard() == code_state, "DAILY_CODE_CHANGED_AFTER_REPLAY")
    _validate_bindings(bindings, root)
    return {"schema_version": SCHEMA, "status": "TEST_ONLY" if injected else "LOCAL_RESEARCH_DAY_COMPLETED",
        "signal_date": frozen["signal_date"], "as_of_date": arguments["as_of_date"],
        "snapshot_file_sha256": arguments["expected_snapshot_sha256"],
        "calendar_sha256": arguments["expected_calendar_sha256"],
        "previous_collection_sha256": arguments["expected_previous_collection_sha256"],
        "previous_outcomes_sha256": arguments["expected_previous_outcomes_sha256"],
        "final_collection_receipt_path": final_collection["origin_path"], "final_collection_receipt_sha256": final_collection["sha256"],
        "final_outcomes_path": final_outcomes["origin_path"], "final_outcomes_sha256": final_outcomes["sha256"],
        "publishable": False, "supervised_parent_completion_required": True,
        "publishable_file_bindings": bindings, "steps": steps, "stop_reason": stop, "api_calls": calls,
        "test_hooks_injected": injected, "test_only": injected, "max_steps": MAX_STEPS, "max_api_calls": MAX_CALLS,
        "wall_clock_window_seconds": WINDOW_SECONDS, "daily_module_sha256": SELF_SHA,
        "dependencies": dict(PINS), **FLAGS}


def _validate_bindings(bindings, root):
    require(type(bindings) is list and len(bindings) <= 20004, "BOUNDED_FINAL_BINDINGS_REQUIRED")
    seen = set()
    for binding in bindings:
        require(type(binding) is dict and set(binding) == {"origin_path", "sha256", "bytes"}, "EXACT_PUBLISHABLE_BINDING_REQUIRED")
        path = natural._path(binding["origin_path"])
        require(root in path.parents and str(path) not in seen, "FINAL_FILES_MUST_BE_UNIQUE_INSIDE_DAY_ROOT")
        seen.add(str(path))
    for binding in bindings:
        require(_recorded(Path(binding["origin_path"])) == binding, "FINAL_PUBLISHABLE_BYTES_CHANGED")


def _fresh_root(arguments, *, parent):
    parent = natural._path(parent)
    require(parent != ROOT and ROOT not in parent.parents and parent not in ROOT.parents, "ISOLATED_DAILY_PARENT_REQUIRED")
    frozen = outcomes._snapshot(natural._read(arguments["snapshot_path"])[0], arguments["expected_snapshot_sha256"])
    root = natural._path(parent / frozen["signal_date"] / arguments["as_of_date"])
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    require(not any(root.iterdir()), "FRESH_EMPTY_DAY_ROOT_REQUIRED")
    return root


def _finish(result, root, *, publishable, deadline=None):
    before = _guard()
    require(result.get("publishable") is False, "CHILD_CANNOT_SELF_ADMIT")
    require(not publishable or result.get("test_only") is False, "SYNTHETIC_RESULT_CANNOT_BECOME_PUBLISHABLE")
    result = {**result, "publishable": publishable,
        "supervised_parent_completion_required": False if publishable else True}
    _validate_bindings(result["publishable_file_bindings"], root)
    require(deadline is None or time.monotonic() < deadline, "FINAL_DAILY_DEADLINE_EXCEEDED")
    raw = encoded(result)
    binding = _new(root / "daily_manifest.json", raw)
    try:
        _validate_bindings(result["publishable_file_bindings"] + [binding], root)
        require(_guard() == before, "FINAL_DAILY_CODE_CHANGED")
        require(_recorded(root/"daily_manifest.json", raw) == binding, "FINAL_DAILY_MANIFEST_CHANGED")
        require(deadline is None or time.monotonic() < deadline, "FINAL_DAILY_DEADLINE_EXCEEDED")
    except Exception:
        # Only this newly created exact file is removed. Partial sources remain;
        # no manifest asserting publishability may survive an overrun.
        require(natural._read(root / "daily_manifest.json")[0] == raw, "LATE_MANIFEST_CHANGED")
        (root / "daily_manifest.json").unlink()
        raise
    return {**result, "daily_manifest_path": binding["origin_path"], "daily_manifest_sha256": binding["sha256"],
        "publishable_file_bindings": result["publishable_file_bindings"] + [binding]}


def _read_result_digest(descriptor, *, window=1.0):
    """A bounded control receipt, never logs/JSON or a disk self-attestation."""
    require(type(window) in (int, float) and 0 < window <= 1, "BOUNDED_RECEIPT_WINDOW_REQUIRED")
    os.set_blocking(descriptor, False)
    deadline, raw = time.monotonic() + window, b""
    while time.monotonic() < deadline:
        try:
            chunk = os.read(descriptor, 65-len(raw))
        except BlockingIOError:
            time.sleep(.005)
            continue
        if not chunk:
            require(len(raw) == 64 and all(c in b"0123456789abcdef" for c in raw),
                "EXACT_CHILD_RESULT_DIGEST_REQUIRED")
            return raw.decode("ascii")
        raw += chunk
        require(len(raw) <= 64, "OVERSIZED_CHILD_RESULT_DIGEST")
    raise ValueError("CHILD_RESULT_DIGEST_EOF_TIMEOUT")


def _supervise(command, root, *, window=WINDOW_SECONDS, receipt_required=False):
    """Regular-file logs; only the created unreaped Linux group is signalled."""
    require(sys.platform.startswith("linux") and callable(getattr(os, "waitid", None))
        and all(hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")), "LINUX_WNOWAIT_REQUIRED")
    require(type(window) in (int, float) and 0 < window <= WINDOW_SECONDS, "BOUNDED_SUPERVISOR_WINDOW_REQUIRED")
    require(type(receipt_required) is bool, "EXACT_RECEIPT_MODE_REQUIRED")
    started, child, status, exit_code, cleanup = time.monotonic(), None, "ERROR", 70, None
    read_fd = write_fd = None
    digest = None
    old = {}
    def interrupted(signum, _frame): raise supervisor._Interrupted(signum)
    with (root / "worker.log").open("xb", buffering=0) as log:
        try:
            for sig in (signal.SIGINT, signal.SIGTERM): old[sig] = signal.signal(sig, interrupted)
            if receipt_required:
                read_fd, write_fd = os.pipe()
                command = [*command, "--internal-result-fd", str(write_fd)]
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                pass_fds=() if write_fd is None else (write_fd,))
            if write_fd is not None:
                os.close(write_fd); write_fd = None
            while True:
                if time.monotonic()-started >= window:
                    status, exit_code = "TIMEOUT_NO_PUBLICATION", 124; break
                if supervisor._leader_status(child) is not None:
                    status = "CHILD_FINISHED"; break
                time.sleep(min(.05, max(.001, window-(time.monotonic()-started))))
        except supervisor._Interrupted as exc:
            status, exit_code = "INTERRUPTED_NO_PUBLICATION", 128+exc.signum
        except Exception:
            status, exit_code = "SUPERVISOR_ERROR_NO_PUBLICATION", 70
        finally:
            for sig in old: signal.signal(sig, signal.SIG_IGN)
            if child is not None:
                cleanup = supervisor._stop_group(child, 1, 2)
                if status == "CHILD_FINISHED" and cleanup["cleanup_error"] is None:
                    exit_code = cleanup["child_returncode"]
                    if type(exit_code) is not int or exit_code != 0: status = "CHILD_FAILED_NO_PUBLICATION"
                elif cleanup["cleanup_error"] is not None: status, exit_code = "CLEANUP_FAILED_NO_PUBLICATION", 70
            for sig, handler in old.items(): signal.signal(sig, handler)
            try:
                if receipt_required and status == "CHILD_FINISHED" and exit_code == 0:
                    digest = _read_result_digest(read_fd)
            except Exception:
                status, exit_code = "CHILD_RECEIPT_FAILED_NO_PUBLICATION", 70
            finally:
                for descriptor in (read_fd, write_fd):
                    if descriptor is not None: os.close(descriptor)
    report = {"status": status, "exit_code": exit_code, "elapsed_seconds": time.monotonic()-started,
        "window_seconds": window, "cleanup": cleanup, "publishable": False,
        "child_result_sha256": digest,
        "containment_scope": "CREATED_PROCESS_GROUP_ONLY_NO_ESCAPED_SESSION_CLAIM", **FLAGS}
    _new(root / "supervisor.json", encoded(report))
    return report


def run_daily(snapshot_path, *, expected_snapshot_sha256, calendar_path, expected_calendar_sha256,
        as_of_date, previous_collection_path=None, expected_previous_collection_sha256=None,
        previous_outcomes_path=None, expected_previous_outcomes_sha256=None, test_hooks=None):
    deadline = time.monotonic() + WINDOW_SECONDS
    arguments = {"snapshot_path": str(snapshot_path), "expected_snapshot_sha256": expected_snapshot_sha256,
        "calendar_path": str(calendar_path), "expected_calendar_sha256": expected_calendar_sha256,
        "as_of_date": as_of_date, "previous_collection_path": None if previous_collection_path is None else str(previous_collection_path),
        "expected_previous_collection_sha256": expected_previous_collection_sha256,
        "previous_outcomes_path": None if previous_outcomes_path is None else str(previous_outcomes_path),
        "expected_previous_outcomes_sha256": expected_previous_outcomes_sha256}
    injected = test_hooks is not None
    if injected:
        require(type(test_hooks) is dict and set(test_hooks) == {"transport", "clock", "monotonic", "sleep", "state_parent"}
            and all(callable(test_hooks[k]) for k in ("transport", "clock", "monotonic", "sleep")), "EXPLICIT_TEST_ONLY_HOOKS_REQUIRED")
        parent = test_hooks["state_parent"]
    else:
        require(sys.platform.startswith("linux"), "NATURAL_DAILY_CLI_REQUIRES_LINUX")
        parent = STATE_PARENT
    frozen, dates, prior_raw, states, before = _inputs(arguments, test_hooks["clock"] if injected else None)
    if prior_raw is not None and _prior(prior_raw, frozen, as_of_date, dates)["versions"][-1]["as_of_date"] == as_of_date:
        # No fresh directory or subprocess is needed to preserve an already
        # published immutable observation at exactly this asof.
        result = _pipeline(arguments, Path(parent) / frozen["signal_date"] / as_of_date, test_hooks=test_hooks)
        _input_guard(states, before)
        return result
    root = _fresh_root(arguments, parent=parent)
    _new(root / "request.json", encoded(arguments))
    if injected:
        result = _pipeline(arguments, root, test_hooks=test_hooks)
        _input_guard(states, before)
        return _finish(result, root, publishable=False)
    command = [sys.executable, "-u", "-m", "work.profit_1000_upgrade.candidate_natural_daily",
        "--internal-worker-request", str(root / "request.json")]
    remaining = deadline - time.monotonic()
    require(0 < remaining <= WINDOW_SECONDS, "DAILY_PREPARATION_DEADLINE_EXCEEDED")
    supervision = _supervise(command, root, window=remaining, receipt_required=True)
    require(supervision["status"] == "CHILD_FINISHED" and supervision["exit_code"] == 0
        and supervision["elapsed_seconds"] < WINDOW_SECONDS, "DAILY_SUPERVISION_FAILED_NO_PUBLISHABLE_MANIFEST")
    result_raw = natural._read(root / "worker_result.json")[0]
    require(sha(result_raw) == supervision.get("child_result_sha256"), "INDEPENDENT_CHILD_RESULT_DIGEST_CHANGED")
    result = natural._json(result_raw)
    require(result.get("daily_module_sha256") == SELF_SHA and result.get("dependencies") == PINS
        and result.get("signal_date") == root.parent.name and result.get("as_of_date") == as_of_date,
        "EXACT_SUPERVISED_RESULT_REQUIRED")
    _input_guard(states, before)
    return _finish(result, root, publishable=result.get("status") == "LOCAL_RESEARCH_DAY_COMPLETED", deadline=deadline)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal-worker-request", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--internal-result-fd", type=int, help=argparse.SUPPRESS)
    for field in ("snapshot", "calendar", "previous-collection", "previous-outcomes"):
        parser.add_argument("--"+field, type=Path)
        parser.add_argument("--"+field+"-sha256")
    parser.add_argument("--as-of-date")
    args = parser.parse_args(argv)
    try:
        if args.internal_worker_request is not None:
            require(sys.platform.startswith("linux") and os.getpid() == os.getsid(0) == os.getpgrp(), "ISOLATED_INTERNAL_WORKER_REQUIRED")
            require(type(args.internal_result_fd) is int and args.internal_result_fd >= 3
                and stat.S_ISFIFO(os.fstat(args.internal_result_fd).st_mode), "INHERITED_RESULT_PIPE_REQUIRED")
            require(all(getattr(args, field) is None for field in ("snapshot", "snapshot_sha256", "calendar", "calendar_sha256",
                "previous_collection", "previous_collection_sha256", "previous_outcomes", "previous_outcomes_sha256", "as_of_date")),
                "INTERNAL_WORKER_CANNOT_OVERRIDE_REQUEST")
            path = natural._path(args.internal_worker_request)
            require(path.name == "request.json" and path.parent.parent.parent == STATE_PARENT,
                "FIXED_INTERNAL_REQUEST_LOCATION_REQUIRED")
            day = natural.scorer._date(path.parent.parent.name, "internal_signal_date")
            asof = natural.scorer._date(path.parent.name, "internal_asof")
            require("20260914" <= day <= asof, "FIXED_INTERNAL_REQUEST_DATES_REQUIRED")
            raw, identity = natural._read(path)
            arguments = natural._json(raw)
            require(type(arguments) is dict and arguments.get("as_of_date") == asof, "INTERNAL_REQUEST_ASOF_CHANGED")
            result = _pipeline(arguments, path.parent)
            require(natural._read(path, identity)[0] == raw, "WORKER_REQUEST_CHANGED")
            require(result.get("signal_date") == day and result.get("as_of_date") == asof, "INTERNAL_RESULT_DATES_CHANGED")
            result_raw = encoded(result)
            _new(path.parent / "worker_result.json", result_raw)
            require(os.write(args.internal_result_fd, sha(result_raw).encode("ascii")) == 64, "COMPLETE_CHILD_RESULT_DIGEST_REQUIRED")
            os.close(args.internal_result_fd)
        else:
            require(args.internal_result_fd is None, "INTERNAL_RESULT_FD_REQUIRES_WORKER")
            require(args.snapshot is not None and args.calendar is not None and args.as_of_date is not None,
                "EXPLICIT_NATURAL_DAILY_INPUTS_REQUIRED")
            result = run_daily(args.snapshot, expected_snapshot_sha256=args.snapshot_sha256,
                calendar_path=args.calendar, expected_calendar_sha256=args.calendar_sha256, as_of_date=args.as_of_date,
                previous_collection_path=args.previous_collection, expected_previous_collection_sha256=args.previous_collection_sha256,
                previous_outcomes_path=args.previous_outcomes, expected_previous_outcomes_sha256=args.previous_outcomes_sha256)
        print(json.dumps({k: result.get(k) for k in ("status", "signal_date", "as_of_date", "api_calls", "publishable",
            "daily_manifest_path", "daily_manifest_sha256")}, sort_keys=True))
        return 0
    except Exception:
        print("NATURAL_DAILY_FAILED_CLOSED_NO_PUBLICATION")
        return 1


if __name__ == "__main__": raise SystemExit(main())
