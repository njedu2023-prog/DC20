"""Synthetic, no-network collector contracts; no real base ZIP or token is read.

Full-run tests replace the expensive immutable-base verifier, code snapshot,
and official transport with explicit in-process synthetic fixtures. Source
parsing/readback stays real. These test outputs are not genuine run evidence.
Every clock/sleep used by RequestBudget is injected: no real throttling waits.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib import error
import zipfile

import pytest

from work.profit_1000_upgrade import stocks_scope_collect as collect

TOKEN = "synthetic_test_credential_not_for_network"
CODE, SECOND, OTHER = "600001.SH", "600002.SH", "600003.SH"
DAY = "20250319"
Budget = collect.RequestBudget


def forbidden(*args, **kwargs):
    raise AssertionError("real network or real sleep forbidden in synthetic tests")


@pytest.fixture(autouse=True)
def no_network_or_secret_access(monkeypatch):
    monkeypatch.setattr(collect.request, "build_opener", forbidden)
    monkeypatch.setattr(collect.time, "sleep", forbidden)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)


class FakeClock:
    def __init__(self, value=0.0):
        self.value, self.sleeps = value, []
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def sleep(self, seconds):
        assert seconds >= 0
        with self.lock:
            self.sleeps.append(seconds)
            self.value += seconds

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


def budget(clock=None):
    clock = clock or FakeClock()
    return Budget(clock=clock, sleep=clock.sleep)


def scope212():
    days = set(collect.inputs.PREFLIGHT_T_DATES)
    current = date(2025, 3, 1)
    while len(days) < 212:
        if current.weekday() < 5:
            days.add(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    days = sorted(days)
    return {"code_bindings": [], "gap_dates": days,
            "gap_candidate_codes": {day: [CODE, SECOND] for day in days},
            "preflight_T_dates": list(collect.inputs.PREFLIGHT_T_DATES),
            "reused_dates": ["20250116"], "precoverage_dates": ["20241231"],
            "frozen_manifest": {"synthetic_fixture": True, "candidates": []},
            "base_archive": {"synthetic_fixture": True, "archive_sha256": "a" * 64}}


def response(day=DAY, *, codes=(CODE,), data_changes=None, envelope_changes=None):
    data = {"fields": list(collect.source.FIELDS),
            "items": [[code, day, 10, 100, 1000, 9] for code in codes],
            "count": len(codes), "has_more": False}
    data.update(data_changes or {})
    envelope = {"code": 0, "detail": "...", "data": data, "msg": "", "request_id": "synthetic"}
    envelope.update(envelope_changes or {})
    return json.dumps(envelope, allow_nan=False).encode()


def one(tmp_path, *, scope=None, day=DAY, call=None, injected=False, token=TOKEN, request_budget=None):
    return collect._collect_one(tmp_path, scope or scope212(), day, token,
                                call or (lambda request, token: response(day)), injected,
                                request_budget or budget())


@pytest.fixture
def full_run(tmp_path, monkeypatch):
    """Explicitly fake the 43MB base audit, but verify byte-exact copy at each check."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("old/source.json", b'{"preserve":"all original bytes"}\n')
        archive.writestr("old/non_source.bin", b"\x00\xff\x01" * 100)
    original = stream.getvalue()
    base = tmp_path / "synthetic-base.zip"
    base.write_bytes(original)
    scope, verified, guards = scope212(), [], []
    monkeypatch.setattr(collect.inputs, "read_base_archive", lambda path: scope)
    def verify(path):
        assert Path(path).read_bytes() == original
        verified.append(Path(path))
        return hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(collect.inputs, "verify_base_unchanged", verify)
    monkeypatch.setattr(collect, "_code_snapshot", lambda _: {"synthetic/code.py": "b" * 64})
    monkeypatch.setattr(collect, "_guard", lambda code: guards.append(dict(code)))
    clock = FakeClock()
    monkeypatch.setattr(collect, "RequestBudget", lambda: budget(clock))
    return {"base": base, "output": tmp_path / "new-output", "scope": scope,
            "original": original, "verified": verified, "guards": guards, "clock": clock}


