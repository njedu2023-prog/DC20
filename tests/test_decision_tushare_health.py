from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from scripts import check_tushare_health as health


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _calendar(open_today: bool) -> tuple[list[str], list[list[object]]]:
    return (
        ["exchange", "cal_date", "is_open", "pretrade_date"],
        [
            ["SSE", "20260814", 1, "20260813"],
            ["SSE", "20260815", 0, "20260814"],
            ["SSE", "20260816", int(open_today), "20260814"],
            ["SSE", "20260817", 1, "20260814"],
        ],
    )


def _auction(trade_date: str = "20260814") -> tuple[list[str], list[list[object]]]:
    return (
        list(health.AUCTION_FIELDS),
        [["600000.SH", trade_date, 10, 10, 10, 10, 1000, 10000, 10]],
    )


def _realtime(
    *,
    ts_code: str = "600000.SH",
    freq: str = "1MIN",
    timestamp: str = "2026-08-17 10:40:00",
    code_field: str = "ts_code",
) -> tuple[list[str], list[list[object]]]:
    response_fields = list(health.REALTIME_FIELDS)
    response_fields[0] = code_field
    return (
        response_fields,
        [[ts_code, freq, timestamp, 10, 10, 10, 10, 1000, 10000]],
    )


def test_missing_token_is_fail_hard() -> None:
    with pytest.raises(health.HealthCheckError, match="not configured"):
        health.run_health_check(
            token="",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
        )


def test_api_code_zero_accepts_empty_items_for_entitlement_probe() -> None:
    response = _FakeHttpResponse(
        {"code": 0, "msg": None, "data": {"fields": [], "items": []}}
    )
    with mock.patch.object(health.request, "urlopen", return_value=response):
        fields, rows = health._api_call(
            "rt_min_daily",
            {"ts_code": "600000.SH", "freq": "1MIN"},
            health.REALTIME_FIELDS,
            "secret-value",
            15,
        )
    assert fields == []
    assert rows == []


def test_api_nonzero_code_is_fail_hard() -> None:
    response = _FakeHttpResponse(
        {"code": 40203, "msg": "permission denied", "data": None}
    )
    with mock.patch.object(health.request, "urlopen", return_value=response):
        with pytest.raises(health.HealthCheckError, match="API code=40203") as caught:
            health._api_call(
                "rt_min_daily",
                {"ts_code": "600000.SH", "freq": "1MIN"},
                health.REALTIME_FIELDS,
                "secret-value",
                15,
            )
    assert caught.value.reason == "api_code"
    assert caught.value.row_count == 0
    assert caught.value.safe_details == {"api_code": 40203}


def test_rt_min_daily_contract_matches_documented_fields_and_frequency() -> None:
    assert health.REALTIME_FIELDS == (
        "ts_code",
        "freq",
        "time",
        "open",
        "close",
        "high",
        "low",
        "vol",
        "amount",
    )
    assert health.REALTIME_CODE_FIELDS == ("ts_code", "code")


