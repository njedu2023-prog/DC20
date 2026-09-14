"""Explicit synthetic HTTP/time/root hooks only; never real market requests."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys

import pytest

from work.profit_1000_upgrade import candidate_natural_daily as m
from work.profit_1000_upgrade import test_candidate_natural_outcome_collect as old


def setup(tmp_path, monkeypatch, *, size=2):
    case = old.setup(tmp_path, monkeypatch, size=size)
    clock = old.Clock(); requests = []
    def transport(contract):
        requests.append(deepcopy(contract))
        return old.response(contract)
    hooks = {"transport": transport, "clock": lambda: datetime(2026, 9, 17, 8, tzinfo=timezone.utc),
        "monotonic": clock.tick, "sleep": clock.sleep, "state_parent": tmp_path.resolve()/"state"}
    return {"case": case, "hooks": hooks, "clock": clock, "requests": requests}


@pytest.fixture
def case(tmp_path, monkeypatch): return setup(tmp_path, monkeypatch)


def run(case, *, asof=None, prior=None, **options):
    source = case["case"]
    kwargs = {"expected_snapshot_sha256": source["snapshot_sha"],
        "calendar_path": source["bundle"]["calendar"]["origin_path"],
        "expected_calendar_sha256": source["bundle"]["calendar"]["sha256"],
        "as_of_date": asof or source["t1"], "test_hooks": case["hooks"]}
    if prior:
        kwargs.update(previous_collection_path=prior["final_collection_receipt_path"],
            expected_previous_collection_sha256=prior["final_collection_receipt_sha256"],
            previous_outcomes_path=prior["final_outcomes_path"], expected_previous_outcomes_sha256=prior["final_outcomes_sha256"])
    kwargs.update(options)
    return m.run_daily(source["snapshot"], **kwargs)


def latest(result):
    return json.loads(Path(result["final_outcomes_path"]).read_bytes())["versions"][-1]


def test_two_rounds_same_asof_native_loss_without_mutating_prior_or_rescoring(case, monkeypatch):
    raw = case["case"]["snapshot"].read_bytes()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    monkeypatch.setattr(m.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("RESCORE"))
    result = run(case)
    assert result["status"] == "TEST_ONLY" and result["publishable"] is False
    assert result["test_only"] is result["test_hooks_injected"] is True
    assert len(result["steps"]) == 2 and result["api_calls"] == 3 * 2 * len(case["case"]["codes"])
    assert len(json.loads(Path(result["final_outcomes_path"]).read_bytes())["versions"]) == 1
    assert result["stop_reason"] == "ALL_PRESENT_SLOT_STOCKS_TERMINAL"
    out = latest(result)
    for name in ("candidate_slots", "promotion_slots"):
        assert len(out[name]) == 2
        for slot in out[name]: assert slot["slot_net_return"] == pytest.approx(-.0245)
    assert raw == case["case"]["snapshot"].read_bytes()
    for binding in result["publishable_file_bindings"]:
        assert m.sha(Path(binding["origin_path"]).read_bytes()) == binding["sha256"]
        assert "collection_1" not in binding["origin_path"] and "observation_1" not in binding["origin_path"]
    assert all(step["intermediate_ledger_publishable"] is False for step in result["steps"])
    assert result["credentials_read_by_coordinator"] is result["formal_ledger_written"] is False


@pytest.mark.parametrize("size", [0, 1, 2, 10])
def test_not_due_preserves_all_four_slots_without_market_requests(tmp_path, monkeypatch, size):
    case = setup(tmp_path, monkeypatch, size=size)
    result = run(case, asof=case["case"]["day"])
    assert result["api_calls"] == 0 and case["requests"] == []
    out = latest(result)
    for name in ("candidate_slots", "promotion_slots"):
        assert len(out[name]) == 2
        for i, slot in enumerate(out[name]):
            assert slot["status"] == ("PENDING_T" if i < size else "MISSING_CANDIDATE")
            assert slot["slot_net_return"] is None


def test_external_older_prefix_preserved_and_same_asof_is_noop(case):
    first = run(case, asof=case["case"]["t"])
    raw = Path(first["final_outcomes_path"]).read_bytes()
    first_files = {b["origin_path"]: Path(b["origin_path"]).read_bytes() for b in first["publishable_file_bindings"]}
    second = run(case, prior=first)
    ledger = json.loads(Path(second["final_outcomes_path"]).read_bytes())
    assert ledger["versions"][:-1] == json.loads(raw)["versions"]
    assert all(Path(p).read_bytes() == body for p, body in first_files.items())
    count = len(case["requests"])
    same = run(case, prior=second)
    assert same["status"] == "EXISTING_IDENTICAL_ASOF_ONLY_NO_COLLECTION"
    assert same["api_calls"] == 0 and same["publishable_file_bindings"] == []
    assert same["daily_manifest_path"] is same["daily_manifest_sha256"] is None
    assert len(case["requests"]) == count


def test_terminal_next_asof_uses_original_sources_and_zero_calls(case):
    first = run(case)
    row_before = latest(first)["native_singleton_reports"]
    count = len(case["requests"])
    second = run(case, asof="20260917", prior=first)
    assert second["api_calls"] == 0 and len(case["requests"]) == count
    for code, old_report in row_before.items():
        assert latest(second)["native_singleton_reports"][code]["rows"] == old_report["rows"]
        assert latest(second)["native_singleton_reports"][code]["source_files"] == old_report["source_files"]


@pytest.mark.parametrize("kind", ["empty", "invalid", "transport"])
def test_failed_or_empty_sources_stop_and_preserve_pending_no_zero(case, kind):
    calls = []
    def transport(contract):
        calls.append(deepcopy(contract))
        if kind == "transport": raise ValueError("synthetic provider failure must not be logged")
        return old.response(contract, empty=kind == "empty", invalid=kind == "invalid")
    case["hooks"]["transport"] = transport
    result = run(case)
    assert len(result["steps"]) == 1
    assert result["stop_reason"] == "INVALID_EMPTY_OR_FAILED_EVIDENCE_PRESERVED_PENDING"
    assert len(calls) == 3 * len(case["case"]["codes"])
    assert all(row["rows"][0]["slot_net_return"] is None for row in latest(result)["native_singleton_reports"].values())
    count = len(calls)
    later = run(case, asof="20260917", prior=result)
    assert later["api_calls"] == 0 and len(calls) == count


def test_T_no_fill_stops_without_minutes_and_uses_native_zero_only(case):
    def transport(contract): return old.response(contract, price=11, pre=10)
    case["hooks"]["transport"] = transport
    result = run(case)
    assert len(result["steps"]) == 1
    assert all(r["rows"][0]["label_status"] == "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
        and r["rows"][0]["slot_net_return"] == 0 for r in latest(result)["native_singleton_reports"].values())


def test_eight_steps_hard_limit_never_requests_ninth_missing_session(case, monkeypatch):
    source = case["case"]
    extra = ["20260918", "20260921", "20260922", "20260923", "20260924", "20260925"]
    calendar_path = Path(source["bundle"]["calendar"]["origin_path"])
    calendar_raw = calendar_path.read_bytes() + "".join(f"SSE,{day},1\n" for day in extra).encode()
    calendar_path.write_bytes(calendar_raw)
    digest = m.sha(calendar_raw)
    source["bundle"]["calendar"]["sha256"] = digest
    monkeypatch.setattr(m.labels.settlement, "CALENDAR_SHA256", digest)
    frozen = json.loads(source["snapshot"].read_bytes())
    for binding in frozen["D_source_evidence"]["source_file_bindings"]:
        if binding["receipt_path"] == str(m.labels.settlement.CALENDAR_PATH):
            binding["sha256"] = digest; binding["bytes"] = len(calendar_raw)
    frozen.pop("snapshot_sha256")
    frozen["snapshot_sha256"] = m.natural.scorer.canonical_sha(frozen)
    snapshot_raw = m.encoded(frozen); source["snapshot"].write_bytes(snapshot_raw); source["snapshot_sha"] = m.sha(snapshot_raw)
    days = ["20260915", "20260916", "20260917", *extra]
    prices = [10.0]
    for _ in days[1:]: prices.append(round(prices[-1]*1.1, 2))
    requests = []
    def transport(contract):
        requests.append(deepcopy(contract))
        _, day, _ = m.collector.request_identity(contract)
        index = days.index(day)
        return old.response(contract, price=prices[index], pre=10 if index == 0 else prices[index-1])
    case["hooks"]["transport"] = transport
    case["hooks"]["clock"] = lambda: datetime(2026, 9, 25, 8, tzinfo=timezone.utc)
    result = run(case, asof="20260925")
    assert len(result["steps"]) == 8 and result["stop_reason"] == "STEP_BUDGET_REACHED"
    assert result["api_calls"] == len(requests) <= 96
    assert max(m.collector.request_identity(r)[1] for r in requests) == "20260924"
    assert all(r["rows"][0]["slot_net_return"] is None for r in latest(result)["native_singleton_reports"].values())
    assert all(r["rows"][0]["missing_evidence_date"] == "20260925" for r in latest(result)["native_singleton_reports"].values())


@pytest.mark.parametrize("when", ["before_write", "after_write"])
def test_deadline_no_publishable_manifest_survives(tmp_path, monkeypatch, when):
    root = tmp_path.resolve()/"root"; root.mkdir()
    f = root/"ledger.json"; f.write_bytes(b"{}")
    result = {"publishable": False, "test_only": False, "publishable_file_bindings": [m._recorded(f)]}
    stamps = iter([2] if when == "before_write" else [0, 2])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(stamps))
    with pytest.raises(ValueError, match="DEADLINE"):
        m._finish(result, root, publishable=True, deadline=1)
    assert not (root/"daily_manifest.json").exists()
    assert f.read_bytes() == b"{}"


def test_test_result_can_never_be_marked_publishable(tmp_path):
    root = tmp_path.resolve()/"root"; root.mkdir()
    with pytest.raises(ValueError, match="SYNTHETIC_RESULT"):
        m._finish({"publishable": False, "test_only": True, "publishable_file_bindings": []}, root, publishable=True)


@pytest.mark.parametrize("kind", ["snapshot_sha", "calendar_sha", "future_asof", "not_completed", "unpaired_prior", "outcome_without_collection"])
def test_invalid_inputs_before_any_collection_or_state_write(case, kind):
    options = {}
    if kind == "snapshot_sha": options["expected_snapshot_sha256"] = "0" * 64
    elif kind == "calendar_sha": options["expected_calendar_sha256"] = "0" * 64
    elif kind == "future_asof": options["asof"] = "20260918"
    elif kind == "not_completed": case["hooks"]["clock"] = lambda: datetime(2026, 9, 16, 6, tzinfo=timezone.utc)
    elif kind == "unpaired_prior": options["previous_collection_path"] = "/nonexistent"
    else:
        options.update(previous_outcomes_path="/nonexistent", expected_previous_outcomes_sha256="1" * 64)
    with pytest.raises(ValueError): run(case, **options)
    assert case["requests"] == []
    assert not case["hooks"]["state_parent"].exists()


def test_existing_asof_root_not_overwritten(case):
    run(case)
    before = {p: p.read_bytes() for p in case["hooks"]["state_parent"].rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError): run(case)
    assert all(p.read_bytes() == value for p, value in before.items())


def test_alias_state_parent_rejected_before_requests(case, tmp_path):
    target = tmp_path.resolve()/"target"; target.mkdir()
    alias = tmp_path.resolve()/"alias"; alias.symlink_to(target, target_is_directory=True)
    case["hooks"]["state_parent"] = alias
    with pytest.raises(ValueError, match="UNALIASED"): run(case)
    assert not case["requests"] and not list(target.iterdir())


def test_late_source_mutation_does_not_create_publishable_manifest(case, monkeypatch):
    original = m.outcomes.evaluate_natural_outcomes
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        case["case"]["snapshot"].write_bytes(case["case"]["snapshot"].read_bytes()+b" ")
        return result
    monkeypatch.setattr(m.outcomes, "evaluate_natural_outcomes", observe)
    with pytest.raises(ValueError): run(case)
    assert not list(case["hooks"]["state_parent"].rglob("daily_manifest.json"))


def test_oversized_step_call_claim_rejected(case, monkeypatch):
    original = m.collector.collect_natural_outcome_sources
    def collect(*args, **kwargs):
        result = original(*args, **kwargs); result["api_calls"] = 13; return result
    monkeypatch.setattr(m.collector, "collect_natural_outcome_sources", collect)
    with pytest.raises(ValueError, match="RESERVED_CALLS"): run(case)


def test_wrong_collector_injection_mode_rejected(case, monkeypatch):
    original = m.collector.collect_natural_outcome_sources
    def collect(*args, **kwargs):
        result = original(*args, **kwargs); result["callable_injected_for_test"] = False; return result
    monkeypatch.setattr(m.collector, "collect_natural_outcome_sources", collect)
    with pytest.raises(ValueError, match="COLLECTOR_MODE"): run(case)


@pytest.mark.parametrize("field", ["transport", "clock", "monotonic", "sleep", "state_parent"])
def test_incomplete_test_hooks_never_fall_through_to_real_transport(case, field):
    del case["hooks"][field]
    with pytest.raises(ValueError, match="TEST_ONLY_HOOKS"): run(case)
    assert not case["requests"]


def test_no_cli_output_override_or_token_environment_reader():
    import ast
    tree = ast.parse(Path(m.__file__).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not any(isinstance(n.func, ast.Attribute) and n.func.attr in ("getenv", "urlopen", "predict_forward", "fit") for n in calls)
    assert not any(isinstance(n, ast.Attribute) and n.attr == "environ" for n in ast.walk(tree))
    assert "--output-root" not in Path(m.__file__).read_text()


def test_natural_entry_rejects_non_linux_before_market_or_source_work(case, monkeypatch):
    monkeypatch.setattr(m.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="REQUIRES_LINUX"):
        run(case, test_hooks=None)
    assert not case["requests"]


def test_test_snapshot_is_not_promoted_by_omitting_test_hooks(case, monkeypatch):
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m.natural, "_now", lambda _: datetime(2026, 9, 17, 8, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="TEST_SNAPSHOT_CANNOT"):
        run(case, test_hooks=None)
    assert not case["requests"]


@pytest.mark.parametrize("failure", [None, "timeout", "wrong_day", "code", "changed_output", "changed_result", "missing_digest"])
def test_parent_supervision_and_manifest_gate_explicitly_mocked_not_real_source(case, monkeypatch, failure):
    source = case["case"]
    frozen = json.loads(source["snapshot"].read_bytes())
    frozen["clock_mode"] = "HOST_SYSTEM_UTC"  # Synthetic test envelope, not a publication proof.
    frozen.pop("snapshot_sha256"); frozen["snapshot_sha256"] = m.natural.scorer.canonical_sha(frozen)
    raw = m.encoded(frozen); source["snapshot"].write_bytes(raw); source["snapshot_sha"] = m.sha(raw)
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m, "STATE_PARENT", case["hooks"]["state_parent"])
    monkeypatch.setattr(m.natural, "_now", lambda _: datetime(2026, 9, 17, 8, tzinfo=timezone.utc))
    commands = []
    def supervise(command, root, *, window, receipt_required):
        commands.append(command)
        assert receipt_required is True
        assert 0 < window <= 300
        assert command[-2:] == ["--internal-worker-request", str(root/"request.json")]
        assert old.TOKEN not in str(command)
        if failure == "timeout": return {"status": "TIMEOUT_NO_PUBLICATION", "exit_code": 124, "elapsed_seconds": 300}
        file = root/"observation_1"/"candidate_natural_outcomes"/"day_20260914.json"
        binding = m._new(file, b'{"explicit_synthetic":true}')
        result = {"status": "LOCAL_RESEARCH_DAY_COMPLETED", "publishable": False, "test_only": False,
            "signal_date": "20260915" if failure == "wrong_day" else "20260914", "as_of_date": source["t1"],
            "daily_module_sha256": "0"*64 if failure == "code" else m.SELF_SHA,
            "dependencies": dict(m.PINS), "publishable_file_bindings": [binding]}
        raw = m.encoded(result)
        digest = m.sha(raw)
        m._new(root/"worker_result.json", raw)
        if failure == "changed_output": file.write_bytes(b"changed")
        if failure == "changed_result":
            # An internally consistent replacement cannot forge the separately
            # returned child-memory digest, even if all disk hashes agree.
            file.write_bytes(b"replacement")
            result["publishable_file_bindings"] = [m._recorded(file)]
            (root/"worker_result.json").write_bytes(m.encoded(result))
        return {"status": "CHILD_FINISHED", "exit_code": 0, "elapsed_seconds": .01,
            "child_result_sha256": None if failure == "missing_digest" else digest}
    monkeypatch.setattr(m, "_supervise", supervise)
    if failure:
        with pytest.raises(ValueError): run(case, test_hooks=None)
        assert not list(case["hooks"]["state_parent"].rglob("daily_manifest.json"))
    else:
        result = run(case, test_hooks=None)
        assert result["publishable"] is True and result["test_only"] is False
        assert Path(result["daily_manifest_path"]).is_file()
    assert len(commands) == 1 and not case["requests"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Actual Linux WNOWAIT/process-group safety is not tested on macOS")
@pytest.mark.parametrize("kind", ["success", "timeout", "descendant", "receipt", "bad_receipt"])
def test_real_linux_supervisor_has_bounded_group_cleanup(tmp_path, kind):
    root = tmp_path.resolve()/kind; root.mkdir()
    if kind == "success": code = "print('synthetic child finished')"
    elif kind == "timeout": code = "import time; time.sleep(30)"
    elif kind == "descendant": code = "import os,time; child=os.fork(); time.sleep(30)"
    else: code = "import os,sys; os.write(int(sys.argv[-1]), b'a'*" + ("64" if kind == "receipt" else "63") + ")"
    report = m._supervise([sys.executable, "-c", code], root, window=.3,
        receipt_required=kind in ("receipt", "bad_receipt"))
    assert report["publishable"] is False
    assert report["cleanup"]["leader_reaped"] is True
    assert report["cleanup"]["kill_sent"] is True
    assert report["elapsed_seconds"] < 4
    assert not (root/"daily_manifest.json").exists()
    expected = {"success": "CHILD_FINISHED", "receipt": "CHILD_FINISHED",
        "bad_receipt": "CHILD_RECEIPT_FAILED_NO_PUBLICATION"}.get(kind, "TIMEOUT_NO_PUBLICATION")
    assert report["status"] == expected
    assert report["child_result_sha256"] == ("a"*64 if kind == "receipt" else None)


def test_fixed_worker_request_depth_and_process_group_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m.os, "getpid", lambda: 123)
    monkeypatch.setattr(m.os, "getsid", lambda _: 124)
    assert m.main(["--internal-worker-request", "/tmp/dc20-candidate-natural-state/20260914/20260916/request.json"]) == 1


@pytest.mark.parametrize("raw", [b"", b"a"*63, b"a"*65, b"a"*64+b"\n", b"A"*64, b"g"*64, b"\xff"*64])
def test_control_receipt_rejects_nonexact_digest_without_unbounded_read(raw):
    reader, writer = os.pipe()
    try:
        os.write(writer, raw); os.close(writer); writer = None
        with pytest.raises(ValueError, match="CHILD_RESULT_DIGEST"):
            m._read_result_digest(reader, window=.05)
    finally:
        os.close(reader)
        if writer is not None: os.close(writer)


def test_control_receipt_requires_eof_and_has_bounded_nonblocking_deadline():
    reader, writer = os.pipe()
    try:
        os.write(writer, b"a"*64)
        began = m.time.monotonic()
        with pytest.raises(ValueError, match="EOF_TIMEOUT"):
            m._read_result_digest(reader, window=.02)
        assert m.time.monotonic()-began < .5
        os.close(writer); writer = None
    finally:
        os.close(reader)
        if writer is not None: os.close(writer)


def test_control_receipt_accepts_exact_lowercase_digest_only_after_eof():
    reader, writer = os.pipe()
    try:
        digest = m.sha(b"independent child memory result")
        os.write(writer, digest.encode("ascii")); os.close(writer); writer = None
        assert m._read_result_digest(reader, window=.05) == digest
    finally:
        os.close(reader)
        if writer is not None: os.close(writer)


@pytest.mark.parametrize("kind", ["wrong_parent", "wrong_asof", "invalid_day", "before_natural_start", "override", "nonpipe"])
def test_private_worker_fixed_path_dates_and_inherited_channel_before_pipeline(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m.os, "getpid", lambda: 123)
    monkeypatch.setattr(m.os, "getsid", lambda _: 123)
    monkeypatch.setattr(m.os, "getpgrp", lambda: 123)
    parent = tmp_path.resolve()/"state"
    monkeypatch.setattr(m, "STATE_PARENT", parent)
    day = "invalid" if kind == "invalid_day" else "20260911" if kind == "before_natural_start" else "20260914"
    path = (tmp_path.resolve()/"other" if kind == "wrong_parent" else parent)/day/"20260916"/"request.json"
    m._new(path, m.encoded({"as_of_date": "20260915" if kind == "wrong_asof" else "20260916"}))
    monkeypatch.setattr(m, "_pipeline", lambda *a, **k: pytest.fail("PRIVATE_INVALID_REQUEST_REACHED_PIPELINE"))
    reader, writer = os.pipe()
    other = None
    try:
        if kind == "nonpipe": other = os.open(path, os.O_RDONLY)
        args = ["--internal-worker-request", str(path), "--internal-result-fd", str(other if other is not None else writer)]
        if kind == "override": args += ["--as-of-date", "20260917"]
        assert m.main(args) == 1
        assert not (path.parent/"worker_result.json").exists()
    finally:
        os.close(reader); os.close(writer)
        if other is not None: os.close(other)


def test_natural_pipeline_rejects_wrong_snapshot_day_root_before_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_inputs", lambda *a: ({"signal_date": "20260914"}, [], None, [], None))
    monkeypatch.setattr(m.collector, "collect_natural_outcome_sources", lambda *a, **k: pytest.fail("COLLECTION"))
    with pytest.raises(ValueError, match="FIXED_NATURAL_DAY_ROOT"):
        m._pipeline({"as_of_date": "20260916"}, tmp_path.resolve()/"20260915"/"20260916")


@pytest.mark.parametrize("replace_disk", [False, True])
def test_mock_worker_sends_memory_digest_not_replaceable_disk_self_receipt(tmp_path, monkeypatch, replace_disk):
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m.os, "getpid", lambda: 123)
    monkeypatch.setattr(m.os, "getsid", lambda _: 123)
    monkeypatch.setattr(m.os, "getpgrp", lambda: 123)
    parent = tmp_path.resolve()/"state"
    monkeypatch.setattr(m, "STATE_PARENT", parent)
    path = parent/"20260914"/"20260916"/"request.json"
    m._new(path, m.encoded({"as_of_date": "20260916"}))
    result = {"signal_date": "20260914", "as_of_date": "20260916", "status": "EXPLICIT_SYNTHETIC_MOCK"}
    monkeypatch.setattr(m, "_pipeline", lambda *a, **k: dict(result))
    original_new = m._new
    def replacement(target, raw):
        binding = original_new(target, raw)
        if replace_disk and target.name == "worker_result.json": target.write_bytes(b'{"changed":true}')
        return binding
    monkeypatch.setattr(m, "_new", replacement)
    reader, writer = os.pipe()
    try:
        assert m.main(["--internal-worker-request", str(path), "--internal-result-fd", str(writer)]) == 0
        writer = None  # The successful child entry closed its inherited writer.
        digest = m._read_result_digest(reader, window=.05)
        assert digest == m.sha(m.encoded(result))
        assert (digest == m.sha((path.parent/"worker_result.json").read_bytes())) is (not replace_disk)
        assert not (path.parent/"daily_manifest.json").exists()
    finally:
        os.close(reader)
        if writer is not None: os.close(writer)


def test_supervisor_spawn_failure_closes_own_control_descriptors(tmp_path, monkeypatch):
    # OS creation and pipe cleanup are real; child launch is explicitly mocked.
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m.os, "waitid", lambda *a: None, raising=False)
    for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"):
        if not hasattr(m.os, name): monkeypatch.setattr(m.os, name, 0, raising=False)
    descriptors = []
    original_pipe = os.pipe
    def pipe():
        values = original_pipe(); descriptors.extend(values); return values
    def popen(*a, **kwargs):
        assert kwargs["pass_fds"] == (descriptors[-1],)
        raise OSError("explicit synthetic spawn failure")
    monkeypatch.setattr(m.os, "pipe", pipe)
    monkeypatch.setattr(m.subprocess, "Popen", popen)
    root = tmp_path.resolve()/"spawn_failure"; root.mkdir()
    report = m._supervise(["not-run"], root, window=.1, receipt_required=True)
    assert report["status"] == "SUPERVISOR_ERROR_NO_PUBLICATION"
    assert report["child_result_sha256"] is None and report["publishable"] is False
    for descriptor in descriptors:
        with pytest.raises(OSError): os.fstat(descriptor)


@pytest.mark.parametrize("kind", ["outside", "duplicate", "sha", "bytes", "extra_key", "symlink"])
def test_publishable_bindings_exact_paths_and_bytes(tmp_path, kind):
    root = tmp_path.resolve()/"root"; root.mkdir()
    file = root/"safe.json"; file.write_bytes(b"{}")
    binding = {"origin_path": str(file), "sha256": m.sha(b"{}"), "bytes": 2}
    bindings = [binding]
    if kind == "outside": binding["origin_path"] = str(tmp_path.resolve()/"outside")
    elif kind == "duplicate": bindings.append(dict(binding))
    elif kind == "sha": binding["sha256"] = "0" * 64
    elif kind == "bytes": binding["bytes"] = 3
    elif kind == "extra_key": binding["source_authority"] = True
    else:
        alias = root/"alias"; alias.symlink_to(file); binding["origin_path"] = str(alias)
    with pytest.raises(ValueError): m._validate_bindings(bindings, root)