def test_registered_limits_and_real_stk_contract():
    contract = collect.expected_contract()
    assert (collect.MAX_CALLS, collect.MAX_SECONDS, collect.START_INTERVAL, collect.MAX_WORKERS) == (212, 1200, 1.0, 2)
    assert contract["max_api_calls"] == 212 and contract["retries"] == 0
    assert contract["timeout_seconds"] == 20 and contract["max_http_response_bytes"] == 4_000_000
    assert contract["params"] == {"trade_date": "FROZEN_GAP_T_DATE", "ts_type": "STK"}
    assert contract["preflight_T_dates"] == ["20250319", "20250826", "20260203"]
    assert contract["preflight_rule"] == "COMPLETE_NONEMPTY_TABLE_WITH_AT_LEAST_ONE_FROZEN_CANDIDATE"
    assert not contract["pagination_or_filter_fallback_allowed"] and not contract["redirects_allowed"]
    assert not contract["label_rebuild_performed"] and not contract["training_performed"]


def test_budget_caps_212_one_second_starts_without_real_sleep():
    clock = FakeClock()
    limited = budget(clock)
    assert [limited.reserve() for _ in range(215)] == list(range(1, 213)) + [None] * 3
    assert limited.calls == 212 and clock() == 211
    assert clock.sleeps == [1.0] * 211


def test_budget_is_thread_safe_and_reserves_attempts_only_once():
    clock = FakeClock()
    limited = budget(clock)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limited.reserve(), range(260)))
    assert sorted(r for r in results if r is not None) == list(range(1, 213))
    assert results.count(None) == 48 and limited.calls == 212
    assert clock() == 211


@pytest.mark.parametrize("elapsed,allowed", [(1179.9, True), (1180, False), (1200, False), (1201, False)])
def test_budget_reserves_timeout_headroom(elapsed, allowed):
    clock = FakeClock()
    limited = budget(clock)
    clock.advance(elapsed)
    assert (limited.reserve() == 1) is allowed
    assert limited.calls == int(allowed)


def test_budget_checks_deadline_again_after_rate_sleep():
    clock = FakeClock()
    limited = budget(clock)
    clock.advance(1179.5)
    assert limited.reserve() == 1
    assert limited.reserve() is None
    assert clock() == 1180.5 and limited.calls == 1


def test_official_transport_https_post_exact_original_params_and_no_redirect(monkeypatch):
    seen = {}
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            seen["read_limit"] = size
            return response()
    class Opener:
        def open(self, req, timeout):
            seen.update(url=req.full_url, method=req.method, payload=json.loads(req.data), timeout=timeout)
            return Reply()
    def build(*handlers):
        assert len(handlers) == 1 and isinstance(handlers[0], collect._NoRedirect)
        assert handlers[0].redirect_request(None, None, 302, TOKEN, {}, "http://other") is None
        return Opener()
    monkeypatch.setattr(collect.request, "build_opener", build)
    assert collect.official_call(collect.source.request_contract(DAY), TOKEN) == response()
    assert seen == {"url": "https://api.tushare.pro", "method": "POST", "timeout": 20,
        "read_limit": 4_000_001, "payload": {"api_name": "stk_auction", "params": {"trade_date": DAY, "ts_type": "STK"},
                                              "fields": ",".join(collect.source.FIELDS), "token": TOKEN}}


@pytest.mark.parametrize("change", ["offset", "limit", "ts_code", "remove_stk", "ETF", "fields", "api"])
def test_transport_rejects_altered_request_before_network(change):
    contract = collect.source.request_contract(DAY)
    if change in ("offset", "limit", "ts_code"):
        contract["params"][change] = 1
    elif change == "remove_stk": del contract["params"]["ts_type"]
    elif change == "ETF": contract["params"]["ts_type"] = "ETF"
    elif change == "fields": contract["fields"].reverse()
    else: contract["api_name"] = "daily"
    with pytest.raises(ValueError, match="TRANSPORT_REQUEST_CHANGED"):
        collect.official_call(contract, TOKEN)


