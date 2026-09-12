"""Synthetic HTTP shape tests; no live market request or source permission."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib import error

import pytest
import yaml

from work.profit_1000_upgrade import auction_envelope_probe as probe

TOKEN = "synthetic_probe_credential_987654321"
PRIVATE_MESSAGE = "PRIVATE_SERVER_MESSAGE_VALUE_674230"
PRIVATE_REQUEST_ID = "PRIVATE_REQUEST_ID_VALUE_853642"
PRIVATE_CELL = "PRIVATE_MARKET_CELL_VALUE_762134"


def payload(**extra):
    return {"code": 0, "msg": PRIVATE_MESSAGE, "request_id": PRIVATE_REQUEST_ID,
            "data": {"fields": list(probe.FIELDS), "items": [["600001.SH", probe.DAY, 10, 100, 1000, PRIVATE_CELL]],
                     "count": 0, "has_more": False}, **extra}


def diagnose(value=None, raw=None, token=TOKEN):
    return probe.diagnose_response(json.dumps(payload() if value is None else value).encode() if raw is None else raw, token=token)


def forbidden(*args, **kwargs):
    raise AssertionError("no live network, codec relaxation or source write is authorized by a unit test")


def test_registered_one_call_exact_date_and_unchanged_v3_envelope_pin():
    contract = probe._guard()
    assert contract == probe.expected_contract()
    assert contract["endpoint"] == "stk_auction" and contract["params"] == {"trade_date": "20250116"}
    assert contract["max_api_calls"] == 1 and contract["retries"] == 0
    assert contract["timeout_seconds"] == 20 and contract["max_http_response_bytes"] == 4_000_000
    assert contract["v3_allowed_top_level_keys"] == ["code", "data", "msg", "request_id"]
    assert probe._file_sha(probe.HERE / "auction_truth_v3.py") == probe.V3_CODEC_SHA256


def test_valid_full_envelope_retains_only_names_types_counts_and_digest():
    original = payload()
    before = copy.deepcopy(original)
    raw = json.dumps(original).encode()
    result = diagnose(raw=raw)
    assert original == before
    assert result["status"] == "DIAGNOSTIC_RESPONSE_SHAPE_CAPTURED"
    assert result["http_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["http_response_bytes"] == len(raw) and result["response_body_complete"] is True
    assert result["code_type"] == "integer" and result["code_is_integer_zero"] is True
    assert result["v3_envelope_checks"] == {"json_object": True, "code_exact_int": True,
                                            "top_level_key_allowlist_pass": True, "combined_gate_pass": True}
    assert result["table_shape"] == {"data_type": "object", "fields_type": "array", "items_type": "array",
                                     "field_names": list(probe.FIELDS), "field_count": 6, "row_count": 1}
    assert result["extra_top_level_fields"] == []
    encoded = json.dumps(result)
    for forbidden_value in (PRIVATE_MESSAGE, PRIVATE_REQUEST_ID, PRIVATE_CELL, "600001.SH", TOKEN):
        assert forbidden_value not in encoded
    assert "network_request_performed" not in result
    assert result["envelope_only_not_full_codec_acceptance"] is True
    assert all(result[key] == value for key, value in probe.FLAGS.items())


@pytest.mark.parametrize("extra_value,type_name", [(None, "null"), (False, "boolean"), (777, "integer"),
                                                   (7.25, "number"), (PRIVATE_CELL, "string"),
                                                   ([PRIVATE_CELL], "array"), ({"hidden": PRIVATE_CELL}, "object")])
def test_unexpected_top_key_type_identifies_gate_without_retaining_value(extra_value, type_name):
    result = diagnose(payload(extra_meta=extra_value))
    assert len(result["extra_top_level_fields"]) == 1
    assert result["extra_top_level_fields"][0]["name"] == "extra_meta"
    assert result["extra_top_level_fields"][0]["type"] == type_name
    assert result["extra_top_level_field_count"] == 1
    assert result["v3_envelope_checks"]["top_level_key_allowlist_pass"] is False
    assert result["v3_envelope_checks"]["combined_gate_pass"] is False
    assert PRIVATE_CELL not in json.dumps(result)


@pytest.mark.parametrize("key", ["token", "TUSHARE_TOKEN", "api_key", "clientSecret", "password", "BearerToken",
                                "auth", "cookie", "session", "JWT", "private_key", "signature",
                                "bad-key", "中文字段", "A" * 33, "a" * 24, "abc123abcdef1234abcd", "a\nfield", TOKEN])
def test_sensitive_or_unsafe_top_keys_are_redacted_without_losing_type(key):
    result = diagnose(payload(**{key: {"hidden": TOKEN}}))
    assert len(result["extra_top_level_fields"]) == 1
    assert result["extra_top_level_fields"][0]["name"] == "REDACTED"
    assert result["extra_top_level_fields"][0]["type"] == "object"
    assert result["redacted_top_level_names"] == 1
    encoded = json.dumps(result)
    assert json.dumps(key) not in encoded and TOKEN not in encoded


def test_distinct_redacted_keys_do_not_collapse_field_count():
    result = diagnose(payload(api_key="secret one", password="secret two"))
    assert result["extra_top_level_field_count"] == result["redacted_top_level_names"] == 2
    assert len(result["extra_top_level_fields"]) == 2


@pytest.mark.parametrize("value,is_null,is_false,is_zero,equals_rows", [
    (None, True, None, None, None), (False, False, True, None, None),
    (True, False, False, None, None), (0, False, None, True, False),
    (1, False, None, False, True), (7, False, None, False, False),
    (0.0, False, None, None, None), ("0", False, None, None, None),
])
def test_extra_key_flags_are_typed_derived_comparisons_only(value, is_null, is_false, is_zero, equals_rows):
    result = diagnose(payload(extra_meta=value))
    extra = result["extra_top_level_fields"][0]
    assert extra["is_null"] is is_null and extra["is_boolean_false"] is is_false
    assert extra["is_integer_zero"] is is_zero and extra["integer_equals_data_row_count"] is equals_rows
    assert "value" not in extra and "content" not in extra
    assert result["extra_field_flags_are_diagnostics_not_pagination_authority"] is True
    assert result["v3_envelope_checks"]["combined_gate_pass"] is False


def test_extra_integer_no_row_count_has_no_invented_comparison():
    value = payload(extra_meta=0)
    value["data"] = None
    extra = diagnose(value)["extra_top_level_fields"][0]
    assert extra["is_integer_zero"] is True and extra["integer_equals_data_row_count"] is None


@pytest.mark.parametrize("code,code_type,exact,is_zero", [(0, "integer", True, True), (2002, "integer", True, False),
                                                        (False, "boolean", False, False), (0.0, "number", False, False),
                                                        ("0", "string", False, False), (None, "null", False, False),
                                                        ([], "array", False, False), ({}, "object", False, False)])
def test_v3_exact_int_check_separate_from_code_zero(code, code_type, exact, is_zero):
    value = payload()
    value["code"] = code
    result = diagnose(value)
    assert result["code_type"] == code_type and result["code_is_exact_integer"] is exact
    assert result["code_is_integer_zero"] is is_zero
    assert result["v3_envelope_checks"]["combined_gate_pass"] is exact
    assert "code_value" not in result


def test_missing_code_has_no_numeric_guess():
    value = payload()
    value.pop("code")
    result = diagnose(value)
    assert result["code_present"] is False and result["code_type"] == "missing"
    assert result["v3_envelope_checks"]["combined_gate_pass"] is False


@pytest.mark.parametrize("value", [[], None, "a private response", 7, True])
def test_nonobject_json_does_not_leak_value(value):
    result = diagnose(raw=json.dumps(value).encode())
    assert result["v3_envelope_checks"]["json_object"] is False
    assert result["top_level_fields"] == [] and result["extra_top_level_fields"] == []
    assert result["code_type"] == "missing" and result["table_shape"]["row_count"] is None
    assert "a private response" not in json.dumps(result)


@pytest.mark.parametrize("fields", [["ts_code", "api_key", TOKEN], [None, 42, {"hidden": TOKEN}], []])
def test_field_names_only_no_sensitive_names_or_field_values(fields):
    value = payload()
    value["data"]["fields"] = fields
    result = diagnose(value)
    assert result["table_shape"]["field_count"] == len(fields)
    assert TOKEN not in json.dumps(result) and "api_key" not in json.dumps(result)
    assert all(name == "REDACTED" for name in result["table_shape"]["field_names"] if name != "ts_code")


def test_no_rows_are_copied_even_when_provider_count_is_inconsistent():
    value = payload()
    value["data"]["items"] *= 5
    value["data"]["count"] = 555555
    result = diagnose(value)
    assert result["table_shape"]["row_count"] == 5
    assert "555555" not in json.dumps(result) and "items" not in result["table_shape"]
    assert PRIVATE_CELL not in json.dumps(result)


@pytest.mark.parametrize("raw,reason", [(b'{"code":0,"code":0}', "DUPLICATE_JSON_KEY"),
                                      (b'{"code":NaN}', "NONFINITE_JSON_NUMBER"),
                                      (b'{"code":1e9999}', "NONFINITE_JSON_NUMBER"),
                                      (b"not json", "INVALID_RESPONSE_JSON"),
                                      (b'"\xff"', "INVALID_RESPONSE_JSON")])
def test_invalid_json_has_only_safe_reason_digest_and_size(raw, reason):
    result = diagnose(raw=raw)
    assert result["status"] == "INVALID_RESPONSE_JSON" and result["diagnostic_reason"] == reason
    assert result["http_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "top_level_fields" not in result


@pytest.mark.parametrize("raw", [b"", b"x" * 4_000_001, {}, "non-original string"])
def test_oversized_or_non_original_response_has_no_false_whole_body_digest(raw):
    result = diagnose(raw=raw)
    assert result["status"] == "INVALID_BOUNDED_RESPONSE"
    assert result["http_response_sha256"] is result["http_response_bytes"] is None
    assert result["response_body_complete"] is False


def test_one_injected_fake_call_saved_as_test_not_actual_network(tmp_path):
    calls = []
    raw = json.dumps(payload(extra_meta=PRIVATE_MESSAGE)).encode()
    def fake(endpoint, params, fields, token, timeout):
        calls.append((endpoint, params, fields, token, timeout))
        return raw
    output = tmp_path / "new-probe"
    result = probe.run_probe(output, token=TOKEN, call=fake)
    assert calls == [("stk_auction", {"trade_date": "20250116"}, probe.FIELDS, TOKEN, 20)]
    assert result["api_calls"] == 1 and result["retries"] == 0
    assert result["network_request_performed"] is False and result["callable_injected_for_test"] is True
    assert set(path.name for path in output.iterdir()) == {"auction_envelope_probe.json"}
    saved = (output / "auction_envelope_probe.json").read_bytes()
    assert json.loads(saved) == result
    for forbidden_value in (TOKEN, PRIVATE_MESSAGE, PRIVATE_REQUEST_ID, PRIVATE_CELL, "600001.SH"):
        assert forbidden_value.encode() not in saved


def test_no_credential_means_no_attempt(tmp_path):
    result = probe.run_probe(tmp_path / "new", token="", call=forbidden)
    assert result["api_calls"] == 0 and result["network_request_performed"] is False
    assert result["status"] == "CREDENTIAL_ABSENT" and result["diagnostic"] is None


@pytest.mark.parametrize("kind", ["http", "exception", "oversized"])
def test_call_failures_no_retry_no_sensitive_exception_values(tmp_path, kind):
    count = []
    def failed(*args):
        count.append(1)
        if kind == "http":
            raise error.HTTPError("https://not-live.invalid/" + TOKEN, 429, PRIVATE_MESSAGE, {}, None)
        if kind == "oversized":
            raise probe.ProbeError("HTTP_RESPONSE_EXCEEDS_4MB")
        raise RuntimeError(TOKEN + PRIVATE_MESSAGE)
    result = probe.run_probe(tmp_path / "new", token=TOKEN, call=failed)
    assert len(count) == result["api_calls"] == 1 and result["retries"] == 0
    assert result["diagnostic"] is None
    assert TOKEN not in json.dumps(result) and PRIVATE_MESSAGE not in json.dumps(result)


def test_existing_output_blocks_request_and_preserves_previous_file(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    previous = output / "original.json"
    previous.write_text("preserve")
    with pytest.raises(probe.ProbeError, match="FRESH_UNALIASED_DIAGNOSTIC_OUTPUT_REQUIRED"):
        probe.run_probe(output, token=TOKEN, call=forbidden)
    assert previous.read_text() == "preserve"


def test_output_alias_rejected_before_request(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(probe.ProbeError):
        probe.run_probe(alias / "new", token=TOKEN, call=forbidden)


@pytest.mark.parametrize("where", ["before", "during"])
def test_pinned_codec_change_prevents_accepted_probe_without_changing_file(tmp_path, monkeypatch, where):
    original = probe._file_sha
    target = probe.HERE / "auction_truth_v3.py"
    changed = [where == "before"]
    monkeypatch.setattr(probe, "_file_sha", lambda path: "0" * 64 if Path(path) == target and changed[0] else original(path))
    def fake(*args):
        changed[0] = True
        return json.dumps(payload()).encode()
    with pytest.raises(probe.ProbeError, match="PINNED_V3_CODEC_CHANGED"):
        probe.run_probe(tmp_path / "new", token=TOKEN, call=fake)
    assert not (tmp_path / "new/auction_envelope_probe.json").exists()


def test_official_http_uses_fixed_post_bytes_and_one_bounded_read(monkeypatch):
    calls, reads = [], []
    raw = json.dumps(payload()).encode()
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            reads.append(limit)
            return raw
    def fake_urlopen(req, timeout):
        calls.append((req, timeout))
        return Response()
    monkeypatch.setattr(probe.request, "urlopen", fake_urlopen)
    assert probe.official_call("stk_auction", {"trade_date": probe.DAY}, probe.FIELDS, TOKEN, 20) is raw
    assert reads == [4_000_001] and len(calls) == 1
    req, timeout = calls[0]
    assert req.full_url == "https://api.tushare.pro" and req.method == "POST" and timeout == 20
    body = json.loads(req.data)
    assert body == {"api_name": "stk_auction", "params": {"trade_date": probe.DAY}, "fields": ",".join(probe.FIELDS), "token": TOKEN}


@pytest.mark.parametrize("endpoint,day,timeout", [("stk_auction_o", probe.DAY, 20),
                                                 ("stk_auction", "20260817", 20),
                                                 ("stk_auction", probe.DAY, 21)])
def test_official_http_no_arbitrary_endpoint_date_or_timeout(monkeypatch, endpoint, day, timeout):
    monkeypatch.setattr(probe.request, "urlopen", forbidden)
    with pytest.raises(probe.ProbeError, match="PROBE_REQUEST_SCOPE_CHANGED"):
        probe.official_call(endpoint, {"trade_date": day}, probe.FIELDS, TOKEN, timeout)


def test_workflow_one_request_id_trigger_readonly_no_manual_rerun_or_extra_secret_scope():
    path = probe.CHECKOUT / ".github/workflows/research_auction_envelope_probe.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"push"}
    assert workflow["on"]["push"] == {"branches": ["main"], "paths": ["work/profit_1000_upgrade/AUCTION_ENVELOPE_PROBE.json"]}
    job = workflow["jobs"]["diagnostic"]
    assert "github.run_attempt == 1" in job["if"] and "refs/heads/main" in job["if"]
    steps = job["steps"]
    secret_steps = [step for step in steps if "secrets." in str(step)]
    assert len(secret_steps) == 1
    assert secret_steps[0]["env"] == {"TUSHARE_TOKEN": "${{ secrets.TUSHARE_TOKEN }}"}
    assert "auction_envelope_probe.py" in secret_steps[0]["run"]
    assert all(len(step["uses"].rsplit("@", 1)[1]) == 40 for step in steps if "uses" in step)
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "--require-hashes" in commands and "pytest -q work/profit_1000_upgrade" in commands
    assert "validate_decision_model_freeze.py" in commands and "git status --porcelain=v1" in commands
    assert not any(term in commands for term in ("collect_v3.py", "auction_diagnostic.py", "git push", "curl "))
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "${{ runner.temp }}/dc20-one-auction-envelope-probe/auction_envelope_probe.json"
