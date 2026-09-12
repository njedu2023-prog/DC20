from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from work.profit_1000_upgrade import collect
from work.profit_1000_upgrade.test_labels import case, CODE, T, T1, daily, minutes, auction
from top10decision.decision.shadow_exit_minute_truth import expected_bar_ends, minute_paths, load_exit_minutes

BUDGET = {"max_api_calls": 20, "max_seconds": 30, "requests_per_second": 2.0, "workers": 4, "timeout_seconds": 20}


def mirror(case):
    root, manifest = case
    (root / collect.MARKER).write_text(json.dumps({"schema_version": "dc20_profit_1000_research_mirror_v1", "production_writes": False,
                                                "plan_sha256": hashlib.sha256((collect.HERE / "PLAN.json").read_bytes()).hexdigest()}))
    return root, manifest


def payload(rows, fields):
    return {"code": 0, "data": {"fields": list(fields), "items": [[row.get(name) for name in fields] for row in rows]}}


def minute_rows(day=T1, *, price=9.8):
    return [{"ts_code": CODE, "trade_time": stamp, "open": price, "high": price,
             "low": price, "close": price, "vol": 100, "amount": 1000} for stamp in expected_bar_ends(day)]


def remove_minutes(root):
    for path in minute_paths(root, T1, CODE):
        path.unlink()


def run(root, manifest, call, **kwargs):
    return collect.collect_history(root, manifest, as_of_date=T1, token="secret-value-not-output",
                                   budget=BUDGET, call=call, **kwargs)


def test_research_marker_required(case):
    root, manifest = case
    with pytest.raises(ValueError, match="MARKER"):
        run(root, manifest, lambda *_: pytest.fail("network"))


def test_checkout_itself_never_writable():
    with pytest.raises(ValueError, match="OUTSIDE_CODE_CHECKOUT"):
        collect._research_root(collect.CHECKOUT)


def test_marker_must_bind_current_plan(case):
    root, manifest = mirror(case)
    marker = root / collect.MARKER
    content = json.loads(marker.read_text())
    content["plan_sha256"] = "0" * 64
    marker.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="MARKER_INVALID"):
        run(root, manifest, lambda *_: pytest.fail("network"))


def test_ancestor_symlink_rejected(case, tmp_path):
    root, _ = mirror(case)
    link = tmp_path / "alias"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="SYMLINK"):
        collect._research_root(link / "data")


def test_budget_is_global_and_rate_limited():
    clock = [0.0]
    delays = []
    def sleep(value):
        delays.append(value)
        clock[0] += value
    budget = dict(BUDGET, max_api_calls=3)
    limiter = collect.RequestBudget(budget, clock=lambda: clock[0], sleep=sleep)
    assert limiter.take() and limiter.take() and limiter.take()
    assert not limiter.take()
    assert limiter.calls == 3
    assert delays == [0.5, 0.5]


@pytest.mark.parametrize("change", [{"workers": 5}, {"max_api_calls": 9001}, {"requests_per_second": 2.1},
                                   {"max_seconds": 5401}, {"timeout_seconds": 21}])
def test_budget_cannot_exceed_scope(change):
    with pytest.raises(ValueError):
        collect.RequestBudget(dict(BUDGET, **change))


def test_auction_attempted_first_and_empty_falls_back(case):
    root, manifest = mirror(case)
    calls = []
    def call(endpoint, params, fields, *_):
        calls.append(endpoint)
        assert endpoint == "stk_auction_o"
        return payload([], fields)
    result = run(root, manifest, call)
    assert calls == ["stk_auction_o"]
    assert result["status"] == "RESEARCH_LABEL_COHORTS_COMPLETE"
    assert result["auction_unavailable_dates"] == 1
    assert result["request_receipts"][0]["status"] == "AUCTION_ABSENT_FALLBACK_DECLARED"
    assert not result["training_performed"] and not result["release_allowed"]
    assert result["auction_attempts_complete"]
    journal = (root / "collection_requests.jsonl").read_text()
    assert "AUCTION_ABSENT_FALLBACK_DECLARED" in journal
    assert "secret-value-not-output" not in journal


def test_absent_credentials_cannot_claim_auction_attempt_complete(case):
    root, manifest = mirror(case)
    result = collect.collect_history(root, manifest, as_of_date=T1, token="", budget=BUDGET,
                                     call=lambda *_: pytest.fail("network"))
    assert result["status"] == "BLOCKED_AUCTION_ATTEMPTS_INCOMPLETE"
    assert not result["auction_attempts_complete"]


def test_journal_cannot_be_overwritten(case):
    root, manifest = mirror(case)
    (root / "collection_requests.jsonl").write_text("prior receipt\n")
    with pytest.raises(ValueError, match="OVERWRITTEN"):
        run(root, manifest, lambda *_: pytest.fail("network"))
    assert (root / "collection_requests.jsonl").read_text() == "prior receipt\n"