@pytest.mark.parametrize("raw", [b"", b"x" * 4_000_001, None, "text"])
def test_transport_response_limit_is_checked_without_retry(monkeypatch, raw):
    opens = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return raw
    class Opener:
        def open(self, req, timeout):
            opens.append(req)
            return Reply()
    monkeypatch.setattr(collect.request, "build_opener", lambda *_: Opener())
    with pytest.raises(ValueError, match="HTTP_RESPONSE_BYTE_LIMIT"):
        collect.official_call(collect.source.request_contract(DAY), TOKEN)
    assert len(opens) == 1


def test_collect_success_original_request_hash_missing_codes_and_no_fallback(tmp_path):
    calls = []
    raw = response(codes=(CODE, OTHER))
    def fake(request, token):
        calls.append(request)
        assert token == TOKEN
        return raw
    item = one(tmp_path, call=fake)
    assert calls == [collect.source.request_contract(DAY)]
    assert item["status"] == "STOCKS_SCOPED_TABLE_PRESENT" and item["api_calls"] == 1
    assert item["rows"] == 2 and item["present_candidate_count"] == 1
    assert item["missing_candidate_codes"] == [SECOND]
    assert item["http_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert item["http_response_bytes"] == len(raw) and len(item["source_files"]) == 2
    # Internal unit exercises the production branch; fixture bytes are NOT a real source artifact.
    assert not item["callable_injected_for_test"] and item["network_request_performed"]
    assert all("sha256" in item for item in item["source_files"])
    assert {p.name for p in tmp_path.rglob("*") if p.is_file()} == {"data.json", "meta.json"}
    metadata = json.loads(collect.source.source_paths(tmp_path, DAY)[1].read_bytes())
    assert metadata["request"] == collect.source.request_contract(DAY)
    assert not metadata["fallback_generated"] and not metadata["label_source_eligible"]
    assert not metadata["actual_execution_claimed"] and not metadata["actual_capacity_verified"]


def test_collect_empty_is_observed_source_not_no_trade_or_zero(tmp_path):
    item = one(tmp_path, call=lambda *_: response(codes=()))
    assert item["status"] == "STOCKS_SCOPED_TABLE_EMPTY" and item["rows"] == 0
    assert item["present_candidate_count"] == 0 and item["missing_candidate_codes"] == [CODE, SECOND]
    assert not collect.preflight_ok(item)
    assert not any(k in item for k in ("price", "return", "net_return", "fallback"))


@pytest.mark.parametrize("kind", ["invalid_json", "wrong_date", "duplicate", "paging", "error_code", "secret",
                                  "network", "http", "oversize", "notbytes"])
def test_collect_failure_never_writes_source_and_preserves_unknown(tmp_path, kind):
    calls = []
    def fake(*args):
        calls.append(args)
        if kind == "network": raise OSError(TOKEN + " private server error")
        if kind == "http": raise error.HTTPError("https://api.tushare.pro", 429, TOKEN, {}, None)
        if kind == "invalid_json": return b"not json"
        if kind == "wrong_date": return response("20250320")
        if kind == "duplicate": return response(codes=(CODE, CODE))
        if kind == "paging": return response(data_changes={"has_more": True})
        if kind == "error_code": return response(envelope_changes={"code": -1, "msg": TOKEN})
        if kind == "secret": return response(envelope_changes={"msg": TOKEN})
        if kind == "oversize": return b"x" * 4_000_001
        return "nonbytes"
    item = one(tmp_path, call=fake)
    assert len(calls) == item["api_calls"] == 1
    assert item["status"].startswith("PENDING_") and not collect.preflight_ok(item)
    assert item["source_files"] == [] and item["rows"] is None and item["missing_candidate_codes"] is None
    assert item["present_candidate_count"] is None and not list(tmp_path.iterdir())
    assert TOKEN not in json.dumps(item) and "private server error" not in json.dumps(item)


@pytest.mark.parametrize("token", ["", "  ", "\n"])
def test_missing_token_never_consumes_budget_or_creates_source(tmp_path, token):
    limited = budget()
    item = one(tmp_path, token=token, call=forbidden, request_budget=limited)
    assert item["status"] == "PENDING_CREDENTIAL_ABSENT" and item["api_calls"] == limited.calls == 0
    assert not list(tmp_path.iterdir())


def test_exhausted_budget_does_not_request_or_write(tmp_path):
    limited = budget()
    limited.calls = 212
    item = one(tmp_path, call=forbidden, request_budget=limited)
    assert item["status"] == "NOT_REQUESTED_BUDGET_EXHAUSTED" and item["api_calls"] == 0
    assert not list(tmp_path.iterdir())


def test_source_write_failure_is_fatal_not_network_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "_write", lambda *args: (_ for _ in ()).throw(OSError("disk write failed")))
    with pytest.raises(OSError, match="disk write failed"):
        one(tmp_path)


