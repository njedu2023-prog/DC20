"""Synthetic two-date detail diagnostics; never live HTTP or source evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib import error

import pytest
import yaml

from work.profit_1000_upgrade import auction_detail_probe as probe

TOKEN = "synthetic_credential_123456789_test"
PRIVATE_MESSAGE = "PRIVATE_SERVER_MESSAGE_749263851"
PRIVATE_ID = "PRIVATE_REQUEST_ID_381264579"
PRIVATE_CELL = "PRIVATE_MARKET_VALUE_529471836"


def payload(detail="信息提示", *, day="20250102", **extra):
    return {"code": 0, "msg": PRIVATE_MESSAGE, "request_id": PRIVATE_ID, "detail": detail,
            "data": {"fields": list(probe.FIELDS), "items": [["600001.SH", day, 10, 100, 1000, PRIVATE_CELL]],
                     "count": 0, "has_more": False}, **extra}


def raw(detail="信息提示", **kwargs):
    return json.dumps(payload(detail, **kwargs), ensure_ascii=False).encode()


def diagnose(detail="信息提示", *, day="20250102", token=TOKEN):
    return probe.diagnose_response(raw(detail, day=day), day, token=token)


def forbidden(*args, **kwargs):
    raise AssertionError("no live HTTP, old runner, source writer, training or codec change")


def test_contract_is_two_exact_dates_and_pins_original_pure_helper_and_codecs():
    value = probe._guard()
    assert value == probe.expected_contract()
    assert value["trade_dates"] == ["20250102", "20250116"]
    assert value["max_api_calls"] == 2 and value["calls_per_date"] == 1 and value["retries"] == 0
    assert value["timeout_seconds"] == 20 and value["max_http_response_bytes"] == 4_000_000
    assert value["shape_helper_sha256"] == "a61919ad87a098bb60b3d494eda699c2d075f1ca1ad9ab9080c9b000e8a3a929"
    assert value["diagnostic_scope"] == "ONLY_SANITIZED_DETAIL_DIAGNOSTIC_NOT_SOURCE"
    assert value["detail_max_characters"] == 200
    assert value["detail_encoding_policy"] == "REJECT_BACKSLASH_PERCENT_AMPERSAND_WITHOUT_DECODING"


@pytest.mark.parametrize("text", ["", " ", "提示", "暂无该日数据", "权限不足", "plain short message",
                                   "data is incomplete", "{}", "null", "中文" * 100])
def test_safe_short_plain_detail_is_retained_verbatim_but_not_qualified(text):
    result = diagnose(text)
    detail = result["detail"]
    assert detail["json_type"] == "string" and detail["char_length"] == len(text)
    assert detail["sanitized_text"] == text and detail["text_retained"] is True
    assert detail["is_empty"] is (text == "") and detail["is_empty_string"] is (text == "")
    assert detail["is_null"] is False and detail["redaction_reason"] is None
    for key, value in probe.FLAGS.items():
        assert result[key] == value
    assert result["detail_may_be_ignored"] is result["detail_is_source_qualification"] is False
    serialized = json.dumps(result)
    for private in (TOKEN, PRIVATE_MESSAGE, PRIVATE_ID, PRIVATE_CELL, "600001.SH"):
        assert private not in serialized


@pytest.mark.parametrize("text", [TOKEN, "prefix " + TOKEN, "TOKEN=x", "secret", "password", "auth denied",
    "credential error", "Bearer abc", "cookie value", "session value", "API_KEY=x", "access-key=abc",
    "密钥测试", "凭证内容", "密码内容", "中文" * 101, "a" * 24, "d73a" * 16,
    "mPt6/Qr+4X=" * 5, "12345678", "参考编号12345678", "abcd1234abcd1234", "ＴＯＫＥＮ=x",
    "token\u200bvalue", "第一行\n第二行", "\t", "\x00", "https://example.com", "name@example.com", "🙂"])
def test_credential_opaque_long_numeric_or_unsafe_detail_is_redacted(text):
    result = diagnose(text)
    detail = result["detail"]
    assert detail["sanitized_text"] == "REDACTED" and detail["text_retained"] is False
    assert detail["char_length"] == len(text) and detail["json_type"] == "string"
    assert detail["redaction_reason"]
    assert text not in json.dumps(result, ensure_ascii=False)


def test_escaped_known_token_is_screened_after_original_json_parse():
    response = raw(TOKEN).replace(TOKEN.encode(), ("\\u0073" + TOKEN[1:]).encode())
    result = probe.diagnose_response(response, probe.DATES[0], token=TOKEN)
    assert result["detail"]["redaction_reason"] == "KNOWN_CREDENTIAL_MATCH"
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("text", [
    "".join(f"%{ord(ch):02X}" for ch in TOKEN),
    "".join(f"\\u{ord(ch):04x}" for ch in "samplekey"),
    "".join(f"&#{ord(ch)};" for ch in "samplekey"),
    "%2573%2561%256D%2570%256C%2565%256B%2565%2579", "%u0073", "\\x73", "&colon;",
    "ordinary & text", "15% complete", "％７３", "＆＃７３；", "＼u0073"])
def test_encoded_or_escape_introducers_are_redacted_without_decoding(text):
    result = diagnose(text, token="samplekey")
    detail = result["detail"]
    assert detail["sanitized_text"] == "REDACTED" and detail["text_retained"] is False
    assert detail["redaction_reason"] == "ENCODED_OR_ESCAPED_TEXT_FORBIDDEN"
    assert detail["char_length"] == len(text)
    assert text not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("encode", [
    lambda text: "".join(f"%{ord(ch):02X}" for ch in text),
    lambda text: "".join(f"\\u{ord(ch):04x}" for ch in text),
    lambda text: "".join(f"&#{ord(ch)};" for ch in text)])
def test_known_credential_encoding_never_enters_persisted_artifact(tmp_path, encode):
    token = "samplekey"
    encoded = encode(token)
    result = probe.run_probe(tmp_path / "new", token=token, call=lambda *args: raw(encoded))
    artifact = (tmp_path / "new/auction_detail_probe.json").read_text()
    assert encoded not in artifact and token not in artifact
    assert all(item["diagnostic"]["detail"]["text_retained"] is False for item in result["requests"])


@pytest.mark.parametrize("value,kind,empty,null", [
    (None, "null", False, True), ({}, "object", True, False), ([], "array", True, False),
    ({"hidden": TOKEN}, "object", False, False), ([TOKEN], "array", False, False),
    (True, "boolean", False, False), (0, "integer", False, False), (12.34, "number", False, False)])
def test_nonstring_values_retain_only_type_and_empty_null_flags(value, kind, empty, null):
    detail = diagnose(value)["detail"]
    assert detail["json_type"] == kind and detail["char_length"] is None
    assert detail["is_empty"] is empty and detail["is_null"] is null
    assert detail["sanitized_text"] == "REDACTED" and detail["text_retained"] is False
    assert detail["is_empty_object"] is (type(value) is dict and not value)
    assert detail["is_empty_array"] is (type(value) is list and not value)
    assert TOKEN not in json.dumps(detail)


def test_missing_detail_is_distinct_from_null_empty_string_and_empty_object():
    value = payload()
    del value["detail"]
    detail = probe.diagnose_response(json.dumps(value).encode(), probe.DATES[0])["detail"]
    assert detail["present"] is False and detail["json_type"] == "missing"
    assert detail["is_null"] is detail["is_empty"] is False
    assert detail["redaction_reason"] == "ABSENT"


def test_original_shape_helper_is_reused_purely_and_reports_each_actual_date(monkeypatch):
    monkeypatch.setattr(probe.shape, "_guard", forbidden)
    monkeypatch.setattr(probe.shape, "run_probe", forbidden)
    monkeypatch.setattr(probe.shape, "official_call", forbidden)
    for day in probe.DATES:
        response = raw("提示", day=day, unexpected_meta={"hidden": PRIVATE_CELL})
        result = probe.diagnose_response(response, day, token=TOKEN)
        observed = result["envelope_shape"]
        assert result["trade_date"] == observed["trade_date"] == day
        assert observed["http_response_sha256"] == hashlib.sha256(response).hexdigest()
        assert observed["http_response_bytes"] == len(response)
        assert {r["name"] for r in observed["extra_top_level_fields"]} == {"detail", "unexpected_meta"}
        assert observed["table_shape"]["row_count"] == 1
        assert PRIVATE_CELL not in json.dumps(result)


@pytest.mark.parametrize("response", [None, "not bytes", b"", pytest.param(b"x" * 4_000_001, id="oversized-4mb"), b"not json",
    b'{"detail":"a","detail":"b"}', b'{"detail":NaN}', b'{"detail":1e400}', b"\xff"])
def test_invalid_original_response_never_retains_detail_or_claims_source(response):
    result = probe.diagnose_response(response, probe.DATES[0])
    assert result["status"] == "DETAIL_NOT_PARSED" and result["detail"] is None
    assert result["source_import_allowed"] is False


@pytest.mark.parametrize("day", ["20260817", "20250103", "2025-01-02", None, 20250102])
def test_pure_diagnostic_is_date_bounded(day):
    with pytest.raises(probe.DetailProbeError, match="OUT_OF_SCOPE"):
        probe.diagnose_response(raw(), day)


def test_runner_calls_exactly_two_dates_once_and_saves_only_one_sanitized_json(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shape, "_guard", forbidden)
    monkeypatch.setattr(probe.shape, "run_probe", forbidden)
    calls = []
    def fake(endpoint, params, fields, token, timeout):
        calls.append((endpoint, params, fields, token, timeout))
        return raw("暂无数据" if params["trade_date"] == "20250102" else {}, day=params["trade_date"])
    output = tmp_path / "new"
    result = probe.run_probe(output, token=TOKEN, call=fake)
    assert [item[1] for item in calls] == [{"trade_date": day} for day in probe.DATES]
    assert len(calls) == result["api_calls"] == 2 and result["retries"] == 0
    assert all(item[0] == "stk_auction" and item[2] == probe.FIELDS and item[4] == 20 for item in calls)
    assert result["status"] == "DIAGNOSTIC_COMPLETE" and result["callable_injected_for_test"] is True
    assert all(item["network_request_performed"] is False for item in result["requests"])
    assert [item["trade_date"] for item in result["requests"]] == list(probe.DATES)
    assert {p.name for p in output.iterdir()} == {"auction_detail_probe.json"}
    serialized = (output / "auction_detail_probe.json").read_text()
    assert "暂无数据" in serialized
    for private in (TOKEN, PRIVATE_MESSAGE, PRIVATE_ID, PRIVATE_CELL):
        assert private not in serialized
    assert len(result["execution_file_bindings"]) == len(probe._IMPLEMENTATIONS)


def test_missing_credentials_keep_two_diagnostic_slots_without_network(tmp_path):
    result = probe.run_probe(tmp_path / "new", token="", call=forbidden)
    assert result["api_calls"] == 0 and result["status"] == "DIAGNOSTIC_INCOMPLETE"
    assert len(result["requests"]) == 2
    assert all(r["status"] == "CREDENTIAL_ABSENT" and r["diagnostic"] is None for r in result["requests"])


@pytest.mark.parametrize("kind", ["http", "exception", "invalid_json"])
def test_first_failure_never_retries_or_skips_second_bounded_date(tmp_path, kind):
    calls = []
    def fake(endpoint, params, fields, token, timeout):
        calls.append(params["trade_date"])
        if len(calls) == 1:
            if kind == "http":
                raise error.HTTPError("https://api.tushare.pro", 403, TOKEN + PRIVATE_MESSAGE, {}, None)
            if kind == "exception":
                raise RuntimeError(TOKEN + PRIVATE_MESSAGE)
            return b"not json"
        return raw("信息提示", day=params["trade_date"])
    result = probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert calls == list(probe.DATES) and result["api_calls"] == 2
    assert result["status"] == "DIAGNOSTIC_INCOMPLETE"
    assert result["requests"][1]["status"] == "DIAGNOSTIC_COMPLETE"
    assert TOKEN not in json.dumps(result) and PRIVATE_MESSAGE not in json.dumps(result)


@pytest.mark.parametrize("filename", ["auction_detail_probe.py", "AUCTION_DETAIL_PROBE.json", "auction_envelope_probe.py",
                                     "AUCTION_ENVELOPE_PROBE.json", "auction_truth_v3.py", "auction_truth.py"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_bound_implementation_change_aborts_without_writing_report(tmp_path, monkeypatch, filename, when):
    original = probe._file_sha
    changed = [when == "before"]
    target = probe.HERE / filename
    monkeypatch.setattr(probe, "_file_sha", lambda path: "0" * 64 if Path(path) == target and changed[0] else original(path))
    def fake(*args):
        changed[0] = True
        return raw()
    with pytest.raises(probe.DetailProbeError):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert not (tmp_path / "new/auction_detail_probe.json").exists()


def test_existing_output_and_symlink_preserve_original_data(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    previous = output / "keep"
    previous.write_bytes(b"keep")
    with pytest.raises(probe.DetailProbeError):
        probe.run_probe(output, token=TOKEN, call=forbidden)
    assert previous.read_bytes() == b"keep"
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(probe.DetailProbeError):
        probe.run_probe(alias / "new", token=TOKEN, call=forbidden)


def test_manual_rerun_is_blocked_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(probe.DetailProbeError, match="RERUN"):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=forbidden)
    assert not (tmp_path / "new").exists()


def test_official_http_is_two_date_bounded_post_with_no_redirects(monkeypatch):
    calls, reads = [], []
    response_bytes = raw()
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            reads.append(maximum)
            return response_bytes
    class Opener:
        def open(self, req, timeout):
            calls.append((req, timeout))
            return Response()
    def opener(handler):
        assert isinstance(handler, probe._NoRedirect)
        assert handler.redirect_request(None, None, 302, None, None, "https://other.invalid") is None
        return Opener()
    monkeypatch.setattr(probe.request, "build_opener", opener)
    for day in probe.DATES:
        assert probe.official_call("stk_auction", {"trade_date": day}, probe.FIELDS, TOKEN, 20) is response_bytes
    assert reads == [4_000_001, 4_000_001]
    for (req, timeout), day in zip(calls, probe.DATES):
        assert req.full_url == "https://api.tushare.pro" and req.method == "POST" and timeout == 20
        assert json.loads(req.data) == {"api_name": "stk_auction", "params": {"trade_date": day},
                                      "fields": ",".join(probe.FIELDS), "token": TOKEN}


@pytest.mark.parametrize("changes", [{"endpoint": "stk_auction_o"}, {"params": {"trade_date": "20260817"}},
    {"params": {"trade_date": "20250102", "ts_code": "600001.SH"}}, {"fields": ("price",)},
    {"timeout": 21}, {"timeout": 20.0}])
def test_official_transport_cannot_expand_scope(monkeypatch, changes):
    monkeypatch.setattr(probe.request, "build_opener", forbidden)
    kwargs = dict(endpoint="stk_auction", params={"trade_date": "20250102"}, fields=probe.FIELDS, token=TOKEN, timeout=20)
    with pytest.raises(probe.DetailProbeError, match="SCOPE"):
        probe.official_call(**{**kwargs, **changes})


def test_workflow_has_exact_new_trigger_readonly_permissions_and_one_secret_step():
    path = probe.CHECKOUT / ".github/workflows/research_auction_detail_probe.yml"
    value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert value["permissions"] == {"contents": "read"}
    assert value["on"] == {"push": {"branches": ["main"], "paths": ["work/profit_1000_upgrade/AUCTION_DETAIL_PROBE.json"]}}
    job = value["jobs"]["diagnostic"]
    assert "github.run_attempt == 1" in job["if"] and "refs/heads/main" in job["if"]
    steps = job["steps"]
    secret_steps = [step for step in steps if "secrets." in str(step)]
    assert len(secret_steps) == 1 and secret_steps[0]["env"] == {"TUSHARE_TOKEN": "${{ secrets.TUSHARE_TOKEN }}"}
    assert "auction_detail_probe.py" in secret_steps[0]["run"]
    assert all(len(step["uses"].rsplit("@", 1)[1]) == 40 for step in steps if "uses" in step)
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "--require-hashes" in commands and "pytest -q work/profit_1000_upgrade" in commands
    assert "validate_decision_model_freeze.py" in commands and "git status --porcelain=v1" in commands
    assert not any(value in commands for value in ("collect_v3.py", "research_v3.py rebuild", "git push", "curl "))
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "${{ runner.temp }}/dc20-two-auction-detail-probe/auction_detail_probe.json"
