"""Offline fake-HTTP collector tests; never claim real provider collection.

Orchestration fixtures mock research mirror reconstruction explicitly, while
the v3 HTTP codec, source-file writer/loader, clock budget and journal remain
real. The synthetic 910-date cohort is not an exchange-history substitute.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import threading
from urllib import error

import pytest

from work.profit_1000_upgrade import collect_v3 as collect

TOKEN = "synthetic_fake_http_credential_987654321"
DAY = "20250102"
CODE = "600001.SH"


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def _forbidden(*args, **kwargs):
    raise AssertionError("unexpected network, label, overwrite or training")


def _payload(day=DAY, *, values=None, code=0, message="", rows=None):
    row = [CODE, day, 10, 100, 1000, 9.5] if values is None else [CODE, day, *values]
    data = {"fields": list(collect.auction.FIELDS), "items": [row] if rows is None else rows,
            "count": 0, "has_more": False}
    return json.dumps({"code": code, "msg": message, "data": data if code == 0 else None}).encode()


def _limiter(**changes):
    clock = Clock()
    return collect.old.RequestBudget({**collect.DEFAULT_BUDGET, **changes}, clock=clock, sleep=clock.sleep)


def _fetch(root, *, day=DAY, token=TOKEN, limiter=None, call=None):
    return collect._fetch(root, day, [CODE], token=token, limiter=limiter or _limiter(),
                          call=call or (lambda endpoint, params, fields, token, timeout: _payload(params["trade_date"])),
                          plan_sha="1" * 64, contract_sha="2" * 64)


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-research-mirror"
    root.mkdir()
    (root / "old-original-source.bin").write_bytes(b"pinned synthetic historical source, do not change")
    (root / ".dc20-profit-1000-research-root.json").write_text('{"synthetic_fixture_only":true}')
    days = _weekdays(date(2022, 11, 14), 517) + _weekdays(date(2025, 1, 1), 393)
    rows = []
    for index, day in enumerate(days):
        for rank in range(1, 9 if index < 383 else 8):
            rows.append({"signal_date": (day - timedelta(days=1)).strftime("%Y%m%d"),
                         "exec_date": day.strftime("%Y%m%d"), "ts_code": f"{600000 + rank:06}.SH",
                         "promotion_rank": rank})
    manifest = {"plan_version": "v3", **dict(collect.policy_v3.CONTRACT), "rows": rows}
    assert len(rows) == 6753
    monkeypatch.setattr(collect.research_v3, "require_research_mirror", lambda value: Path(value).resolve())
    monkeypatch.setattr(collect.research_v3, "prepare_history", lambda value: copy.deepcopy(manifest))
    monkeypatch.setattr(collect.research_v3, "load_plan", lambda: {"as_of_date": "20260911"})
    monkeypatch.setattr(collect.research_v3, "verify_import", lambda value: None)
    monkeypatch.setattr(collect, "_limiter", lambda budget: _limiter(**budget))
    return root, manifest


def test_registered_contract_exact_scope_endpoints_and_no_training():
    expected = collect.expected_contract()
    assert collect.collection_contract() == expected
    assert expected["budget"] == {"max_api_calls": 393, "max_seconds": 1200, "requests_per_second": 2.0,
                                    "workers": 4, "timeout_seconds": 20}
    assert expected["allowed_endpoints"] == ["stk_auction"] and expected["retries"] == 0
    assert expected["candidate_rows"] == 6753 and expected["T_dates"] == 910
    assert expected["pre_coverage_T_dates"] == 517 and expected["canonical_T_dates"] == 393
    assert expected["training_performed"] is expected["labels_rebuilt"] is expected["settlement_performed"] is False
    assert collect._file_sha(collect.HERE / "collect.py") == collect.OLD_COLLECT_SHA
    assert collect._file_sha(collect.HERE / "auction_truth.py") == collect.OLD_AUCTION_SHA


@pytest.mark.parametrize("field", list(collect.DEFAULT_BUDGET))
def test_budget_can_only_decrease_from_registered_stage(field):
    budget = dict(collect.DEFAULT_BUDGET)
    budget[field] += 1
    with pytest.raises(ValueError, match="V3_BUDGET_EXCEEDED"):
        collect._budget(budget)


@pytest.mark.parametrize("change", [{"max_api_calls": 0}, {"workers": True}, {"workers": 1.5},
                                     {"requests_per_second": float("nan")}, {"max_seconds": float("inf")}])
def test_invalid_budget_types_and_bounds_fail(change):
    with pytest.raises(ValueError):
        collect._budget({**collect.DEFAULT_BUDGET, **change})


def test_budget_extra_or_missing_field_rejected():
    for value in ({}, {**collect.DEFAULT_BUDGET, "retries": 1}):
        with pytest.raises(ValueError, match="V3_BUDGET_FIELDS_CHANGED"):
            collect._budget(value)


def test_fetch_original_http_exact_canonical_endpoint_and_bound_source_pair(tmp_path):
    calls = []
    raw = _payload()
    def call(endpoint, params, fields, token, timeout):
        calls.append((endpoint, params, fields, token, timeout))
        return raw
    result = _fetch(tmp_path, call=call)
    assert calls == [("stk_auction", {"trade_date": DAY}, collect.auction.FIELDS, TOKEN, 20)]
    assert result["network_request_performed"] is True and result["status"] == "EXACT_TRUTH_WRITTEN"
    assert result["source_rows"] == result["candidate_rows_present"] == 1
    assert result["http_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["http_response_bytes"] == len(raw)
    loaded = collect.auction.load(tmp_path, DAY)
    assert loaded.requested_code is None and loaded.rows[CODE]["price"] == 10
    assert list(map(dict, loaded.source_files)) == result["new_source_files"]
    data, meta = collect.auction.source_paths(tmp_path, DAY)
    metadata = json.loads(meta.read_bytes())
    assert metadata["ingestion"] == "ORIGINAL_HTTP_RESPONSE_ONLY"
    assert metadata["diagnostic_table_imported"] is False
    assert TOKEN.encode() not in data.read_bytes() + meta.read_bytes() + json.dumps(result).encode()
    assert "net_return" not in result and "proxy_fill" not in result


@pytest.mark.parametrize("values", [[10, 100, 999, 9.5], [None, 100, 1000, 9.5],
                                     [10, None, 1000, 9.5], [None, 0, 0, 9.5], ["10.001", 100, "1000.1", 9.5]])
def test_candidate_numeric_qualification_does_not_reject_complete_table(tmp_path, values):
    result = _fetch(tmp_path, call=lambda *args: _payload(values=values))
    assert result["status"] == "EXACT_TRUTH_WRITTEN"
    row = dict(collect.auction.load(tmp_path, DAY).rows[CODE])
    assert [row[key] for key in ("price", "vol", "amount", "pre_close")] == values
    assert result.get("return") is None and result.get("capacity_proxy_verified") is None


@pytest.mark.parametrize("kind", ["empty", "permission"])
def test_valid_unavailable_receipt_is_retained_not_fabricated_fill(tmp_path, kind):
    raw = _payload(rows=[]) if kind == "empty" else _payload(code=2002, message="权限不足")
    result = _fetch(tmp_path, call=lambda *args: raw)
    assert result["status"] == "EXACT_TRUTH_WRITTEN"
    assert result["source_status"] == ("CANONICAL_TABLE_EMPTY" if kind == "empty" else "ENTITLEMENT_DENIED")
    assert result["candidate_rows_present"] == 0
    assert dict(collect.auction.load(tmp_path, DAY).rows) == {}
    assert "net_return" not in result and "fallback_price" not in result


def test_precoverage_is_bound_declaration_without_http_or_source_files(tmp_path):
    result = _fetch(tmp_path, day="20241231", call=_forbidden)
    assert result["status"] == "HISTORY_BEFORE_CANONICAL_COVERAGE"
    assert result["network_request_performed"] is False and result["new_source_files"] == []
    assert result["plan_sha256"] == "1" * 64 and result["collection_contract_sha256"] == "2" * 64
    assert _snapshot(tmp_path) == {}


@pytest.mark.parametrize("kind", ["missing_token", "exhausted_calls", "exhausted_seconds"])
def test_credential_or_budget_missing_never_calls_network(tmp_path, kind):
    limiter = _limiter(max_api_calls=1, max_seconds=1)
    token = "" if kind == "missing_token" else TOKEN
    if kind == "exhausted_calls":
        assert limiter.take()
    elif kind == "exhausted_seconds":
        limiter.clock.now = 2
    result = _fetch(tmp_path, token=token, limiter=limiter, call=_forbidden)
    assert result["status"] == ("PENDING_CREDENTIAL_ABSENT" if kind == "missing_token" else "PENDING_BUDGET_EXHAUSTED")
    assert result["network_request_performed"] is False and result["new_source_files"] == []


@pytest.mark.parametrize("raw,reason", [
    (b"not json", "INVALID_SOURCE_JSON"),
    (b'{"code":0,"code":1}', "DUPLICATE_JSON_KEY"),
    (b'{"code":0,"data":NaN}', "NONFINITE_JSON_NUMBER"),
    (_payload(code=500, message="private unknown server text"), "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE"),
    (_payload(code=500, message="rate limit private server text"), "OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE"),
])
def test_codec_errors_are_exact_safe_enums_not_server_text(tmp_path, raw, reason):
    result = _fetch(tmp_path, call=lambda *args: raw)
    assert result["status"] == "PENDING_INVALID_SOURCE_NOT_IMPUTED" and result["reason"] == reason
    assert result["network_request_performed"] is True and result["new_source_files"] == []
    assert _snapshot(tmp_path) == {}
    assert b"private" not in json.dumps(result).encode()


@pytest.mark.parametrize("raw", [None, {}, "raw string", b"", b"x" * 4_000_001])
def test_only_original_bounded_http_bytes_allowed(tmp_path, raw):
    result = _fetch(tmp_path, call=lambda *args: raw)
    assert result["status"] == "PENDING_INVALID_HTTP_BYTES"
    assert result["new_source_files"] == [] and _snapshot(tmp_path) == {}


@pytest.mark.parametrize("kind", ["exception", "http", "malicious_codec"])
def test_exception_messages_credentials_never_persist(tmp_path, monkeypatch, kind):
    def call(*args):
        if kind == "http":
            raise error.HTTPError("https://invalid.test/" + TOKEN, 429, TOKEN, {}, None)
        raise RuntimeError(TOKEN)
    if kind == "malicious_codec":
        def bad_codec(*args, **kwargs):
            raise ValueError(TOKEN)
        monkeypatch.setattr(collect.auction, "source_bytes", bad_codec)
        call = lambda *args: _payload()
    result = _fetch(tmp_path, call=call)
    assert TOKEN not in json.dumps(result) and result["new_source_files"] == []
    assert result["reason"] == {"http": "HTTP_ERROR", "exception": "NETWORK_OR_RESPONSE_ERROR", "malicious_codec": "UNCLASSIFIED_CODEC_REJECTION"}[kind]
    assert _snapshot(tmp_path) == {}


@pytest.mark.parametrize("where", ["message", "row"])
def test_token_in_response_cannot_enter_source_pair(tmp_path, where):
    raw = _payload(message=TOKEN) if where == "message" else _payload(values=[TOKEN, 100, 1000, 9.5])
    result = _fetch(tmp_path, call=lambda *args: raw)
    assert result["status"] == "PENDING_INVALID_SOURCE_NOT_IMPUTED"
    assert TOKEN not in json.dumps(result) and _snapshot(tmp_path) == {}


@pytest.mark.parametrize("day", ["20241231", DAY])
def test_existing_pair_or_orphan_is_never_overwritten_or_reused(tmp_path, day):
    path, _ = collect.auction.source_paths(tmp_path, day)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original orphan retained")
    before = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="EXISTING_TRUTH_CANNOT_BE_OVERWRITTEN"):
        _fetch(tmp_path, day=day, call=_forbidden)
    assert _snapshot(tmp_path) == before


def test_all_910_dates_stay_in_journal_when_credential_absent(mirror):
    root, manifest = mirror
    original = _snapshot(root)
    progress = []
    result = collect.collect_history(root, manifest, "", call=_forbidden, progress=progress.append)
    assert result["status"] == "BLOCKED" and result["api_calls"] == 0
    assert len(result["request_receipts"]) == 910
    assert result["request_status_counts"] == {"HISTORY_BEFORE_CANONICAL_COVERAGE": 517, "PENDING_CREDENTIAL_ABSENT": 393}
    assert result["precoverage_dates"] == 517 and result["covered_dates"] == 393
    assert result["source_files"] == result["new_source_files"] == []
    journal = root / "collection_requests.jsonl"
    lines = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(lines) == 910 and sorted(lines, key=lambda r: r["trade_date"]) == result["request_receipts"]
    assert result["journal_binding"]["sha256"] == hashlib.sha256(journal.read_bytes()).hexdigest()
    assert all((root / path).read_bytes() == raw for path, raw in original.items())
    assert set(_snapshot(root)) == set(original) | {"collection_requests.jsonl"}
    assert all(result[key] == value for key, value in collect.FLAGS.items())
    assert progress and progress[-1]["event"] == "CANONICAL_COLLECTION_FINISHED"
    assert not any("token" in json.dumps(event).lower() or "600001" in json.dumps(event) for event in progress)


def test_lower_call_budget_has_no_retry_and_keeps_every_unrequested_date(mirror):
    root, manifest = mirror
    calls, lock = [], threading.Lock()
    def fake(endpoint, params, fields, token, timeout):
        with lock:
            calls.append((endpoint, dict(params)))
        return _payload(params["trade_date"])
    result = collect.collect_history(root, manifest, TOKEN, budget={**collect.DEFAULT_BUDGET, "max_api_calls": 2}, call=fake)
    assert len(calls) == result["api_calls"] == 2
    assert len({params["trade_date"] for _, params in calls}) == 2
    assert all(endpoint == "stk_auction" and set(params) == {"trade_date"} for endpoint, params in calls)
    assert result["request_status_counts"] == {"HISTORY_BEFORE_CANONICAL_COVERAGE": 517, "EXACT_TRUTH_WRITTEN": 2, "PENDING_BUDGET_EXHAUSTED": 391}
    assert result["status"] == "BLOCKED" and result["auction_attempts_complete"] is False
    assert len(result["source_files"]) == 4
    assert len(result["request_receipts"]) == 910 and result["retries"] == 0
    assert TOKEN.encode() not in b"".join(_snapshot(root).values()) + json.dumps(result).encode()


def test_full_phase_calls_each_covered_date_once_no_minutes_daily_or_labels(mirror, monkeypatch):
    from work.profit_1000_upgrade import labels, labels_v3, candidate
    root, manifest = mirror
    calls = []
    monkeypatch.setattr(labels, "build_labels", _forbidden)
    monkeypatch.setattr(labels_v3, "build_labels", _forbidden)
    monkeypatch.setattr(candidate, "run_candidate", _forbidden)
    def fake(endpoint, params, fields, token, timeout):
        calls.append((endpoint, params["trade_date"]))
        return _payload(params["trade_date"], rows=[])
    result = collect.collect_history(root, manifest, TOKEN, call=fake)
    assert result["status"] == "COMPLETE" and result["api_calls"] == len(calls) == 393
    assert len(set(calls)) == 393 and {endpoint for endpoint, day in calls} == {"stk_auction"}
    assert result["minute_api_calls"] == result["daily_api_calls"] == 0
    assert len(result["source_files"]) == 786
    assert result["auction_evidence_complete"] and result["auction_attempts_complete"]
    assert result["training_performed"] is result["labels_rebuilt"] is result["settlement_performed"] is False


@pytest.mark.parametrize("existing", ["collection_requests.jsonl", "collection_receipt.json", "new_source"])
def test_repeated_collection_is_rejected_before_network_and_no_overwrites(mirror, existing):
    root, manifest = mirror
    path = (root / existing) if existing != "new_source" else collect.auction.source_paths(root, DAY)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"do not replace original")
    before = _snapshot(root)
    with pytest.raises(ValueError, match="EXISTING_TRUTH_CANNOT_BE_OVERWRITTEN|INITIAL_V3_COLLECTION_CANNOT_REUSE_EXISTING_SOURCE"):
        collect.collect_history(root, manifest, TOKEN, call=_forbidden)
    assert _snapshot(root) == before


@pytest.mark.parametrize("change", ["count", "duplicate", "future", "split", "contract"])
def test_scope_rejects_changed_frozen_universe(mirror, change):
    _, manifest = mirror
    changed = copy.deepcopy(manifest)
    if change == "count":
        changed["rows"].pop()
    elif change == "duplicate":
        changed["rows"][1] = copy.deepcopy(changed["rows"][0])
    elif change == "future":
        changed["rows"][0]["exec_date"] = "20260915"
    elif change == "split":
        for row in changed["rows"]:
            if row["exec_date"] == "20250101":
                row["exec_date"] = "20241231"
                row["signal_date"] = "20241230"
    else:
        changed["entry_policy_id"] = "old_v2"
    with pytest.raises(ValueError):
        collect._scope(changed, collect.collection_contract())


def test_argument_manifest_must_equal_fresh_root_reconstruction(mirror):
    root, manifest = mirror
    changed = copy.deepcopy(manifest)
    changed["rows"][0]["promotion_rank"] = 9
    with pytest.raises(ValueError, match="FROZEN_MANIFEST_RECONSTRUCTION_CHANGED"):
        collect.collect_history(root, changed, TOKEN, call=_forbidden)
    assert not (root / "collection_requests.jsonl").exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_original_import_receipt_must_verify_before_and_after_collection(mirror, monkeypatch, when):
    root, manifest = mirror
    observed = []
    def verify(value):
        observed.append(value)
        if when == "before" or len(observed) == 2:
            raise ValueError("SYNTHETIC_ORIGINAL_IMPORT_BINDING_CHANGED")
    monkeypatch.setattr(collect.research_v3, "verify_import", verify)
    with pytest.raises(ValueError, match="SYNTHETIC_ORIGINAL_IMPORT_BINDING_CHANGED"):
        collect.collect_history(root, manifest, "", call=_forbidden)
    assert len(observed) == (1 if when == "before" else 2)
    assert (root / "collection_requests.jsonl").exists() is (when == "after")


def test_imported_original_source_change_aborts_receipt(mirror):
    root, manifest = mirror
    def changed(*args):
        (root / "old-original-source.bin").write_bytes(b"tampered")
        return _payload(args[1]["trade_date"])
    with pytest.raises(ValueError, match="IMPORTED_SOURCE_CHANGED_DURING_COLLECTION"):
        collect.collect_history(root, manifest, TOKEN, budget={**collect.DEFAULT_BUDGET, "max_api_calls": 1}, call=changed)
    assert (root / "collection_requests.jsonl").is_file() and not (root / "collection_receipt.json").exists()


def test_midflight_journal_tampering_is_detected_before_more_http(mirror):
    root, manifest = mirror
    def progress(event):
        (root / "collection_requests.jsonl").write_bytes(b"injected journal")
    with pytest.raises(ValueError, match="COLLECTION_JOURNAL_CHANGED"):
        collect.collect_history(root, manifest, "", call=_forbidden, progress=progress)


def test_partial_pair_write_is_hard_failure_not_completed_evidence(mirror, monkeypatch):
    root, manifest = mirror
    def partial(root, paths, bodies):
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        paths[0].write_bytes(bodies[0])
        raise OSError("synthetic failed second write")
    monkeypatch.setattr(collect.old, "_write_pair", partial)
    with pytest.raises(ValueError, match="UNEXPECTED_COLLECTION_FILE_OR_PARTIAL_PAIR"):
        collect.collect_history(root, manifest, TOKEN, budget={**collect.DEFAULT_BUDGET, "max_api_calls": 1},
                                call=lambda endpoint, params, *args: _payload(params["trade_date"]))
    assert not (root / "collection_receipt.json").exists()
    journal = (root / "collection_requests.jsonl").read_text()
    assert "PENDING_INVALID_SOURCE_NOT_IMPUTED" in journal and TOKEN not in journal


@pytest.mark.parametrize("where", ["before", "during"])
def test_collector_implementation_change_is_detected_without_editing_code(mirror, monkeypatch, where):
    root, manifest = mirror
    original = collect._file_sha
    target = collect.HERE / "collect_v3.py"
    changed = [where == "before"]
    def sha(path):
        return "0" * 64 if Path(path) == target and changed[0] else original(path)
    monkeypatch.setattr(collect, "_file_sha", sha)
    def progress(event):
        changed[0] = True
    with pytest.raises(ValueError, match="V3_COLLECTION_CODE_CHANGED_SINCE_IMPORT"):
        collect.collect_history(root, manifest, "", call=_forbidden, progress=progress)
    if where == "before":
        assert not (root / "collection_requests.jsonl").exists()


def test_cli_cannot_write_report_outside_exact_mirror_path(mirror, monkeypatch):
    root, _ = mirror
    monkeypatch.setattr(collect.sys, "argv", ["collect_v3.py", "--root", str(root), "--report", str(root.parent / "other.json")])
    monkeypatch.setattr(collect.research_v3, "prepare_history", _forbidden)
    with pytest.raises(ValueError, match="V3_RECEIPT_MUST_BE_EXACT_MIRROR_PATH"):
        collect.main()


def test_cli_writes_only_exact_immutable_receipt_and_blocked_is_nonzero(mirror, monkeypatch):
    root, manifest = mirror
    target = root / "collection_receipt.json"
    monkeypatch.setenv("TUSHARE_TOKEN", "")
    monkeypatch.setattr(collect.sys, "argv", ["collect_v3.py", "--root", str(root), "--report", str(target)])
    monkeypatch.setattr(collect.old, "official_call_v2", _forbidden)
    assert collect.main() == 2
    receipt = json.loads(target.read_text())
    assert receipt["api_calls"] == 0 and receipt["status"] == "BLOCKED"
    assert receipt["phase"] == "CANONICAL_AUCTION_ONLY"
    with pytest.raises(ValueError, match="EXISTING_TRUTH_CANNOT_BE_OVERWRITTEN"):
        collect.main()