def test_rt_min_daily_accepts_documented_code_result_header() -> None:
    fields, _ = _realtime(code_field="code")
    start = datetime(2026, 8, 17, 9, 30)
    rows = [
        [
            "600000.SH",
            "1MIN",
            (start + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S"),
            10,
            10,
            10,
            10,
            1000,
            10000,
        ]
        for offset in range(121)
    ]
    valid = health._valid_realtime_rows(
        fields,
        rows,
        probe_code="600000.SH",
        now_shanghai=datetime(2026, 8, 17, 11, 34, tzinfo=health.SHANGHAI),
    )
    assert valid == rows


@pytest.mark.parametrize(
    "identity_fields",
    [
        ("ts_code", "code"),
        ("code", "code"),
        ("symbol",),
    ],
)
def test_rt_min_daily_rejects_ambiguous_or_unsupported_identity_fields(
    identity_fields: tuple[str, ...],
) -> None:
    fields = [*identity_fields, *health.REALTIME_VALUE_FIELDS]
    rows = [
        [
            *(["600000.SH"] * len(identity_fields)),
            "1MIN",
            "2026-08-17 10:40:00",
            10,
            10,
            10,
            10,
            1000,
            10000,
        ]
    ]
    with pytest.raises(health.HealthCheckError) as caught:
        health._valid_realtime_rows(
            fields,
            rows,
            probe_code="600000.SH",
            now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
        )
    assert caught.value.reason == "schema"
    assert caught.value.row_count == 1


def test_rt_min_daily_code_alias_still_enforces_probe_identity() -> None:
    fields, rows = _realtime(ts_code="000001.SZ", code_field="code")
    with pytest.raises(health.HealthCheckError) as caught:
        health._valid_realtime_rows(
            fields,
            rows,
            probe_code="600000.SH",
            now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
        )
    assert caught.value.reason == "identity"


@pytest.mark.parametrize("width_delta", [-1, 1])
def test_rt_min_daily_rejects_rows_that_do_not_match_field_width(
    width_delta: int,
) -> None:
    fields, rows = _realtime(code_field="code")
    row = rows[0][:-1] if width_delta < 0 else [*rows[0], "extra"]
    with pytest.raises(health.HealthCheckError) as caught:
        health._valid_realtime_rows(
            fields,
            [row],
            probe_code="600000.SH",
            now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
        )
    assert caught.value.reason == "schema"
    assert caught.value.row_count == 1


def test_weekend_checks_calendar_auction_and_realtime_entitlement() -> None:
    calls: list[str] = []

    def fake_call(api_name, params, fields, token, timeout):
        calls.append(api_name)
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            assert params["trade_date"] == "20260814"
            return _auction()
        if api_name == "rt_min_daily":
            assert params == {"ts_code": "600000.SH", "freq": "1MIN"}
            return [], []
        raise AssertionError(api_name)

    result = health.run_health_check(
        token="secret-value",
        now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
        api_call=fake_call,
    )

    assert calls == ["trade_cal", "stk_auction_o", "rt_min_daily"]
    assert result["overall_status"] == "pass"
    realtime = next(check for check in result["checks"] if check["name"] == "rt_min_daily")
    assert realtime["status"] == "pass"
    assert realtime["data_status"] == "not_applicable"
    assert realtime["reason"] == "exchange_closed_today"
    assert realtime["row_count"] == 0


def test_open_session_requires_nonempty_realtime_probe() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            fields, rows = _calendar(open_today=False)
            for row in rows:
                if row[1] == "20260817":
                    row[2] = 1
            return fields, rows
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return _realtime()
        raise AssertionError(api_name)

    result = health.run_health_check(
        token="secret-value",
        now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
        api_call=fake_call,
    )
    realtime = next(check for check in result["checks"] if check["name"] == "rt_min_daily")
    assert realtime["status"] == "pass"
    assert realtime["row_count"] == 1


def test_empty_realtime_during_active_window_fails() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return list(fields), []
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="no valid rows") as caught:
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )

    failure = caught.value
    assert failure.reason == "active_window_probe_failure"
    assert failure.safe_details["endpoint"] == "rt_min_daily"
    assert failure.safe_details["active_window"] is True
    assert failure.safe_details["probes"] == [
        {
            "ts_code": probe_code,
            "row_count": 0,
            "status": "fail",
            "reason": "empty_rows",
        }
        for probe_code in health.DEFAULT_PROBE_CODES
    ]


def test_active_window_diagnostic_distinguishes_api_schema_and_timestamp() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            probe_code = params["ts_code"]
            if probe_code == health.DEFAULT_PROBE_CODES[0]:
                raise health.HealthCheckError(
                    "permission rejected",
                    reason="api_code",
                    safe_details={"api_code": 40203},
                )
            if probe_code == health.DEFAULT_PROBE_CODES[1]:
                return ["ts_code", "freq"], [[probe_code, "1MIN"]]
            return _realtime(ts_code=probe_code, timestamp="not-a-timestamp")
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError) as caught:
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )

    probes = caught.value.safe_details["probes"]
    assert [probe["ts_code"] for probe in probes] == list(health.DEFAULT_PROBE_CODES)
    assert [probe["reason"] for probe in probes] == [
        "api_code",
        "schema",
        "timestamp",
    ]
    assert [probe["row_count"] for probe in probes] == [0, 1, 1]
    assert probes[0]["api_code"] == 40203
    encoded = json.dumps(caught.value.safe_details, ensure_ascii=False)
    assert "secret-value" not in encoded
    assert "permission rejected" not in encoded
    assert "/tmp/" not in encoded


