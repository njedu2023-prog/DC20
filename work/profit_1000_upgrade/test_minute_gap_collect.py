"""Synthetic-only tests; every HTTP call is blocked or explicitly replaced."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib import error, request  # Initialize SSL before transport mocking.

import pytest

from work.profit_1000_upgrade import minute_gap_collect as c

DAY, CODE = "20260106", "001299.SZ"
PAIR = (DAY, CODE)
LABEL_SHA = "a" * 64
TOKEN = "synthetic-token-never-a-real-credential"
REAL_BUDGET = c.RequestBudget


class FakeClock:
    def __init__(self): self.value, self.sleeps = 0.0, []
    def __call__(self): return self.value
    def sleep(self, duration):
        assert duration >= 0
        self.sleeps.append(duration)
        self.value += duration


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None): return cls(2026, 9, 13, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("REAL_NETWORK_FORBIDDEN_IN_TEST")
    monkeypatch.setattr(c, "official_call", forbidden)
    monkeypatch.setattr(c, "datetime", FixedDatetime)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(c, "RequestBudget", lambda total: REAL_BUDGET(total, clock=fake, sleep=fake.sleep))
    return fake


def pairs(n):
    return [(DAY, f"{index:06d}.SZ") for index in range(1, n + 1)]


def response(pair=PAIR, *, edit=None, fields=None, reverse=False):
    day, code = pair
    rows = [{"ts_code": code, "trade_time": stamp, "open": 10, "close": 10,
             "high": 10, "low": 10, "vol": 100, "amount": 1000}
            for stamp in c.source._minute.expected_bar_ends(day)]
    columns = list(fields or c.source.FIELDS)
    if reverse: rows.reverse()
    body = {"code": 0, "data": {"fields": columns,
        "items": [[r[key] for key in columns] for r in rows], "count": 0, "has_more": False}}
    if edit: edit(body)
    return json.dumps(body).encode()


def save_plan(tmp_path, planned=None, **changes):
    plan = c.expected_plan(planned or [PAIR], LABEL_SHA)
    plan.update(changes)
    raw = c.json_bytes(plan)
    path = tmp_path / "registered-plan.json"
    path.write_bytes(raw)
    return path, c.sha(raw), plan


def run(tmp_path, monkeypatch, clock, planned=None, responder=None, token=TOKEN, **kwargs):
    path, digest, plan = save_plan(tmp_path, planned)
    seen = []
    def fake(contract, actual_token):
        assert actual_token == token
        pair = (contract["params"]["start_date"][:10].replace("-", ""), contract["params"]["ts_code"])
        seen.append((pair, clock()))
        return responder(pair) if responder else response(pair)
    monkeypatch.setattr(c, "official_call", fake)
    root = tmp_path / "output"
    report = c.run_collection(root, plan_path=path, expected_plan_sha256=digest,
        expected_label_report_sha256=LABEL_SHA, token=token, **kwargs)
    return root, report, seen, plan


def one(tmp_path, monkeypatch, payload=None, failure=None):
    def fake(*args):
        if failure is not None: raise failure
        return response() if payload is None else payload
    monkeypatch.setattr(c, "official_call", fake)
    clock = FakeClock()
    result = c.collect_one(tmp_path, PAIR, [PAIR], TOKEN, REAL_BUDGET(1, clock=clock, sleep=clock.sleep))
    return result


@pytest.mark.parametrize("n,expected", [(1, [0]), (2, [0, 1]), (3, [0, 1, 2]), (4, [0, 2, 3]), (5000, [0, 2500, 4999])])
def test_plan_exact_scope_and_unique_preflight(n, expected):
    planned = pairs(n)
    plan = c.expected_plan(planned, LABEL_SHA)
    assert plan["pairs"] == [list(p) for p in planned]
    assert plan["preflight_pairs"] == [list(planned[i]) for i in expected]
    assert plan["max_api_calls"] == 5000 and plan["planned_pair_count"] == n
    assert plan["as_of_date"] == "20260911"
    assert plan["max_workers"] == 4 and plan["retries"] == 0
    assert plan["socket_timeout_seconds"] == 20
    assert plan["request_start_budget_seconds"] == 4200
    assert plan["request_start_headroom_seconds"] == 20
    assert plan["minimum_request_start_interval_seconds"] == 0.5
    assert plan["time_semantics"] == "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED"
    assert plan["frozen_dependencies"] == c.PINNED_DEPENDENCIES
    assert all(plan[k] is v for k, v in c.FLAGS.items())


@pytest.mark.parametrize("planned", [[], pairs(5001), [PAIR, PAIR], list(reversed(pairs(2))),
    [PAIR, [DAY]], [[DAY, CODE, "extra"]], [None], "not-a-list", {PAIR}])
def test_plan_rejects_nonexact_scope(planned):
    with pytest.raises(ValueError): c.expected_plan(planned, LABEL_SHA)


@pytest.mark.parametrize("day,code", [("20221031", CODE), ("20260912", CODE), ("20260914", CODE),
    ("20260230", CODE), ("2026-01-06", CODE), ("２０２６０１０６", CODE), (20260106, CODE),
    (True, CODE), (DAY, "001299.BJ"), (DAY, "001299.sz"), (DAY, "１２３４５６.SZ"),
    (DAY, "001299"), (DAY, "../001299.SZ"), (DAY, None)])
def test_identity_bounds_never_touch_holdout(day, code):
    with pytest.raises(ValueError): c.identity(day, code)


@pytest.mark.parametrize("day", ["20221101", "20260911"])
def test_identity_inclusive_registered_boundaries(day):
    assert c.identity(day, CODE) == (day, CODE)


@pytest.mark.parametrize("value", [None, "", "a" * 63, "A" * 64, "z" * 64, 0, True])
def test_required_sha_is_exact_lowercase(value):
    with pytest.raises(ValueError): c.expected_plan([PAIR], value)


def test_read_plan_requires_exact_canonical_bytes_and_both_shas(tmp_path):
    path, digest, plan = save_plan(tmp_path)
    assert c.read_plan(path, digest, LABEL_SHA) == (plan, path.read_bytes())
    with pytest.raises(ValueError, match="PLAN_SHA"): c.read_plan(path, "b" * 64, LABEL_SHA)
    with pytest.raises(ValueError, match="LABEL_REPORT_SHA"): c.read_plan(path, digest, "b" * 64)
    path.write_bytes(json.dumps(plan).encode())
    with pytest.raises(ValueError, match="CONTRACT_CHANGED"):
        c.read_plan(path, c.sha(path.read_bytes()), LABEL_SHA)


@pytest.mark.parametrize("change", [{"max_api_calls": 5001}, {"retries": 1}, {"query_start": "09:30:00"},
    {"max_workers": 5}, {"as_of_date": "20260914"}, {"planned_pair_count": True},
    {"preflight_pairs": []}, {"provider_timestamp_semantics_confirmed": True},
    {"upstream_label_report_verified_by_collector": True}, {"unregistered": "value"}])
def test_read_plan_refuses_contract_mutations(tmp_path, change):
    path, digest, _ = save_plan(tmp_path, **change)
    with pytest.raises(ValueError, match="CONTRACT_CHANGED"): c.read_plan(path, digest, LABEL_SHA)


@pytest.mark.parametrize("raw", [b'{"label_report_sha256":"a","label_report_sha256":"b"}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}', b'[]', b'bad', b'\xff'])
def test_plan_strict_json(tmp_path, raw):
    path = tmp_path / "plan.json"; path.write_bytes(raw)
    with pytest.raises(ValueError): c.read_plan(path, c.sha(raw), LABEL_SHA)


def test_guard_binds_exact_frozen_code_and_self(monkeypatch):
    actual = c.guard()
    assert actual == {**c.PINNED_DEPENDENCIES, "work/profit_1000_upgrade/minute_gap_collect.py": c.file_sha(Path(c.__file__))}
    monkeypatch.setattr(c, "PINNED_DEPENDENCIES", {**c.PINNED_DEPENDENCIES,
        "work/profit_1000_upgrade/minute_truth.py": "0" * 64})
    with pytest.raises(ValueError, match="CODE_CHANGED"): c.guard()


@pytest.mark.parametrize("n", [1, 2, 3, 5000])
def test_budget_global_start_spacing_and_call_cap(n):
    clock = FakeClock(); budget = REAL_BUDGET(n, clock=clock, sleep=clock.sleep)
    actual = [budget.reserve() for _ in range(n)]
    assert actual == [(i + 1, i * .5) for i in range(n)]
    assert budget.reserve() is None and budget.calls == n


def test_budget_global_lock_applies_to_four_workers():
    clock = FakeClock(); budget = REAL_BUDGET(20, clock=clock, sleep=clock.sleep)
    with ThreadPoolExecutor(max_workers=4) as pool: actual = list(pool.map(lambda _: budget.reserve(), range(20)))
    assert sorted(actual) == [(i + 1, i * .5) for i in range(20)]


@pytest.mark.parametrize("value", [0, -1, 5001, True, 2.0, "2"])
def test_budget_invalid_total(value):
    with pytest.raises(ValueError): REAL_BUDGET(value)


def test_budget_admission_deadline_rechecked_after_rate_sleep():
    clock = FakeClock(); budget = REAL_BUDGET(2, clock=clock, sleep=clock.sleep)
    assert budget.reserve() == (1, 0)
    def oversleep(seconds): clock.value = 4180
    budget.sleep = oversleep
    assert budget.reserve() is None and budget.calls == 1


@pytest.mark.parametrize("elapsed", [4180, 4200, 4300])
def test_budget_no_request_without_full_socket_headroom(elapsed):
    clock = FakeClock(); budget = REAL_BUDGET(1, clock=clock, sleep=clock.sleep)
    clock.value = elapsed
    assert budget.reserve() is None and budget.calls == 0


def test_request_exact_0931_only():
    contract = c.request_contract(*PAIR)
    assert contract == {"api_name": "stk_mins", "fields": list(c.source.FIELDS), "params": {
        "ts_code": CODE, "freq": "1min", "start_date": "2026-01-06 09:31:00", "end_date": "2026-01-06 15:00:00"}}


def test_official_transport_https_post_timeout_byte_cap_no_redirect(monkeypatch):
    # Invoke the original function saved below; no actual network is contacted.
    seen = {}
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): seen["read_size"] = size; return b"{}"
    class Opener:
        def open(self, req, *, timeout):
            seen.update(url=req.full_url, method=req.method, body=json.loads(req.data), timeout=timeout)
            return Reply()
    def build(handler): seen["handler"] = handler; return Opener()
    monkeypatch.setattr(request, "build_opener", build)
    assert ORIGINAL_OFFICIAL(c.request_contract(*PAIR), TOKEN) == b"{}"
    assert seen["url"] == "https://api.tushare.pro" and seen["method"] == "POST"
    assert seen["body"] == {**c.request_contract(*PAIR), "fields": ",".join(c.source.FIELDS), "token": TOKEN}
    assert seen["timeout"] == 20 and seen["read_size"] == 1_000_001
    assert type(seen["handler"]) is c.NoRedirect
    assert seen["handler"].redirect_request(None, None, 302, "opaque", {}, "https://elsewhere.invalid") is None


ORIGINAL_OFFICIAL = c.official_call


@pytest.mark.parametrize("change", [{"start_date": "2026-01-06 09:30:00"}, {"end_date": "2026-01-06 14:59:00"},
    {"end_date": "2026-01-07 15:00:00"}, {"freq": "5min"}, {"limit": 240}, {"offset": 0},
    {"ts_type": "STK"}, {"ts_code": "001299.BJ"}])
def test_transport_does_not_accept_request_fallback(monkeypatch, change):
    contract = c.request_contract(*PAIR); contract["params"].update(change)
    monkeypatch.setattr(request, "build_opener", lambda *args: pytest.fail("NO_HTTP_ALLOWED"))
    with pytest.raises(ValueError): ORIGINAL_OFFICIAL(contract, TOKEN)


@pytest.mark.parametrize("change", [{"api_name": "daily"}, {"fields": []}, {"fields": ",".join(c.source.FIELDS)}, {"token": TOKEN}])
def test_transport_rejects_envelope_changes(monkeypatch, change):
    contract = {**c.request_contract(*PAIR), **change}
    monkeypatch.setattr(request, "build_opener", lambda *args: pytest.fail("NO_HTTP_ALLOWED"))
    with pytest.raises(ValueError): ORIGINAL_OFFICIAL(contract, TOKEN)


@pytest.mark.parametrize("raw", [b"", b"x" * 1_000_001, {}, bytearray(b"{}")])
def test_transport_bound_and_bytes(monkeypatch, raw):
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return raw
    class Opener:
        def open(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError): ORIGINAL_OFFICIAL(c.request_contract(*PAIR), TOKEN)


def test_original_data_and_http_identity_preserved(tmp_path, monkeypatch):
    raw = response(fields=tuple(reversed(c.source.FIELDS)), reverse=True,
        edit=lambda body: body["data"]["items"][0].__setitem__(5, "10.000"))
    item = one(tmp_path, monkeypatch, raw)
    assert item["status"] == "RESEARCH_MINUTE_SOURCE_WRITTEN" and item["source_rows"] == 240
    data_path, meta_path = c.source.paths(tmp_path, *PAIR)
    assert json.loads(data_path.read_bytes()) == json.loads(raw)["data"]
    meta = json.loads(meta_path.read_bytes())
    assert meta["response_body_sha256"] == item["http_response_sha256"] == c.sha(raw)
    assert item["http_response_bytes"] == len(raw)
    assert meta["request"] == item["request"]["params"]
    assert "network_request_performed" not in meta
    assert meta["source_values_modified"] is False and meta["provider_timestamp_semantics_confirmed"] is False
    assert c.source.load(tmp_path, *PAIR)["source_files"] == [{k: b[k] for k in ("path", "sha256")} for b in item["source_files"]]
    assert {p.name for p in tmp_path.rglob("*") if p.is_file()} == {data_path.name, meta_path.name}


def mutate_bad(body, case):
    data = body["data"]; items = data["items"]; fields = data["fields"]
    if case == "missing": items.pop(10)
    elif case == "extra": items.append(list(items[-1]))
    elif case == "duplicate": items[5] = list(items[4])
    elif case == "0930": items[0][1] = "2026-01-06 09:30:00"
    elif case == "lunch": items[120][1] = "2026-01-06 13:00:00"
    elif case == "wrong-day": items[0][1] = "2026-01-05 09:31:00"
    elif case == "wrong-code": items[0][0] = "600000.SH"
    elif case == "zero-price": items[0][2] = 0
    elif case == "bool-price": items[0][2] = True
    elif case == "nan": items[0][2] = float("nan")
    elif case == "infinity": items[0][2] = float("inf")
    elif case == "bad-price": items[0][2] = "bad"
    elif case == "zero-vol-nonflat": items[0][6] = 0; items[0][4] = 11
    elif case == "negative-vol": items[0][6] = -1
    elif case == "field-duplicate": fields[0] = fields[1]
    elif case == "row-short": items[0].pop()
    elif case == "more": data["has_more"] = True
    elif case == "more-int": data["has_more"] = 0
    elif case == "count-mismatch": data["count"] = 241
    elif case == "count-bool": data["count"] = True
    elif case == "unknown-data-key": data["raw_msg"] = "not-allowed"
    elif case == "api-error": body["code"] = -2001; body["msg"] = TOKEN
    elif case == "api-bool": body["code"] = False
    elif case == "empty": data["items"] = []
    else: raise AssertionError(case)


@pytest.mark.parametrize("case", ["missing", "extra", "duplicate", "0930", "lunch", "wrong-day", "wrong-code",
    "zero-price", "bool-price", "nan", "infinity", "bad-price", "zero-vol-nonflat", "negative-vol",
    "field-duplicate", "row-short", "more", "more-int", "count-mismatch", "count-bool", "unknown-data-key", "api-error", "api-bool", "empty"])
def test_bad_minutes_never_drop_repair_or_persist_partial(tmp_path, monkeypatch, case):
    raw = response(edit=lambda body: mutate_bad(body, case))
    item = one(tmp_path, monkeypatch, raw)
    assert item["status"] == "PENDING_INVALID_MINUTE_SOURCE"
    assert item["source_files"] == [] and item["source_rows"] is None
    assert item["api_calls"] == 1 and item["http_response_sha256"] == c.sha(raw)
    assert not list(tmp_path.rglob("*.json"))
    assert TOKEN not in json.dumps(item)


@pytest.mark.parametrize("raw", [b'{}', b'{"code":0,"code":0,"data":{}}', b'{"x":NaN}', b'{"x":1e400}', b'bad', b'\xff', b"x" * 1_000_001])
def test_malformed_response_fails_closed_without_sources(tmp_path, monkeypatch, raw):
    item = one(tmp_path, monkeypatch, raw)
    assert item["status"] == "PENDING_INVALID_MINUTE_SOURCE" and item["source_files"] == []
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("where,escaped", [("msg", False), ("msg", True), ("detail", False), ("request_id", True)])
def test_token_echo_even_discarded_envelope_rejects_and_never_persists(tmp_path, monkeypatch, where, escaped):
    raw = response(edit=lambda body: body.update({where: TOKEN}))
    if escaped: raw = raw.replace(TOKEN.encode(), "".join(f"\\u{ord(ch):04x}" for ch in TOKEN).encode())
    item = one(tmp_path, monkeypatch, raw)
    assert item["reason"] == "STRICT_RESPONSE_AND_CREDENTIAL_GUARD_FAILED"
    assert TOKEN not in json.dumps(item) and not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("exc,status", [(TimeoutError(TOKEN), "PENDING_NETWORK_OR_RESPONSE_ERROR"),
    (OSError(TOKEN), "PENDING_NETWORK_OR_RESPONSE_ERROR"), (ValueError(TOKEN), "PENDING_INVALID_MINUTE_SOURCE"),
    (error.HTTPError("https://api.tushare.pro", 429, TOKEN, {}, None), "PENDING_HTTP_ERROR")])
def test_network_exceptions_safe_and_never_retried(tmp_path, monkeypatch, exc, status):
    item = one(tmp_path, monkeypatch, failure=exc)
    assert item["status"] == status and item["api_calls"] == 1
    assert item["source_rows"] is None and not item["source_files"]
    assert item["http_response_sha256"] is None and TOKEN not in json.dumps(item)


def test_filesystem_failure_not_misreported_as_network(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "write", lambda *args: (_ for _ in ()).throw(OSError("write-failed")))
    with pytest.raises(OSError, match="write-failed"): one(tmp_path, monkeypatch)


@pytest.mark.parametrize("n", [1, 2, 3, 8])
def test_full_collection_precise_preflight_then_remaining_and_receipt(tmp_path, monkeypatch, clock, n):
    planned = pairs(n)
    monkeypatch.setenv("GITHUB_RUN_ID", "123456789")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    root, report, seen, plan = run(tmp_path, monkeypatch, clock, planned)
    preflight = [tuple(p) for p in plan["preflight_pairs"]]
    assert [p for p, _ in seen[:len(preflight)]] == preflight
    assert {p for p, _ in seen} == set(planned) and len(seen) == n
    assert report["status"] == "MINUTE_GAPS_COLLECTED" and report["api_calls"] == n
    assert report["qualified_source_pairs"] == n and report["preflight_passed"] is True
    assert [(r["trade_date"], r["ts_code"]) for r in report["requests"]] == planned
    assert report["run_id"] == "123456789" and report["run_commit"] == "b" * 40
    assert all(report[k] is v for k, v in c.FLAGS.items())
    assert report["callable_injected_for_test"] is False
    assert json.loads((root / c.RECEIPT_FILE).read_bytes()) == report
    assert (root / c.PLAN_COPY).read_bytes() == c.json_bytes(plan)
    journal = [json.loads(line) for line in (root / c.JOURNAL_FILE).read_bytes().splitlines()]
    assert [r["request_sequence"] for r in journal] == list(range(1, n + 1))
    assert [r["request_start_elapsed_seconds"] for r in journal] == [i * .5 for i in range(n)]
    assert len(report["source_files"]) == 2 * n and len(report["output_file_bindings"]) == 2 * n + 2
    assert all(c.binding(root, root / b["path"]) == b for b in report["output_file_bindings"])
    assert all(TOKEN.encode() not in path.read_bytes() for path in root.rglob("*") if path.is_file())


@pytest.mark.parametrize("failed_preflight_index", [0, 1, 2])
def test_preflight_failure_stops_and_retains_every_planned_slot(tmp_path, monkeypatch, clock, failed_preflight_index):
    planned = pairs(9); preflight = [planned[0], planned[4], planned[-1]]
    def responder(pair): return b'{"code":1}' if pair == preflight[failed_preflight_index] else response(pair)
    root, report, seen, _ = run(tmp_path, monkeypatch, clock, planned, responder)
    assert [p for p, _ in seen] == preflight[:failed_preflight_index + 1]
    assert report["status"] == "PREFLIGHT_BLOCKED" and report["api_calls"] == failed_preflight_index + 1
    assert report["preflight_passed"] is False and len(report["requests"]) == 9
    assert report["qualified_source_pairs"] == failed_preflight_index
    assert sum(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" for r in report["requests"]) == 8 - failed_preflight_index
    assert all(r["source_rows"] is None for r in report["requests"] if not r["source_files"])


def test_bulk_error_retains_pending_does_not_remove_pair_or_retry(tmp_path, monkeypatch, clock):
    planned = pairs(9); failure = planned[2]
    root, report, seen, _ = run(tmp_path, monkeypatch, clock, planned,
        lambda pair: b'{"code":1}' if pair == failure else response(pair))
    assert report["status"] == "MINUTE_GAPS_PARTIAL" and report["api_calls"] == 9
    assert report["qualified_source_pairs"] == 8 and len(report["requests"]) == 9
    assert sum(p == failure for p, _ in seen) == 1
    row = report["requests"][2]
    assert row["status"] == "PENDING_INVALID_MINUTE_SOURCE" and row["source_rows"] is None
    assert row["source_files"] == [] and all(not p.exists() for p in c.source.paths(root, *failure))


def test_missing_credentials_has_zero_calls_and_all_scope_rows(tmp_path, monkeypatch, clock):
    root, report, seen, _ = run(tmp_path, monkeypatch, clock, pairs(4), token="")
    assert seen == [] and report["api_calls"] == 0 and len(report["requests"]) == 4
    assert report["status"] == "PREFLIGHT_BLOCKED"
    assert report["request_status_counts"] == {"PENDING_CREDENTIAL_ABSENT": 1, "NOT_REQUESTED_PREFLIGHT_BLOCKED": 3}
    assert report["source_files"] == [] and (root / c.JOURNAL_FILE).read_bytes() == b""


def test_budget_exhaustion_keeps_exact_unrequested_tail(tmp_path, monkeypatch, clock):
    planned = pairs(9); calls = 0
    def responder(pair):
        nonlocal calls
        calls += 1
        if calls == 3: clock.value = 4180
        return response(pair)
    root, report, seen, _ = run(tmp_path, monkeypatch, clock, planned, responder)
    assert report["status"] == "MINUTE_GAPS_PARTIAL" and report["api_calls"] == 3
    assert report["request_status_counts"] == {"RESEARCH_MINUTE_SOURCE_WRITTEN": 3, "NOT_REQUESTED_BUDGET_EXHAUSTED": 6}
    assert len(report["requests"]) == 9


def test_bulk_executor_is_exact_four_workers_and_after_preflight(tmp_path, monkeypatch, clock):
    real_executor, invocations = c.ThreadPoolExecutor, []
    def executor(*, max_workers):
        invocations.append(max_workers)
        assert len(list(tmp_path.rglob("*.meta.json"))) == 3
        return real_executor(max_workers=max_workers)
    monkeypatch.setattr(c, "ThreadPoolExecutor", executor)
    run(tmp_path, monkeypatch, clock, pairs(7))
    assert invocations == [4]


@pytest.mark.parametrize("value", ["2", "0", "01", "garbage"])
def test_run_attempt_not_one_refused_before_output(tmp_path, monkeypatch, value):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", value)
    with pytest.raises(ValueError, match="RERUN"):
        c.run_collection(tmp_path / "out", expected_plan_sha256=LABEL_SHA, expected_label_report_sha256=LABEL_SHA, token=TOKEN)
    assert not (tmp_path / "out").exists()


def test_public_injected_transport_cannot_make_real_source(tmp_path):
    with pytest.raises(ValueError, match="NO_INJECTION"):
        c.run_collection(tmp_path / "out", expected_plan_sha256=LABEL_SHA,
            expected_label_report_sha256=LABEL_SHA, token=TOKEN, call=lambda *_: response())
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("run_id,commit", [("opaque-secret-string", "opaque-secret-string"), ("1" * 21, "a" * 41), ("１２３", "A" * 40)])
def test_untrusted_environment_identity_never_persisted(tmp_path, monkeypatch, clock, run_id, commit):
    monkeypatch.setenv("GITHUB_RUN_ID", run_id); monkeypatch.setenv("GITHUB_SHA", commit)
    _, report, _, _ = run(tmp_path, monkeypatch, clock)
    assert report["run_id"] is None and report["run_commit"] is None


def test_plan_changed_during_requests_fails_no_receipt(tmp_path, monkeypatch, clock):
    path, digest, _ = save_plan(tmp_path)
    def fake(*args): path.write_bytes(b"changed"); return response()
    monkeypatch.setattr(c, "official_call", fake)
    root = tmp_path / "out"
    with pytest.raises(ValueError, match="PLAN_SHA"):
        c.run_collection(root, plan_path=path, expected_plan_sha256=digest,
            expected_label_report_sha256=LABEL_SHA, token=TOKEN)
    assert not (root / c.RECEIPT_FILE).exists()


def test_code_changed_during_requests_fails_no_receipt(tmp_path, monkeypatch, clock):
    calls = 0; real_guard = c.guard
    def guard():
        nonlocal calls
        calls += 1
        if calls == 2: raise ValueError("MINUTE_COLLECTION_CODE_CHANGED")
        return real_guard()
    monkeypatch.setattr(c, "guard", guard)
    with pytest.raises(ValueError, match="CODE_CHANGED"): run(tmp_path, monkeypatch, clock)
    assert not (tmp_path / "output" / c.RECEIPT_FILE).exists()


def test_source_changed_after_readback_fails_no_receipt(tmp_path, monkeypatch, clock):
    real = c.run_requests
    def mutate(root, *args):
        result = real(root, *args)
        c.source.paths(root, *PAIR)[0].write_bytes(b"changed")
        return result
    monkeypatch.setattr(c, "run_requests", mutate)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"): run(tmp_path, monkeypatch, clock)
    assert not (tmp_path / "output" / c.RECEIPT_FILE).exists()


@pytest.mark.parametrize("relative", ["../bad", "/absolute", "a/../bad", "./bad", "a//bad", "a\\bad", ""])
def test_write_exclusive_strict_relative_paths(tmp_path, relative):
    with pytest.raises(ValueError): c.write(tmp_path, relative, b"safe", TOKEN)


def test_write_refuses_overwrite_and_token(tmp_path):
    c.write(tmp_path, "safe.json", b"{}", TOKEN)
    with pytest.raises(ValueError, match="EXCLUSIVE"): c.write(tmp_path, "safe.json", b"new", TOKEN)
    with pytest.raises(ValueError, match="CREDENTIAL"): c.write(tmp_path, "secret.json", TOKEN.encode(), TOKEN)
    assert (tmp_path / "safe.json").read_bytes() == b"{}" and not (tmp_path / "secret.json").exists()


def test_existing_output_and_inside_checkout_refused(tmp_path, monkeypatch, clock):
    plan, digest, _ = save_plan(tmp_path)
    for output in (tmp_path, c.CHECKOUT / "never-created-minute-output"):
        with pytest.raises(ValueError): c.run_collection(output, plan_path=plan,
            expected_plan_sha256=digest, expected_label_report_sha256=LABEL_SHA, token=TOKEN)
    assert not (c.CHECKOUT / "never-created-minute-output").exists()


def test_symlink_plan_and_output_and_hardlink_plan_refused(tmp_path, monkeypatch, clock):
    plan, digest, _ = save_plan(tmp_path)
    symlink = tmp_path / "alias-plan.json"; symlink.symlink_to(plan)
    with pytest.raises(ValueError): c.read_plan(symlink, digest, LABEL_SHA)
    linked = tmp_path / "hard-plan.json"; os.link(plan, linked)
    with pytest.raises(ValueError): c.read_plan(linked, digest, LABEL_SHA)
    outalias = tmp_path / "alias"; outalias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError): c.write(tmp_path, "alias/unsafe", b"safe", TOKEN)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "hardlink", "fifo"])
def test_unregistered_or_alias_nodes_refused_by_inventory(tmp_path, kind):
    safe = tmp_path / "safe"; safe.write_bytes(b"safe")
    other = tmp_path / "other"
    if kind == "file": other.write_bytes(b"extra")
    elif kind == "directory": other.mkdir()
    elif kind == "symlink": other.symlink_to(safe)
    elif kind == "hardlink": os.link(safe, other)
    elif kind == "fifo": os.mkfifo(other)
    with pytest.raises(ValueError): c.inventory(tmp_path, {"safe"})


def test_only_registered_minute_outputs_and_no_label_model_paths(tmp_path, monkeypatch, clock):
    root, report, _, _ = run(tmp_path, monkeypatch, clock)
    names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    allowed = {c.PLAN_COPY, c.RECEIPT_FILE, c.JOURNAL_FILE} | {b["path"] for b in report["source_files"]}
    assert names == allowed
    assert all(b["path"].startswith(c.source.SOURCE_ROOT + "/") for b in report["source_files"])
    assert not any("labels" in name or "model" in name or "outputs/decision" in name for name in names)


@pytest.mark.parametrize("status,exit_code", [("MINUTE_GAPS_COLLECTED", 0), ("MINUTE_GAPS_PARTIAL", 2), ("PREFLIGHT_BLOCKED", 2)])
def test_cli_exit_codes_and_fixed_safe_summary(tmp_path, monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(c.sys, "argv", ["minute_gap_collect", "--output", str(tmp_path / "out"),
        "--expected-plan-sha256", LABEL_SHA, "--expected-label-report-sha256", LABEL_SHA])
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    def fake(output, **kwargs):
        assert kwargs["token"] == TOKEN
        return {"status": status, "api_calls": 1, "planned_pair_count": 3, "qualified_source_pairs": 1}
    monkeypatch.setattr(c, "run_collection", fake)
    assert c.main() == exit_code
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout)["status"] == status and stderr == "" and TOKEN not in stdout


def test_cli_failure_does_not_echo_exception_or_credential(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(c.sys, "argv", ["minute_gap_collect", "--output", str(tmp_path / "out"),
        "--expected-plan-sha256", LABEL_SHA, "--expected-label-report-sha256", LABEL_SHA])
    monkeypatch.setattr(c, "run_collection", lambda *a, **k: (_ for _ in ()).throw(ValueError(TOKEN)))
    assert c.main() == 1
    stdout, stderr = capsys.readouterr()
    assert stdout == "" and stderr == "MINUTE_GAP_COLLECTION_FAILED_CLOSED\n"
