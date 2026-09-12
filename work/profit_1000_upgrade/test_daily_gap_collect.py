"""Unit/CLI contracts using explicitly simulated HTTP; never market evidence."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from work.profit_1000_upgrade import daily_gap_collect as c

TOKEN = "SIMULATED_TEST_CREDENTIAL_40ddab8885"
RUN, COMMIT = "123456", "a" * 40


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, value): self.now += value


def envelope(contract, *, empty=True):
    api, p = contract["api_name"], contract["params"]
    row = [p["ts_code"], p["trade_date"]]
    row += [10.0, 11.0, 9.0, 10.5, 10.0, 1200.0, 12600.0, 5.0] if api == "daily" else [None, "S"]
    return {"request_id": "simulated-no-network", "code": 0, "msg": "", "detail": "",
            "data": {"fields": list(c.FIELDS[api]), "items": [] if empty else [row], "count": 0, "has_more": False}}


def raw(contract, *, empty=True): return c.json_bytes(envelope(contract, empty=empty))


@pytest.fixture
def run_env(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", RUN)
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)


@pytest.fixture
def fixture_plan(tmp_path):
    # Synthetic copy of the exact real registered contract, not a collection.
    path = tmp_path / "plan.json"
    path.write_bytes(c.json_bytes(c.expected_plan()))
    return path


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    def call(contract, token):
        assert token == TOKEN
        calls.append(copy.deepcopy(contract))
        return raw(contract)
    monkeypatch.setattr(c, "official_call", call)
    clock = Clock()
    original_budget = c.RequestBudget
    monkeypatch.setattr(c, "RequestBudget", lambda: original_budget(clock=clock, sleep=clock.sleep))
    return calls, clock


def collect(tmp_path, fixture_plan, **kwargs):
    options = dict(expected_plan_sha256=c.file_sha(fixture_plan), expected_label_report_sha256=c.LABEL_SHA,
                   expected_run_id=RUN, expected_run_commit=COMMIT, token=TOKEN, plan_path=fixture_plan)
    options.update(kwargs)
    return c.run_collection(tmp_path / "output", **options)


def test_checked_in_plan_is_exact():
    path = c.HERE / c.CONTRACT_FILE
    plan, _ = c.read_plan(path, c.file_sha(path), c.LABEL_SHA)
    assert plan == c.expected_plan()
    assert len(plan["requests"]) == 14
    assert [x["ordinal"] for x in plan["requests"]] == list(range(1, 15))
    assert sum(x["api_name"] == "daily" for x in plan["requests"]) == 13
    assert len(set(c.SCOPE)) == 14
    assert plan["label_source_eligible"] is False
    assert plan["nontrading_session_qualified"] is False
    assert all(x["signal_date"] <= "20260814" for x in plan["missing_daily_cases"])


@pytest.mark.parametrize("change", ["extra", "reorder", "duplicate", "future", "wrong_code", "wrong_endpoint", "retry", "more_calls", "label", "old_zip", "case", "flag", "ordinal", "fields", "page"])
def test_rehashed_plan_mutations_are_not_authority(tmp_path, fixture_plan, change):
    p = c.expected_plan()
    if change == "extra": p["unregistered"] = True
    if change == "reorder": p["requests"].reverse()
    if change == "duplicate": p["requests"][1] = copy.deepcopy(p["requests"][0])
    if change == "future": p["requests"][0]["params"]["trade_date"] = "20260914"
    if change == "wrong_code": p["requests"][0]["params"]["ts_code"] = "000001.SZ"
    if change == "wrong_endpoint": p["requests"][0]["api_name"] = "stk_auction"
    if change == "retry": p["retries"] = 1
    if change == "more_calls": p["max_api_calls"] = 15
    if change == "label": p["label_report_sha256"] = "b" * 64
    if change == "old_zip": p["diagnostic_archive"]["sha256"] = "b" * 64
    if change == "case": p["missing_daily_cases"][0]["missing_date"] = "20240501"
    if change == "flag": p["nontrading_session_qualified"] = True
    if change == "ordinal": p["requests"][0]["ordinal"] = True
    if change == "fields": p["requests"][0]["fields"].reverse()
    if change == "page": p["requests"][0]["params"]["offset"] = 1
    fixture_plan.write_bytes(c.json_bytes(p))
    with pytest.raises(ValueError): c.read_plan(fixture_plan, c.file_sha(fixture_plan), c.LABEL_SHA)


@pytest.mark.parametrize("plan_sha,label_sha", [("0" * 64, c.LABEL_SHA), ("x", c.LABEL_SHA), (None, c.LABEL_SHA), ("AUTO", c.LABEL_SHA), ("ACTUAL", "b" * 64)])
def test_external_sha_required(fixture_plan, plan_sha, label_sha):
    if plan_sha == "ACTUAL": plan_sha = c.file_sha(fixture_plan)
    with pytest.raises(ValueError): c.read_plan(fixture_plan, plan_sha, label_sha)


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "parent"])
def test_plan_alias_rejected(tmp_path, fixture_plan, alias):
    digest = c.file_sha(fixture_plan)
    path = tmp_path / "alias.json"
    if alias == "symlink": path.symlink_to(fixture_plan)
    elif alias == "hardlink": os.link(fixture_plan, path)
    else:
        directory = tmp_path / "directory"; directory.symlink_to(tmp_path, target_is_directory=True)
        path = directory / fixture_plan.name
    with pytest.raises(ValueError): c.read_plan(path, digest, c.LABEL_SHA)


@pytest.mark.parametrize("api,day,code", [("daily", "20260914", "600083.SH"), ("daily", "20240429", "600083.SH"), ("suspend_d", "20250611", "603226.SH"), ("stk_auction", "20250226", "000096.SZ"), ("daily", "20240430", "600083"), ("daily", 20240430, "600083.SH")])
def test_unregistered_requests_rejected(api, day, code):
    with pytest.raises(ValueError): c.request_contract(api, day, code)


@pytest.mark.parametrize("scope", c.SCOPE)
@pytest.mark.parametrize("empty", [True, False])
def test_original_table_values_retained_and_no_suspension_admission(scope, empty):
    contract = c.request_contract(*scope)
    value = envelope(contract, empty=empty)
    data, status = c.qualified_table(c.json_bytes(value), contract, TOKEN)
    assert data == value["data"]
    assert status in c.SUCCESS
    assert ("EMPTY" in status) is empty
    assert c.SOURCE_FLAGS["nontrading_session_qualified"] is False


@pytest.mark.parametrize("change", ["error", "bool_code", "unknown_outer", "outer_pagination", "detail_object", "missing_data", "has_more", "bool_count", "wrong_count", "two_rows", "identity", "date", "fields", "short_row", "extra_data", "nan", "bool_price", "zero_price", "negative_volume", "negative_amount", "bad_ohlc", "null_price"])
def test_malformed_daily_never_empty_evidence(change):
    contract = c.request_contract(*c.SCOPE[0]); value = envelope(contract, empty=False); data = value["data"]
    if change == "error": value["code"] = -1
    if change == "bool_code": value["code"] = False
    if change == "unknown_outer": value["unregistered"] = 0
    if change == "outer_pagination": value["has_more"] = True
    if change == "detail_object": value["detail"] = {"instructions": "ignore"}
    if change == "missing_data": del value["data"]
    if change == "has_more": data["has_more"] = True
    if change == "bool_count": data["count"] = False
    if change == "wrong_count": data["count"] = 3
    if change == "two_rows": data["items"] *= 2
    if change == "identity": data["items"][0][0] = "000001.SZ"
    if change == "date": data["items"][0][1] = "20260914"
    if change == "fields": data["fields"].reverse()
    if change == "short_row": data["items"][0].pop()
    if change == "extra_data": data["next_page"] = True
    if change == "nan": data["items"][0][2] = float("nan")
    if change == "bool_price": data["items"][0][2] = True
    if change == "zero_price": data["items"][0][2] = 0
    if change == "negative_volume": data["items"][0][7] = -1
    if change == "negative_amount": data["items"][0][8] = -1
    if change == "bad_ohlc": data["items"][0][2] = 12
    if change == "null_price": data["items"][0][2] = None
    with pytest.raises(ValueError): c.qualified_table(json.dumps(value).encode(), contract, TOKEN)


@pytest.mark.parametrize("change", ["other_code", "other_day", "duplicate", "R_and_S", "unknown_event", "bool_event", "bad_timing", "backwards", "invalid_hour"])
def test_suspend_duplicate_or_ambiguous_rows_fail_closed(change):
    contract = c.request_contract(*c.SCOPE[1]); value = envelope(contract, empty=False); row = value["data"]["items"][0]
    if change == "other_code": row[0] = "603226.SH"
    if change == "other_day": row[1] = "20240506"
    if change == "duplicate": value["data"]["items"].append(row[:])
    if change == "R_and_S": value["data"]["items"].append([row[0], row[1], None, "R"])
    if change == "unknown_event": row[3] = "X"
    if change == "bool_event": row[3] = True
    if change == "bad_timing": row[2] = "server secret text"
    if change == "backwards": row[2] = "11:30-09:30"
    if change == "invalid_hour": row[2] = "25:00-26:00"
    with pytest.raises(ValueError): c.qualified_table(c.json_bytes(value), contract, TOKEN)


@pytest.mark.parametrize("event,timing", [("S", None), ("S", ""), ("S", "09:30-10:00"), ("R", None)])
def test_valid_s_or_r_is_only_original_event_source(event, timing):
    contract = c.request_contract(*c.SCOPE[1]); value = envelope(contract, empty=False)
    value["data"]["items"][0][2:] = [timing, event]
    table, status = c.qualified_table(c.json_bytes(value), contract, TOKEN)
    assert table == value["data"] and status == "SUSPEND_EVENTS_SOURCE_WRITTEN"


@pytest.mark.parametrize("location", ["msg", "detail", "request_id", "key", "escaped", "data"])
def test_credentials_in_any_response_location_not_persisted(location):
    contract = c.request_contract(*c.SCOPE[0]); value = envelope(contract)
    if location == "key": value[TOKEN] = "value"
    elif location == "data": value["data"]["items"] = [[TOKEN]]
    else: value["msg" if location == "escaped" else location] = TOKEN
    body = c.json_bytes(value)
    if location == "escaped": body = body.replace(TOKEN.encode(), "".join("\\u%04x" % ord(ch) for ch in TOKEN).encode())
    with pytest.raises(ValueError, match="CREDENTIAL"): c.qualified_table(body, contract, TOKEN)


@pytest.mark.parametrize("body", [b'{"code":0,"code":1,"data":{}}', b'NaN', b'Infinity', b'{"code":0,"data":{},"msg":1e999}', b'', b'x' * (c.MAX_BYTES + 1)])
def test_duplicate_nonfinite_and_oversize_json(body):
    with pytest.raises(ValueError): c.qualified_table(body, c.request_contract(*c.SCOPE[0]), TOKEN)


def test_budget_half_second_spacing_and_exact_cap():
    clock = Clock(); budget = c.RequestBudget(clock=clock, sleep=clock.sleep)
    assert [budget.reserve() for _ in range(14)] == [(i + 1, i * .5) for i in range(14)]
    assert budget.reserve() is None


def test_budget_headroom_and_sleep_crossing_deadline():
    clock = Clock(); budget = c.RequestBudget(clock=clock, sleep=clock.sleep)
    clock.now = 578.75; assert budget.reserve() == (1, 578.75)
    assert budget.reserve() is None and budget.calls == 1
    clock.now = 579; assert budget.reserve() is None


def test_all_fourteen_simulated_sources_bound_and_readback(tmp_path, fixture_plan, run_env, fake_http):
    calls, clock = fake_http
    report = collect(tmp_path, fixture_plan)
    root = tmp_path / "output"
    assert report["status"] == "DAILY_GAP_SOURCES_COLLECTED" and report["qualified_source_pairs"] == 14
    assert len(calls) == report["api_calls"] == 14
    assert calls == [{k: v for k, v in q.items() if k != "ordinal"} for q in c.expected_plan()["requests"]]
    assert report["request_status_counts"] == {"DAILY_EMPTY_SOURCE_WRITTEN": 13, "SUSPEND_EMPTY_SOURCE_WRITTEN": 1}
    assert len(report["source_files"]) == 28 and len(report["output_file_bindings"]) == 30
    assert len(list(p for p in root.rglob('*') if p.is_file())) == 31
    journal = [c.parse(line) for line in (root / c.JOURNAL_FILE).read_bytes().splitlines()]
    assert journal == report["requests"]
    for row in report["requests"]:
        assert row["source_rows"] == 0 and len(row["source_files"]) == 2
        data, meta = [root / b["path"] for b in row["source_files"]]
        assert c.parse(meta.read_bytes()) == c.metadata(data.read_bytes(), row)
        assert c.parse(data.read_bytes())["items"] == []
        assert TOKEN.encode() not in meta.read_bytes()
    assert c.parse((root / c.RECEIPT_FILE).read_bytes()) == report
    assert all(report[key] is value for key, value in c.FLAGS.items())


@pytest.mark.parametrize("failure", ["bad_json", "error_envelope", "identity", "timeout", "http", "transport", "credential"])
def test_one_failure_retained_no_retry_and_no_fake_pair(tmp_path, fixture_plan, run_env, fake_http, monkeypatch, failure):
    calls, _ = fake_http; good = c.official_call
    def caller(contract, token):
        if not calls:
            calls.append(copy.deepcopy(contract))
            if failure == "bad_json": return b"not json"
            if failure == "error_envelope": return b'{"code":-1,"data":null,"msg":"SERVER_PRIVATE_TEXT"}'
            if failure == "identity":
                value = envelope(contract, empty=False); value["data"]["items"][0][1] = "20260914"; return c.json_bytes(value)
            if failure == "timeout": raise c.TransportFailure("TRANSPORT_TIMEOUT")
            if failure == "http": raise c.TransportFailure("HTTP_ERROR")
            if failure == "transport": raise OSError("SERVER_PRIVATE_TEXT")
            return c.json_bytes({**envelope(contract), "msg": token})
        return good(contract, token)
    monkeypatch.setattr(c, "official_call", caller)
    report = collect(tmp_path, fixture_plan)
    assert len(calls) == 14 and report["qualified_source_pairs"] == 13
    assert report["status"] == "DAILY_GAP_SOURCES_PARTIAL"
    assert report["requests"][0]["source_files"] == [] and report["requests"][0]["source_rows"] is None
    assert report["requests"][0]["status"].startswith("PENDING_")
    assert len(report["source_files"]) == 26
    persisted = b"".join(p.read_bytes() for p in (tmp_path / "output").rglob('*') if p.is_file())
    assert TOKEN.encode() not in persisted and b"SERVER_PRIVATE_TEXT" not in persisted


def test_missing_credential_zero_http_complete_blocked_receipt(tmp_path, fixture_plan, run_env, fake_http):
    calls, _ = fake_http; report = collect(tmp_path, fixture_plan, token="")
    assert calls == [] and report["api_calls"] == 0
    assert len(report["requests"]) == 14 and report["qualified_source_pairs"] == 0
    assert report["status"] == "DAILY_GAP_SOURCES_BLOCKED"
    assert report["request_status_counts"] == {"PENDING_CREDENTIAL_ABSENT": 14}
    assert (tmp_path / "output" / c.JOURNAL_FILE).read_bytes() == b""


def test_wall_budget_preserves_unattempted_slots(tmp_path, fixture_plan, run_env, fake_http, monkeypatch):
    calls, clock = fake_http; good = c.official_call
    def caller(contract, token):
        body = good(contract, token); clock.now = 600; return body
    monkeypatch.setattr(c, "official_call", caller)
    report = collect(tmp_path, fixture_plan)
    assert len(calls) == 1 and report["api_calls"] == 1 and report["qualified_source_pairs"] == 1
    assert report["request_status_counts"]["NOT_REQUESTED_WALL_BUDGET"] == 13
    assert all(x["http_response_sha256"] is None and not x["network_request_performed"] for x in report["requests"][1:])


@pytest.mark.parametrize("change", ["run", "commit", "attempt", "missing_attempt", "expected_run", "expected_commit", "injection", "label_sha", "plan_sha"])
def test_environment_and_entry_guard_before_writes(tmp_path, fixture_plan, run_env, fake_http, monkeypatch, change):
    kwargs = {}
    if change == "run": monkeypatch.setenv("GITHUB_RUN_ID", "999")
    if change == "commit": monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    if change == "attempt": monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    if change == "missing_attempt": monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    if change == "expected_run": kwargs["expected_run_id"] = 123
    if change == "expected_commit": kwargs["expected_run_commit"] = "invalid"
    if change == "injection": kwargs["call"] = lambda: None
    if change == "label_sha": kwargs["expected_label_report_sha256"] = "b" * 64
    if change == "plan_sha": kwargs["expected_plan_sha256"] = "b" * 64
    with pytest.raises(ValueError): collect(tmp_path, fixture_plan, **kwargs)
    assert not (tmp_path / "output").exists() and fake_http[0] == []


@pytest.mark.parametrize("alias", ["existing", "symlink", "broken_symlink", "parent_symlink"])
def test_output_must_be_fresh_unaliased(tmp_path, fixture_plan, run_env, fake_http, alias):
    output = tmp_path / "output"
    if alias == "existing": output.mkdir()
    if alias == "symlink": output.symlink_to(tmp_path, target_is_directory=True)
    if alias == "broken_symlink": output.symlink_to(tmp_path / "absent")
    if alias == "parent_symlink":
        linked = tmp_path / "linked"; linked.symlink_to(tmp_path, target_is_directory=True)
        tmp_path = linked
    with pytest.raises(ValueError): collect(tmp_path, fixture_plan)
    assert fake_http[0] == []


def test_rerun_refused_without_overwriting_previous_bytes(tmp_path, fixture_plan, run_env, fake_http):
    collect(tmp_path, fixture_plan); root = tmp_path / "output"
    original = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): collect(tmp_path, fixture_plan)
    assert original == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize("mutation", ["source", "plan", "extra", "extra_dir", "hardlink", "symlink", "fifo", "journal", "code_guard"])
def test_midrun_mutations_fail_closed(tmp_path, fixture_plan, run_env, fake_http, monkeypatch, mutation):
    def progress(item):
        if item["ordinal"] != 14: return
        root = tmp_path / "output"; source = next(root.rglob('*.data.json'))
        if mutation == "source": source.write_bytes(source.read_bytes() + b" ")
        if mutation == "plan": fixture_plan.write_bytes(fixture_plan.read_bytes() + b" ")
        if mutation == "extra": (root / "extra.txt").write_text("not registered")
        if mutation == "extra_dir": (root / "extra").mkdir()
        if mutation == "hardlink": os.link(source, root / "hardlink")
        if mutation == "symlink": (root / "linked").symlink_to(source)
        if mutation == "fifo": os.mkfifo(root / "named_pipe")
        if mutation == "journal": (root / c.JOURNAL_FILE).write_bytes(b"")
        if mutation == "code_guard": monkeypatch.setattr(c, "_IMPORTED_SHA", "b" * 64)
    with pytest.raises(ValueError): collect(tmp_path, fixture_plan, progress=progress)
    assert not (tmp_path / "output" / c.RECEIPT_FILE).exists()


def test_partial_pair_io_error_is_not_final_success(tmp_path, fixture_plan, run_env, fake_http, monkeypatch):
    original_write = c.write
    def writer(root, relative, body, token):
        if relative.endswith('.meta.json'): raise OSError("simulated disk failure")
        return original_write(root, relative, body, token)
    monkeypatch.setattr(c, "write", writer)
    with pytest.raises(OSError): collect(tmp_path, fixture_plan)
    root = tmp_path / "output"
    assert len(list(root.rglob('*.data.json'))) == 1
    assert not list(root.rglob('*.meta.json')) and not (root / c.RECEIPT_FILE).exists()


def test_journal_is_incremental_before_next_http(tmp_path, fixture_plan, run_env, fake_http, monkeypatch):
    good = c.official_call; count = 0
    def caller(contract, token):
        nonlocal count
        journal = (tmp_path / "output" / c.JOURNAL_FILE).read_bytes().splitlines()
        assert len(journal) == count
        count += 1
        return good(contract, token)
    monkeypatch.setattr(c, "official_call", caller)
    collect(tmp_path, fixture_plan)
    assert count == 14


def test_real_transport_function_rejects_scope_before_process(monkeypatch):
    monkeypatch.setattr(c.multiprocessing, "get_context", lambda _: pytest.fail("must not create transport"))
    with pytest.raises(ValueError): c.official_call({"api_name": "daily", "params": {"trade_date": "20260914", "ts_code": "600083.SH"}}, TOKEN)


def test_no_redirect_handler():
    assert c.NoRedirect().redirect_request(None, None, 302, "secret", {}, "https://evil.invalid") is None


def test_parent_deadline_stops_simulated_hung_worker_without_http(monkeypatch):
    def hung(connection, contract, token): time.sleep(10)
    monkeypatch.setattr(c, "_transport_worker", hung)
    monkeypatch.setattr(c, "TRANSPORT_TIMEOUT", .05)
    start = time.monotonic()
    with pytest.raises(c.TransportFailure, match="TRANSPORT_TIMEOUT"):
        c.official_call(c.request_contract(*c.SCOPE[0]), TOKEN)
    assert time.monotonic() - start < 2


@pytest.mark.parametrize("packet,expected", [(b'S{}', b'{}'), (b'EHTTP_ERROR', 'HTTP_ERROR'), (b'EPRETEND_SERVER_SECRET', 'TRANSPORT_ERROR'), (b'EHTTP_RESPONSE_BYTE_LIMIT', 'HTTP_RESPONSE_BYTE_LIMIT')])
def test_transport_ipc_safe_fixed_errors_without_http(monkeypatch, packet, expected):
    def simulated(connection, contract, token): connection.send_bytes(packet); connection.close()
    monkeypatch.setattr(c, "_transport_worker", simulated)
    if isinstance(expected, bytes): assert c.official_call(c.request_contract(*c.SCOPE[0]), TOKEN) == expected
    else:
        with pytest.raises(c.TransportFailure, match='^' + expected + '$'):
            c.official_call(c.request_contract(*c.SCOPE[0]), TOKEN)


def test_cli_no_credentials_is_bounded_no_network_blocked_receipt(tmp_path, fixture_plan, run_env):
    env = {**os.environ, "TUSHARE_TOKEN": "", "PYTHONDONTWRITEBYTECODE": "1"}
    before = list(tmp_path.rglob('__pycache__'))
    result = subprocess.run([sys.executable, '-B', str(c.HERE / 'daily_gap_collect.py'), '--output', str(tmp_path / 'cli-output'),
        '--registered-plan', str(fixture_plan), '--expected-plan-sha256', c.file_sha(fixture_plan),
        '--expected-label-report-sha256', c.LABEL_SHA], capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 2
    report = c.parse((tmp_path / 'cli-output' / c.RECEIPT_FILE).read_bytes())
    assert report['api_calls'] == 0 and len(report['requests']) == 14
    assert report['status'] == 'DAILY_GAP_SOURCES_BLOCKED'
    assert list(tmp_path.rglob('__pycache__')) == before
    assert TOKEN not in result.stdout + result.stderr


def test_cli_invalid_sha_exits_one_without_output(tmp_path, fixture_plan, run_env):
    result = subprocess.run([sys.executable, '-B', str(c.HERE / 'daily_gap_collect.py'), '--output', str(tmp_path / 'cli-output'),
        '--registered-plan', str(fixture_plan), '--expected-plan-sha256', 'b' * 64,
        '--expected-label-report-sha256', c.LABEL_SHA], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and not (tmp_path / 'cli-output').exists()
    assert result.stderr.strip() == 'DAILY_GAP_COLLECTION_FAILED_CLOSED'