def test_active_window_valid_fallback_cannot_mask_prior_api_failure() -> None:
    realtime_calls: list[str] = []

    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            probe_code = params["ts_code"]
            realtime_calls.append(probe_code)
            if len(realtime_calls) == 1:
                raise health.HealthCheckError(
                    "permission rejected",
                    reason="api_code",
                    safe_details={"api_code": 40203},
                )
            return _realtime(ts_code=probe_code, timestamp="2026-08-16 10:40:00")
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError) as caught:
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )

    assert realtime_calls == list(health.DEFAULT_PROBE_CODES[:2])
    assert [probe["reason"] for probe in caught.value.safe_details["probes"]] == [
        "api_code",
        "valid_rows",
    ]


@pytest.mark.parametrize(("hour", "minute"), [(9, 35), (15, 30)])
def test_realtime_window_boundaries_are_inclusive(hour: int, minute: int) -> None:
    calls: list[str] = []

    def fake_call(api_name, params, fields, token, timeout):
        calls.append(api_name)
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return _realtime(timestamp=f"2026-08-16 {hour:02d}:{minute:02d}:00")
        raise AssertionError(api_name)

    result = health.run_health_check(
        token="secret-value",
        now_shanghai=datetime(2026, 8, 16, hour, minute, tzinfo=health.SHANGHAI),
        api_call=fake_call,
    )
    assert calls == ["trade_cal", "stk_auction_o", "rt_min_daily"]
    assert result["checks"][-1]["status"] == "pass"


def test_auction_permission_failure_propagates() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        raise health.HealthCheckError("stk_auction_o: API code=40203: permission denied")

    with pytest.raises(health.HealthCheckError, match="permission denied"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


@pytest.mark.parametrize(
    ("hour", "minute"),
    [(9, 34), (15, 31)],
)
def test_open_day_outside_active_window_checks_entitlement_without_data_gate(
    hour: int, minute: int
) -> None:
    calls: list[str] = []

    def fake_call(api_name, params, fields, token, timeout):
        calls.append(api_name)
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            assert params["trade_date"] == "20260814"
            return _auction("20260814")
        if api_name == "rt_min_daily":
            return [], []
        raise AssertionError(api_name)

    result = health.run_health_check(
        token="secret-value",
        now_shanghai=datetime(2026, 8, 16, hour, minute, tzinfo=health.SHANGHAI),
        api_call=fake_call,
    )
    assert calls == ["trade_cal", "stk_auction_o", "rt_min_daily"]
    realtime = next(check for check in result["checks"] if check["name"] == "rt_min_daily")
    assert realtime["status"] == "pass"
    assert realtime["data_status"] == "not_applicable"
    assert realtime["reason"] == "outside_09:35_15:30_shanghai_window"
    assert realtime["row_count"] == 0


def test_outside_window_entitlement_api_failure_is_fail_hard() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            raise health.HealthCheckError("rt_min_daily: API code=40203: permission denied")
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="permission denied"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 8, 0, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


@pytest.mark.parametrize(
    ("bad_values", "case_name"),
    [
        ({"ts_code": "999999.SH"}, "wrong code"),
        ({"freq": "5MIN"}, "wrong frequency"),
        ({"timestamp": "2026-08-16 10:40:00"}, "yesterday"),
        ({"timestamp": "2026-08-17 10:43:00"}, "future beyond skew"),
    ],
)
def test_active_window_rejects_wrong_realtime_identity_or_time(
    bad_values: dict[str, str], case_name: str
) -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return _realtime(**bad_values)
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="no valid rows"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


def test_active_window_allows_small_realtime_clock_skew() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return _realtime(timestamp="2026-08-17 10:41:00")
        raise AssertionError(api_name)

    result = health.run_health_check(
        token="secret-value",
        now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
        api_call=fake_call,
    )
    assert result["checks"][-1]["status"] == "pass"
    assert result["checks"][-1]["row_count"] == 1


def test_active_window_rejects_mixed_valid_and_wrong_realtime_rows() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            response_fields, valid_rows = _realtime()
            wrong_row = [*valid_rows[0]]
            wrong_row[0] = "999999.SH"
            return response_fields, [*valid_rows, wrong_row]
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="no valid rows"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 17, 10, 40, tzinfo=health.SHANGHAI),
            probe_codes=("600000.SH",),
            api_call=fake_call,
        )