def test_source_readback_binding_mismatch_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(collect.source, "load", lambda *args: SimpleNamespace(source_files=()))
    with pytest.raises(ValueError, match="SOURCE_READBACK_MISMATCH"):
        one(tmp_path)


@pytest.mark.parametrize("failure_index", [0, 1, 2])
@pytest.mark.parametrize("failure", ["empty", "unrelated", "network"])
def test_preflight_serial_stops_on_first_failure_before_remaining_209(tmp_path, failure_index, failure):
    scope, calls, progress = scope212(), [], []
    main_thread = threading.get_ident()
    def fake(request, token):
        day = request["params"]["trade_date"]
        assert threading.get_ident() == main_thread
        calls.append(day)
        if len(calls) - 1 == failure_index:
            if failure == "network": raise OSError(TOKEN)
            return response(day, codes=() if failure == "empty" else (OTHER,))
        return response(day)
    rows, passed = collect._run_requests(tmp_path, scope, TOKEN, fake, False, budget(), progress.append)
    assert not passed and calls == scope["preflight_T_dates"][:failure_index + 1]
    assert len(rows) == 212 and {r["trade_date"] for r in rows} == set(scope["gap_dates"])
    assert sum(r["api_calls"] for r in rows) == failure_index + 1
    untouched = [r for r in rows if not r["api_calls"]]
    assert len(untouched) == 211 - failure_index
    assert all(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" and r["rows"] is None for r in untouched)
    assert [p["phase"] for p in progress] == ["preflight"] * (failure_index + 1)


def test_full_212_only_after_three_successful_preflights_and_at_most_two_workers(tmp_path, monkeypatch):
    scope, calls, thread_ids = scope212(), [], []
    lock, active, peak = threading.Lock(), 0, 0
    main_thread = threading.get_ident()
    actual_pool = collect.ThreadPoolExecutor
    pool_sizes = []
    def pool(*args, **kwargs):
        pool_sizes.append(kwargs["max_workers"])
        return actual_pool(*args, **kwargs)
    monkeypatch.setattr(collect, "ThreadPoolExecutor", pool)
    def fake(request, token):
        nonlocal active, peak
        day = request["params"]["trade_date"]
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(day)
            thread_ids.append(threading.get_ident())
            if len(calls) <= 3:
                assert threading.get_ident() == main_thread
            else:
                assert calls[:3] == scope["preflight_T_dates"]
                assert threading.get_ident() != main_thread
        raw = response(day)
        with lock: active -= 1
        return raw
    limited = budget()
    rows, passed = collect._run_requests(tmp_path, scope, TOKEN, fake, False, limited)
    assert passed and len(calls) == len(set(calls)) == limited.calls == 212
    assert set(calls) == set(scope["gap_dates"]) and calls[:3] == scope["preflight_T_dates"]
    assert pool_sizes == [2] and peak <= 2
    assert all(r["status"] == "STOCKS_SCOPED_TABLE_PRESENT" for r in rows)
    assert sorted(r["request_sequence"] for r in rows) == list(range(1, 213))
    assert sum(r["preflight"] for r in rows) == 3


def test_nonpreflight_failure_is_not_retried_or_replaced_by_fallback(tmp_path):
    scope, calls = scope212(), []
    failed = next(day for day in scope["gap_dates"] if day not in scope["preflight_T_dates"])
    def fake(request, token):
        day = request["params"]["trade_date"]
        calls.append(day)
        if day == failed: raise OSError(TOKEN)
        return response(day)
    rows, passed = collect._run_requests(tmp_path, scope, TOKEN, fake, False, budget())
    assert passed and len(calls) == 212 and calls.count(failed) == 1
    item = next(r for r in rows if r["trade_date"] == failed)
    assert item["status"] == "PENDING_NETWORK_OR_RESPONSE_ERROR" and item["source_files"] == []
    assert item["rows"] is None and item["missing_candidate_codes"] is None


def test_offline_internal_full_receipt_copies_whole_zip_unchanged(full_run, monkeypatch):
    f = full_run
    calls = []
    def fake(request, token):
        day = request["params"]["trade_date"]
        calls.append(day)
        return response(day)
    monkeypatch.setattr(collect, "official_call", fake)
    report = collect.run_collection(f["base"], f["output"], token=TOKEN)
    assert report["status"] == "SOURCE_GAPS_COLLECTED" and report["api_calls"] == 212
    assert report["qualified_new_source_dates"] == 212 and report["qualified_source_dates_including_base"] == 393
    assert not report["callable_injected_for_test"]
    # This is a monkeypatched internal unit, never an independently verified real run.
    assert report["base_archive"]["synthetic_fixture"] is True
    assert not report["remaining_gap_dates"] and len(report["source_files"]) == 424
    assert (f["output"] / "base_v3.zip").read_bytes() == f["original"]
    assert f["verified"] == [f["output"] / "base_v3.zip", f["base"], f["output"] / "base_v3.zip"]
    assert len(f["guards"]) == 2
    saved = json.loads((f["output"] / collect.RECEIPT_FILE).read_bytes())
    assert saved == report and TOKEN not in json.dumps(saved)
    journal = [json.loads(line) for line in (f["output"] / collect.JOURNAL_FILE).read_bytes().splitlines()]
    assert [r["request_sequence"] for r in journal] == list(range(1, 213))
    assert all(r["request"]["params"]["ts_type"] == "STK" for r in journal)
    assert len(report["output_file_bindings"]) == 427
    assert {p.relative_to(f["output"]).as_posix() for p in f["output"].rglob("*") if p.is_file()} == {
        collect.RECEIPT_FILE, *[binding["path"] for binding in report["output_file_bindings"]]}
    assert all(report[k] == v for k, v in collect.FLAGS.items())
    with pytest.raises(ValueError, match="FRESH_UNALIASED"):
        collect.run_collection(f["base"], f["output"], token=TOKEN)


def test_no_token_receipt_preserves_all_212_gaps_and_zero_requests(full_run):
    f = full_run
    report = collect.run_collection(f["base"], f["output"], token="")
    assert report["status"] == "PREFLIGHT_BLOCKED" and report["api_calls"] == 0
    assert report["qualified_new_source_dates"] == 0 and len(report["remaining_gap_dates"]) == 212
    assert report["source_files"] == [] and len(report["requests"]) == 212
    assert (f["output"] / collect.JOURNAL_FILE).read_bytes() == b""
    assert len(report["output_file_bindings"]) == 3


def test_failed_preflight_receipt_does_not_claim_success(full_run, monkeypatch):
    f = full_run
    monkeypatch.setattr(collect, "official_call", lambda *_: b"bad")
    report = collect.run_collection(f["base"], f["output"], token=TOKEN)
    assert report["status"] == "PREFLIGHT_BLOCKED" and report["api_calls"] == 1
    assert report["qualified_new_source_dates"] == 0 and not report["preflight_passed"]
    assert len(report["remaining_gap_dates"]) == 212
    assert report["new_source_status_counts"] == {"NOT_REQUESTED_PREFLIGHT_BLOCKED": 211, "PENDING_INVALID_STOCKS_SOURCE": 1}


def test_partial_receipt_distinguishes_complete_empty_from_unknown_failed_day(full_run, monkeypatch):
    f = full_run
    remaining = [day for day in f["scope"]["gap_dates"] if day not in f["scope"]["preflight_T_dates"]]
    empty, failed = remaining[:2]
    calls = []
    def fake(request, token):
        day = request["params"]["trade_date"]
        calls.append(day)
        if day == failed: raise OSError(TOKEN)
        return response(day, codes=() if day == empty else (CODE,))
    monkeypatch.setattr(collect, "official_call", fake)
    report = collect.run_collection(f["base"], f["output"], token=TOKEN)
    assert report["status"] == "SOURCE_GAPS_PARTIAL" and report["preflight_passed"]
    assert report["api_calls"] == 212 and len(calls) == len(set(calls)) == 212
    assert report["qualified_new_source_dates"] == 211 and report["qualified_source_dates_including_base"] == 392
    assert report["remaining_gap_dates"] == [failed]
    records = {r["trade_date"]: r for r in report["requests"]}
    assert records[empty]["status"] == "STOCKS_SCOPED_TABLE_EMPTY"
    assert records[empty]["missing_candidate_codes"] == [CODE, SECOND]
    assert records[empty]["rows"] == 0 and len(records[empty]["source_files"]) == 2
    assert records[failed]["rows"] is None and records[failed]["missing_candidate_codes"] is None
    assert records[failed]["source_files"] == []


@pytest.mark.parametrize("tamper", ["extra_file", "mutate_source"])
def test_late_output_changes_fail_before_receipt(full_run, monkeypatch, tamper):
    f = full_run
    count = 0
    def fake(request, token):
        nonlocal count
        count += 1
        day = request["params"]["trade_date"]
        if count == 2:
            if tamper == "extra_file":
                (f["output"] / "unregistered.json").write_bytes(b"{}")
            else:
                path = collect.source.source_paths(f["output"], f["scope"]["preflight_T_dates"][0])[0]
                path.write_bytes(b"{}")
            return b"bad"
        return response(day)
    monkeypatch.setattr(collect, "official_call", fake)
    with pytest.raises(ValueError, match="(COLLECTED_SOURCE_CHANGED|UNREGISTERED_OUTPUT_FILE)"):
        collect.run_collection(f["base"], f["output"], token=TOKEN)
    assert not (f["output"] / collect.RECEIPT_FILE).exists()


@pytest.mark.parametrize("attempt", ["0", "2", "01", "invalid"])
def test_workflow_rerun_rejected_before_base_or_network(tmp_path, monkeypatch, attempt):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    monkeypatch.setattr(collect.inputs, "read_base_archive", forbidden)
    with pytest.raises(ValueError, match="RERUN_NOT_ALLOWED"):
        collect.run_collection(tmp_path / "base", tmp_path / "out", token=TOKEN)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["symlink", "existing", "checkout", "checkout_child"])
