"""Synthetic tests only: no real HTTP/token/base-archive or market source writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib import error

import pytest

from work.profit_1000_upgrade import auction_single_stock_probe as probe

TOKEN = "synthetic_credential_for_single_stock_test"
PRIVATE = "PRIVATE_UNTRUSTED_PROVIDER_TEXT_93647120"
DAY, CODE = probe.PAIRS[0]


def forbidden(*args, **kwargs):
    raise AssertionError("network, market source, label, price or training call forbidden")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(probe.request, "build_opener", forbidden)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)


def payload(day=DAY, code=CODE, *, empty=False):
    return {"code": 0, "detail": "...", "msg": PRIVATE, "request_id": PRIVATE,
            "data": {"fields": list(probe.FIELDS), "items": [] if empty else [[code, day, 13.579, 30100, 987654.321, 12.123]],
                     "has_more": False, "count": 0}}


def raw(value=None):
    return json.dumps(payload() if value is None else value, ensure_ascii=False, allow_nan=False).encode()


def diagnose(value=None, day=DAY, code=CODE):
    return probe.diagnose_response(raw(value), day, code, token=TOKEN)


def test_contract_two_verified_gap_candidates_exact_scopes():
    contract = probe.expected_contract()
    assert probe.PAIRS == (("20250319", "000612.SZ"), ("20260203", "000995.SZ"))
    assert contract["requests"] == [{"api_name": "stk_auction", "params": {"trade_date": d, "ts_code": c},
                                     "fields": list(probe.FIELDS)} for d, c in probe.PAIRS]
    assert contract["max_api_calls"] == 2 and contract["calls_per_pair"] == 1 and contract["retries"] == 0
    assert contract["socket_timeout_seconds"] == 20 and contract["max_http_response_bytes"] == 4_000_000
    assert contract["branch"] == "main" and contract["run_attempt"] == 1
    assert not contract["redirects_allowed"] and not contract["pagination_or_request_fallback_allowed"]
    assert contract["verified_stocks_scope_failure"]["individual_pagination_predicate_failure_known"] is False
    assert contract["verified_stocks_scope_failure"]["run_id"] == "34686515444"
    assert probe.CONTRACT_FILE == "AUCTION_SINGLE_STOCK_PROBE.json"
    assert probe.OUTPUT_FILE == "auction_single_stock_probe.json"


@pytest.mark.parametrize("detail", ["absent", "", "..."])
def test_valid_single_row_is_only_structural_observation_with_original_hash(detail, monkeypatch):
    for name in ("source_bytes", "source_paths", "load", "entry_price", "qualify_row"):
        monkeypatch.setattr(probe.codec, name, forbidden)
    value = payload()
    if detail == "absent": del value["detail"]
    else: value["detail"] = detail
    original = raw(value)
    result = probe.diagnose_response(original, DAY, CODE, token=TOKEN)
    assert result["status"] == "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED"
    assert result["row_count"] == 1 and result["same_requested_identity_observed"]
    assert result["complete_nonempty_single_row_observed"] and result["table_structure_validation_performed"]
    assert result["envelope_shape"]["http_response_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["envelope_shape"]["http_response_bytes"] == len(original)
    assert all(result[key] == value for key, value in probe.FLAGS.items())
    text = json.dumps(result)
    assert all(private not in text for private in (TOKEN, PRIVATE, "13.579", "30100", "987654.321", "12.123"))


def test_exact_complete_empty_is_not_price_or_no_trade_evidence():
    result = diagnose(payload(empty=True))
    assert result["status"] == "EMPTY_NOT_PRICE_EVIDENCE" and result["row_count"] == 0
    assert not result["same_requested_identity_observed"] and not result["complete_nonempty_single_row_observed"]
    assert not result["fallback_generated"] and not result["entry_source_eligible"]
    assert not any(key in result for key in ("price", "return", "net_return", "no_fill"))


@pytest.mark.parametrize("value", [None, False, True, 0, "…", "....", " ...", "... ", "．．．", "\n", [], {}])
def test_invalid_detail_never_relaxed_or_saved(value):
    response = payload()
    response["detail"] = value
    result = diagnose(response)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_BLOCKED"
    assert result["reason"] == "NONEMPTY_OR_INVALID_API_DETAIL"
    assert result["row_count"] is None


@pytest.mark.parametrize("code", [None, False, True, 0.0, "0", -1, 1, 2002])
def test_success_code_must_be_exact_integer_zero(code):
    value = payload()
    value["code"] = code
    result = diagnose(value)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_BLOCKED"
    assert result["reason"] == "SUCCESS_CODE_EXACT_ZERO_REQUIRED" and result["row_count"] is None


@pytest.mark.parametrize("key,value", [("has_more", None), ("has_more", True), ("has_more", 0), ("has_more", "false"),
    ("count", None), ("count", False), ("count", True), ("count", 0.0), ("count", "1"), ("count", -1), ("count", 2),
    ("items", None), ("items", {}), ("items", False)])
def test_explicit_complete_table_requires_exact_types(key, value):
    response = payload()
    response["data"][key] = value
    result = diagnose(response)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_BLOCKED"
    assert result["reason"] == "EXPLICIT_COMPLETE_TABLE_REQUIRED"


@pytest.mark.parametrize("key", ["has_more", "count", "items"])
def test_missing_pagination_not_imputed(key):
    response = payload()
    del response["data"][key]
    assert diagnose(response)["reason"] == "EXPLICIT_COMPLETE_TABLE_REQUIRED"


@pytest.mark.parametrize("kind,reason", [
    ("wrong_day", "SOURCE_WRONG_TRADE_DATE"), ("wrong_code", "SOURCE_WRONG_REQUESTED_CODE"),
    ("two_rows", "INVALID_ROW_COUNT_OR_POSSIBLE_TRUNCATION"), ("duplicate", "INVALID_ROW_COUNT_OR_POSSIBLE_TRUNCATION"),
    ("duplicate_field", "INVALID_FIELDS"), ("missing_field", "INVALID_FIELDS"), ("extra_field", "INVALID_FIELDS"),
    ("short_row", "INVALID_ROW_SHAPE"), ("nested_value", "UNSAFE_NESTED_TABLE_VALUE"),
    ("extra_data", "INVALID_DATA_TABLE")])
def test_frozen_codec_enforces_same_day_same_code_uniqueness_and_shape(kind, reason):
    value = payload()
    data = value["data"]
    if kind == "wrong_day": data["items"][0][1] = "20250320"
    elif kind == "wrong_code": data["items"][0][0] = "000678.SZ"
    elif kind == "two_rows": data["items"].append(["000678.SZ", DAY, 1, 100, 100, 1])
    elif kind == "duplicate": data["items"].append(list(data["items"][0]))
    elif kind == "duplicate_field": data["fields"][2] = "vol"
    elif kind == "missing_field": data["fields"].pop()
    elif kind == "extra_field": data["fields"].append("other")
    elif kind == "short_row": data["items"][0].pop()
    elif kind == "nested_value": data["items"][0][2] = {"private": PRIVATE}
    else: data["unknown"] = PRIVATE
    result = diagnose(value)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_BLOCKED" and result["reason"] == reason
    assert result["table_structure_validation_performed"] and result["row_count"] is None
    assert PRIVATE not in json.dumps(result)


def test_wrong_identity_cannot_substitute_the_other_registered_pair():
    value = payload(*probe.PAIRS[1])
    assert diagnose(value)["reason"] in {"SOURCE_WRONG_TRADE_DATE", "SOURCE_WRONG_REQUESTED_CODE"}


def test_fields_order_preserved_and_count_one_is_accepted():
    value = payload()
    value["data"]["fields"].reverse()
    value["data"]["items"][0].reverse()
    value["data"]["count"] = 1
    assert diagnose(value)["status"] == "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED"


def test_numeric_errors_are_not_price_qualification():
    value = payload()
    value["data"]["items"][0][2:] = [None, -1, False, 0]
    result = diagnose(value)
    assert result["status"] == "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED"
    assert not result["entry_price_qualification_performed"] and not result["capacity_qualification_performed"]


@pytest.mark.parametrize("original", [None, "notbytes", b"", b"x" * 4_000_001, b"bad", b"\xff",
    b'{"code":0,"code":0}', b'{"data":{"count":NaN}}', b'{"data":{"count":Infinity}}', b'{"data":{"count":1e400}}'])
def test_malformed_duplicate_nonfinite_or_oversized_response_not_parsed(original):
    result = probe.diagnose_response(original, DAY, CODE, token=TOKEN)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_NOT_PARSED" and result["row_count"] is None
    assert not result["source_import_allowed"] and not result["source_files_written"]


@pytest.mark.parametrize("location", ["msg", "request_id", "detail", "row", "unknown_key"])
def test_credential_never_saved_even_in_ignored_envelope_fields(location):
    value = payload()
    if location == "row": value["data"]["items"][0][2] = TOKEN
    elif location == "unknown_key": value[TOKEN] = TOKEN
    else: value[location] = TOKEN
    result = diagnose(value)
    assert result["status"] == "SINGLE_STOCK_RESPONSE_BLOCKED"
    assert TOKEN not in json.dumps(result)


def test_json_escaped_credential_and_unrelated_opaque_cell_are_rejected():
    value = payload()
    value["msg"] = TOKEN
    escaped = "".join("\\u%04x" % ord(ch) for ch in TOKEN)
    encoded = raw(value).replace(TOKEN.encode(), escaped.encode())
    assert probe.diagnose_response(encoded, DAY, CODE, token=TOKEN)["reason"] == "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED"
    value = payload()
    value["data"]["items"][0][2] = "abcd1234" * 8
    assert diagnose(value)["reason"] == "CREDENTIAL_LIKE_SOURCE_NOT_PERSISTED"


def test_unknown_envelope_value_and_name_are_not_persisted():
    value = payload()
    value[PRIVATE] = {"more": PRIVATE}
    result = diagnose(value)
    assert result["reason"] == "INVALID_SINGLE_STOCK_API_ENVELOPE"
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("day,code", [("20250319", "000995.SZ"), ("20260203", "000612.SZ"), ("20250320", CODE), (20250319, CODE), (DAY, None)])
def test_exact_pair_scope_not_cross_product(day, code):
    with pytest.raises(probe.SingleStockProbeError, match="PAIR_OUT_OF_SCOPE"):
        probe.request_contract(day, code)
    with pytest.raises(probe.SingleStockProbeError, match="PAIR_OUT_OF_SCOPE"):
        probe.diagnose_response(raw(), day, code)


def test_unknown_codec_error_is_replaced_with_fixed_enum(monkeypatch):
    def bad(*args, **kwargs): raise probe.codec.AuctionSourceError(TOKEN + PRIVATE)
    monkeypatch.setattr(probe.codec, "table_rows", bad)
    result = diagnose()
    assert result["reason"] == "INVALID_SINGLE_STOCK_TABLE"
    assert TOKEN not in json.dumps(result) and PRIVATE not in json.dumps(result)


def test_official_transport_exact_documented_params_https_noredirect(monkeypatch):
    seen = {}
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            seen["size"] = size
            return raw()
    class Opener:
        def open(self, req, timeout):
            seen.update(url=req.full_url, method=req.method, body=json.loads(req.data), timeout=timeout)
            return Reply()
    def build(handler):
        assert isinstance(handler, probe._NoRedirect)
        assert handler.redirect_request(None, None, 302, TOKEN, {}, "http://other") is None
        return Opener()
    monkeypatch.setattr(probe.request, "build_opener", build)
    assert probe.official_call(probe.ENDPOINT, {"trade_date": DAY, "ts_code": CODE}, probe.FIELDS, TOKEN, 20) == raw()
    assert seen == {"url": "https://api.tushare.pro", "method": "POST", "size": 4_000_001, "timeout": 20,
                    "body": {"api_name": "stk_auction", "params": {"trade_date": DAY, "ts_code": CODE},
                             "fields": ",".join(probe.FIELDS), "token": TOKEN}}


@pytest.mark.parametrize("change", ["ts_type", "offset", "limit", "missing_code", "wrong_code", "wrong_day", "fields", "timeout", "endpoint"])
def test_transport_scope_rejects_any_unregistered_filter_or_endpoint(change):
    params, fields, timeout, endpoint = {"trade_date": DAY, "ts_code": CODE}, probe.FIELDS, 20, probe.ENDPOINT
    if change in ("ts_type", "offset", "limit"): params[change] = "STK"
    elif change == "missing_code": del params["ts_code"]
    elif change == "wrong_code": params["ts_code"] = "000995.SZ"
    elif change == "wrong_day": params["trade_date"] = "20250320"
    elif change == "fields": fields = tuple(reversed(fields))
    elif change == "timeout": timeout = 20.0
    else: endpoint = "daily"
    with pytest.raises(probe.SingleStockProbeError):
        probe.official_call(endpoint, params, fields, TOKEN, timeout)


@pytest.mark.parametrize("body", [b"", b"x" * 4_000_001, None, "not bytes"])
def test_official_transport_bounded_body_without_retry(monkeypatch, body):
    calls = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            assert size == 4_000_001
            return body
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            return Reply()
    monkeypatch.setattr(probe.request, "build_opener", lambda *_: Opener())
    with pytest.raises(probe.SingleStockProbeError, match="WITHIN_4MB"):
        probe.official_call(probe.ENDPOINT, {"trade_date": DAY, "ts_code": CODE}, probe.FIELDS, TOKEN, 20)
    assert len(calls) == 1


def test_two_calls_exact_order_both_results_only_one_safe_output(tmp_path, monkeypatch):
    for name in ("source_bytes", "source_paths", "load", "entry_price", "qualify_row"):
        monkeypatch.setattr(probe.codec, name, forbidden)
    calls = []
    def fake(endpoint, params, fields, token, timeout):
        calls.append((endpoint, dict(params), tuple(fields), timeout))
        return raw(payload(params["trade_date"], params["ts_code"]))
    output = tmp_path / "new"
    report = probe.run_probe(output, token=TOKEN, call=fake)
    assert calls == [(probe.ENDPOINT, {"trade_date": day, "ts_code": code}, probe.FIELDS, 20) for day, code in probe.PAIRS]
    assert report["api_calls"] == report["max_api_calls"] == 2 and report["retries"] == 0
    assert report["status"] == "DIAGNOSTIC_COMPLETE" and report["callable_injected_for_test"]
    assert all(not item["network_request_performed"] for item in report["requests"])
    assert all(item["diagnostic"]["status"] == "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED" for item in report["requests"])
    assert {p.name for p in output.iterdir()} == {probe.OUTPUT_FILE}
    saved = json.loads((output / probe.OUTPUT_FILE).read_bytes())
    assert saved == report and TOKEN not in json.dumps(saved) and PRIVATE not in json.dumps(saved)
    assert all(report[key] == value for key, value in probe.FLAGS.items())
    assert len(report["execution_file_bindings"]) == 7
    assert not report["independent_run_identity_verified"]


@pytest.mark.parametrize("failure", ["network", "http", "parse", "blocked", "empty"])
def test_first_failure_never_retried_and_second_pair_always_recorded(tmp_path, failure):
    calls = []
    def fake(endpoint, params, fields, token, timeout):
        calls.append((params["trade_date"], params["ts_code"]))
        if len(calls) == 1:
            if failure == "network": raise OSError(TOKEN + PRIVATE)
            if failure == "http": raise error.HTTPError("https://api.tushare.pro", 429, TOKEN + PRIVATE, {}, None)
            if failure == "parse": return b"bad"
            value = payload(params["trade_date"], params["ts_code"], empty=failure == "empty")
            if failure == "blocked": value["data"]["has_more"] = True
            return raw(value)
        return raw(payload(params["trade_date"], params["ts_code"]))
    report = probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert calls == list(probe.PAIRS) and report["api_calls"] == 2 and len(report["requests"]) == 2
    assert report["requests"][1]["diagnostic"]["status"] == "SINGLE_STOCK_COMPLETE_NONEMPTY_OBSERVED"
    assert report["status"] == ("DIAGNOSTIC_COMPLETE" if failure in ("blocked", "empty") else "DIAGNOSTIC_INCOMPLETE")
    assert TOKEN not in json.dumps(report) and PRIVATE not in json.dumps(report)


def test_absent_credential_keeps_two_unknown_results_without_calls(tmp_path):
    report = probe.run_probe(tmp_path / "new", token="", call=forbidden)
    assert report["api_calls"] == 0 and report["status"] == "DIAGNOSTIC_INCOMPLETE"
    assert len(report["requests"]) == 2
    assert all(item["status"] == "CREDENTIAL_ABSENT" and item["diagnostic"] is None for item in report["requests"])


@pytest.mark.parametrize("key,value", [("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", "01"),
                                      ("GITHUB_REF", "refs/heads/other"), ("GITHUB_REF", "refs/pull/1/merge")])
def test_main_branch_first_attempt_only_before_calls(tmp_path, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(probe.SingleStockProbeError, match="(RERUN_NOT_ALLOWED|MAIN_BRANCH_REQUIRED)"):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=forbidden)
    assert not list(tmp_path.iterdir())


def test_run_identity_is_safe_environment_claim_not_external_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "12345678")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    report = probe.run_probe(tmp_path / "new", token="", call=forbidden)
    assert report["run_id"] == "12345678" and report["run_commit"] == "a" * 40
    assert not report["independent_run_identity_verified"]
    monkeypatch.setenv("GITHUB_RUN_ID", TOKEN)
    monkeypatch.setenv("GITHUB_SHA", TOKEN)
    report = probe.run_probe(tmp_path / "other", token="", call=forbidden)
    assert report["run_id"] is None and report["run_commit"] is None
    assert TOKEN not in json.dumps(report)


def test_duplicate_output_and_symlink_output_rejected(tmp_path):
    output = tmp_path / "new"
    probe.run_probe(output, token="", call=forbidden)
    original = (output / probe.OUTPUT_FILE).read_bytes()
    with pytest.raises(probe.SingleStockProbeError, match="FRESH_UNALIASED"):
        probe.run_probe(output, token=TOKEN, call=forbidden)
    assert (output / probe.OUTPUT_FILE).read_bytes() == original
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(probe.SingleStockProbeError, match="FRESH_UNALIASED"):
        probe.run_probe(alias, token=TOKEN, call=forbidden)


def test_output_inside_checkout_rejected_before_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "CHECKOUT", tmp_path / "fake-checkout")
    with pytest.raises(probe.SingleStockProbeError, match="OUTSIDE_CHECKOUT"):
        probe.run_probe(probe.CHECKOUT / "new", token=TOKEN, call=forbidden)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("dependency", list(probe.PINNED_DEPENDENCIES))
def test_registration_and_dependency_guards(monkeypatch, dependency):
    bindings = probe._guard()
    assert set(bindings) == {"auction_single_stock_probe.py", probe.CONTRACT_FILE, *probe.PINNED_DEPENDENCIES}
    original = probe._file_sha
    monkeypatch.setattr(probe, "_file_sha", lambda path: "0" * 64 if Path(path).name == dependency else original(path))
    with pytest.raises(probe.SingleStockProbeError, match="PINNED_SINGLE_STOCK_DEPENDENCY_CHANGED"):
        probe._guard()


def test_contract_drift_is_rejected_without_rewriting_file(monkeypatch):
    expected = probe.expected_contract()
    monkeypatch.setattr(probe, "expected_contract", lambda: {**expected, "retries": 1})
    with pytest.raises(probe.SingleStockProbeError, match="CONTRACT_CHANGED"):
        probe._guard()


def test_midrun_guard_drift_aborts_before_second_call_and_writing_result(tmp_path, monkeypatch):
    bindings, calls = probe._guard(), []
    checks = 0
    def guard():
        nonlocal checks
        checks += 1
        return bindings if checks <= 2 else {**bindings, "auction_single_stock_probe.py": "0" * 64}
    monkeypatch.setattr(probe, "_guard", guard)
    def fake(endpoint, params, fields, token, timeout):
        calls.append(params)
        return raw()
    with pytest.raises(probe.SingleStockProbeError, match="EXECUTION_BINDINGS_CHANGED"):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert len(calls) == 1 and not (tmp_path / "new" / probe.OUTPUT_FILE).exists()


@pytest.mark.parametrize("status,exit_code", [("DIAGNOSTIC_COMPLETE", 0), ("DIAGNOSTIC_INCOMPLETE", 2), ("error", 1)])
def test_cli_exit_codes_and_safe_error_output(tmp_path, monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(probe.sys, "argv", ["probe", "--output", str(tmp_path / "new")])
    def fake(*args, **kwargs):
        if status == "error": raise OSError(TOKEN + PRIVATE)
        return {"status": status, "api_calls": 2, "max_api_calls": 2}
    monkeypatch.setattr(probe, "run_probe", fake)
    assert probe.main() == exit_code
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err and PRIVATE not in output.out + output.err