def test_auction_empty_or_wrong_session_is_fail_hard() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return _auction("20260813")
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="completed session 20260814"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


def test_partial_endpoint_schema_is_fail_hard() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return ["ts_code", "trade_date"], [["600000.SH", "20260814"]]
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="required fields are missing"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


def test_empty_auction_is_fail_hard() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        if api_name == "trade_cal":
            return _calendar(open_today=False)
        if api_name == "stk_auction_o":
            return list(health.AUCTION_FIELDS), []
        raise AssertionError(api_name)

    with pytest.raises(health.HealthCheckError, match="stk_auction_o: no valid rows"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


def test_non_sse_or_conflicting_calendar_is_fail_hard() -> None:
    fields, rows = _calendar(open_today=False)
    wrong_exchange = [["SZSE", *row[1:]] for row in rows]
    with pytest.raises(health.HealthCheckError, match="no valid SSE"):
        health._calendar_map(fields, wrong_exchange)

    conflicting = [*rows, ["SSE", "20260816", 1, "20260814"]]
    with pytest.raises(health.HealthCheckError, match="conflicting is_open"):
        health._calendar_map(fields, conflicting)


def test_today_missing_from_calendar_is_fail_hard() -> None:
    def fake_call(api_name, params, fields, token, timeout):
        calendar_fields, calendar_rows = _calendar(open_today=False)
        return calendar_fields, [row for row in calendar_rows if row[1] != "20260816"]

    with pytest.raises(health.HealthCheckError, match="today 20260816 is absent"):
        health.run_health_check(
            token="secret-value",
            now_shanghai=datetime(2026, 8, 16, 10, 40, tzinfo=health.SHANGHAI),
            api_call=fake_call,
        )


def test_failure_output_redacts_token(capsys) -> None:
    token = "very-secret-token"
    with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": token}, clear=False), mock.patch.object(
        health,
        "_api_call",
        side_effect=health.HealthCheckError(f"backend echoed {token}"),
    ):
        assert health.main([]) == 1
    captured = capsys.readouterr()
    assert token not in captured.err
    assert "***" in captured.err


def test_active_failure_output_is_one_line_safe_structured_json(capsys) -> None:
    token = "very-secret-token"

    def fake_call(api_name, params, fields, api_token, timeout):
        assert api_token == token
        if api_name == "trade_cal":
            return _calendar(open_today=True)
        if api_name == "stk_auction_o":
            return _auction()
        if api_name == "rt_min_daily":
            return list(fields), []
        raise AssertionError(api_name)

    with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": token}, clear=False), mock.patch.object(
        health, "_api_call", side_effect=fake_call
    ):
        assert health.main(["--now-shanghai", "2026-08-16T10:40:00+08:00"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["overall_status"] == "fail"
    assert payload["diagnostic"]["endpoint"] == "rt_min_daily"
    assert len(payload["diagnostic"]["probes"]) == len(health.DEFAULT_PROBE_CODES)
    assert all(
        probe["reason"] == "empty_rows"
        for probe in payload["diagnostic"]["probes"]
    )
    assert token not in lines[0]
    assert "/tmp/" not in lines[0]


def test_workflow_is_pinned_read_only_and_has_no_runtime_install() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "check_tushare_health.yml"
    ).read_text(encoding="utf-8")
    assert "runs-on: ubuntu-24.04" in workflow
    assert "python-version: \"3.12.13\"" in workflow
    assert "sys.version_info[:3] == (3, 12, 13)" in workflow
    assert "pip install" not in workflow
    assert "pip.__version__" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "cron: \"40 2 * * 1-5\"" in workflow
    assert "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09" in workflow
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in workflow
    assert "requirements" not in workflow
    assert "upload-artifact" not in workflow
    assert "git push" not in workflow
    assert '${RUNNER_TEMP}/dc20-tushare-health.json' in workflow
    assert 'health command must emit exactly one safe JSON line' in workflow
    assert 'os.environ["GITHUB_STEP_SUMMARY"]' in workflow
    assert 'exit "${status}"' in workflow
    assert "continue-on-error" not in workflow