def test_exact_missing_minutes_collected_once_after_auction(case):
    root, manifest = mirror(case)
    remove_minutes(root)
    calls = []
    def call(endpoint, params, fields, *_):
        calls.append(endpoint)
        return payload([] if endpoint == "stk_auction_o" else minute_rows(), fields)
    result = run(root, manifest, call)
    assert calls == ["stk_auction_o", "stk_mins"]
    assert result["status"] == "RESEARCH_LABEL_COHORTS_COMPLETE"
    assert len(result["new_source_files"]) == 2
    assert load_exit_minutes(root, T1, CODE)["complete_session"]
    assert result["api_calls"] == 2


def test_existing_valid_minute_sources_not_overwritten(case):
    root, manifest = mirror(case)
    before = {path: path.read_bytes() for path in minute_paths(root, T1, CODE)}
    run(root, manifest, lambda endpoint, params, fields, *_: payload([], fields))
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_incomplete_minutes_preserve_unknown(case):
    root, manifest = mirror(case)
    remove_minutes(root)
    def call(endpoint, params, fields, *_):
        return payload([] if endpoint == "stk_auction_o" else minute_rows()[:-1], fields)
    result = run(root, manifest, call)
    assert result["status"] == "PENDING_RESEARCH_TRUTH"
    assert result["label_status_counts"] == {"PENDING_EXIT_MISSING_MINUTES": 1}
    assert not result["new_source_files"]


def test_exception_and_server_error_never_persist_token(case):
    root, manifest = mirror(case)
    remove_minutes(root)
    def call(endpoint, params, fields, token, *_):
        if endpoint == "stk_auction_o":
            return {"code": -2001, "msg": "permission denied token=" + token}
        raise RuntimeError("network error token=" + token)
    result = run(root, manifest, call)
    serialized = json.dumps(result)
    assert "secret-value-not-output" not in serialized
    assert "PENDING_ENTITLEMENT_DENIED" in serialized
    assert "PENDING_NETWORK_OR_RESPONSE_ERROR" in serialized
    assert result["status"] == "PENDING_RESEARCH_TRUTH"


def test_existing_orphan_minute_pair_cannot_be_overwritten(case):
    root, manifest = mirror(case)
    path, meta = minute_paths(root, T1, CODE)
    meta.unlink()
    before = path.read_bytes()
    def call(endpoint, params, fields, *_):
        assert endpoint == "stk_auction_o"
        return payload([], fields)
    result = run(root, manifest, call)
    assert result["status"] == "PENDING_RESEARCH_TRUTH"
    assert path.read_bytes() == before and not meta.exists()


def test_valid_auction_source_writes_verified_metadata_and_preferred(case):
    root, manifest = mirror(case)
    def call(endpoint, params, fields, *_):
        assert endpoint == "stk_auction_o"
        row = {"ts_code": CODE, "trade_date": T, "close": 10, "open": 10, "high": 10,
               "low": 10, "vol": 100000, "amount": 20000000, "vwap": 10}
        return payload([row], fields)
    result = run(root, manifest, call)
    assert len(result["new_source_files"]) == 2
    labels = collect.build_labels(root, manifest, as_of_date=T1)
    assert labels["rows"][0]["entry_price_source"] == "TUSHARE_STK_AUCTION_O"


def test_out_of_scope_missing_request_rejected(case):
    root, manifest = mirror(case)
    def fake_labels(*_, **__):
        return {"rows": [{"missing_evidence_date": "20990101", "missing_evidence_code": CODE,
                          "missing_evidence_kind": "exit_1000_1m"}], "cohorts_by_date": {}}
    with pytest.raises(ValueError, match="OUTSIDE_RESEARCH_SCOPE"):
        run(root, manifest, lambda endpoint, params, fields, *_: payload([], fields), build_labels_fn=fake_labels)


def test_minute_response_identity_cannot_be_rewritten(case):
    root, manifest = mirror(case)
    remove_minutes(root)
    bad_rows = minute_rows()
    bad_rows[0]["ts_code"] = "600999.SH"
    result = run(root, manifest, lambda endpoint, params, fields, *_: payload([] if endpoint == "stk_auction_o" else bad_rows, fields))
    assert result["status"] == "PENDING_RESEARCH_TRUTH"
    assert not result["new_source_files"]


def test_daily_metadata_supports_explicit_limit_preclose(case):
    root, _ = mirror(case)
    rows = [{"trade_date": T1, "ts_code": CODE, "pre_close": 9.7, "up_limit": 10.67, "down_limit": 8.73}]
    raw, meta = collect._market_bytes(rows, T1, "stk_limit", {CODE}, "2026-09-14T08:00:00Z")
    assert b"pre_close" in raw and b"9.7" in raw
    assert json.loads(meta)["immutable"] is True