def test_output_must_be_fresh_unaliased_outside_checkout(full_run, monkeypatch, name):
    f = full_run
    if name == "symlink":
        actual = f["output"].parent / "real-output"
        actual.mkdir()
        f["output"].symlink_to(actual, target_is_directory=True)
        output = f["output"]
    elif name == "existing":
        f["output"].mkdir()
        output = f["output"]
    else:
        checkout = f["output"].parent / "fake-checkout"
        monkeypatch.setattr(collect, "CHECKOUT", checkout)
        output = checkout if name == "checkout" else checkout / "child"
    with pytest.raises(ValueError, match="(FRESH_UNALIASED|OUTSIDE_CHECKOUT)"):
        collect.run_collection(f["base"], output, token=TOKEN)


def test_write_is_exclusive_and_token_checked_before_file_creation(tmp_path):
    binding = collect._write(tmp_path, "safe/nested.json", b"safe", TOKEN)
    assert binding == {"path": "safe/nested.json", "sha256": hashlib.sha256(b"safe").hexdigest(), "bytes": 4}
    with pytest.raises(ValueError, match="EXCLUSIVE_UNALIASED"):
        collect._write(tmp_path, "safe/nested.json", b"replace", TOKEN)
    with pytest.raises(ValueError, match="CREDENTIAL_PERSISTENCE"):
        collect._write(tmp_path, "credential.json", TOKEN.encode(), TOKEN)
    assert not (tmp_path / "credential.json").exists()
    assert (tmp_path / "safe/nested.json").read_bytes() == b"safe"


