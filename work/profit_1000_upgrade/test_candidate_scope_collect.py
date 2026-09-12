"""Offline transport/budget/append-only tests; no real-source evidence generated."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from work.profit_1000_upgrade import candidate_scope_collect as c


class Clock:
    def __init__(self):
        self.now = 0.0
    def clock(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def budget():
    clock = Clock()
    return clock, c.RequestBudget(clock=clock.clock, sleep=clock.sleep)


def raw_response(pair, *, empty=False):
    day, code = pair
    return json.dumps({"code": 0, "detail": "...", "data": {"fields": list(c.source.FIELDS),
        "items": [] if empty else [[code, day, 10.0, 10000, 100000, 9.8]], "count": 0, "has_more": False}}).encode()


def test_registered_contract_exact():
    assert c.inputs._json((c.HERE / c.CONTRACT_FILE).read_bytes()) == c.expected_contract()
    assert c.MAX_CALLS == 1860 and c.MAX_WORKERS == 4 and c.START_INTERVAL == 0.5
    assert c.PREFLIGHT_PAIRS == (("20250319", "000612.SZ"), ("20260203", "000995.SZ"))
    assert c.FLAGS["source_only"] and not c.FLAGS["source_import_into_labels_performed"]


def test_budget_concurrent_exact_cap_no_retries():
    clock, b = budget()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: b.reserve(), range(1868)))
    assert sorted(x for x in results if x is not None) == list(range(1, 1861))
    assert results.count(None) == 8 and b.calls == 1860
    assert clock.now == (1860 - 1) * 0.5


def test_budget_reserves_socket_headroom():
    clock, b = budget()
    clock.now = c.MAX_SECONDS - c.TIMEOUT
    assert b.reserve() is None and b.calls == 0


def test_injected_transport_rejected_before_creating_root(tmp_path):
    out = tmp_path / "never-created"
    with pytest.raises(ValueError, match="NO_INJECTION"):
        c.run_collection(tmp_path / "absent.zip", out, token="fixture", call=lambda *a: b"fake")
    assert not out.exists()


def test_attempt_rerun_rejected_before_source_access(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(ValueError, match="RERUN_NOT_ALLOWED"):
        c.run_collection(tmp_path / "absent.zip", tmp_path / "out", token="fixture")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("path", ["../escape", "x/../escape", "/escape", "x//y", "./x", "x\\y"])
def test_append_only_writer_rejects_unsafe_path(tmp_path, path):
    with pytest.raises(ValueError, match="UNSAFE_RELATIVE"):
        c.write(tmp_path, path, b"safe", "")


def test_append_only_writer_rejects_token_overwrite_and_symlink(tmp_path):
    with pytest.raises(ValueError, match="CREDENTIAL"):
        c.write(tmp_path, "x", b"sentinel-token", "sentinel-token")
    c.write(tmp_path, "x", b"safe", "")
    with pytest.raises(ValueError, match="EXCLUSIVE"):
        c.write(tmp_path, "x", b"replacement", "")
    (tmp_path / "link").symlink_to(tmp_path / "x")
    with pytest.raises(ValueError, match="EXCLUSIVE"):
        c.write(tmp_path, "link", b"replacement", "")
    assert (tmp_path / "x").read_bytes() == b"safe"


def test_missing_credential_does_not_call_network(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "official_call", lambda *a: pytest.fail("network not allowed"))
    _, b = budget()
    result = c.collect_one(tmp_path, c.PREFLIGHT_PAIRS[0], "", b)
    assert result["status"] == "PENDING_CREDENTIAL_ABSENT" and result["api_calls"] == 0
    assert b.calls == 0 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("empty", [False, True])
def test_fixture_response_readback_preserves_candidate_identity(tmp_path, monkeypatch, empty):
    pair = c.PREFLIGHT_PAIRS[0]
    payload = raw_response(pair, empty=empty)
    monkeypatch.setattr(c, "official_call", lambda contract, token: payload)
    _, b = budget()
    item = c.collect_one(tmp_path, pair, "fixture-secret-not-in-response", b)
    assert item["status"] == ("CANDIDATE_TABLE_EMPTY" if empty else "CANDIDATE_TABLE_PRESENT")
    assert item["rows"] == (0 if empty else 1) and len(item["source_files"]) == 2
    assert item["http_response_sha256"] == c.sha(payload)
    assert c.preflight_ok(item) is (not empty)
    assert "entry_price" not in item and "slot_net_return" not in item
    # These are temporary fixture files only, not a verified collection receipt.
    assert not (tmp_path / c.RECEIPT_FILE).exists()


@pytest.mark.parametrize("response", [b"not-json", b'{"code":-1,"msg":"private server detail"}',
    b'{"code":0,"detail":"not allowed","data":{}}'])
def test_invalid_fixture_is_pending_without_source_or_fallback(tmp_path, monkeypatch, response):
    monkeypatch.setattr(c, "official_call", lambda *a: response)
    _, b = budget()
    result = c.collect_one(tmp_path, c.PREFLIGHT_PAIRS[0], "fixture-secret", b)
    assert result["status"] == "PENDING_INVALID_CANDIDATE_SOURCE" and result["api_calls"] == 1
    assert result["source_files"] == [] and list(tmp_path.iterdir()) == []
    assert "private server detail" not in json.dumps(result)


def test_http_error_only_records_safe_status(tmp_path, monkeypatch):
    def fail(*a):
        raise HTTPError("https://example.invalid", 429, "private-server-text", {}, None)
    monkeypatch.setattr(c, "official_call", fail)
    _, b = budget()
    result = c.collect_one(tmp_path, c.PREFLIGHT_PAIRS[0], "fixture-secret", b)
    assert result["status"] == "PENDING_HTTP_ERROR" and result["reason"] == "HTTP_429"
    assert "private-server-text" not in json.dumps(result) and not result["source_files"]


def test_filesystem_failure_does_not_become_network_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "official_call", lambda *a: raw_response(c.PREFLIGHT_PAIRS[0]))
    def fail(*a):
        raise OSError("disk failure fixture")
    monkeypatch.setattr(c, "write", fail)
    _, b = budget()
    with pytest.raises(OSError, match="disk failure"):
        c.collect_one(tmp_path, c.PREFLIGHT_PAIRS[0], "fixture-secret", b)


def test_failed_preflight_stops_all_remaining_calls(tmp_path, monkeypatch):
    expected = sorted([*c.PREFLIGHT_PAIRS, ("20250320", "000001.SZ")])
    monkeypatch.setattr(c, "pairs", lambda scope: expected)
    seen = []
    def fail(root, pair, token, b):
        seen.append(pair)
        return c.blank(pair, "PENDING_INVALID_CANDIDATE_SOURCE")
    monkeypatch.setattr(c, "collect_one", fail)
    _, b = budget()
    result, passed = c.run_requests(tmp_path, {}, "fixture", b)
    assert not passed and seen == [c.PREFLIGHT_PAIRS[0]]
    assert len(result) == 3 and sum(r["status"] == "NOT_REQUESTED_PREFLIGHT_BLOCKED" for r in result) == 2


def test_two_preflights_before_each_remaining_pair_once(tmp_path, monkeypatch):
    expected = sorted([*c.PREFLIGHT_PAIRS, ("20250320", "000001.SZ"), ("20250320", "000002.SZ")])
    monkeypatch.setattr(c, "pairs", lambda scope: expected)
    seen = []
    def success(root, pair, token, b):
        seen.append(pair)
        return {**c.blank(pair), "status": "CANDIDATE_TABLE_PRESENT", "rows": 1}
    monkeypatch.setattr(c, "collect_one", success)
    _, b = budget()
    result, passed = c.run_requests(tmp_path, {}, "fixture", b)
    assert passed and seen[:2] == list(c.PREFLIGHT_PAIRS)
    assert sorted(seen) == expected and len(seen) == len(set(seen))
    assert [(r["trade_date"], r["ts_code"]) for r in result] == expected


def test_workflow_has_outer_deadline_readonly_scope_and_no_retries():
    import yaml
    workflow = yaml.safe_load((c.CHECKOUT / ".github/workflows/research_candidate_auction_gaps.yml").read_text())
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    job = workflow["jobs"]["candidate-sources"]
    assert job["timeout-minutes"] == 70
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "--kill-after=30s 55m" in runs and "COLLECTION.json" not in runs
    assert "candidate_scope_verify.py" in runs and "10295337206/zip" in runs
    assert "--retry" not in runs and "git push" not in runs
