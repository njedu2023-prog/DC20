"""Offline synthetic pagination-shape diagnostics; never live HTTP or labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib import error

import pytest

from work.profit_1000_upgrade import auction_pagination_probe as probe

TOKEN = "synthetic_credential_123456789_test"
PRIVATE = "PRIVATE_PROVIDER_VALUE_837152963"


def payload(**changes):
    return {"code": 0, "msg": PRIVATE, "request_id": PRIVATE, "detail": "...",
            "data": {"fields": list(probe.FIELDS), "items": [["600001.SH", "20250319", 10, 100, 1000, PRIVATE]],
                     "count": 0, "has_more": False}, **changes}


def raw(value=None):
    return json.dumps(payload() if value is None else value, ensure_ascii=False).encode()


def diagnose(value=None, *, day=probe.DATES[0]):
    return probe.diagnose_response(raw(value), day, token=TOKEN)


def forbidden(*args, **kwargs):
    raise AssertionError("no live HTTP, source/label generation or old runner")


def test_contract_is_two_new_dates_and_three_frozen_dependencies():
    value = probe._guard()
    assert value == probe.expected_contract()
    assert value["trade_dates"] == ["20250319", "20260203"]
    assert value["max_api_calls"] == 2 and value["calls_per_date"] == 1 and value["retries"] == 0
    assert value["timeout_seconds"] == 20 and value["max_http_response_bytes"] == 4_000_000
    assert value["redirects_allowed"] is False
    assert value["base_failed_run"]["run_id"] == "34684000971"
    assert value["base_failed_run"]["artifact_id"] == "10295337206"
    assert value["base_failed_run"]["archive_sha256"] == "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"


def test_valid_shape_is_only_predicates_not_table_or_source_acceptance(monkeypatch):
    from work.profit_1000_upgrade import auction_http_v3, auction_truth_v3
    for module, names in ((auction_http_v3, ["source_bytes"]), (auction_truth_v3, ["source_bytes", "table_rows", "load", "entry_price"]),
                          (probe.shape, ["_guard", "run_probe", "official_call"])):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    response = raw()
    result = probe.diagnose_response(response, probe.DATES[1], token=TOKEN)
    shape, checks = result["envelope_shape"], result["pagination"]
    assert result["trade_date"] == shape["trade_date"] == "20260203"
    assert shape["http_response_sha256"] == hashlib.sha256(response).hexdigest()
    assert shape["http_response_bytes"] == len(response)
    assert checks["placeholder_preconditions_pass"] is True and checks["failed_placeholder_preconditions"] == []
    assert checks["count_is_exact_integer"] and checks["count_is_zero"] and not checks["count_equals_items_row_count"]
    assert checks["count_value_if_bounded_integer"] == 0 and checks["items_row_count"] == 1
    assert checks["has_more_is_exact_false"] and not checks["has_more_is_exact_true"]
    assert result["server_error_category"] is None
    for key, value in probe.FLAGS.items():
        assert result[key] == value
    text = json.dumps(result)
    for private in (TOKEN, PRIVATE, "600001.SH"):
        assert private not in text


@pytest.mark.parametrize("detail", [None, {}, [], False, 0, "", "…", "....", " ...", "... ", TOKEN])
def test_detail_is_exact_placeholder_only_without_value_persistence(detail):
    result = diagnose(payload(detail=detail))
    assert result["pagination"]["detail_is_ascii_placeholder"] is False
    assert "DETAIL_EXACT_ASCII_PLACEHOLDER" in result["pagination"]["failed_placeholder_preconditions"]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("value,kind,false,true", [(None, "null", False, False), (0, "integer", False, False),
    (1, "integer", False, False), (False, "boolean", True, False), (True, "boolean", False, True),
    ("false", "string", False, False), ([], "array", False, False), ({}, "object", False, False)])
def test_has_more_never_coerces_null_integer_or_string(value, kind, false, true):
    value_payload = payload()
    value_payload["data"]["has_more"] = value
    checks = diagnose(value_payload)["pagination"]
    assert checks["has_more_present"] and checks["has_more_type"] == kind
    assert checks["has_more_is_exact_false"] is false and checks["has_more_is_exact_true"] is true
    assert ("HAS_MORE_EXACT_FALSE" in checks["failed_placeholder_preconditions"]) is (not false)


@pytest.mark.parametrize("value,kind,integer,zero,equal,saved", [
    (0, "integer", True, True, False, 0), (1, "integer", True, False, True, 1), (2, "integer", True, False, False, 2),
    (-1, "integer", True, False, False, None), (10_000_000, "integer", True, False, False, 10_000_000),
    (10_000_001, "integer", True, False, False, None), (False, "boolean", False, False, False, None),
    (True, "boolean", False, False, False, None), (0.0, "number", False, False, False, None),
    (None, "null", False, False, False, None), ("0", "string", False, False, False, None),
    (TOKEN, "string", False, False, False, None), ([], "array", False, False, False, None), ({}, "object", False, False, False, None)])
def test_count_exact_types_and_bounded_integer_values(value, kind, integer, zero, equal, saved):
    value_payload = payload()
    value_payload["data"]["count"] = value
    result = diagnose(value_payload)
    checks = result["pagination"]
    assert checks["count_present"] and checks["count_type"] == kind
    assert checks["count_is_exact_integer"] is integer and checks["count_is_zero"] is zero
    assert checks["count_equals_items_row_count"] is equal and checks["count_value_if_bounded_integer"] == saved
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("key,predicate", [("count", "COUNT_PRESENT"), ("has_more", "HAS_MORE_PRESENT"), ("items", "ITEMS_EXACT_ARRAY")])
def test_missing_fields_are_not_imputed_to_zero_or_false(key, predicate):
    value = payload()
    del value["data"][key]
    checks = diagnose(value)["pagination"]
    assert checks[key + "_type"] == "missing"
    assert not checks[key + "_present"]
    assert predicate in checks["failed_placeholder_preconditions"]


@pytest.mark.parametrize("items", [None, {}, "items", False, 0])
def test_nonarray_items_cannot_supply_row_count_or_count_equality(items):
    value = payload()
    value["data"]["items"] = items
    checks = diagnose(value)["pagination"]
    assert checks["items_is_exact_list"] is False and checks["items_row_count"] is None
    assert checks["count_equals_items_row_count"] is False


@pytest.mark.parametrize("data", [None, [], "data", 0, False])
def test_nondict_data_is_explained_without_values(data):
    checks = diagnose(payload(data=data))["pagination"]
    assert checks["data_is_exact_dict"] is False
    assert {"DATA_EXACT_OBJECT", "HAS_MORE_PRESENT", "COUNT_PRESENT", "ITEMS_EXACT_ARRAY"} <= set(checks["failed_placeholder_preconditions"])


@pytest.mark.parametrize("code", [False, True, 0.0, "0", None, -1, 1])
def test_only_exact_integer_zero_passes_code_predicate(code):
    result = diagnose(payload(code=code))
    assert not result["pagination"]["code_is_exact_integer_zero"]
    assert "CODE_EXACT_INTEGER_ZERO" in result["pagination"]["failed_placeholder_preconditions"]


@pytest.mark.parametrize("message,category", [("频率限制", "RATE_LIMIT"), ("too many requests", "RATE_LIMIT"),
    ("权限不足", "ENTITLEMENT"), ("permission denied", "ENTITLEMENT"), ("token invalid", "AUTH"),
    ("认证失败", "AUTH"), (PRIVATE, "OTHER"), ({"private": PRIVATE}, "OTHER")])
def test_nonzero_api_message_only_becomes_finite_diagnostic_category(message, category):
    result = diagnose(payload(code=-123, msg=message))
    assert result["server_error_category"] == category
    assert PRIVATE not in json.dumps(result)
    assert str(-123) not in json.dumps(result)
    assert diagnose(payload(code=0, msg=message))["server_error_category"] is None


@pytest.mark.parametrize("response", [None, "not bytes", b"", pytest.param(b"x" * 4_000_001, id="oversized-4mb"), b"not json", b"\xff",
    b'{"count":0,"count":1}', b'{"data":{"count":NaN}}', b'{"data":{"count":1e400}}'])
def test_invalid_original_response_has_no_parsed_pagination_or_source_claim(response):
    result = probe.diagnose_response(response, probe.DATES[0], token=TOKEN)
    assert result["status"] == "PAGINATION_NOT_PARSED" and result["pagination"] is None
    assert result["source_import_allowed"] is False


def test_unknown_fields_messages_detail_and_market_cells_only_redact_or_derive():
    value = payload(detail=TOKEN, msg=TOKEN, request_id=TOKEN)
    value[TOKEN] = {"private": PRIVATE}
    value["a" * 24] = TOKEN
    value["data"]["fields"].append(TOKEN)
    value["data"]["items"][0].append(TOKEN)
    result = diagnose(value)
    text = json.dumps(result)
    assert TOKEN not in text and PRIVATE not in text and "a" * 24 not in text
    assert result["envelope_shape"]["redacted_top_level_names"] == 2


@pytest.mark.parametrize("day", ["20250102", "20250318", "2026-02-03", None, 20260203])
def test_date_scope_is_exact(day):
    with pytest.raises(probe.PaginationProbeError, match="OUT_OF_SCOPE"):
        probe.diagnose_response(raw(), day)


def test_two_sequential_calls_save_exactly_one_diagnostic_file(tmp_path):
    calls = []
    def fake(endpoint, params, fields, token, timeout):
        calls.append((endpoint, dict(params), tuple(fields), timeout))
        return raw()
    output = tmp_path / "new"
    report = probe.run_probe(output, token=TOKEN, call=fake)
    assert calls == [("stk_auction", {"trade_date": day}, probe.FIELDS, 20) for day in probe.DATES]
    assert report["api_calls"] == 2 and report["max_api_calls"] == 2 and report["retries"] == 0
    assert report["callable_injected_for_test"] is True
    assert all(not r["network_request_performed"] and r["api_calls"] == 1 for r in report["requests"])
    assert {p.relative_to(output).as_posix() for p in output.rglob("*")} == {"auction_pagination_probe.json"}
    saved = json.loads((output / "auction_pagination_probe.json").read_bytes())
    assert saved == report and len(saved["execution_file_bindings"]) == 6
    assert TOKEN not in json.dumps(saved) and PRIVATE not in json.dumps(saved)


@pytest.mark.parametrize("failure", ["http", "exception", "invalid"])
def test_failure_is_not_retried_and_second_date_still_has_one_diagnostic_slot(tmp_path, failure):
    calls = []
    def fake(*args):
        calls.append(args[1]["trade_date"])
        if len(calls) == 1:
            if failure == "http":
                raise error.HTTPError("https://api.tushare.pro", 429, TOKEN + PRIVATE, {}, None)
            if failure == "exception":
                raise OSError(TOKEN + PRIVATE)
            return b"not JSON"
        return raw()
    report = probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert calls == list(probe.DATES) and report["api_calls"] == 2 and report["status"] == "DIAGNOSTIC_INCOMPLETE"
    assert report["requests"][1]["status"] == "DIAGNOSTIC_COMPLETE"
    assert TOKEN not in json.dumps(report) and PRIVATE not in json.dumps(report)


def test_no_credentials_do_not_read_secret_or_make_requests(tmp_path):
    report = probe.run_probe(tmp_path / "new", token="", call=forbidden)
    assert report["api_calls"] == 0 and len(report["requests"]) == 2
    assert all(row["status"] == "CREDENTIAL_ABSENT" for row in report["requests"])


@pytest.mark.parametrize("filename", [p.name for p in probe._IMPLEMENTATIONS])
@pytest.mark.parametrize("when", ["before", "during"])
def test_code_or_contract_change_aborts_before_artifact_persistence(tmp_path, monkeypatch, filename, when):
    original = probe._file_sha
    changed = [when == "before"]
    monkeypatch.setattr(probe, "_file_sha", lambda path: "0" * 64 if Path(path).name == filename and changed[0] else original(path))
    def fake(*args):
        changed[0] = True
        return raw()
    with pytest.raises(probe.PaginationProbeError):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert not (tmp_path / "new/auction_pagination_probe.json").exists()


def test_repeat_run_existing_output_and_symlinks_are_rejected(tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep"
    marker.write_bytes(b"keep")
    with pytest.raises(probe.PaginationProbeError):
        probe.run_probe(output, token=TOKEN, call=forbidden)
    assert marker.read_bytes() == b"keep"
    link = tmp_path / "alias"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(probe.PaginationProbeError):
        probe.run_probe(link / "new", token=TOKEN, call=forbidden)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(probe.PaginationProbeError, match="RERUN"):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=forbidden)


def test_checkout_and_ancestor_output_are_forbidden_without_writing(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(probe, "CHECKOUT", root)
    with pytest.raises(probe.PaginationProbeError, match="OUTSIDE_CHECKOUT"):
        probe.run_probe(root / "new", token=TOKEN, call=forbidden)
    assert not (root / "new").exists()


def test_official_transport_posts_only_two_scoped_dates_without_redirect(monkeypatch):
    calls, reads = [], []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            reads.append(maximum)
            return raw()
    class Opener:
        def open(self, req, timeout):
            calls.append((req, timeout))
            return Response()
    def opener(handler):
        assert handler.redirect_request(None, None, 302, None, None, "https://other.invalid") is None
        return Opener()
    monkeypatch.setattr(probe.request, "build_opener", opener)
    for day in probe.DATES:
        probe.official_call("stk_auction", {"trade_date": day}, probe.FIELDS, TOKEN, 20)
    assert reads == [4_000_001, 4_000_001]
    for (req, timeout), day in zip(calls, probe.DATES):
        assert req.full_url == "https://api.tushare.pro" and req.method == "POST" and timeout == 20
        assert json.loads(req.data) == {"api_name": "stk_auction", "params": {"trade_date": day},
                                      "fields": ",".join(probe.FIELDS), "token": TOKEN}


@pytest.mark.parametrize("changes", [{"endpoint": "stk_mins"}, {"params": {"trade_date": "20250102"}},
    {"params": {"trade_date": "20250319", "ts_code": "600001.SH"}}, {"fields": ("price",)},
    {"timeout": 21}, {"timeout": 20.0}])
def test_official_call_cannot_expand_request_scope(monkeypatch, changes):
    monkeypatch.setattr(probe.request, "build_opener", forbidden)
    kwargs = dict(endpoint="stk_auction", params={"trade_date": "20250319"}, fields=probe.FIELDS, token=TOKEN, timeout=20)
    with pytest.raises(probe.PaginationProbeError, match="SCOPE"):
        probe.official_call(**{**kwargs, **changes})
