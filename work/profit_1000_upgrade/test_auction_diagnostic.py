"""Offline diagnostic fixtures; never call a provider or relax the codec."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib import error

import pytest

from work.profit_1000_upgrade import auction_diagnostic as diag

TOKEN = "synthetic_diagnostic_credential_1234567890"
DAY = diag.DATES[0]
STAMP = "2026-09-12T08:00:00Z"


def row(*, code="600001.SH", day=DAY, price=10, vol=100, amount=1000, pre=9):
    return [code, day, price, vol, amount, pre]


def payload(rows=None, **data):
    return {"code": 0, "msg": "", "data": {"fields": list(diag.FIELDS),
                "items": [row()] if rows is None else rows, "count": 0, "has_more": False, **data}}


def diagnose(value=None, *, raw=None, day=DAY):
    raw = json.dumps(payload() if value is None else value).encode() if raw is None else raw
    return diag.diagnose_response(raw, day, token=TOKEN, fetched_at_utc=STAMP)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def __call__(self):
        return self.now
    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


def test_current_contract_pins_requests_and_dependencies():
    plan = diag.contract()
    assert plan == diag.expected_contract()
    assert plan["requests"] == [{"trade_date": "20250116"}, {"trade_date": "20260817"}]
    assert plan["max_api_calls"] == 2 and plan["retries"] == 0
    assert plan["source_archive_sha256"] == diag.ARCHIVE_SHA256


@pytest.mark.parametrize("rows", [[row()], [], [row(pre=0)], [row(code="920001.BJ")],
                                  [row(vol=0, amount=0, price=0)], [row(price="10", vol="100", amount="1000")]])
def test_valid_original_table_retained_without_truth_promotion(rows):
    source = payload(rows)
    original = copy.deepcopy(source)
    data, report = diagnose(source)
    assert data == source["data"] and source == original
    assert report["codec_source_validation"]["accepted"]
    assert report["original_table_validation"]["accepted"]
    assert report["row_diagnostic"]["accepted_rows"] == len(rows)
    assert report["row_diagnostic"]["eligible_as_source"] is False
    for key, expected in diag.FLAGS.items():
        assert report[key] == expected


@pytest.mark.parametrize("change,reason", [
    ({"amount": 1001}, "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT"),
    ({"vol": 100.5, "amount": 1005}, "INVALID_SHARE_VOLUME"),
    ({"price": None}, "INVALID_NUMERIC_VALUE"),
    ({"pre": None}, "INVALID_NUMERIC_VALUE"),
    ({"vol": 0, "amount": 1}, "ZERO_VOLUME_NONZERO_AMOUNT"),
    ({"price": 0}, "POSITIVE_VOLUME_REQUIRES_PRICE_AND_AMOUNT"),
    ({"amount": 0}, "POSITIVE_VOLUME_REQUIRES_PRICE_AND_AMOUNT"),
    ({"price": True}, "INVALID_NUMERIC_VALUE"),
    ({"price": -1}, "INVALID_NUMERIC_VALUE"),
    ({"code": "BAD"}, "INVALID_STOCK_CODE"),
    ({"day": "20250117"}, "SOURCE_WRONG_TRADE_DATE"),
])
def test_exact_codec_rejection_not_masked_or_fixed(change, reason):
    original = payload([row(**change)])
    data, report = diagnose(original)
    assert data == original["data"]
    assert report["codec_source_validation"] == {
        "accepted": False, "rejection_class": "AuctionSourceError", "reason": reason}
    assert report["original_table_validation"]["reason"] == reason
    diagnostic = report["row_diagnostic"]
    assert diagnostic["rejection_reason_counts"] == {reason: 1}
    assert diagnostic["representative_rows"][reason][0]["original_values"] == original["data"]["items"][0]


@pytest.mark.parametrize("fields", [list(reversed(diag.FIELDS)), list(diag.FIELDS)])
def test_original_field_order_retained(fields):
    mapping = dict(zip(diag.FIELDS, row()))
    source = payload([[mapping[name] for name in fields]], fields=fields)
    data, report = diagnose(source)
    assert data["fields"] == fields and data["items"] == source["data"]["items"]
    assert report["codec_source_validation"]["accepted"]


@pytest.mark.parametrize("change,reason", [({"count": 9}, "POSITIVE_COUNT_MISMATCH"),
                                         ({"has_more": True}, "PAGINATED_SOURCE_FORBIDDEN"),
                                         ({"count": None}, "POSITIVE_COUNT_MISMATCH"),
                                         ({"count": -1}, "POSITIVE_COUNT_MISMATCH")])
def test_original_table_envelope_rejection_separate_from_valid_single_rows(change, reason):
    source = payload(**change)
    data, report = diagnose(source)
    assert data == source["data"]
    assert report["original_table_validation"]["reason"] == reason
    assert report["row_diagnostic"]["accepted_rows"] == 1
    assert report["row_diagnostic"]["synthetic_single_row_envelopes"] is True
    assert report["source_import_allowed"] is False


def test_duplicate_code_crossrow_not_hidden_by_single_row_diagnostics():
    data, report = diagnose(payload([row(), row()]))
    assert report["original_table_validation"]["reason"] == "DUPLICATE_STOCK_ROW"
    assert report["duplicate_stock_rows"] == {"600001.SH": 2}
    assert report["row_diagnostic"]["accepted_rows"] == 2
    assert len(data["items"]) == 2


@pytest.mark.parametrize("change,reason", [({"fields": list(diag.FIELDS)[:-1]}, "INVALID_FIELDS"),
                                         ({"items": [row()[:-1]]}, "INVALID_ROW_SHAPE")])
def test_shape_rejections_still_retain_safe_original_table(change, reason):
    original = payload(**change)
    data, report = diagnose(original)
    assert data == original["data"]
    assert report["original_table_validation"]["reason"] == reason
    assert report["row_diagnostic"]["rejection_reason_counts"] == {reason: 1}


def test_bounded_representatives_do_not_discard_complete_original_rows():
    rows = [row(code=f"{i:06}.SH", amount=1001) for i in range(20)]
    data, report = diagnose(payload(rows))
    reason = "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT"
    assert len(data["items"]) == 20
    assert report["row_diagnostic"]["rejection_reason_counts"][reason] == 20
    assert len(report["row_diagnostic"]["representative_rows"][reason]) == 5


@pytest.mark.parametrize("where", ["row", "msg", "unknown_envelope", "unknown_table", "field", "escaped"])
def test_credentials_anywhere_prevent_table_persistence(where):
    source = payload()
    if where == "row":
        source["data"]["items"][0][2] = TOKEN
    elif where == "msg":
        source["msg"] = TOKEN
    elif where == "unknown_envelope":
        source["unexpected"] = {"value": TOKEN}
    elif where == "unknown_table":
        source["data"]["unexpected"] = TOKEN
    elif where == "field":
        source["data"]["fields"][2] = "api_key"
    else:
        source["msg"] = TOKEN
    raw = json.dumps(source).encode()
    if where == "escaped":
        raw = raw.replace(TOKEN.encode(), ("\\u" + format(ord(TOKEN[0]), "04x") + TOKEN[1:]).encode())
    data, report = diagnose(raw=raw)
    assert data is None and report["original_table_retained"] is False
    assert report["reason"] == "CREDENTIAL_LIKE_RESPONSE_NOT_RETAINED"
    assert TOKEN.encode() not in diag._json(report)


@pytest.mark.parametrize("unknown", ["top", "data"])
def test_unknown_fields_never_written(unknown):
    source = payload()
    if unknown == "top":
        source["extra"] = "private server text never retained"
    else:
        source["data"]["extra"] = "private server text never retained"
    data, report = diagnose(source)
    serialized = diag._json(report) + (diag._json(data) if data else b"")
    assert b"private server text" not in serialized
    assert (data is None) == (unknown == "data")


@pytest.mark.parametrize("code,msg,reason", [(2002, "权限不足", None),
                                         (500, "internal unknown user message", "UNKNOWN_API_FAILURE_NOT_SOURCE_UNAVAILABLE"),
                                         (500, "rate limit", "OPERATIONAL_FAILURE_NOT_SOURCE_UNAVAILABLE")])
def test_api_failure_keeps_only_code_allowlisted_rejection(code, msg, reason):
    data, report = diagnose({"code": code, "msg": msg, "data": None})
    assert data is None and report["status"] == "DIAGNOSTIC_API_ERROR"
    assert report["api_code"] == code
    assert report["codec_source_validation"]["reason"] == reason
    assert msg.encode() not in diag._json(report)
    assert report["fallback_generated"] is False


@pytest.mark.parametrize("raw,reason", [(b'{"code":0,"code":1}', "DUPLICATE_JSON_KEY"),
                                     (b'{"code":0,"data":NaN}', "NONFINITE_JSON_NUMBER"),
                                     (b'{"code":0,"data":1e9999}', "NONFINITE_JSON_NUMBER"),
                                     (b"not json", "INVALID_RESPONSE_JSON"),
                                     (b"x" * (diag.MAX_BYTES + 1), "RESPONSE_EXCEEDS_4MB")])
def test_bounded_strict_json_failures_are_fixed_enums(raw, reason):
    data, report = diagnose(raw=raw)
    assert data is None and report["reason"] == reason
    assert report["http_response_bytes"] == len(raw)
    assert report["http_response_sha256"] == diag._sha(raw)


def test_row_limit_does_not_partially_accept_table():
    data, report = diagnose(payload([row()] * (diag.MAX_ROWS + 1)))
    assert data is None
    assert report["reason"] == "UNSAFE_TABLE_SHAPE_NOT_RETAINED"


def test_unknown_codec_message_never_leaks(monkeypatch):
    codec, _ = diag._dependencies()
    def fail(*args, **kwargs):
        raise codec.AuctionSourceError(TOKEN)
    monkeypatch.setattr(codec, "_table", fail)
    data, report = diagnose()
    assert data is not None
    assert report["codec_source_validation"]["reason"] == "UNCLASSIFIED_CODEC_REJECTION"
    assert TOKEN.encode() not in diag._json(report)


def test_two_exact_fullmarket_calls_without_retry_and_original_evidence(tmp_path):
    clock, calls = Clock(), []
    def call(endpoint, params, fields, token, timeout):
        calls.append((clock(), endpoint, params, fields, token, timeout))
        return json.dumps(payload([row(day=params["trade_date"], amount=1001)])).encode()
    out = tmp_path / "diagnostic"
    result = diag.probe(out, token=TOKEN, runner_temp=tmp_path, call=call, clock=clock, sleep=clock.sleep)
    assert len(calls) == result["api_calls"] == 2
    assert calls[1][0] - calls[0][0] >= 1
    assert [c[2] for c in calls] == [{"trade_date": day} for day in diag.DATES]
    assert all(c[1] == "stk_auction" and c[3] == diag.FIELDS and c[5] == 20 for c in calls)
    assert len(list(out.iterdir())) == 4
    for receipt in result["requests"]:
        file = out / f'{receipt["trade_date"]}.original_data.json'
        assert json.loads(file.read_bytes())["items"][0][4] == 1001
        assert receipt["http_response_sha256"] and receipt["http_response_bytes"] > 0
    assert all(TOKEN.encode() not in p.read_bytes() for p in out.iterdir())


@pytest.mark.parametrize("exception", [TimeoutError(TOKEN), error.URLError(TOKEN), ValueError(TOKEN)])
def test_network_failure_does_not_retry_or_leak(tmp_path, exception):
    calls, clock = [], Clock()
    def call(*args):
        calls.append(args)
        raise exception
    out = tmp_path / "diagnostic"
    report = diag.probe(out, token=TOKEN, runner_temp=tmp_path, call=call, clock=clock, sleep=clock.sleep)
    assert len(calls) == report["api_calls"] == 2
    assert all(TOKEN.encode() not in p.read_bytes() for p in out.iterdir())
    assert len(list(out.iterdir())) == 2


def test_wall_budget_stops_second_attempt(tmp_path):
    clock = Clock()
    def call(*args):
        clock.now += 61
        raise TimeoutError()
    report = diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path,
                        call=call, clock=clock, sleep=clock.sleep)
    assert report["api_calls"] == 1 and report["status"] == "STOPPED_HARD_BUDGET"


def test_overslept_rate_limit_cannot_exceed_wall_budget(tmp_path):
    clock = Clock()
    def delayed_sleep(duration):
        clock.now += 61
    report = diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path,
                        call=lambda *args: json.dumps(payload()).encode(),
                        clock=clock, sleep=delayed_sleep)
    assert report["api_calls"] == 1 and report["status"] == "STOPPED_HARD_BUDGET"


def test_clock_without_required_rate_sleep_rejects_second_call(tmp_path):
    clock = Clock()
    with pytest.raises(diag.DiagnosticError, match="RATE_LIMIT_CLOCK"):
        diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path,
                    call=lambda *args: json.dumps(payload()).encode(),
                    clock=clock, sleep=lambda duration: None)


def test_output_failure_is_not_masked_as_network_failure(tmp_path, monkeypatch):
    real_write = diag._write
    def failing_write(out, name, value, token):
        if "original_data" in name:
            raise OSError("synthetic output failure")
        return real_write(out, name, value, token)
    monkeypatch.setattr(diag, "_write", failing_write)
    with pytest.raises(OSError, match="synthetic output failure"):
        diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path,
                    call=lambda *args: json.dumps(payload()).encode())


def test_existing_output_file_symlink_cannot_be_written(tmp_path):
    out = tmp_path / "diagnostic"
    out.mkdir()
    target = tmp_path / "original"
    target.write_bytes(b"original")
    (out / "auction_diagnostic.json").symlink_to(target)
    with pytest.raises(diag.DiagnosticError, match="IMMUTABLE_SAFE_OUTPUT"):
        diag._write(out, "auction_diagnostic.json", {}, TOKEN)
    assert target.read_bytes() == b"original"


def test_missing_token_never_calls_network(tmp_path):
    def forbidden(*args):
        raise AssertionError("network forbidden")
    report = diag.probe(tmp_path / "diagnostic", token="", runner_temp=tmp_path, call=forbidden)
    assert report["api_calls"] == 0 and report["status"] == "BLOCKED_MISSING_CREDENTIAL"


def test_second_probe_cannot_overwrite_or_repeat(tmp_path):
    out = tmp_path / "diagnostic"
    diag.probe(out, token="", runner_temp=tmp_path)
    hashes = {p.name: diag._sha(p.read_bytes()) for p in out.iterdir()}
    with pytest.raises(diag.DiagnosticError, match="FRESH"):
        diag.probe(out, token=TOKEN, runner_temp=tmp_path, call=lambda *a: pytest.fail("duplicate call"))
    assert hashes == {p.name: diag._sha(p.read_bytes()) for p in out.iterdir()}


@pytest.mark.parametrize("location", ["output", "parent", "outside"])
def test_unsafe_output_blocked_before_calls(tmp_path, location):
    area = tmp_path / "area"
    area.mkdir()
    output = area / "diagnostic"
    if location == "output":
        output.symlink_to(tmp_path / "elsewhere")
    elif location == "parent":
        alias = tmp_path / "alias"
        alias.symlink_to(area, target_is_directory=True)
        output = alias / "diagnostic"
    else:
        output = tmp_path / "outside"
    with pytest.raises(diag.DiagnosticError):
        diag.probe(output, token=TOKEN, runner_temp=area, call=lambda *a: pytest.fail("network"))


def test_duplicate_run_attempt_rejected_before_output_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(diag.DiagnosticError, match="DUPLICATE_RUN_ATTEMPT"):
        diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path)
    assert not (tmp_path / "diagnostic").exists()


def test_pinned_dependency_failure_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(diag, "CODEC_SHA256", "0" * 64)
    with pytest.raises(diag.DiagnosticError):
        diag.probe(tmp_path / "diagnostic", token=TOKEN, runner_temp=tmp_path)


def test_workflow_trigger_permission_and_secret_order():
    path = diag.ROOT / ".github/workflows/research_profit_auction_diagnostic.yml"
    value = path.read_text()
    assert '"work/profit_1000_upgrade/AUCTION_DIAGNOSTIC_REQUEST.json"' in value
    assert "workflow_dispatch" not in value and "schedule:" not in value and "contents: write" not in value
    assert "contents: read" in value and "persist-credentials: false" in value
    assert "github.run_attempt == 1" in value
    assert value.index("python -m pytest") < value.index("secrets.TUSHARE_TOKEN")
    assert "COLLECTION_V2.json" not in value
