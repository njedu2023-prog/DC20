import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("suspension_probe", Path(__file__).with_name("suspension_probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
TOKEN = "opaque-test-credential-must-not-persist"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def response(fields, rows, **envelope):
    return json.dumps({"code": 0, "data": {"fields": list(fields), "items": [[r[k] for k in fields] for r in rows]}, **envelope}).encode()


def daily(params):
    return {"ts_code": params["ts_code"], "trade_date": params["trade_date"],
            "open": 10, "high": 11, "low": 9, "close": 10, "pre_close": 10,
            "vol": 100, "amount": 1000, "pct_chg": 0}


class SuspensionProbeTests(unittest.TestCase):
    def run_probe(self, call, *, token=TOKEN, clock=None, inspect=None):
        timer = clock or Clock()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            output = base / "fresh"
            result = probe.probe(output, token=token, runner_temp=base, call=call,
                                 clock=timer, sleep=timer.sleep)
            self.assertLessEqual(result["api_calls"], 10)
            self.assertEqual(json.loads((output / "receipt.json").read_text()), result)
            self.assertFalse(result["settlement_performed"])
            self.assertFalse(result["synthetic_ohlc_created"])
            self.assertFalse(result["production_writes"])
            self.assertFalse(result["release_allowed"])
            for case in result["cases"]:
                self.assertFalse(case["settlement_allowed"])
                self.assertFalse(case["full_day_suspension_automatically_confirmed"])
            for path in output.rglob("*"):
                if path.is_file() and token:
                    self.assertNotIn(token.encode(), path.read_bytes())
            for binding in result["source_files"]:
                raw = (output / binding["path"]).read_bytes()
                self.assertEqual(len(raw), binding["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"])
            if inspect:
                inspect(result, output)
            return result

    def test_fixed_ten_requests_exact_dates_and_rate(self):
        observed, times = [], []
        timer = Clock()
        def call(endpoint, params, fields, token, timeout):
            observed.append((endpoint, params, fields))
            times.append(timer())
            self.assertEqual(token, TOKEN)
            self.assertEqual(timeout, 20)
            return response(fields, [])
        result = self.run_probe(call, clock=timer)
        self.assertEqual(result["api_calls"], 10)
        self.assertEqual(times, list(range(10)))
        self.assertEqual([(e, p["ts_code"], p.get("trade_date") or p["start_date"], p.get("end_date")) for e, p, f in observed], [
            ("daily", "600234.SH", "20240429", None), ("suspend_d", "600234.SH", "20240429", "20240430"),
            ("daily", "603226.SH", "20250610", None), ("suspend_d", "603226.SH", "20250610", "20250613"),
            ("daily", "603122.SH", "20251117", None), ("suspend_d", "603122.SH", "20251117", "20251120"),
            ("daily", "603580.SH", "20260707", None), ("suspend_d", "603580.SH", "20260707", "20260714"),
            ("daily", "002036.SZ", "20260723", None), ("suspend_d", "002036.SZ", "20260723", "20260730"),
        ])
        self.assertEqual(result["status"], "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT")
        self.assertTrue(all(c["status"] == "UNRESOLVED_MISSING_DAILY_OR_SUSPENSION_EVIDENCE" for c in result["cases"]))

    def test_suspension_events_are_positive_evidence_not_settlement(self):
        cases = {c["ts_code"]: c for c in probe.contract()["cases"]}
        def call(endpoint, params, fields, *args):
            if endpoint == "daily":
                return response(fields, [])
            case = cases[params["ts_code"]]
            rows = [{"ts_code": params["ts_code"], "trade_date": d, "suspend_timing": None, "suspend_type": "S"} for d in case["missing_exchange_sessions"]]
            rows.append({"ts_code": params["ts_code"], "trade_date": case["observed_resumption_date"], "suspend_timing": None, "suspend_type": "R"})
            return response(fields, rows)
        result = self.run_probe(call)
        self.assertTrue(all(c["gap_event_coverage_complete"] for c in result["cases"]))
        self.assertTrue(all(c["status"] == "SUSPENSION_RECORDED_FOR_GAP_REQUIRES_EVENT_REVIEW" for c in result["cases"]))

    def test_intraday_suspension_is_not_a_full_day_explanation(self):
        def call(endpoint, params, fields, *args):
            rows = [] if endpoint == "daily" else [{"ts_code": params["ts_code"], "trade_date": params["start_date"], "suspend_timing": "09:30-10:00", "suspend_type": "S"}]
            return response(fields, rows)
        result = self.run_probe(call)
        self.assertTrue(all(c["status"] == "INTRADAY_OR_RESUMPTION_NOT_FULL_DAY_PROOF" for c in result["cases"]))
        self.assertFalse(any(c["gap_event_coverage_complete"] for c in result["cases"]))

    def test_one_s_record_does_not_prove_multiday_gap(self):
        def call(endpoint, params, fields, *args):
            rows = [] if endpoint == "daily" else [{"ts_code": params["ts_code"], "trade_date": params["start_date"], "suspend_timing": "", "suspend_type": "S"}]
            return response(fields, rows)
        result = self.run_probe(call)
        self.assertTrue(result["cases"][0]["gap_event_coverage_complete"])
        self.assertFalse(any(c["gap_event_coverage_complete"] for c in result["cases"][1:]))

    def test_resumption_on_missing_date_is_not_suspension_proof(self):
        def call(endpoint, params, fields, *args):
            rows = [] if endpoint == "daily" else [{"ts_code": params["ts_code"], "trade_date": params["start_date"], "suspend_timing": None, "suspend_type": "R"}]
            return response(fields, rows)
        result = self.run_probe(call)
        self.assertTrue(all(c["status"] == "INTRADAY_OR_RESUMPTION_NOT_FULL_DAY_PROOF" for c in result["cases"]))

    def test_actual_daily_row_kept_without_rewriting_archive(self):
        def call(endpoint, params, fields, *args):
            return response(fields, [daily(params)] if endpoint == "daily" else [])
        result = self.run_probe(call)
        self.assertTrue(all(c["status"] == "DAILY_ROW_NOW_AVAILABLE_REVIEW_ARCHIVE_DIFFERENCE" for c in result["cases"]))
        self.assertTrue(all(r["row_count"] == 1 for r in result["requests"] if r["endpoint"] == "daily"))

    def test_daily_and_s_record_conflict_remains_visible(self):
        def call(endpoint, params, fields, *args):
            rows = [daily(params)] if endpoint == "daily" else [{"ts_code": params["ts_code"], "trade_date": params["start_date"], "suspend_timing": None, "suspend_type": "S"}]
            return response(fields, rows)
        result = self.run_probe(call)
        self.assertTrue(all(c["status"] == "CONFLICTING_DAILY_AND_SUSPENSION_EVIDENCE" for c in result["cases"]))

    def test_original_data_values_preserved_but_messages_never_persist(self):
        originals = []
        def call(endpoint, params, fields, *args):
            raw = response(fields, [daily(params)] if endpoint == "daily" else [],
                           msg="message " + TOKEN, detail="detail " + TOKEN, request_id=TOKEN)
            originals.append(raw)
            return raw
        def inspect(result, output):
            for receipt, raw in zip(result["requests"], originals):
                self.assertEqual(receipt["http_response_sha256"], hashlib.sha256(raw).hexdigest())
                data_file = output / receipt["source_files"][0]["path"]
                self.assertEqual(json.loads(data_file.read_bytes()), json.loads(raw)["data"])
        self.run_probe(call, inspect=inspect)

    def test_credential_absent_sends_nothing(self):
        def fail(*args):
            self.fail("no credential must not send")
        result = self.run_probe(fail, token="")
        self.assertEqual(result["api_calls"], 0)
        self.assertTrue(all(r["status"] == "CREDENTIAL_ABSENT" for r in result["requests"]))

    def test_endpoint_denied_stops_only_that_endpoint_without_leak(self):
        def call(endpoint, params, fields, *args):
            return json.dumps({"code": -1, "msg": "没有权限 " + TOKEN}).encode() if endpoint == "suspend_d" else response(fields, [])
        result = self.run_probe(call)
        self.assertEqual(result["api_calls"], 6)
        self.assertEqual(result["requests"][1]["status"], "ENTITLEMENT_DENIED")
        self.assertEqual(result["requests"][3]["status"], "SKIPPED_AFTER_ENDPOINT_ENTITLEMENT_FAILURE")

    def test_rejected_token_stops_all_calls(self):
        result = self.run_probe(lambda *args: json.dumps({"code": -1, "msg": "token rejected " + TOKEN}).encode())
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["requests"][1]["status"], "SKIPPED_AFTER_CREDENTIAL_FAILURE")

    def test_network_exception_is_safe_and_bounded(self):
        def call(*args):
            raise RuntimeError(TOKEN)
        result = self.run_probe(call)
        self.assertEqual(result["api_calls"], 10)
        self.assertTrue(all(r["status"] == "NETWORK_OR_RESPONSE_ERROR" for r in result["requests"]))

    def test_wall_budget_stops_remaining_calls(self):
        timer = Clock()
        def call(endpoint, params, fields, *args):
            timer.now += 150
            return response(fields, [])
        result = self.run_probe(call, clock=timer)
        self.assertEqual(result["api_calls"], 2)
        self.assertEqual(result["requests"][2]["status"], "SKIPPED_BUDGET_EXHAUSTED")

    def test_invalid_rows_and_credential_echo_never_become_evidence(self):
        bad_rows = [
            {"ts_code": "000001.SZ", "trade_date": "20240429", "suspend_timing": None, "suspend_type": "S"},
            {"ts_code": "600234.SH", "trade_date": "20240428", "suspend_timing": None, "suspend_type": "S"},
            {"ts_code": "600234.SH", "trade_date": "20240429", "suspend_timing": TOKEN, "suspend_type": "S"},
            {"ts_code": "600234.SH", "trade_date": "20240429", "suspend_timing": None, "suspend_type": "unknown"},
        ]
        case = probe.contract()["cases"][0]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                probe._rows(json.loads(response(probe.SUSPEND_FIELDS, [row])), "suspend_d", case)

    def test_invalid_numeric_duplicate_truncation_and_columns_fail_closed(self):
        case = probe.contract()["cases"][0]
        params = {"ts_code": case["ts_code"], "trade_date": case["first_missing_date"]}
        for invalid in (True, None, float("nan"), -1):
            row = daily(params)
            row["close"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                probe._rows(json.loads(response(probe.DAILY_FIELDS, [row])), "daily", case)
        for data in ({"fields": list(probe.DAILY_FIELDS), "items": [], "has_more": True},
                     {"fields": ["ts_code"] * len(probe.DAILY_FIELDS), "items": []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                probe._rows({"code": 0, "data": data}, "daily", case)
        row = {"ts_code": case["ts_code"], "trade_date": case["first_missing_date"], "suspend_timing": None, "suspend_type": "S"}
        with self.assertRaises(ValueError):
            probe._rows(json.loads(response(probe.SUSPEND_FIELDS, [row, row])), "suspend_d", case)

    def test_output_must_be_new_inside_runner_temp_and_outside_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            output = base / "fresh"
            probe._fresh_output(output, base)
            with self.assertRaises(FileExistsError):
                probe._fresh_output(output, base)
            with self.assertRaisesRegex(ValueError, "new child"):
                probe._fresh_output(base.parent / "outside", base)
            with self.assertRaisesRegex(ValueError, "RUNNER_TEMP"):
                probe._fresh_output(output, None)
            with self.assertRaisesRegex(ValueError, "outside checkout"):
                probe._fresh_output(probe.ROOT / "unsafe-probe", probe.ROOT)

    def test_symlink_and_dotdot_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            target = base / "target"
            target.mkdir()
            link = base / "alias"
            link.symlink_to(target, target_is_directory=True)
            for output in (link / "new", base / "target" / ".." / "new"):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, "aliased"):
                    probe._fresh_output(output, base)

    def test_bound_calendar_change_fails_before_requests(self):
        with mock.patch.object(probe, "CALENDAR_SHA", "0" * 64), self.assertRaisesRegex(ValueError, "binding"):
            probe.contract()

    def test_cli_rejects_arbitrary_stock_arguments(self):
        with mock.patch.object(probe.sys, "argv", ["suspension_probe.py", "--output", "/unused", "--ts-code", "000001.SZ"]), mock.patch.object(probe, "probe") as run, mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                probe.main()
            run.assert_not_called()

    def test_transport_is_fixed_https_and_response_bounded(self):
        raw = response(probe.DAILY_FIELDS, [])
        with mock.patch.object(probe.request, "urlopen", return_value=io.BytesIO(raw)) as opener:
            self.assertEqual(probe.official_call("daily", {"ts_code": "600234.SH", "trade_date": "20240429"}, probe.DAILY_FIELDS, TOKEN, 20), raw)
            req = opener.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.tushare.pro")
            self.assertEqual(json.loads(req.data)["token"], TOKEN)
            self.assertEqual(opener.call_args.kwargs, {"timeout": 20})
        with mock.patch.object(probe.request, "urlopen", return_value=io.BytesIO(b"x" * (probe.MAX_RESPONSE_BYTES + 1))), self.assertRaisesRegex(ValueError, "too large"):
            probe.official_call("daily", {}, probe.DAILY_FIELDS, TOKEN, 20)


if __name__ == "__main__":
    unittest.main()