@pytest.mark.parametrize("relative", ["../escape.json", "nested/../../escape.json", "./local.json", "a//b.json", "a\\b.json", "/absolute.json"])
def test_write_rejects_parent_traversal(tmp_path, relative):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        collect._write(root, relative, b"safe", TOKEN)
    assert not (tmp_path / "escape.json").exists()


def test_write_and_binding_reject_symlink_targets(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "alias").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="UNALIASED"):
        collect._write(root, "alias/data.json", b"safe", TOKEN)
    assert not list(target.iterdir())
    (target / "existing").write_bytes(b"safe")
    (root / "file_alias").symlink_to(target / "existing")
    with pytest.raises(ValueError, match="UNALIASED"):
        collect._binding(root, root / "file_alias")


def test_code_guard_reads_real_contract_and_rejects_hash_drift(monkeypatch):
    collect._guard({})
    monkeypatch.setattr(collect.inputs, "_file_sha", lambda path: "a" * 64)
    with pytest.raises(ValueError, match="COLLECTION_CODE_CHANGED"):
        collect._guard({"work/profit_1000_upgrade/stocks_scope_collect.py": "b" * 64})


def test_import_time_snapshot_binds_executables_and_runtime_snapshot_adds_contract():
    executable = {path for path in collect.NEW_CODE if path.endswith(".py")}
    assert set(collect._IMPORTED_CODE) == executable
    assert collect._IMPORTED_CODE == {path: collect.inputs._file_sha(collect.CHECKOUT / path) for path in executable}
    assert set(collect._code_snapshot({"code_bindings": []})) == set(collect.NEW_CODE)


def test_code_guard_rejects_contract_drift(monkeypatch):
    expected = collect.expected_contract()
    monkeypatch.setattr(collect, "expected_contract", lambda: {**expected, "retries": 1})
    with pytest.raises(ValueError, match="REGISTERED_COLLECTION_CHANGED"):
        collect._guard({})


def test_run_final_guard_change_is_fatal_and_no_receipt(full_run, monkeypatch):
    f = full_run
    guards = []
    def guard(code):
        guards.append(code)
        if len(guards) == 2: raise ValueError("COLLECTION_CODE_CHANGED")
    monkeypatch.setattr(collect, "_guard", guard)
    with pytest.raises(ValueError, match="COLLECTION_CODE_CHANGED"):
        collect.run_collection(f["base"], f["output"], token="")
    assert not (f["output"] / collect.RECEIPT_FILE).exists()


def test_bad_base_fails_before_creating_output_or_requests(tmp_path, monkeypatch):
    def reject(path): raise ValueError("PINNED_BASE_ARCHIVE_CHANGED")
    monkeypatch.setattr(collect.inputs, "read_base_archive", reject)
    with pytest.raises(ValueError, match="PINNED_BASE_ARCHIVE_CHANGED"):
        collect.run_collection(tmp_path / "base", tmp_path / "out", token=TOKEN)
    assert not (tmp_path / "out").exists()


def test_public_injected_call_cannot_mint_real_source_or_copy_base(tmp_path, monkeypatch):
    monkeypatch.setattr(collect.inputs, "read_base_archive", forbidden)
    with pytest.raises(ValueError, match="SYNTHETIC_TRANSPORT_CANNOT_CREATE_SOURCE"):
        collect.run_collection(tmp_path / "base", tmp_path / "out", token=TOKEN, call=forbidden)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("injected", [True, None, 0, "false"])
def test_internal_injection_flag_must_be_exact_false(tmp_path, injected):
    limited = budget()
    with pytest.raises(ValueError, match="SYNTHETIC_TRANSPORT_CANNOT_CREATE_SOURCE"):
        one(tmp_path, injected=injected, call=forbidden, request_budget=limited)
    assert limited.calls == 0 and not list(tmp_path.iterdir())
